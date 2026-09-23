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


@dataclass
class GeminiProvider:
    """Google Gemini adapter kept behind the provider contract."""

    api_key: str
    model: str

    def complete(self, messages: list[LLMMessage], max_tokens: int) -> str | None:
        if not self.api_key:
            return None

        from google import genai
        from google.genai import types

        contents = [_gemini_content(message) for message in messages]
        response = genai.Client(api_key=self.api_key).models.generate_content(
            model=self.model,
            contents=contents,
            config=types.GenerateContentConfig(max_output_tokens=max_tokens),
        )
        return (response.text or "").strip() or None


def _gemini_content(message: LLMMessage) -> dict[str, Any]:
    """Translate the provider-neutral message shape to Gemini contents."""
    role = "model" if message.get("role") == "assistant" else "user"
    content = message.get("content", "")
    if isinstance(content, str):
        parts = [{"text": content}]
    else:
        parts = []
        for item in content:
            if item.get("type") == "text":
                parts.append({"text": item.get("text", "")})
            elif item.get("type") == "image":
                source = item.get("source", {})
                parts.append({
                    "inline_data": {
                        "mime_type": source.get("media_type", "application/octet-stream"),
                        "data": source.get("data", ""),
                    }
                })
    return {"role": role, "parts": parts}


def get_provider_for_model(provider_name: str, model: str) -> LLMProvider:
    provider_name = provider_name.lower()
    if provider_name == "anthropic":
        return AnthropicProvider(api_key=settings.anthropic_api_key, model=model)
    if provider_name == "gemini":
        return GeminiProvider(api_key=settings.gemini_api_key, model=model)
    raise ValueError(f"Unsupported LLM provider: {provider_name}")


def get_llm_provider() -> LLMProvider:
    """Backward-compatible default provider."""
    return get_provider_for_model(
        settings.llm_provider,
        settings.llm_model or (settings.gemini_model if settings.llm_provider.lower() == "gemini" else settings.anthropic_model),
    )
