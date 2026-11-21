"""Regression tests for the 21 Nov 2026 ingestion crash + Gemini config fix.

FIX A -- "'NoneType' object has no attribute 'strip'" killing an ingestion
job: a relation the LLM returns with a missing endpoint must be dropped
per-fact (with a logged warning), the chunk's other facts kept, and any
residual AttributeError inside extraction must surface as ExtractionError
so the pipeline records a per-chunk failure instead of failing the job.

FIX B -- Gemini config traps: GOOGLE_API_KEY fallback, GEMINI_API_KEY
precedence, default model gemini-2.5-flash-lite.

No LLM call is made here: the gateway is mocked, and Settings is built
fresh from monkeypatched env (with _env_file=None so a local .env cannot
leak into the precedence checks).
"""
import json
import logging

import pytest

from backend.config import Settings
from backend.knowledge import component_extraction as ce
from backend.knowledge.schema import MachineKnowledgeExtractionResult


def _mock_gateway(monkeypatch, payload):
    monkeypatch.setattr(ce.gateway, "complete", lambda *args, **kwargs: json.dumps(payload))


def _payload_with_null_endpoints():
    # Two good entities, one good relation, and one relation per missing
    # endpoint shape (null subject, null object). Plus a legacy component
    # and procedure, to prove the chunk's other facts survive.
    return {
        "components": [{"name": "Wire Feed Motor", "function": "Feeds wire"}],
        "procedures": [{"name": "Check Feed", "procedure_type": "maintenance", "steps": ["Inspect roller"]}],
        "entities": [
            {"id": "entity:motor", "name": "Wire Feed Motor", "entity_type": "motor"},
            {"id": "entity:fan", "name": "Fan", "entity_type": "fan"},
        ],
        "relations": [
            {"subject_id": "entity:motor", "subject_name": "Wire Feed Motor",
             "relation_type": "drives", "object_id": "entity:fan", "object_name": "Fan"},
            {"subject_id": "entity:motor", "subject_name": None,
             "relation_type": "drives", "object_id": "entity:fan", "object_name": "Fan"},
            {"subject_id": "entity:motor", "subject_name": "Wire Feed Motor",
             "relation_type": "drives", "object_id": "entity:fan", "object_name": None},
        ],
    }


def test_null_endpoint_relations_are_dropped_and_other_facts_kept(monkeypatch, caplog):
    _mock_gateway(monkeypatch, _payload_with_null_endpoints())
    with caplog.at_level(logging.WARNING, logger="backend.knowledge.component_extraction"):
        result, model = ce.extract_chunk_knowledge("motor text")
    assert [e.name for e in model.entities] == ["Wire Feed Motor", "Fan"]
    assert len(model.relations) == 1
    assert model.relations[0].subject_name == "Wire Feed Motor"
    assert model.relations[0].object_name == "Fan"
    # The chunk's other facts survive the bad relations.
    assert result.components[0].name == "Wire Feed Motor"
    assert result.procedures[0].name == "Check Feed"
    # The drop is visible, not silent: a warning with counts.
    assert "relations=2" in caplog.text


def test_null_endpoint_relations_dropped_in_universal_conversion_with_chunk_id(caplog):
    # Direct _universal_model_from_result level: the drop warning carries
    # the chunk id the batch caller threads through.
    result = MachineKnowledgeExtractionResult.model_validate(_payload_with_null_endpoints())
    with caplog.at_level(logging.WARNING, logger="backend.knowledge.component_extraction"):
        model = ce._universal_model_from_result(result, chunk_id="chunk-abc")
    assert len(model.relations) == 1
    assert "chunk_id=chunk-abc" in caplog.text
    assert "relations=2" in caplog.text


def test_null_endpoint_relations_dropped_per_chunk_in_batch(monkeypatch):
    payload = {"chunks": [
        {"source_chunk_id": "c1", "knowledge": _payload_with_null_endpoints()},
        {"source_chunk_id": "c2", "knowledge": {
            "entities": [{"id": "entity:pump", "name": "Pump", "entity_type": "pump"}],
        }},
    ]}
    _mock_gateway(monkeypatch, payload)
    out = ce.extract_chunk_knowledge_batch([("c1", "text one"), ("c2", "text two")])
    assert len(out["c1"][1].relations) == 1
    assert [e.name for e in out["c1"][1].entities] == ["Wire Feed Motor", "Fan"]
    assert [e.name for e in out["c2"][1].entities] == ["Pump"]


def test_blank_endpoint_relation_is_dropped_too(monkeypatch):
    payload = dict(_payload_with_null_endpoints())
    payload["relations"] = [
        {"subject_id": "entity:motor", "subject_name": "   ",
         "relation_type": "drives", "object_id": "entity:fan", "object_name": "Fan"},
    ]
    _mock_gateway(monkeypatch, payload)
    _, model = ce.extract_chunk_knowledge("motor text")
    assert model.relations == []
    assert [e.name for e in model.entities] == ["Wire Feed Motor", "Fan"]


def test_behavior_without_subject_is_dropped_and_named_behavior_kept(monkeypatch, caplog):
    payload = _payload_with_null_endpoints()
    payload["behaviors"] = [
        {"id": "behavior:b1", "subject_id": "entity:motor", "subject_name": "Wire Feed Motor",
         "description": "Motor runs when powered"},
        {"id": "behavior:b2", "subject_id": "entity:motor", "subject_name": None,
         "description": "Something happens"},
    ]
    _mock_gateway(monkeypatch, payload)
    with caplog.at_level(logging.WARNING, logger="backend.knowledge.component_extraction"):
        _, model = ce.extract_chunk_knowledge("motor text")
    assert "Wire Feed Motor" in [b.subject_name for b in model.behaviors]
    # The nameless behavior is gone; the only other behavior is the
    # legacy procedure "Check Feed" (subject_name = procedure name).
    assert [b.subject_name for b in model.behaviors] == ["Wire Feed Motor", "Check Feed"]
    assert "behaviors=1" in caplog.text


def test_attribute_error_in_universal_conversion_becomes_extraction_error_single(monkeypatch):
    def _boom(result, chunk_id=None):
        raise AttributeError("'NoneType' object has no attribute 'strip'")

    monkeypatch.setattr(ce, "_universal_model_from_result", _boom)
    _mock_gateway(monkeypatch, _payload_with_null_endpoints())
    with pytest.raises(ce.ExtractionError, match="unusable universal fact"):
        ce.extract_chunk_knowledge("motor text")


def test_attribute_error_in_universal_conversion_becomes_extraction_error_batch(monkeypatch):
    def _boom(result, chunk_id=None):
        raise AttributeError("'NoneType' object has no attribute 'strip'")

    monkeypatch.setattr(ce, "_universal_model_from_result", _boom)
    payload = {"chunks": [
        {"source_chunk_id": "c1", "knowledge": _payload_with_null_endpoints()},
        {"source_chunk_id": "c2", "knowledge": {"components": []}},
    ]}
    _mock_gateway(monkeypatch, payload)
    with pytest.raises(ce.ExtractionError, match="unusable universal fact for c1"):
        ce.extract_chunk_knowledge_batch([("c1", "text one"), ("c2", "text two")])


def test_verification_does_not_crash_on_none_relation_type():
    # A3 audit: verification.py used to call r.relation_type.strip()
    # unguarded. A relation built without validation (model_construct)
    # carrying None must not crash verify_machine_model.
    from backend.knowledge.machine_model import MachineEntity, MachineRelation, UniversalMachineModel
    from backend.knowledge.verification import verify_machine_model

    rel = MachineRelation.model_construct(
        subject_id="a", subject_name="Pump", relation_type=None,
        object_id="b", object_name="Valve",
    )
    model = UniversalMachineModel(
        entities=[
            MachineEntity.model_construct(id="a", name="Pump", entity_type="pump"),
            MachineEntity.model_construct(id="b", name="Valve", entity_type="valve"),
        ],
        relations=[rel],
    )
    issues = verify_machine_model(model)  # must not raise AttributeError
    assert isinstance(issues, list)


def test_verification_does_not_crash_on_none_entity_type():
    # A3 audit, folded in from the verifier's probe: verification.py used
    # to call e.entity_type.strip() unguarded. An entity built without
    # validation (model_construct) carrying None must not crash
    # verify_machine_model. Assert no-raise, not zero issues: a model
    # like this can legitimately carry warnings (e.g. disconnected graph).
    from backend.knowledge.machine_model import MachineEntity, MachineRelation, UniversalMachineModel
    from backend.knowledge.verification import verify_machine_model

    rel = MachineRelation.model_construct(
        subject_id="a", subject_name="Pump", relation_type="drives",
        object_id="b", object_name="Valve",
    )
    model = UniversalMachineModel(
        entities=[
            MachineEntity.model_construct(id="a", name="Pump", entity_type=None),
            MachineEntity.model_construct(id="b", name="Valve", entity_type="valve"),
        ],
        relations=[rel],
    )
    issues = verify_machine_model(model)  # must not raise AttributeError
    assert isinstance(issues, list)


def test_verification_does_not_crash_on_none_entity_name():
    # A3 audit, folded in from the verifier's probe: verification.py used
    # to call e.name.strip() unguarded in the shared-name grouping. An
    # entity built without validation (model_construct) carrying None must
    # not crash verify_machine_model. Assert no-raise, not zero issues.
    from backend.knowledge.machine_model import MachineEntity, MachineRelation, UniversalMachineModel
    from backend.knowledge.verification import verify_machine_model

    rel = MachineRelation.model_construct(
        subject_id="a", subject_name="Pump", relation_type="drives",
        object_id="b", object_name="Valve",
    )
    model = UniversalMachineModel(
        entities=[
            MachineEntity.model_construct(id="a", name=None, entity_type="pump"),
            MachineEntity.model_construct(id="b", name="Valve", entity_type="valve"),
        ],
        relations=[rel],
    )
    issues = verify_machine_model(model)  # must not raise AttributeError
    assert isinstance(issues, list)


def test_background_job_failure_logs_traceback(monkeypatch, caplog):
    # Logging patch (21 Nov 2026): _run_job_background used to store only
    # str(exc) on the job row and log nothing, so a background-job crash
    # left no traceback anywhere except the uvicorn console -- and only if
    # something logged it. A stubbed ingest_pdf raising must leave a logged
    # traceback (with the job id) while the job row keeps exactly the
    # stored error_message behaviour it had before.
    import backend.api.routes.ingestion as ingestion_routes
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from backend.db.models import (
        Base, DocType, IngestionJob, IngestionStatus, Product, Revision,
    )

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    product = Product(manufacturer="Harbor Freight", family="OMNIPRO", model="220")
    db.add(product)
    db.flush()
    revision = Revision(product_id=product.id, label="Rev A")
    db.add(revision)
    db.flush()
    job = IngestionJob(revision_id=revision.id, idempotency_key="gemini-fix-logging",
                       content_sha256="0" * 64, doc_type=DocType.manual,
                       title="Test Manual", source_path="/tmp/test.pdf")
    db.add(job)
    db.commit()

    monkeypatch.setattr(ingestion_routes, "SessionLocal", Session)
    monkeypatch.setattr(ingestion_routes, "load_checkpoint", lambda job_id: {"ingest_tier2": True})

    def _boom(**kwargs):
        raise RuntimeError("simulated ingestion failure")

    monkeypatch.setattr(ingestion_routes, "ingest_pdf", _boom)

    with caplog.at_level(logging.ERROR, logger="backend.api.routes.ingestion"):
        ingestion_routes._run_job_background(job.id)

    # The traceback is logged (logger.exception), with the job id.
    assert job.id in caplog.text
    assert "simulated ingestion failure" in caplog.text
    assert "RuntimeError" in caplog.text
    assert "Traceback" in caplog.text
    # Job state is exactly as before the logging change: failed, message stored.
    failed = Session().get(IngestionJob, job.id)
    assert failed.status == IngestionStatus.failed
    assert failed.error_message == "simulated ingestion failure"


# --- FIX B: Gemini config -----------------------------------------------

def _settings(monkeypatch, **env):
    for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_MODEL"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def test_google_api_key_fallback(monkeypatch):
    assert _settings(monkeypatch, GOOGLE_API_KEY="google-key").gemini_api_key == "google-key"


def test_gemini_api_key_wins_when_both_set(monkeypatch):
    settings = _settings(monkeypatch, GEMINI_API_KEY="gemini-key", GOOGLE_API_KEY="google-key")
    assert settings.gemini_api_key == "gemini-key"


def test_gemini_api_key_alone_still_works(monkeypatch):
    assert _settings(monkeypatch, GEMINI_API_KEY="gemini-key").gemini_api_key == "gemini-key"


def test_no_key_means_empty_gemini_key(monkeypatch):
    assert _settings(monkeypatch).gemini_api_key == ""


def test_default_gemini_model_is_lite(monkeypatch):
    assert _settings(monkeypatch).gemini_model == "gemini-2.5-flash-lite"


def test_gemini_model_env_override_still_works(monkeypatch):
    assert _settings(monkeypatch, GEMINI_MODEL="gemini-2.5-flash").gemini_model == "gemini-2.5-flash"


# ---------------------------------------------------------------------------
# FIX2 (21 Nov 2026) -- _merge_contexts None-strip crash.
#
# Nishant's run on the fix-1 package crashed in
# backend/knowledge/global_integration.py::_merge_contexts, line 483:
#   "failure_modes": lambda x: (x.get("symptom", "").strip().lower(),
#                               x.get("diagnostic_test", "").strip().lower())
# The .get(..., "") default only applies when the key is MISSING; a row
# carrying "diagnostic_test": None explicitly returns None from .get and
# None.strip() raises AttributeError. diagnostic_test is legitimately
# Optional (machine_model.py:267, schema.py:85) -- a failure mode with no
# documented test is normal. All 8 key lambdas that call .strip() carried
# the identical trap (11 .strip() call sites); the fix is (x.get(k) or "")
# at every one. Rows are kept with empty-string keys, never dropped.
# ---------------------------------------------------------------------------
from backend.knowledge.global_integration import _merge_contexts


def test_merge_contexts_failure_mode_with_none_diagnostic_test_kept():
    # Exact traceback scenario: a failure_modes row with diagnostic_test=None.
    merged = _merge_contexts([{
        "failure_modes": [
            {"symptom": "Motor hums, no wire feed", "diagnostic_test": None},
        ],
    }])
    assert len(merged["failure_modes"]) == 1
    assert merged["failure_modes"][0]["symptom"] == "Motor hums, no wire feed"
    assert merged["failure_modes"][0]["diagnostic_test"] is None


def test_merge_contexts_failure_mode_with_none_symptom_kept():
    merged = _merge_contexts([{
        "failure_modes": [
            {"symptom": None, "diagnostic_test": "Measure voltage at motor terminals"},
            {"symptom": "No wire feed", "diagnostic_test": None},
        ],
    }])
    # Distinct empty-string keys ("", "measure...") and ("no wire feed", ""):
    # both rows are kept, neither dropped nor rejected.
    assert len(merged["failure_modes"]) == 2


def test_merge_contexts_none_fields_still_shadow_on_same_key():
    # Behavior beyond None-safety is unchanged: a child row with the same
    # logical key (here the empty diagnostic_test key) shadows the parent.
    parent = {"failure_modes": [
        {"symptom": "No wire feed", "diagnostic_test": None, "source_id": "parent"},
    ]}
    child = {"failure_modes": [
        {"symptom": "No wire feed", "diagnostic_test": None, "source_id": "child"},
    ]}
    merged = _merge_contexts([parent, child])
    assert len(merged["failure_modes"]) == 1
    assert merged["failure_modes"][0]["source_id"] == "child"


def test_merge_contexts_none_in_other_key_fields_kept():
    # Same None-in-field case for every other lambda kind that .strip()s.
    merged = _merge_contexts([{
        "universal_entities": [
            {"name": None, "entity_type": "motor"},
            {"name": "Wire Feed Motor", "entity_type": "motor"},
        ],
        "universal_relations": [
            {"subject_name": None, "relation_type": "drives", "object_name": "Fan"},
            {"subject_name": "Motor", "relation_type": "drives", "object_name": None},
        ],
        "behaviors": [
            {"subject_name": None, "description": "Feeds wire when triggered"},
            {"subject_name": "Motor", "description": None},
        ],
        "legacy_components": [
            {"name": None, "part_number": "P-1"},
        ],
        "procedures": [
            {"name": None, "procedure_type": "maintenance"},
            {"name": "Check Feed", "procedure_type": "maintenance"},
        ],
        "schematic_nodes": [
            {"label": None, "symbol_type": "terminal"},
        ],
        "expert_knowledge": [
            {"title": None, "knowledge_type": "tip"},
        ],
    }])
    assert len(merged["universal_entities"]) == 2
    assert merged["universal_entities"][0]["name"] is None
    assert len(merged["universal_relations"]) == 2
    assert len(merged["behaviors"]) == 2
    assert merged["behaviors"][1]["description"] is None
    assert len(merged["legacy_components"]) == 1
    assert len(merged["procedures"]) == 2
    assert len(merged["schematic_nodes"]) == 1
    assert len(merged["expert_knowledge"]) == 1
