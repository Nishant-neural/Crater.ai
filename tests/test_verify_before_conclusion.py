"""Fix-3: verify-before-conclusion gate, no-repeat discriminating test,
and escalation removal — all with the LLM gateway stubbed (no SDK, no network).

Gate (Phase 0 requirement, re-verified in code before the fix):
``run_turn`` used to set the session concluded/escalated the moment the LLM
picked a conclusion/escalate step, and ``respond`` only ran the tournament
while the session was still active — so the concluding turn never got a
tournament run and the conclusion went out unverified. The gate below
demotes an unvalidated conclusion to a question (the session stays active)
and only lets a VALIDATED conclusion stand; the tournament also runs
BEFORE the LLM turn so the model reasons over the verified ranking.

No-repeat (Nishant's live session, turns #3-#4): the same leading-cause
question was asked on two consecutive turns. Once a discriminating test /
leading-cause question has been asked and answered for a cause, it is
never asked again for that cause in the session.

Escalation removal (Nishant's instruction): the loop never escalates.
The turn bound now ends with the unavailable-with-attempt instead.
"""
import json
import sys
import types

# The test venv has no rank_bm25 (the retrieval import chain needs it only
# at import time). Stub it before importing the diagnostics routes.
_bm25 = types.ModuleType("rank_bm25")


class _BM25Okapi:
    def __init__(self, *args, **kwargs):
        pass


_bm25.BM25Okapi = _BM25Okapi
sys.modules.setdefault("rank_bm25", _bm25)

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.api.routes import diagnostics as diagnostics_routes
from backend.db.models import (
    Base, DiagnosticSession, DiagnosticStatus, DigitalTwin,
    MachineKnowledgeModelSnapshot, TwinStatus,
)
from backend.diagnostics import agent as diag_agent
from backend.diagnostics.backtracking import FaultSignature, select_discriminating_test
from backend.diagnostics.schema import DiagnosticState
from backend.simulation.schema import DigitalTwinDefinition


# ---------------------------------------------------------------------------
# Fixture: the OMNIPRO wire-feed twin (same seed rules as the tournament
# tests), plus a Wire Feed Motor that has no fault rule, so some causes
# are unevaluable by construction.
# ---------------------------------------------------------------------------

def _tournament_definition():
    transitions = [
        {"id": "seed:feed-tension-too-tight", "name": "Feed tensioner too tight stops wire feed",
         "conditions": {"entity:feed-tensioner.tension_ok": False},
         "effects": {"entity:welding-wire.feeding": False},
         "evidence": [{"fact": "Feed tension too tight stops wire feed.", "page": 42}]},
        {"id": "seed:feed-tension-ok-restores-feed", "name": "Correct feed tension restores wire feed",
         "conditions": {"entity:feed-tensioner.tension_ok": True},
         "effects": {"entity:welding-wire.feeding": True},
         "evidence": [{"fact": "Correct feed tension restores wire feed.", "page": 42}]},
        {"id": "seed:wingnut-loose-spool-spins", "name": "Loose wingnut lets spool spin freely",
         "conditions": {"entity:wingnut.tension_ok": False},
         "effects": {"entity:wire-spool.spinning_freely": True},
         "evidence": [{"fact": "A loose wingnut lets the spool spin freely.", "page": 10}]},
        {"id": "seed:wingnut-tight-stops-spool-spin", "name": "Tight wingnut stops spool spinning",
         "conditions": {"entity:wingnut.tension_ok": True},
         "effects": {"entity:wire-spool.spinning_freely": False},
         "evidence": [{"fact": "Tightening the wingnut stops the spool.", "page": 10}]},
    ]
    return DigitalTwinDefinition(
        name="Test OMNIPRO wire feed",
        components=[
            {"id": "entity:feed-tensioner", "name": "Feed Tensioner", "component_type": "other",
             "initial_state": {"tension_ok": True}},
            {"id": "entity:welding-wire", "name": "Welding Wire", "component_type": "other",
             "initial_state": {"feeding": True}},
            {"id": "entity:wingnut", "name": "Wingnut", "component_type": "other",
             "initial_state": {"tension_ok": True}},
            {"id": "entity:wire-spool", "name": "Wire Spool", "component_type": "other",
             "initial_state": {"spinning_freely": False}},
            {"id": "entity:wire-feed-motor", "name": "Wire Feed Motor", "component_type": "other",
             "initial_state": {"running": True}},
        ],
        initial_signals={},
        transitions=transitions,
    )


def _initial_twin_state(definition):
    return {"signals": dict(definition.initial_signals),
            "components": {c.id: dict(c.initial_state) for c in definition.components}}


_SNAPSHOT_MODEL = {
    "failure_modes": [
        {"name": "Wire Does Not Feed",
         "symptoms": ["Wire Feed Motor Runs but Wire Does Not Feed Properly"],
         "entity_ids": ["entity:feed-tensioner"], "evidence": []},
    ],
    "procedures": [
        {"name": "Adjust the Feed Tensioner",
         "steps": ["Loosen the Feed Tensioner knob", "Feed wire and check tension"]},
    ],
}

TENSIONER_CAUSE = "Wire Feed Motor Runs but Wire Does Not Feed Properly"
WINGNUT_CAUSE = "Wingnut is loose, spool spins freely"
MOTOR_CAUSE = "Wire Feed Motor is overheating"
AMBIGUOUS_CAUSE = "Feed Tensioner and Welding Wire fault"
PRESSURE_CAUSE = "Insufficient wire feed pressure."
TIGHT_CAUSE = "Feed Tensioner is too tight."


def _install_twin(db, revision_id="r1"):
    definition = _tournament_definition()
    twin = DigitalTwin(
        product_id="p1", revision_id=revision_id, name="test twin",
        definition=definition.model_dump(mode="json"),
        state=_initial_twin_state(definition), status=TwinStatus.active)
    snapshot = MachineKnowledgeModelSnapshot(
        revision_id=revision_id, version=1, model=dict(_SNAPSHOT_MODEL))
    db.add_all([twin, snapshot])
    db.commit()


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    session = factory()
    yield session
    session.close()


def _make_session(db, revision_id="r1", turns_taken=0, hypotheses=None):
    state = DiagnosticState(
        symptoms=["Wire Feed Motor Runs but Wire Does Not Feed Properly"],
        hypotheses=[{"cause": c, "rationale": "test", "confidence": 0.5}
                    for c in (hypotheses or [])],
        turns_taken=turns_taken)
    session = DiagnosticSession(
        product_id="p1", revision_id=revision_id, status=DiagnosticStatus.active,
        state=state.model_dump(mode="json"))
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def _fake_gateway(monkeypatch, script):
    """script: list of (text, truncated) returned in order; repair calls consume the next entry."""
    calls = {"n": 0, "messages": []}
    responses = list(script)

    def complete_result(task, messages, **kwargs):
        calls["n"] += 1
        calls["messages"].append(messages)
        return responses.pop(0)

    monkeypatch.setattr(diag_agent.gateway, "complete_result", complete_result)
    return calls


@pytest.fixture(autouse=True)
def _stub_retrieve(monkeypatch):
    monkeypatch.setattr(
        diag_agent, "retrieve_revision_context", lambda *a, **k: ([], []))


def _turn(hypotheses, step_type, step_content, confidence=0.9):
    return {
        "hypotheses": [{"cause": c, "rationale": "test", "confidence": 0.5}
                       for c in hypotheses],
        "eliminated_hypotheses": [],
        "overall_confidence": confidence,
        "next_step": {"type": step_type, "content": step_content,
                      "rationale": "test", "safety_notes": []},
    }


def _question_turn(cause):
    return _turn(
        [PRESSURE_CAUSE, TIGHT_CAUSE], "question",
        f"What do you observe when you check the leading cause — {cause}? "
        "Report the result and I will update the ranking.",
        confidence=0.5)


# ---------------------------------------------------------------------------
# (i) Conclusion about a cause whose component is matched but has no
# fault rule ends the session plainly (fix-3 extension).
#
# MOTOR_CAUSE was previously pinned here as demoting
# (test_conclusion_with_unvalidated_top_hypothesis_is_demoted): its
# component, Wire Feed Motor, IS in the twin, but no simulation rule
# describes a fault in it, so the row was status "unavailable" with
# ``cannot_express`` False. That is exactly the new shape: the
# tournament can never VALIDATE a conclusion about a fault in a
# component with no fault rule, so demoting would loop until the turn
# limit asking for observations that cannot help. The demotion
# behaviour itself is still pinned, by the ambiguous-cause test below.
# ---------------------------------------------------------------------------

def test_conclusion_about_matched_component_with_no_fault_rule_ends_session_plainly(monkeypatch, db):
    _install_twin(db)
    _fake_gateway(monkeypatch, [(json.dumps(_turn(
        [MOTOR_CAUSE, TENSIONER_CAUSE], "conclusion",
        "Wire Feed Motor is overheating — replace it")), False)])
    session = _make_session(db)
    state = diag_agent.run_turn(db, session, "motor is hot to the touch")

    content = state.current_step.content
    assert "twin cannot express" in content.lower()
    assert MOTOR_CAUSE in content
    assert "No simulation rule in this twin describes a fault in entity:wire-feed-motor" in content
    assert "?" not in content  # asks no further question
    assert "What do you observe" not in content
    assert "next untested cause" not in content
    assert "UNVERIFIED" not in content  # not a demotion
    assert state.turns_taken == 1  # ended at once, not at the turn limit
    assert session.status == DiagnosticStatus.active
    assert any(t["kind"] == "conclusion_unexpressible" for t in state.timeline)
    assert any(t["kind"] == "diagnosis_unavailable" for t in state.timeline)
    assert not any(t["kind"] == "conclusion_held" for t in state.timeline)
    assert "What is missing" in content
    # The tournament re-ranked: the VALIDATED tensioner is now on top,
    # so the attempt's ranked guesses lead with it.
    assert state.hypotheses[0].cause == TENSIONER_CAUSE
    unexpressible = next(
        t for t in state.timeline if t["kind"] == "conclusion_unexpressible")
    row = next(r for r in unexpressible["results"] if MOTOR_CAUSE in r["cause"])
    assert row["status"] == "unavailable"
    assert row["cannot_express"] is True
    assert row["fault_component_id"] == "entity:wire-feed-motor"


def test_conclusion_with_ambiguous_cause_is_still_demoted(monkeypatch, db):
    # The demotion path still exists: a cause the twin CAN express but
    # cannot resolve (here it matches two components, so no guess is
    # made) is held as UNVERIFIED and the session asks about the next
    # untested cause -- more observation could disambiguate it.
    _install_twin(db)
    _fake_gateway(monkeypatch, [(json.dumps(_turn(
        [AMBIGUOUS_CAUSE, TENSIONER_CAUSE], "conclusion",
        "Feed Tensioner and Welding Wire fault — replace both")), False)])
    session = _make_session(db)
    state = diag_agent.run_turn(db, session, "feed and wire both look wrong")

    assert state.current_step.type.value == "question"
    assert session.status == DiagnosticStatus.active
    assert "UNVERIFIED" in state.current_step.content
    assert any(t["kind"] == "conclusion_held" for t in state.timeline)
    held = next(t for t in state.timeline if t["kind"] == "conclusion_held")
    assert held["verdict"] == "unavailable"
    assert not any(t["kind"] == "conclusion_unexpressible" for t in state.timeline)
    # The tournament re-ranked: the VALIDATED tensioner is now on top,
    # even though the conclusion about the ambiguous cause was held.
    assert state.hypotheses[0].cause == TENSIONER_CAUSE


# ---------------------------------------------------------------------------
# (ii) Conclusion with a VALIDATED top hypothesis stands.
# ---------------------------------------------------------------------------

def test_conclusion_with_validated_top_hypothesis_stands(monkeypatch, db):
    _install_twin(db)
    _fake_gateway(monkeypatch, [(json.dumps(_turn(
        [TENSIONER_CAUSE], "conclusion", "Adjust the Feed Tensioner")), False)])
    session = _make_session(db)
    state = diag_agent.run_turn(db, session, "wire still not feeding")

    assert state.current_step.type.value == "conclusion"
    assert session.status == DiagnosticStatus.concluded
    assert any(t["kind"] == "conclusion_verified" for t in state.timeline)
    verified = next(t for t in state.timeline if t["kind"] == "conclusion_verified")
    assert verified["cause"] == TENSIONER_CAUSE


# ---------------------------------------------------------------------------
# (iii) No twin: conclusion is labelled unverified, timeline says so.
# ---------------------------------------------------------------------------

def test_conclusion_without_twin_is_labelled_unverified(monkeypatch, db):
    # No twin installed: the session's revision has no approved twin.
    _fake_gateway(monkeypatch, [(json.dumps(_turn(
        [MOTOR_CAUSE, TENSIONER_CAUSE], "conclusion",
        "Wire Feed Motor is overheating — replace it")), False)])
    session = _make_session(db)
    state = diag_agent.run_turn(db, session, "motor is hot to the touch")

    assert state.current_step.type.value == "question"
    assert session.status == DiagnosticStatus.active
    assert "UNVERIFIED" in state.current_step.content
    assert "no approved twin" in state.current_step.content.lower()
    assert any("no approved twin" in (t.get("step") or "").lower()
               for t in state.timeline)


# ---------------------------------------------------------------------------
# (iv) Tournament verdicts are visible to the LLM before it concludes.
# ---------------------------------------------------------------------------

def test_tournament_verdicts_visible_to_llm_before_conclusion(monkeypatch, db):
    _install_twin(db)
    calls = _fake_gateway(monkeypatch, [
        (json.dumps(_turn(
            [TENSIONER_CAUSE, MOTOR_CAUSE], "question",
            "What do you observe when you check the leading cause — "
            "Wire Feed Motor Runs but Wire Does Not Feed Properly? "
            "Report the result and I will update the ranking.",
            confidence=0.5)), False),
        (json.dumps(_turn([TENSIONER_CAUSE], "conclusion",
                          "Adjust the Feed Tensioner")), False),
    ])
    session = _make_session(db)
    diag_agent.run_turn(db, session, "wire not feeding")
    state = diag_agent.run_turn(db, session, "tensioner looks tight")

    # Turn 2's prompt — what the model saw before picking the conclusion —
    # carries the pre-turn tournament's verdicts.
    prompt = calls["messages"][1][0]["content"]
    assert "Twin verification" in prompt
    assert "VALIDATED" in prompt
    assert TENSIONER_CAUSE in prompt
    assert "the twin cannot evaluate" in prompt  # the motor row is visible too
    # The pre-turn tournament is on the timeline for turn 2.
    assert any(t["kind"] == "early_tournament" and t["turn"] == 2
               for t in state.timeline)
    assert state.current_step.type.value == "conclusion"
    assert session.status == DiagnosticStatus.concluded


# ---------------------------------------------------------------------------
# (v) The gate applies on the start-session first turn too.
# ---------------------------------------------------------------------------

def test_start_session_first_turn_conclusion_is_gated(monkeypatch, db):
    # MOTOR_CAUSE is the matched-component-no-fault-rule shape (fix-3
    # extension): concluding about it on the very first turn ends the
    # session plainly with the cannot-express attempt, instead of the
    # demotion this test previously pinned -- the gate applies on the
    # start-session first turn too, and this cause can never validate.
    _install_twin(db)
    _fake_gateway(monkeypatch, [(json.dumps(_turn(
        [MOTOR_CAUSE, TENSIONER_CAUSE], "conclusion",
        "Wire Feed Motor is overheating — replace it")), False)])
    session = diag_agent.start_session(
        db, "p1", "r1", "Wire Feed Motor Runs but Wire Does Not Feed Properly")

    assert session.status == DiagnosticStatus.active
    state = DiagnosticState.model_validate(session.state)
    assert state.current_step.type.value == "question"
    assert "twin cannot express" in state.current_step.content.lower()
    assert "?" not in state.current_step.content
    assert "UNVERIFIED" not in state.current_step.content
    assert any(t["kind"] == "conclusion_unexpressible" for t in state.timeline)


# ---------------------------------------------------------------------------
# No-repeat: the same leading-cause question is never asked twice.
# ---------------------------------------------------------------------------

def test_same_cause_is_not_asked_twice_in_one_session(monkeypatch, db):
    _fake_gateway(monkeypatch, [
        (json.dumps(_question_turn(PRESSURE_CAUSE)), False),
        (json.dumps(_question_turn(PRESSURE_CAUSE)), False),
    ])
    session = _make_session(db)
    first = diag_agent.run_turn(db, session, "wire not feeding")
    assert PRESSURE_CAUSE in first.current_step.content

    second = diag_agent.run_turn(db, session, "pressure looks fine, still not feeding")
    assert second.current_step.type.value == "question"
    assert second.current_step.content != first.current_step.content
    assert PRESSURE_CAUSE not in second.current_step.content
    # Moves to the next untested cause instead of repeating the question.
    assert TIGHT_CAUSE in second.current_step.content


def test_all_causes_checked_asks_what_changed(monkeypatch, db):
    _fake_gateway(monkeypatch, [
        (json.dumps(_question_turn(PRESSURE_CAUSE)), False),
        (json.dumps(_question_turn(TIGHT_CAUSE)), False),
        (json.dumps(_question_turn(PRESSURE_CAUSE)), False),
    ])
    session = _make_session(db)
    diag_agent.run_turn(db, session, "wire not feeding")
    diag_agent.run_turn(db, session, "pressure looks fine")
    third = diag_agent.run_turn(db, session, "tensioner looks fine too")

    assert third.current_step.type.value == "question"
    assert "what changed" in third.current_step.content.lower()


def _two_test_signatures():
    return [
        FaultSignature(cause=TENSIONER_CAUSE, tests={
            "Check feed tension": "tight", "Check wire spool": "spins"}),
        FaultSignature(cause=WINGNUT_CAUSE, tests={
            "Check feed tension": "loose", "Check wire spool": "still"}),
    ]


def test_select_discriminating_test_excludes_already_asked():
    sigs = _two_test_signatures()
    hypotheses = [{"cause": TENSIONER_CAUSE, "confidence": 0.5},
                  {"cause": WINGNUT_CAUSE, "confidence": 0.5}]
    best = select_discriminating_test(sigs, hypotheses)
    assert best["test"] == "Check feed tension"
    again = select_discriminating_test(
        sigs, hypotheses, exclude_tests={"Check feed tension"})
    assert again["test"] == "Check wire spool"
    assert select_discriminating_test(
        sigs, hypotheses,
        exclude_tests={"Check feed tension", "Check wire spool"}) is None


def test_early_tournament_does_not_repeat_discriminating_test(monkeypatch, db):
    _install_twin(db)
    monkeypatch.setattr(diagnostics_routes, "_signatures_for_session",
                        _two_test_signatures)
    state = DiagnosticState(
        symptoms=["Wire Feed Motor Runs but Wire Does Not Feed Properly"],
        hypotheses=[{"cause": TENSIONER_CAUSE, "rationale": "test", "confidence": 0.5},
                    {"cause": WINGNUT_CAUSE, "rationale": "test", "confidence": 0.5}],
        current_step={"type": "question", "content": "What do you observe?"},
        turns_taken=2,
        timeline=[{
            "turn": 1, "kind": "early_tournament",
            "input": "hypothesis tournament (early)",
            "available": True, "counts": {"validated": 2, "rejected": 0, "unavailable": 0},
            "discriminating_test": {"test": "Check feed tension", "rationale": "r"},
            "results": [], "step_type": "early_tournament",
            "step": "Hypothesis tournament (early): 2 validated",
        }])
    session = DiagnosticSession(
        product_id="p1", revision_id="r1", status=DiagnosticStatus.active,
        state=state.model_dump(mode="json"))
    db.add(session)
    db.commit()

    summary = diagnostics_routes._apply_early_tournament(db, session, state)
    assert summary is not None
    assert summary["discriminating_test"]["test"] == "Check wire spool"
    assert state.current_step.content.startswith(
        "Discriminating test: Check wire spool")


# ---------------------------------------------------------------------------
# Escalation removal: the loop never escalates.
# ---------------------------------------------------------------------------

def test_llm_escalate_step_becomes_a_question(monkeypatch, db):
    _fake_gateway(monkeypatch, [(json.dumps(_turn(
        [TENSIONER_CAUSE], "escalate", "Handing off to a human expert.",
        confidence=0.1)), False)])
    session = _make_session(db)
    state = diag_agent.run_turn(db, session, "wire not feeding")

    assert state.current_step.type.value == "question"
    assert session.status == DiagnosticStatus.active
    assert all(t.get("step_type") != "escalate" for t in state.timeline)


def test_turn_limit_ends_with_unavailable_attempt(monkeypatch, db):
    _fake_gateway(monkeypatch, [(json.dumps(_turn(
        [PRESSURE_CAUSE, TIGHT_CAUSE], "question",
        "What do you observe when you check the leading cause — "
        "Insufficient wire feed pressure.? Report the result and I will update the ranking.",
        confidence=0.5)), False)])
    session = _make_session(db, turns_taken=7,
                            hypotheses=[PRESSURE_CAUSE, TIGHT_CAUSE])
    state = diag_agent.run_turn(db, session, "still not feeding")

    assert state.current_step.type.value == "question"
    assert session.status == DiagnosticStatus.active
    assert "turn limit" in state.current_step.content.lower()
    assert PRESSURE_CAUSE in state.current_step.content
    assert "What is missing" in state.current_step.content
    assert any(t["kind"] == "diagnosis_unavailable" for t in state.timeline)
    assert all(t.get("step_type") != "escalate" for t in state.timeline)


def test_validated_conclusion_at_turn_limit_still_concludes(monkeypatch, db):
    """The turn bound must not override a conclusion the gate validated."""
    _install_twin(db)
    _fake_gateway(monkeypatch, [(json.dumps(_turn(
        [TENSIONER_CAUSE], "conclusion", "Adjust the Feed Tensioner")), False)])
    session = _make_session(db, turns_taken=7)
    state = diag_agent.run_turn(db, session, "wire still not feeding")

    assert state.current_step.type.value == "conclusion"
    assert session.status == DiagnosticStatus.concluded


# ---------------------------------------------------------------------------
# Twin-cannot-express: a concluded cause the twin cannot model at all
# ends the session at once with a plain statement -- no demotion, no
# next-cause question, no loop to the turn limit.
#
# The trap (Nishant's live case-1 session): the leading cause,
# "Insufficient wire feed pressure", is an adjustment-type cause with no
# entity_ids -- no component in the twin can carry it. The hard gate
# ("a conclusion stands only if the twin tests the top theory and
# passes") would wait forever for a pass that cannot come, demoting the
# conclusion turn after turn until the turn limit. The gate keys this
# path on the tournament row's ``cannot_express`` signal, not on the
# word "unavailable" alone: in this fixture the pressure cause takes
# the registry shape (a reviewed RepairAction whose components --
# Wire Feed Mechanism, MIG Gun, Welder -- this small twin does not
# have), so its row is status "unavailable" with ``cannot_express``
# True. The motor cause is the third shape (fix-3 extension): its
# component IS matched, but no simulation rule describes a fault in it,
# so its row is likewise "unavailable" with ``cannot_express`` True
# (see test_conclusion_about_matched_component_with_no_fault_rule_ends_session_plainly).
# The still-demoting shape is the ambiguous cause, which matches two
# components and carries ``cannot_express`` False
# (see test_conclusion_with_ambiguous_cause_is_still_demoted).
# ---------------------------------------------------------------------------

def test_conclusion_about_cause_twin_cannot_express_ends_session_plainly(monkeypatch, db):
    _install_twin(db)
    pressure_conclusion = _turn(
        [PRESSURE_CAUSE, TIGHT_CAUSE], "conclusion",
        "Insufficient wire feed pressure — increase the feed pressure properly")
    _fake_gateway(monkeypatch, [
        (json.dumps(pressure_conclusion), False),
        (json.dumps(pressure_conclusion), False),
    ])
    session = _make_session(db)
    state = diag_agent.run_turn(db, session, "wire not feeding")

    content = state.current_step.content
    assert "twin cannot express" in content.lower()
    assert PRESSURE_CAUSE in content
    assert "?" not in content  # asks no further question
    assert "What do you observe" not in content
    assert "next untested cause" not in content
    assert "UNVERIFIED" not in content  # not a demotion
    assert state.turns_taken == 1  # ended at once, not at the turn limit
    # Ending, not a validated conclusion: there is no unavailable status
    # in DiagnosticStatus, and "concluded" would falsely claim a grounded
    # recommendation -- the same shape as the turn-bound ending.
    assert session.status == DiagnosticStatus.active
    assert any(t["kind"] == "conclusion_unexpressible" for t in state.timeline)
    assert any(t["kind"] == "diagnosis_unavailable" for t in state.timeline)
    assert not any(t["kind"] == "conclusion_held" for t in state.timeline)
    assert "What is missing" in content
    unexpressible = next(
        t for t in state.timeline if t["kind"] == "conclusion_unexpressible")
    row = next(r for r in unexpressible["results"] if PRESSURE_CAUSE in r["cause"])
    assert row["status"] == "unavailable"
    assert row["cannot_express"] is True

    # A second turn that concludes the same cause ends the same way at
    # once -- the loop never runs to the turn limit.
    second = diag_agent.run_turn(db, session, "still not feeding, pressure looks low")
    assert second.turns_taken == 2
    assert "twin cannot express" in second.current_step.content.lower()
    assert "?" not in second.current_step.content
