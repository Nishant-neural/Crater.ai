"""Phase 1 Step 4 gate tests: rank-then-ask (audit Step 3, p.13).

One diagnostic turn is two structured-output calls — call 1 ranks
3-5 hypotheses (server-enforced, structured component + value,
citations intersected with the chunks retrieved this turn), call 2
asks / concludes from that ranking — and every turn 1 carries a
discriminating_test from the single backtracking picker
(``select_discriminating_test``), or the deterministic ranking
fallback when no signature test splits the ranking (the Miller
registry carries no per-cause test at all).

The harness rule is checked directly: ``score_discriminating_test``
scores 1 only when the step is a question/action whose text names at
least two distinct top-5 hypothesis causes.
"""
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db.models import Base, DiagnosticSession, DiagnosticStatus, Product
from backend.diagnostics import agent as diag_agent
from backend.diagnostics.backtracking import (
    select_discriminating_test, signatures_from_registry,
)
from backend.diagnostics.schema import DiagnosticState, EvidenceRef, Hypothesis
from backend.knowledge.repair_cases import load_miller_registry, load_registry
from benchmark.harness import score_discriminating_test

REPO_ROOT = Path(__file__).resolve().parent.parent

PRESSURE = "Insufficient wire feed pressure."
TIGHT = "Feed Tensioner is too tight."
GUN = "Damaged MIG Gun, cable, or liner assembly."
ROLLER = "Incorrect wire feed roller size."


def _rank(*causes, citations=("c1",)):
    return {
        "hypotheses": [
            {"cause": c, "component": "Feed Tensioner", "value": "low",
             "rationale": "p42", "confidence": 0.5 - 0.1 * i,
             "supporting_evidence_chunk_ids": list(citations)}
            for i, c in enumerate(causes)
        ],
        "eliminated_hypotheses": [],
    }


def _ask(step_type="question", content="What did the check show?"):
    return {"overall_confidence": 0.5,
            "next_step": {"type": step_type, "content": content,
                          "rationale": "test", "safety_notes": []}}


def _script(*turns):
    script = []
    for rank, ask in turns:
        script.append((json.dumps(rank), False))
        script.append((json.dumps(ask), False))
    return script


def _fake_gateway(monkeypatch, script):
    calls = {"n": 0, "kwargs": [], "messages": []}
    responses = list(script)

    def complete_result(task, messages, **kwargs):
        calls["kwargs"].append(kwargs)
        calls["messages"].append(messages)
        calls["n"] += 1
        return responses.pop(0)

    monkeypatch.setattr(diag_agent.gateway, "complete_result", complete_result)
    return calls


def _evidence():
    # Evidence whose text contains the component the rank stub names,
    # so the component-grounding check keeps it.
    return [EvidenceRef(chunk_id="c1", content="Feed Tensioner step 27: check the tension.",
                        source_document="manual", page_number=42)]


def _state():
    return DiagnosticState(symptoms=["Wire Feed Motor Runs but Wire Does Not Feed Properly"],
                           turns_taken=0)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    session = factory()
    yield session
    session.close()


def _make_session(db, product_id="p1", revision_id="r1",
                  symptoms=("Wire Feed Motor Runs but Wire Does Not Feed Properly",),
                  **state_kwargs):
    session = DiagnosticSession(
        product_id=product_id, revision_id=revision_id, status=DiagnosticStatus.active,
        state=DiagnosticState(
            symptoms=list(symptoms), **state_kwargs).model_dump(mode="json"))
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


# ---------------------------------------------------------------------------
# Two-call split
# ---------------------------------------------------------------------------

def test_turn_makes_two_structured_calls_rank_then_ask(monkeypatch):
    calls = _fake_gateway(monkeypatch, _script((_rank(PRESSURE, TIGHT, GUN), _ask())))
    result = diag_agent._run_llm_turn(_state(), _evidence())
    assert calls["n"] == 2
    rank_kwargs, ask_kwargs = calls["kwargs"]
    assert rank_kwargs["json_schema"]["required"] == ["hypotheses", "eliminated_hypotheses"]
    assert "next_step" not in rank_kwargs["json_schema"]["required"]
    assert ask_kwargs["json_schema"]["required"] == ["overall_confidence", "next_step"]
    assert "hypotheses" not in ask_kwargs["json_schema"]["required"]
    assert rank_kwargs["temperature"] == 0.2
    assert ask_kwargs["temperature"] == 0.2
    # The rank prompt carries the evidence; the ask prompt carries the
    # ranking + discriminating candidate and no evidence chunk text.
    assert "Feed Tensioner step 27" in calls["messages"][0][0]["content"]
    assert "Feed Tensioner step 27" not in calls["messages"][1][0]["content"]
    assert PRESSURE in calls["messages"][1][0]["content"]  # ranked block
    assert "Discriminating-test candidate" in calls["messages"][1][0]["content"]
    assert len(result["hypotheses"]) == 3
    assert result["next_step"]["type"] == "question"


def test_ask_prompt_carries_no_evidence_block():
    ranked = _rank(PRESSURE, TIGHT, GUN)["hypotheses"]
    prompt = diag_agent.build_ask_prompt(
        _state(), ranked, {"test": "t", "rationale": "r", "separates": [PRESSURE, TIGHT]}, "")
    assert len(prompt) <= diag_agent._PROMPT_TOTAL_BUDGET_CHARS
    assert "Evidence (whole rows" not in prompt
    assert PRESSURE in prompt and TIGHT in prompt and GUN in prompt


# ---------------------------------------------------------------------------
# Server-enforced 3-5 hypotheses
# ---------------------------------------------------------------------------

def test_rank_with_six_hypotheses_is_cut_to_top_five(monkeypatch):
    six = _rank(PRESSURE, TIGHT, GUN, ROLLER, "Fifth cause", "Sixth cause")
    _fake_gateway(monkeypatch, _script((six, _ask())))
    result = diag_agent._run_llm_turn(_state(), _evidence())
    assert [h["cause"] for h in result["hypotheses"]] == [
        PRESSURE, TIGHT, GUN, ROLLER, "Fifth cause"]


def test_single_guess_is_repaired_then_rejected_not_persisted(monkeypatch):
    """Audit P-03: Gemini returned a single guess in 7 sessions. A
    one-hypothesis rank spends the one repair call; if the repair
    still returns one, the turn degrades with the previous hypotheses
    intact — a single guess is never persisted as the ranking."""
    calls = _fake_gateway(monkeypatch, [
        (json.dumps(_rank(PRESSURE)), False),
        (json.dumps(_rank(PRESSURE)), False),
    ])
    state = _state()
    state.hypotheses = [Hypothesis(cause="Prior cause", rationale="p", confidence=0.4)]
    result = diag_agent._run_llm_turn(state, _evidence())
    assert calls["n"] == 2  # rank + one repair, no ask call
    assert result["next_step"]["type"] == "question"
    assert [h["cause"] for h in result["hypotheses"]] == ["Prior cause"]


def test_two_hypotheses_repaired_to_three_are_accepted(monkeypatch):
    calls = _fake_gateway(monkeypatch, [
        (json.dumps(_rank(PRESSURE, TIGHT)), False),
        (json.dumps(_rank(PRESSURE, TIGHT, GUN)), False),
        (json.dumps(_ask()), False),
    ])
    result = diag_agent._run_llm_turn(_state(), _evidence())
    assert calls["n"] == 3  # rank + repair + ask
    assert len(result["hypotheses"]) == 3


def test_empty_ranking_is_rejected_not_persisted(monkeypatch):
    empty = {"hypotheses": [], "eliminated_hypotheses": []}
    _fake_gateway(monkeypatch, [
        (json.dumps(empty), False), (json.dumps(empty), False)])
    result = diag_agent._run_llm_turn(_state(), _evidence())
    assert result["next_step"]["type"] == "question"
    assert result["hypotheses"] == []


# ---------------------------------------------------------------------------
# Citation intersection + structured component/value (P-14 / P-16)
# ---------------------------------------------------------------------------

def test_citation_intersection_drops_unretrieved_ids_and_labels_guesses(monkeypatch):
    rank = _rank(PRESSURE, TIGHT, GUN)
    rank["hypotheses"][0]["supporting_evidence_chunk_ids"] = ["c1", "not-retrieved"]
    rank["hypotheses"][1]["supporting_evidence_chunk_ids"] = ["not-retrieved"]
    rank["hypotheses"][2]["supporting_evidence_chunk_ids"] = []
    _fake_gateway(monkeypatch, _script((rank, _ask())))
    result = diag_agent._run_llm_turn(_state(), _evidence())
    by_cause = {h["cause"]: h for h in result["hypotheses"]}
    # Retrieved id survives; the unretrieved id is dropped, never honoured.
    assert by_cause[PRESSURE]["supporting_evidence_chunk_ids"] == ["c1"]
    assert by_cause[PRESSURE]["citation_status"] == "grounded"
    # Zero surviving citations -> kept, labelled guess (never dropped).
    assert by_cause[TIGHT]["supporting_evidence_chunk_ids"] == []
    assert by_cause[TIGHT]["citation_status"] == "guess"
    assert by_cause[GUN]["citation_status"] == "guess"
    assert len(result["hypotheses"]) == 3


def test_structured_component_and_value_survive_when_grounded(monkeypatch):
    rank = _rank(PRESSURE, TIGHT, GUN)
    _fake_gateway(monkeypatch, _script((rank, _ask())))
    result = diag_agent._run_llm_turn(_state(), _evidence())
    assert result["hypotheses"][0]["component"] == "Feed Tensioner"
    assert result["hypotheses"][0]["value"] == "low"


def test_ungrounded_component_is_blanked_hypothesis_kept(monkeypatch):
    """A component the model invents (not in the prompted evidence) is
    quarantined on its own field — the hypothesis is kept, the
    unverified component is blanked, never asserted from free text."""
    rank = _rank(PRESSURE, TIGHT, GUN)
    rank["hypotheses"][0]["component"] = "Imaginary Flux Capacitor"
    _fake_gateway(monkeypatch, _script((rank, _ask())))
    result = diag_agent._run_llm_turn(_state(), _evidence())
    assert result["hypotheses"][0]["cause"] == PRESSURE
    assert result["hypotheses"][0]["component"] == ""


def test_ask_failure_keeps_the_ranking(monkeypatch):
    _fake_gateway(monkeypatch, [
        (json.dumps(_rank(PRESSURE, TIGHT, GUN)), False),
        ("garbage {{{", False), ("still garbage", False)])
    result = diag_agent._run_llm_turn(_state(), _evidence())
    assert [h["cause"] for h in result["hypotheses"]] == [PRESSURE, TIGHT, GUN]
    assert result["next_step"]["type"] == "question"
    assert result["discriminating_test"] is not None


# ---------------------------------------------------------------------------
# Discriminating test: the single picker, then the fallback
# ---------------------------------------------------------------------------

def test_picker_splits_omnipro_case1_from_registry_signatures():
    """The one picker (backtracking.select_discriminating_test) over
    the OMNIPRO registry signatures splits the case-1 ranking — the
    test it returns carries information gain and the causes it
    separates. No second picker is built for this step."""
    signatures = signatures_from_registry(load_registry())
    hypotheses = [{"cause": c, "confidence": 0.5}
                  for c in [PRESSURE, TIGHT, GUN, ROLLER]]
    picked = select_discriminating_test(signatures, hypotheses)
    assert picked is not None
    # Registry signatures carry the test as a sentence (the
    # "step27_wood_test" id lives only in the oracle-vocabulary
    # signatures, which never enter the request path).
    assert picked["test"] == ("feed wire against wood 2-3 inches away; "
                              "the wire should bend, not stop.")
    assert picked["information_gain_bits"] > 0
    assert len(picked["separates"]) >= 2
    # The agent-level helper returns the picker result unchanged.
    state = DiagnosticState(symptoms=["Wire Feed Motor Runs but Wire Does Not Feed Properly"])
    got = diag_agent._discriminating_test_for_ranking(hypotheses, signatures, state)
    assert got["test"] == picked["test"]
    assert got.get("source") != "ranking_fallback"


def test_picker_returns_none_for_miller_fallback_names_the_test():
    """Miller's registry carries no per-cause test (0/63 causes), so
    the picker cannot split a Miller ranking — the deterministic
    fallback names the leading unasked cause instead, labelled as a
    fallback, with the top two ranked causes as the pair it separates."""
    signatures = signatures_from_registry(load_miller_registry())
    assert all(not s.tests for s in signatures)
    hypotheses = [{"cause": c, "confidence": 0.5}
                  for c in ["Cause A: check the drive rolls.",
                            "Cause B: check the line switch.",
                            "Cause C: check the wire spool."]]
    state = DiagnosticState(symptoms=["Low, high, or erratic wire speed."])
    assert select_discriminating_test(signatures, hypotheses) is None
    got = diag_agent._discriminating_test_for_ranking(hypotheses, signatures, state)
    assert got is not None
    assert got["source"] == "ranking_fallback"
    assert "Cause A: check the drive rolls." in got["test"]
    assert got["separates"] == ["Cause A: check the drive rolls.",
                                "Cause B: check the line switch."]


def test_signatures_are_product_matched_omnipro_and_miller(db, monkeypatch):
    """_signatures_for_product matches the registry to the product by
    manufacturer/family/model; an unknown product gets no signatures
    (the fallback applies) instead of another product's tests.

    Phase 3 extraction: the product list comes from ingestion's HTTP
    API, not the Product table — so the client is stubbed with the
    same three products the test used to seed."""
    import backend.retrieval.ingestion_client as ingestion_client_mod

    class _FakeClient:
        def list_products(self):
            return [
                {"id": "omni", "manufacturer": "Harbor Freight",
                 "family": "OMNIPRO", "model": "220"},
                {"id": "mill", "manufacturer": "Miller Electric",
                 "family": "Millermatic", "model": "252"},
                {"id": "other", "manufacturer": "Unknown",
                 "family": "Nope", "model": "0"},
            ]

    monkeypatch.setattr(ingestion_client_mod, "get_ingestion_client",
                        lambda *a, **k: _FakeClient())
    from backend.db.models import DiagnosticSession as _S
    omni = diag_agent._signatures_for_product(_S(product_id="omni"))
    assert len(omni) == 45  # one signature per OMNIPRO registry cause
    assert any(s.tests for s in omni)
    mill = diag_agent._signatures_for_product(_S(product_id="mill"))
    assert len(mill) == 63  # one signature per Miller registry cause
    assert all(not s.tests for s in mill)
    assert diag_agent._signatures_for_product(_S(product_id="other")) == []
    assert diag_agent._signatures_for_product(_S(product_id="missing")) == []


# ---------------------------------------------------------------------------
# Turn 1 end to end: every turn-1 response carries a discriminating test
# ---------------------------------------------------------------------------

def _stub_retrieve(monkeypatch, chunk_id="c1", content=None):
    from backend.retrieval.hybrid import RetrievedChunk
    chunk = RetrievedChunk(chunk_id=chunk_id,
                           content=content or "Feed Tensioner step 27: check the tension.",
                           page_number=42, document_id="d1", chunk_type="text")
    monkeypatch.setattr(diag_agent, "hybrid_retrieve", lambda *a, **k: [chunk])


def _stub_products(monkeypatch, products):
    """Phase 3 extraction: _signatures_for_product matches the registry
    against ingestion's HTTP product list, not the Product table — so
    product-dependent turn tests stub the ingestion client."""
    import backend.retrieval.ingestion_client as ingestion_client_mod

    class _FakeClient:
        def list_products(self):
            return products

    monkeypatch.setattr(ingestion_client_mod, "get_ingestion_client",
                        lambda *a, **k: _FakeClient())


def test_turn1_carries_discriminating_test_and_scores_on_harness(monkeypatch, db):
    """The frozen baseline scored discriminating test 0/17 in both
    runs. A turn-1 response now carries a discriminating_test on the
    state, and its step (content + rationale) names at least two
    ranked causes, so the harness scorer gives 1.0."""
    _stub_retrieve(monkeypatch)
    _fake_gateway(monkeypatch, _script((_rank(PRESSURE, TIGHT, GUN), _ask())))
    session = _make_session(db)
    state = diag_agent.run_turn(db, session, "checked tension, still not feeding")
    assert len(state.hypotheses) == 3
    assert state.discriminating_test is not None
    assert state.discriminating_test["test"]
    assert state.discriminating_test["separates"]
    assert state.current_step.type.value == "question"
    ranked_causes = [h.cause for h in state.hypotheses]
    scored = score_discriminating_test(state.current_step.model_dump(), ranked_causes)
    assert scored["score"] == 1.0
    assert len(scored["separates"]) >= 2


def test_turn1_with_omnipro_product_uses_picker_test(monkeypatch, db):
    """With a product-matched OMNIPRO registry the turn-1 test is the
    picker's (the feed-against-wood test), not the fallback — and the
    step carries it."""
    db.add(Product(id="omni", manufacturer="Harbor Freight", family="OMNIPRO", model="220"))
    db.commit()
    _stub_products(monkeypatch, [{"id": "omni", "manufacturer": "Harbor Freight",
                                  "family": "OMNIPRO", "model": "220"}])
    _stub_retrieve(monkeypatch)
    rank = _rank(PRESSURE, TIGHT, GUN, ROLLER)
    _fake_gateway(monkeypatch, _script((rank, _ask())))
    session = _make_session(db, product_id="omni")
    state = diag_agent.run_turn(db, session, "checked tension, still not feeding")
    assert state.discriminating_test is not None
    assert state.discriminating_test["test"] == (
        "feed wire against wood 2-3 inches away; the wire should bend, not stop.")
    assert state.discriminating_test["information_gain_bits"] > 0
    assert "feed wire against wood" in state.current_step.content


def test_turn1_with_miller_product_uses_fallback_test(monkeypatch, db):
    """With a product-matched Miller registry the picker cannot split
    (no per-cause tests), so turn 1 carries the labelled fallback —
    still a discriminating test, still scoring on the harness."""
    db.add(Product(id="mill", manufacturer="Miller Electric", family="Millermatic", model="252"))
    db.commit()
    _stub_products(monkeypatch, [{"id": "mill", "manufacturer": "Miller Electric",
                                  "family": "Millermatic", "model": "252"}])
    _stub_retrieve(monkeypatch)
    _fake_gateway(monkeypatch, _script((_rank(PRESSURE, TIGHT, GUN), _ask())))
    session = _make_session(db, product_id="mill")
    state = diag_agent.run_turn(db, session, "checked tension, still not feeding")
    assert state.discriminating_test is not None
    assert state.discriminating_test["source"] == "ranking_fallback"
    ranked_causes = [h.cause for h in state.hypotheses]
    assert score_discriminating_test(
        state.current_step.model_dump(), ranked_causes)["score"] == 1.0


# ---------------------------------------------------------------------------
# Miller turn-1 smoke: the real §6-5 chunk, hand-ranked from its rows
# ---------------------------------------------------------------------------

def _miller_s65_chunk() -> str:
    rows = json.loads((REPO_ROOT / "frozen" / "miller_parser_rows.json").read_text())
    s65 = [r for r in rows if str(r.get("source_table", "")).startswith("6-5")]
    assert len(s65) == 21
    table = "\n".join(f"Trouble: {r['symptom']} Remedy: {r['remedy']}" for r in s65)
    assert len(table) == 2885
    return table


def test_miller_turn1_smoke_ranked_cited_with_discriminating_test(monkeypatch, db):
    """Miller turn 1 (symptom miller-01) through run_turn with the real
    whole §6-5 chunk as the retrieved evidence and a hand-written rank
    built from its rows (the stub stands in for the model; the test
    proves the plumbing — enforcement, intersection, test attach —
    around it). Gate: 3-5 ranked hypotheses, citations intersect the
    retrieved chunk, discriminating_test present, rank prompt ≤ 5,000."""
    table = _miller_s65_chunk()
    _stub_retrieve(monkeypatch, chunk_id="s65", content=table)
    remedies = [
        "Be sure line disconnect switch is On (see Section 4-13).",
        "Check the wire feed speed control setting.",
        "Be sure the gun trigger is fully depressed.",
    ]
    rank = {
        "hypotheses": [
            {"cause": r, "component": "line disconnect switch", "value": "",
             "rationale": "§6-5 row", "confidence": 0.5 - 0.1 * i,
             "supporting_evidence_chunk_ids": ["s65"]}
            for i, r in enumerate(remedies)
        ],
        "eliminated_hypotheses": [],
    }
    calls = _fake_gateway(monkeypatch, _script((rank, _ask())))
    db.add(Product(id="mill", manufacturer="Miller Electric", family="Millermatic", model="252"))
    db.commit()
    _stub_products(monkeypatch, [{"id": "mill", "manufacturer": "Miller Electric",
                                  "family": "Millermatic", "model": "252"}])
    session = _make_session(
        db, product_id="mill",
        symptoms=["Low, high, or erratic wire speed."])  # miller-01
    # run_turn appends the input as an observation; the symptom is set above.
    state = diag_agent.run_turn(db, session, "wire speed is erratic")
    assert 3 <= len(state.hypotheses) <= 5
    retrieved_ids = {"s65"}
    for h in state.hypotheses:
        assert set(h.supporting_evidence_chunk_ids) <= retrieved_ids
        assert h.citation_status == "grounded"
    assert state.discriminating_test is not None
    assert state.discriminating_test["source"] == "ranking_fallback"
    assert state.discriminating_test["separates"] == remedies[:2]
    ranked_causes = [h.cause for h in state.hypotheses]
    assert score_discriminating_test(
        state.current_step.model_dump(), ranked_causes)["score"] == 1.0
    rank_prompt = calls["messages"][0][0]["content"]
    assert len(rank_prompt) <= diag_agent._PROMPT_TOTAL_BUDGET_CHARS
    assert table in rank_prompt  # whole §6-5 chunk, once
    assert rank_prompt.count(table) == 1


# ---------------------------------------------------------------------------
# Endings carry the test (audit P-09: both endings passed None)
# ---------------------------------------------------------------------------

def test_turn_bound_ending_carries_discriminating_test():
    result = {
        "hypotheses": [{"cause": PRESSURE, "confidence": 0.5, "rationale": "p42",
                        "supporting_evidence_chunk_ids": ["c1"]}],
        "eliminated_hypotheses": [],
        "overall_confidence": 0.5,
        "next_step": {"type": "question", "content": "x", "rationale": "y"},
        "discriminating_test": {"test": "step27_wood_test",
                                "rationale": "splits the ranking"},
    }
    state = DiagnosticState(turns_taken=7)  # about to become the 8th turn
    out = diag_agent._apply_forced_safeguards(state, result)
    assert out.get("turn_bound_reached") is True
    assert "Discriminating test: step27_wood_test" in out["next_step"]["content"]
    assert "What is missing" in out["next_step"]["content"]


def test_cannot_express_ending_carries_test_and_asks_no_question():
    test = {"test": "Run the manual's check for the leading cause — "
                    "Insufficient wire feed pressure. — and report the result.",
            "rationale": "No signature test splits this ranking."}
    state = DiagnosticState(
        symptoms=["Wire Feed Motor Runs but Wire Does Not Feed Properly"],
        hypotheses=[Hypothesis(cause=PRESSURE, rationale="p42", confidence=0.5)])
    step, _reason = diag_agent._cannot_express_ending(
        state, PRESSURE, "no component in this twin can carry it", test)
    assert "Discriminating test:" in step["content"]
    assert "?" not in step["content"]  # the ending is the answer, not a question
    assert "What is missing" in step["content"]
