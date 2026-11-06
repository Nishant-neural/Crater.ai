"""Regression tests for the multi-chunk batch extraction fix (Nov 2026).

These tests use a mocked gateway, so no LLM call is made. They cover only
the changes in this patch; they do not prove the new batch prompt makes a
live model return non-empty results -- that still needs the gold-set test
on the user's side with the new prompt.
"""
import json

import pytest

from backend.knowledge import component_extraction as ce
from backend.knowledge.normalization import normalize_payload


def _mock_gateway(monkeypatch, payload):
    monkeypatch.setattr(ce.gateway, "complete", lambda *args, **kwargs: json.dumps(payload))


def test_dict_evidence_normalized_to_list():
    item = {"name": "Wire Unraveling", "evidence": {"fact": "If Wire Spool can spin freely", "source_type": "text"}}
    out = normalize_payload(item, kind="failure_mode")
    assert out["evidence"] == [{"fact": "If Wire Spool can spin freely", "source_type": "text"}]


def test_string_evidence_normalized_to_list():
    item = {"name": "X", "evidence": "some fact"}
    out = normalize_payload(item, kind="failure_mode")
    assert out["evidence"] == [{"fact": "some fact"}]


def test_single_p10_dict_evidence_no_longer_loses_chunk(monkeypatch):
    # The crater-3 p10 loss, reproduced: rich output whose only defect was
    # failure_modes evidence being a dict, not a list.
    payload = {
        "components": [{"name": "Wingnut", "function": "Secures the spool"}],
        "relationships": [],
        "procedures": [{"name": "Spool Installation", "procedure_type": "installation", "steps": ["Place the spool"]}],
        "failure_modes": [
            {"name": "Wire Unraveling", "symptoms": ["wire unravels"],
             "evidence": {"fact": "If Wire Spool can spin freely", "source_type": "text"}},
        ],
    }
    _mock_gateway(monkeypatch, payload)
    result, model = ce.extract_chunk_knowledge("spool text")
    assert result.components[0].name == "Wingnut"
    assert result.procedures[0].name == "Spool Installation"
    assert len(model.failure_modes) == 1
    assert model.failure_modes[0].evidence[0].fact == "If Wire Spool can spin freely"


def test_batch_string_components_become_named_components(monkeypatch):
    payload = {"chunks": [
        {"source_chunk_id": "c1", "knowledge": {
            "components": ["Chipping Hammer", "Wire Brush"],
            "procedures": [{"name": "Clean", "procedure_type": "maintenance", "steps": ["Clean off slag"]}],
        }},
        {"source_chunk_id": "c2", "knowledge": {"components": []}},
    ]}
    _mock_gateway(monkeypatch, payload)
    out = ce.extract_chunk_knowledge_batch([("c1", "text one"), ("c2", "text two")])
    assert [c.name for c in out["c1"][0].components] == ["Chipping Hammer", "Wire Brush"]


def test_batch_quantity_parameter_becomes_name(monkeypatch):
    payload = {"chunks": [
        {"source_chunk_id": "c1", "knowledge": {
            "quantities": [{"id": "quantity:q1", "entity_id": "", "parameter": "Power Input (Mig)", "value": "120 VAC / 60 Hz", "unit": "VAC"}],
        }},
        {"source_chunk_id": "c2", "knowledge": {"components": []}},
    ]}
    _mock_gateway(monkeypatch, payload)
    out = ce.extract_chunk_knowledge_batch([("c1", "text one"), ("c2", "text two")])
    assert out["c1"][1].quantities[0].name == "Power Input (Mig)"
    assert out["c1"][1].quantities[0].value == "120 VAC / 60 Hz"


def test_batch_quantity_without_name_becomes_unresolved_placeholder(monkeypatch):
    # Correction to an earlier assumption: MachineModelBase already
    # supplies explicit unresolved placeholders for missing required
    # fields, so a quantity with no name/entity_id is NOT lost and does
    # NOT fail the job. It is kept with name "Unresolved item" and
    # entity_id "unresolved:entity", and its value is preserved.
    payload = {"chunks": [
        {"source_chunk_id": "c1", "knowledge": {
            "quantities": [{"id": "quantity:q1", "value": "86", "unit": "VDC"}],
        }},
        {"source_chunk_id": "c2", "knowledge": {"components": []}},
    ]}
    _mock_gateway(monkeypatch, payload)
    out = ce.extract_chunk_knowledge_batch([("c1", "text one"), ("c2", "text two")])
    qty = out["c1"][1].quantities[0]
    assert qty.value == "86"
    assert qty.entity_id == "unresolved:entity"
    assert qty.name == "Unresolved item"


def test_batch_truly_unusable_quantity_is_extraction_error_not_uncaught(monkeypatch):
    # Dict collections pass extraction (list[dict]) but are validated
    # only in universal conversion. A quantity whose value is an object,
    # not a string, fails there. Before this patch that raised an
    # uncaught ValidationError that bypassed the pipeline's split
    # recovery; now it must surface as ExtractionError so the batch
    # splits to singles.
    payload = {"chunks": [
        {"source_chunk_id": "c1", "knowledge": {
            "quantities": [{"id": "quantity:q1", "entity_id": "e1", "name": "OCV", "value": {"a": 1}, "unit": "VDC"}],
        }},
        {"source_chunk_id": "c2", "knowledge": {"components": []}},
    ]}
    _mock_gateway(monkeypatch, payload)
    with pytest.raises(ce.ExtractionError, match="unusable universal fact"):
        ce.extract_chunk_knowledge_batch([("c1", "text one"), ("c2", "text two")])


def test_batch_prompt_shows_full_field_shapes():
    prompt = ce._BATCH_EXTRACTION_PROMPT
    # The old prompt showed only empty lists and told the model to use
    # shapes it could not see. The new prompt must contain the shapes.
    assert '"procedure_type"' in prompt
    assert '"entity_id"' in prompt
    assert "failure mode's evidence must be a list" in prompt
    assert 'Do not use a "parameter" field' in prompt
    # And it must still require every supplied id exactly once.
    assert "must appear exactly once" in prompt


def test_procedure_without_type_still_fails_visibly(monkeypatch):
    # Deliberately NOT fixed by inventing a type: a procedure with no
    # procedure_type must fail validation (and therefore split in the
    # pipeline), not be silently assigned "maintenance".
    payload = {"chunks": [
        {"source_chunk_id": "c1", "knowledge": {
            "procedures": [{"procedure_name": "Clean", "steps": ["Clean off slag"]}],
        }},
        {"source_chunk_id": "c2", "knowledge": {"components": []}},
    ]}
    _mock_gateway(monkeypatch, payload)
    with pytest.raises(ce.ExtractionError):
        ce.extract_chunk_knowledge_batch([("c1", "text one"), ("c2", "text two")])
