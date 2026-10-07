from backend.knowledge.machine_model import UniversalMachineModel, MachineEntity, MachineRelation, MachineState
from backend.simulation.compiler import compile_machine_model
from backend.simulation.engine import DigitalTwinEngine

def test_compiler_preserves_machine_topology_and_state_without_inventing_rules():
    model = UniversalMachineModel(
        entities=[
            MachineEntity(id="entity:m1", name="Motor M1", entity_type="motor", properties={"initial_state": {"running": False}}),
            MachineEntity(id="entity:c1", name="Controller C1", entity_type="controller"),
        ],
        relations=[MachineRelation(subject_id="entity:c1", subject_name="Controller C1", relation_type="controls", object_id="entity:m1", object_name="Motor M1")],
        states=[MachineState(entity_id="entity:m1", name="Running", value="false")],
    )
    definition, warnings = compile_machine_model(model, name="Test machine")
    assert {c.id for c in definition.components} == {"entity:m1", "entity:c1"}
    assert definition.metadata["topology"][0]["relation_type"] == "controls"
    assert definition.transitions == []
    assert warnings
    engine = DigitalTwinEngine(definition)
    assert engine.state["components"]["entity:m1"]["running"] is False
