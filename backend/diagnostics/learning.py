"""Phase 5 — verify / repair / learn hardening (plan.md §4 Phase 5).

(a) Outcome recording: a field outcome SUCCESSFUL/FAILED is recorded
    by the technician and kept SEPARATE from the twin verdict
    VALIDATED/REJECTED — it never overwrites it. A twin VALIDATED
    followed by field FAILED flags the implicated archetype row for
    review (an ``ArchetypeRowFlag`` row, cleared only by a named
    reviewer).
(b) Learning gate: an unseen fault solved in the field becomes a
    registry case only after expert approval. Proposals live in the
    append-only learning log (``LearningLogEntry``); the proposal is
    immutable, the review decision is recorded exactly once with the
    reviewer identity, and nothing auto-promotes — not even a case with
    many field successes.
(c) Versioning lives in ``backend.simulation.library_versions``.
(d) Full RepairAction lives in ``backend.simulation.repair_actions``.
(e) ``validation_history`` per archetype row is WRITTEN from recorded
    field outcomes into ``FieldValidationHistory`` — field outcomes
    only, never simulator verdicts (field_promotion.py).

The merged registry (``load_registry_with_learned``) is the file
registry plus approved learned cases. Pending or rejected entries are
never in it.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.db.models import (
    ArchetypeRowFlag, DiagnosticOutcome, FieldValidationHistory,
    LearningLogEntry,
)
from backend.knowledge.repair_cases import RepairCase, RepairCaseRegistry, load_registry
from backend.simulation.field_promotion import (
    FIELD_OUTCOMES, FieldOutcome, RepairRecord, record_field_outcome,
)
from backend.simulation.repair_actions import find_registry_cause

# DiagnosticOutcome.outcome -> field outcome. "inconclusive" maps to
# nothing: an inconclusive outcome is not field evidence either way.
FIELD_OUTCOME_MAP = {"fixed": "SUCCESSFUL", "not_fixed": "FAILED"}

# The last N field outcomes kept per (archetype, row) history row.
_HISTORY_OUTCOMES_CAP = 50


def last_twin_verdict(state: dict[str, Any] | None) -> str | None:
    """The last verify_repair verdict recorded on a session timeline
    (VALIDATED / REJECTED / None). The timeline is the record of what
    the twin said; the outcome endpoint reads it, never re-runs verify.
    Twin verdicts are stored lowercase ("validated"); the field-outcome
    statuses are uppercase (VALIDATED/REJECTED) — normalize here so the
    comparison in apply_field_outcome cannot silently miss."""
    for entry in reversed((state or {}).get("timeline") or []):
        if entry.get("kind") == "verify_repair" and entry.get("verdict"):
            return str(entry["verdict"]).upper()
    return None


def implicated_archetype_rows(session_state: dict[str, Any] | None,
                              confirmed_cause: str | None,
                              twin_definition: Any | None) -> list[dict[str, str]]:
    """The archetype rows implicated by a confirmed cause.

    Registry cause -> fault_injection -> behaviour owner archetype +
    fault-template row ids (via failure_to_behaviour). Without a
    registry cause, a twin, or a resolvable owner, the list is empty —
    the outcome is still recorded, but no row is implicated (and no
    row is flagged on a guess).
    """
    if not confirmed_cause or twin_definition is None:
        return []
    registry_cause = find_registry_cause(confirmed_cause)
    if registry_cause is None or not registry_cause.get("fault_injection"):
        return []
    try:
        from backend.simulation.repair_actions import failure_to_behaviour
        f2b = failure_to_behaviour(twin_definition, registry_cause["fault_injection"],
                                   registry_cause)
    except Exception:
        return []
    bindings = (getattr(twin_definition, "metadata", None) or {}).get("archetype_bindings") or {}
    owner_id = f2b.behaviour_owner_id
    archetype_id = (bindings.get(owner_id) or {}).get("archetype_id") if owner_id else None
    if not archetype_id:
        return []
    rows = list(f2b.fault_template_row_ids or [])
    if not rows:
        return [{"archetype_id": archetype_id, "row_id": ""}]
    return [{"archetype_id": archetype_id, "row_id": row_id} for row_id in rows]


def _history_row(db: Any, archetype_id: str, row_id: str) -> FieldValidationHistory:
    row = db.query(FieldValidationHistory).filter_by(
        archetype_id=archetype_id, row_id=row_id).first()
    if row is None:
        row = FieldValidationHistory(archetype_id=archetype_id, row_id=row_id,
                                     validated_count=0, rejected_count=0,
                                     field_instances=0, outcomes=[])
        db.add(row)
        db.flush()
    return row


def apply_field_outcome(db: Any, outcome: DiagnosticOutcome,
                        session_state: dict[str, Any] | None,
                        twin_definition: Any | None,
                        twin_verdict: str | None = None) -> dict[str, Any]:
    """Apply one recorded field outcome end to end (Phase 5a + 5e).

    - Maps the outcome to SUCCESSFUL/FAILED (inconclusive: no history
      write, stated in the result).
    - Writes validation_history for the implicated archetype rows.
    - Twin VALIDATED + field FAILED flags each implicated row for
      review and sets the RepairRecord learning signal.
    - A confirmed cause with no registry match proposes a learning-log
      entry (pending — never auto-promoted).
    Returns everything the outcome endpoint reports, so the flow is
    visible rather than silent.
    """
    field_outcome = FIELD_OUTCOME_MAP.get(outcome.outcome)
    verdict = twin_verdict if twin_verdict is not None else last_twin_verdict(session_state)
    verdict = verdict.upper() if verdict else None
    record = RepairRecord(repair_id=outcome.session_id,
                          case_id=(session_state or {}).get("case_id") or "",
                          cause=outcome.confirmed_cause or "")
    if verdict in ("VALIDATED", "REJECTED"):
        record.record_twin_verdict(verdict)
    history_written: list[dict[str, Any]] = []
    flags_created: list[dict[str, Any]] = []
    if field_outcome is not None:
        record.record_field_outcome(field_outcome, technician="field")
        rows = implicated_archetype_rows(session_state, outcome.confirmed_cause,
                                         twin_definition)
        for row_spec in rows:
            history = _history_row(db, row_spec["archetype_id"], row_spec["row_id"])
            # Reuse the 3D counting rule (field outcomes only) on a
            # lightweight stand-in, then persist the counts.
            from types import SimpleNamespace
            stand_in = SimpleNamespace(validation_history={
                "validated_count": history.validated_count,
                "rejected_count": history.rejected_count,
                "field_instances": history.field_instances,
                "field_outcomes": list(history.outcomes or [])})
            field_record = FieldOutcome(
                archetype_id=row_spec["archetype_id"], row_id=row_spec["row_id"] or None,
                technician="field", outcome=field_outcome,
                notes=outcome.technician_notes)
            updated = record_field_outcome(stand_in, field_record)
            history.validated_count = int(updated["validated_count"])
            history.rejected_count = int(updated["rejected_count"])
            history.field_instances = int(updated["field_instances"])
            history.outcomes = list(updated.get("field_outcomes") or [])[:_HISTORY_OUTCOMES_CAP]
            history_written.append({
                "archetype_id": row_spec["archetype_id"], "row_id": row_spec["row_id"],
                "validated_count": history.validated_count,
                "rejected_count": history.rejected_count,
                "field_instances": history.field_instances})
            if record.learning_signal:
                flag = flag_row_for_review(
                    db, row_spec["archetype_id"], row_spec["row_id"],
                    reason=(f"Twin VALIDATED but field FAILED for cause "
                            f"{outcome.confirmed_cause!r} (session {outcome.session_id}). "
                            f"{record.learning_signal}"),
                    source_outcome_id=outcome.id)
                flags_created.append({"archetype_id": flag.archetype_id,
                                      "row_id": flag.row_id, "flag_id": flag.id})
    learning_entry = None
    if outcome.confirmed_cause and find_registry_cause(outcome.confirmed_cause) is None:
        learning_entry = propose_learning_entry(
            db, source_type="field_outcome", source_id=outcome.id,
            session_id=outcome.session_id, product_id=outcome.product_id,
            symptom="; ".join((session_state or {}).get("symptoms") or []),
            cause=outcome.confirmed_cause,
            proposed_case={
                "symptom": "; ".join((session_state or {}).get("symptoms") or []),
                "cause": outcome.confirmed_cause,
                "fix": outcome.repair_performed or "",
                "test": "",
                "components": [],
                "source": f"field outcome {outcome.id} (session {outcome.session_id})",
            })
    db.commit()
    return {"field_outcome": field_outcome,
            "twin_verdict": verdict,
            "twin_verdict_field_outcome_separate": True,
            "learning_signal": record.learning_signal,
            "history_written": history_written,
            "flags_created": flags_created,
            "learning_entry": ({"id": learning_entry.id, "status": learning_entry.status}
                               if learning_entry is not None else None),
            "note": ("inconclusive outcome: not field evidence, no history "
                     "written" if field_outcome is None else
                     f"field {field_outcome} written to {len(history_written)} row(s)")}


def flag_row_for_review(db: Any, archetype_id: str, row_id: str,
                        reason: str, source_outcome_id: str | None = None
                        ) -> ArchetypeRowFlag:
    """Flag an archetype row for review. Deduped on (archetype, row,
    source outcome): the same contradiction does not open a second flag."""
    existing = db.query(ArchetypeRowFlag).filter_by(
        archetype_id=archetype_id, row_id=row_id,
        source_outcome_id=source_outcome_id, status="flagged").first()
    if existing is not None:
        return existing
    flag = ArchetypeRowFlag(archetype_id=archetype_id, row_id=row_id or "",
                            reason=reason, source_outcome_id=source_outcome_id)
    db.add(flag)
    db.flush()
    return flag


def clear_row_flag(db: Any, flag_id: str, reviewer: str, note: str = "") -> ArchetypeRowFlag:
    """Clear a flagged row. A named reviewer is required; clearing an
    already-cleared flag is rejected."""
    if not reviewer or not reviewer.strip():
        raise ValueError("clearing a flag requires a named reviewer")
    flag = db.get(ArchetypeRowFlag, flag_id)
    if flag is None:
        raise ValueError(f"no flag with id {flag_id!r}")
    if flag.status != "flagged":
        raise ValueError(f"flag {flag_id!r} is already {flag.status}")
    flag.status = "cleared"
    flag.reviewed_by = reviewer.strip()
    flag.reviewed_at = datetime.utcnow()
    flag.review_note = note
    db.commit()
    return flag


def propose_learning_entry(db: Any, source_type: str, source_id: str | None,
                           session_id: str | None, product_id: str,
                           symptom: str, cause: str,
                           proposed_case: dict[str, Any]) -> LearningLogEntry:
    """Append one proposal to the learning log (pending). Deduped on
    (product, symptom, cause) while pending: the same field solution
    does not open a second proposal."""
    existing = db.query(LearningLogEntry).filter_by(
        product_id=product_id, symptom=symptom, cause=cause,
        status="pending").first()
    if existing is not None:
        return existing
    entry = LearningLogEntry(source_type=source_type, source_id=source_id,
                             session_id=session_id, product_id=product_id,
                             symptom=symptom, cause=cause,
                             proposed_case=dict(proposed_case), status="pending")
    db.add(entry)
    db.flush()
    return entry


def review_learning_entry(db: Any, entry_id: str, decision: str,
                          reviewer: str, reason: str = "") -> LearningLogEntry:
    """The learning gate, enforced in code. ``decision`` is "approved"
    or "rejected". A named reviewer is required; an entry is reviewed
    exactly once (a second review raises); the proposal itself is never
    edited. Only an approved entry enters the merged registry."""
    if decision not in ("approved", "rejected"):
        raise ValueError(f"decision must be 'approved' or 'rejected', got {decision!r}")
    if not reviewer or not reviewer.strip():
        raise ValueError("reviewing a learning entry requires a named reviewer")
    entry = db.get(LearningLogEntry, entry_id)
    if entry is None:
        raise ValueError(f"no learning entry with id {entry_id!r}")
    if entry.status != "pending":
        raise ValueError(f"learning entry {entry_id!r} was already {entry.status} "
                         f"by {entry.reviewer}; the gate is decided once")
    entry.status = decision
    entry.reviewer = reviewer.strip()
    entry.review_reason = reason
    entry.reviewed_at = datetime.utcnow()
    db.commit()
    return entry


def learned_cases(db: Any, product_id: str | None = None) -> list[dict[str, Any]]:
    """Approved learned cases only — pending and rejected entries are
    never returned, so nothing auto-promotes into the registry."""
    query = db.query(LearningLogEntry).filter_by(status="approved")
    if product_id:
        query = query.filter_by(product_id=product_id)
    return [{"id": e.id, "symptom": e.symptom, "cause": e.cause,
             "proposed_case": dict(e.proposed_case or {}),
             "reviewer": e.reviewer}
            for e in query.order_by(LearningLogEntry.created_at).all()]


def load_registry_with_learned(db: Any, product_id: str | None = None
                               ) -> RepairCaseRegistry:
    """The file registry plus approved learned cases, as new registry
    cases. Learned cases get ids ``learned-<entry id prefix>`` and rank
    after the file cases; their table page is 0 (no manual page — the
    evidence is the field outcome, recorded on the entry)."""
    registry = load_registry()
    approved = learned_cases(db, product_id)
    if not approved:
        return registry
    cases = list(registry.cases)
    rank = max((c.rank for c in cases), default=0)
    for item in approved:
        case_data = item["proposed_case"] or {}
        rank += 1
        # model_construct: a learned case has field evidence, not manual
        # pages, so it honestly carries tier1_pages=[] — the file-registry
        # validator (every case cites Tier 1 pages) does not apply to it.
        # The cause still needs a fix or a test; a proposal with neither
        # is not promoted (review should have caught it; enforce here).
        if not (case_data.get("fix") or case_data.get("test")):
            continue
        cases.append(RepairCase.model_construct(
            id=f"learned-{item['id'][:8]}", rank=rank,
            symptom=item["symptom"] or case_data.get("symptom") or "",
            table_page=0, tier1_pages=[], referenced_pages=[],
            causes=[{"cause": item["cause"],
                     "fix": case_data.get("fix") or None,
                     "test": case_data.get("test") or None,
                     "components": list(case_data.get("components") or [])}],
            library_types=[]))
    return registry.model_copy(update={"cases": cases})
