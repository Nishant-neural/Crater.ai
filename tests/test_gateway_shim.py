"""Gateway shim tests (gateway audit §8 acceptance + §2 budget).

Non-LLM only: no provider call is made here. The app is exercised
through TestClient; /ready is expected 503 in this environment (no
Qdrant, no CrossEncoder, no LLM key) — the test asserts the 503 names
the failed checks instead of asserting readiness.
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.api.middleware import LearningGateMiddleware
from backend.api.rate_limit import RateLimiter
from backend.api.readiness import check_llm_keys
from backend.config import settings

client = TestClient(app, raise_server_exceptions=False)


def test_health_is_liveness_only():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_request_id_generated_and_echoed():
    resp = client.get("/health")
    assert resp.headers["X-Request-ID"]
    assert resp.json() == {"status": "ok"}


def test_request_id_propagated_when_supplied():
    resp = client.get("/health", headers={"X-Request-ID": "trace-1"})
    assert resp.headers["X-Request-ID"] == "trace-1"


def test_unknown_route_uses_error_envelope():
    resp = client.get("/no-such-route")
    assert resp.status_code == 404
    error = resp.json()["error"]
    assert error["code"] == "not_found"
    assert error["message"]
    assert error["request_id"] == resp.headers["X-Request-ID"]


def test_expert_routes_cut_404_envelope():
    for path in ("/v1/expert/knowledge", "/expert/knowledge"):
        resp = client.get(path)
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"


def test_learning_gated_403_by_default():
    resp = client.get("/v1/learning/review")
    assert resp.status_code == 403
    error = resp.json()["error"]
    assert error["code"] == "learning_deferred"
    assert "pilot" in error["message"]


def test_learning_gate_opens_when_enabled(monkeypatch):
    # Gate logic on a dummy app: the real route needs a DB, the gate
    # itself must pass the request through once the flag flips.
    monkeypatch.setattr(settings, "learning_enabled", True)
    dummy = FastAPI()
    dummy.add_middleware(LearningGateMiddleware)

    @dummy.get("/v1/learning/review")
    def _review():
        return {"ok": True}

    assert TestClient(dummy).get("/v1/learning/review").json() == {"ok": True}


def test_validation_errors_use_error_envelope():
    resp = client.post("/v1/diagnose/start", json={})
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "unprocessable"
    assert "product_id" in error["message"]


def test_v1_and_legacy_diagnose_routes_both_resolve():
    # 422 = the handler was reached (validation); 404 would mean the
    # route is not mounted.
    assert client.post("/v1/diagnose/start", json={}).status_code == 422
    assert client.post("/diagnose/start", json={}).status_code == 422


def test_ready_503_names_failed_checks(monkeypatch, tmp_path):
    # Point the readiness DB probe at a throwaway sqlite file: the
    # module-global engine binds its relative path at import time, so
    # chdir cannot redirect it (verified while writing this test).
    from sqlalchemy import create_engine as _create_engine

    import backend.api.readiness as readiness_mod

    monkeypatch.setattr(
        readiness_mod, "engine",
        _create_engine(f"sqlite:///{tmp_path}/ready.db"),
    )
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "openrouter_api_key", "")
    resp = client.get("/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert "llm_keys" in body["checks"]
    assert "qdrant" in body["checks"]
    assert "openrouter" in body["checks"]["llm_keys"]["detail"]


def test_llm_keys_check_names_missing_provider(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openrouter")
    monkeypatch.setattr(settings, "openrouter_api_key", "")
    check = check_llm_keys()
    assert check["ready"] is False
    assert "openrouter" in check["detail"]
    monkeypatch.setattr(settings, "openrouter_api_key", "k")
    assert check_llm_keys()["ready"] is True


def test_auth_verify_allows_when_disabled_and_blocks_when_enabled(monkeypatch):
    monkeypatch.setattr(settings, "gateway_auth_enabled", False)
    assert client.get("/auth/verify").status_code == 200
    monkeypatch.setattr(settings, "gateway_auth_enabled", True)
    monkeypatch.setattr(settings, "gateway_api_keys", "secret-key")
    assert client.get("/auth/verify").status_code == 401
    resp = client.get("/auth/verify", headers={"X-Api-Key": "secret-key"})
    assert resp.status_code == 200


def test_rate_limiter_diagnosis_burst_then_429(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", "")
    limiter = RateLimiter()
    key = "diagnosis:test-key"
    allowed = sum(limiter.allow(key, "diagnosis")[0] for _ in range(20))
    assert allowed == 20  # burst capacity
    ok, retry_after = limiter.allow(key, "diagnosis")
    assert ok is False
    assert retry_after >= 1


def test_metrics_endpoint_serves_prometheus_text():
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "crater_gateway_requests_total" in resp.text
