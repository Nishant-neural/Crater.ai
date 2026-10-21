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
