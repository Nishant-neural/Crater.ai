"""Readiness checks behind the ingestion service's GET /ready
(contracts README §1.4: ingestion checks Postgres, Qdrant, Redis).

/health stays process-up. /ready is 200 only when every dependency
the service needs is healthy, otherwise 503 with a per-dependency
breakdown naming which check failed. No silent fallbacks: a dead
Qdrant, Redis, or Celery worker pool is a readiness failure, never
a degraded run.

Checks:
- postgres: SELECT 1 on the app engine.
- qdrant: the ``crater_chunks`` collection exists and answers.
  The client is constructed directly (``QdrantClient(url=...)``) —
  the same call ``vector_store.get_client`` makes — so this module
  does not pull in the embeddings import chain.
- redis: PING on the Celery broker (db 0) and the result backend
  (db 1).
- celery: at least one worker answers ``control.ping``. A broker
  that answers but has no worker is not ready: an upload would
  queue work nobody runs.
"""
from __future__ import annotations

import logging

from qdrant_client import QdrantClient
import redis as redis_lib
from sqlalchemy import text

from backend.config import settings
from backend.db.session import engine
from backend.ingestion.celery_app import broker_url, celery_app, result_backend_url

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


def check_redis() -> dict:
    try:
        for name, url in (("broker", broker_url), ("result backend", result_backend_url)):
            client = redis_lib.Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
            client.ping()
        return {"ready": True, "detail": "broker (db 0) and result backend (db 1) answer PING"}
    except Exception as exc:  # noqa: BLE001 - readiness reports, never raises
        return {"ready": False, "detail": f"redis unreachable: {exc}"}


def check_celery() -> dict:
    try:
        replies = celery_app.control.ping(timeout=2)
        if not replies:
            return {"ready": False, "detail": "no Celery workers responded to ping"}
        workers = sorted({next(iter(reply)) for reply in replies if reply})
        return {"ready": True, "detail": f"workers responding: {', '.join(workers)}"}
    except Exception as exc:  # noqa: BLE001 - readiness reports, never raises
        return {"ready": False, "detail": f"celery unreachable: {exc}"}


def readiness_checks() -> dict[str, dict]:
    return {
        "postgres": check_postgres(),
        "qdrant": check_qdrant(),
        "redis": check_redis(),
        "celery": check_celery(),
    }


def is_ready(checks: dict[str, dict] | None = None) -> bool:
    checks = checks if checks is not None else readiness_checks()
    return all(check.get("ready") for check in checks.values())
