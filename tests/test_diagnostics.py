"""
Tests for the deterministic safety guardrails around the diagnostic loop —
these must hold regardless of what the LLM decides, per plan.md §25.
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


def test_forced_escalation_on_sustained_low_confidence():
    state = DiagnosticState(turns_taken=4)  # about to become the 5th turn
    result = _apply_forced_safeguards(state, _base_result(confidence=0.1))
    assert result["next_step"]["type"] == "escalate"


def test_forced_escalation_on_turn_limit_regardless_of_confidence():
    state = DiagnosticState(turns_taken=7)  # about to become the 8th turn
    result = _apply_forced_safeguards(state, _base_result(confidence=0.95))
    assert result["next_step"]["type"] == "escalate"


def test_high_confidence_conclusion_is_not_overridden_before_limits():
    state = DiagnosticState(turns_taken=2)
    result = _apply_forced_safeguards(state, _base_result(confidence=0.9, step_type="conclusion"))
    assert result["next_step"]["type"] == "conclusion"
