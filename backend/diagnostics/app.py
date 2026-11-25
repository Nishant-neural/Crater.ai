"""Diagnosis-agent service entrypoint.

Run with: uvicorn backend.diagnostics.app:app --host 0.0.0.0 --port 8000

Phase 3 diagnosis-agent extraction (contracts README §0/§10, step 3):
this app serves ONLY the diagnosis-owned routes — the diagnose
sessions/turns (``routes/diagnostics.py``), the retrieval read
(``routes/query.py``), and the learning routes (``routes/learning.py``,
mounted but gated 403 ``learning_deferred`` by
``LearningGateMiddleware`` until the pilot gate flips, contracts
§2.4) — mounted under ``/v1/`` with the legacy unversioned alias,
exactly as the monolith mounts them today. Ingestion, twin, gateway
and Mode B job routes are NOT mounted here; they live in their own
services. The route handlers themselves are unchanged except for the
cross-service reads, which now go over HTTP to ingestion's API
(``backend/retrieval/ingestion_client.py``) instead of ingestion's
tables: the split moves the reads, not the loop logic.

/health is liveness only (200 while the process is up, checks
nothing). /ready gates on Postgres, Qdrant, the CrossEncoder model
and the ingestion API (``backend/diagnostics/readiness.py``) and
fails 503 naming the checks that failed — a downstream outage never
causes a restart loop, and never a silent degraded run.

The §2.5 public-surface removals land here (contracts §2.5):
verify-repair, verify-hypotheses, early-tournament, upstream-walk,
next-test, guided-checks, safety GET and fleet-patterns GET do not
exist on this app — a client calling any of them gets 404 in the
standard envelope. The tournament and the upstream walk themselves
stay, driven by POST /diagnose/{id}/respond.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.api.middleware import (
    LearningGateMiddleware, RequestIDMiddleware, error_envelope,
)
from backend.api.routes import diagnostics, learning, query
from backend.db.session import init_db
from backend.diagnostics.readiness import is_ready, readiness_checks
from backend.retrieval.hybrid import RetrievalUnavailableError
from backend.retrieval.ingestion_client import IngestionUnavailableError
from backend.retrieval.reranker import RerankerUnavailableError


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Crater.ai — diagnosis-agent service",
    version="1.0.0",
    lifespan=lifespan,
)

# Middleware: added last runs first. RequestID is outermost so the
# learning gate's 403 still carries a request ID.
app.add_middleware(LearningGateMiddleware)
app.add_middleware(RequestIDMiddleware)

# Diagnosis-owned routers only, under /v1/ plus the legacy alias
# during migration (same mount the monolith uses today).
_v1_routers = [diagnostics, query, learning]
for _router in _v1_routers:
    app.include_router(_router.router, prefix="/v1")
    app.include_router(_router.router)


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", None)
               or request.headers.get("X-Request-ID") or "unknown")


@app.get("/health")
def health():
    # Liveness only: 200 while the process is up, checks NO dependency
    # (contracts §1.4).
    return {"status": "ok"}


@app.get("/ready")
def ready():
    """Readiness: 200 only when Postgres, Qdrant, the CrossEncoder
    model and the ingestion API are all up (contracts §1.4).
    Otherwise 503 with a per-dependency breakdown naming which check
    failed."""
    checks = readiness_checks()
    ok = is_ready(checks)
    return JSONResponse(
        status_code=200 if ok else 503,
        content={"status": "ready" if ok else "not_ready", "checks": checks},
    )


@app.exception_handler(RetrievalUnavailableError)
async def retrieval_unavailable_handler(request: Request, exc: RetrievalUnavailableError):
    """Qdrant / semantic retrieval down: HTTP 503 with a reason (P-06).

    Raised from hybrid_retrieve, which runs inside run_turn for both
    POST /diagnose/start and POST /diagnose/{id}/respond, and inside
    POST /query — one handler covers all three. Never a silent
    BM25-only result: the fallback was deleted in Phase 1 Step 2.
    """
    return JSONResponse(
        status_code=503,
        content=error_envelope(
            "retrieval_degraded", str(exc.reason), _request_id(request),
            upstream="qdrant"),
    )


@app.exception_handler(RerankerUnavailableError)
async def reranker_unavailable_handler(request: Request, exc: RerankerUnavailableError):
    """CrossEncoder model not loaded: HTTP 503, never a silent fallback."""
    return JSONResponse(
        status_code=503,
        content=error_envelope(
            "reranker_unavailable", str(exc), _request_id(request),
            upstream="reranker"),
    )


@app.exception_handler(IngestionUnavailableError)
async def ingestion_unavailable_handler(request: Request, exc: IngestionUnavailableError):
    """Ingestion's HTTP API down: HTTP 503 naming ingestion (contracts
    §3.3 — diagnosis does not proceed on partial knowledge in silence)."""
    return JSONResponse(
        status_code=503,
        content=error_envelope(
            "ingestion_unavailable", str(exc.reason), _request_id(request),
            upstream="ingestion"),
    )


def _http_error_code(status_code: int) -> str:
    return {400: "bad_request", 401: "unauthenticated", 403: "forbidden",
            404: "not_found", 409: "conflict",
            422: "unprocessable", 429: "rate_limited"}.get(
                status_code, "http_error")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """422 validation failures in the standard envelope, not FastAPI's
    default {"detail": [...]} shape (contracts §1.3)."""
    return JSONResponse(
        status_code=422,
        content=error_envelope(
            "unprocessable",
            "Request validation failed: "
            + "; ".join(
                f"{'.'.join(str(p) for p in e.get('loc', []))}: {e.get('msg')}"
                for e in exc.errors()),
            _request_id(request)),
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Every HTTP error in the standard envelope (contracts §1.3)."""
    return JSONResponse(
        status_code=exc.status_code,
        content=error_envelope(
            _http_error_code(exc.status_code), str(exc.detail),
            _request_id(request)),
    )


@app.exception_handler(StarletteHTTPException)
async def starlette_http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Unknown routes (incl. the removed §2.5 paths) in the same envelope."""
    return JSONResponse(
        status_code=exc.status_code,
        content=error_envelope(
            _http_error_code(exc.status_code), str(exc.detail),
            _request_id(request)),
    )
