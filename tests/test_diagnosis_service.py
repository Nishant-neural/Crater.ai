"""Phase 3 diagnosis-agent extraction tests (contracts README §0/§10 step 3).

Non-LLM only: no provider call is made here. The diagnosis-agent
service app (``backend.diagnostics.app``) is exercised through
TestClient against an in-memory SQLite database; the ingestion HTTP
API is stubbed with httpx.MockTransport / fake clients, and the
Qdrant-down kill test uses the real absence of Qdrant.

Covered:
- diagnosis /health (liveness) + /ready (503 naming Postgres/Qdrant/
  CrossEncoder/ingestion failures; 200 when the checks are mocked ready).
- POST /v1/diagnose/start validates product/revision against
  ingestion's HTTP API (404 unknown product/revision) and creates
  the session (LLM + retrieval stubbed, as in the walk tests).
- All 8 §2.5 endpoints 404 on the diagnosis app; the served OpenAPI
  covers the contracts/openapi-diagnosis.yaml paths and none of the
  removed ones.
- Learning routes gated 403 learning_deferred on the diagnosis app.
- Knowledge via HTTP: POST /v1/query checks the revision through the
  ingestion client; hybrid/BM25 read chunks from the ingestion client.
- Kill tests: Qdrant down -> POST /v1/query 503 naming Qdrant in the
  standard envelope, while /health stays 200; ingestion down ->
  diagnosis /ready 503 naming ingestion.
- Mode B diagnose calls the diagnosis-agent service over HTTP
  (``_start_diagnosis_session``): success, HTTP error -> 503,
  non-2xx status passthrough.
- Twin client HTTP path: POST /v1/verify -> 202 + job, poll to a
  succeeded job, VALIDATED only with rules_used (the real twin-app
  HTTP path is covered by tests/test_twin_client.py).
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.models import Base, Product, Revision
from backend.db.session import get_session
from backend.diagnostics.app import app as diagnosis_app

REPO = Path(__file__).resolve().parent.parent

REMOVED_PATHS = [
    "/v1/diagnose/{session_id}/verify-repair",
    "/v1/diagnose/{session_id}/verify-hypotheses",
    "/v1/diagnose/{session_id}/early-tournament",
    "/v1/diagnose/{session_id}/upstream-walk",
    "/v1/diagnose/{session_id}/next-test",
    "/v1/diagnose/{session_id}/guided-checks",
    "/v1/diagnose/{session_id}/safety",
    "/v1/diagnose/{session_id}/fleet-patterns",
]


def _db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


@pytest.fixture(autouse=True)
def _global_tables():
    """The Postgres progress store uses the global engine
    (./crater.db by default); make sure its tables exist."""
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
def diagnosis_client(db):
    diagnosis_app.dependency_overrides[get_session] = lambda: db
    client = TestClient(diagnosis_app, raise_server_exceptions=False)
    yield client
    diagnosis_app.dependency_overrides.clear()


class _FakeIngestionClient:
    """Ingestion HTTP API, canned for one product + revision."""

    def __init__(self, product_id, revision_id, chunks=None, model=None):
        self._product_id = product_id
        self._revision_id = revision_id
        self._chunks = chunks or []
        self._model = model or {"entities": [], "relations": []}

    def list_products(self):
        return [{"id": self._product_id, "manufacturer": "Harbor Freight",
                 "family": "OMNIPRO", "model": "220"}]

    def list_revisions(self, product_id):
        if product_id != self._product_id:
            return None
        return [{"id": self._revision_id, "product_id": product_id,
                 "label": "Rev A"}]

    def get_revision_summary(self, revision_id):
        if revision_id != self._revision_id:
            return None
        return {"id": revision_id, "product_id": self._product_id,
                "label": "Rev A"}

    def get_model(self, revision_id):
        if revision_id != self._revision_id:
            return None
        return {"revision_id": revision_id, "version": 1,
                "model": self._model, "source_counts": {}}

    def get_failure_modes(self, revision_id, symptom=None):
        return []

    def fetch_chunks(self, revision_id, **kwargs):
        return list(self._chunks)

    def fetch_all_chunks(self, revision_id, doc_type=None):
        return list(self._chunks)

    def fetch_chunks_by_ids(self, revision_id, ids):
        by_id = {c["id"]: c for c in self._chunks}
        return [by_id[cid] for cid in ids if cid in by_id]


@pytest.fixture()
def stub_ingestion(monkeypatch, db):
    """Stub the ingestion client factory for the seeded product/revision."""
    import backend.retrieval.ingestion_client as ingestion_client_mod

    product = db._test_product  # type: ignore[attr-defined]
    revision = db._test_revision  # type: ignore[attr-defined]
    fake = _FakeIngestionClient(product.id, revision.id)

    def _factory(*a, **k):
        return fake

    monkeypatch.setattr(ingestion_client_mod, "get_ingestion_client", _factory)
    return fake


@pytest.fixture()
def stub_turn(monkeypatch):
    """No LLM, no Qdrant in this VM: the turn degrades to keeping the
    (empty, at start) hypotheses, which is all the start test needs."""
    import backend.diagnostics.agent as agent_module

    monkeypatch.setattr(agent_module, "hybrid_retrieve", lambda *a, **k: [])
    monkeypatch.setattr(agent_module.gateway, "complete_result",
                        lambda *a, **k: ("not json", False))


# --- /health + /ready -------------------------------------------------------

def test_diagnosis_health_is_liveness_only(diagnosis_client):
    resp = diagnosis_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_diagnosis_ready_503_names_failed_checks(diagnosis_client):
    # No Qdrant, no CrossEncoder (sentence_transformers is not
    # installed in this VM) and no ingestion API on :8000; Postgres
    # is the local SQLite file and answers SELECT 1.
    resp = diagnosis_client.get("/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    checks = body["checks"]
    assert checks["postgres"]["ready"] is True
    for name in ("qdrant", "reranker", "ingestion"):
        assert checks[name]["ready"] is False, name
        assert checks[name]["detail"], name


def test_diagnosis_ready_200_when_checks_pass(diagnosis_client, monkeypatch):
    import backend.diagnostics.readiness as readiness

    monkeypatch.setattr(readiness, "check_qdrant",
                        lambda: {"ready": True, "detail": "mocked"})
    monkeypatch.setattr(readiness, "check_reranker",
                        lambda: {"ready": True, "detail": "mocked"})
    monkeypatch.setattr(readiness, "check_ingestion",
                        lambda: {"ready": True, "detail": "mocked"})
    resp = diagnosis_client.get("/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"


def test_diagnosis_ready_503_names_ingestion_only(diagnosis_client, monkeypatch):
    """Ingestion-API-down -> diagnosis /ready 503 naming ingestion
    (the other checks pass)."""
    import backend.diagnostics.readiness as readiness

    monkeypatch.setattr(readiness, "check_qdrant",
                        lambda: {"ready": True, "detail": "mocked"})
    monkeypatch.setattr(readiness, "check_reranker",
                        lambda: {"ready": True, "detail": "mocked"})
    resp = diagnosis_client.get("/ready")
    assert resp.status_code == 503
    checks = resp.json()["checks"]
    assert checks["ingestion"]["ready"] is False
    assert checks["qdrant"]["ready"] is True
    assert checks["reranker"]["ready"] is True


# --- contract YAML + served surface ------------------------------------------

def test_diagnosis_contract_has_no_removed_paths():
    doc = yaml.safe_load((REPO / "contracts" / "openapi-diagnosis.yaml").read_text())
    for path in doc["paths"]:
        for removed in ("verify-repair", "verify-hypotheses", "early-tournament",
                        "upstream-walk", "next-test", "guided-checks",
                        "safety", "fleet-patterns", "expert"):
            assert removed not in path, path


def test_diagnosis_app_serves_contract_surface(diagnosis_client):
    openapi = diagnosis_client.get("/openapi.json").json()
    paths = set(openapi["paths"])
    contract = {"", "/v1"}
    expected = set()
    for prefix in contract:
        for path in ("/diagnose/start", "/diagnose/sessions",
                     "/diagnose/{session_id}/respond",
                     "/diagnose/{session_id}/context",
                     "/diagnose/{session_id}",
                     "/diagnose/{session_id}/outcome",
                     "/query"):
            expected.add(prefix + path)
    expected |= {"/health", "/ready"}
    missing = expected - paths
    assert not missing, missing
    # The §2.5 removals are absent from the served surface.
    for removed in REMOVED_PATHS:
        assert removed not in paths, removed
    assert not any("expert" in p for p in paths)


def test_removed_endpoints_404(diagnosis_client, db):
    from backend.db.models import DiagnosticSession, DiagnosticStatus

    session = DiagnosticSession(product_id="p", revision_id="r",
                                status=DiagnosticStatus.active, state={})
    db.add(session)
    db.commit()
    sid = session.id
    for removed in REMOVED_PATHS:
        path = removed.replace("{session_id}", sid)
        resp = diagnosis_client.get(path)
        assert resp.status_code == 404, ("get", path, resp.text)
        resp = diagnosis_client.post(path, json={})
        assert resp.status_code == 404, ("post", path, resp.text)


# --- learning gate -----------------------------------------------------------

def test_learning_routes_gated_403(diagnosis_client):
    resp = diagnosis_client.get("/v1/learning/review")
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "learning_deferred"
    resp = diagnosis_client.get("/learning/review")
    assert resp.status_code == 403, resp.text


# --- start validates product/revision over HTTP ------------------------------

def test_start_404_unknown_product(diagnosis_client, stub_ingestion):
    resp = diagnosis_client.post("/v1/diagnose/start", json={
        "product_id": "no-such-product",
        "symptom": "Wire does not feed",
    })
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "not_found"


def test_start_404_unknown_revision(diagnosis_client, stub_ingestion, db):
    product = db._test_product  # type: ignore[attr-defined]
    resp = diagnosis_client.post("/v1/diagnose/start", json={
        "product_id": product.id, "revision_id": "no-such-revision",
        "symptom": "Wire does not feed",
    })
    assert resp.status_code == 404, resp.text


def test_start_creates_session(diagnosis_client, stub_ingestion, stub_turn, db):
    product = db._test_product  # type: ignore[attr-defined]
    revision = db._test_revision  # type: ignore[attr-defined]
    resp = diagnosis_client.post("/v1/diagnose/start", json={
        "product_id": product.id, "revision_id": revision.id,
        "symptom": "Wire does not feed",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["session_id"]
    assert body["status"] == "active"


def test_start_ingestion_down_503_names_ingestion(diagnosis_client):
    # No ingestion client stub: the real client hits localhost:8000,
    # which nothing listens on in this VM.
    resp = diagnosis_client.post("/v1/diagnose/start", json={
        "product_id": "p1", "symptom": "Wire does not feed",
    })
    assert resp.status_code == 503, resp.text
    error = resp.json()["error"]
    assert error["code"] == "ingestion_unavailable"
    assert error["upstream"] == "ingestion"


# --- knowledge via HTTP ------------------------------------------------------

def test_query_checks_revision_via_http(diagnosis_client, stub_ingestion):
    # Unknown revision -> 404 from the revision check, before any retrieval.
    resp = diagnosis_client.post("/v1/query", json={
        "question": "why does the wire not feed?",
        "revision_id": "no-such-revision",
    })
    assert resp.status_code == 404, resp.text


def test_hybrid_retrieve_reads_chunks_via_http(monkeypatch):
    """hybrid_retrieve fuses semantic + BM25 ids, then fetches the
    chunk contents from the ingestion client — no Chunk table read."""
    import backend.retrieval.hybrid as hybrid

    chunks = [
        {"id": "c1", "content": "Terminal X12 should read 24 V.",
         "page_number": 12, "document_id": "d1",
         "document_title": "Manual", "doc_type": "manual",
         "chunk_type": "text"},
        {"id": "c2", "content": "Check the drive roll pressure.",
         "page_number": 31, "document_id": "d1",
         "document_title": "Manual", "doc_type": "manual",
         "chunk_type": "text"},
    ]
    fake = _FakeIngestionClient("prod-1", "rev-1", chunks=chunks)
    monkeypatch.setattr(hybrid, "semantic_search",
                        lambda *a, **k: [("c1", 1.0), ("c2", 0.9)])
    monkeypatch.setattr(hybrid, "bm25_search",
                        lambda *a, **k: [("c1", 1.0)])
    monkeypatch.setattr(hybrid, "rerank",
                        lambda _q, candidates, _top_k: [cid for cid, _ in candidates])
    out = hybrid.hybrid_retrieve("What voltage at X12?", revision_id="rev-1",
                                 client=fake)
    assert [r.chunk_id for r in out] == ["c1", "c2"]
    assert out[0].content == "Terminal X12 should read 24 V."
    assert out[0].source_document == "Manual"


def test_bm25_search_reads_chunks_via_http():
    """BM25 builds its corpus from the ingestion client's chunk pages."""
    from backend.retrieval.bm25 import bm25_search

    chunks = [
        {"id": "c1", "content": "Terminal X12 should read 24 V.",
         "page_number": 12, "document_id": "d1",
         "document_title": "Manual", "doc_type": "manual",
         "chunk_type": "text"},
    ]
    fake = _FakeIngestionClient("prod-1", "rev-1", chunks=chunks)
    out = bm25_search("terminal X12 voltage", 5, revision_id="rev-1", client=fake)
    assert out and out[0][0] == "c1"
    # No scope at all returns []: retrieval is hard revision-scoped.
    assert bm25_search("terminal", 5, client=fake) == []


# --- Qdrant-down kill test ---------------------------------------------------

def test_qdrant_down_query_503_names_qdrant_health_200(diagnosis_client, stub_ingestion, db):
    revision = db._test_revision  # type: ignore[attr-defined]
    resp = diagnosis_client.post("/v1/query", json={
        "question": "why does the wire not feed?",
        "revision_id": revision.id,
    })
    assert resp.status_code == 503, resp.text
    error = resp.json()["error"]
    assert error["code"] == "retrieval_degraded"
    assert error["upstream"] == "qdrant"
    assert "qdrant" in error["message"].lower() or "semantic" in error["message"].lower()
    health = diagnosis_client.get("/health")
    assert health.status_code == 200


# --- Mode B diagnose over HTTP -----------------------------------------------

def test_mode_b_start_diagnosis_session_http(monkeypatch):
    """_start_diagnosis_session POSTs to the diagnosis-agent service:
    success returns the session id; a transport failure is a 503;
    a non-2xx status passes through."""
    import httpx as _httpx
    import backend.api.routes.mode_b as mode_b_routes

    class _Resp:
        def __init__(self, status_code, payload=None):
            self.status_code = status_code
            self._payload = payload or {}

        def json(self):
            return self._payload

    def _client_with(post):
        class _FakeClient:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def post(self, *a, **k):
                return post(*a, **k)

        return _FakeClient

    monkeypatch.setattr(_httpx, "Client",
                        _client_with(lambda *a, **k: _Resp(200, {"session_id": "sess-9"})))
    out = mode_b_routes._start_diagnosis_session("p1", "r1", "Wire does not feed")
    assert out == {"session_id": "sess-9"}

    def _down(*a, **k):
        raise _httpx.ConnectError(
            "connection refused",
            request=_httpx.Request("POST", "http://diagnosis-agent:8000/v1/diagnose/start"))

    monkeypatch.setattr(_httpx, "Client", _client_with(_down))
    import pytest as _pytest
    from fastapi import HTTPException as _HTTPException
    with _pytest.raises(_HTTPException) as excinfo:
        mode_b_routes._start_diagnosis_session("p1", "r1", "Wire does not feed")
    assert excinfo.value.status_code == 503

    monkeypatch.setattr(
        _httpx, "Client",
        _client_with(lambda *a, **k: _Resp(404, {"error": {"code": "not_found"}})))
    with _pytest.raises(_HTTPException) as excinfo:
        mode_b_routes._start_diagnosis_session("p1", "r1", "Wire does not feed")
    assert excinfo.value.status_code == 404


# --- twin client HTTP path ---------------------------------------------------

def test_twin_client_http_202_poll_validated():
    """TwinVerifyClient over HTTP: POST /v1/verify -> 202 + job, poll
    GET /v1/verify/jobs/{id} to a succeeded job; VALIDATED stands only
    with rules_used (test_twin_client.py covers the in-process backend
    and the degrade paths)."""
    from backend.diagnostics.twin_verification import TwinVerifyClient

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v1/verify":
            return httpx.Response(202, json={"job_id": "job-1"})
        if request.method == "GET" and request.url.path == "/v1/verify/jobs/job-1":
            return httpx.Response(200, json={
                "job_id": "job-1", "status": "succeeded", "twin_id": "twin-1",
                "results": [{"cause": "Feed Tensioner is too tight.",
                             "verdict": "VALIDATED",
                             "reason": "seed rules fired",
                             "rules_used": [{"id": "seed:feed-tension-too-tight"}]}]})
        return httpx.Response(404, json={"error": {"code": "not_found"}})

    client = TwinVerifyClient(base_url="http://twin-test",
                              transport=httpx.MockTransport(handler))
    out = client.verify_sync([{"cause": "Feed Tensioner is too tight."}],
                             revision_id="rev-1")
    assert out["available"] is True
    assert out["counts"] == {"validated": 1, "rejected": 0, "unavailable": 0}
    assert out["results"][0]["verdict"] == "VALIDATED"


def test_twin_client_http_down_degrades_not_verified():
    from backend.diagnostics.twin_verification import TwinVerifyClient

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = TwinVerifyClient(base_url="http://twin-test",
                              transport=httpx.MockTransport(handler))
    out = client.verify_sync([{"cause": "Feed Tensioner is too tight."}],
                             revision_id="rev-1")
    assert out["available"] is False
    assert out["results"][0]["verdict"] == "NOT_VERIFIED"
