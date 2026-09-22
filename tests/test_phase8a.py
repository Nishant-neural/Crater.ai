from backend.knowledge.evidence import MachineEvidence
from backend.knowledge.machine_model import (
    MachineBehavior,
    MachineEntity,
    MachineRelation,
    UniversalMachineModel,
)
from backend.knowledge.validation import validate_machine_model


def test_universal_machine_model_round_trip():
    model = UniversalMachineModel(
        entities=[
            MachineEntity(id="pump-1", name="Pump P1", entity_type="pump"),
            MachineEntity(id="valve-3", name="Valve V3", entity_type="valve"),
        ],
        relations=[
            MachineRelation(
                subject_id="pump-1",
                subject_name="Pump P1",
                relation_type="connected_to",
                object_id="valve-3",
                object_name="Valve V3",
                description="Pump P1 connected to Valve V3",
                evidence=[MachineEvidence(fact="Pump P1 connected to Valve V3", source_document="manual.pdf", page=42, confidence=0.94)],
            )
        ],
        behaviors=[
            MachineBehavior(
                id="behavior-1",
                subject_id="pump-1",
                subject_name="Pump P1",
                description="increases fluid pressure",
            )
        ],
    )

    issues = validate_machine_model(model)
    assert issues == []
    assert model.entities[0].name == "Pump P1"
    assert model.relations[0].relation_type == "connected_to"


def test_validation_detects_dangling_relation_and_conflict():
    model = UniversalMachineModel(
        entities=[
            MachineEntity(id="pump-1", name="Pump P1", entity_type="pump"),
        ],
        relations=[
            MachineRelation(
                subject_id="pump-1",
                subject_name="Pump P1",
                relation_type="connected_to",
                object_id="missing-id",
                object_name="Valve V3",
                description="dangling reference",
            ),
        ],
        states=[
            {"entity_id": "pump-1", "name": "running", "value": "true"},
            {"entity_id": "pump-1", "name": "running", "value": "false"},
        ],
    )

    issues = validate_machine_model(model)
    assert any("missing" in issue.lower() for issue in issues)
    assert any("conflict" in issue.lower() for issue in issues)
