"""Phase 6 — Functional Digital Twin API."""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from backend.db.models import DigitalTwin, DigitalTwinEvent
from backend.db.session import get_session
from backend.simulation.schema import TwinCreate, TwinCommand, TwinView
from backend.simulation.service import create_twin, create_demo_twin, execute, snapshot

router = APIRouter(prefix="/digital-twins", tags=["digital twin"])


def _view(twin: DigitalTwin) -> TwinView:
    from backend.simulation.schema import DigitalTwinDefinition
    return TwinView(
        id=twin.id, product_id=twin.product_id, revision_id=twin.revision_id,
        name=twin.name, status=twin.status.value, model_version=twin.model_version,
        definition=DigitalTwinDefinition.model_validate(twin.definition),
        snapshot=snapshot(twin),
    )


@router.post("", response_model=TwinView)
def create(payload: TwinCreate, db: Session = Depends(get_session)):
    try:
        return _view(create_twin(db, **payload.model_dump()))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/demo/{product_id}/{revision_id}", response_model=TwinView)
def demo(product_id: str, revision_id: str, db: Session = Depends(get_session)):
    try:
        return _view(create_demo_twin(db, product_id, revision_id))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("", response_model=list[TwinView])
def list_twins(db: Session = Depends(get_session)):
    return [_view(t) for t in db.query(DigitalTwin).order_by(DigitalTwin.created_at.desc()).all()]


@router.get("/{twin_id}", response_model=TwinView)
def get_twin(twin_id: str, db: Session = Depends(get_session)):
    twin = db.get(DigitalTwin, twin_id)
    if not twin:
        raise HTTPException(404, "Digital twin not found")
    return _view(twin)


@router.post("/{twin_id}/commands", response_model=TwinView)
def command(twin_id: str, payload: TwinCommand, db: Session = Depends(get_session)):
    twin = db.get(DigitalTwin, twin_id)
    if not twin:
        raise HTTPException(404, "Digital twin not found")
    try:
        execute(db, twin, payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _view(twin)


@router.get("/{twin_id}/events")
def events(twin_id: str, db: Session = Depends(get_session)):
    if not db.get(DigitalTwin, twin_id):
        raise HTTPException(404, "Digital twin not found")
    return [
        {"id": e.id, "event_type": e.event_type.value, "command": e.command,
         "state_before": e.state_before, "state_after": e.state_after,
         "trace": e.trace, "created_at": e.created_at}
        for e in db.query(DigitalTwinEvent).filter_by(twin_id=twin_id).order_by(DigitalTwinEvent.created_at).all()
    ]
