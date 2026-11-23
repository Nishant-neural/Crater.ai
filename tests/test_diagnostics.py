"""
Tests for the deterministic guardrails around the diagnostic loop.

Fix-3 removed escalation from the loop entirely (Nishant: it cut his live
session off instead of diagnosing). What these guards now enforce:
- no step is ever an escalation (a model returning one is degraded to a
  question, and the turn bound ends the loop with the
  unavailable-with-attempt instead);
- a high-confidence conclusion before the limits is not overridden.
"""
from backend.diagnostics.agent import _apply_forced_safeguards
from backend.diagnostics.schema import DiagnosticState


def _base_result(confidence: float, step_type: str = "question") -> dict:
    return {
        "hypotheses": [],
        "eliminated_hypotheses": [],
        "overall_confidence": confidence,
        "next_step": {"type": step_type, "content": "x", "rationale": "y", "safety_notes": []},
    }


def test_no_forced_escalation_when_confidence_high_and_turns_low():
    state = DiagnosticState(turns_taken=1)
    result = _apply_forced_safeguards(state, _base_result(confidence=0.9))
    assert result["next_step"]["type"] == "question"


def test_low_confidence_no_longer_escalates():
    """Removed in fix-3: sustained low confidence used to force an escalate
    step. The loop has no escalation path any more — the turn stays a
    question and the session stays active."""
    state = DiagnosticState(turns_taken=4)  # about to become the 5th turn
    result = _apply_forced_safeguards(state, _base_result(confidence=0.1))
    assert result["next_step"]["type"] == "question"


def test_turn_limit_ends_with_unavailable_attempt_not_escalation():
    """Removed in fix-3: the turn limit used to force an escalate step.
    It now ends the loop with the unavailable-with-attempt (ranked best
    guesses + what is missing) as a question step — never a bare stop and
    never an escalation."""
    state = DiagnosticState(turns_taken=7)  # about to become the 8th turn
    result = _apply_forced_safeguards(state, _base_result(confidence=0.95))
    assert result["next_step"]["type"] == "question"
    assert result.get("turn_bound_reached") is True
    assert "turn limit" in result["next_step"]["content"].lower()
    assert "unknown upstream fault" in result["next_step"]["content"]
    assert "What is missing" in result["next_step"]["content"]


def test_escalate_step_from_model_is_degraded_to_question():
    """A model returning an escalate step (the schema no longer offers it)
    is degraded to a question, never honoured."""
    state = DiagnosticState(turns_taken=1)
    result = _apply_forced_safeguards(state, _base_result(confidence=0.9, step_type="escalate"))
    assert result["next_step"]["type"] == "question"


def test_high_confidence_conclusion_is_not_overridden_before_limits():
    state = DiagnosticState(turns_taken=2)
    result = _apply_forced_safeguards(state, _base_result(confidence=0.9, step_type="conclusion"))
    assert result["next_step"]["type"] == "conclusion"
