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
- prompt slimming: state_json carries evidence ids only, never the
  full chunk text;
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
from backend.db.models import Base, Component, DiagnosticSession, DiagnosticStatus, FailureMode
from backend.diagnostics import agent as diag_agent
from backend.diagnostics.schema import DiagnosticState, EvidenceRef, Hypothesis
from backend.llm.provider import AnthropicProvider, GeminiProvider


VALID_TURN = {
    "hypotheses": [
        {"cause": "Insufficient wire feed pressure", "rationale": "p42 step 27", "confidence": 0.45,
         "supporting_evidence_chunk_ids": ["c1"]},
        {"cause": "Feed Tensioner is too tight", "rationale": "binds the mechanism", "confidence": 0.30,
         "supporting_evidence_chunk_ids": ["c1"]},
    ],
    "eliminated_hypotheses": [],
    "overall_confidence": 0.45,
    "next_step": {"type": "question", "content": "Is the feed tensioner set per step 27?",
                  "rationale": "Separates the top two causes.", "safety_notes": []},
}


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
    calls = {"n": 0, "kwargs": []}
    responses = list(script)

    def complete_result(task, messages, **kwargs):
        calls["kwargs"].append(kwargs)
        calls["n"] += 1
        return responses.pop(0)

    monkeypatch.setattr(diag_agent.gateway, "complete_result", complete_result)
    return calls


def test_fenced_json_parses_and_keeps_hypotheses(monkeypatch):
    _fake_gateway(monkeypatch, [('```json\n' + json.dumps(VALID_TURN) + '\n```', False)])
    result = diag_agent._run_llm_turn(_state_with_hypothesis(), _evidence(), "knowledge")
    assert [h["cause"] for h in result["hypotheses"]] == [
        "Insufficient wire feed pressure", "Feed Tensioner is too tight"]
    assert result["next_step"]["type"] == "question"


def test_diagnosis_call_uses_schema_temperature_and_no_per_call_cap(monkeypatch):
    calls = _fake_gateway(monkeypatch, [(json.dumps(VALID_TURN), False)])
    diag_agent._run_llm_turn(_state_with_hypothesis(), _evidence(), "knowledge")
    kwargs = calls["kwargs"][0]
    assert kwargs["temperature"] == 0.2
    assert kwargs["json_schema"]["required"] == ["hypotheses", "eliminated_hypotheses", "overall_confidence", "next_step"]
    assert "max_tokens" not in kwargs  # route ceiling (8192) applies instead


def test_truncated_output_is_repaired_not_escalated(monkeypatch):
    truncated = json.dumps(VALID_TURN)[:80]  # cut off mid-object
    _fake_gateway(monkeypatch, [(truncated, True), (json.dumps(VALID_TURN), False)])
    result = diag_agent._run_llm_turn(_state_with_hypothesis(), _evidence(), "knowledge")
    assert result["hypotheses"][0]["cause"] == "Insufficient wire feed pressure"
    assert result["next_step"]["type"] == "question"


def test_unrecoverable_output_degrades_to_question_with_hypotheses_intact(monkeypatch):
    _fake_gateway(monkeypatch, [("garbage {{{", False), ("still garbage", False)])
    state = _state_with_hypothesis()
    result = diag_agent._run_llm_turn(state, _evidence(), "knowledge")
    assert result["next_step"]["type"] == "question"
    assert result["next_step"]["type"] != "escalate"
    assert [h["cause"] for h in result["hypotheses"]] == ["Insufficient wire feed pressure"]
    assert result["overall_confidence"] == 0.45


def test_provider_exception_degrades_to_question(monkeypatch):
    def boom(task, messages, **kwargs):
        raise RuntimeError("429 Resource exhausted")

    monkeypatch.setattr(diag_agent.gateway, "complete_result", boom)
    result = diag_agent._run_llm_turn(_state_with_hypothesis(), _evidence(), "knowledge")
    assert result["next_step"]["type"] == "question"
    assert result["hypotheses"][0]["cause"] == "Insufficient wire feed pressure"


def test_no_provider_still_escalates_honestly(monkeypatch):
    _fake_gateway(monkeypatch, [(None, False)])
    result = diag_agent._run_llm_turn(_state_with_hypothesis(), _evidence(), "knowledge")
    assert result["next_step"]["type"] == "escalate"


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
    _fake_gateway(monkeypatch, [(json.dumps(bad), False)])
    result = diag_agent._run_llm_turn(_state_with_hypothesis(), _evidence(), "knowledge")
    assert [h["cause"] for h in result["hypotheses"]] == ["Good cause"]
    assert result["hypotheses"][0]["confidence"] == pytest.approx(0.45)
    assert result["overall_confidence"] == pytest.approx(0.45)
    assert result["next_step"]["type"] == "question"  # defaulted, not a crash


def test_compact_state_json_carries_ids_not_chunk_text():
    state = _state_with_hypothesis()
    state.evidence = _evidence()
    dumped = diag_agent._compact_state_json(state)
    assert "XXXX" not in dumped  # the 5000-char chunk body is gone
    assert "c1" in dumped and "42" in dumped


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
    from backend.retrieval.knowledge import RetrievedKnowledge

    calls = {"retrieve": 0}
    chunk = RetrievedChunk(chunk_id="c1", content="feed pressure step 27", page_number=42,
                           document_id="d1", chunk_type="text")
    knowledge = [RetrievedKnowledge("failure_modes", "fm1", 2.0,
                                    {"symptom": "wire does not feed", "possible_causes": ["pressure"]},
                                    "p1", "r1", "Rev A")]

    def fake_retrieve(session, query, product_id, revision_id):
        calls["retrieve"] += 1
        return [chunk], knowledge

    monkeypatch.setattr(diag_agent, "retrieve_revision_context", fake_retrieve)
    _fake_gateway(monkeypatch, [(json.dumps(VALID_TURN), False), ("garbage {{{", False), ("also garbage", False)])

    session = _make_session(db)
    state = diag_agent.run_turn(db, session, "checked tension, still not feeding")
    assert state.hypotheses[0].cause == "Insufficient wire feed pressure"
    assert session.status == DiagnosticStatus.active
    assert calls["retrieve"] == 1  # one retrieval per turn, not two

    # Second turn: unparseable output must NOT escalate or lose hypotheses.
    state = diag_agent.run_turn(db, session, "roller looks fine")
    assert session.status == DiagnosticStatus.active
    assert [h.cause for h in state.hypotheses] == [
        "Insufficient wire feed pressure", "Feed Tensioner is too tight"]
    assert state.current_step.type.value == "question"
    assert calls["retrieve"] == 2


def test_load_product_knowledge_filters_to_relevant_rows(db):
    for i in range(30):
        db.add(Component(revision_id="r1", name=f"Unrelated Part {i:02d}",
                         function="something else entirely", location_description="elsewhere"))
    db.add(Component(revision_id="r1", name="Wire Feed Motor", function="drives the wire",
                     location_description="wire feed assembly"))
    db.add(Component(revision_id="r1", name="Feed Tensioner", function="sets feed pressure",
                     location_description="wire feed assembly"))
    db.add(FailureMode(revision_id="r1", symptom="Wire does not feed",
                       possible_causes=["Insufficient wire feed pressure"]))
    for i in range(20):
        db.add(FailureMode(revision_id="r1", symptom=f"Unrelated symptom {i}",
                           possible_causes=["other"]))
    db.commit()

    block = diag_agent._load_product_knowledge(
        db, "r1", "Wire Feed Motor Runs but Wire Does Not Feed Properly Feed Tensioner")
    assert "Wire Feed Motor" in block
    assert "Feed Tensioner" in block
    assert "Wire does not feed" in block
    assert "Unrelated Part 29" not in block  # only 23 of the 30 unrelated rows fit under the cap
    assert "Unrelated symptom 19" not in block
    component_lines = [ln for ln in block.splitlines() if ln.startswith("- ") and "Symptom" not in ln]
    assert len(component_lines) <= diag_agent._MAX_COMPONENT_LINES
