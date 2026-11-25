"""Task-aware LLM model router (renamed from llm/gateway.py, gateway audit
Step 1: it routes between LLM models, it is not the HTTP gateway).

Keeps model selection out of business logic. Routing is deterministic by task;
adaptive routing can be added later without changing callers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
        # Last-resort model default is per-provider: without this an
        # openrouter route with no model configured silently asked for a
        # Claude model id and got a 404 (fix5).
        if provider == "gemini":
            default_model = settings.gemini_model
        elif provider == "openrouter":
            default_model = settings.openrouter_model
        else:
            default_model = settings.anthropic_model
        model = str(cfg.get("model") or settings.llm_model or default_model)
        max_tokens = int(cfg.get("max_tokens") or settings.task_max_tokens.get(task, 2000))
        return ModelRoute(task, provider, model, max_tokens)

    def provider(self, task: str) -> tuple[LLMProvider, ModelRoute]:
        route = self.route(task)
        return get_provider_for_model(route.provider, route.model), route

    def complete(self, task: str, messages: list[dict[str, Any]], max_tokens: int | None = None) -> str | None:
        provider, route = self.provider(task)
        return provider.complete(messages=messages, max_tokens=max_tokens or route.max_tokens)

    def complete_result(
        self,
        task: str,
        messages: list[dict[str, Any]],
        max_tokens: int | None = None,
        json_schema: dict[str, Any] | None = None,
        temperature: float | None = None,
    ) -> tuple[str | None, bool]:
        """Structured-output call: returns (text, truncated). max_tokens
        defaults to the task route's ceiling, so callers that pass nothing
        get the configured ceiling instead of a per-call cap. Other tasks
        keep using complete() and are unaffected."""
        provider, route = self.provider(task)
        return provider.complete_result(
            messages=messages,
            max_tokens=max_tokens or route.max_tokens,
            json_schema=json_schema,
            temperature=temperature,
        )


# NOTE (gateway audit §4): the process-local _GeminiExtractionRateLimiter
# lived here and is deleted. Process-local limiting cannot coordinate
# across replicas; inbound limiting is now the gateway's Redis token
# bucket (backend/api/rate_limit.py) and provider retries stay in
# backend/llm/provider.py. settings.extraction_gemini_rpm is retained
# in config only as documentation of the old ceiling; nothing reads it.
gateway = ModelGateway()
