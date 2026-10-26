from backend.knowledge.machine_model import MachineBehavior, MachineConstraint, MachineProcedure
from backend.knowledge.evidence import MachineEvidence


def test_behavior_legacy_aliases_normalize_without_losing_payload():
    behavior = MachineBehavior.model_validate({"component": "Welding arm", "behavior": "produce toxic fumes"})
    assert behavior.id
    assert behavior.subject_name == "Welding arm"
    assert behavior.description == "produce toxic fumes"
    assert behavior.subject_id.startswith("unresolved:")


def test_evidence_reference_without_fact_is_explicitly_marked():
    evidence = MachineEvidence.model_validate({"source_id": "abc", "claim_status": "observed"})
    assert "abc" in evidence.metadata["source_id"]
    assert evidence.metadata["normalization_warning"] == "missing_fact_text"


def test_procedure_aliases_and_required_defaults():
    procedure = MachineProcedure.model_validate({"title": "Check pressure", "instructions": ["Read gauge"]})
    assert procedure.name == "Check pressure"
    assert procedure.steps == ["Read gauge"]
    assert procedure.id



def test_generic_normalizer_preserves_original_and_maps_aliases():
    from backend.knowledge.normalization import normalize_payload

    result = normalize_payload({"component": "Pump", "behavior": "overheats", "extra_signal": 17}, kind="behavior")
    assert result["name"] == "Pump"
    assert result["description"] == "overheats"
    assert result["extra_signal"] == 17
    assert result["_original_payload"]["component"] == "Pump"
    assert "generated_stable_id" in result["normalization_warnings"]


def test_semantic_validation_returns_unresolved_reference_ledger():
    from backend.knowledge.machine_model import MachineRelation, UniversalMachineModel
    from backend.knowledge.validation import validate_machine_model_with_ledger

    model = UniversalMachineModel(relations=[MachineRelation(
        subject_id="missing-a", subject_name="A", relation_type="connects",
        object_id="missing-b", object_name="B",
    )])
    issues, ledger = validate_machine_model_with_ledger(model)
    assert any("missing subject entity" in issue for issue in issues)
    assert any(item["fact_type"] == "relation" and "unresolved_entity_reference" in item["reason"] for item in ledger)
