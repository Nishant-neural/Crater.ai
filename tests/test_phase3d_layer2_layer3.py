"""Phase 3D tests — Layer 2 drafting workflow, Layer 3 field promotion,
review view, and the binding points-table decision.

No live LLM is called anywhere here: the drafting machinery is exercised
with hand-written drafts that stand in for LLM output, and the gateway
path is exercised with a stubbed gateway (see
tests/test_phase456_readiness_drafting.py). The LLM is a slot
(``DraftLLM``), implemented for real by ``gateway_draft_llm`` on the
existing gateway.
"""

import json

import pytest

from backend.simulation import archetypes as arch
from backend.simulation.archetypes import ARCHETYPES
from backend.simulation.binding import bind_model
from backend.simulation.drafting import (
    DRAFT_STATES,
    DraftArchetype,
    DraftAuthorization,
    DraftFailureMode,
    DraftParameter,
    DraftPort,
    DraftRow,
    DraftingBrief,
    DraftingLoop,
    archetype_to_draft,
    build_drafting_brief,
    check_binding,
    check_oracle,
    check_port_signature_collision,
    check_template_executability,
    check_value_provenance,
    draft_archetype,
    run_pre_review_gate,
    sandbox_archetype_library,
)
from backend.simulation.field_promotion import (
    FieldOutcome,
    PromotionApproval,
    RepairRecord,
    evaluate_promotion,
    record_field_outcome,
)
from backend.simulation.omnipro_proof import build_omnipro_proof_model
from backend.simulation.review_view import (
    REVIEW_CHECKLIST,
    FieldReview,
    ReviewRecord,
    record_review,
    render_review_view,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _brief(case_id="omni-05-does-not-function"):
    return build_drafting_brief(case_id)


def _switch_draft(**overrides):
    """A draft that copies the A2 switch (the relay) — the baseline for
    the collision and oracle checks."""
    draft = archetype_to_draft(ARCHETYPES["switch"],
                               intended_entity="Trigger")
    data = draft.model_dump(mode="json")
    data.update(overrides)
    return DraftArchetype.model_validate(data)


def _gas_regulator_draft():
    """A NEW archetype draft for case 8 (porosity): a gas regulator on the
    Gas Valve. It mirrors the A10 valve's rows but adds a distinguishing
    ``pressure_out`` port, so it does not collide with the valve's
    signature and binds only the Gas Valve."""
    rows = []
    for row_id, inputs, fault, outputs, next_state in [
        ("gas-regulator.v-open.open",
         {"fluid_in": "normal", "control_in": "high"}, "none",
         {"fluid_out": "normal", "pressure_out": "normal"}, "open"),
        ("gas-regulator.v-closed.closed",
         {"fluid_in": "normal", "control_in": "low"}, "none",
         {"fluid_out": "absent", "pressure_out": "absent"}, "closed"),
        ("gas-regulator.v-no-fluid.closed",
         {"fluid_in": "absent", "control_in": "high"}, "none",
         {"fluid_out": "absent", "pressure_out": "absent"}, "closed"),
        ("gas-regulator.v-stuck-closed.stuck", {}, "stuck_closed",
         {"fluid_out": "absent", "pressure_out": "absent"}, "stuck"),
        ("gas-regulator.v-stuck-open.stuck", {}, "stuck_open",
         {"fluid_out": "normal", "pressure_out": "normal"}, "stuck"),
    ]:
        rows.append(DraftRow(row_id=row_id, inputs=inputs, fault=fault,
                             outputs=outputs, next_state=next_state,
                             source_pages=[43]))
    return DraftArchetype(
        archetype_id="gas-regulator", human_name="gas regulator",
        aliases=["gas regulator", "pressure regulator"],
        ports=[DraftPort(name="fluid_in", direction="in", domain="material",
                         source_page=43),
               DraftPort(name="control_in", direction="in", domain="signal",
                         source_page=43),
               DraftPort(name="fluid_out", direction="out", domain="material",
                         source_page=43),
               DraftPort(name="pressure_out", direction="out",
                         domain="material", source_page=43, required=False)],
        states=["open", "closed", "stuck", "unknown"],
        parameters=[DraftParameter(name="set_pressure", unbound=True,
                                   unbound_reason="no nameplate value for "
                                                  "the regulator in the manual")],
        propagation_table=rows,
        failure_modes=[DraftFailureMode(
            name="stuck_closed", fault_state="stuck_closed",
            discriminating_test="gas_flow_check (p43 case 8)",
            expected_trace=["gas-valve.fluid_out -> absent",
                            "mig-gun.gas_out -> absent",
                            "welding-arc.weld_quality -> degraded"],
            source_pages=[43])],
        version="0.1.0",
        # The regulator refines the valve for the Gas Valve: the sandbox
        # removes the valve for the duration of the evaluation, so the
        # §8 unique-match rule can bind the Gas Valve to this draft.
        # pressure_out is optional — the Gas Valve carries no pressure
        # port, so a required pressure_out would block the binding.
        supersedes=["valve"],
        intended_entity="Gas Valve")


# ---------------------------------------------------------------------------
# The LLM slot — wired to the gateway (readiness item C)
# ---------------------------------------------------------------------------

def test_draft_archetype_without_llm_uses_the_gateway(monkeypatch):
    """No explicit LLM -> the gateway-backed DraftLLM, stubbed here (never
    a live call). The stub records the call shape: task "drafting", the
    DraftArchetype JSON schema, temperature 0.0."""
    import backend.simulation.drafting as drafting_module

    seen: dict = {}

    def _stub(task, messages, json_schema=None, temperature=None,
              max_tokens=None):
        seen.update(task=task, messages=messages, json_schema=json_schema,
                    temperature=temperature, max_tokens=max_tokens)
        return (json.dumps({"archetype_id": "stub", "human_name": "stub",
                            "states": ["ok"], "version": "0.1.0"}), False)

    monkeypatch.setattr(drafting_module.gateway, "complete_result", _stub)
    brief = _brief()
    result = draft_archetype(brief.model_dump(mode="json"), llm=None)
    assert result["archetype_id"] == "stub"
    assert seen["task"] == "drafting"
    assert seen["temperature"] == 0.0
    assert seen["json_schema"]["properties"]["propagation_table"]
    assert "Drafting brief" in seen["messages"][0]["content"]


def test_draft_archetype_with_a_stub_llm_returns_its_output():
    brief = _brief()
    stub = lambda b: {"archetype_id": "stub", "human_name": "stub",
                      "states": ["ok"], "version": "0.1.0"}
    assert draft_archetype(brief.model_dump(mode="json"), llm=stub)["archetype_id"] == "stub"


# ---------------------------------------------------------------------------
# The drafting brief (v3 §7.2)
# ---------------------------------------------------------------------------

def test_brief_has_the_case_facts_schema_nearest_and_gold():
    brief = _brief("omni-05-does-not-function")
    assert brief.case.case_id == "omni-05-does-not-function"
    assert "Does Not Function" in brief.case.symptom
    assert len(brief.case.causes) == 5
    # Only the structured facts for the case, not the manual.
    assert brief.facts.quantities or brief.facts.failure_modes
    # The archetype schema as JSON schema for structured output.
    assert brief.archetype_json_schema["properties"]["propagation_table"]
    # The nearest existing archetypes, top 2.
    assert len(brief.nearest_archetypes) == 2
    assert all("similarity" in n for n in brief.nearest_archetypes)
    # The 3 hand-written archetype instances as gold examples.
    assert len(brief.gold_examples) == 3
    assert {g["archetype_id"] for g in brief.gold_examples} == \
        {"switch", "converter", "transmission"}
    # The hard constraints list.
    assert any("unbound" in c for c in brief.constraints)
    assert any("declared ports" in c for c in brief.constraints)
    # Mode B for the first two Layer 2 instances (calibration).
    assert brief.drafting_mode == "B"


def test_brief_for_an_unknown_case_raises():
    with pytest.raises(ValueError, match="no registry case"):
        build_drafting_brief("omni-99-no-such-case")


# ---------------------------------------------------------------------------
# Check 1 — schema validation (one repair retry, then reject)
# ---------------------------------------------------------------------------

def test_schema_check_passes_a_valid_draft():
    from backend.simulation.drafting import check_schema
    draft, check = check_schema(_gas_regulator_draft().model_dump(mode="json"))
    assert draft is not None
    assert check.passed


def test_schema_check_repairs_a_trailing_comma_then_passes():
    from backend.simulation.drafting import check_schema
    raw = json.dumps(_gas_regulator_draft().model_dump(mode="json"))
    raw = raw[:-1] + ",}"  # trailing comma before the closing brace
    draft, check = check_schema(raw)
    assert draft is not None
    assert check.passed
    assert "repair retry" in check.reasons[0]


def test_schema_check_rejects_malformed_output():
    from backend.simulation.drafting import check_schema
    draft, check = check_schema("{not json at all")
    assert draft is None
    assert not check.passed
    assert check.outcome == "reject"


# ---------------------------------------------------------------------------
# Check 2 — value provenance (quarantine, never silently drop)
# ---------------------------------------------------------------------------

def test_provenance_unbound_parameter_passes():
    brief = _brief("omni-08-porosity")
    draft = _gas_regulator_draft()
    check = check_value_provenance(draft, brief)
    assert check.passed
    assert check.outcome == "pass"


def test_provenance_value_outside_the_allow_list_is_quarantined_not_rejected():
    brief = _brief("omni-08-porosity")
    draft = _gas_regulator_draft()
    draft.parameters.append(DraftParameter(
        name="set_pressure", value="999 psi", source_page=43,
        source_field="nameplate"))
    check = check_value_provenance(draft, brief)
    # The draft continues to review WITH the flag — this check never rejects.
    assert check.passed
    assert check.outcome == "flag"
    assert check.evidence["quarantined_fields"] == ["set_pressure"]
    assert "quarantined" in check.reasons[0]


# ---------------------------------------------------------------------------
# Check 3 — port-signature collision
# ---------------------------------------------------------------------------

def test_collision_identical_signature_with_no_distinguishing_evidence_rejects():
    # A draft that copies the switch's ports exactly, with no new
    # parameter or failure mode, is indistinguishable from the relay.
    draft = _switch_draft()
    # The gold-example conversion marks the parameters unbound but keeps
    # their names, so strip them to leave no distinguishing evidence.
    draft.parameters = []
    draft.failure_modes = []
    check = check_port_signature_collision(draft)
    assert not check.passed
    assert check.outcome == "reject"
    assert "indistinguishable from switch" in check.reasons[0]


def test_collision_distinct_signature_passes():
    draft = _gas_regulator_draft()
    check = check_port_signature_collision(draft)
    assert check.passed


def test_collision_identical_signature_with_distinguishing_mode_passes():
    # A draft with the switch's ports but a failure mode the switch does
    # not have is distinguishable (the mode is the evidence).
    draft = _switch_draft()
    draft.failure_modes.append(DraftFailureMode(
        name="welded_contacts", fault_state="stuck_closed",
        discriminating_test="trigger_check (p43 case 5)",
        expected_trace=[], source_pages=[43]))
    check = check_port_signature_collision(draft)
    assert check.passed


# ---------------------------------------------------------------------------
# Check 4 — template executability
# ---------------------------------------------------------------------------

def test_executability_valid_rows_pass():
    draft = _gas_regulator_draft()
    check = check_template_executability(draft)
    assert check.passed


def test_executability_undeclared_condition_field_rejects():
    draft = _gas_regulator_draft()
    draft.propagation_table[0].inputs["not_a_port"] = "normal"
    check = check_template_executability(draft)
    assert not check.passed
    assert check.outcome == "reject"
    assert "undeclared field" in check.reasons[0]


def test_executability_undeclared_effect_rejects():
    draft = _gas_regulator_draft()
    draft.propagation_table[0].outputs["not_an_output"] = "normal"
    check = check_template_executability(draft)
    assert not check.passed
    assert "undeclared output" in check.reasons[0]


# ---------------------------------------------------------------------------
# Check 5 — oracle run (sandbox test twin)
# ---------------------------------------------------------------------------

def test_archetype_to_draft_preserves_parameter_domains():
    # A boolean parameter (groove_matches) must stay boolean in the
    # draft: without its domain the sandbox infers "qualitative", and
    # the rows referencing groove_matches: True/False never match any
    # enumerated combo, so the feeder outputs unknown and the oracle
    # fails O2 on the groove row and on the downstream gun row.
    draft = archetype_to_draft(ARCHETYPES["transmission"],
                               intended_entity="Wire Feed Mechanism")
    by_name = {p.name: p for p in draft.parameters}
    assert by_name["groove_matches"].domain == "boolean"
    assert by_name["tension"].domain == "qualitative"
    assert by_name["groove_matches"].unbound
    # And the nominal operating point must survive too: without it the
    # compiler cannot seed the nominal state, so a fault on a downstream
    # component (the gun, case 1(c)) leaves the feeder's tension/groove
    # unset and the feeder reads unknown instead of normal.
    assert by_name["tension"].nominal_default == "normal"
    assert by_name["groove_matches"].nominal_default is True


def test_oracle_check_passes_when_the_draft_reproduces_its_case():
    # The A4 transmission draft on case 1 (4/4): the sandbox twin binds
    # the feeder to the drafted transmission and the oracle passes O2.
    brief = _brief("omni-01-wire-not-feeding")
    draft = archetype_to_draft(ARCHETYPES["transmission"],
                               intended_entity="Wire Feed Mechanism")
    check = check_oracle(draft, brief)
    assert check.passed, check.reasons
    assert check.evidence["oracle_vectors"]["headline"]["passed"] == 4


def test_oracle_check_rejects_when_the_draft_fails_o2_on_its_case():
    # The same A4 draft with the tension-low row's output broken (the
    # feeder passes normal instead of low) does not reproduce case 1.
    brief = _brief("omni-01-wire-not-feeding")
    draft = archetype_to_draft(ARCHETYPES["transmission"],
                               intended_entity="Wire Feed Mechanism")
    for row in draft.propagation_table:
        if row.row_id == "transmission.t2-low.slipping":
            row.outputs["material_out"] = "normal"
    check = check_oracle(draft, brief)
    assert not check.passed
    assert check.outcome == "reject"
    assert check.evidence["oracle_vectors"]["o2_failures"]


# ---------------------------------------------------------------------------
# Check 6 — binding check
# ---------------------------------------------------------------------------

def test_binding_check_binds_the_intended_entity_and_no_other():
    brief = _brief("omni-08-porosity")
    draft = _gas_regulator_draft()
    check = check_binding(draft, brief)
    assert check.passed, check.reasons
    assert check.evidence["bindings"]["Gas Valve"] == "gas-regulator"


def test_binding_check_rejects_a_draft_with_no_intended_entity():
    brief = _brief("omni-08-porosity")
    draft = _gas_regulator_draft()
    draft.intended_entity = None
    check = check_binding(draft, brief)
    assert not check.passed
    assert check.outcome == "reject"


# ---------------------------------------------------------------------------
# The full gate + the sandbox
# ---------------------------------------------------------------------------

def test_full_gate_makes_the_gas_regulator_a_candidate():
    brief = _brief("omni-08-porosity")
    draft, result = run_pre_review_gate(
        _gas_regulator_draft().model_dump(mode="json"), brief,
        draft_id="draft-gas-regulator-1")
    assert draft is not None
    assert result.verdict == "candidate", [c.reasons for c in result.checks]
    assert [c.check for c in result.checks] == [
        "1-schema", "2-value-provenance", "3-port-signature-collision",
        "4-template-executability", "5-oracle", "6-binding"]
    assert result.quarantined_fields == []


def test_full_gate_rejects_a_colliding_draft_before_the_oracle():
    brief = _brief("omni-05-does-not-function")
    draft = _switch_draft()
    draft.parameters = []
    draft.failure_modes = []
    _draft, result = run_pre_review_gate(
        draft.model_dump(mode="json"), brief, draft_id="draft-collision-1")
    assert result.verdict == "rejected"
    assert any(c.check == "3-port-signature-collision" and
               c.outcome == "reject" for c in result.checks)
    # The oracle and binding checks never ran (a rejection on 3 stops the gate).
    assert [c.check for c in result.checks] == [
        "1-schema", "2-value-provenance", "3-port-signature-collision",
        "4-template-executability"]


def test_sandbox_registers_the_draft_only_inside_the_context():
    draft = _gas_regulator_draft()
    assert "gas-regulator" not in ARCHETYPES
    with sandbox_archetype_library(draft) as archetype:
        assert ARCHETYPES["gas-regulator"] is archetype
        assert archetype.version == "0.1.0"
    assert "gas-regulator" not in ARCHETYPES


def test_sandbox_supersedes_and_restores_the_replaced_archetype():
    # A draft that refines an existing archetype has that archetype
    # removed for the duration of the evaluation (so the §8 unique-match
    # rule can bind the intended entity to the draft) and restored on
    # exit, so the approved library is never changed by an evaluation.
    draft = _gas_regulator_draft()
    assert draft.supersedes == ["valve"]
    assert "valve" in ARCHETYPES
    with sandbox_archetype_library(draft):
        assert "gas-regulator" in ARCHETYPES
        assert "valve" not in ARCHETYPES
    assert "gas-regulator" not in ARCHETYPES
    assert "valve" in ARCHETYPES


def test_sandbox_rejects_a_supersede_of_an_unknown_archetype():
    draft = _gas_regulator_draft()
    draft.supersedes = ["no-such-archetype"]
    with pytest.raises(ValueError, match="supersedes"):
        with sandbox_archetype_library(draft):
            pass


def test_candidate_version_is_0x_not_an_approved_version():
    draft = _gas_regulator_draft()
    assert draft.version.startswith("0.")
    assert draft.authoring_layer == "layer2-llm-drafted"


# ---------------------------------------------------------------------------
# Authorization states (enforced in code)
# ---------------------------------------------------------------------------

def test_authorization_happy_path_and_approval_record():
    auth = DraftAuthorization(draft_id="draft-gas-regulator-1")
    assert auth.state == "draft"
    auth.transition("candidate")
    auth.transition("in review")
    auth.transition("approved", reviewer="Nishant",
                    checklist_hash="abc123def456")
    assert auth.state == "approved"
    assert auth.approval is not None
    assert auth.approval.reviewer == "Nishant"
    assert auth.approval.checklist_hash == "abc123def456"
    assert auth.approval.version == "0.1.0"
    assert auth.approval.approvers == ["Nishant"]
    assert len(auth.history) == 3


def test_authorization_illegal_transition_raises():
    auth = DraftAuthorization(draft_id="draft-x")
    with pytest.raises(ValueError, match="illegal draft transition"):
        auth.transition("approved")  # draft -> approved is not allowed


def test_authorization_approve_without_a_record_raises():
    auth = DraftAuthorization(draft_id="draft-x")
    auth.transition("candidate")
    auth.transition("in review")
    with pytest.raises(ValueError, match="approval record"):
        auth.transition("approved")  # no reviewer / checklist hash


def test_authorization_states_are_the_six_from_the_design():
    assert set(DRAFT_STATES) == {"draft", "candidate", "in review",
                                 "approved", "rejected", "needs revision"}


# ---------------------------------------------------------------------------
# The bounded revision loop (max two rounds)
# ---------------------------------------------------------------------------

def test_revision_loop_returns_a_revision_brief_for_flagged_fields_only():
    brief = _brief("omni-08-porosity")
    draft = _gas_regulator_draft()
    draft.parameters.append(DraftParameter(
        name="set_pressure", value="999 psi", source_page=43))
    _draft, result = run_pre_review_gate(
        draft.model_dump(mode="json"), brief, draft_id="draft-rev-1")
    assert result.verdict == "candidate"  # check 2 flags, does not reject
    loop = DraftingLoop(draft_id="draft-rev-1")
    revision = loop.request_revision(result, reviewer="Nishant")
    assert revision["revision_round"] == 1
    assert revision["max_revision_rounds"] == 2
    assert "set_pressure" in str(revision["flagged_fields"])
    assert "ONLY the flagged fields" in revision["instruction"]


def test_revision_loop_caps_at_two_rounds_then_a_human_writes_it():
    loop = DraftingLoop(draft_id="draft-rev-2")
    from backend.simulation.drafting import PreReviewGateResult
    empty = PreReviewGateResult(draft_id="draft-rev-2", verdict="candidate")
    loop.request_revision(empty, reviewer="Nishant")
    loop.request_revision(empty, reviewer="Nishant")
    with pytest.raises(ValueError, match="Layer 1 fallback"):
        loop.request_revision(empty, reviewer="Nishant")


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
# Review view (v3 §7.4, five parts)
# ---------------------------------------------------------------------------

def _candidate_gate_result():
    brief = _brief("omni-08-porosity")
    _draft, result = run_pre_review_gate(
        _gas_regulator_draft().model_dump(mode="json"), brief,
        draft_id="draft-gas-regulator-1")
    return result


def test_review_view_has_all_five_parts():
    result = _candidate_gate_result()
    html_doc = render_review_view(
        _gas_regulator_draft(), result,
        brief=_brief("omni-08-porosity"),
        nearest_archetype_id="valve",
        trace_messages=["Injected fault at gas-valve.fault = 'stuck_closed'.",
                        "Gas Valve.fluid_out 'normal' → 'absent'"],
        oracle_rows=[{"row_id": "oracle-row-40",
                      "cause": "Shielding gas bottle is empty.",
                      "checks": {"O2": {"passed": True, "reasons": []}}}],
        binding_result={"Gas Valve": "gas-regulator",
                        "Shielding Gas Bottle": "storage"})
    assert "1. One-page summary" in html_doc
    assert "2. Diff against the nearest existing archetype" in html_doc
    assert "3. Trace playback" in html_doc
    assert "4. Oracle result + binding result" in html_doc
    assert "5. Per-field actions + review checklist" in html_doc
    # The diff names the nearest archetype and the delta (pressure_out).
    assert "valve" in html_doc
    assert "pressure_out" in html_doc
    # The trace playback and the oracle row are present.
    assert "stuck_closed" in html_doc
    assert "oracle-row-40" in html_doc
    # Per-field actions and the checklist are form controls, not a single button.
    assert "name='field:set_pressure'" in html_doc
    assert "name='checklist'" in html_doc
    assert html_doc.count("type='checkbox'") == len(REVIEW_CHECKLIST)
    # Self-contained: no external script or stylesheet.
    assert "<script src=" not in html_doc
    assert 'href="http' not in html_doc


def test_record_review_requires_the_full_checklist_to_approve():
    review = ReviewRecord(
        draft_id="draft-gas-regulator-1", reviewer="Nishant",
        checklist_ticked=REVIEW_CHECKLIST[:3],
        decision="approved")
    with pytest.raises(ValueError, match="checklist is incomplete"):
        record_review(review)


def test_record_review_with_a_full_checklist_returns_the_record():
    review = ReviewRecord(
        draft_id="draft-gas-regulator-1", reviewer="Nishant",
        field_reviews=[FieldReview(field="set_pressure", action="approve")],
        checklist_ticked=list(REVIEW_CHECKLIST),
        decision="approved", decision_reason="All checks pass; the diff is real.")
    record = record_review(review)
    assert record["decision"] == "approved"
    assert record["checklist_ticked"] == len(REVIEW_CHECKLIST)
    assert len(record["checklist_hash"]) == 16
    assert record["field_reviews"][0]["field"] == "set_pressure"


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
