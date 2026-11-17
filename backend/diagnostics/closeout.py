"""Phase 5 (steal item 5) — close-out-note mining + template check as a
learning source.

The learning loop ingests technician free-text close-out notes
(``DiagnosticOutcome.technician_notes``) as outcome records.

Rules, from the breakdown-diagnosis skill (evidence-sources.md):

- Group identical note text across machines/models IN CODE and count
  the distinct makes/models each group spans. Text shared across
  different makes/models is a TEMPLATE — shown, labelled "same text on
  N assets — not specific to this machine", and NEVER cited as this
  machine's evidence.
- The same failure recurring on same-model siblings IS a fleet pattern
  and is citable, with the records named (via ``sibling_search``).
- Weight by symptom similarity, then recency.
- Signal words "again", "still", "same as last time", "temporary fix",
  "needs proper repair" mark a fault unresolved.
- Report coverage honestly: N of M notes usable; terse/useless notes
  reported as such, never over-read.
- This feeds validation_history and the learning gate — a mined group
  proposes a learning-log entry (pending) only when it is not a
  template and carries a confirmed cause; expert approval is still
  required, nothing auto-promotes.

No LLM calls. A note is usable when it has at least 10 non-space
characters; anything shorter is terse and reported as such.
"""
from __future__ import annotations

import re
from typing import Any

from backend.db.models import DiagnosticOutcome, Product
from backend.diagnostics.backtracking import _texts_match
from backend.diagnostics.fleet import sibling_search
from backend.diagnostics.learning import propose_learning_entry

UNRESOLVED_SIGNAL_WORDS = ("again", "still", "same as last time",
                           "temporary fix", "needs proper repair")
MIN_USABLE_NOTE_LEN = 10


def _norm_note(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _is_unresolved(text: str) -> list[str]:
    lowered = (text or "").lower()
    return [word for word in UNRESOLVED_SIGNAL_WORDS if word in lowered]


def mine_closeout_notes(db: Any, symptom: str | None = None,
                        product_id: str | None = None) -> dict[str, Any]:
    """Mine close-out notes into groups, templates, fleet patterns and
    unresolved flags. ``symptom`` weights the ranking (similarity first,
    then recency); without it, groups rank by size then recency."""
    outcomes = db.query(DiagnosticOutcome).filter(
        DiagnosticOutcome.technician_notes.isnot(None)).all()
    total = len(outcomes)
    usable = [o for o in outcomes
              if len((o.technician_notes or "").strip()) >= MIN_USABLE_NOTE_LEN]
    terse = [o for o in outcomes if o not in usable]
    products = {p.id: p for p in db.query(Product).all()}

    groups: dict[str, dict[str, Any]] = {}
    for outcome in usable:
        key = _norm_note(outcome.technician_notes or "")
        group = groups.setdefault(key, {
            "note_text": (outcome.technician_notes or "").strip(),
            "outcome_ids": [], "product_ids": set(), "makes_models": set(),
            "confirmed_causes": set(), "outcomes": [], "unresolved_signals": set(),
        })
        group["outcome_ids"].append(outcome.id)
        group["product_ids"].add(outcome.product_id)
        product = products.get(outcome.product_id)
        if product is not None:
            group["makes_models"].add(
                f"{product.manufacturer} {product.family} {product.model}".strip())
        if outcome.confirmed_cause:
            group["confirmed_causes"].add(outcome.confirmed_cause)
        group["outcomes"].append(outcome)
        group["unresolved_signals"].update(_is_unresolved(outcome.technician_notes or ""))

    result_groups: list[dict[str, Any]] = []
    for group in groups.values():
        group_outcomes = sorted(group["outcomes"],
                                key=lambda o: o.created_at or "", reverse=True)
        latest = group_outcomes[0]
        is_template = len(group["makes_models"]) >= 2
        similar = True
        if symptom:
            similar = any(_texts_match(symptom, (o.confirmed_cause or "")
                                       + " " + (o.technician_notes or ""))
                          for o in group_outcomes)
        fleet_pattern = None
        if not is_template and product_id:
            # Same-model recurrence is citable — with the records named.
            search = sibling_search(db, product_id,
                                    symptom or (latest.confirmed_cause or ""))
            if search.get("fleet_pattern"):
                fleet_pattern = search["fleet_pattern"]
        result_groups.append({
            "note_text": group["note_text"],
            "count": len(group["outcome_ids"]),
            "outcome_ids": group["outcome_ids"],
            "makes_models": sorted(group["makes_models"]),
            "asset_count": len(group["product_ids"]),
            "is_template": is_template,
            "template_label": (f"same text on {len(group['product_ids'])} assets "
                               f"— not specific to this machine"
                               if is_template else None),
            "confirmed_causes": sorted(group["confirmed_causes"]),
            "unresolved": bool(group["unresolved_signals"]),
            "unresolved_signals": sorted(group["unresolved_signals"]),
            "similar_to_symptom": similar,
            "latest_at": (latest.created_at.isoformat()
                          if latest.created_at is not None else None),
            "fleet_pattern": fleet_pattern,
        })
    # Weight by symptom similarity, then recency (latest first), then size.
    result_groups.sort(key=lambda g: (not g["similar_to_symptom"],
                                      g["latest_at"] or "", -g["count"]))
    return {
        "coverage": {"total_notes": total, "usable": len(usable),
                     "terse": len(terse),
                     "terse_outcome_ids": [o.id for o in terse],
                     "note": (f"{len(usable)} of {total} notes usable; "
                              f"{len(terse)} terse/useless notes reported as "
                              f"such, never over-read")},
        "groups": result_groups,
        "templates": [g for g in result_groups if g["is_template"]],
        "fleet_patterns": [g for g in result_groups if g["fleet_pattern"]],
        "unresolved": [g for g in result_groups if g["unresolved"]],
    }


def propose_from_mining(db: Any, group: dict[str, Any], product_id: str,
                        session_id: str | None = None) -> Any | None:
    """Propose a learning-log entry from one mined group. Only for a
    non-template group with a confirmed cause: a template is never this
    machine's evidence, and a group with no cause proposes nothing.
    Returns the entry (pending) or None."""
    if group.get("is_template"):
        return None
    causes = group.get("confirmed_causes") or []
    if not causes:
        return None
    return propose_learning_entry(
        db, source_type="closeout_note", source_id=(group.get("outcome_ids") or [None])[0],
        session_id=session_id, product_id=product_id,
        symptom="", cause=causes[0],
        proposed_case={"symptom": "", "cause": causes[0], "fix": "",
                       "test": "", "components": [],
                       "source": (f"close-out-note mining: {group['count']} "
                                  f"identical note(s), outcomes "
                                  f"{', '.join(group.get('outcome_ids') or [])}")})
