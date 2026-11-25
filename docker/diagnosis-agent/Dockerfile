# Diagnosis-agent container (contracts README §0/§10 step 3; diagnosis audit).
# Serves ONLY the diagnosis-owned routes via backend.diagnostics.app:app
# (diagnose sessions/turns, POST /v1/query, learning routes mounted but
# gated 403 learning_deferred) with /health (liveness) + /ready
# (Postgres + Qdrant + CrossEncoder + ingestion API). Retrieval search /
# hybrid / rerank / knowledge lookup live here and only here; indexing
# and embedding stay in ingestion. Verification goes through the thin
# async TwinVerifyClient (POST /v1/verify -> 202 + job, poll); until
# the twin-verifier service is split, TWIN_VERIFIER_BASE_URL is empty
# and the client runs the same job contract against the turn's DB
# session in-process (TEMPORARY — see backend/diagnostics/agent.py).
# Like the gateway and ingestion images, this still builds the monolith
# tree for now: respond()'s early tournament and upstream walk still
# run in-process simulation code, and the learning/fleet/downtime/pm
# modules are DEFER-retained in this tree. The slim per-service
# dependency set lands with the twin-verifier step.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_NO_CACHE=1

WORKDIR /app

# curl for the /health healthcheck (no tesseract: diagnosis never OCRs).
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv \
    && uv sync --frozen --no-dev --no-install-project

COPY backend ./backend
COPY alembic ./alembic
COPY alembic.ini ./
RUN uv sync --frozen --no-dev

EXPOSE 8000

# /health is liveness-only (no dependency checks); /ready gates on
# Postgres + Qdrant + CrossEncoder + the ingestion API.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# Serve. No migration step here: the ingestion container owns
# `alembic upgrade head` on start (contracts §8.2); this service only
# reads/writes the diagnosis-owned tables Alembic already created.
CMD ["sh", "-c", "exec uv run uvicorn backend.diagnostics.app:app --host 0.0.0.0 --port 8000"]
