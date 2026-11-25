"""Twin-verifier service entrypoint.

Run with: uvicorn backend.simulation.app:app --host 0.0.0.0 --port 8000
Worker with: celery -A backend.simulation.celery_app:celery_app worker

Phase 3 twin-verifier extraction (contracts README §0/§10, step 4):
this app serves ONLY the twin-owned routes — the simulation lifecycle
(``routes/simulation.py``: compile, approve, experiment, hypotheses,
context, revision-twin), the digital-twin reads/commands
(``routes/digital_twin.py``), and the async verify jobs
(``routes/verify.py``: POST /v1/verify -> 202 + job, GET
/v1/verify/jobs/{job_id}) — mounted under ``/v1/`` with the legacy
unversioned alias, exactly as the monolith mounts them today.
Diagnosis, ingestion, gateway and Mode B routes are NOT mounted here;
they live in their own services. The route handlers themselves are
unchanged except for the verify job store (Postgres ``verify_jobs``
instead of the in-memory ``_JOBS`` dict) and the empty-assertions 400
guard on experiments.

/health is liveness only (200 while the process is up, checks
nothing). /ready gates on Postgres, the twin store (>= 1 approved
twin — zero approved twins is a readiness failure, never a silent
fallback), the frozen archetype library (``3c-1.0.0``), Redis and
Celery (``backend/simulation/readiness.py``) and fails 503 naming the
checks that failed — a downstream outage never causes a restart loop,
and never a silent degraded run.

The twin visual is NOT served here: ``visual.py`` moved with its
endpoint to the gateway shim (``backend/api/visual.py`` +
``backend/api/main.py``) — the twin service must not own
presentation. The oracle is NOT here either: it moved to
harness-scorer (``benchmark/oracle.py``).
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.api.middleware import RequestIDMiddleware, error_envelope
from backend.api.routes import digital_twin, simulation, verify
from backend.db.session import init_db
from backend.simulation.readiness import is_ready, readiness_checks


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Crater.ai — twin-verifier service",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(RequestIDMiddleware)

# Twin-owned routers only, under /v1/ plus the legacy alias during
# migration (same mount the monolith uses today). Exception: the verify
# router already carries its own /v1/verify prefix, so it is mounted
# once, without a second /v1.
_v1_routers = [digital_twin, simulation]
for _router in _v1_routers:
    app.include_router(_router.router, prefix="/v1")
    app.include_router(_router.router)
app.include_router(verify.router)


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
    """Readiness: 200 only when Postgres, the twin store (>= 1
    approved twin), the frozen archetype library, Redis and Celery
    are all up (contracts §1.4, §5.5). Otherwise 503 with a
    per-dependency breakdown naming which check failed."""
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
