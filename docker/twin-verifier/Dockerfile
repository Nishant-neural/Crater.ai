# Twin-verifier container (contracts README §0/§10 step 4; twin audit).
# Serves ONLY the twin-owned routes via backend.simulation.app:app
# (simulation lifecycle: compile/approve/experiment/hypotheses/context,
# digital-twin reads/commands, async verify jobs POST /v1/verify -> 202
# + job) with /health (liveness) + /ready (Postgres + twin store +
# frozen archetype library 3c-1.0.0 + Redis + Celery). The twin is a
# deterministic state machine, not a physics solver: only transitions
# from explicit simulation_rules or approved library templates — no
# Modelica, z3, SimPy or CAD in this image. The oracle lives in
# harness-scorer, not here; the visual lives on the gateway shim.
# Like the gateway, ingestion and diagnosis-agent images, this still
# builds the monolith tree for now: the compile route still reads the
# canonical-model snapshot rows via the shared models module (the
# schema split is a separate step), and the DEFER-retained
# field_promotion / library_versions modules stay in this tree. The
# slim per-service dependency set lands with the frontend step.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_NO_CACHE=1

WORKDIR /app

# curl for the /health healthcheck (no tesseract: the twin never OCRs).
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
# Postgres + twin store (>= 1 approved twin) + frozen library + Redis
# + Celery.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# Serve. No migration step here: the ingestion container owns
# `alembic upgrade head` on start (contracts §8.2); this service only
# reads/writes the twin-owned tables Alembic already created.
CMD ["sh", "-c", "exec uv run uvicorn backend.simulation.app:app --host 0.0.0.0 --port 8000"]
