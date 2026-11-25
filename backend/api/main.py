"""FastAPI app entrypoint. Run with: uvicorn backend.api.main:app --reload

Gateway Step 1 (gateway audit §5/§9, contracts README §2): the edge
is Traefik v3 in front of this thin shim, and the shim lives inside
the monolith until the oracle gate passes. All route handlers move
under /v1/ VERBATIM (backend/api/routes/*.py, unchanged); the
unversioned paths survive as a legacy alias during migration only,
then are deleted. The shim owns only: X-Request-ID, the forwardAuth
target (API-key validation), /health (liveness), /ready (aggregation),
the uniform error envelope, rate limiting, and Prometheus metrics.
No business logic lives here.
"""
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.api import auth as auth_mod
from backend.api import metrics as metrics_mod
from backend.api.middleware import (
    LearningGateMiddleware, RateLimitMiddleware, RequestIDMiddleware,
    error_envelope,
)
from backend.api.readiness import is_ready, readiness_checks
from backend.api.routes import diagnostics, digital_twin, ingestion, learning, machine_knowledge, mode_b, products, query, schematics, verify, visualization, simulation
from backend.api.visual import layout_twin
from backend.config import settings
from backend.db.models import DigitalTwin
from backend.db.session import get_session, init_db
from backend.simulation.schema import DigitalTwinDefinition
from sqlalchemy.orm import Session
from backend.retrieval.hybrid import RetrievalUnavailableError
from backend.retrieval.ingestion_client import IngestionUnavailableError
from backend.retrieval.reranker import RerankerUnavailableError


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Crater.ai — Phases 1-7: Product Knowledge, Diagnostic Agent, Schematic Intelligence, Technical Visualization, Expert Knowledge, Functional Digital Twin",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS origins come from environment per deployment (gateway audit §4):
# no origin list is ever compiled into code again. Empty CORS_ORIGINS
# means no CORS middleware (same-origin / proxied only); see
# .env.example for the local-dev origin list.
if settings.cors_origins.strip():
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Middleware: added last runs first. RequestID is outermost so every
# 403/429 below still carries a request ID.
app.add_middleware(LearningGateMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestIDMiddleware)

# All public routes live under /v1/ (contracts §2.2). The unversioned
# include is the legacy alias for migration only. Exception: the verify
# router already carries its own /v1/verify prefix, so it is mounted
# once, without a second /v1.
_v1_routers = [products, ingestion, query, diagnostics, schematics,
               visualization, digital_twin, simulation, learning,
               mode_b, machine_knowledge]
for _router in _v1_routers:
    app.include_router(_router.router, prefix="/v1")
    app.include_router(_router.router)
app.include_router(verify.router)


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", None)
               or request.headers.get("X-Request-ID") or "unknown")


@app.get("/health")
def health():
    # Liveness only: 200 while the process is up, checks NO dependency,
    # so a downstream outage never causes a restart loop (contracts §1.4).
    return {"status": "ok"}


@app.get("/ready")
def ready():
    """Readiness: 200 only when DB, Qdrant, the reranker model, and the
    LLM-key configuration are all up (contracts §1.4).

    The reranker check moves to diagnosis-agent's /ready at the split;
    the LLM-key check is the gateway's own (a missing key is an explicit
    error, never a silent fallback).
    """
    checks = readiness_checks()
    ok = is_ready(checks)
    return JSONResponse(
        status_code=200 if ok else 503,
        content={"status": "ready" if ok else "not_ready", "checks": checks},
    )


@app.get("/metrics")
def metrics():
    return metrics_mod.metrics_response()


@app.get("/auth/verify")
def auth_verify(request: Request):
    """Traefik forwardAuth target (gateway audit §7; contracts §1.5).

    200 lets the request through, 401 blocks it. With
    GATEWAY_AUTH_ENABLED off (local dev default) this always answers
    200; enable it for any shared deployment.
    """
    if not auth_mod.auth_enabled():
        return {"status": "ok"}
    if auth_mod.validate_api_key(request.headers.get("X-Api-Key")):
        return {"status": "ok"}
    return JSONResponse(
        status_code=401,
        content=error_envelope(
            "unauthenticated", "A valid X-Api-Key header is required.",
            _request_id(request)),
    )


@app.get("/simulation/twins/{twin_id}/visual")
@app.get("/v1/simulation/twins/{twin_id}/visual")
def twin_visual(twin_id: str, db: Session = Depends(get_session)):
    """Render-ready layout of a compiled twin: components as nodes,
    canonical relations as edges.

    Phase 3 twin-verifier extraction: this endpoint MOVED here from
    ``backend/api/routes/simulation.py`` with ``backend/api/visual.py``
    (twin audit Recheck — visual.py is LIVE presentation and moves
    with its endpoint to gateway / frontend rendering). The twin
    service must not own presentation; the frontend
    (``VisualTwin.jsx`` via ``getTwinVisual``) still calls this path.
    """
    twin = db.get(DigitalTwin, twin_id)
    if not twin:
        raise HTTPException(404, "Digital twin not found")
    definition = DigitalTwinDefinition.model_validate(twin.definition)
    return {"twin_id": twin.id, "revision_id": twin.revision_id, "name": twin.name,
            "status": twin.status.value, "model_version": twin.model_version,
            **layout_twin(definition, twin.state or {})}


@app.api_route("/v1/expert/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
@app.api_route("/expert/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def expert_cut(request: Request, path: str):
    """Expert routes are CUT (amendment resolution 3; contracts §2.3).

    No /v1/expert/... route is mounted and no routes/expert.py exists
    in this tree; this handler exists only so the CUT is an explicit
    404 in the standard envelope instead of FastAPI's default shape.
    Expert *knowledge* (FailureMode rows + provenance) stays in
    diagnosis-agent as data; expert *capture* is what was cut.
    """
    return JSONResponse(
        status_code=404,
        content=error_envelope(
            "not_found",
            "Expert routes were removed; there is no /v1/expert/... API.",
            _request_id(request)),
    )


@app.exception_handler(RetrievalUnavailableError)
async def retrieval_unavailable_handler(request: Request, exc: RetrievalUnavailableError):
    """Qdrant / semantic retrieval down: HTTP 503 with a reason (P-06).

    Raised from hybrid_retrieve, which runs inside run_turn for both
    POST /diagnose/start and POST /diagnose/{id}/respond, and inside
    POST /query -- one handler covers all three. Never a silent
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
    §3.3 — diagnosis does not proceed on partial knowledge in silence).
    Raised from the knowledge / chunk / product lookups the diagnosis
    routes now make over HTTP."""
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
    """Unknown routes (incl. removed /v1/ paths) in the same envelope."""
    return JSONResponse(
        status_code=exc.status_code,
        content=error_envelope(
            _http_error_code(exc.status_code), str(exc.detail),
            _request_id(request)),
    )
