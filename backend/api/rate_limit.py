"""Gateway rate limiting: Redis token bucket (contracts README §1.5).

Interim guardrails until Nishant sets a Phase 5 load target, per API key:

- diagnosis: 10 req/min sustained, 20 burst
- reads: 60 req/min
- ingestion uploads: 5 uploads/hour

429 + Retry-After on exceed. This replaces the deleted process-local
Gemini limiter (backend/llm/model_router.py): the bucket is shared
across gateway replicas when REDIS_URL is set. Redis holds no durable
state — only buckets — so a missing Redis falls back to a process-local
bucket (single replica) with a warning, never a startup failure.
"""
from __future__ import annotations

import logging
import math
import threading
import time

from backend.config import settings

logger = logging.getLogger(__name__)

# (capacity, refill tokens per second)
LIMITS = {
    "diagnosis": (20.0, 10.0 / 60.0),
    "reads": (60.0, 60.0 / 60.0),
    "uploads": (5.0, 5.0 / 3600.0),
}

_LUA_CONSUME = """
local tokens = tonumber(redis.call('HGET', KEYS[1], 'tokens'))
local updated = tonumber(redis.call('HGET', KEYS[1], 'updated'))
local now = tonumber(ARGV[1])
if tokens == nil then tokens = tonumber(ARGV[2]) end
if updated == nil then updated = now end
tokens = math.min(tonumber(ARGV[2]), tokens + (now - updated) * tonumber(ARGV[3]))
if tokens >= 1 then
  redis.call('HSET', KEYS[1], 'tokens', tokens - 1, 'updated', now)
  redis.call('EXPIRE', KEYS[1], 3600)
  return 0
end
redis.call('HSET', KEYS[1], 'tokens', tokens, 'updated', now)
redis.call('EXPIRE', KEYS[1], 3600)
return math.ceil((1 - tokens) / tonumber(ARGV[3]))
"""


class _MemoryBucket:
    """Process-local token bucket (fallback when Redis is absent)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, tuple[float, float]] = {}

    def allow(self, key: str, kind: str) -> tuple[bool, int]:
        capacity, rate = LIMITS[kind]
        now = time.monotonic()
        with self._lock:
            tokens, updated = self._state.get(key, (capacity, now))
            tokens = min(capacity, tokens + (now - updated) * rate)
            if tokens >= 1:
                self._state[key] = (tokens - 1, now)
                return True, 0
            self._state[key] = (tokens, now)
            return False, max(1, math.ceil((1 - tokens) / rate))


class RateLimiter:
    """Token bucket shared across replicas via Redis when configured."""

    def __init__(self) -> None:
        self._memory = _MemoryBucket()
        self._redis = None
        if settings.redis_url:
            try:
                import redis as redis_lib

                self._redis = redis_lib.Redis.from_url(
                    settings.redis_url, socket_connect_timeout=2,
                    socket_timeout=2, decode_responses=True,
                )
                self._redis.ping()
            except Exception as exc:  # noqa: BLE001 - Redis is optional here
                logger.warning("gateway rate limit: Redis unreachable (%s); "
                               "using process-local bucket", exc)
                self._redis = None
        elif not getattr(self, "_warned", False):
            logger.warning("gateway rate limit: REDIS_URL unset; using "
                           "process-local bucket (single replica only)")
            self._warned = True

    def allow(self, key: str, kind: str) -> tuple[bool, int]:
        """(allowed, retry_after_seconds). retry_after is 0 when allowed."""
        if self._redis is None:
            return self._memory.allow(key, kind)
        capacity, rate = LIMITS[kind]
        try:
            retry_after = self._redis.eval(
                _LUA_CONSUME, 1, f"ratelimit:{kind}:{key}",
                time.time(), capacity, rate,
            )
            return (int(retry_after) == 0), max(0, int(retry_after))
        except Exception as exc:  # noqa: BLE001 - never fail a request on Redis
            logger.warning("gateway rate limit: Redis error (%s); "
                           "falling back to process-local bucket", exc)
            self._redis = None
            return self._memory.allow(key, kind)


def classify(path: str, method: str) -> str:
    """Which bucket a request draws from (contracts §1.5)."""
    if "/diagnose" in path:
        return "diagnosis"
    if method == "POST" and "/ingest" in path:
        return "uploads"
    return "reads"


limiter = RateLimiter()
