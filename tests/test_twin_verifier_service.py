"""Phase 3 twin-verifier extraction tests (contracts README §5, §8.1).

Non-LLM only: no provider call is made here. The twin-verifier
service app (``backend.simulation.app``) and the monolith app are
exercised through TestClient against an in-memory SQLite database;
the Celery dispatch is stubbed at the route seam to run the job
inline (no broker in this VM), and the Redis/Celery-down readiness
failures use the real absence of both.

Covered:
- twin /health (liveness) + /ready (503 naming Postgres/twin-store/
  library/Redis/Celery failures; 200 when the checks are mocked
  ready; twin_store unit checks: zero approved twins fails, a draft
  twin does not count, an approved twin passes; library frozen at
  3c-1.0.0).
- POST /v1/verify -> 202 + Postgres job row (not the old in-memory
  _JOBS dict): queued before the worker runs, idempotency reuse,
  same-key/different-payload 409, broker outage 503 naming
  Redis/Celery.
- GET /v1/verify/jobs/{id} polls to a succeeded job: VALIDATED only
  with rules_used + trace + source evidence + frozen library version;
  no twin -> succeeded NOT_VERIFIED; no matching component ->
  CANNOT_EXPRESS with the case explained; timeout -> succeeded
  NOT_VERIFIED with reason "twin verification timed out".
- Twin lifecycle on the service app: compile -> draft, approve ->
  active, revision-twin read.
- Experiments: empty assertions -> 400 (never validated); a real
  experiment validates; batch hypotheses with an empty-assertions
  experiment -> 400.
- Visual moved out: the twin app has no visual path (404); the
  gateway shim still serves GET /simulation/twins/{id}/visual.
- Contract YAML: verify surface present, no visual path on the twin
  contract, library version enum frozen.
- Oracle moved: benchmark/oracle.py exists, backend/simulation/
  oracle.py is gone, no production module imports it.
- drafting / review_view CUT verified gone; field_promotion /
  library_versions DEFER-retained and kept.
- Celery task: retries 3x with backoff, ignores an unknown job,
  job-cap math (2 s/hypothesis, 10 s/job).
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.routes import verify as verify_routes
from backend.db.models import (
    Base, DigitalTwin, MachineKnowledgeModelSnapshot, Product, Revision,
    TwinStatus, VerifyJob,
)
from backend.db.session import get_session
from backend.api.main import app as monolith_app
from backend.simulation.app import app as twin_app
from backend.simulation.schema import DigitalTwinDefinition

REPO = Path(__file__).resolve().parent.parent

TIGHT = "Feed Tensioner is too tight."
VALIDATED_CAUSE = "Wire Feed Motor Runs but Wire Does Not Feed Properly"


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
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _inline_dispatch(db, monkeypatch):
    """Stub the Celery dispatch so the job runs inline against ``db``
    (no broker in this VM). Returns the list of dispatched job ids."""
    from backend.simulation.tasks import execute_job

    dispatched: list = []

    def _fresh_session():
        return sessionmaker(bind=db.get_bind())()

    class _InlineTask:
        @staticmethod
        def delay(job_id):
            dispatched.append(job_id)
            execute_job(db, job_id, session_factory=_fresh_session)

    monkeypatch.setattr(verify_routes, "run_verify_job", _InlineTask)
    return dispatched


@pytest.fixture()
def twin_client(db, monkeypatch):
    dispatched = _inline_dispatch(db, monkeypatch)
    twin_app.dependency_overrides[get_session] = lambda: db
    client = TestClient(twin_app, raise_server_exceptions=False)
    client._dispatched = dispatched  # type: ignore[attr-defined]
    yield client
    twin_app.dependency_overrides.clear()


@pytest.fixture()
def twin_client_queued(db, monkeypatch):
    """Dispatch records the job id but never runs it: the job stays
    queued, so the 202 shape and the poll-before-worker shape are
    observable."""
    dispatched: list = []

    class _RecordOnlyTask:
        @staticmethod
        def delay(job_id):
            dispatched.append(job_id)

    monkeypatch.setattr(verify_routes, "run_verify_job", _RecordOnlyTask)
    twin_app.dependency_overrides[get_session] = lambda: db
    client = TestClient(twin_app, raise_server_exceptions=False)
    client._dispatched = dispatched  # type: ignore[attr-defined]
    yield client
    twin_app.dependency_overrides.clear()


@pytest.fixture()
def monolith_client(db):
    monolith_app.dependency_overrides[get_session] = lambda: db
    client = TestClient(monolith_app, raise_server_exceptions=False)
    yield client
    monolith_app.dependency_overrides.clear()


# --- /health + /ready -------------------------------------------------------

def test_twin_health_is_liveness_only(twin_client):
    resp = twin_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_twin_ready_503_names_failed_checks(twin_client):
    # No Redis, no Celery worker in this VM; Postgres is the local
    # SQLite file and answers SELECT 1; the library is frozen.
    resp = twin_client.get("/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    checks = body["checks"]
    assert set(checks) == {"postgres", "twin_store", "library", "redis", "celery"}
    assert checks["postgres"]["ready"] is True
    assert checks["library"]["ready"] is True
    assert checks["library"]["version"] == "3c-1.0.0"
    for name in ("redis", "celery"):
        assert checks[name]["ready"] is False, name
        assert checks[name]["detail"], name


def test_twin_ready_200_when_checks_pass(twin_client, monkeypatch):
    import backend.simulation.readiness as readiness

    for name in ("check_postgres", "check_twin_store", "check_library",
                 "check_redis", "check_celery"):
        monkeypatch.setattr(readiness, name,
                            lambda *a, **k: {"ready": True, "detail": "mocked"})
    resp = twin_client.get("/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"


def test_twin_store_check_counts_only_approved_twins(db):
    from backend.simulation.readiness import check_twin_store

    def factory():
        return sessionmaker(bind=db.get_bind())()

    assert check_twin_store(session_factory=factory)["ready"] is False
    draft = DigitalTwin(product_id="p1", revision_id="r1", name="draft",
                        definition={}, state={}, status=TwinStatus.draft)
    db.add(draft)
    db.commit()
    assert check_twin_store(session_factory=factory)["ready"] is False
    active = DigitalTwin(product_id="p1", revision_id="r1", name="active",
                         definition={}, state={}, status=TwinStatus.active)
    db.add(active)
    db.commit()
    ready = check_twin_store(session_factory=factory)
    assert ready["ready"] is True
    assert "1 approved twin" in ready["detail"]


def test_library_check_is_frozen_at_3c_1_0_0():
    from backend.simulation.library_versions import CURRENT_LIBRARY_VERSION
    from backend.simulation.readiness import check_library

    assert CURRENT_LIBRARY_VERSION == "3c-1.0.0"
    ready = check_library()
    assert ready["ready"] is True
    assert ready["version"] == "3c-1.0.0"


# --- POST /v1/verify -> 202 + Postgres job ----------------------------------

def test_verify_post_202_queued_before_worker_runs(twin_client_queued, db):
    _install_twin(db)
    resp = twin_client_queued.post("/v1/verify", json={
        "revision_id": "r1",
        "hypotheses": [{"cause": VALIDATED_CAUSE}]})
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["job_id"]
    assert body["status"] == "queued"
    assert body["estimated_ms"] == 2000
    assert twin_client_queued._dispatched == [body["job_id"]]  # type: ignore[attr-defined]
    # The job is a Postgres row, not an in-memory dict entry.
    fresh = sessionmaker(bind=db.get_bind())()
    try:
        row = fresh.get(VerifyJob, body["job_id"])
        assert row is not None
        assert row.status == "queued"
        assert row.request["hypotheses"][0]["cause"] == VALIDATED_CAUSE
    finally:
        fresh.close()
    # Polling before the worker runs: empty results, zero counts.
    got = twin_client_queued.get(f"/v1/verify/jobs/{body['job_id']}")
    assert got.status_code == 200
    assert got.json()["status"] == "queued"
    assert got.json()["results"] == []
    assert got.json()["counts"] == {"validated": 0, "rejected": 0,
                                    "cannot_express": 0, "not_verified": 0}


def test_verify_job_store_is_postgres_not_memory():
    import backend.api.routes.verify as verify_routes_mod

    assert not hasattr(verify_routes_mod, "_JOBS"), (
        "the in-memory _JOBS dict must be gone; the job store is Postgres")


def test_verify_post_empty_hypotheses_422(twin_client):
    resp = twin_client.post("/v1/verify", json={
        "revision_id": "r1", "hypotheses": []})
    assert resp.status_code == 422, resp.text


def test_verify_unknown_job_404(twin_client):
    assert twin_client.get("/v1/verify/jobs/nope").status_code == 404


def test_verify_idempotency_reuse_and_conflict(twin_client_queued, db):
    _install_twin(db)
    payload = {"revision_id": "r1", "hypotheses": [{"cause": "x"}],
               "idempotency_key": "k-1"}
    first = twin_client_queued.post("/v1/verify", json=payload)
    assert first.status_code == 202
    job_id = first.json()["job_id"]
    dispatched_before = list(twin_client_queued._dispatched)  # type: ignore[attr-defined]
    again = twin_client_queued.post("/v1/verify", json=payload)
    assert again.status_code == 202
    assert again.json()["job_id"] == job_id
    assert twin_client_queued._dispatched == dispatched_before  # type: ignore[attr-defined]
    conflict = twin_client_queued.post("/v1/verify", json={
        "revision_id": "r1", "hypotheses": [{"cause": "different"}],
        "idempotency_key": "k-1"})
    assert conflict.status_code == 409, conflict.text


def test_verify_broker_outage_is_loud_503(twin_client, db, monkeypatch):
    _install_twin(db)

    class _DownTask:
        @staticmethod
        def delay(job_id):
            raise ConnectionError("Error 111 connecting to localhost:6379")

    monkeypatch.setattr(verify_routes, "run_verify_job", _DownTask)
    resp = twin_client.post("/v1/verify", json={
        "revision_id": "r1", "hypotheses": [{"cause": "x"}]})
    assert resp.status_code == 503, resp.text
    error = resp.json()["error"]
    assert "redis" in error["message"].lower() or "celery" in error["message"].lower()


# --- GET job: verdicts ------------------------------------------------------

def _poll(twin_client, job_id):
    resp = twin_client.get(f"/v1/verify/jobs/{job_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_verify_poll_validated_with_rules_trace_evidence(twin_client, db):
    _install_twin(db)
    created = twin_client.post("/v1/verify", json={
        "revision_id": "r1",
        "hypotheses": [{"cause": VALIDATED_CAUSE}]})
    assert created.status_code == 202, created.text
    job = _poll(twin_client, created.json()["job_id"])
    assert job["status"] == "succeeded"
    assert job["counts"] == {"validated": 1, "rejected": 0,
                             "cannot_express": 0, "not_verified": 0}
    row = job["results"][0]
    assert row["verdict"] == "VALIDATED"
    assert row["status"] == "validated"
    assert row["rules_used"], "a VALIDATED row must carry rules_used"
    assert any(r.get("id") == "seed:feed-tension-too-tight"
               for r in row["rules_used"])
    assert row["trace"], "a VALIDATED row must carry the trace"
    assert row["evidence_pages"] == [42], "the seed rule's evidence page"
    assert row["twin_library_version"] == "3c-1.0.0"
    assert row["reason"], "a verdict reason is never empty"
    assert job["twin_id"]


def test_verify_no_twin_succeeds_not_verified(twin_client, db):
    created = twin_client.post("/v1/verify", json={
        "revision_id": "no-such-revision",
        "hypotheses": [{"cause": "Wire does not feed"}]})
    assert created.status_code == 202
    job = _poll(twin_client, created.json()["job_id"])
    assert job["status"] == "succeeded"
    assert job["twin_id"] is None
    assert job["counts"]["not_verified"] == 1
    row = job["results"][0]
    assert row["verdict"] == "NOT_VERIFIED"
    assert "no approved twin" in row["reason"]


def test_verify_cannot_express_names_the_case(twin_client, db):
    _install_twin(db)
    created = twin_client.post("/v1/verify", json={
        "revision_id": "r1",
        "hypotheses": [{"cause": "Zqx unknown cause"}]})
    assert created.status_code == 202
    job = _poll(twin_client, created.json()["job_id"])
    assert job["counts"]["cannot_express"] == 1
    row = job["results"][0]
    assert row["verdict"] == "CANNOT_EXPRESS"
    # Case 1 of the three: no component can carry the cause — and the
    # reason says what is missing instead of a bare "unavailable".
    assert "Could not match the cause" in row["reason"]
    assert "cannot express this failure" in row["reason"]


def test_verify_timeout_is_not_verified(twin_client, db, monkeypatch):
    import backend.simulation.tasks as tasks_mod

    _install_twin(db)

    def _slow(session, payload):
        time.sleep(0.5)
        return {"job_id": "slow", "status": "succeeded", "twin_id": "t1",
                "results": [], "counts": {}}

    monkeypatch.setattr(tasks_mod, "execute_verify_job", _slow)
    created = twin_client.post("/v1/verify", json={
        "revision_id": "r1", "timeout_ms": 100,
        "hypotheses": [{"cause": VALIDATED_CAUSE}]})
    assert created.status_code == 202
    job = _poll(twin_client, created.json()["job_id"])
    # A timed-out job SUCCEEDS with NOT_VERIFIED, never failed.
    assert job["status"] == "succeeded"
    assert job["counts"]["not_verified"] == 1
    row = job["results"][0]
    assert row["verdict"] == "NOT_VERIFIED"
    assert row["reason"] == "twin verification timed out"


# --- Twin lifecycle: compile draft -> human-gated approve --------------------

def test_compile_draft_then_approve(twin_client, db):
    from backend.simulation.omnipro_proof import build_omnipro_proof_model

    product = Product(manufacturer="Harbor Freight", family="OMNIPRO", model="220")
    db.add(product)
    db.flush()
    revision = Revision(product_id=product.id, label="Rev A")
    db.add(revision)
    db.flush()
    proof_model = build_omnipro_proof_model()
    db.add(MachineKnowledgeModelSnapshot(
        revision_id=revision.id, version=1,
        model=proof_model.model_dump(mode="json")))
    db.commit()

    compiled = twin_client.post(
        f"/v1/simulation/revisions/{revision.id}/compile")
    assert compiled.status_code == 200, compiled.text
    body = compiled.json()
    assert body["status"] == "draft"
    assert body["twin_id"]
    assert body["revision_id"] == revision.id
    twin_id = body["twin_id"]

    approved = twin_client.post(f"/v1/simulation/twins/{twin_id}/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "active"

    got = twin_client.get(f"/v1/simulation/revisions/{revision.id}/twin")
    assert got.status_code == 200
    assert got.json()["twin_id"] == twin_id

    missing = twin_client.post("/v1/simulation/twins/nope/approve")
    assert missing.status_code == 404


# --- Experiments: empty assertions -> 400 -----------------------------------

def _experiment_payload(**overrides):
    payload = {
        "name": "Feed tension check",
        "hypothesis": "Loosening the tensioner restores feed.",
        "fault_commands": [
            {"command": "set_component_state", "target": "entity:feed-tensioner",
             "field": "tension_ok", "value": False}],
        "intervention_commands": [
            {"command": "set_component_state", "target": "entity:feed-tensioner",
             "field": "tension_ok", "value": True}],
        "assertions": [
            {"path": "components.entity:welding-wire.feeding", "expected": True}],
    }
    payload.update(overrides)
    return payload


def test_experiment_empty_assertions_400(twin_client, db):
    twin = _install_twin(db)
    resp = twin_client.post(
        f"/v1/simulation/twins/{twin.id}/experiment",
        json=_experiment_payload(assertions=[]))
    assert resp.status_code == 400, resp.text
    assert "assertions" in resp.json()["error"]["message"].lower()


def test_experiment_validates_on_the_twin(twin_client, db):
    twin = _install_twin(db)
    resp = twin_client.post(
        f"/v1/simulation/twins/{twin.id}/experiment",
        json=_experiment_payload())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["validated"] is True
    assert body["verdict"] == "validated"
    assert body["fault_state"]["components"]["entity:welding-wire"]["feeding"] is False
    assert body["final_state"]["components"]["entity:welding-wire"]["feeding"] is True


def test_hypotheses_empty_assertions_400(twin_client, db):
    twin = _install_twin(db)
    resp = twin_client.post(
        f"/v1/simulation/twins/{twin.id}/hypotheses",
        json={"experiments": [_experiment_payload(assertions=[])]})
    assert resp.status_code == 400, resp.text


def test_experiment_agent_guard_rejects_empty_assertions():
    from backend.simulation.agent import run_experiment
    from backend.simulation.simulation_schema import ExperimentRequest

    with pytest.raises(ValueError, match="assertions must not be empty"):
        run_experiment(_tournament_definition(), {},
                       ExperimentRequest(name="x", hypothesis="y", assertions=[]))


# --- Visual moved out ---------------------------------------------------------

def test_twin_app_has_no_visual_route(twin_client, db):
    twin = _install_twin(db)
    assert twin_client.get(f"/v1/simulation/twins/{twin.id}/visual").status_code == 404
    assert twin_client.get(f"/simulation/twins/{twin.id}/visual").status_code == 404


def test_gateway_shim_still_serves_twin_visual(monolith_client, db):
    twin = _install_twin(db)
    resp = monolith_client.get(f"/simulation/twins/{twin.id}/visual")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["twin_id"] == twin.id
    assert len(body["nodes"]) == 2
    assert body["nodes"][0]["name"] == "Feed Tensioner"
    aliased = monolith_client.get(f"/v1/simulation/twins/{twin.id}/visual")
    assert aliased.status_code == 200


# --- Served surface + contract YAML -------------------------------------------

def test_twin_app_serves_contract_surface(twin_client):
    openapi = twin_client.get("/openapi.json").json()
    paths = set(openapi["paths"])
    expected = {
        "/v1/verify", "/v1/verify/jobs/{job_id}",
        "/v1/simulation/revisions/{revision_id}/compile",
        "/v1/simulation/twins/{twin_id}/approve",
        "/v1/simulation/revisions/{revision_id}/twin",
        "/v1/simulation/twins/{twin_id}/context",
        "/v1/simulation/twins/{twin_id}/experiment",
        "/v1/simulation/twins/{twin_id}/hypotheses",
        "/v1/digital-twins", "/v1/digital-twins/{twin_id}",
        "/v1/digital-twins/{twin_id}/commands",
        "/v1/digital-twins/{twin_id}/events",
        "/health", "/ready",
    }
    missing = expected - paths
    assert not missing, missing
    assert not any("visual" in p for p in paths), (
        "the twin service must not own presentation")
    assert not any("oracle" in p for p in paths)


def test_twin_contract_yaml():
    doc = yaml.safe_load((REPO / "contracts" / "openapi-twin-verifier.yaml").read_text())
    assert "/verify" in doc["paths"]
    assert "/verify/jobs/{job_id}" in doc["paths"]
    verdict = doc["components"]["schemas"]["VerdictResult"]
    assert verdict["properties"]["verdict"]["enum"] == [
        "VALIDATED", "REJECTED", "CANNOT_EXPRESS", "NOT_VERIFIED"]
    assert verdict["properties"]["twin_library_version"]["enum"] == ["3c-1.0.0"]


# --- Oracle relocation ---------------------------------------------------------

def test_oracle_moved_to_harness_scorer():
    assert (REPO / "benchmark" / "oracle.py").exists()
    assert not (REPO / "backend" / "simulation" / "oracle.py").exists()
    for name in ("run_benchmark_oracle.py", "run_phase3a_baseline.py",
                 "run_phase3b_oracle.py", "run_phase3c_oracle.py",
                 "run_phase4_offtable.py"):
        assert (REPO / "benchmark" / name).exists(), name
        assert not (REPO / "scripts" / name).exists(), name
    # No production module imports the oracle from its harness home.
    for path in (REPO / "backend").rglob("*.py"):
        text = path.read_text(errors="ignore")
        assert "benchmark.oracle" not in text, path
        assert "from benchmark import oracle" not in text, path
    from benchmark.oracle import load_vocabulary, run_oracle  # noqa: F401
    from backend.simulation.oracle_shared import normalize_text
    assert normalize_text("  Feed  Tensioner ") == "feed tensioner"


# --- CUTs verified gone; DEFER-retained kept ----------------------------------

def test_drafting_and_review_view_are_cut():
    assert not (REPO / "backend" / "simulation" / "drafting.py").exists()
    assert not (REPO / "backend" / "simulation" / "review_view.py").exists()
    for path in (REPO / "backend").rglob("*.py"):
        text = path.read_text(errors="ignore")
        assert "simulation.drafting" not in text, path
        assert "simulation.review_view" not in text, path
    # DEFER-retained as scalability base: kept, not cut.
    assert (REPO / "backend" / "simulation" / "field_promotion.py").exists()
    assert (REPO / "backend" / "simulation" / "library_versions.py").exists()
    from backend.simulation.library_versions import CURRENT_LIBRARY_VERSION
    assert CURRENT_LIBRARY_VERSION == "3c-1.0.0"


# --- Celery task config + early returns ---------------------------------------

def test_celery_task_retries_three_times_with_backoff():
    from backend.simulation.tasks import run_verify_job

    assert run_verify_job.max_retries == 3
    assert run_verify_job.retry_backoff is True
    assert Exception in (run_verify_job.autoretry_for or ())


def test_job_cap_seconds():
    from backend.simulation.tasks import job_cap_seconds

    assert job_cap_seconds({"hypotheses": [{"cause": "a"}]}) == 2.0
    assert job_cap_seconds({"hypotheses": [{"cause": "a"}] * 3,
                            "timeout_ms": 500}) == 1.5
    assert job_cap_seconds({"hypotheses": [{"cause": "a"}] * 10}) == 10.0
    assert job_cap_seconds({"hypotheses": [{"cause": "a"}] * 20,
                            "timeout_ms": 2000}) == 10.0


def test_celery_task_ignores_unknown_job():
    from backend.db.session import init_db
    from backend.simulation.tasks import run_verify_job

    init_db()
    result = run_verify_job.apply(args=["no-such-job"])
    assert result.state == "SUCCESS"
    assert result.result is None


def test_celery_task_marks_failed_job_on_worker_error():
    from backend.db.session import SessionLocal, init_db
    from backend.simulation.tasks import run_verify_job
    import backend.simulation.tasks as tasks_mod

    init_db()
    session = SessionLocal()
    job = VerifyJob(revision_id="r1", status="queued",
                    request={"revision_id": "r1",
                             "hypotheses": [{"cause": "x"}]})
    session.add(job)
    session.commit()
    job_id = job.id
    session.close()
    original = tasks_mod.execute_verify_job

    def _boom(session, payload):
        raise RuntimeError("simulated worker failure")

    tasks_mod.execute_verify_job = _boom
    try:
        result = run_verify_job.apply(args=[job_id])
        assert result.state == "FAILURE"
        check = SessionLocal()
        try:
            row = check.get(VerifyJob, job_id)
            assert row.status == "failed"
            assert "simulated worker failure" in (row.error_message or "")
        finally:
            check.close()
    finally:
        tasks_mod.execute_verify_job = original
        cleanup = SessionLocal()
        try:
            row = cleanup.get(VerifyJob, job_id)
            if row is not None:
                cleanup.delete(row)
                cleanup.commit()
        finally:
            cleanup.close()
