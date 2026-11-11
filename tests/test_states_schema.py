"""States-schema fix (v10.1).

The v8 prompts ask for states as name/value objects -- {"name": "tightness",
"value": "loose"} with the value "a JSON boolean, number, or string" -- and
the LLM follows that shape. But MachineEntity.states was list[str] and
MachineState.value was str | None, so Pydantic rejected the whole chunk at
parse time, before per-fact quarantine could run. Live failure on the 9 Nov
2026 re-ingestion (chunk f24862c2-498e-442c-a2dc-a8e8d7e3605b):

    entities.0.states.0 {'name': 'tightness', 'value': 'loose'}
    entities.1.states.0 {'name': 'spinning', 'value': 'free'}
    entities.2.states.0 {'name': 'inserted', 'value': False}

Every test here feeds RAW dicts (what the LLM actually returns) through the
real schema -- never hand-built Pydantic objects. That is the exact gap the
v8 tests had: their fixtures were built in code with empty entity states
and string-valued top-level states, so the real LLM shape never reached
model_validate.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db.models import Base, MachineKnowledgeEntity
from backend.knowledge.code_first_integration import build_code_first_payload
from backend.knowledge.component_extraction import (
    _universal_model_from_result,
    persist_machine_knowledge,
)
from backend.knowledge.machine_model import (
    MachineEntity,
    MachineState,
    UniversalMachineModel,
)
from backend.knowledge.schema import MachineKnowledgeExtractionResult
from backend.knowledge.validation import quarantine_machine_model
from backend.simulation.compiler import _coerce_state_value, compile_machine_model


# The exact payload shape from the live failure, plus a numeric value, a
# plain-string state (the pre-v8 shape), and top-level states with
# boolean/number values.
LIVE_PAYLOAD = {
    "entities": [
        {"id": "entity:p1", "name": "Feed Tensioner", "entity_type": "component",
         "states": [{"name": "tightness", "value": "loose"}]},
        {"id": "entity:p2", "name": "Wire Spool", "entity_type": "component",
         "states": [{"name": "spinning", "value": "free"}]},
        {"id": "entity:p3", "name": "Gun Cable Connector", "entity_type": "component",
         "states": [{"name": "inserted", "value": False}]},
        {"id": "entity:p4", "name": "Feed Roller", "entity_type": "component",
         "states": [{"name": "speed", "value": 120}, "running",
                    {"name": "groove", "value": 0.035}]},
    ],
    "states": [
        {"entity_id": "entity:p1", "name": "tension_ok", "value": False},
        {"entity_id": "entity:p2", "name": "feeding", "value": 120},
        {"entity_id": "entity:p3", "name": "inserted", "value": "false"},
    ],
}


def test_exact_live_payload_parses_through_real_schema():
    # This is the call that raised on the live run: the raw LLM dicts go
    # through MachineKnowledgeExtractionResult.model_validate, not through
    # hand-built objects.
    result = MachineKnowledgeExtractionResult.model_validate(LIVE_PAYLOAD)
    by_name = {e.name: e for e in result.entities}
    assert [(s.name, s.value) for s in by_name["Feed Tensioner"].states] == [
        ("tightness", "loose")]
    assert [(s.name, s.value) for s in by_name["Wire Spool"].states] == [
        ("spinning", "free")]
    # Boolean value kept as a boolean, not rejected or stringified.
    assert [(s.name, s.value) for s in by_name["Gun Cable Connector"].states] == [
        ("inserted", False)]
    # Numeric value kept typed; a bare string becomes a named state.
    assert [(s.name, s.value) for s in by_name["Feed Roller"].states] == [
        ("speed", 120), ("running", None), ("groove", 0.035)]


def test_states_survive_universal_model_with_typed_values():
    model = _universal_model_from_result(
        MachineKnowledgeExtractionResult.model_validate(LIVE_PAYLOAD))
    by_id = {s.entity_id: s for s in model.states}
    assert by_id["entity:p1"].value is False
    assert by_id["entity:p2"].value == 120 and isinstance(by_id["entity:p2"].value, int)
    # A string "false" stays a string here; the compiler coerces it.
    assert by_id["entity:p3"].value == "false"
    assert _coerce_state_value(by_id["entity:p3"].value) is False
    assert [(s.name, s.value) for s in model.entities[2].states] == [("inserted", False)]


def test_states_survive_quarantine_and_rule_veto_sees_embedded_states():
    # A rule naming a state that exists ONLY as an entity-embedded state
    # must survive the veto: the chunk extracted that state. (Before v10.1
    # the veto's known-fields loop could not read an entity state's owner,
    # so such a rule was quarantined even though the state was extracted.)
    result = MachineKnowledgeExtractionResult.model_validate({
        "entities": [
            {"id": "entity:p1", "name": "Feed Tensioner", "entity_type": "component",
             "states": [{"name": "tightness", "value": "loose"}]},
            {"id": "entity:p2", "name": "Welding Wire", "entity_type": "component",
             "states": []},
        ],
        "states": [
            {"entity_id": "entity:p2", "name": "feeding", "value": True},
        ],
        "simulation_rules": [
            {"id": "rule:1", "name": "Tension fault",
             "conditions": {"Feed Tensioner.tightness": "loose"},
             "effects": {"Welding Wire.feeding": False},
             "description": "", "evidence": []},
        ],
    })
    model = _universal_model_from_result(result)
    kept, quarantined = quarantine_machine_model(model)
    assert [r["id"] for r in kept.simulation_rules] == ["rule:1"]
    assert not [q for q in quarantined if q["fact_type"] == "simulation_rule"]
    assert len(kept.entities) == 2
    assert [(s.name, s.value) for s in kept.entities[0].states] == [("tightness", "loose")]
    assert [(s.entity_id, s.name, s.value) for s in kept.states] == [
        ("entity:p2", "feeding", True)]


def test_plain_string_states_still_parse():
    # The pre-v8 shape -- states as bare strings -- keeps working.
    result = MachineKnowledgeExtractionResult.model_validate({
        "entities": [
            {"id": "entity:p1", "name": "Switch", "entity_type": "component",
             "states": ["running", "idle"]},
        ],
        "states": [
            {"entity_id": "entity:p1", "name": "running", "value": "true"},
        ],
    })
    assert [(s.name, s.value) for s in result.entities[0].states] == [
        ("running", None), ("idle", None)]
    # Top-level states stay raw dicts at the result stage; they become
    # MachineState objects in _universal_model_from_result.
    assert result.states[0]["value"] == "true"
    model = _universal_model_from_result(result)
    assert model.states[0].value == "true"


def test_malformed_state_does_not_kill_chunk():
    # A state with no usable shape is kept as a name rather than failing
    # the whole chunk: the bad fact is isolated, the chunk survives.
    result = MachineKnowledgeExtractionResult.model_validate({
        "entities": [
            {"id": "entity:p1", "name": "Switch", "entity_type": "component",
             "states": [5, {"value": "loose"}, {"name": "running", "value": True}]},
        ],
    })
    states = [(s.name, s.value) for s in result.entities[0].states]
    assert states[0] == ("5", None)
    assert states[1][0] == ""  # dict without a name: nameless, ignored downstream
    assert states[2] == ("running", True)


def _make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_states_persist_as_json_dicts():
    db = _make_db()
    model = _universal_model_from_result(
        MachineKnowledgeExtractionResult.model_validate(LIVE_PAYLOAD))
    persist_machine_knowledge(db, revision_id=None, source_chunk_id="chunk-a", model=model)
    db.commit()
    rows = db.query(MachineKnowledgeEntity).order_by(MachineKnowledgeEntity.name).all()
    by_name = {r.name: r for r in rows}
    # Plain JSON dicts in the JSON column -- no Pydantic objects leaked.
    assert by_name["Gun Cable Connector"].states == [{"name": "inserted", "value": False}]
    assert by_name["Feed Roller"].states == [
        {"name": "speed", "value": 120},
        {"name": "running", "value": None},
        {"name": "groove", "value": 0.035},
    ]
    assert all(isinstance(s, dict) for r in rows for s in (r.states or []))


def test_embedded_states_reach_code_first_payload():
    # The context builder hands integration the persisted rows as dicts;
    # dict states embedded in an entity must land in payload["states"]
    # with the entity_id rewritten to the canonical id.
    context = {
        "universal_entities": [
            {"source_id": "e1", "name": "Feed Tensioner", "entity_type": "component",
             "properties": {}, "ports": [],
             "states": [{"name": "tightness", "value": "loose"}],
             "evidence": [], "source_chunk_id": "chunk-a"},
            {"source_id": "e2", "name": "Welding Wire", "entity_type": "component",
             "properties": {}, "ports": [], "states": [],
             "evidence": [], "source_chunk_id": "chunk-a"},
        ],
        "universal_relations": [],
        "universal_facts": [
            {"source_id": "s1", "fact_type": "state",
             "payload": {"entity_id": "entity:feed-tensioner", "name": "tension_ok", "value": True},
             "source_chunk_id": "chunk-a"},
        ],
        "behaviors": [], "legacy_components": [], "legacy_relationships": [],
        "procedures": [], "failure_modes": [],
        "schematic_nodes": [], "schematic_edges": [], "expert_knowledge": [],
    }
    payload = build_code_first_payload(context)["payload"]
    embedded = [s for s in payload["states"] if s["name"] == "tightness"]
    assert len(embedded) == 1
    assert embedded[0]["entity_id"] == "entity:feed-tensioner"
    assert embedded[0]["value"] == "loose"


def test_compiler_keeps_native_typed_state_values():
    # Native bool/int values pass through coercion unchanged (a no-op),
    # while string values are still coerced -- the rule can fire either way.
    assert _coerce_state_value(False) is False
    assert _coerce_state_value(120) == 120
    assert _coerce_state_value("false") is False
    model = UniversalMachineModel(
        entities=[
            MachineEntity(id="entity:feed-tensioner", name="Feed Tensioner", entity_type="component"),
            MachineEntity(id="entity:welding-wire", name="Welding Wire", entity_type="component"),
        ],
        states=[
            MachineState(entity_id="entity:feed-tensioner", name="tension_ok", value=False),
            MachineState(entity_id="entity:welding-wire", name="feeding", value=120),
        ],
        simulation_rules=[
            {"id": "rule:1", "name": "Tension fault",
             "conditions": {"entity:feed-tensioner.tension_ok": False},
             "effects": {"entity:welding-wire.feeding": False},
             "description": "p42", "source_ids": ["chunk-a"], "evidence": []},
        ],
    )
    definition, _warnings = compile_machine_model(model, name="test twin")
    tensioner = next(c for c in definition.components if c.id == "entity:feed-tensioner")
    wire = next(c for c in definition.components if c.id == "entity:welding-wire")
    assert tensioner.initial_state == {"tension_ok": False}
    assert wire.initial_state == {"feeding": 120}
    assert len(definition.transitions) == 1
