"""Phase 3 ingestion extraction tests (contracts README §3, §8).

Non-LLM only: no provider call is made here. The ingestion service
app (``backend.ingestion.app``) and the monolith app are exercised
through TestClient against an in-memory SQLite database; the Celery
dispatch is stubbed at the route seam (no broker in this VM), and
the Qdrant-down kill test uses the real absence of Qdrant.

Covered:
- ingestion /health (liveness) + /ready (503 naming Postgres/Qdrant/
  Redis/Celery failures; 200 when the checks are mocked ready).
- POST /v1/ingest dispatches to Celery (not BackgroundTasks):
  202 + job, idempotency reuse, same-key/different-content 409,
  broker outage 503 naming Redis/Celery.
- GET /v1/ingest/jobs/{id} carries the contracts §3.2 progress object.
- GET /v1/knowledge/revisions/{id}/failure-modes (new in this step).
- No stale-heuristic flip: a processing job with an old updated_at
  stays processing in the revision status.
- Expert trio CUT: files gone, no expert route on the ingestion app.
- Contract YAML: no POST /v1/tables/parse anywhere; the served
  ingestion paths match the contract's job/knowledge surface.
- Kill test: Qdrant down -> POST /v1/query 503 naming Qdrant in the
  standard envelope, while /health stays 200.
"""
from __future__ import annotations

import io
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.models import (
    Base, DocType, Document, FailureMode, IngestionJob, IngestionStatus,
    Product, Revision,
)
from backend.db.session import get_session
from backend.ingestion.app import app as ingestion_app
from backend.api.main import app as monolith_app

REPO = Path(__file__).resolve().parent.parent


def _db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


@pytest.fixture(autouse=True)
def _global_tables():
    """The Celery task and the Postgres progress store use the global
    engine (./crater.db by default); make sure its tables exist."""
    from backend.db.session import init_db

    init_db()


@pytest.fixture()
def db():
    session = _db()
    product = Product(manufacturer="Harbor Freight", family="OMNIPRO", model="220")
    session.add(product)
    session.flush()
    revision = Revision(product_id=product.id, label="Rev A")
    session.add(revision)
    session.flush()
    session._test_product = product  # type: ignore[attr-defined]
    session._test_revision = revision  # type: ignore[attr-defined]
    yield session
    session.close()


@pytest.fixture()
def ingestion_client(db, monkeypatch):
    """Ingestion app with the request session overridden to the test
    DB and the Postgres progress store stubbed in-memory (the real
    store uses the global engine; the stub keeps this hermetic)."""
    import backend.api.routes.ingestion as routes_ing

    store: dict = {}

    def fake_load(job_id):
        data = {"stage": "start", "completed_pages": [], "vectors_done": False,
                "vector_chunks_done": [], "knowledge_done": [],
                "knowledge_failures": {}, "schematics_done": [],
                "integration_done": False, "verification_warnings": [],
                "tier2_deferred": [], "ingest_tier2": True}
        data.update(store.get(job_id, {}))
        return data

    def fake_save(job_id, **updates):
        data = fake_load(job_id)
        data.update(updates)
        store[job_id] = data
        return dict(data)

    monkeypatch.setattr(routes_ing, "load_checkpoint", fake_load)
    monkeypatch.setattr(routes_ing, "save_checkpoint", fake_save)

    dispatched: list = []

    class _FakeTask:
        @staticmethod
        def delay(job_id):
            dispatched.append(job_id)

    monkeypatch.setattr(routes_ing, "run_ingestion_job", _FakeTask)
    ingestion_app.dependency_overrides[get_session] = lambda: db
    client = TestClient(ingestion_app, raise_server_exceptions=False)
    client._dispatched = dispatched  # type: ignore[attr-defined]
    yield client
    ingestion_app.dependency_overrides.clear()


@pytest.fixture()
def monolith_client(db):
    monolith_app.dependency_overrides[get_session] = lambda: db
    client = TestClient(monolith_app, raise_server_exceptions=False)
    yield client
    monolith_app.dependency_overrides.clear()


# --- /health + /ready -------------------------------------------------------

def test_ingestion_health_is_liveness_only(ingestion_client):
    resp = ingestion_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_ingestion_ready_503_names_failed_checks(ingestion_client):
    # No Qdrant, no Redis, no Celery worker in this VM; Postgres is
    # the local SQLite file and answers SELECT 1.
    resp = ingestion_client.get("/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    checks = body["checks"]
    assert checks["postgres"]["ready"] is True
    for name in ("qdrant", "redis", "celery"):
        assert checks[name]["ready"] is False, name
        assert checks[name]["detail"], name


def test_ingestion_ready_200_when_checks_pass(ingestion_client, monkeypatch):
    import backend.ingestion.readiness as readiness

    monkeypatch.setattr(readiness, "check_qdrant",
                        lambda: {"ready": True, "detail": "mocked"})
    monkeypatch.setattr(readiness, "check_redis",
                        lambda: {"ready": True, "detail": "mocked"})
    monkeypatch.setattr(readiness, "check_celery",
                        lambda: {"ready": True, "detail": "mocked"})
    resp = ingestion_client.get("/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"


# --- POST /v1/ingest dispatches to Celery -----------------------------------

def _upload(client, revision_id, key, pdf_bytes):
    return client.post(
        "/v1/ingest",
        data={"revision_id": revision_id, "doc_type": "manual",
              "title": "OMNIPRO 220", "idempotency_key": key},
        files={"file": ("manual.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
    )


def test_ingest_dispatches_to_celery_and_returns_job(ingestion_client, db):
    revision = db._test_revision  # type: ignore[attr-defined]
    pdf = b"%PDF-1.4 fake manual"
    resp = _upload(ingestion_client, revision.id, "key-1", pdf)
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["job_id"]
    assert body["status"] == "queued"
    assert body["progress"]["pages_done"] == 0
    assert ingestion_client._dispatched == [body["job_id"]]  # type: ignore[attr-defined]
    # The uploaded file is content-addressed on disk; clean it up.
    from backend.api.routes.ingestion import _store_upload  # noqa: F401
    import hashlib
    sha = hashlib.sha256(pdf).hexdigest()
    stored = REPO / "data" / "uploads" / f"{sha}.pdf"
    if stored.exists():
        stored.unlink()


def test_ingest_idempotency_reuse_and_conflict(ingestion_client, db):
    revision = db._test_revision  # type: ignore[attr-defined]
    pdf_a = b"%PDF-1.4 manual A"
    pdf_b = b"%PDF-1.4 manual B different"
    first = _upload(ingestion_client, revision.id, "key-dup", pdf_a)
    assert first.status_code == 202
    job_id = first.json()["job_id"]
    dispatched_before = list(ingestion_client._dispatched)  # type: ignore[attr-defined]
    # Same key + same content: reuse, no second dispatch.
    again = _upload(ingestion_client, revision.id, "key-dup", pdf_a)
    assert again.status_code == 202
    assert again.json()["reused"] is True
    assert again.json()["job_id"] == job_id
    assert ingestion_client._dispatched == dispatched_before  # type: ignore[attr-defined]
    # Same key + different content: 409.
    conflict = _upload(ingestion_client, revision.id, "key-dup", pdf_b)
    assert conflict.status_code == 409
    import hashlib
    for pdf in (pdf_a, pdf_b):
        stored = REPO / "data" / "uploads" / f"{hashlib.sha256(pdf).hexdigest()}.pdf"
        if stored.exists():
            stored.unlink()


def test_ingest_broker_outage_is_loud_503(ingestion_client, db, monkeypatch):
    import backend.api.routes.ingestion as routes_ing

    class _DownTask:
        @staticmethod
        def delay(job_id):
            raise ConnectionError("Error 111 connecting to localhost:6379")

    monkeypatch.setattr(routes_ing, "run_ingestion_job", _DownTask)
    revision = db._test_revision  # type: ignore[attr-defined]
    resp = _upload(ingestion_client, revision.id, "key-down", b"%PDF-1.4 x")
    assert resp.status_code == 503, resp.text
    error = resp.json()["error"]
    assert "redis" in error["message"].lower() or "celery" in error["message"].lower()
    import hashlib
    stored = REPO / "data" / "uploads" / f"{hashlib.sha256(b'%PDF-1.4 x').hexdigest()}.pdf"
    if stored.exists():
        stored.unlink()


def test_job_response_carries_contract_progress(ingestion_client, db):
    revision = db._test_revision  # type: ignore[attr-defined]
    resp = _upload(ingestion_client, revision.id, "key-prog", b"%PDF-1.4 p")
    job_id = resp.json()["job_id"]
    got = ingestion_client.get(f"/v1/ingest/jobs/{job_id}")
    assert got.status_code == 200
    progress = got.json()["progress"]
    for field in ("pages_total", "pages_done", "chunks_total", "chunks_done",
                  "table_rows_total", "table_rows_parsed",
                  "tier2_deferred_chunks", "knowledge_failures",
                  "integration_done"):
        assert field in progress, field
    assert progress["knowledge_failures"] == []
    assert progress["integration_done"] is False
    import hashlib
    stored = REPO / "data" / "uploads" / f"{hashlib.sha256(b'%PDF-1.4 p').hexdigest()}.pdf"
    if stored.exists():
        stored.unlink()


# --- failure-modes read API -------------------------------------------------

def test_failure_modes_endpoint(ingestion_client, db):
    revision = db._test_revision  # type: ignore[attr-defined]
    db.add(FailureMode(revision_id=revision.id, symptom="Wire does not feed",
                       possible_causes=["Drive roll pressure low", "Liner blocked"],
                       diagnostic_test="Check drive roll tension",
                       expected_observation="Wire feeds smoothly",
                       source_chunk_id=None))
    db.add(FailureMode(revision_id=revision.id, symptom="No arc",
                       possible_causes=["Loose ground clamp"],
                       diagnostic_test="Check ground clamp",
                       expected_observation="Arc strikes",
                       source_chunk_id=None))
    db.commit()
    resp = ingestion_client.get(f"/v1/knowledge/revisions/{revision.id}/failure-modes")
    assert resp.status_code == 200
    modes = resp.json()["failure_modes"]
    assert len(modes) == 2
    by_symptom = {m["symptom"]: m for m in modes}
    assert by_symptom["Wire does not feed"]["possible_causes"] == [
        "Drive roll pressure low", "Liner blocked"]
    filtered = ingestion_client.get(
        f"/v1/knowledge/revisions/{revision.id}/failure-modes",
        params={"symptom": "arc"})
    assert [m["symptom"] for m in filtered.json()["failure_modes"]] == ["No arc"]
    missing = ingestion_client.get("/v1/knowledge/revisions/nope/failure-modes")
    assert missing.status_code == 404


# --- no stale-heuristic flip ------------------------------------------------

def test_processing_job_is_not_flipped_by_status_read(ingestion_client, db):
    revision = db._test_revision  # type: ignore[attr-defined]
    job = IngestionJob(revision_id=revision.id, idempotency_key="stale-check",
                       content_sha256="a" * 64, doc_type=DocType.manual,
                       title="Manual", source_path="/tmp/manual.pdf",
                       status=IngestionStatus.processing,
                       stage="processing|45|Building canonical machine knowledge")
    db.add(job)
    db.commit()
    # Age the row past the old 5-minute heuristic; the status read
    # must NOT flip it to failed (the heuristic is deleted).
    job.updated_at = datetime.utcnow() - timedelta(minutes=10)
    db.commit()
    resp = ingestion_client.get(f"/v1/ingest/revisions/{revision.id}/status")
    assert resp.status_code == 200
    assert resp.json()["status"] == "processing"
    assert resp.json()["jobs"][0]["status"] == "processing"


# --- expert trio CUT ---------------------------------------------------------

def test_expert_trio_files_are_gone():
    for path in ("backend/knowledge/expert.py",
                 "backend/knowledge/expert_schema.py",
                 "backend/knowledge/expert_prompts.py",
                 "backend/api/routes/expert.py",
                 "frontend/src/components/ExpertKnowledge.jsx"):
        assert not (REPO / path).exists(), path


def test_ingestion_app_has_no_expert_route(ingestion_client):
    resp = ingestion_client.get("/v1/expert/knowledge")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


# --- contract YAML -----------------------------------------------------------

def test_ingestion_contract_has_no_table_parser_service():
    doc = yaml.safe_load((REPO / "contracts" / "openapi-ingestion.yaml").read_text())
    for path in doc["paths"]:
        assert "tables/parse" not in path, path
        assert "expert" not in path, path


def test_ingestion_app_serves_contract_surface(ingestion_client):
    openapi = ingestion_client.get("/openapi.json").json()
    paths = set(openapi["paths"])
    expected = {
        "/v1/ingest", "/v1/ingest/jobs/{job_id}",
        "/v1/ingest/jobs/{job_id}/retry",
        "/v1/ingest/jobs/{job_id}/lazy-ingest",
        "/v1/ingest/revisions/{revision_id}/status",
        "/v1/knowledge/revisions/{revision_id}/failure-modes",
        "/v1/knowledge/revisions/{revision_id}/model",
        "/v1/knowledge/revisions/{revision_id}/entities",
        "/v1/knowledge/revisions/{revision_id}/facts",
        "/v1/knowledge/revisions/{revision_id}/verify",
        "/v1/knowledge/revisions/{revision_id}/integrate",
        "/v1/mode-b/start",
        "/v1/mode-b/jobs/{job_id}/progress",
        "/v1/mode-b/jobs/{job_id}/diagnose",
        "/v1/mode-b/products/{product_id}/graduate",
        "/health", "/ready",
    }
    missing = expected - paths
    assert not missing, missing
    assert not any("tables/parse" in p for p in paths)
    assert not any("expert" in p for p in paths)


# --- Celery task config + early returns --------------------------------------

def test_celery_task_retries_three_times_with_backoff():
    from backend.ingestion.tasks import run_ingestion_job

    assert run_ingestion_job.max_retries == 3
    assert run_ingestion_job.retry_backoff is True
    assert Exception in (run_ingestion_job.autoretry_for or ())


def test_celery_task_ignores_unknown_job():
    from backend.ingestion.tasks import run_ingestion_job

    result = run_ingestion_job.apply(args=["no-such-job"])
    assert result.state == "SUCCESS"
    assert result.result is None


def test_celery_task_marks_missing_revision_failed():
    from backend.db.session import SessionLocal
    from backend.ingestion.tasks import run_ingestion_job

    session = SessionLocal()
    job = IngestionJob(revision_id="no-such-revision", idempotency_key="task-missing-rev",
                       content_sha256="b" * 64, doc_type=DocType.manual,
                       title="Manual", source_path="/tmp/manual.pdf")
    session.add(job)
    session.commit()
    job_id = job.id
    session.close()
    try:
        result = run_ingestion_job.apply(args=[job_id])
        assert result.state == "SUCCESS"
        check = SessionLocal()
        try:
            row = check.get(IngestionJob, job_id)
            assert row.status == IngestionStatus.failed
            assert row.error_message == "Revision not found"
        finally:
            check.close()
    finally:
        cleanup = SessionLocal()
        try:
            row = cleanup.get(IngestionJob, job_id)
            if row is not None:
                cleanup.delete(row)
                cleanup.commit()
        finally:
            cleanup.close()
        from backend.ingestion.checkpoints import clear_checkpoint
        clear_checkpoint(job_id)


# --- Qdrant-down kill test (monolith /v1/query) ------------------------------

def test_qdrant_down_query_503_names_qdrant_health_200(monolith_client, db, monkeypatch):
    # Phase 3 extraction: POST /v1/query checks the revision against
    # ingestion's HTTP API before retrieving, so the ingestion client
    # is stubbed to return the seeded revision — the Qdrant failure
    # below must still surface as 503 retrieval_degraded.
    import backend.retrieval.ingestion_client as ingestion_client_mod

    revision = db._test_revision  # type: ignore[attr-defined]

    class _FakeIngestionClient:
        def get_revision_summary(self, revision_id):
            if revision_id == revision.id:
                return {"id": revision.id, "product_id": revision.product_id,
                        "label": "Rev A"}
            return None

    monkeypatch.setattr(ingestion_client_mod, "get_ingestion_client",
                        lambda *a, **k: _FakeIngestionClient())
    resp = monolith_client.post("/v1/query", json={
        "question": "why does the wire not feed?",
        "revision_id": revision.id,
    })
    assert resp.status_code == 503, resp.text
    error = resp.json()["error"]
    assert error["code"] == "retrieval_degraded"
    assert error["upstream"] == "qdrant"
    assert "qdrant" in error["message"].lower() or "semantic" in error["message"].lower()
    health = monolith_client.get("/health")
    assert health.status_code == 200
