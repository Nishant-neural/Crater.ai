"""Minimal RepairAction schema + ExpectedTrace + FailureToBehaviour
(design v3, third Amendments items 1–3; 3B scope).

A RepairAction is a reviewed repair for one registry cause: the fault
injection it repairs, the canonical procedure references (page evidence
only, never invented), the state effects the repair applies, the
postconditions the twin must show afterwards, the discriminating tests
(from the vocabulary), and the expected trace — the chain of state
changes the repair must show, by archetype row id + path, in order.

Full preconditions/safety is Phase 5 — deliberately NOT built here.
Only case 1 has reviewed actions in 3B; cases 2–9 get actions only when
their archetypes and trace contracts are reviewed (3C).

Paths in the data files are written as "<Component Name>.<field>" and
resolved against a twin by unique name match only; an ambiguous or
missing component makes the action inapplicable to that twin, never
guessed. The fault_injection_ref is "<registry case id>:<cause letter>"
(the cause's index within its case: a, b, c, d).
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from backend.simulation.archetypes import ARCHETYPES
from backend.simulation.oracle import load_fault_injections, normalize_text

_DEFAULT_ACTIONS_PATH = Path(__file__).with_name("repair_actions_omnipro.json")
_DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parents[1] / "knowledge" / "repair_cases_omnipro.json"


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


class ProcedureRef(BaseModel):
    page: int
    quote: str = ""
    source: str = ""


class TraceStep(BaseModel):
    path: str
    value: Any
    transition_id: str | None = None


class ExpectedTrace(BaseModel):
    required_state_changes: list[TraceStep] = Field(default_factory=list)
    forbidden_state_changes: list[TraceStep] = Field(default_factory=list)
    terminal_conditions: dict[str, Any] = Field(default_factory=dict)


class RepairAction(BaseModel):
    id: str
    name: str
    case_id: str
    cause_letter: str = ""
    fault_injection_ref: str
    procedure_refs: list[ProcedureRef] = Field(default_factory=list)
    state_effects: dict[str, Any] = Field(default_factory=dict)
    postconditions: dict[str, Any] = Field(default_factory=dict)
    required_tests: list[str] = Field(default_factory=list)
    expected_trace: ExpectedTrace | None = None
    # Phase 5 (d): the full RepairAction schema. Preconditions are the
    # states that must hold before the repair starts (each cites the
    # manual page it comes from in the procedure refs); safety
    # requirements are the safety lines for this repair, presented
    # BEFORE the repair steps (steal item 6). Both are reviewer-authored
    # in the data file; page evidence lives in procedure_refs.
    preconditions: list[str] = Field(default_factory=list)
    safety_requirements: list[str] = Field(default_factory=list)


class FailureToBehaviour(BaseModel):
    """The failure-to-behaviour mapping (third Amendments item 3): a
    registry cause resolved to the twin behaviour it drives.

    physical_part: the component the manual names (e.g. Feed Tensioner).
    behaviour_owner_instance: the twin component whose archetype rows
    express the fault (e.g. Wire Feed Mechanism — the tensioner is a
    part, the feeder is the behaviour owner).
    fault: the injection, as recorded in the registry.
    affected_downstream: components downstream of the owner along the
    reviewed edges (the topology the fault propagates through).
    fault_template_row_ids: the owner's archetype rows whose inputs or
    fault match the injection (the fault templates).
    repair_procedure_ref: the canonical procedure citation for the fix.
    """
    physical_part: str | None = None
    behaviour_owner_instance: str | None = None
    behaviour_owner_id: str | None = None
    fault: dict[str, Any] | None = None
    affected_downstream: list[str] = Field(default_factory=list)
    fault_template_row_ids: list[str] = Field(default_factory=list)
    repair_procedure_ref: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def load_repair_actions(path: str | Path | None = None) -> list[RepairAction]:
    with open(path or _DEFAULT_ACTIONS_PATH, encoding="utf-8") as handle:
        data = json.load(handle)
    return [RepairAction.model_validate(item) for item in data.get("actions") or []]


def load_registry_causes(path: str | Path | None = None) -> list[dict[str, Any]]:
    """The registry's causes, raw (the Pydantic registry model drops the
    ``fault_injection`` field — read the JSON, like the oracle does)."""
    with open(path or _DEFAULT_REGISTRY_PATH, encoding="utf-8") as handle:
        registry = json.load(handle)
    causes: list[dict[str, Any]] = []
    for case in registry.get("cases") or []:
        for index, cause in enumerate(case.get("causes") or []):
            causes.append({
                "case_id": case.get("id") or "",
                "case_symptom": case.get("symptom") or "",
                "cause_letter": chr(ord("a") + index),
                "cause": cause.get("cause") or "",
                "components": list(cause.get("components") or []),
                "fix": cause.get("fix") or "",
                "test": cause.get("test") or "",
                "fault_injection": cause.get("fault_injection"),
            })
    return causes


def find_registry_cause(cause_text: str, failure_mode_name: str | None = None,
                        path: str | Path | None = None) -> dict[str, Any] | None:
    """Find the registry cause for a hypothesis cause string.

    The failure-mode name (the case symptom) narrows the search to one
    case first; within the case the cause is matched by the same
    substring rule the tournament uses for failure modes. No match is
    returned when the text is ambiguous across causes — never guessed.
    """
    norm_cause = normalize_text(cause_text)
    causes = load_registry_causes(path)
    pool = causes
    if failure_mode_name:
        norm_fm = normalize_text(failure_mode_name)
        in_case = [c for c in causes
                   if norm_fm and (norm_fm in normalize_text(c["case_symptom"])
                                   or normalize_text(c["case_symptom"]) in norm_fm)]
        if in_case:
            pool = in_case
    matches: list[dict[str, Any]] = []
    for cause in pool:
        haystacks = [normalize_text(cause["cause"]), normalize_text(cause["fix"]),
                     normalize_text(cause["test"])]
        if any(h and (h in norm_cause or norm_cause in h) for h in haystacks):
            matches.append(cause)
    if len(matches) == 1:
        return matches[0]
    return None


def find_repair_action(fault_injection_ref: str,
                       path: str | Path | None = None) -> RepairAction | None:
    for action in load_repair_actions(path):
        if action.fault_injection_ref == fault_injection_ref:
            return action
    return None


# ---------------------------------------------------------------------------
# Path resolution (component NAME -> twin component id, unique match only)
# ---------------------------------------------------------------------------

def resolve_name_path(path: str, definition: Any) -> tuple[str | None, str]:
    """Resolve "<Component Name>.<field>" to "<component id>.<field>".

    Unique name match only; returns (resolved_path, reason). An
    unresolvable path makes the action inapplicable to this twin.
    """
    if "." not in path:
        return None, f"path {path!r} has no component.field form"
    name, field = path.rsplit(".", 1)
    matches = [c for c in definition.components if _norm(c.name) == _norm(name)]
    if len(matches) == 1:
        return f"{matches[0].id}.{field}", ""
    if not matches:
        return None, f"no twin component named {name!r}"
    return None, f"{len(matches)} twin components named {name!r}; ambiguous"


def resolve_action(action: RepairAction, definition: Any) -> tuple[dict[str, Any], list[str]]:
    """Resolve an action's paths against a twin.

    Returns (resolved, problems). resolved holds the id-based
    state_effects, postconditions and expected_trace (with the trace
    steps' transition names resolved from the definition). problems
    lists every path that did not resolve — the action is inapplicable
    to this twin when problems is non-empty.
    """
    resolved: dict[str, Any] = {"state_effects": {}, "postconditions": {},
                                "expected_trace": None}
    problems: list[str] = []
    for source, target in (("state_effects", "state_effects"),
                           ("postconditions", "postconditions")):
        for path, value in getattr(action, source).items():
            resolved_path, reason = resolve_name_path(path, definition)
            if resolved_path is None:
                problems.append(f"{source} {path!r}: {reason}")
            else:
                resolved[target][resolved_path] = value
    if action.expected_trace is not None:
        trace: dict[str, Any] = {"required": [], "forbidden": [],
                                 "terminal": {}}
        for step in action.expected_trace.required_state_changes:
            resolved_path, reason = resolve_name_path(step.path, definition)
            if resolved_path is None:
                problems.append(f"expected trace required step {step.path!r}: {reason}")
            else:
                trace["required"].append({"path": resolved_path, "value": step.value,
                                         "transition_id": step.transition_id})
        for step in action.expected_trace.forbidden_state_changes:
            resolved_path, reason = resolve_name_path(step.path, definition)
            if resolved_path is None:
                problems.append(f"expected trace forbidden step {step.path!r}: {reason}")
            else:
                trace["forbidden"].append({"path": resolved_path, "value": step.value,
                                          "transition_id": step.transition_id})
        for path, value in action.expected_trace.terminal_conditions.items():
            resolved_path, reason = resolve_name_path(path, definition)
            if resolved_path is None:
                problems.append(f"expected trace terminal condition {path!r}: {reason}")
            else:
                trace["terminal"][resolved_path] = value
        resolved["expected_trace"] = trace
    return resolved, problems


# ---------------------------------------------------------------------------
# FailureToBehaviour
# ---------------------------------------------------------------------------

def failure_to_behaviour(definition: Any, injection: dict[str, Any] | None,
                         registry_cause: dict[str, Any] | None,
                         bindings: dict[str, Any] | None = None) -> FailureToBehaviour:
    """Build the failure-to-behaviour mapping for one injection.

    The behaviour owner is the injection's instance (resolved by unique
    name); the fault-template row ids are the owner's archetype rows
    whose inputs or fault match the injection; the affected downstream
    is the reviewed edge list walked from the owner.
    """
    if not injection:
        return FailureToBehaviour()
    instance_name = injection.get("instance") or ""
    matches = [c for c in definition.components if _norm(c.name) == _norm(instance_name)]
    owner = matches[0] if len(matches) == 1 else None
    owner_name = owner.name if owner is not None else None
    owner_id = owner.id if owner is not None else None
    row_ids: list[str] = []
    if owner is not None:
        # The fault-template rows are the twin transitions whose
        # conditions match the injection: an archetype row of the
        # owner's archetype (tension=low -> T2-low) OR a reviewed link
        # fault-override (gun fault=open -> the feeder->gun override
        # row, since the gun is A8-unbound in 3B and its wire path is
        # link-carried). Generated totality rows are not templates.
        field = injection.get("parameter") or "fault"
        value = injection.get("value") if "parameter" in injection else injection.get("fault_state")
        for transition in (getattr(definition, "transitions", None) or []):
            if "u-totality" in (transition.id or ""):
                continue
            if (transition.conditions or {}).get(f"{owner.id}.{field}") == value:
                row_ids.append(transition.id)
    downstream: list[str] = []
    if owner_name is not None:
        try:
            from backend.simulation.edges import load_edge_list
            edge_list = load_edge_list()
            by_source: dict[str, list[str]] = {}
            for edge in edge_list.get("edges") or []:
                by_source.setdefault(_norm(edge.get("source_component") or ""), []).append(
                    str(edge.get("target_component") or ""))
            seen = {owner_name}
            frontier = [owner_name]
            while frontier:
                current = frontier.pop(0)
                for target in by_source.get(_norm(current), []):
                    if _norm(target) not in {_norm(s) for s in seen}:
                        seen.add(target)
                        downstream.append(target)
                        frontier.append(target)
        except (OSError, ValueError):
            downstream = []
    procedure_ref = None
    if registry_cause is not None:
        procedure_ref = {"fix": registry_cause.get("fix") or "",
                         "test": registry_cause.get("test") or ""}
    physical_part = None
    if registry_cause is not None and registry_cause.get("components"):
        physical_part = registry_cause["components"][0]
    return FailureToBehaviour(
        physical_part=physical_part,
        behaviour_owner_instance=owner_name,
        behaviour_owner_id=owner_id,
        fault=injection,
        affected_downstream=downstream,
        fault_template_row_ids=row_ids,
        repair_procedure_ref=procedure_ref,
    )


# ---------------------------------------------------------------------------
# Trace checking
# ---------------------------------------------------------------------------

def parse_trace_messages(messages: list[str]) -> list[dict[str, Any]]:
    """Parse engine step() change messages ("<name>: <path> <old> -> <new>")
    into ordered entries. Unparseable lines are skipped, never guessed."""
    entries: list[dict[str, Any]] = []
    for message in messages or []:
        if ": " not in message or " → " not in message:
            continue
        name, rest = message.split(": ", 1)
        path, _, new_repr = rest.partition(" → ")
        # rest is "<path> <old> -> <new>"; the old value sits between the
        # path and the arrow — take the first token as the path.
        path = path.split(" ", 1)[0]
        try:
            value = ast.literal_eval(new_repr.strip())
        except (ValueError, SyntaxError):
            continue
        entries.append({"transition_name": name, "path": path, "value": value})
    return entries


def check_expected_trace(trace_entries: list[dict[str, Any]],
                         fault_state: dict[str, Any],
                         expected_trace: dict[str, Any] | None,
                         definition: Any) -> dict[str, Any]:
    """Check the intervention trace against an ExpectedTrace contract.

    A required (path, value) is satisfied if it appears in the trace in
    order — or if it already held in the fault state (a value the repair
    does not need to change, e.g. a symptom premise that was true all
    along: it counts at position -1, before any change, and the
    positions must stay non-decreasing). A missing required step makes
    the repair unavailable ("trace does not show the required chain").
    A forbidden (path, value) appearing in the trace rejects the repair.
    When the contract names a transition id for a step, the producing
    transition's id must match it (resolvable by name); a mismatch is
    recorded as a note, not silently ignored.
    """
    if expected_trace is None:
        return {"satisfied": True, "missing": [], "forbidden_hit": None,
                "note": "thin trace — no trace contract", "produced_by": {}}
    name_to_id = {t.name: t.id for t in (definition.transitions or [])}
    components = fault_state.get("components", {}) if isinstance(fault_state, dict) else {}

    def fault_value(path: str) -> Any:
        if "." not in path:
            return None
        cid, field = path.split(".", 1)
        return (components.get(cid) or {}).get(field)

    positions: list[int] = []
    missing: list[str] = []
    produced_by: dict[str, str | None] = {}
    cursor = -1
    for step in expected_trace.get("required") or []:
        found_at: int | None = None
        producer: str | None = None
        if fault_value(step["path"]) == step["value"] and cursor == -1:
            found_at = -1
        else:
            for index in range(max(cursor, 0), len(trace_entries)):
                entry = trace_entries[index]
                if entry["path"] == step["path"] and entry["value"] == step["value"]:
                    found_at = index
                    producer = entry["transition_name"]
                    break
        if found_at is None:
            missing.append(step["path"])
            continue
        if found_at < cursor:
            missing.append(step["path"])
            continue
        cursor = found_at
        produced_by[step["path"]] = producer
        expected_id = step.get("transition_id")
        if expected_id is not None and producer is not None:
            actual_id = name_to_id.get(producer)
            if actual_id is not None and actual_id != expected_id:
                missing.append(
                    f"{step['path']} (produced by {actual_id}, contract names {expected_id})")
    forbidden_hit: dict[str, Any] | None = None
    for step in expected_trace.get("forbidden") or []:
        for entry in trace_entries:
            if entry["path"] == step["path"] and entry["value"] == step["value"]:
                forbidden_hit = {"path": step["path"], "value": step["value"],
                                 "produced_by": entry["transition_name"]}
                break
        if forbidden_hit is not None:
            break
    satisfied = not missing and forbidden_hit is None
    note_parts: list[str] = []
    if missing:
        note_parts.append("trace does not show the required chain: "
                          + ", ".join(missing))
    if forbidden_hit is not None:
        note_parts.append("forbidden change present: "
                          f"{forbidden_hit['path']} became {forbidden_hit['value']!r}")
    return {"satisfied": satisfied, "missing": missing,
            "forbidden_hit": forbidden_hit,
            "note": "; ".join(note_parts) if note_parts else "trace shows the required chain",
            "produced_by": produced_by}


# ---------------------------------------------------------------------------
# Safety-first presentation (Phase 5d + steal item 6)
# ---------------------------------------------------------------------------

def render_repair_action_safety_first(action: RepairAction) -> dict[str, Any]:
    """Present a full RepairAction with the safety line BEFORE the repair
    steps. A repair with a safety implication leads with it; nobody is
    sent to the machine above the safety flag. The order is fixed:
    Safety, then Preconditions, then Steps (procedure refs), then the
    expected trace summary. The safety section is always present — an
    action with no safety_requirements still carries the lockout line,
    so its absence is visible rather than silent.
    """
    from backend.diagnostics.safety import LOCKOUT_LINE
    safety_lines = [LOCKOUT_LINE] + list(action.safety_requirements or [])
    return {
        "action_id": action.id,
        "name": action.name,
        "fault_injection_ref": action.fault_injection_ref,
        "order": ["Safety", "Preconditions", "Steps", "Expected trace"],
        "Safety": safety_lines,
        "Preconditions": list(action.preconditions or []),
        "Steps": [{"page": ref.page, "quote": ref.quote, "source": ref.source}
                  for ref in action.procedure_refs],
        "Expected trace": {
            "required": [{"path": step.path, "value": step.value}
                         for step in (action.expected_trace.required_state_changes
                                      if action.expected_trace else [])],
            "terminal": dict(action.expected_trace.terminal_conditions)
                        if action.expected_trace else {},
        },
    }
