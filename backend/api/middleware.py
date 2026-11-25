"""Gateway shim middleware (gateway audit Step 1, ~120-line budget).

- X-Request-ID: generated when absent, propagated on the request and
  echoed on the response, and carried in every log line via
  request.state.request_id. A user-reported failure must be findable
  in every service it touched (contracts §1.2).
- Uniform error envelope (contracts §1.3): every failure is
  {"error": {"code", "message", "request_id", "upstream"}}.
- Learning gate (contracts §2.4): /v1/learning/... (and the legacy
  /learning/... alias) returns 403 learning_deferred in production v1
  until the pilot gate flips (CRATER_LEARNING_ENABLED / the
  learning_enabled setting, flipped only after >=20 pilot outcomes).
- Rate limiting (contracts §1.5): Redis token bucket via
  backend/api/rate_limit.py; 429 + Retry-After on exceed.

Middleware runs outermost-first in the order added in main.py; the
request-ID middleware is added last so it wraps everything else and
every 403/429 it produces still carries a request ID.
"""
from __future__ import annotations

import json
import logging
import time
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from backend.api import metrics as metrics_mod
from backend.api.rate_limit import classify, limiter
from backend.config import settings

logger = logging.getLogger(__name__)


def error_envelope(code: str, message: str, request_id: str,
                   upstream: str | None = None) -> dict:
    error: dict[str, str] = {
        "code": code,
        "message": message,
        "request_id": request_id,
    }
    if upstream:
        error["upstream"] = upstream
    return {"error": error}


def _request_id(request: Request) -> str:
    return str(request.headers.get("X-Request-ID") or uuid.uuid4())


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Generate/propagate X-Request-ID; structured JSON log per request."""

    async def dispatch(self, request: Request, call_next):
        request_id = _request_id(request)
        request.state.request_id = request_id
        started = time.monotonic()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        route = request.scope.get("route")
        route_path = getattr(route, "path", request.url.path)
        metrics_mod.observe(route_path, request.method,
                            response.status_code,
                            time.monotonic() - started)
        logger.info(json.dumps({
            "request_id": request_id, "method": request.method,
            "path": request.url.path, "status": response.status_code,
        }))
        return response


class LearningGateMiddleware(BaseHTTPMiddleware):
    """403 learning_deferred while the pilot gate is closed."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if (path == "/learning" or path.startswith("/learning/")
                or path == "/v1/learning"
                or path.startswith("/v1/learning/")):
            if not settings.learning_enabled:
                request_id = getattr(request.state, "request_id", None) \
                    or _request_id(request)
                return JSONResponse(
                    status_code=403,
                    content=error_envelope(
                        "learning_deferred",
                        "Learning is deferred until the pilot produces "
                        "field outcomes.",
                        request_id,
                    ),
                )
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-API-key token bucket; 429 + Retry-After on exceed."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path in ("/health", "/ready", "/metrics",
                                "/auth/verify"):
            return await call_next(request)
        key = request.headers.get("X-Api-Key") or (
            request.client.host if request.client else "unknown"
        )
        kind = classify(request.url.path, request.method)
        allowed, retry_after = limiter.allow(f"{kind}:{key}", kind)
        if not allowed:
            request_id = getattr(request.state, "request_id", None) \
                or _request_id(request)
            return JSONResponse(
                status_code=429,
                content=error_envelope(
                    "rate_limited",
                    f"Rate limit exceeded for {kind}; retry after "
                    f"{retry_after} seconds.",
                    request_id,
                ),
                headers={"Retry-After": str(retry_after)},
            )
        return await call_next(request)
