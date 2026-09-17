"""Phase 6 tests for deterministic functional digital twins."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from backend.db.models import Base, Product, Revision, DigitalTwinEvent
from backend.simulation.engine import DigitalTwinEngine, demo_definition
from backend.simulation.schema import TwinCommand
from backend.simulation.service import create_demo_twin, execute


def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def seed():
    session = db()
    p = Product(manufacturer="Acme", family="Drive", model="D-1")
    session.add(p); session.flush()
    r = Revision(product_id=p.id, label="Rev A")
    session.add(r); session.commit()
    return session, p, r


def test_demo_reaches_safe_idle_state():
    engine = DigitalTwinEngine(demo_definition())
    engine.step()
    assert engine.state["components"]["safety_relay"]["energized"] is True
    assert engine.state["components"]["controller"]["enabled"] is True
    assert engine.state["components"]["motor"]["running"] is False


def test_fault_reproduces_motor_stop_and_recovery():
    engine = DigitalTwinEngine(demo_definition())
    engine.step()
    engine.command("set_signal", "start_command", value=True)
    assert engine.state["components"]["motor"]["running"] is True
    engine.command("set_component_state", "x12", "connected", False)
    assert engine.state["components"]["motor"]["running"] is False
    engine.command("set_component_state", "x12", "connected", True)
    assert engine.state["components"]["safety_relay"]["energized"] is True
    assert engine.state["components"]["controller"]["enabled"] is True


def test_persisted_twin_has_audit_event_and_revision_scope():
    session, p, r = seed()
    twin = create_demo_twin(session, p.id, r.id)
    twin, event = execute(session, twin, TwinCommand(command="set_signal", target="start_command", value=True))
    assert twin.revision_id == r.id
    assert twin.state["components"]["motor"]["running"] is True
    assert event.state_before["components"]["motor"]["running"] is False
    assert session.query(DigitalTwinEvent).count() == 1
