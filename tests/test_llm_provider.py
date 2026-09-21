from types import SimpleNamespace

from backend.config import settings
from backend.llm.provider import AnthropicProvider, GeminiProvider, _gemini_content, get_llm_provider


def test_anthropic_provider_forwards_text_messages_and_returns_text(monkeypatch):
    captured = {}

    class FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(content=[SimpleNamespace(text=" model answer ")])

    class FakeAnthropic:
        def __init__(self, api_key):
            captured["api_key"] = api_key
            self.messages = FakeMessages()

    monkeypatch.setattr("anthropic.Anthropic", FakeAnthropic)

    result = AnthropicProvider("test-key", "test-model").complete(
        [{"role": "user", "content": "hello"}], max_tokens=42
    )

    assert result == "model answer"
    assert captured == {
        "api_key": "test-key",
        "model": "test-model",
        "max_tokens": 42,
        "messages": [{"role": "user", "content": "hello"}],
    }


def test_anthropic_provider_forwards_multimodal_messages_unchanged(monkeypatch):
    captured = {}

    class FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(content=[])

    class FakeAnthropic:
        def __init__(self, api_key):
            self.messages = FakeMessages()

    monkeypatch.setattr("anthropic.Anthropic", FakeAnthropic)
    messages = [{"role": "user", "content": [{"type": "image", "source": {"data": "abc"}}]}]

    assert AnthropicProvider("test-key", "test-model").complete(messages, 10) is None
    assert captured["messages"] == messages


def test_anthropic_provider_without_credentials_does_not_import_or_call_sdk():
    assert AnthropicProvider("", "test-model").complete([], 10) is None


def test_gemini_content_translates_text_and_assistant_roles():
    assert _gemini_content({"role": "user", "content": "hello"}) == {
        "role": "user",
        "parts": [{"text": "hello"}],
    }
    assert _gemini_content({"role": "assistant", "content": "answer"})["role"] == "model"


def test_gemini_content_translates_anthropic_image_content():
    assert _gemini_content({
        "role": "user",
        "content": [
            {"type": "text", "text": "read this"},
            {"type": "image", "source": {
                "media_type": "image/png", "data": "abc123"
            }},
        ],
    }) == {
        "role": "user",
        "parts": [
            {"text": "read this"},
            {"inline_data": {"mime_type": "image/png", "data": "abc123"}},
        ],
    }


def test_gemini_provider_without_credentials_does_not_import_or_call_sdk():
    assert GeminiProvider("", "test-model").complete([], 10) is None


def test_provider_factory_selects_gemini(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    monkeypatch.setattr(settings, "gemini_api_key", "gemini-key")
    monkeypatch.setattr(settings, "gemini_model", "gemini-test")
    monkeypatch.setattr(settings, "llm_model", "")

    provider = get_llm_provider()

    assert isinstance(provider, GeminiProvider)
    assert provider.api_key == "gemini-key"
    assert provider.model == "gemini-test"