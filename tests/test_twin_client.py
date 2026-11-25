"""Phase 1 Step 5 gate tests: the twin as a thin async client (audit:
twin verifier as a thin client over POST /v1/verify -> 202 + job).

The client never invents a pass: no ``rules_used`` means no VALIDATED
(demoted at the service map and re-checked at the client), a down or
absent twin degrades to NOT_VERIFIED, and the client never raises.

Vocabulary: the job contract carries uppercase ``verdict``
(VALIDATED / REJECTED / CANNOT_EXPRESS / NOT_VERIFIED); the loop's
summary rows carry lowercase ``status`` (validated / rejected /
unavailable). Both are asserted where they belong.
"""
import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db.models import (
    Base, DigitalTwin, MachineKnowledgeModelSnapshot, TwinStatus,
)
from backend.db.session import get_session
from backend.diagnostics.schema import DiagnosticState
from backend.diagnostics.twin_verification import (
    TwinVerifyClient,
    cannot_express_cause,
    conclusion_cause,
    verdict_for_cause,
)
from backend.simulation import verify_service
from backend.simulation.app import app as twin_app
from backend.simulation.schema import DigitalTwinDefinition
from tests.twin_testkit import make_twin_client, stub_inline_dispatch

TIGHT = "Feed Tensioner is too tight."
# The cause that VALIDATES against this proof twin (same as
# TENSIONER_CAUSE in test_verify_before_conclusion.py): it matches the
# snapshot failure mode, resolves to entity:feed-tensioner, and the
# seed fault + recovery rules fire. TIGHT instead hits the registry
# path, whose reviewed RepairAction names full-machine components this
# seed twin does not have, so it reads CANNOT_EXPRESS here.
VALIDATED_CAUSE = "Wire Feed Motor Runs but Wire Does Not Feed Properly"


# ---------------------------------------------------------------------------
# Fixture: the OMNIPRO wire-feed proof twin (same seed rules as the
# tournament tests in test_verify_before_conclusion.py).
# ---------------------------------------------------------------------------

def _tournament_definition():
    transitions = [
        {"id": "seed:feed-tension-too-tight", "name": "Feed tensioner too tight stops wire feed",
         "conditions": {"entity:feed-tensioner.tension_ok": False},
         "effects": {"entity:welding-wire.feeding": False},
         "evidence": [{"fact": "Feed tension too tight stops wire feed.", "page": 42}]},
        {"id": "seed:feed-tension-ok-restores-feed", "name": "Correct feed tension restores wire feed",
         "conditions": {"entity:feed-tensioner.tension_ok": True},
         "effects": {"entity:welding-wire.feeding": True},
         "evidence": [{"fact": "Correct feed tension restores wire feed.", "page": 42}]},
    ]
    return DigitalTwinDefinition(
        name="Test OMNIPRO wire feed",
        components=[
            {"id": "entity:feed-tensioner", "name": "Feed Tensioner", "component_type": "other",
             "initial_state": {"tension_ok": True}},
            {"id": "entity:welding-wire", "name": "Welding Wire", "component_type": "other",
             "initial_state": {"feeding": True}},
        ],
        initial_signals={},
        transitions=transitions,
    )


def _install_twin(db, revision_id="r1"):
    definition = _tournament_definition()
    twin = DigitalTwin(
        product_id="p1", revision_id=revision_id, name="test twin",
        definition=definition.model_dump(mode="json"),
        state={"signals": {}, "components": {c.id: dict(c.initial_state)
                                            for c in definition.components}},
        status=TwinStatus.active)
    snapshot = MachineKnowledgeModelSnapshot(
        revision_id=revision_id, version=1,
        model={"failure_modes": [
            {"name": "Wire Does Not Feed",
             "symptoms": ["Wire Feed Motor Runs but Wire Does Not Feed Properly"],
             "entity_ids": ["entity:feed-tensioner"], "evidence": []}],
               "procedures": [
            {"name": "Adjust the Feed Tensioner",
             "steps": ["Loosen the Feed Tensioner knob"]}]})
    db.add_all([twin, snapshot])
    db.commit()
    return twin


@pytest.fixture()
def db():
    # StaticPool + check_same_thread False: the TestClient runs the app
    # in a worker thread, and the graph/client share this session.
    from sqlalchemy.pool import StaticPool
    engine = create_engine("sqlite:///:memory:",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    session = factory()
    yield session
    session.close()


# ---------------------------------------------------------------------------
# Service map: no VALIDATED without rules_used
# ---------------------------------------------------------------------------

def test_map_verdict_row_demotes_validated_without_rules():
    row = {"cause": TIGHT, "status": "validated",
           "rules_used": [], "evidence": []}
    mapped = verify_service.map_verdict_row(row)
    assert mapped["verdict"] == "NOT_VERIFIED"
    assert "rules_used" in mapped["reason"]


def test_map_verdict_row_keeps_validated_with_rules():
    row = {"cause": TIGHT, "status": "validated",
           "rules_used": ["seed:feed-tension-too-tight"], "evidence": ["x"]}
    mapped = verify_service.map_verdict_row(row)
    assert mapped["verdict"] == "VALIDATED"
    assert mapped["status"] == "validated"


# ---------------------------------------------------------------------------
# Client over HTTP to the real twin-verifier app (the in-process
# LocalVerifyBackend fallback was removed in the Phase 3 twin split;
# the test kit stubs the Celery dispatch to run inline, no broker).
# ---------------------------------------------------------------------------

def test_client_validated_when_rules_used(db, monkeypatch):
    _install_twin(db)
    client = make_twin_client(db, monkeypatch)
    out = client.verify_sync(
        [{"cause": VALIDATED_CAUSE}], revision_id="r1")
    assert out["available"] is True
    result = out["results"][0]
    assert result["verdict"] == "VALIDATED"
    assert result["status"] == "validated"
    assert result["rules_used"], "a VALIDATED row must carry rules_used"
    assert any(r.get("id") == "seed:feed-tension-too-tight"
               for r in result["rules_used"])


def test_client_no_validated_without_rules_used(db):
    """Even if the twin service returned VALIDATED with no rules, the
    client re-checks the rule before surfacing it (defence in depth
    with the service map)."""
    def _handler(request):
        assert request.url.path == "/v1/verify"
        return httpx.Response(200, json={
            "job_id": "j1", "status": "succeeded", "twin_id": "t1",
            "results": [{"cause": TIGHT, "status": "validated",
                         "verdict": "VALIDATED", "rules_used": [],
                         "evidence": []}],
            "counts": {}})

    client = TwinVerifyClient(
        base_url="http://twin-test", transport=httpx.MockTransport(_handler))
    out = client.verify_sync([{"cause": TIGHT}], revision_id="r1")
    assert out["results"][0]["verdict"] == "NOT_VERIFIED"
    assert out["results"][0]["status"] == "unavailable"


def test_client_degrades_to_not_verified_when_twin_down(db):
    def _boom(request):
        raise httpx.ConnectError("twin unreachable", request=request)

    client = TwinVerifyClient(
        base_url="http://twin-test", transport=httpx.MockTransport(_boom))
    out = client.verify_sync(
        [{"cause": TIGHT}, {"cause": "Feed Tensioner is too loose."}],
        revision_id="r1")
    assert out["available"] is False
    assert len(out["results"]) == 2
    assert all(r["verdict"] == "NOT_VERIFIED" for r in out["results"])
    assert all(r["status"] == "unavailable" for r in out["results"])
    # Never raises: the loop keeps turning on a dead twin.
    assert out["results"][0]["rules_used"] == []


def test_client_no_backend_is_not_verified():
    client = TwinVerifyClient()
    out = client.verify_sync([{"cause": "x"}], revision_id="r1")
    assert out["available"] is False
    assert out["results"][0]["verdict"] == "NOT_VERIFIED"
    assert out["results"][0]["status"] == "unavailable"


def test_client_async_contract_matches_sync(db, monkeypatch):
    _install_twin(db)
    client = make_twin_client(db, monkeypatch)
    out = asyncio.run(client.verify([{"cause": VALIDATED_CAUSE}], revision_id="r1"))
    assert out["available"] is True
    assert out["results"][0]["verdict"] == "VALIDATED"


# ---------------------------------------------------------------------------
# Verdict helpers (kept pure in the thin client)
# ---------------------------------------------------------------------------

def test_verdict_helpers():
    rows = [
        {"cause": "a", "status": "validated"},
        {"cause": "b", "status": "rejected"},
        {"cause": "c", "status": "unavailable", "cannot_express": True}]
    assert verdict_for_cause(rows, "a") == "validated"
    assert verdict_for_cause(rows, "b") == "rejected"
    assert verdict_for_cause(rows, "c") == "unavailable"
    assert verdict_for_cause(rows, "zzz") == "unavailable"
    assert verdict_for_cause(None, "a") == "unavailable"
    assert cannot_express_cause(rows, "c") is True
    assert cannot_express_cause(rows, "a") is False
    state = DiagnosticState(symptoms=["wire not feeding"], hypotheses=[
        {"cause": TIGHT, "rationale": "t", "confidence": 0.5},
        {"cause": "Wingnut is loose, spool spins freely", "rationale": "t",
         "confidence": 0.5}])
    assert conclusion_cause(state, "Adjust the Feed Tensioner") is state.hypotheses[0]
    assert conclusion_cause(state, "the spool spins freely") is state.hypotheses[1]


# ---------------------------------------------------------------------------
# POST /v1/verify -> 202 + job; GET job
# ---------------------------------------------------------------------------

def _verify_app(db, monkeypatch):
    stub_inline_dispatch(db, monkeypatch)
    monkeypatch.setattr(
        twin_app, "dependency_overrides", {get_session: lambda: db})
    return twin_app


def test_verify_endpoint_202_and_job_get(db, monkeypatch):
    _install_twin(db)
    app = _verify_app(db, monkeypatch)
    client = TestClient(app, raise_server_exceptions=False)
    created = client.post("/v1/verify", json={
        "revision_id": "r1",
        "hypotheses": [{"cause": VALIDATED_CAUSE}]})
    assert created.status_code == 202, created.text
    body = created.json()
    assert body["status"] == "succeeded"
    assert body["job_id"]
    assert body["twin_id"]
    job = client.get(f"/v1/verify/jobs/{body['job_id']}")
    assert job.status_code == 200
    assert job.json()["status"] == "succeeded"
    assert job.json()["results"][0]["verdict"] == "VALIDATED"
    assert job.json()["results"][0]["status"] == "validated"


def test_verify_endpoint_idempotency_key_reuses_job(db, monkeypatch):
    _install_twin(db)
    app = _verify_app(db, monkeypatch)
    client = TestClient(app, raise_server_exceptions=False)
    first = client.post("/v1/verify", json={
        "revision_id": "r1",
        "hypotheses": [{"cause": "x"}],
        "idempotency_key": "k-1"})
    second = client.post("/v1/verify", json={
        "revision_id": "r1",
        "hypotheses": [{"cause": "x"}],
        "idempotency_key": "k-1"})
    assert first.json()["job_id"] == second.json()["job_id"]


def test_verify_endpoint_unknown_job_404(db, monkeypatch):
    app = _verify_app(db, monkeypatch)
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/v1/verify/jobs/nope").status_code == 404
