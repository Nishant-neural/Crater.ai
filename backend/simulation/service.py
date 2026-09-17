"""Persistence boundary for Phase 6 digital twins."""
from __future__ import annotations
from sqlalchemy.orm import Session
from backend.db.models import DigitalTwin, DigitalTwinEvent, TwinEventType, TwinStatus, Product, Revision
from .engine import DigitalTwinEngine, demo_definition
from .schema import DigitalTwinDefinition, TwinCommand


def _engine(twin: DigitalTwin) -> DigitalTwinEngine:
    return DigitalTwinEngine(DigitalTwinDefinition.model_validate(twin.definition), twin.state)


def create_twin(db: Session, product_id: str, revision_id: str, definition: DigitalTwinDefinition) -> DigitalTwin:
    product = db.get(Product, product_id)
    revision = db.get(Revision, revision_id)
    if not product or not revision:
        raise ValueError("Product or revision not found")
    if revision.product_id != product_id:
        raise ValueError("Revision does not belong to the selected product")
    engine = DigitalTwinEngine(definition)
    engine.step()
    twin = DigitalTwin(
        product_id=product_id, revision_id=revision_id, name=definition.name,
        description=definition.description, definition=definition.model_dump(),
        state=engine.state, model_version=1, status=TwinStatus.active,
    )
    db.add(twin)
    db.commit()
    db.refresh(twin)
    return twin


def create_demo_twin(db: Session, product_id: str, revision_id: str) -> DigitalTwin:
    return create_twin(db, product_id, revision_id, demo_definition())


def execute(db: Session, twin: DigitalTwin, command: TwinCommand) -> tuple[DigitalTwin, DigitalTwinEvent]:
    if twin.status != TwinStatus.active:
        raise ValueError("Digital twin is archived")
    before = dict(twin.state)
    engine = _engine(twin)
    trace = engine.command(command.command, command.target, command.field, command.value, command.reason)
    twin.state = engine.state
    event_type = TwinEventType.reset if command.command == "reset" else TwinEventType.command
    event = DigitalTwinEvent(
        twin_id=twin.id, event_type=event_type, command=command.model_dump(),
        state_before=before, state_after=engine.state, trace=trace,
    )
    db.add(event)
    db.commit()
    db.refresh(twin)
    return twin, event


def snapshot(twin: DigitalTwin):
    return _engine(twin).snapshot(twin.id, twin.name, twin.status.value)
