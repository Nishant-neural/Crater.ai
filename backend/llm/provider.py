"""The provider boundary used by agents and extraction services."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from backend.config import settings


LLMMessage = dict[str, Any]


class LLMProvider(Protocol):
    """Minimal contract shared by hosted and local model adapters."""

    def complete(self, messages: list[LLMMessage], max_tokens: int) -> str | None:
        """Return the model's text response, or None when unavailable."""


@dataclass
class AnthropicProvider:
    """Claude adapter kept behind the provider contract."""

    api_key: str
    model: str

    def complete(self, messages: list[LLMMessage], max_tokens: int) -> str | None:
        if not self.api_key:
            return None

        from anthropic import Anthropic

        response = Anthropic(api_key=self.api_key).messages.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=messages,
        )
        if not response.content:
            return None
        return getattr(response.content[0], "text", "").strip() or None


def get_llm_provider() -> LLMProvider:
    """Build the configured provider for the current application settings."""
    provider_name = settings.llm_provider.lower()
    if provider_name == "anthropic":
        return AnthropicProvider(
            api_key=settings.anthropic_api_key,
            model=settings.llm_model or settings.anthropic_model,
        )
    raise ValueError(f"Unsupported LLM provider: {settings.llm_provider}")