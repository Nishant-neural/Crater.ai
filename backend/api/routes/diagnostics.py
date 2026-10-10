"""
The Phase 2 payoff endpoints: start a troubleshooting session on a symptom,
then keep feeding it observations/measurements until it concludes or
escalates.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from backend.db.models import (
    DiagnosticSession, Product, Revision, Document, Component, ComponentRelationship,
    MachineKnowledgeModelSnapshot, DiagnosticOutcome, Expert, ExpertInterview, ExpertInterviewTurn,
    KnowledgeVersion, ExpertKnowledge, KnowledgeStatus, KnowledgeType, InterviewStatus,
)
from backend.db.session import get_session
from backend.diagnostics.agent import run_turn, start_session
from backend.diagnostics.schema import DiagnosticSessionView, DiagnosticState

router = APIRouter(prefix="/diagnose", tags=["diagnostics"])


class StartDiagnosticRequest(BaseModel):
    product_id: str
    revision_id: str | None = None
    symptom: str


class RespondRequest(BaseModel):
    input: str
    input_kind: str = "observation"  # "symptom" | "observation" | "measurement"


def _to_view(session: DiagnosticSession) -> DiagnosticSessionView:
    return DiagnosticSessionView(
        session_id=session.id,
        product_id=session.product_id,
        revision_id=session.revision_id,
        status=session.status.value,
        state=DiagnosticState.model_validate(session.state),
    )


@router.post("/start", response_model=DiagnosticSessionView)
def start(payload: StartDiagnosticRequest, db: DBSession = Depends(get_session)):
    product = db.get(Product, payload.product_id)
    if not product:
        raise HTTPException(404, "Product not found")
    if payload.revision_id:
        revision = db.get(Revision, payload.revision_id)
        if not revision:
            raise HTTPException(404, "Revision not found")
        if revision.product_id != payload.product_id:
            raise HTTPException(400, "revision_id must belong to product_id")

    session = start_session(db, payload.product_id, payload.revision_id, payload.symptom)
    return _to_view(session)


@router.post("/{session_id}/respond", response_model=DiagnosticSessionView)
def respond(session_id: str, payload: RespondRequest, db: DBSession = Depends(get_session)):
    session = db.get(DiagnosticSession, session_id)
    if not session:
        raise HTTPException(404, "Diagnostic session not found")
    if session.status.value != "active":
        raise HTTPException(400, f"Session already {session.status.value}; start a new session to continue troubleshooting.")

    run_turn(db, session, payload.input, input_kind=payload.input_kind)
    db.refresh(session)
    return _to_view(session)


@router.get("/{session_id}/context")
def get_diagnostic_context(session_id: str, db: DBSession = Depends(get_session)):
    """Return stable machine context for the Phase 9 diagnostic workstation."""
    session = db.get(DiagnosticSession, session_id)
    if not session:
        raise HTTPException(404, "Diagnostic session not found")
    product = db.get(Product, session.product_id)
    revision = db.get(Revision, session.revision_id) if session.revision_id else None

    docs = []
    if revision:
        docs = [
            {"id": d.id, "title": d.title, "doc_type": d.doc_type.value, "page_count": d.page_count}
            for d in db.query(Document).filter(Document.revision_id == revision.id).all()
        ]
    components = []
    relationships = []
    if revision:
        components = [
            {"id": c.id, "name": c.name, "function": c.function, "part_number": c.part_number}
            for c in db.query(Component).filter(Component.revision_id == revision.id).all()
        ]
        comp_ids = {c["id"] for c in components}
        rels = db.query(ComponentRelationship).filter(
            ComponentRelationship.from_component_id.in_(comp_ids or [""]),
            ComponentRelationship.to_component_id.in_(comp_ids or [""]),
        ).all()
        by_id = {c["id"]: c["name"] for c in components}
        relationships = [
            {"id": r.id, "from": by_id.get(r.from_component_id, r.from_component_id),
             "to": by_id.get(r.to_component_id, r.to_component_id),
             "type": r.relation_type.value, "description": r.description}
            for r in rels
        ]

    snapshot = None
    if revision:
        row = db.query(MachineKnowledgeModelSnapshot).filter_by(
            revision_id=revision.id
        ).order_by(MachineKnowledgeModelSnapshot.version.desc()).first()
        if row:
            model = row.model or {}
            snapshot = {
                "version": row.version,
                "entity_count": len(model.get("entities", [])),
                "relation_count": len(model.get("relations", [])),
                "fact_count": sum(len(model.get(k, [])) for k in ("ports", "quantities", "states", "events", "constraints")),
                "conflict_count": len(model.get("conflicts", [])),
                "completeness": model.get("completeness", {}),
            }

    return {
        "product": (
            {"id": product.id, "manufacturer": product.manufacturer, "family": product.family, "model": product.model}
            if product else None
        ),
        "revision": (
            {"id": revision.id, "label": revision.label, "parent_revision_id": revision.parent_revision_id}
            if revision else None
        ),
        "documents": docs,
        "components": components,
        "relationships": relationships,
        "knowledge": snapshot,
    }


@router.get("/{session_id}", response_model=DiagnosticSessionView)
def get_session_view(session_id: str, db: DBSession = Depends(get_session)):
    session = db.get(DiagnosticSession, session_id)
    if not session:
        raise HTTPException(404, "Diagnostic session not found")
    return _to_view(session)


class OutcomeFeedbackRequest(BaseModel):
    outcome: str  # fixed | not_fixed | inconclusive
    confirmed_cause: str | None = None
    repair_performed: str | None = None
    technician_notes: str | None = None
    submit_correction_for_review: bool = False
    technician_name: str = "Diagnostic technician"


@router.post("/{session_id}/outcome")
def record_outcome(session_id: str, payload: OutcomeFeedbackRequest, db: DBSession = Depends(get_session)):
    """Record repair result. Optional corrections become draft expert knowledge for review."""
    if payload.outcome not in {"fixed", "not_fixed", "inconclusive"}:
        raise HTTPException(422, "outcome must be fixed, not_fixed, or inconclusive")
    session = db.get(DiagnosticSession, session_id)
    if not session:
        raise HTTPException(404, "Diagnostic session not found")
    existing = db.query(DiagnosticOutcome).filter_by(session_id=session_id).first()
    if existing:
        raise HTTPException(409, "Outcome already recorded for this session")
    state = session.state or {}
    outcome = DiagnosticOutcome(
        session_id=session.id, product_id=session.product_id, revision_id=session.revision_id,
        outcome=payload.outcome, confirmed_cause=payload.confirmed_cause,
        repair_performed=payload.repair_performed, technician_notes=payload.technician_notes,
        review_status="pending_review" if payload.submit_correction_for_review and (payload.confirmed_cause or payload.technician_notes) else "recorded",
    )
    db.add(outcome)
    db.flush()
    if outcome.review_status == "pending_review":
        expert = Expert(name=(payload.technician_name.strip() or "Diagnostic technician"), role="Repair outcome feedback")
        db.add(expert); db.flush()
        interview = ExpertInterview(
            expert_id=expert.id, product_id=session.product_id, revision_id=session.revision_id,
            topic=f"Outcome feedback for diagnostic session {session.id}",
            status=InterviewStatus.completed,
        )
        db.add(interview); db.flush()
        transcript = [
            ("interviewer", f"Diagnostic symptom: {'; '.join(state.get('symptoms', []))}"),
            ("expert", f"Reported outcome: {payload.outcome}. Repair performed: {payload.repair_performed or 'not specified'}. Confirmed cause/correction: {payload.confirmed_cause or 'not specified'}. Notes: {payload.technician_notes or 'none'}."),
        ]
        for idx, (speaker, content) in enumerate(transcript, 1):
            db.add(ExpertInterviewTurn(interview_id=interview.id, sequence=idx, speaker=speaker, content=content))
        version = KnowledgeVersion(interview_id=interview.id, revision_id=session.revision_id, version=1, status=KnowledgeStatus.draft)
        db.add(version); db.flush()
        db.add(ExpertKnowledge(
            knowledge_version_id=version.id, knowledge_type=KnowledgeType.heuristic,
            title=f"Field outcome: {payload.confirmed_cause or payload.outcome}",
            symptom="; ".join(state.get("symptoms", [])) or None,
            condition=payload.technician_notes, action=payload.repair_performed,
            failure_mode=payload.confirmed_cause, confidence=0.5,
            applicable_models=[], applicable_revisions=[], evidence_turn_ids=[],
            source_quote=f"Diagnostic session {session.id}; outcome={payload.outcome}",
            safety_notes=["Draft derived from technician feedback; verify before operational use."],
        ))
        outcome.knowledge_version_id = version.id
    db.commit()
    return {"outcome_id": outcome.id, "session_id": session_id, "outcome": outcome.outcome,
            "review_status": outcome.review_status, "knowledge_version_id": outcome.knowledge_version_id,
            "message": "Outcome recorded. Any generated knowledge remains draft until reviewed and approved."}


@router.get("/{session_id}/outcome")
def get_outcome(session_id: str, db: DBSession = Depends(get_session)):
    row = db.query(DiagnosticOutcome).filter_by(session_id=session_id).first()
    if not row:
        raise HTTPException(404, "No repair outcome recorded")
    return {"outcome_id": row.id, "session_id": row.session_id, "outcome": row.outcome,
            "confirmed_cause": row.confirmed_cause, "repair_performed": row.repair_performed,
            "technician_notes": row.technician_notes, "review_status": row.review_status,
            "knowledge_version_id": row.knowledge_version_id}


@router.get("/{session_id}/next-test")
def most_informative_test(session_id: str, db: DBSession = Depends(get_session)):
    """Rank documented diagnostic tests by how well their cause scope separates live hypotheses."""
    session = db.get(DiagnosticSession, session_id)
    if not session:
        raise HTTPException(404, "Diagnostic session not found")
    state = session.state or {}
    hypotheses = state.get("hypotheses", [])
    if len(hypotheses) < 2:
        return {"available": False, "reason": "At least two competing hypotheses are needed to rank discriminating tests.", "tests": []}
    from backend.db.models import FailureMode
    modes = db.query(FailureMode).filter_by(revision_id=session.revision_id).all() if session.revision_id else []
    names = [str(h.get("cause", "")).casefold() for h in hypotheses[:5]]
    tests = []
    seen = set()
    for mode in modes:
        if not mode.diagnostic_test:
            continue
        key = mode.diagnostic_test.strip().casefold()
        if key in seen:
            continue
        seen.add(key)
        causes = [str(c).casefold() for c in (mode.possible_causes or [])]
        matched = [i for i, name in enumerate(names) if any(c and (c in name or name in c) for c in causes)]
        # Reward tests tied to some but not all hypotheses; do not pretend to calculate true information gain.
        separation = min(len(matched), len(names) - len(matched))
        coverage = len(matched) / len(names)
        score = separation + (0.25 if 0 < coverage < 1 else 0)
        tests.append({"test": mode.diagnostic_test, "expected_observation": mode.expected_observation,
                      "related_symptom": mode.symptom, "matched_hypotheses": [hypotheses[i].get("cause") for i in matched],
                      "score": round(score, 3), "evidence_chunk_id": mode.source_chunk_id,
                      "selection_basis": "heuristic overlap between documented possible causes and current hypotheses"})
    tests.sort(key=lambda x: (x["score"], len(x["matched_hypotheses"])), reverse=True)
    return {"available": bool(tests), "method": "heuristic_discrimination_not_probabilistic_information_gain",
            "tests": tests[:5], "note": "Only documented tests are shown; a low score or empty list means the stored knowledge cannot currently distinguish these hypotheses."}


@router.get("/{session_id}/guided-checks")
def guided_physical_checks(session_id: str, db: DBSession = Depends(get_session)):
    """Return physical checks grounded in documented tests and located schematic labels."""
    session = db.get(DiagnosticSession, session_id)
    if not session:
        raise HTTPException(404, "Diagnostic session not found")
    state = session.state or {}
    hypotheses = state.get("hypotheses", [])
    modes = db.query(FailureMode).filter_by(revision_id=session.revision_id).all() if session.revision_id else []
    docs = db.query(Document).filter_by(revision_id=session.revision_id).all() if session.revision_id else []
    doc_ids = [d.id for d in docs]
    from backend.db.models import SchematicNode
    nodes = db.query(SchematicNode).filter(SchematicNode.document_id.in_(doc_ids or [""])).all()
    labels = sorted({n.label for n in nodes if n.label})
    checks = []
    for mode in modes:
        if not mode.diagnostic_test:
            continue
        cause_text = " ".join(mode.possible_causes or []).casefold()
        matched = [h for h in hypotheses[:5] if any(w and w in cause_text for w in str(h.get("cause", "")).casefold().split() if len(w) > 3)]
        if not matched and hypotheses:
            continue
        checks.append({
            "instruction": mode.diagnostic_test,
            "target_hypotheses": [h.get("cause") for h in matched],
            "expected_observation": mode.expected_observation,
            "source_chunk_id": mode.source_chunk_id,
            "candidate_schematic_labels": [label for label in labels if label.casefold() in mode.diagnostic_test.casefold() or mode.diagnostic_test.casefold() in label.casefold()],
            "safety_note": "Follow the machine's approved isolation/LOTO procedure. Do not probe energized circuits unless the manufacturer's procedure and site rules explicitly permit it.",
            "limits": "No voltage, pin number, or pass/fail threshold is inferred unless explicitly present in the stored diagnostic test.",
        })
    return {"session_id": session_id, "checks": checks[:10],
            "message": "Instructions are limited to stored documented tests; schematic label matches are candidates, not verified pin-level wiring."}
