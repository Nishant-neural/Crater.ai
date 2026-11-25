"""The provider boundary used by agents and extraction services."""
from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import Any, Protocol

import httpx

from backend.config import settings


LLMMessage = dict[str, Any]


# --- Shared retry for transient provider overload (fix5, 21 Nov 2026) ---
# 429/503 only, up to 3 attempts: exponential backoff 1s then 2s,
# honouring a Retry-After header when present (capped at 120s so a
# pathological header cannot stall an ingestion job).

_RETRYABLE_STATUS = (429, 503)
_MAX_ATTEMPTS = 3
_RETRY_AFTER_CAP_SECONDS = 120.0

# Gateway audit §3: every provider call carries an explicit timeout.
# 180s for LLM providers (inter-service HTTP is 10s, contracts §1.2).
LLM_PROVIDER_TIMEOUT_SECONDS = 180.0


class LLMKeyMissingError(RuntimeError):
    """No API key is configured for the requested LLM provider.

    Gateway audit §3: a missing key is an explicit error, never a silent
    (None, False). Callers already treat a raised error as a loud,
    logged failure (extraction raises ExtractionError, diagnosis
    degrades the turn); /ready reports it via the llm_keys check
    (backend/api/readiness.py) so a misconfigured deploy is visible
    before any diagnosis is attempted.
    """

    def __init__(self, provider_name: str):
        super().__init__(
            f"No API key configured for LLM provider {provider_name!r}: "
            "set the provider's API key environment variable"
        )
        self.provider_name = provider_name


def _retry_after_seconds(value: Any) -> float | None:
    """Parse a Retry-After header value (seconds only); None if absent/bad."""
    if value is None:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if seconds < 0:
        return None
    return min(seconds, _RETRY_AFTER_CAP_SECONDS)


def _sleep_before_retry(attempt: int, retry_after: float | None) -> None:
    """Wait before the next attempt: Retry-After wins, else 1s, 2s, ..."""
    if retry_after is not None:
        time.sleep(retry_after)
        return
    time.sleep(min(30.0, 2.0 ** attempt))


def _status_of_error(exc: Exception) -> int | None:
    """Best-effort HTTP status for an exception: .code / .status_code
    first (google-genai errors carry .code), else a 429/503 token in the
    message — the codebase already matches Gemini errors by message
    tokens (backend/knowledge/component_extraction.py)."""
    for attr in ("code", "status_code"):
        code = getattr(exc, attr, None)
        if isinstance(code, int):
            return code
    text = str(exc)
    for status in _RETRYABLE_STATUS:
        if str(status) in text:
            return status
    return None


def _retry_after_of_error(exc: Exception) -> float | None:
    """Best-effort Retry-After for an exception: httpx response headers,
    or a google-genai error carrying a response object."""
    for source in (getattr(exc, "headers", None),
                   getattr(getattr(exc, "response", None), "headers", None)):
        if source is not None:
            try:
                return _retry_after_seconds(source.get("Retry-After"))
            except Exception:
                return None
    return None


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
            raise LLMKeyMissingError("anthropic")

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
            raise LLMKeyMissingError("gemini")

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
            # Retry transient overload (429/503) instead of failing the
            # whole extraction/diagnosis call on the first 503: the client
            # stays open across attempts, exactly as before.
            for attempt in range(_MAX_ATTEMPTS):
                try:
                    response = client.models.generate_content(
                        model=self.model,
                        contents=contents,
                        config=types.GenerateContentConfig(**config_kwargs),
                    )
                    break
                except Exception as exc:
                    if (
                        _status_of_error(exc) not in _RETRYABLE_STATUS
                        or attempt == _MAX_ATTEMPTS - 1
                    ):
                        raise
                    _sleep_before_retry(attempt, _retry_after_of_error(exc))
        truncated = False
        try:
            candidate = response.candidates[0]
            truncated = "MAX_TOKENS" in str(getattr(candidate, "finish_reason", "") or "")
        except (AttributeError, IndexError, TypeError):
            truncated = False
        return (response.text or "").strip() or None, truncated


@dataclass
class OpenRouterProvider:
    """OpenAI-compatible chat-completions adapter (OpenRouter by default).

    Generic over base_url: pointing it at another OpenAI-compatible
    endpoint (Groq, Cerebras, ...) is a config change, no new adapter.
    Structured output is response_format {"type": "json_object"} whenever
    a json_schema is given, plain text otherwise. 429/503 retry 3x with
    exponential backoff honouring Retry-After; any other HTTP failure
    raises with the status in the message, never a silent None. Transport
    is httpx with an explicit 180s timeout (gateway audit §3: no
    provider call without a stated timeout; urllib is gone).
    A missing key raises LLMKeyMissingError, never a silent (None, False).
    """

    api_key: str
    model: str
    base_url: str = "https://openrouter.ai/api/v1"

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
        if not self.api_key:
            raise LLMKeyMissingError("openrouter")
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [_openrouter_message(m) for m in messages],
            "max_tokens": max_tokens,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if json_schema is not None:
            payload["response_format"] = {"type": "json_object"}
        url = self.base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        data: dict[str, Any] = {}
        with httpx.Client(timeout=LLM_PROVIDER_TIMEOUT_SECONDS) as client:
            for attempt in range(_MAX_ATTEMPTS):
                try:
                    response = client.post(url, json=payload, headers=headers)
                except httpx.TimeoutException as exc:
                    raise RuntimeError(
                        f"OpenRouter request timed out after "
                        f"{LLM_PROVIDER_TIMEOUT_SECONDS:.0f}s for model "
                        f"{self.model}: {exc}"
                    ) from exc
                except httpx.HTTPError as exc:
                    raise RuntimeError(
                        f"OpenRouter request failed for model "
                        f"{self.model}: {exc}"
                    ) from exc
                if (
                    response.status_code in _RETRYABLE_STATUS
                    and attempt < _MAX_ATTEMPTS - 1
                ):
                    _sleep_before_retry(
                        attempt,
                        _retry_after_seconds(
                            response.headers.get("Retry-After")
                        ),
                    )
                    continue
                if response.status_code >= 400:
                    detail = response.text[:500]
                    raise RuntimeError(
                        f"OpenRouter request failed: HTTP {response.status_code} "
                        f"for model {self.model}: "
                        f"{detail or response.reason_phrase}"
                    )
                data = response.json()
                break
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") for part in content if part.get("type") == "text"
            )
        text = (content or "").strip() or None
        truncated = choice.get("finish_reason") == "length"
        return text, truncated


def _openrouter_message(message: LLMMessage) -> dict[str, Any]:
    """Translate the provider-neutral message shape to OpenAI chat format.
    String content passes through; Anthropic-style list content becomes
    OpenAI parts (text stays text, images become image_url data URLs so
    nothing is silently dropped — a text-only model rejects them loudly
    instead of answering on a partial prompt)."""
    role = message.get("role", "user")
    content = message.get("content", "")
    if isinstance(content, str):
        return {"role": role, "content": content}
    parts: list[dict[str, Any]] = []
    for item in content:
        if item.get("type") == "text":
            parts.append({"type": "text", "text": item.get("text", "")})
        elif item.get("type") == "image":
            source = item.get("source", {})
            media_type = source.get("media_type", "image/png")
            parts.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{media_type};base64,{source.get('data', '')}"
                },
            })
    return {"role": role, "content": parts}


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
    if provider_name == "openrouter":
        return OpenRouterProvider(
            api_key=settings.openrouter_api_key,
            model=model,
            base_url=settings.openrouter_base_url,
        )
    raise ValueError(f"Unsupported LLM provider: {provider_name}")


def _default_model_for_provider(provider_name: str) -> str:
    """The model a provider resolves to when no model is configured.
    Per-task/global overrides win first (callers check llm_model);
    this is only the last-resort default per provider."""
    provider_name = provider_name.lower()
    if provider_name == "gemini":
        return settings.gemini_model
    if provider_name == "openrouter":
        return settings.openrouter_model
    return settings.anthropic_model


def get_llm_provider() -> LLMProvider:
    """Backward-compatible default provider."""
    return get_provider_for_model(
        settings.llm_provider,
        settings.llm_model or _default_model_for_provider(settings.llm_provider),
    )
