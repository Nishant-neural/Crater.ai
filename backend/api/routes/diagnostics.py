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
    MachineKnowledgeModelSnapshot,
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
