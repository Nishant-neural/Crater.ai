"""fix5: OpenRouter fallback provider, Gemini 503/429 retry, new routing.

- OpenRouter is an OpenAI-compatible chat-completions adapter over stdlib
  urllib (no new dependency). All HTTP is mocked here; no live calls.
- Routing (user decision 21 Nov 2026): diagnosis + rerank default to
  openrouter, extraction/integration/vision/expert stay on llm_provider.
- Default free model verified live 21 Nov 2026 via GET
  https://openrouter.ai/api/v1/models: google/gemma-4-31b-it:free
  (context 262144); the suggested meta-llama/llama-3.3-70b-instruct:free
  is delisted.
"""
import importlib
import io
import json
import urllib.error
from types import SimpleNamespace

import pytest

from backend.config import Settings, settings
from backend.llm.gateway import ModelGateway
from backend.llm.provider import (
    GeminiProvider,
    OpenRouterProvider,
    get_llm_provider,
    get_provider_for_model,
)


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------

class FakeHTTPResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeUrlopen:
    """Replays outcomes (FakeHTTPResponse or exception) in order."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _http_error(code, retry_after=None):
    headers = {}
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError(
        "https://openrouter.ai/api/v1/chat/completions",
        code,
        f"{code} error",
        headers,
        io.BytesIO(b'{"error": {"message": "boom"}}'),
    )


def _ok_response(text=" model answer ", finish_reason="stop"):
    return FakeHTTPResponse({
        "choices": [{"message": {"role": "assistant", "content": text},
                     "finish_reason": finish_reason}],
    })


def _request_body(fake):
    assert len(fake.requests) == 1
    request = fake.requests[0]
    assert request.get_method() == "POST"
    assert request.full_url == "https://openrouter.ai/api/v1/chat/completions"
    assert request.get_header("Authorization") == "Bearer test-key"
    assert request.get_header("Content-type") == "application/json"
    return json.loads(request.data.decode("utf-8"))


class FakeTransientError(Exception):
    """Stands in for a google-genai ServerError/ClientError (.code)."""

    def __init__(self, code):
        super().__init__(f"{code} UNAVAILABLE: high demand")
        self.code = code


class FakeGeminiModels:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def generate_content(self, **kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeGeminiClient:
    def __init__(self, outcomes, captured):
        self._models = FakeGeminiModels(outcomes)
        self._captured = captured

    @property
    def models(self):
        return self._models

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._captured["closed"] = True


def _gemini_client_with(monkeypatch, outcomes):
    captured = {"closed": False}
    models_holder = {}

    class FakeClient(FakeGeminiClient):
        def __init__(self, api_key):
            super().__init__(outcomes, captured)
            models_holder["models"] = self._models

    monkeypatch.setattr("google.genai.Client", FakeClient)
    return captured, models_holder


# ---------------------------------------------------------------------------
# OpenRouter provider: request shape + responses
# ---------------------------------------------------------------------------

def test_openrouter_json_mode_sends_response_format_and_returns_text(monkeypatch):
    fake = FakeUrlopen([_ok_response()])
    monkeypatch.setattr("urllib.request.urlopen", fake)

    text, truncated = OpenRouterProvider("test-key", "test-model").complete_result(
        [{"role": "user", "content": "hello"}],
        max_tokens=42,
        json_schema={"type": "object", "properties": {"a": {"type": "string"}}},
    )

    assert text == "model answer"
    assert truncated is False
    body = _request_body(fake)
    assert body["model"] == "test-model"
    assert body["max_tokens"] == 42
    assert body["messages"] == [{"role": "user", "content": "hello"}]
    assert body["response_format"] == {"type": "json_object"}


def test_openrouter_plain_text_omits_response_format(monkeypatch):
    fake = FakeUrlopen([_ok_response()])
    monkeypatch.setattr("urllib.request.urlopen", fake)

    text, _ = OpenRouterProvider("test-key", "test-model").complete_result(
        [{"role": "user", "content": "hello"}], max_tokens=10
    )

    assert text == "model answer"
    assert "response_format" not in _request_body(fake)


def test_openrouter_truncation_flag_comes_from_finish_reason(monkeypatch):
    fake = FakeUrlopen([_ok_response(finish_reason="length")])
    monkeypatch.setattr("urllib.request.urlopen", fake)

    _, truncated = OpenRouterProvider("test-key", "test-model").complete_result(
        [{"role": "user", "content": "hello"}], max_tokens=10
    )
    assert truncated is True


def test_openrouter_empty_key_returns_none_without_request(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("no request may be made without a key")

    monkeypatch.setattr("urllib.request.urlopen", _boom)
    assert OpenRouterProvider("", "test-model").complete([], 10) is None


def test_openrouter_image_content_becomes_data_url_part(monkeypatch):
    fake = FakeUrlopen([_ok_response()])
    monkeypatch.setattr("urllib.request.urlopen", fake)

    OpenRouterProvider("test-key", "test-model").complete(
        [{"role": "user", "content": [
            {"type": "text", "text": "read this"},
            {"type": "image", "source": {"media_type": "image/png", "data": "abc123"}},
        ]}],
        max_tokens=10,
    )

    body = _request_body(fake)
    assert body["messages"] == [{
        "role": "user",
        "content": [
            {"type": "text", "text": "read this"},
            {"type": "image_url",
             "image_url": {"url": "data:image/png;base64,abc123"}},
        ],
    }]


def test_openrouter_custom_base_url_is_honoured(monkeypatch):
    fake = FakeUrlopen([_ok_response()])
    monkeypatch.setattr("urllib.request.urlopen", fake)

    OpenRouterProvider("test-key", "m", base_url="https://example.test/v1/").complete(
        [{"role": "user", "content": "hi"}], max_tokens=5
    )

    assert fake.requests[0].full_url == "https://example.test/v1/chat/completions"


# ---------------------------------------------------------------------------
# OpenRouter provider: retries + raised errors
# ---------------------------------------------------------------------------

def test_openrouter_429_retries_once_honouring_retry_after(monkeypatch):
    sleeps = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
    fake = FakeUrlopen([_http_error(429, retry_after="3"), _ok_response()])
    monkeypatch.setattr("urllib.request.urlopen", fake)

    text = OpenRouterProvider("test-key", "test-model").complete(
        [{"role": "user", "content": "hello"}], max_tokens=10
    )

    assert text == "model answer"
    assert len(fake.requests) == 2
    assert sleeps == [3.0]


def test_openrouter_503_retries_with_exponential_backoff(monkeypatch):
    sleeps = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
    fake = FakeUrlopen([_http_error(503), _ok_response()])
    monkeypatch.setattr("urllib.request.urlopen", fake)

    text = OpenRouterProvider("test-key", "test-model").complete(
        [{"role": "user", "content": "hello"}], max_tokens=10
    )

    assert text == "model answer"
    assert len(fake.requests) == 2
    assert sleeps == [1.0]


def test_openrouter_503_three_times_raises_with_status(monkeypatch):
    sleeps = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
    fake = FakeUrlopen([_http_error(503), _http_error(503), _http_error(503)])
    monkeypatch.setattr("urllib.request.urlopen", fake)

    with pytest.raises(RuntimeError, match="503"):
        OpenRouterProvider("test-key", "test-model").complete(
            [{"role": "user", "content": "hello"}], max_tokens=10
        )

    assert len(fake.requests) == 3
    assert sleeps == [1.0, 2.0]


def test_openrouter_404_raises_with_status_and_no_retry(monkeypatch):
    fake = FakeUrlopen([_http_error(404)])
    monkeypatch.setattr("urllib.request.urlopen", fake)

    with pytest.raises(RuntimeError, match="404"):
        OpenRouterProvider("test-key", "test-model").complete(
            [{"role": "user", "content": "hello"}], max_tokens=10
        )

    assert len(fake.requests) == 1


def test_openrouter_401_raises_with_status_and_no_retry(monkeypatch):
    fake = FakeUrlopen([_http_error(401)])
    monkeypatch.setattr("urllib.request.urlopen", fake)

    with pytest.raises(RuntimeError, match="401"):
        OpenRouterProvider("test-key", "test-model").complete(
            [{"role": "user", "content": "hello"}], max_tokens=10
        )

    assert len(fake.requests) == 1


# ---------------------------------------------------------------------------
# Gemini retry
# ---------------------------------------------------------------------------

def test_gemini_retries_503_then_succeeds(monkeypatch):
    sleeps = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
    _captured, holder = _gemini_client_with(monkeypatch, [
        FakeTransientError(503),
        SimpleNamespace(text=" model answer ", candidates=[]),
    ])

    text = GeminiProvider("test-key", "test-model").complete(
        [{"role": "user", "content": "hello"}], max_tokens=42
    )

    assert text == "model answer"
    assert holder["models"].calls == 2
    assert sleeps == [1.0]


def test_gemini_retries_429_then_succeeds(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    _captured, holder = _gemini_client_with(monkeypatch, [
        FakeTransientError(429),
        SimpleNamespace(text="ok", candidates=[]),
    ])

    assert GeminiProvider("test-key", "test-model").complete(
        [{"role": "user", "content": "hello"}], max_tokens=10
    ) == "ok"
    assert holder["models"].calls == 2


def test_gemini_503_three_times_raises(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    _captured, holder = _gemini_client_with(monkeypatch, [
        FakeTransientError(503),
        FakeTransientError(503),
        FakeTransientError(503),
    ])

    with pytest.raises(FakeTransientError):
        GeminiProvider("test-key", "test-model").complete(
            [{"role": "user", "content": "hello"}], max_tokens=10
        )

    assert holder["models"].calls == 3


def test_gemini_non_retryable_error_raises_immediately(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    _captured, holder = _gemini_client_with(monkeypatch, [ValueError("boom")])

    with pytest.raises(ValueError, match="boom"):
        GeminiProvider("test-key", "test-model").complete(
            [{"role": "user", "content": "hello"}], max_tokens=10
        )

    assert holder["models"].calls == 1


# ---------------------------------------------------------------------------
# Routing: factory + gateway defaults
# ---------------------------------------------------------------------------

def test_provider_factory_selects_openrouter(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "or-key")
    monkeypatch.setattr(settings, "openrouter_base_url", "https://example.test/v1")

    provider = get_provider_for_model("openrouter", "some-model")

    assert isinstance(provider, OpenRouterProvider)
    assert provider.api_key == "or-key"
    assert provider.model == "some-model"
    assert provider.base_url == "https://example.test/v1"


def test_get_llm_provider_openrouter_uses_openrouter_model(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openrouter")
    monkeypatch.setattr(settings, "llm_model", "")
    monkeypatch.setattr(settings, "openrouter_api_key", "or-key")
    monkeypatch.setattr(settings, "openrouter_model", "or-model")

    provider = get_llm_provider()

    assert isinstance(provider, OpenRouterProvider)
    assert provider.model == "or-model"


def _route_with(fresh_settings, task):
    """Route one task against a fresh Settings object (gateway swap)."""
    mod = importlib.import_module("backend.llm.gateway")
    old = mod.settings
    mod.settings = fresh_settings
    try:
        return ModelGateway().route(task)
    finally:
        mod.settings = old


def test_clean_env_diagnosis_and_rerank_default_to_openrouter(monkeypatch):
    for var in ("DIAGNOSIS_PROVIDER", "RERANK_PROVIDER", "EXTRACTION_PROVIDER",
                "LLM_PROVIDER", "OPENROUTER_MODEL", "DIAGNOSIS_MODEL",
                "RERANK_MODEL"):
        monkeypatch.delenv(var, raising=False)
    fresh = Settings()

    assert fresh.diagnosis_provider == "openrouter"
    assert fresh.rerank_provider == "openrouter"
    assert fresh.extraction_provider == ""
    assert _route_with(fresh, "diagnosis").provider == "openrouter"
    assert _route_with(fresh, "diagnosis").model == "google/gemma-4-31b-it:free"
    assert _route_with(fresh, "rerank").provider == "openrouter"
    assert _route_with(fresh, "rerank").model == "google/gemma-4-31b-it:free"


def test_clean_env_extraction_still_follows_llm_provider(monkeypatch):
    for var in ("DIAGNOSIS_PROVIDER", "RERANK_PROVIDER", "EXTRACTION_PROVIDER",
                "LLM_PROVIDER"):
        monkeypatch.delenv(var, raising=False)
    fresh = Settings()

    route = _route_with(fresh, "extraction")
    assert route.provider == "anthropic"  # the llm_provider default, unchanged
    assert route.model == "claude-sonnet-4-6"


def test_diagnosis_provider_env_override_wins(monkeypatch):
    monkeypatch.setenv("DIAGNOSIS_PROVIDER", "gemini")
    monkeypatch.setenv("DIAGNOSIS_MODEL", "gemini-override")
    fresh = Settings()

    route = _route_with(fresh, "diagnosis")
    assert route.provider == "gemini"
    assert route.model == "gemini-override"


def test_openrouter_model_env_sets_diagnosis_model(monkeypatch):
    monkeypatch.delenv("DIAGNOSIS_PROVIDER", raising=False)
    monkeypatch.delenv("DIAGNOSIS_MODEL", raising=False)
    monkeypatch.setenv("OPENROUTER_MODEL", "some/other-free:free")
    fresh = Settings()

    assert fresh.openrouter_model == "some/other-free:free"
    assert _route_with(fresh, "diagnosis").model == "some/other-free:free"


def test_other_defaults_unchanged(monkeypatch):
    for var in ("LLM_PROVIDER", "GEMINI_MODEL", "ANTHROPIC_MODEL",
                "EXTRACTION_PROVIDER", "INTEGRATION_PROVIDER",
                "VISION_PROVIDER", "EXPERT_PROVIDER"):
        monkeypatch.delenv(var, raising=False)
    fresh = Settings()

    assert fresh.llm_provider == "anthropic"
    assert fresh.llm_model == ""
    assert fresh.gemini_model == "gemini-2.5-flash-lite"
    assert fresh.anthropic_model == "claude-sonnet-4-6"
    assert fresh.extraction_provider == ""
    assert fresh.integration_provider == ""
    assert fresh.vision_provider == ""
    assert fresh.expert_provider == ""
    assert fresh.openrouter_base_url == "https://openrouter.ai/api/v1"
