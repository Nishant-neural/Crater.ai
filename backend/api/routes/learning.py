"""Phase 5 — learning review view + gated review endpoints.

The review view (``GET /learning/review``) is where a recorded field
outcome becomes visible end to end: the validation_history it wrote,
the archetype rows it flagged, the learning-log proposals awaiting
expert approval, and the PM drafts. Nothing here auto-promotes: every
decision endpoint requires a named reviewer and decides exactly once.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from backend.db.models import (
    ArchetypeRowFlag, DigitalTwin, FieldValidationHistory, LearningLogEntry,
    PMDraft,
)
from backend.db.session import get_session
from backend.diagnostics.downtime import bad_actor_ranking, downtime_report
from backend.diagnostics.learning import (
    clear_row_flag, learned_cases, review_learning_entry,
)
from backend.diagnostics.pm import review_pm_draft
from backend.simulation.library_versions import (
    CURRENT_LIBRARY_VERSION, KNOWN_LIBRARY_VERSIONS,
    pin_library_version, recompile_with_version, rollback_library_version,
)

router = APIRouter(prefix="/learning", tags=["learning"])


class ReviewRequest(BaseModel):
    decision: str  # approved | rejected
    reviewer: str
    reason: str = ""


class ClearFlagRequest(BaseModel):
    reviewer: str
    note: str = ""


class RollbackRequest(BaseModel):
    target_version: str
    actor: str
    reason: str


@router.get("/review")
def review_view(db: DBSession = Depends(get_session)):
    """The Phase 5 review view: flagged rows, pending learning entries,
    PM drafts, field-validation history, and approved learned cases."""
    flags = db.query(ArchetypeRowFlag).filter_by(status="flagged").all()
    pending = db.query(LearningLogEntry).filter_by(status="pending").all()
    drafts = db.query(PMDraft).filter_by(status="draft").all()
    history = db.query(FieldValidationHistory).all()
    return {
        "flagged_rows": [{"id": f.id, "archetype_id": f.archetype_id,
                          "row_id": f.row_id, "reason": f.reason,
                          "source_outcome_id": f.source_outcome_id,
                          "created_at": f.created_at.isoformat() if f.created_at else None}
                         for f in flags],
        "pending_learning_entries": [
            {"id": e.id, "source_type": e.source_type, "source_id": e.source_id,
             "session_id": e.session_id, "product_id": e.product_id,
             "symptom": e.symptom, "cause": e.cause,
             "proposed_case": e.proposed_case, "status": e.status,
             "created_at": e.created_at.isoformat() if e.created_at else None}
            for e in pending],
        "pm_drafts": [{"id": d.id, "product_id": d.product_id,
                       "failure_mode": d.failure_mode,
                       "current_interval": d.current_interval,
                       "proposed_interval": d.proposed_interval,
                       "proposed_change": d.proposed_change, "status": d.status}
                      for d in drafts],
        "field_validation_history": [
            {"archetype_id": h.archetype_id, "row_id": h.row_id,
             "validated_count": h.validated_count,
             "rejected_count": h.rejected_count,
             "field_instances": h.field_instances}
            for h in history],
        "approved_learned_cases": learned_cases(db),
        "counts": {"flagged_rows": len(flags), "pending_entries": len(pending),
                   "pm_drafts": len(drafts), "history_rows": len(history)},
    }


@router.post("/entries/{entry_id}/review")
def review_entry(entry_id: str, payload: ReviewRequest, db: DBSession = Depends(get_session)):
    """The learning gate: approve or reject a proposed registry case.
    A named reviewer is required; an entry is decided exactly once."""
    try:
        entry = review_learning_entry(db, entry_id, payload.decision,
                                      payload.reviewer, payload.reason)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return {"id": entry.id, "status": entry.status, "reviewer": entry.reviewer,
            "review_reason": entry.review_reason,
            "message": ("Approved — the case now enters the merged registry"
                        if entry.status == "approved" else
                        "Rejected — the proposal stays out of the registry")}


@router.post("/rows/{flag_id}/clear")
def clear_flag(flag_id: str, payload: ClearFlagRequest, db: DBSession = Depends(get_session)):
    try:
        flag = clear_row_flag(db, flag_id, payload.reviewer, payload.note)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return {"id": flag.id, "status": flag.status, "reviewed_by": flag.reviewed_by}


@router.post("/pm-drafts/{draft_id}/review")
def review_pm(draft_id: str, payload: ReviewRequest, db: DBSession = Depends(get_session)):
    try:
        draft = review_pm_draft(db, draft_id, payload.decision, payload.reviewer)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return {"id": draft.id, "status": draft.status, "reviewer": draft.reviewer}


@router.get("/bad-actors")
def bad_actors(db: DBSession = Depends(get_session)):
    """Batch bad-actor ranking (steal item 9), from recorded outcomes only."""
    return bad_actor_ranking(db)


@router.get("/downtime/{session_id}")
def downtime(session_id: str, db: DBSession = Depends(get_session)):
    """The structured downtime record for one diagnosis (steal item 8)."""
    record = downtime_report(db, session_id)
    if record is None:
        raise HTTPException(404, "No downtime record for this session")
    return record


@router.get("/library-versions")
def library_versions():
    return {"current": CURRENT_LIBRARY_VERSION,
            "known": list(KNOWN_LIBRARY_VERSIONS)}


@router.post("/twins/{twin_id}/pin-library-version")
def pin_twin(twin_id: str, version: str | None = None,
             db: DBSession = Depends(get_session)):
    twin = db.get(DigitalTwin, twin_id)
    if twin is None:
        raise HTTPException(404, "Twin not found")
    try:
        pinned = pin_library_version(twin, version)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    db.commit()
    return {"twin_id": twin.id, "library_version": pinned}


@router.post("/twins/{twin_id}/recompile")
def recompile_twin(twin_id: str, version: str, explicit: bool = False,
                   db: DBSession = Depends(get_session)):
    """Recompilation against an older version is explicit: without
    explicit=true this refuses."""
    twin = db.get(DigitalTwin, twin_id)
    if twin is None:
        raise HTTPException(404, "Twin not found")
    try:
        decision = recompile_with_version(twin, version, explicit=explicit)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return decision


@router.post("/twins/{twin_id}/rollback-library-version")
def rollback_twin(twin_id: str, payload: RollbackRequest,
                  db: DBSession = Depends(get_session)):
    twin = db.get(DigitalTwin, twin_id)
    if twin is None:
        raise HTTPException(404, "Twin not found")
    try:
        result = rollback_library_version(db, twin, payload.target_version,
                                          payload.actor, payload.reason)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return result
