"""Phase 3D — Review view (v3 §7.4).

A self-contained HTML report generator (no React build step, no external
dependencies, inline CSS). The Amendments say the review view comes first
as a markdown report from the oracle run, with the UI in 3D; this is the
3D form: one HTML file per draft, with the five parts the design requires:

1. A one-page summary (archetype, cases served, ports/states/parameters
   in a table, each value with its source page).
2. A diff against the nearest existing archetype instance — only what is
   new/changed. The review surface is the delta, not the whole contract.
3. Trace playback in the visual twin (the fault chain changing step by
   step, from the oracle run).
4. The oracle result for its case (which rows pass/fail, with expected vs
   actual vectors for failures) + the binding result (which entities bind,
   with evidence; which stay unresolved).
5. Per-field actions (approve / edit / reject with reason) plus
   whole-draft approve / request revision / reject, with the review
   checklist as individual checkboxes the reviewer ticks — not a single
   approve button.

The HTML is a static report: the reviewer's choices are form controls
(radio buttons per field, checkboxes for the checklist). The completed
review is recorded by ``record_review`` (below), which takes the
reviewer's choices and produces the authorization transition + a review
record. Nothing in this file calls an LLM or touches a live twin.
"""

from __future__ import annotations

import html
import json
from typing import Any

from pydantic import BaseModel, Field

from .archetypes import ARCHETYPES
from .drafting import DraftArchetype, PreReviewGateResult

# The v2 §5.1 review checklist, as individual checkboxes the reviewer
# ticks — not a single approve button (a single button is how
# rubber-stamping happens).
REVIEW_CHECKLIST: list[str] = [
    "Every parameter value has a source page, or is explicitly marked unbound.",
    "Every row's condition references only declared ports, states and parameters.",
    "Every row's effect is a declared state or a port value in its domain.",
    "The oracle O2 passes on the draft's own case (the behaviour reproduces the manual's rows).",
    "The binding binds the intended entity and no entity it should not.",
    "Every failure mode names a discriminating test.",
    "The diff against the nearest archetype is real (not a duplicate of an existing archetype).",
    "No invented values: unbound is acceptable, a guessed value is not.",
]


class FieldReview(BaseModel):
    """One per-field action from the reviewer."""

    field: str
    action: str  # "approve" | "edit" | "reject"
    reason: str | None = None
    edited_value: Any = None


class ReviewRecord(BaseModel):
    """The completed review: the reviewer's per-field actions, the
    checklist ticks, and the whole-draft decision."""

    draft_id: str
    reviewer: str
    field_reviews: list[FieldReview] = Field(default_factory=list)
    checklist_ticked: list[str] = Field(default_factory=list)
    checklist_total: int = len(REVIEW_CHECKLIST)
    decision: str  # "approved" | "rejected" | "needs revision"
    decision_reason: str | None = None

    @property
    def checklist_hash(self) -> str:
        """A stable hash of the ticked checklist items, recorded on the
        approval record so the approval is checkable, not a claim."""
        import hashlib
        payload = json.dumps(sorted(self.checklist_ticked), sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    @property
    def checklist_complete(self) -> bool:
        return len(self.checklist_ticked) == self.checklist_total


def record_review(review: ReviewRecord) -> dict[str, Any]:
    """Record a completed review. Approval requires the full checklist
    ticked — a draft cannot be approved with unchecked items. Returns the
    record (as a dict) for the authorization transition."""
    if review.decision == "approved" and not review.checklist_complete:
        raise ValueError(
            f"cannot approve draft {review.draft_id!r}: the review checklist "
            f"is incomplete ({len(review.checklist_ticked)}/"
            f"{review.checklist_total} ticked). Approval requires every "
            f"item ticked — a single approve button is how rubber-stamping "
            f"happens.")
    return {
        "draft_id": review.draft_id,
        "reviewer": review.reviewer,
        "decision": review.decision,
        "decision_reason": review.decision_reason,
        "checklist_hash": review.checklist_hash,
        "checklist_ticked": len(review.checklist_ticked),
        "checklist_total": review.checklist_total,
        "field_reviews": [f.model_dump(mode="json")
                          for f in review.field_reviews],
    }


def _esc(value: Any) -> str:
    return html.escape(str(value))


def _diff_vs_nearest(draft: DraftArchetype,
                     nearest_archetype_id: str | None) -> dict[str, Any]:
    """The diff against the nearest existing archetype instance — only
    what is new or changed. The review surface is the delta."""
    if nearest_archetype_id is None or \
            nearest_archetype_id not in ARCHETYPES:
        return {"nearest": None, "added_ports": [p.name for p in draft.ports],
                "removed_ports": [], "added_parameters": [p.name for p in draft.parameters],
                "removed_parameters": [], "added_states": list(draft.states),
                "added_rows": [r.row_id for r in draft.propagation_table],
                "added_failure_modes": [f.name for f in draft.failure_modes]}
    nearest = ARCHETYPES[nearest_archetype_id]
    nearest_ports = {p.name for p in nearest.ports}
    nearest_params = {p.name for p in nearest.parameters}
    nearest_states = set(nearest.states)
    nearest_rows = {r.row_id for r in nearest.propagation_table}
    nearest_modes = {m.name for m in nearest.failure_modes}
    return {
        "nearest": nearest_archetype_id,
        "added_ports": sorted({p.name for p in draft.ports} - nearest_ports),
        "removed_ports": sorted(nearest_ports - {p.name for p in draft.ports}),
        "added_parameters": sorted(
            {p.name for p in draft.parameters} - nearest_params),
        "removed_parameters": sorted(
            nearest_params - {p.name for p in draft.parameters}),
        "added_states": sorted(set(draft.states) - nearest_states),
        "added_rows": sorted(
            {r.row_id for r in draft.propagation_table} - nearest_rows),
        "added_failure_modes": sorted(
            {f.name for f in draft.failure_modes} - nearest_modes),
    }


def render_review_view(draft: DraftArchetype,
                       gate_result: PreReviewGateResult,
                       *,
                       brief: Any = None,
                       nearest_archetype_id: str | None = None,
                       trace_messages: list[str] | None = None,
                       oracle_rows: list[dict[str, Any]] | None = None,
                       binding_result: dict[str, Any] | None = None,
                       reviewer: str = "Nishant") -> str:
    """Render the five-part review view as a self-contained HTML string."""
    diff = _diff_vs_nearest(draft, nearest_archetype_id)
    checks_html = "".join(
        f"<li>{'PASS' if c.passed else 'FAIL'} — { _esc(c.check)}"
        f"{' (' + c.outcome + ')' if c.outcome != 'pass' else ''}"
        f"{': ' + _esc('; '.join(c.reasons)) if c.reasons else ''}</li>"
        for c in gate_result.checks)
    # Part 1: one-page summary.
    param_rows = "".join(
        f"<tr><td>{_esc(p.name)}</td><td>{_esc(p.value) if not p.unbound else '<i>unbound</i>'}</td>"
        f"<td>{'p' + str(p.source_page) if p.source_page else (p.unbound_reason or '—')}</td></tr>"
        for p in draft.parameters)
    port_rows = "".join(
        f"<tr><td>{_esc(p.name)}</td><td>{_esc(p.direction)}</td><td>{_esc(p.domain)}</td>"
        f"<td>{'p' + str(p.source_page) if p.source_page else (p.inferred_from_template or '—')}</td></tr>"
        for p in draft.ports)
    row_rows = "".join(
        f"<tr><td>{_esc(r.row_id)}</td><td>{_esc(json.dumps(r.inputs))}</td>"
        f"<td>{_esc(json.dumps(r.outputs))}</td><td>{_esc(r.next_state)}</td>"
        f"<td>{', '.join('p' + str(pg) for pg in r.source_pages)}</td></tr>"
        for r in draft.propagation_table)
    mode_rows = "".join(
        f"<tr><td>{_esc(f.name)}</td><td>{_esc(f.discriminating_test)}</td>"
        f"<td>{_esc(', '.join(f.expected_trace))}</td></tr>"
        for f in draft.failure_modes)
    # Part 2: diff.
    diff_html = f"<p>Nearest existing archetype: <b>{_esc(diff['nearest'] or 'none (no near neighbour)')}</b></p>"
    for key, label in (("added_ports", "Added ports"), ("removed_ports", "Removed ports"),
                       ("added_parameters", "Added parameters"),
                       ("removed_parameters", "Removed parameters"),
                       ("added_states", "Added states"),
                       ("added_rows", "Added rows"),
                       ("added_failure_modes", "Added failure modes")):
        values = diff[key]
        diff_html += f"<p><b>{label}:</b> {', '.join(_esc(v) for v in values) if values else '<i>none</i>'}</p>"
    # Part 3: trace playback.
    trace_html = "<ol>" + "".join(
        f"<li>{_esc(m)}</li>" for m in (trace_messages or [])) + "</ol>" \
        if trace_messages else "<p><i>No trace attached (the oracle check did not produce one).</i></p>"
    # Part 4: oracle + binding.
    oracle_html = ""
    for row in (oracle_rows or []):
        o2 = row.get("checks", {}).get("O2", {})
        oracle_html += (f"<p><b>{_esc(row.get('row_id', ''))}</b> — "
                        f"{_esc(row.get('cause', ''))}: "
                        f"O2 {'pass' if o2.get('passed') else 'FAIL'}"
                        f"{': ' + _esc('; '.join(o2.get('reasons', []))) if o2.get('reasons') else ''}</p>")
    if not oracle_html:
        oracle_html = "<p><i>No oracle rows attached.</i></p>"
    binding_html = ""
    if binding_result:
        for name, arch_id in sorted(binding_result.items()):
            binding_html += f"<p>{_esc(name)} → <b>{_esc(arch_id or 'unresolved')}</b></p>"
    else:
        binding_html = "<p><i>No binding result attached.</i></p>"
    # Part 5: per-field actions + checklist.
    field_names = [p.name for p in draft.parameters] + \
        [p.name for p in draft.ports] + [r.row_id for r in draft.propagation_table]
    field_html = ""
    for field_name in field_names:
        field_html += (
            f"<div><b>{_esc(field_name)}</b> "
            f"<label><input type='radio' name='field:{_esc(field_name)}' value='approve'> approve</label> "
            f"<label><input type='radio' name='field:{_esc(field_name)}' value='edit'> edit</label> "
            f"<label><input type='radio' name='field:{_esc(field_name)}' value='reject'> reject</label> "
            f"<input type='text' name='reason:{_esc(field_name)}' placeholder='reason (required for edit/reject)' size='40'></div>")
    checklist_html = "".join(
        f"<div><label><input type='checkbox' name='checklist' value='{i}'> {_esc(item)}</label></div>"
        for i, item in enumerate(REVIEW_CHECKLIST))
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Review — draft { _esc(draft.archetype_id)} v{ _esc(draft.version)}</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 900px; margin: 2em auto; padding: 0 1em; color: #1a1a1a; }}
h1, h2 {{ border-bottom: 1px solid #ccc; padding-bottom: 0.2em; }}
table {{ border-collapse: collapse; width: 100%; margin: 0.5em 0; }}
th, td {{ border: 1px solid #ccc; padding: 0.3em 0.5em; text-align: left; }}
th {{ background: #f0f0f0; }}
.verdict {{ font-size: 1.2em; font-weight: bold; }}
.flag {{ color: #8a6d00; }} .fail {{ color: #a00; }} .pass {{ color: #070; }}
</style></head><body>
<h1>Review — draft <code>{_esc(draft.archetype_id)}</code> v{_esc(draft.version)}</h1>
<p class="verdict">Gate verdict: <span class="{'pass' if gate_result.verdict == 'candidate' else 'fail'}">{_esc(gate_result.verdict)}</span>
&middot; authoring layer: {_esc(draft.authoring_layer)}
&middot; reviewer: {_esc(reviewer)}</p>
<p><i>Candidate 0.x versions compile only in sandbox test twins, never an approved twin.</i></p>

<h2>1. One-page summary</h2>
<p><b>Archetype:</b> {_esc(draft.human_name)} ({_esc(draft.archetype_id)}) &middot;
<b>Intended entity:</b> {_esc(draft.intended_entity or '—')} &middot;
<b>Cases served:</b> {_esc(brief.case.case_id if brief is not None and getattr(brief, 'case', None) is not None else '—')}</p>
<h3>Ports</h3><table><tr><th>name</th><th>direction</th><th>domain</th><th>source</th></tr>{port_rows}</table>
<h3>Parameters</h3><table><tr><th>name</th><th>value</th><th>source / unbound reason</th></tr>{param_rows}</table>
<h3>States</h3><p>{', '.join(_esc(s) for s in draft.states)}</p>
<h3>Propagation rows</h3><table><tr><th>row</th><th>inputs</th><th>outputs</th><th>next state</th><th>source pages</th></tr>{row_rows}</table>
<h3>Failure modes</h3><table><tr><th>name</th><th>discriminating test</th><th>expected trace</th></tr>{mode_rows}</table>

<h2>2. Diff against the nearest existing archetype</h2>
{diff_html}
<p><i>The review surface is the delta, not the whole contract. For a new type, the reviewer is judging
"how is this different from the nearest type, and is that difference real?"</i></p>

<h2>3. Trace playback (from the oracle run)</h2>
{trace_html}

<h2>4. Oracle result + binding result</h2>
<h3>Pre-review gate</h3><ul>{checks_html}</ul>
<h3>Oracle rows for the case</h3>{oracle_html}
<h3>Binding (§8 unique-match rule)</h3>{binding_html}

<h2>5. Per-field actions + review checklist</h2>
<p><i>Tick every checklist item to approve. A single approve button is how rubber-stamping happens.</i></p>
{field_html}
<h3>Review checklist</h3>{checklist_html}
<div>
<button type="button">Approve</button>
<button type="button">Request revision</button>
<button type="button">Reject</button>
</div>
<p><i>Target: review in under 15 minutes per archetype instance. If it takes longer, the draft was too big —
split the instance or improve the brief, don't ask the reviewer to work harder.</i></p>
</body></html>"""
