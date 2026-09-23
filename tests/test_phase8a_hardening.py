from backend.knowledge.evidence import MachineEvidence
from backend.knowledge.machine_model import (
    MachineEntity, MachineFailureMode, MachineProcedure, MachineState,
    UniversalMachineModel,
)
from backend.knowledge.validation import invalid_fact_keys, validate_facts


def test_evidence_retains_revision():
    evidence = MachineEvidence(
        fact="Pump pressure is 4 bar",
        source_document="manual.pdf",
        page=12,
        revision_id="rev-a",
    )
    assert evidence.revision_id == "rev-a"


def test_procedure_and_failure_mode_are_universal_primitives():
    model = UniversalMachineModel(
        entities=[MachineEntity(id="pump", name="Pump", entity_type="pump")],
        procedures=[MachineProcedure(
            id="proc-1", name="Prime pump", procedure_type="maintenance",
            steps=["Open valve", "Start pump"],
        )],
        failure_modes=[MachineFailureMode(
            id="failure-1", name="Pump cavitation", symptom="Noise",
            possible_causes=["Low inlet pressure"],
        )],
    )
    facts = dict((kind, payload) for kind, _, payload in model.all_facts())
    assert "procedure" in facts
    assert "failure_mode" in facts


def test_validation_is_fact_level():
    model = UniversalMachineModel(
        entities=[MachineEntity(id="pump", name="Pump", entity_type="pump")],
        relations=[],
        states=[
            MachineState(entity_id="pump", name="running", value="true"),
            MachineState(entity_id="pump", name="running", value="false"),
        ],
    )
    issues = validate_facts(model)
    assert any(i.fact_type == "state" and i.severity == "warning" for i in issues)
    assert not invalid_fact_keys(model)
