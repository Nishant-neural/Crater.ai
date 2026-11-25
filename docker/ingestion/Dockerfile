# Ingestion container (contracts README §0/§10 step 2; ingestion audit Step 5).
# Serves ONLY the ingestion-owned routes via backend.ingestion.app:app
# (jobs, products/revisions, machine-knowledge reads, Mode B jobs) with
# /health (liveness) + /ready (Postgres + Qdrant + Redis + Celery).
# The worker runs the same image with the command overridden in
# docker-compose.yml (celery worker). Like the gateway image, this still
# builds the monolith tree for now: routes/mode_b.py imports the
# diagnosis agent in-process, and that coupling is removed by the
# diagnosis-agent step, not by pretending it is gone here. The slim
# per-service dependency set lands with that step.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_NO_CACHE=1

WORKDIR /app

# tesseract for the ingestion OCR fallback (monolith dep, kept for now).
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv \
    && uv sync --frozen --no-dev --no-install-project

COPY backend ./backend
COPY scripts ./scripts
COPY alembic ./alembic
COPY alembic.ini ./
RUN uv sync --frozen --no-dev

EXPOSE 8000

# /health is liveness-only (no dependency checks); /ready gates on
# Postgres + Qdrant + Redis + Celery.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# Apply Alembic migrations, then serve. No SQLite->Postgres data
# migration exists or is wanted (contracts §8.2): the next ingestion
# round re-ingests from PDFs against Postgres.
CMD ["sh", "scripts/start_ingestion.sh"]
