"""Test-only kit: a TwinVerifyClient wired to the REAL twin-verifier app.

Phase 3 twin-verifier extraction removed the in-monolith
``LocalVerifyBackend``: turn-level tests that used to verify against
a twin in the test DB now need a client that speaks the real
``POST /v1/verify`` (202 + job) contract. This helper builds one:

- the twin-verifier app (``backend.simulation.app``) served by a
  TestClient, with its ``get_session`` overridden to the test ``db``;
- the Celery dispatch stubbed (``monkeypatch``) to run the job inline
  against ``db`` via ``execute_job`` with a fresh session on the same
  engine for the worker thread (no broker in this VM);
- the client talks to that app over ``httpx.MockTransport``.

Nothing here is production code: the stub exists only so tests can
drive the real verify route + worker path without Redis. Use it as::

    client = make_twin_client(db, monkeypatch)
    summary = client.verify_sync([{"cause": ...}], revision_id="r1")
    # or pass it into run_turn / _apply_early_tournament as twin_client
"""
from __future__ import annotations

import httpx
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from backend.api.routes import verify as verify_routes
from backend.db.session import get_session
from backend.diagnostics.twin_verification import TwinVerifyClient
from backend.simulation.app import app as twin_app


def stub_inline_dispatch(db, monkeypatch):
    """Stub the verify Celery dispatch so jobs run inline against
    ``db`` (no broker in this VM). Returns the dispatched job ids.
    Shared by the twin service tests and the client tests."""
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


def make_twin_client(db, monkeypatch):
    """Build a TwinVerifyClient wired to the real twin-verifier app
    over HTTP, with the Celery dispatch stubbed to run inline."""
    dispatched = stub_inline_dispatch(db, monkeypatch)
    monkeypatch.setattr(
        twin_app, "dependency_overrides", {get_session: lambda: db})
    tc = TestClient(twin_app, raise_server_exceptions=False)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v1/verify":
            import json as _json

            resp = tc.post(
                "/v1/verify",
                json=_json.loads(request.content or b"{}"))
        elif (request.method == "GET"
              and request.url.path.startswith("/v1/verify/jobs/")):
            resp = tc.get(request.url.path)
        else:
            return httpx.Response(
                404, json={"error": {"code": "not_found"}})
        return httpx.Response(resp.status_code, json=resp.json())

    return TwinVerifyClient(
        base_url="http://twin-test",
        transport=httpx.MockTransport(handler))
