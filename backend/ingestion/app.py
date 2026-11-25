"""Ingestion service entrypoint.

Run with: uvicorn backend.ingestion.app:app --host 0.0.0.0 --port 8000
Worker with: celery -A backend.ingestion.celery_app:celery_app worker

Phase 3 ingestion extraction (contracts README §0/§10, step 2):
this app serves ONLY the ingestion-owned routes — jobs
(``routes/ingestion.py``), products + revisions registry
(``routes/products.py``), machine-knowledge reads
(``routes/machine_knowledge.py``), and the Mode B job surface
(``routes/mode_b.py``) — mounted under ``/v1/`` with the legacy
unversioned alias, exactly as the monolith mounts them today.
Diagnosis, twin and gateway routes are NOT mounted here; they live
in their own services. The route handlers themselves are unchanged:
the split moves the mount, not the logic.

/health is liveness only (200 while the process is up, checks
nothing). /ready gates on Postgres, Qdrant, Redis and Celery
(``backend/ingestion/readiness.py``) and fails 503 naming the
checks that failed — a downstream outage never causes a restart
loop, and never a silent degraded run.

Known coupling, not hidden: ``routes/mode_b.py`` imports
``start_session`` from the diagnosis agent for
``POST /mode-b/jobs/{id}/diagnose``. That in-process call becomes
an HTTP call to diagnosis-agent in the diagnosis-agent step
(contracts §2.2: Mode B diagnose is diagnosis-owned); until then
the ingestion image still builds the monolith tree, as the gateway
image does.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.api.middleware import RequestIDMiddleware, error_envelope
from backend.api.routes import ingestion, machine_knowledge, mode_b, products
from backend.db.session import init_db
from backend.ingestion.readiness import is_ready, readiness_checks


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Crater.ai — ingestion service",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(RequestIDMiddleware)

# Ingestion-owned routers only, under /v1/ plus the legacy alias
# during migration (same mount the monolith uses today).
_v1_routers = [products, ingestion, machine_knowledge, mode_b]
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
    """Readiness: 200 only when Postgres, Qdrant, Redis and Celery
    are all up (contracts §1.4). Otherwise 503 with a per-dependency
    breakdown naming which check failed."""
    checks = readiness_checks()
    ok = is_ready(checks)
    return JSONResponse(
        status_code=200 if ok else 503,
        content={"status": "ready" if ok else "not_ready", "checks": checks},
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
    """Unknown routes in the same envelope."""
    return JSONResponse(
        status_code=exc.status_code,
        content=error_envelope(
            _http_error_code(exc.status_code), str(exc.detail),
            _request_id(request)),
    )
