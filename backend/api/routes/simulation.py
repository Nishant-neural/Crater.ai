"""Phase 7 — Simulation Agent API."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.db.models import DigitalTwin
from backend.db.session import get_session
from backend.simulation.schema import DigitalTwinDefinition
from backend.simulation.simulation_schema import (
    ExperimentRequest, ExperimentResult, HypothesisBatchRequest, HypothesisBatchResult,
)
from backend.simulation.agent import run_experiment, run_hypotheses

router = APIRouter(prefix="/simulation", tags=["simulation agent"])


def _definition(twin: DigitalTwin) -> DigitalTwinDefinition:
    return DigitalTwinDefinition.model_validate(twin.definition)


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
    return HypothesisBatchResult(
        results=run_hypotheses(_definition(twin), twin.state, payload.experiments)
    )
