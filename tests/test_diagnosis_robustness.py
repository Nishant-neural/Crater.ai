"""Diagnosis robustness (v12): a bad model output must never kill a session.

Covers, with the gateway mocked (no SDK, no network):
- structured-output plumbing: the diagnosis call carries a JSON schema
  and temperature 0.2, and no per-call max_tokens (route ceiling 8192);
- lenient parsing of the outputs models actually return (fences,
  preamble, trailing commas, single quotes, unquoted keys, truncation
  + one repair call);
- degradation: an unrecoverable turn keeps the session ACTIVE with the
  previous hypotheses intact and asks a question — never escalates;
- per-hypothesis quarantine: one malformed hypothesis is dropped, the
  good ones from the same turn are kept; percent confidences rescale;
- prompt slimming (Phase 1 Step 3): the prompt carries one copy of
  the facts — whole evidence rows — with no knowledge block and no
  truncation; the state is a typed summary, not a JSON dump;
- retrieval runs once per turn, not twice.
"""
import json
import sys
import types
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.config import settings
from backend.db.models import Base, DiagnosticSession, DiagnosticStatus
from backend.diagnostics import agent as diag_agent
from backend.diagnostics.schema import DiagnosticState, EvidenceRef, Hypothesis
from backend.llm.provider import AnthropicProvider, GeminiProvider


# Phase 1 Step 4: the turn is two structured-output calls now — the rank
# call returns hypotheses + eliminations (3 to 5, server-enforced), the
# ask call returns overall confidence + the next step. VALID_RANK cites
# chunk c1, the evidence chunk these tests use.
VALID_RANK = {
    "hypotheses": [
        {"cause": "Insufficient wire feed pressure", "component": "", "value": "low",
         "rationale": "p42 step 27", "confidence": 0.45,
         "supporting_evidence_chunk_ids": ["c1"]},
        {"cause": "Feed Tensioner is too tight", "component": "", "value": "",
         "rationale": "binds the mechanism", "confidence": 0.30,
         "supporting_evidence_chunk_ids": ["c1"]},
        {"cause": "Damaged MIG Gun, cable, or liner assembly", "component": "", "value": "open",
         "rationale": "p42 liner check", "confidence": 0.15,
         "supporting_evidence_chunk_ids": ["c1"]},
    ],
    "eliminated_hypotheses": [],
}
VALID_ASK = {
    "overall_confidence": 0.45,
    "next_step": {"type": "question", "content": "Is the feed tensioner set per step 27?",
                  "rationale": "Separates the top two causes.", "safety_notes": []},
}
# One valid turn = rank response, then ask response, in call order.
VALID_TURN_SCRIPT = [(json.dumps(VALID_RANK), False), (json.dumps(VALID_ASK), False)]


def _state_with_hypothesis() -> DiagnosticState:
    return DiagnosticState(
        symptoms=["Wire Feed Motor Runs but Wire Does Not Feed Properly"],
        hypotheses=[Hypothesis(cause="Insufficient wire feed pressure", rationale="p42", confidence=0.45,
                               supporting_evidence_chunk_ids=["c1"])],
        confidence=0.45,
        turns_taken=1,
    )


def _evidence() -> list[EvidenceRef]:
    return [EvidenceRef(chunk_id="c1", content="X" * 5000, source_document="manual", page_number=42)]


# ---------------------------------------------------------------------------
# Provider: structured output
# ---------------------------------------------------------------------------

def test_anthropic_json_mode_forces_tool_use_and_returns_tool_input(monkeypatch):
    captured = {}

    class FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                stop_reason="tool_use",
                content=[SimpleNamespace(type="tool_use", input={"a": 1})],
            )

    class FakeAnthropic:
        def __init__(self, api_key):
            self.messages = FakeMessages()

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=FakeAnthropic))
    text, truncated = AnthropicProvider("k", "m").complete_result(
        [{"role": "user", "content": "hi"}], 100,
        json_schema={"type": "object"}, temperature=0.2,
    )
    assert json.loads(text) == {"a": 1}
    assert truncated is False
    assert captured["temperature"] == 0.2
    assert captured["tool_choice"] == {"type": "tool", "name": "diagnostic_turn"}
    assert captured["tools"][0]["input_schema"] == {"type": "object"}


def test_anthropic_plain_call_unchanged(monkeypatch):
    captured = {}

    class FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(stop_reason="end_turn",
                                   content=[SimpleNamespace(text=" model answer ")])

    class FakeAnthropic:
        def __init__(self, api_key):
            self.messages = FakeMessages()

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=FakeAnthropic))
    assert AnthropicProvider("k", "m").complete([{"role": "user", "content": "hi"}], 42) == "model answer"
    assert "tools" not in captured and "temperature" not in captured
    assert captured == {"model": "m", "max_tokens": 42, "messages": [{"role": "user", "content": "hi"}]}


def test_anthropic_max_tokens_stop_reports_truncated(monkeypatch):
    class FakeMessages:
        def create(self, **kwargs):
            return SimpleNamespace(stop_reason="max_tokens", content=[SimpleNamespace(text="cut")])

    class FakeAnthropic:
        def __init__(self, api_key):
            self.messages = FakeMessages()

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=FakeAnthropic))
    _text, truncated = AnthropicProvider("k", "m").complete_result([{"role": "user", "content": "hi"}], 10)
    assert truncated is True


def _install_fake_genai(monkeypatch, finish_reason="STOP", text='{"ok": true}'):
    captured = {}

    class FakeConfig:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class FakeModels:
        def generate_content(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                text=text,
                candidates=[SimpleNamespace(finish_reason=finish_reason)],
            )

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    genai = types.SimpleNamespace(Client=FakeClient, types=types.SimpleNamespace(GenerateContentConfig=FakeConfig))
    google = types.SimpleNamespace(genai=genai)
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", genai.types)
    return captured


def test_gemini_json_mode_sets_mime_type_and_schema(monkeypatch):
    captured = _install_fake_genai(monkeypatch)
    text, truncated = GeminiProvider("k", "m").complete_result(
        [{"role": "user", "content": "hi"}], 8192,
        json_schema={"type": "object"}, temperature=0.2,
    )
    assert json.loads(text) == {"ok": True}
    assert truncated is False
    # GenerateContentConfig received the JSON-mode flags:
    assert captured["model"] == "m"
    assert captured["max_output_tokens"] == 8192
    assert captured["temperature"] == 0.2
    assert captured["response_mime_type"] == "application/json"
    assert captured["response_schema"] == {"type": "object"}


def test_gemini_truncation_detected_from_finish_reason(monkeypatch):
    _install_fake_genai(monkeypatch, finish_reason="MAX_TOKENS", text='{"cut": ')
    _text, truncated = GeminiProvider("k", "m").complete_result([{"role": "user", "content": "hi"}], 10)
    assert truncated is True


def test_gemini_plain_call_passes_only_max_tokens(monkeypatch):
    captured = _install_fake_genai(monkeypatch)
    assert GeminiProvider("k", "m").complete([{"role": "user", "content": "hi"}], 42) == '{"ok": true}'
    assert captured["max_output_tokens"] == 42
    assert "response_mime_type" not in captured
    assert "response_schema" not in captured
    assert "temperature" not in captured


def test_diagnosis_route_ceiling_is_8192():
    assert settings.model_routes["diagnosis"]["max_tokens"] == 8192
    assert settings.task_max_tokens["diagnosis"] == 8192


# ---------------------------------------------------------------------------
# Lenient parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [
    '```json\n{"a": 1}\n```',
    'Here is the diagnostic turn:\n{"a": 1}\nHope this helps.',
    '{"a": 1,}',
    "{'a': 1}",
    '{a: 1}',
    '{"a": "it is true that it broke"}',  # "true" inside a string must survive
])
def test_lenient_parse_recovers_real_model_outputs(raw):
    assert diag_agent._parse_lenient_json(raw) == {"a": 1} or diag_agent._parse_lenient_json(raw)["a"] in (1, "it is true that it broke")


def test_lenient_parse_returns_none_for_garbage():
    assert diag_agent._parse_lenient_json("not json at all {{{") is None
    assert diag_agent._parse_lenient_json("") is None
    assert diag_agent._parse_lenient_json(None) is None


# ---------------------------------------------------------------------------
# _run_llm_turn: parse, repair, degrade — never crash, never escalate
# ---------------------------------------------------------------------------

def _fake_gateway(monkeypatch, script):
    """script: list of (text, truncated) returned in order; repair calls consume the next entry."""
    calls = {"n": 0, "kwargs": [], "messages": []}
    responses = list(script)

    def complete_result(task, messages, **kwargs):
        calls["kwargs"].append(kwargs)
        calls["messages"].append(messages)
        calls["n"] += 1
        return responses.pop(0)

    monkeypatch.setattr(diag_agent.gateway, "complete_result", complete_result)
    return calls


def test_fenced_json_parses_and_keeps_hypotheses(monkeypatch):
    _fake_gateway(monkeypatch, [('```json\n' + json.dumps(VALID_RANK) + '\n```', False)] + VALID_TURN_SCRIPT[1:])
    result = diag_agent._run_llm_turn(_state_with_hypothesis(), _evidence())
    assert [h["cause"] for h in result["hypotheses"]] == [
        "Insufficient wire feed pressure", "Feed Tensioner is too tight",
        "Damaged MIG Gun, cable, or liner assembly"]
    assert result["next_step"]["type"] == "question"


def test_diagnosis_call_uses_schema_temperature_and_no_per_call_cap(monkeypatch):
    calls = _fake_gateway(monkeypatch, list(VALID_TURN_SCRIPT))
    diag_agent._run_llm_turn(_state_with_hypothesis(), _evidence())
    assert calls["n"] == 2  # rank call, then ask call
    rank_kwargs, ask_kwargs = calls["kwargs"]
    assert rank_kwargs["temperature"] == 0.2
    assert rank_kwargs["json_schema"]["required"] == ["hypotheses", "eliminated_hypotheses"]
    assert ask_kwargs["temperature"] == 0.2
    assert ask_kwargs["json_schema"]["required"] == ["overall_confidence", "next_step"]
    assert "max_tokens" not in rank_kwargs  # route ceiling (8192) applies instead
    assert "max_tokens" not in ask_kwargs


def test_truncated_output_is_repaired_not_escalated(monkeypatch):
    truncated = json.dumps(VALID_RANK)[:80]  # cut off mid-object
    _fake_gateway(monkeypatch, [(truncated, True), (json.dumps(VALID_RANK), False)] + VALID_TURN_SCRIPT[1:])
    result = diag_agent._run_llm_turn(_state_with_hypothesis(), _evidence())
    assert result["hypotheses"][0]["cause"] == "Insufficient wire feed pressure"
    assert result["next_step"]["type"] == "question"


def test_unrecoverable_output_degrades_to_question_with_hypotheses_intact(monkeypatch):
    _fake_gateway(monkeypatch, [("garbage {{{", False), ("still garbage", False)])
    state = _state_with_hypothesis()
    result = diag_agent._run_llm_turn(state, _evidence())
    assert result["next_step"]["type"] == "question"
    assert result["next_step"]["type"] != "escalate"
    assert [h["cause"] for h in result["hypotheses"]] == ["Insufficient wire feed pressure"]
    assert result["overall_confidence"] == 0.45


def test_provider_exception_degrades_to_question(monkeypatch):
    def boom(task, messages, **kwargs):
        raise RuntimeError("429 Resource exhausted")

    monkeypatch.setattr(diag_agent.gateway, "complete_result", boom)
    result = diag_agent._run_llm_turn(_state_with_hypothesis(), _evidence())
    assert result["next_step"]["type"] == "question"
    assert result["hypotheses"][0]["cause"] == "Insufficient wire feed pressure"


def test_no_provider_returns_question_not_escalation(monkeypatch):
    """Fix-3: the no-provider case used to return an escalate step.
    Escalation is removed from the loop — it now returns a question step
    (hypotheses intact) that says plainly no provider is configured."""
    _fake_gateway(monkeypatch, [(None, False)])
    result = diag_agent._run_llm_turn(_state_with_hypothesis(), _evidence())
    assert result["next_step"]["type"] == "question"
    assert "provider" in result["next_step"]["rationale"].lower()
    assert [h["cause"] for h in result["hypotheses"]] == ["Insufficient wire feed pressure"]


def test_wrong_shape_is_normalized_not_crashed(monkeypatch):
    bad = {
        "hypotheses": [
            {"cause": "Good cause", "rationale": "r", "confidence": 45,  # percent, not 0..1
             "supporting_evidence_chunk_ids": ["c1"]},
            {"rationale": "no cause at all", "confidence": 0.5},  # malformed: dropped
            "just a string",  # malformed: dropped
        ],
        "eliminated_hypotheses": [],
        # overall_confidence and next_step missing entirely
    }
    # The bad rank has only 1 valid hypothesis — below the 3 minimum —
    # so the server spends its one repair call on a full ranking; the
    # repair returns 3 hypotheses led by the good one, then the ask call
    # supplies the (defaulted) next step.
    repaired_rank = {"hypotheses": [
        {"cause": "Good cause", "rationale": "r", "confidence": 45,
         "supporting_evidence_chunk_ids": ["c1"]},
        {"cause": "Second cause", "rationale": "r", "confidence": 0.3,
         "supporting_evidence_chunk_ids": ["c1"]},
        {"cause": "Third cause", "rationale": "r", "confidence": 0.2,
         "supporting_evidence_chunk_ids": ["c1"]},
    ], "eliminated_hypotheses": []}
    _fake_gateway(monkeypatch, [(json.dumps(bad), False),
                                (json.dumps(repaired_rank), False),
                                (json.dumps(VALID_ASK), False)])
    result = diag_agent._run_llm_turn(_state_with_hypothesis(), _evidence())
    assert result["hypotheses"][0]["cause"] == "Good cause"
    assert len(result["hypotheses"]) == 3
    assert result["hypotheses"][0]["confidence"] == pytest.approx(0.45)
    assert result["overall_confidence"] == pytest.approx(0.45)
    assert result["next_step"]["type"] == "question"  # defaulted, not a crash


def test_state_summary_carries_no_chunk_text():
    """Phase 1 Step 3: the state is a typed summary, not a JSON dump —
    it carries symptoms and hypothesis causes, never chunk text. The
    chunk body itself appears exactly once, whole, in the evidence
    block of the assembled prompt."""
    state = _state_with_hypothesis()
    state.evidence = _evidence()
    summary = diag_agent._state_summary(state)
    assert "XXXX" not in summary  # the 5000-char chunk body is gone
    assert "Wire Feed Motor Runs but Wire Does Not Feed Properly" in summary
    assert "Insufficient wire feed pressure" in summary
    prompt = diag_agent.build_diagnosis_prompt(state, _evidence())
    assert prompt.count("X" * 5000) == 1  # whole chunk, exactly once
    assert "[c1] (p.42, manual)" in prompt


# ---------------------------------------------------------------------------
# run_turn end to end: session stays alive through a bad turn
# ---------------------------------------------------------------------------

@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    session = factory()
    yield session
    session.close()


def _make_session(db):
    session = DiagnosticSession(
        product_id="p1", revision_id="r1", status=DiagnosticStatus.active,
        state=DiagnosticState(symptoms=["Wire Feed Motor Runs but Wire Does Not Feed Properly"]).model_dump(mode="json"),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def test_run_turn_retrieves_once_and_survives_bad_output(monkeypatch, db):
    from backend.retrieval.hybrid import RetrievedChunk

    calls = {"retrieve": 0}
    chunk = RetrievedChunk(chunk_id="c1", content="feed pressure step 27", page_number=42,
                           document_id="d1", chunk_type="text")

    def fake_retrieve(query, product_id=None, revision_id=None):
        # Phase 3 extraction: hybrid_retrieve no longer takes a DB
        # session — the query is the first positional.
        calls["retrieve"] += 1
        return [chunk]

    monkeypatch.setattr(diag_agent, "hybrid_retrieve", fake_retrieve)
    gateway_calls = _fake_gateway(monkeypatch, list(VALID_TURN_SCRIPT) + [("garbage {{{", False), ("also garbage", False)])

    session = _make_session(db)
    state = diag_agent.run_turn(db, session, "checked tension, still not feeding")
    assert state.hypotheses[0].cause == "Insufficient wire feed pressure"
    assert session.status == DiagnosticStatus.active
    assert calls["retrieve"] == 1  # one retrieval per turn, not two

    # Phase 1 Step 3: the turn's prompt carries the evidence once,
    # whole, and no knowledge block at all.
    prompt = gateway_calls["messages"][0][0]["content"]
    assert prompt.count("feed pressure step 27") == 1
    assert "Known product knowledge" not in prompt
    assert "Canonical knowledge" not in prompt
    assert "Components (most relevant" not in prompt

    # Second turn: unparseable output must NOT escalate or lose hypotheses.
    state = diag_agent.run_turn(db, session, "roller looks fine")
    assert session.status == DiagnosticStatus.active
    assert [h.cause for h in state.hypotheses] == [
        "Insufficient wire feed pressure", "Feed Tensioner is too tight",
        "Damaged MIG Gun, cable, or liner assembly"]
    assert state.current_step.type.value == "question"
    assert calls["retrieve"] == 2


def test_run_turn_prompt_has_no_knowledge_block(monkeypatch, db):
    """Phase 1 Step 3: the deleted knowledge loader must not be
    reachable from the turn path — there is no function left to call,
    and the prompt carries the facts once, as evidence."""
    assert not hasattr(diag_agent, "_load_product_knowledge")
    assert not hasattr(diag_agent, "_knowledge_context")
    assert not hasattr(diag_agent, "_compact_state_json")
