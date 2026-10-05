"""Machine-model-backed simulation and diagnostic verification API."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.db.models import DigitalTwin, MachineKnowledgeModelSnapshot, Product, Revision
from backend.db.session import get_session
from backend.simulation.schema import DigitalTwinDefinition
from backend.simulation.simulation_schema import ExperimentRequest, ExperimentResult, HypothesisBatchRequest, HypothesisBatchResult
from backend.simulation.agent import run_experiment, run_hypotheses
from backend.simulation.compiler import compile_machine_model
from backend.knowledge.global_integration import _model_from_payload
from backend.simulation.service import create_twin, snapshot

router = APIRouter(prefix="/simulation", tags=["simulation"])


def _definition(twin: DigitalTwin) -> DigitalTwinDefinition:
    return DigitalTwinDefinition.model_validate(twin.definition)


@router.post("/revisions/{revision_id}/compile")
def compile_revision(revision_id: str, db: Session = Depends(get_session)):
    revision = db.get(Revision, revision_id)
    if not revision:
        raise HTTPException(404, "Revision not found")
    product = db.get(Product, revision.product_id)
    row = db.query(MachineKnowledgeModelSnapshot).filter_by(revision_id=revision_id).order_by(MachineKnowledgeModelSnapshot.version.desc()).first()
    if not row:
        raise HTTPException(409, "No canonical machine model has been integrated for this revision")
    model = _model_from_payload(row.model)
    definition, warnings = compile_machine_model(
        model,
        name=f"{product.manufacturer} {product.model} — {revision.label} Simulation" if product else f"Revision {revision.label} Simulation",
        description="Functional simulation compiled from Crater.ai's canonical machine model.",
    )
    # Replace the latest compiled twin for this revision; historical event logs remain attached to older twins.
    twin = DigitalTwin(
        product_id=revision.product_id,
        revision_id=revision_id,
        name=definition.name,
        description=definition.description,
        definition=definition.model_dump(mode="json"),
        state={"signals": dict(definition.initial_signals), "components": {c.id: dict(c.initial_state) for c in definition.components}},
        model_version=row.version,
    )
    db.add(twin)
    db.commit()
    db.refresh(twin)
    return {"twin_id": twin.id, "revision_id": revision_id, "model_version": row.version, "warnings": warnings, "definition": definition.model_dump(mode="json"), "snapshot": snapshot(twin)}


@router.get("/revisions/{revision_id}/twin")
def get_revision_twin(revision_id: str, db: Session = Depends(get_session)):
    twin = db.query(DigitalTwin).filter_by(revision_id=revision_id).order_by(DigitalTwin.created_at.desc()).first()
    if not twin:
        raise HTTPException(404, "No simulation twin has been compiled for this revision")
    return {"twin_id": twin.id, "revision_id": revision_id, "definition": twin.definition, "snapshot": snapshot(twin)}


@router.get("/twins/{twin_id}/context")
def twin_context(twin_id: str, db: Session = Depends(get_session)):
    twin = db.get(DigitalTwin, twin_id)
    if not twin:
        raise HTTPException(404, "Digital twin not found")
    definition = _definition(twin)
    return {"twin_id": twin.id, "revision_id": twin.revision_id, "definition": definition.model_dump(mode="json"), "snapshot": snapshot(twin)}


@router.post("/twins/{twin_id}/experiment", response_model=ExperimentResult)
def experiment(twin_id: str, payload: ExperimentRequest, db: Session = Depends(get_session)):
    twin = db.get(DigitalTwin, twin_id)
    if not twin:
        raise HTTPException(404, "Digital twin not found")
    return run_experiment(_definition(twin), twin.state, payload)


@router.post("/twins/{twin_id}/hypotheses", response_model=HypothesisBatchResult)
def hypotheses(twin_id: str, payload: HypothesisBatchRequest, db: Session = Depends(get_session)):
    twin = db.get(DigitalTwin, twin_id)
    if not twin:
        raise HTTPException(404, "Digital twin not found")
    return HypothesisBatchResult(results=run_hypotheses(_definition(twin), twin.state, payload.experiments))
