"""The provider boundary used by agents and extraction services."""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Protocol

from backend.config import settings


LLMMessage = dict[str, Any]


class LLMProvider(Protocol):
    """Minimal contract shared by hosted and local model adapters."""

    def complete(self, messages: list[LLMMessage], max_tokens: int) -> str | None:
        """Return the model's text response, or None when unavailable."""

    def complete_result(
        self,
        messages: list[LLMMessage],
        max_tokens: int,
        json_schema: dict[str, Any] | None = None,
        temperature: float | None = None,
    ) -> tuple[str | None, bool]:
        """Return (text, truncated). With json_schema the provider must
        return structured JSON (not free text); truncated is True when the
        provider stopped because the output cap was hit."""


@dataclass
class AnthropicProvider:
    """Claude adapter kept behind the provider contract."""

    api_key: str
    model: str

    def complete(self, messages: list[LLMMessage], max_tokens: int) -> str | None:
        text, _truncated = self.complete_result(messages, max_tokens)
        return text

    def complete_result(
        self,
        messages: list[LLMMessage],
        max_tokens: int,
        json_schema: dict[str, Any] | None = None,
        temperature: float | None = None,
    ) -> tuple[str | None, bool]:
        """With a JSON schema, force a tool call whose input IS the JSON
        object (Claude has no response-format flag on messages.create, so
        forced tool use is the structured-output mechanism); without one,
        this is the plain text reply exactly as before."""
        if not self.api_key:
            return None, False

        from anthropic import Anthropic

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if json_schema is not None:
            kwargs["tools"] = [{
                "name": "diagnostic_turn",
                "description": "Return the diagnostic turn as structured JSON.",
                "input_schema": json_schema,
            }]
            kwargs["tool_choice"] = {"type": "tool", "name": "diagnostic_turn"}
        response = Anthropic(api_key=self.api_key).messages.create(**kwargs)
        truncated = getattr(response, "stop_reason", None) == "max_tokens"
        if not response.content:
            return None, truncated
        for block in response.content:
            if getattr(block, "type", None) == "tool_use":
                return json.dumps(getattr(block, "input", {}), ensure_ascii=False), truncated
        return getattr(response.content[0], "text", "").strip() or None, truncated


@dataclass
class GeminiProvider:
    """Google Gemini adapter kept behind the provider contract."""

    api_key: str
    model: str

    def complete(self, messages: list[LLMMessage], max_tokens: int) -> str | None:
        text, _truncated = self.complete_result(messages, max_tokens)
        return text

    def complete_result(
        self,
        messages: list[LLMMessage],
        max_tokens: int,
        json_schema: dict[str, Any] | None = None,
        temperature: float | None = None,
    ) -> tuple[str | None, bool]:
        """With a JSON schema, set response_mime_type + response_schema so
        the model can only return JSON; without one, the plain text reply
        exactly as before. Truncation is read off the first candidate's
        finish reason."""
        if not self.api_key:
            return None, False

        from google import genai
        from google.genai import types

        contents = [_gemini_content(message) for message in messages]
        config_kwargs: dict[str, Any] = {"max_output_tokens": max_tokens}
        if temperature is not None:
            config_kwargs["temperature"] = temperature
        if json_schema is not None:
            config_kwargs["response_mime_type"] = "application/json"
            config_kwargs["response_schema"] = json_schema
        with genai.Client(api_key=self.api_key) as client:
            response = client.models.generate_content(
                model=self.model,
                contents=contents,
                config=types.GenerateContentConfig(**config_kwargs),
            )
        truncated = False
        try:
            candidate = response.candidates[0]
            truncated = "MAX_TOKENS" in str(getattr(candidate, "finish_reason", "") or "")
        except (AttributeError, IndexError, TypeError):
            truncated = False
        return (response.text or "").strip() or None, truncated


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
