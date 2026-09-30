import pytest
from pydantic import ValidationError

from backend.knowledge.evidence import MachineEvidence
from backend.knowledge.machine_model import MachineEntity, MachineRelation, UniversalMachineModel
from backend.knowledge.verification import verify_machine_model
from backend.knowledge import component_extraction


def test_claim_status_is_constrained():
    assert MachineEvidence(fact="explicit fact", claim_status="observed").claim_status == "observed"
    assert MachineEvidence(fact="derived fact", claim_status="inferred").claim_status == "inferred"
    assert MachineEvidence(fact="uncertain fact", claim_status="uncertain").claim_status == "uncertain"
    with pytest.raises(ValidationError):
        MachineEvidence(fact="bad status", claim_status="guess")


def test_unknown_ontology_terms_are_warnings_not_errors():
    model = UniversalMachineModel(
        entities=[
            MachineEntity(id="a", name="Pump P1", entity_type="pump"),
            MachineEntity(id="b", name="Custom X", entity_type="custom_device"),
        ],
        relations=[MachineRelation(
            subject_id="a", subject_name="Pump P1", relation_type="custom_relation",
            object_id="b", object_name="Custom X",
        )],
    )
    issues = verify_machine_model(model)
    ontology = [i for i in issues if i.category == "ontology"]
    assert len(ontology) == 2
    assert all(i.severity == "warning" for i in ontology)


def test_invalid_extraction_is_not_treated_as_empty_success(monkeypatch):
    monkeypatch.setattr(component_extraction.gateway, "complete", lambda *args, **kwargs: "not json")
    with pytest.raises(component_extraction.ExtractionError):
        component_extraction.extract_chunk_knowledge("technical text")
