"""Task-aware LLM gateway.

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
        model = str(cfg.get("model") or settings.llm_model or (settings.gemini_model if provider == "gemini" else settings.anthropic_model))
        max_tokens = int(cfg.get("max_tokens") or settings.task_max_tokens.get(task, 2000))
        return ModelRoute(task, provider, model, max_tokens)

    def provider(self, task: str) -> tuple[LLMProvider, ModelRoute]:
        route = self.route(task)
        return get_provider_for_model(route.provider, route.model), route

    def complete(self, task: str, messages: list[dict[str, Any]], max_tokens: int | None = None) -> str | None:
        provider, route = self.provider(task)
        return provider.complete(messages=messages, max_tokens=max_tokens or route.max_tokens)

gateway = ModelGateway()
