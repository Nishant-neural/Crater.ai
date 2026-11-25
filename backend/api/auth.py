"""API-key auth helper: the gateway shim as Traefik's forwardAuth target
(gateway audit §2/§7; contracts README §1.5).

Keys are validated here; Traefik rejects unauthenticated requests before
they reach any service. OIDC / Keycloak is DEFER (contracts §1.5).

Bootstrap order until the api_keys Postgres table lands with the
Postgres move (contracts §8.2): the GATEWAY_API_KEYS env list, then a
`api_keys` table if one exists. Auth is OFF by default (local dev):
with GATEWAY_AUTH_ENABLED unset the forwardAuth target answers 200
and logs one loud warning at startup. A request with no key while auth
is on is a 401 in the standard error envelope — never a silent pass.
"""
from __future__ import annotations

import logging

from sqlalchemy import text

from backend.config import settings
from backend.db.session import engine

logger = logging.getLogger(__name__)

_warned = False


def _env_keys() -> set[str]:
    return {k.strip() for k in settings.gateway_api_keys.split(",") if k.strip()}


def _key_in_postgres(key: str) -> bool:
    """True if the key is in an api_keys table; False when the table
    does not exist yet (pre-Postgres-move) or the key is not there."""
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT 1 FROM api_keys WHERE key = :key LIMIT 1"),
                {"key": key},
            ).first()
        return row is not None
    except Exception:  # noqa: BLE001 - no table yet is a normal state
        return False


def validate_api_key(key: str | None) -> bool:
    if not key:
        return False
    if key in _env_keys():
        return True
    return _key_in_postgres(key)


def auth_enabled() -> bool:
    global _warned
    enabled = bool(settings.gateway_auth_enabled)
    if not enabled and not _warned:
        logger.warning("gateway auth: GATEWAY_AUTH_ENABLED is off; the "
                       "forwardAuth target allows every request (local "
                       "dev only — enable it for any shared deployment)")
        _warned = True
    return enabled
