from types import SimpleNamespace

from backend.llm.provider import AnthropicProvider


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