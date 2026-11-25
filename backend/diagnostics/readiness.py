"""Readiness checks behind the diagnosis-agent service's GET /ready
(contracts README §1.4: diagnosis-agent checks Postgres, Qdrant,
CrossEncoder loaded, and — added by this extraction — the ingestion
API it reads knowledge and chunks from over HTTP).

/health stays process-up. /ready is 200 only when every dependency
the service needs is healthy, otherwise 503 with a per-dependency
breakdown naming which check failed. No silent fallbacks: a dead
Qdrant, an unloaded reranker, or an unreachable ingestion API is a
readiness failure, never a degraded run.

Checks:
- postgres: SELECT 1 on the app engine (diagnosis sessions +
  LangGraph checkpoints live here).
- qdrant: the ``crater_chunks`` collection exists and answers.
  Diagnosis is the sole search reader (ingestion is the sole writer).
- reranker: the CrossEncoder model has loaded (amendment resolution
  1). The first probe warms the model; later probes are fast.
- ingestion: GET {ingestion_base_url}/health answers 200. The
  ingestion service's own /health is liveness-only, so a 200 here
  proves the process answers; knowledge/chunk reads fail loudly
  per call when it does not.
"""
from __future__ import annotations

import logging

import httpx
from qdrant_client import QdrantClient
from sqlalchemy import text

from backend.config import settings
from backend.db.session import engine
from backend.retrieval import reranker as reranker_mod

logger = logging.getLogger(__name__)


def check_postgres() -> dict:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"ready": True, "detail": "SELECT 1 ok"}
    except Exception as exc:  # noqa: BLE001 - readiness reports, never raises
        return {"ready": False, "detail": f"postgres unreachable: {exc}"}


def check_qdrant() -> dict:
    try:
        client = QdrantClient(url=settings.qdrant_url)
        if not client.collection_exists(settings.qdrant_collection):
            return {
                "ready": False,
                "detail": f"collection {settings.qdrant_collection!r} does not exist",
            }
        client.get_collection(settings.qdrant_collection)
        return {
            "ready": True,
            "detail": f"collection {settings.qdrant_collection!r} reachable",
        }
    except Exception as exc:  # noqa: BLE001 - readiness reports, never raises
        return {"ready": False, "detail": f"qdrant unreachable: {exc}"}


def check_reranker() -> dict:
    try:
        reranker_mod.ensure_reranker_loaded()
    except reranker_mod.RerankerUnavailableError as exc:
        return {
            "ready": False,
            "detail": str(exc),
            "model": settings.reranker_model,
        }
    return {
        "ready": True,
        "detail": "model loaded",
        "model": settings.reranker_model,
    }


def check_ingestion() -> dict:
    try:
        with httpx.Client(timeout=2.0) as client:
            resp = client.get(
                f"{settings.ingestion_base_url.rstrip('/')}/health")
        if resp.status_code != 200:
            return {"ready": False,
                    "detail": f"ingestion /health returned HTTP {resp.status_code}"}
        return {"ready": True,
                "detail": f"ingestion reachable at {settings.ingestion_base_url}"}
    except Exception as exc:  # noqa: BLE001 - readiness reports, never raises
        return {"ready": False, "detail": f"ingestion unreachable: {exc}"}


def readiness_checks() -> dict[str, dict]:
    return {
        "postgres": check_postgres(),
        "qdrant": check_qdrant(),
        "reranker": check_reranker(),
        "ingestion": check_ingestion(),
    }


def is_ready(checks: dict[str, dict] | None = None) -> bool:
    checks = checks if checks is not None else readiness_checks()
    return all(check.get("ready") for check in checks.values())
