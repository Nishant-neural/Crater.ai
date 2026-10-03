"""Task-aware LLM gateway.

Keeps model selection out of business logic. Routing is deterministic by task;
adaptive routing can be added later without changing callers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import threading
import time

from backend.config import settings
from backend.llm.provider import LLMProvider, get_provider_for_model

@dataclass(frozen=True)
class ModelRoute:
    task: str
    provider: str
    model: str
    max_tokens: int

class ModelGateway:
    def route(self, task: str) -> ModelRoute:
        cfg = settings.model_routes.get(task, {}) if isinstance(settings.model_routes, dict) else {}
        provider = str(cfg.get("provider") or settings.llm_provider).lower()
        model = str(cfg.get("model") or settings.llm_model or (settings.gemini_model if provider == "gemini" else settings.anthropic_model))
        max_tokens = int(cfg.get("max_tokens") or settings.task_max_tokens.get(task, 2000))
        return ModelRoute(task, provider, model, max_tokens)

    def provider(self, task: str) -> tuple[LLMProvider, ModelRoute]:
        route = self.route(task)
        return get_provider_for_model(route.provider, route.model), route

    def complete(self, task: str, messages: list[dict[str, Any]], max_tokens: int | None = None) -> str | None:
        provider, route = self.provider(task)
        if task == "extraction" and route.provider == "gemini":
            _gemini_extraction_limiter.wait()
        return provider.complete(messages=messages, max_tokens=max_tokens or route.max_tokens)


class _GeminiExtractionRateLimiter:
    """Process-local limiter for Gemini extraction calls.

    The project currently targets a 15 RPM Gemini limit. We deliberately run
    below that ceiling so retries/transient timing do not immediately cause
    another 429. This is intentionally scoped to extraction; interactive
    diagnosis and other model routes keep their own throughput.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_call = 0.0

    def wait(self) -> None:
        rpm = max(1, int(settings.extraction_gemini_rpm))
        interval = 60.0 / rpm
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._last_call + interval - now)
            if delay:
                time.sleep(delay)
            self._last_call = time.monotonic()


_gemini_extraction_limiter = _GeminiExtractionRateLimiter()



gateway = ModelGateway()
