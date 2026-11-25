"""Readiness checks behind the twin-verifier service's GET /ready
(contracts README §1.4, §5.5: twin-verifier checks Postgres and the
twin store; §5.2 adds the Celery queue the verify jobs run on).

/health stays process-up. /ready is 200 only when every dependency
the service needs is healthy, otherwise 503 with a per-dependency
breakdown naming which check failed. No silent fallbacks: a missing
database, zero approved twins, an unfrozen library, or a dead queue
is a readiness failure, never a degraded run.

Checks:
- postgres: SELECT 1 on the app engine.
- twin_store: at least one APPROVED (active) twin exists. Zero
  approved twins is a readiness failure, never a silent fallback
  (contracts §5.5 / audit p.11): a verify job with no twin would
  answer every hypothesis NOT_VERIFIED, which is a loud verdict, not
  a ready service. ``session_factory`` is injectable for tests; the
  production default is the app ``SessionLocal``.
- library: the archetype library imports and its version is the
  frozen ``3c-1.0.0`` (``ARCHETYPE_LIBRARY_VERSION`` in
  ``backend/simulation/archetypes.py``). Any other version means the
  frozen library moved under the service, and the service is not
  ready to stamp verdicts with it.
- redis: PING on the Celery broker (db 0) and the result backend
  (db 1).
- celery: at least one worker answers ``control.ping``. A broker
  that answers but has no worker is not ready: a verify job would
  queue work nobody runs.
"""
from __future__ import annotations

import logging

import redis as redis_lib
from sqlalchemy import text

from backend.db.models import DigitalTwin, TwinStatus
from backend.db.session import SessionLocal, engine
from backend.simulation.celery_app import broker_url, celery_app, result_backend_url
from backend.simulation.library_versions import CURRENT_LIBRARY_VERSION

logger = logging.getLogger(__name__)

FROZEN_LIBRARY_VERSION = "3c-1.0.0"


def check_postgres() -> dict:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"ready": True, "detail": "SELECT 1 ok"}
    except Exception as exc:  # noqa: BLE001 - readiness reports, never raises
        return {"ready": False, "detail": f"postgres unreachable: {exc}"}


def check_twin_store(session_factory=None) -> dict:
    try:
        factory = session_factory or SessionLocal
        session = factory()
        try:
            count = (session.query(DigitalTwin)
                     .filter_by(status=TwinStatus.active).count())
        finally:
            session.close()
        if count < 1:
            return {
                "ready": False,
                "detail": "no approved twin in the twin store yet; "
                          "compile and approve one first",
            }
        return {"ready": True, "detail": f"{count} approved twin(s)"}
    except Exception as exc:  # noqa: BLE001 - readiness reports, never raises
        return {"ready": False, "detail": f"twin store unreachable: {exc}"}


def check_library() -> dict:
    try:
        from backend.simulation.archetypes import ARCHETYPE_LIBRARY_VERSION
    except Exception as exc:  # noqa: BLE001 - readiness reports, never raises
        return {"ready": False, "detail": f"archetype library not loadable: {exc}"}
    if ARCHETYPE_LIBRARY_VERSION != FROZEN_LIBRARY_VERSION:
        return {
            "ready": False,
            "detail": f"archetype library version {ARCHETYPE_LIBRARY_VERSION!r} "
                      f"is not the frozen {FROZEN_LIBRARY_VERSION!r}",
            "version": ARCHETYPE_LIBRARY_VERSION,
        }
    if CURRENT_LIBRARY_VERSION != FROZEN_LIBRARY_VERSION:
        return {
            "ready": False,
            "detail": f"library pin {CURRENT_LIBRARY_VERSION!r} "
                      f"is not the frozen {FROZEN_LIBRARY_VERSION!r}",
            "version": CURRENT_LIBRARY_VERSION,
        }
    return {"ready": True, "detail": f"archetype library frozen at {FROZEN_LIBRARY_VERSION}",
            "version": FROZEN_LIBRARY_VERSION}


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
        "twin_store": check_twin_store(),
        "library": check_library(),
        "redis": check_redis(),
        "celery": check_celery(),
    }


def is_ready(checks: dict[str, dict] | None = None) -> bool:
    checks = checks if checks is not None else readiness_checks()
    return all(check.get("ready") for check in checks.values())
