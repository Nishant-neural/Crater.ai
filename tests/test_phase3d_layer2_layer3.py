"""Phase 3D tests — Layer 3 field promotion (DEFER, scalability base)
and the binding points-table decision.

Phase 1 Step 5 CUT drafting (``backend/simulation/drafting.py``) and
review view (``backend/simulation/review_view.py``): the Layer 2
drafting workflow and its review surface serve neither the core loop
nor scalability, so their tests were removed with the modules. What
remains here is the deferred field-promotion behaviour and the
points-table decision, which are untouched by the cut.
"""

import json

import pytest

from backend.simulation import archetypes as arch
from backend.simulation.archetypes import ARCHETYPES
from backend.simulation.binding import bind_model
from backend.simulation.field_promotion import (
    FieldOutcome,
    PromotionApproval,
    RepairRecord,
    evaluate_promotion,
    record_field_outcome,
)
from backend.simulation.omnipro_proof import build_omnipro_proof_model


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Layer 3 — field promotion (nothing auto-promotes)
# ---------------------------------------------------------------------------

def test_field_outcome_wires_validation_history():
    archetype = ARCHETYPES["transmission"]
    before = dict(archetype.validation_history)
    record_field_outcome(archetype, FieldOutcome(
        archetype_id="transmission", row_id="transmission.t2-low.slipping",
        machine_variant="OMNIPRO 220", technician="tech-1",
        outcome="SUCCESSFUL", discriminating_test="step27_wood_test",
        test_outcome="stops"))
    assert archetype.validation_history["field_instances"] == \
        before.get("field_instances", 0) + 1
    assert archetype.validation_history["validated_count"] == \
        before.get("validated_count", 0) + 1
    # Restore (the test must not pollute the shared library).
    archetype.validation_history = before


def test_promotion_never_auto_promotes_even_after_many_validations():
    archetype = ARCHETYPES["transmission"]
    before = dict(archetype.validation_history)
    for _ in range(50):
        record_field_outcome(archetype, FieldOutcome(
            archetype_id="transmission", technician="tech-1",
            outcome="SUCCESSFUL"))
    decision = evaluate_promotion(archetype, approval=None)
    assert not decision.promoted
    assert "Nothing auto-promotes" in decision.reason
    assert decision.field_evidence["field_instances"] == \
        before.get("field_instances", 0) + 50
    # Simulator verdicts are never counted: the decision carries only
    # field evidence.
    assert set(decision.field_evidence) == {"field_successful",
                                            "field_failed", "field_instances"}
    archetype.validation_history = before


def test_promotion_with_a_human_approval_promotes_on_field_evidence_only():
    archetype = ARCHETYPES["transmission"]
    before = dict(archetype.validation_history)
    record_field_outcome(archetype, FieldOutcome(
        archetype_id="transmission", technician="tech-1",
        outcome="SUCCESSFUL"))
    record_field_outcome(archetype, FieldOutcome(
        archetype_id="transmission", technician="tech-1",
        outcome="FAILED"))
    approval = PromotionApproval(
        approver="Nishant", reason="2 field instances reviewed; the "
        "successful one outweighs the failed one for this row.")
    decision = evaluate_promotion(archetype, approval=approval)
    assert decision.promoted
    assert decision.approval is not None
    assert decision.approval.approver == "Nishant"
    assert decision.field_evidence["field_successful"] == \
        before.get("validated_count", 0) + 1
    assert decision.field_evidence["field_failed"] == \
        before.get("rejected_count", 0) + 1
    assert "Simulator verdicts were not counted" in decision.reason
    archetype.validation_history = before


def test_repair_record_keeps_twin_validated_and_field_successful_separate():
    record = RepairRecord(repair_id="r-1", case_id="omni-01-wire-not-feeding",
                          cause="Insufficient wire feed pressure.")
    record.record_twin_verdict("VALIDATED")
    assert record.twin_verdict == "VALIDATED"
    assert record.field_outcome is None
    # A twin VALIDATED followed by field FAILED is a learning signal.
    record.record_field_outcome("FAILED", technician="tech-1")
    assert record.field_outcome == "FAILED"
    assert record.learning_signal is not None
    assert "learning signal" in record.learning_signal
    # And a twin VALIDATED followed by field SUCCESSFUL is not.
    record2 = RepairRecord(repair_id="r-2", case_id="omni-01-wire-not-feeding",
                           cause="Insufficient wire feed pressure.")
    record2.record_twin_verdict("VALIDATED")
    record2.record_field_outcome("SUCCESSFUL", technician="tech-1")
    assert record2.learning_signal is None


# ---------------------------------------------------------------------------
# Binding points table — decided on measured 3B/3C data (no table)
# ---------------------------------------------------------------------------

def test_points_table_not_needed_on_measured_data():
    """v3 §8 defers the points table until the oracle gives data to tune
    it against. The measured 3B/3C data: the §8 unique-match rule binds
    15/18 on the proof twin with 0 ambiguous and 0 wrong bindings, and
    the 3 unresolved entities have NO signature at all (no ports, no
    bound parameters) — a points table ranks candidates, and with no
    signature there are no candidates to rank, so it could not bind them
    either. The rule is sufficient on the measured data; no points table
    is built. If coverage ever falls below this rate or an ambiguous
    binding appears, the question reopens."""
    model = build_omnipro_proof_model()
    bindings = bind_model(model)
    bound = [r for r in bindings.values() if r["archetype_id"] is not None]
    unresolved = [r for r in bindings.values() if r["archetype_id"] is None]
    assert len(bound) == 15
    assert len(unresolved) == 3
    # 0 ambiguous: no unresolved entity has competing candidates.
    for rec in unresolved:
        assert not rec.get("candidates"), rec["entity_name"]
        # And no signature at all (nothing for a points table to score).
        assert rec["evidence"]["ports"] == []
        assert rec["evidence"]["parameters_bound"] == []
    # 0 wrong bindings: every bound entity's archetype matches its
    # declared library type / ports (spot-checked by the 3B binding test).
    assert {r["entity_name"] for r in unresolved} == {
        "Wingnut", "Feed Tensioner", "Welding Wire"}
