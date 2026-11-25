"""Readiness checks behind GET /ready (Phase 1 Step 2; infra audit).

/health stays process-up. /ready aggregates the dependencies a
diagnosis turn actually needs, and fails (HTTP 503) while any of them
is down:

- database: Postgres in production, SQLite here -- SELECT 1.
- qdrant: the collection exists and answers (infra: collection_exists
  plus get_collection). A dead Qdrant is a readiness failure, not a
  silent BM25-only run (P-06).
- reranker: the CrossEncoder model has loaded (amendment resolution 1).
  The first probe warms the model -- it downloads on first load if the
  cache is empty, so the first /ready after a deploy can take a while;
  later probes are fast (load state is cached per process).

A check that fails returns {"ready": False, "detail": <reason>} so the
caller sees why, instead of a bare red/green.
"""
from __future__ import annotations

import logging

from sqlalchemy import text

from backend.config import settings
from backend.db.session import engine
from backend.retrieval import reranker as reranker_mod

logger = logging.getLogger(__name__)


def check_database() -> dict:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"ready": True, "detail": "SELECT 1 ok"}
    except Exception as exc:  # noqa: BLE001 - readiness reports, never raises
        return {"ready": False, "detail": f"database unreachable: {exc}"}


def check_qdrant() -> dict:
    try:
        from backend.retrieval.vector_store import get_client  # lazy: heavy client import

        client = get_client()
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


def check_llm_keys() -> dict:
    """Gateway /ready check (contracts README §1.4; gateway audit §3):
    an LLM key must be configured for the provider the diagnosis route
    will use, otherwise the failure is reported here instead of as a
    silent (None, False) at the first diagnosis call. No key value is
    ever returned, only which provider is missing one."""
    provider = str(settings.model_routes.get("diagnosis", {}).get("provider")
                   or settings.llm_provider).lower()
    key_attr = {
        "anthropic": "anthropic_api_key",
        "gemini": "gemini_api_key",
        "openrouter": "openrouter_api_key",
    }.get(provider)
    if key_attr is None:
        return {"ready": False,
                "detail": f"unknown LLM provider {provider!r} for diagnosis"}
    if not getattr(settings, key_attr, ""):
        return {"ready": False,
                "detail": f"no API key configured for LLM provider {provider!r}"}
    return {"ready": True,
            "detail": f"API key configured for LLM provider {provider!r}"}


def readiness_checks() -> dict[str, dict]:
    return {
        "database": check_database(),
        "qdrant": check_qdrant(),
        "reranker": check_reranker(),
        "llm_keys": check_llm_keys(),
    }


def is_ready(checks: dict[str, dict] | None = None) -> bool:
    checks = checks if checks is not None else readiness_checks()
    return all(check.get("ready") for check in checks.values())
