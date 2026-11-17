"""Readiness item C — the real LLM wired into the DraftLLM slot.

``gateway_draft_llm`` calls the existing gateway (task "drafting", the
same ``complete_result`` interface diagnosis uses, structured output
against the DraftArchetype schema, temperature 0.0). The gateway is
STUBBED in every test here: no live LLM call happens in the suite. The
first REAL draft + the 15-minute review measurement stays a live check
for the user (needs his LLM credentials + a chosen first Layer 2 type).
"""
import json

import pytest

import backend.simulation.drafting as drafting_module
from backend.simulation.drafting import (
    DRAFTING_MAX_TOKENS,
    DRAFTING_TASK,
    DraftArchetype,
    check_schema,
    draft_archetype,
    gateway_draft_llm,
)

BRIEF = {"case": {"case_id": "omni-05-does-not-function"},
         "facts": {"quantities": []}}

DRAFT_DICT = {
    "archetype_id": "stub",
    "human_name": "stub",
    "states": ["ok"],
    "version": "0.1.0",
}


def _stubbed(monkeypatch, raw, truncated=False):
    def _stub(task, messages, json_schema=None, temperature=None,
              max_tokens=None):
        _stub.seen = {"task": task, "messages": messages,
                      "json_schema": json_schema, "temperature": temperature,
                      "max_tokens": max_tokens}
        return raw, truncated

    monkeypatch.setattr(drafting_module.gateway, "complete_result", _stub)
    return _stub


def test_gateway_draft_llm_calls_the_gateway_with_schema(monkeypatch):
    stub = _stubbed(monkeypatch, json.dumps(DRAFT_DICT))
    result = gateway_draft_llm(BRIEF)
    assert result == DRAFT_DICT
    assert stub.seen["task"] == DRAFTING_TASK == "drafting"
    assert stub.seen["temperature"] == 0.0
    assert stub.seen["max_tokens"] == DRAFTING_MAX_TOKENS == 8192
    assert stub.seen["json_schema"]["properties"]["propagation_table"]
    assert "Drafting brief" in stub.seen["messages"][0]["content"]


def test_draft_archetype_defaults_to_the_gateway(monkeypatch):
    _stubbed(monkeypatch, json.dumps(DRAFT_DICT))
    assert draft_archetype(BRIEF)["archetype_id"] == "stub"


def test_gateway_draft_llm_parses_fenced_json(monkeypatch):
    _stubbed(monkeypatch, "```json\n" + json.dumps(DRAFT_DICT) + "\n```")
    assert gateway_draft_llm(BRIEF) == DRAFT_DICT


def test_gateway_draft_llm_no_provider_raises(monkeypatch):
    _stubbed(monkeypatch, None)
    with pytest.raises(RuntimeError, match="No LLM provider is configured"):
        gateway_draft_llm(BRIEF)


def test_gateway_draft_llm_truncated_raises(monkeypatch):
    _stubbed(monkeypatch, json.dumps(DRAFT_DICT), truncated=True)
    with pytest.raises(RuntimeError, match="truncated"):
        gateway_draft_llm(BRIEF)


def test_gateway_draft_llm_unparseable_raises(monkeypatch):
    _stubbed(monkeypatch, "not json at all {{{")
    with pytest.raises(ValueError, match="parseable JSON"):
        gateway_draft_llm(BRIEF)


def test_gateway_draft_llm_non_object_raises(monkeypatch):
    _stubbed(monkeypatch, "[1, 2, 3]")
    with pytest.raises(ValueError, match="JSON object"):
        gateway_draft_llm(BRIEF)


def test_stubbed_gateway_draft_passes_schema_check(monkeypatch):
    """The wired path produces a draft the pre-review gate accepts as
    schema-valid (check 1) — the draft itself is a stub, not a real
    LLM draft; no live call is made."""
    _stubbed(monkeypatch, json.dumps(DRAFT_DICT))
    draft, check = check_schema(draft_archetype(BRIEF))
    assert isinstance(draft, DraftArchetype)
    assert check.passed
