"""Visual twin: backend layout + endpoint.

The layout must be deterministic (same twin -> same picture), render
every component (unconnected ones are shown, not hidden), draw edges
only from the documented topology, and never invent a node for a
relation endpoint that is not a twin component. The verification
result must carry the three states the frontend playback steps through.
"""
from backend.simulation.schema import DigitalTwinDefinition
from backend.simulation.verification import run_verified_repair
from backend.simulation.visual import layout_twin


def _definition():
    return DigitalTwinDefinition(
        name="OMNIPRO 220 — test twin",
        components=[
            {"id": "entity:wingnut", "name": "Wingnut", "component_type": "other",
             "initial_state": {"tight": True}, "source_ids": []},
            {"id": "entity:wire-spool", "name": "Wire Spool", "component_type": "other",
             "initial_state": {"spinning_freely": False}, "source_ids": []},
            {"id": "entity:feed-tensioner", "name": "Feed Tensioner", "component_type": "other",
             "initial_state": {"tension_ok": True}, "source_ids": []},
            {"id": "entity:welding-wire", "name": "Welding Wire", "component_type": "other",
             "initial_state": {"feeding": True}, "source_ids": []},
            {"id": "entity:fan", "name": "Fan", "component_type": "other",
             "initial_state": {"running": True}, "source_ids": []},
        ],
        initial_signals={},
        transitions=[
            {"id": "seed:wingnut-loose", "name": "Wingnut loose",
             "conditions": {"entity:wingnut.tight": False},
             "effects": {"entity:wire-spool.spinning_freely": True},
             "description": "", "source_ids": [], "evidence": []},
            {"id": "seed:wingnut-tight", "name": "Wingnut tight",
             "conditions": {"entity:wingnut.tight": True},
             "effects": {"entity:wire-spool.spinning_freely": False},
             "description": "", "source_ids": [], "evidence": []},
        ],
        metadata={"topology": [
            {"subject_id": "entity:wingnut", "subject_name": "Wingnut",
             "relation_type": "secures", "object_id": "entity:wire-spool", "object_name": "Wire Spool"},
            {"subject_id": "entity:feed-tensioner", "subject_name": "Feed Tensioner",
             "relation_type": "tensions", "object_id": "entity:welding-wire", "object_name": "Welding Wire"},
            # Endpoint is not a twin component: dropped, never invented.
            {"subject_id": "entity:wingnut", "subject_name": "Wingnut",
             "relation_type": "secures", "object_id": "entity:ghost", "object_name": "Ghost"},
        ]},
    )


def _state():
    return {"signals": {}, "components": {
        "entity:wingnut": {"tight": True},
        "entity:wire-spool": {"spinning_freely": False},
        "entity:feed-tensioner": {"tension_ok": True},
        "entity:welding-wire": {"feeding": True},
        "entity:fan": {"running": True},
    }}


def test_layout_is_deterministic():
    first = layout_twin(_definition(), _state())
    second = layout_twin(_definition(), _state())
    assert first == second


def test_roots_in_layer_zero_unconnected_last():
    out = layout_twin(_definition(), _state())
    by_id = {n["id"]: n for n in out["nodes"]}
    assert len(out["nodes"]) == 5
    # Wingnut and Feed Tensioner have no incoming relation: layer 0.
    assert by_id["entity:wingnut"]["layer"] == 0
    assert by_id["entity:feed-tensioner"]["layer"] == 0
    # Their targets sit one layer downstream.
    assert by_id["entity:wire-spool"]["layer"] == 1
    assert by_id["entity:welding-wire"]["layer"] == 1
    # Fan has no relations at all: shown, in the last layer.
    assert by_id["entity:fan"]["layer"] == max(n["layer"] for n in out["nodes"])
    assert by_id["entity:fan"]["x"] > by_id["entity:wingnut"]["x"]


def test_edges_only_from_documented_topology():
    out = layout_twin(_definition(), _state())
    assert {(e["from"], e["to"]) for e in out["edges"]} == {
        ("entity:wingnut", "entity:wire-spool"),
        ("entity:feed-tensioner", "entity:welding-wire"),
    }
    assert all(e["label"] for e in out["edges"])


def test_layout_carries_live_state_and_transitions():
    out = layout_twin(_definition(), _state())
    by_id = {n["id"]: n for n in out["nodes"]}
    assert by_id["entity:wingnut"]["state"] == {"tight": True}
    assert by_id["entity:wingnut"]["initial_state"] == {"tight": True}
    assert {t["id"] for t in out["transitions"]} == {"seed:wingnut-loose", "seed:wingnut-tight"}
    assert out["canvas"]["width"] > 0 and out["canvas"]["height"] > 0


def test_visual_endpoint_returns_layout():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from backend.api.routes.simulation import twin_visual
    from backend.db.models import Base, DigitalTwin, TwinStatus

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    twin = DigitalTwin(
        product_id="prod-1", revision_id="rev-1", name="test twin",
        description="", definition=_definition().model_dump(mode="json"),
        state=_state(), model_version=1, status=TwinStatus.active,
    )
    db.add(twin)
    db.commit()
    out = twin_visual(twin.id, db)
    assert out["twin_id"] == twin.id
    assert out["status"] == "active"
    assert len(out["nodes"]) == 5
    assert len(out["edges"]) == 2
    assert len(out["transitions"]) == 2


def test_verified_repair_result_carries_three_states_for_playback():
    # The frontend playback steps through these three states; if any is
    # missing the visual twin has nothing to animate.
    definition = _definition()
    outcome = run_verified_repair(
        definition, _state(),
        fault_component_id="entity:wingnut", repair_name="Tighten wingnut",
        hypothesis="Tightening the wingnut stops the spool spinning freely.",
    )
    assert outcome.available and outcome.verdict == "validated"
    result = outcome.result
    assert result.baseline_state["components"]["entity:wire-spool"] == {"spinning_freely": False}
    assert result.fault_state["components"]["entity:wire-spool"] == {"spinning_freely": True}
    assert result.final_state["components"]["entity:wire-spool"] == {"spinning_freely": False}
    assert result.fault_trace and result.intervention_trace
