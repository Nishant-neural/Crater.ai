"""fix5: OpenRouter fallback provider, Gemini 503/429 retry, new routing.

- OpenRouter is an OpenAI-compatible chat-completions adapter over httpx
  with an explicit 180s timeout (gateway audit §3: urllib is gone).
  All HTTP is mocked here; no live calls.
- Routing (user decision 21 Nov 2026): diagnosis defaults to
  openrouter, extraction/integration/vision/expert stay on llm_provider.
- Reranking is a local CrossEncoder since Phase 1 Step 2, not an LLM
  route: the rerank_model / rerank_provider entries were deleted in
  Phase 1 Step 3, so there is no "rerank" entry in model_routes.
- Default free model verified live 21 Nov 2026 via GET
  https://openrouter.ai/api/v1/models: google/gemma-4-31b-it:free
  (context 262144); the suggested meta-llama/llama-3.3-70b-instruct:free
  is delisted.
"""
import importlib
import json
from types import SimpleNamespace

import pytest

from backend.config import Settings, settings
from backend.llm.model_router import ModelGateway
from backend.llm.provider import (
    GeminiProvider,
    LLMKeyMissingError,
    OpenRouterProvider,
    get_llm_provider,
    get_provider_for_model,
)


# ---------------------------------------------------------------------------
# fakes (httpx-shaped: the provider builds httpx.Client(timeout=180) and
# calls client.post(url, json=..., headers=...))
# ---------------------------------------------------------------------------

class FakeHTTPXResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self.headers = dict(headers or {})
        self._payload = payload

    @property
    def text(self):
        return json.dumps(self._payload) if self._payload is not None else ""

    def json(self):
        return self._payload

    @property
    def reason_phrase(self):
        return "error"


class FakeRequest:
    def __init__(self, url, json_body, headers):
        self.url = str(url)
        self.json_body = json_body
        self.headers = dict(headers or {})


class FakeHTTPXClient:
    """Replays outcomes (FakeHTTPXResponse or exception) in order."""

    def __init__(self, outcomes, timeout=None):
        self.outcomes = list(outcomes)
        self.requests = []
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, json=None, headers=None):
        self.requests.append(FakeRequest(url, json, headers))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _http_error(code, retry_after=None):
    headers = {}
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return FakeHTTPXResponse(
        code, {"error": {"message": "boom"}}, headers=headers
    )


def _ok_response(text=" model answer ", finish_reason="stop"):
    return FakeHTTPXResponse(200, {
        "choices": [{"message": {"role": "assistant", "content": text},
                     "finish_reason": finish_reason}],
    })


def _request_body(fake):
    assert len(fake.requests) == 1
    request = fake.requests[0]
    assert request.url == "https://openrouter.ai/api/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer test-key"
    assert request.headers["Content-Type"] == "application/json"
    return request.json_body


def _patch_httpx(monkeypatch, fake):
    def _factory(timeout=None, **kwargs):
        fake.timeout = timeout
        return fake

    monkeypatch.setattr("httpx.Client", _factory)


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
    fake = FakeHTTPXClient([_ok_response()])
    _patch_httpx(monkeypatch, fake)

    text, truncated = OpenRouterProvider("test-key", "test-model").complete_result(
        [{"role": "user", "content": "hello"}],
        max_tokens=42,
        json_schema={"type": "object", "properties": {"a": {"type": "string"}}},
    )

    assert text == "model answer"
    assert truncated is False
    assert fake.timeout == 180.0  # gateway audit §3: explicit 180s timeout
    body = _request_body(fake)
    assert body["model"] == "test-model"
    assert body["max_tokens"] == 42
    assert body["messages"] == [{"role": "user", "content": "hello"}]
    assert body["response_format"] == {"type": "json_object"}


def test_openrouter_plain_text_omits_response_format(monkeypatch):
    fake = FakeHTTPXClient([_ok_response()])
    _patch_httpx(monkeypatch, fake)

    text, _ = OpenRouterProvider("test-key", "test-model").complete_result(
        [{"role": "user", "content": "hello"}], max_tokens=10
    )

    assert text == "model answer"
    assert "response_format" not in _request_body(fake)


def test_openrouter_truncation_flag_comes_from_finish_reason(monkeypatch):
    fake = FakeHTTPXClient([_ok_response(finish_reason="length")])
    _patch_httpx(monkeypatch, fake)

    _, truncated = OpenRouterProvider("test-key", "test-model").complete_result(
        [{"role": "user", "content": "hello"}], max_tokens=10
    )
    assert truncated is True


def test_openrouter_empty_key_raises_without_request(monkeypatch):
    # Gateway audit §3: a missing key is an explicit error, never a silent
    # (None, False) — and no request may be made without a key.
    def _boom(*args, **kwargs):
        raise AssertionError("no request may be made without a key")

    monkeypatch.setattr("httpx.Client", _boom)
    with pytest.raises(LLMKeyMissingError, match="openrouter"):
        OpenRouterProvider("", "test-model").complete([], 10)


def test_openrouter_image_content_becomes_data_url_part(monkeypatch):
    fake = FakeHTTPXClient([_ok_response()])
    _patch_httpx(monkeypatch, fake)

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
    fake = FakeHTTPXClient([_ok_response()])
    _patch_httpx(monkeypatch, fake)

    OpenRouterProvider("test-key", "m", base_url="https://example.test/v1/").complete(
        [{"role": "user", "content": "hi"}], max_tokens=5
    )

    assert fake.requests[0].url == "https://example.test/v1/chat/completions"


# ---------------------------------------------------------------------------
# OpenRouter provider: retries + raised errors
# ---------------------------------------------------------------------------

def test_openrouter_429_retries_once_honouring_retry_after(monkeypatch):
    sleeps = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
    fake = FakeHTTPXClient([_http_error(429, retry_after="3"), _ok_response()])
    _patch_httpx(monkeypatch, fake)

    text = OpenRouterProvider("test-key", "test-model").complete(
        [{"role": "user", "content": "hello"}], max_tokens=10
    )

    assert text == "model answer"
    assert len(fake.requests) == 2
    assert sleeps == [3.0]


def test_openrouter_503_retries_with_exponential_backoff(monkeypatch):
    sleeps = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
    fake = FakeHTTPXClient([_http_error(503), _ok_response()])
    _patch_httpx(monkeypatch, fake)

    text = OpenRouterProvider("test-key", "test-model").complete(
        [{"role": "user", "content": "hello"}], max_tokens=10
    )

    assert text == "model answer"
    assert len(fake.requests) == 2
    assert sleeps == [1.0]


def test_openrouter_503_three_times_raises_with_status(monkeypatch):
    sleeps = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
    fake = FakeHTTPXClient([_http_error(503), _http_error(503), _http_error(503)])
    _patch_httpx(monkeypatch, fake)

    with pytest.raises(RuntimeError, match="503"):
        OpenRouterProvider("test-key", "test-model").complete(
            [{"role": "user", "content": "hello"}], max_tokens=10
        )

    assert len(fake.requests) == 3
    assert sleeps == [1.0, 2.0]


def test_openrouter_404_raises_with_status_and_no_retry(monkeypatch):
    fake = FakeHTTPXClient([_http_error(404)])
    _patch_httpx(monkeypatch, fake)

    with pytest.raises(RuntimeError, match="404"):
        OpenRouterProvider("test-key", "test-model").complete(
            [{"role": "user", "content": "hello"}], max_tokens=10
        )

    assert len(fake.requests) == 1


def test_openrouter_401_raises_with_status_and_no_retry(monkeypatch):
    fake = FakeHTTPXClient([_http_error(401)])
    _patch_httpx(monkeypatch, fake)

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
    mod = importlib.import_module("backend.llm.model_router")
    old = mod.settings
    mod.settings = fresh_settings
    try:
        return ModelGateway().route(task)
    finally:
        mod.settings = old


def test_clean_env_diagnosis_defaults_to_openrouter_rerank_route_removed(monkeypatch):
    for var in ("DIAGNOSIS_PROVIDER", "EXTRACTION_PROVIDER",
                "LLM_PROVIDER", "OPENROUTER_MODEL", "DIAGNOSIS_MODEL"):
        monkeypatch.delenv(var, raising=False)
    fresh = Settings()

    assert fresh.diagnosis_provider == "openrouter"
    assert fresh.extraction_provider == ""
    assert _route_with(fresh, "diagnosis").provider == "openrouter"
    assert _route_with(fresh, "diagnosis").model == "google/gemma-4-31b-it:free"
    # Phase 1 Step 3: no rerank LLM route remains (local CrossEncoder).
    assert "rerank" not in fresh.model_routes
    assert "rerank" not in fresh.task_max_tokens
    assert not hasattr(fresh, "rerank_provider")
    assert not hasattr(fresh, "rerank_model")


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
