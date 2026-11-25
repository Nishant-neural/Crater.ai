# Gateway container (gateway audit §2/§4; contracts README §2).
# Step 1: the shim still lives inside the monolith (backend/api/main.py),
# so this image builds the monolith. The slim per-service dependency
# set (fastapi, uvicorn, httpx, pydantic-settings, prometheus-client,
# redis only — audit §3) lands when the shim moves out to its own
# service; until then the image carries the monolith's tree.
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
COPY benchmark ./benchmark
COPY frontend ./frontend
RUN uv sync --frozen --no-dev

EXPOSE 8000

# /health is liveness-only (no dependency checks); /ready gates on
# Postgres + Qdrant + reranker + LLM-key configuration.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["uv", "run", "uvicorn", "backend.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
