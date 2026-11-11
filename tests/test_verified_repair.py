"""Verified-repair loop: diagnosis -> twin experiment -> VALIDATED / REJECTED.

The feature only works if every gate holds:
1. Extraction emits states, simulation rules (rule-11 contract) and
   failure-mode entity links, and they survive into the universal model.
2. Chunk quarantine vetoes a rule whose path names an entity or state the
   chunk did not extract -- per rule, never per chunk.
3. Code-first integration rewrites rule paths to canonical ids, unique-only
   with same-chunk preference, and resolves failure-mode entity names to
   canonical ids; ambiguity stays unresolved, never guessed.
4. The compiler coerces string state values ("true", "120") to JSON types
   so rule conditions can match, and carries rule evidence into the twin.
5. The verification builder always produces >= 1 assertion (the vacuous
   path in run_experiment must never be reachable), and its pre-flight
   gates distinguish "cannot evaluate" (unavailable) from "evaluated and
   failed" (rejected).
6. Expert seed rules resolve by name at compile time, unique match only.
"""
from backend.knowledge import component_extraction as ce
from backend.knowledge.code_first_integration import build_code_first_payload
from backend.knowledge.component_extraction import _universal_model_from_result
from backend.knowledge.machine_model import (
    MachineEntity, MachineState, UniversalMachineModel,
)
from backend.knowledge.schema import MachineKnowledgeExtractionResult
from backend.knowledge.validation import quarantine_machine_model
from backend.simulation.compiler import _coerce_state_value, compile_machine_model
from backend.simulation.expert_rules import apply_expert_seed
from backend.simulation.schema import DigitalTwinDefinition
from backend.simulation.verification import build_verification_experiment, run_verified_repair


# ---------------------------------------------------------------------------
# 1. Extraction contract
# ---------------------------------------------------------------------------

def test_prompts_carry_simulation_rule_contract():
    assert "simulation_rules" in ce._EXTRACTION_PROMPT
    assert "simulation_rules" in ce._BATCH_EXTRACTION_PROMPT
    for prompt in (ce._EXTRACTION_PROMPT, ce._BATCH_EXTRACTION_PROMPT):
        assert "ONLY when the text explicitly defines" in prompt
        assert "Never infer a rule" in prompt
        assert "<Entity Name>.<state name>" in prompt


def test_universal_model_carries_rules_and_failure_mode_entity_names():
    result = MachineKnowledgeExtractionResult(
        entities=[
            {"id": "entity:p1", "name": "Feed Tensioner", "entity_type": "component"},
            {"id": "entity:p2", "name": "Welding Wire", "entity_type": "component"},
        ],
        states=[
            {"entity_id": "entity:p1", "name": "tension_ok", "value": "true"},
            {"entity_id": "entity:p2", "name": "feeding", "value": "true"},
        ],
        simulation_rules=[
            {"id": "rule:1", "name": "Tension fault",
             "conditions": {"Feed Tensioner.tension_ok": False},
             "effects": {"Welding Wire.feeding": False}, "description": "", "evidence": []},
        ],
        failure_modes=[
            {"id": "failure:1", "name": "Wire Does Not Feed",
             "symptoms": ["Wire Feed Motor Runs but Wire Does Not Feed Properly"],
             "entity_ids": ["entity:p1"], "evidence": []},
        ],
    )
    model = _universal_model_from_result(result)
    assert len(model.simulation_rules) == 1
    assert model.simulation_rules[0]["conditions"] == {"Feed Tensioner.tension_ok": False}
    assert model.failure_modes[0].entity_names == ["Feed Tensioner"]


# ---------------------------------------------------------------------------
# 2. Chunk quarantine veto
# ---------------------------------------------------------------------------

def _quarantine_model():
    return UniversalMachineModel(
        entities=[
            MachineEntity(id="entity:feed-tensioner", name="Feed Tensioner", entity_type="component"),
            MachineEntity(id="entity:welding-wire", name="Welding Wire", entity_type="component"),
        ],
        states=[
            MachineState(entity_id="entity:feed-tensioner", name="tension_ok", value="true"),
            MachineState(entity_id="entity:welding-wire", name="feeding", value="true"),
        ],
        simulation_rules=[
            {"id": "rule:good", "name": "Good rule",
             "conditions": {"Feed Tensioner.tension_ok": False},
             "effects": {"Welding Wire.feeding": False}},
            {"id": "rule:bad-entity", "name": "Unknown entity",
             "conditions": {"Ghost Component.tension_ok": False},
             "effects": {"Welding Wire.feeding": False}},
            {"id": "rule:bad-state", "name": "Unknown state",
             "conditions": {"Feed Tensioner.pressure": False},
             "effects": {"Welding Wire.feeding": False}},
            {"id": "rule:no-effects", "name": "No effects",
             "conditions": {"Feed Tensioner.tension_ok": False}, "effects": {}},
        ],
    )


def test_quarantine_vetoes_bad_rules_per_rule_not_per_chunk():
    kept, quarantined = quarantine_machine_model(_quarantine_model())
    assert [r["id"] for r in kept.simulation_rules] == ["rule:good"]
    assert len(kept.entities) == 2 and len(kept.states) == 2
    reasons = [q["reason"] for q in quarantined if q["fact_type"] == "simulation_rule"]
    assert len(reasons) == 3
    assert any("Ghost Component" in r for r in reasons)
    assert any("pressure" in r for r in reasons)
    assert any("missing_conditions_or_effects" in r for r in reasons)


def test_quarantine_accepts_canonical_id_paths_after_integration():
    # integrate_revision_knowledge re-runs the veto on the integrated model,
    # where rule paths already carry canonical ids, not names. An id-pathed
    # rule for a kept entity with a known state must survive; a bad id must
    # still quarantine. (Caught by the verify script's leg E, not the unit
    # tests: the code-first rule rewrite passed, then integration dropped it.)
    model = UniversalMachineModel(
        entities=[
            MachineEntity(id="entity:feed-tensioner", name="Feed Tensioner", entity_type="component"),
            MachineEntity(id="entity:welding-wire", name="Welding Wire", entity_type="component"),
        ],
        states=[
            MachineState(entity_id="entity:feed-tensioner", name="tension_ok", value="true"),
            MachineState(entity_id="entity:welding-wire", name="feeding", value="true"),
        ],
        simulation_rules=[
            {"id": "rule:id-path", "name": "Id-pathed rule",
             "conditions": {"entity:feed-tensioner.tension_ok": False},
             "effects": {"entity:welding-wire.feeding": False}},
            {"id": "rule:bad-id", "name": "Unknown id",
             "conditions": {"entity:ghost.tension_ok": False},
             "effects": {"entity:welding-wire.feeding": False}},
        ],
    )
    kept, quarantined = quarantine_machine_model(model)
    assert [r["id"] for r in kept.simulation_rules] == ["rule:id-path"]
    assert any(q["reason"] == "simulation_rule_unknown_entity:entity:ghost"
               for q in quarantined if q["fact_type"] == "simulation_rule")


# ---------------------------------------------------------------------------
# 3. Code-first veto + rewrite + failure-mode linking
# ---------------------------------------------------------------------------

def _code_first_context():
    return {
        "universal_entities": [
            {"source_id": "e1", "name": "Feed Tensioner", "entity_type": "component",
             "properties": {}, "ports": [], "states": [], "evidence": [], "source_chunk_id": "chunk-a"},
            {"source_id": "e2", "name": "Welding Wire", "entity_type": "component",
             "properties": {}, "ports": [], "states": [], "evidence": [], "source_chunk_id": "chunk-a"},
            {"source_id": "e3", "name": "Fan", "entity_type": "component",
             "properties": {"part_number": "44"}, "ports": [], "states": [], "evidence": [], "source_chunk_id": "chunk-b"},
            {"source_id": "e4", "name": "Fan", "entity_type": "component",
             "properties": {"part_number": "53"}, "ports": [], "states": [], "evidence": [], "source_chunk_id": "chunk-b"},
        ],
        "universal_relations": [],
        "universal_facts": [
            {"source_id": "s1", "fact_type": "state",
             "payload": {"entity_id": "entity:feed-tensioner", "name": "tension_ok", "value": True},
             "source_chunk_id": "chunk-a"},
            {"source_id": "s2", "fact_type": "state",
             "payload": {"entity_id": "entity:welding-wire", "name": "feeding", "value": True},
             "source_chunk_id": "chunk-a"},
            {"source_id": "r1", "fact_type": "simulation_rule",
             "payload": {"id": "rule:1", "name": "Tension fault",
                         "conditions": {"Feed Tensioner.tension_ok": False},
                         "effects": {"Welding Wire.feeding": False},
                         "description": "p42", "evidence": [{"fact": "p42 quote"}]},
             "source_chunk_id": "chunk-a"},
            {"source_id": "r2", "fact_type": "simulation_rule",
             "payload": {"id": "rule:2", "name": "Ambiguous fan rule",
                         "conditions": {"Fan.running": False},
                         "effects": {"Welding Wire.feeding": False}},
             "source_chunk_id": "chunk-b"},
            {"source_id": "f1", "fact_type": "failure_mode",
             "payload": {"id": "failure:1", "name": "Wire Does Not Feed",
                         "symptoms": ["Wire Feed Motor Runs but Wire Does Not Feed Properly"],
                         "entity_names": ["Feed Tensioner"], "evidence": []},
             "source_chunk_id": "chunk-a"},
        ],
        "behaviors": [], "legacy_components": [], "legacy_relationships": [],
        "procedures": [], "failure_modes": [],
        "schematic_nodes": [], "schematic_edges": [], "expert_knowledge": [],
    }


def test_code_first_rewrites_rule_paths_and_links_failure_modes():
    state = build_code_first_payload(_code_first_context())
    payload = state["payload"]
    assert len(payload["simulation_rules"]) == 1
    rule = payload["simulation_rules"][0]
    assert rule["conditions"] == {"entity:feed-tensioner.tension_ok": False}
    assert rule["effects"] == {"entity:welding-wire.feeding": False}
    assert rule["evidence"] == [{"fact": "p42 quote"}]
    # The ambiguous Fan rule stays unresolved, never guessed.
    assert len(payload["simulation_rules"]) == 1
    assert any(u["source_id"] == "r2" and "not_uniquely_resolvable" in u["reason"]
               for u in payload["unresolved_facts"])
    # Failure-mode entity name resolves to the canonical id.
    assert len(payload["failure_modes"]) == 1
    assert payload["failure_modes"][0]["entity_ids"] == ["entity:feed-tensioner"]


def test_state_with_opaque_chunk_local_id_resolves_via_stamped_entity_name():
    # The LLM sometimes invents ids like "entity:p1" (the "entity:p3" class
    # of failure behind the v7 relation fallback). A state referencing one
    # must still resolve at integration, via the entity name stamped at
    # extraction -- otherwise the rule veto loses its known fields and the
    # rule dies too. This is the regression for the verify script's leg E.
    result = MachineKnowledgeExtractionResult(
        entities=[
            {"id": "entity:p1", "name": "Feed Tensioner", "entity_type": "component"},
            {"id": "entity:p2", "name": "Welding Wire", "entity_type": "component"},
        ],
        states=[
            {"entity_id": "entity:p1", "name": "tension_ok", "value": "true"},
            {"entity_id": "entity:p2", "name": "feeding", "value": "true"},
        ],
        simulation_rules=[
            {"id": "rule:1", "name": "Tension fault",
             "conditions": {"Feed Tensioner.tension_ok": False},
             "effects": {"Welding Wire.feeding": False}},
        ],
    )
    model = _universal_model_from_result(result)
    assert model.states[0].properties["entity_name"] == "Feed Tensioner"
    # Simulate the persisted fact rows the context builder produces.
    context = {
        "universal_entities": [
            {"source_id": "e1", "name": "Feed Tensioner", "entity_type": "component",
             "properties": {}, "ports": [], "states": [], "evidence": [], "source_chunk_id": "chunk-a"},
            {"source_id": "e2", "name": "Welding Wire", "entity_type": "component",
             "properties": {}, "ports": [], "states": [], "evidence": [], "source_chunk_id": "chunk-a"},
        ],
        "universal_relations": [],
        "universal_facts": [
            {"source_id": "s1", "fact_type": "state",
             "payload": model.states[0].model_dump(mode="json"), "source_chunk_id": "chunk-a"},
            {"source_id": "s2", "fact_type": "state",
             "payload": model.states[1].model_dump(mode="json"), "source_chunk_id": "chunk-a"},
            {"source_id": "r1", "fact_type": "simulation_rule",
             "payload": dict(model.simulation_rules[0]), "source_chunk_id": "chunk-a"},
        ],
        "behaviors": [], "legacy_components": [], "legacy_relationships": [],
        "procedures": [], "failure_modes": [],
        "schematic_nodes": [], "schematic_edges": [], "expert_knowledge": [],
    }
    payload = build_code_first_payload(context)["payload"]
    assert {s["entity_id"] for s in payload["states"]} == {
        "entity:feed-tensioner", "entity:welding-wire"}
    assert len(payload["simulation_rules"]) == 1
    assert payload["simulation_rules"][0]["conditions"] == {
        "entity:feed-tensioner.tension_ok": False}


# ---------------------------------------------------------------------------
# 4. Compiler coercion + evidence pass-through
# ---------------------------------------------------------------------------

def test_coerce_state_value():
    assert _coerce_state_value("true") is True
    assert _coerce_state_value("FALSE") is False
    assert _coerce_state_value("120") == 120
    assert _coerce_state_value("21.5") == 21.5
    assert _coerce_state_value("off") == "off"
    assert _coerce_state_value(True) is True


def test_compiler_coerces_string_states_and_carries_rule_evidence():
    model = UniversalMachineModel(
        entities=[
            MachineEntity(id="entity:feed-tensioner", name="Feed Tensioner", entity_type="component"),
            MachineEntity(id="entity:welding-wire", name="Welding Wire", entity_type="component"),
        ],
        states=[
            MachineState(entity_id="entity:feed-tensioner", name="tension_ok", value="true"),
            MachineState(entity_id="entity:welding-wire", name="feeding", value="true"),
        ],
        simulation_rules=[
            {"id": "rule:1", "name": "Tension fault",
             "conditions": {"entity:feed-tensioner.tension_ok": False},
             "effects": {"entity:welding-wire.feeding": False},
             "description": "p42", "source_ids": ["chunk-a"],
             "evidence": [{"fact": "Feed Tensioner is too tight.", "page": 42}]},
        ],
    )
    definition, warnings = compile_machine_model(model, name="test twin")
    tensioner = next(c for c in definition.components if c.id == "entity:feed-tensioner")
    assert tensioner.initial_state == {"tension_ok": True}
    assert len(definition.transitions) == 1
    assert definition.transitions[0].source_ids == ["chunk-a"]
    assert definition.transitions[0].evidence[0]["page"] == 42


# ---------------------------------------------------------------------------
# 5. Verification builder + pre-flight gates
# ---------------------------------------------------------------------------

def _wire_feed_definition(extra_transitions=None, initial_signals=None):
    transitions = [
        {"id": "seed:feed-tension-too-tight", "name": "Feed tensioner too tight stops wire feed",
         "conditions": {"entity:feed-tensioner.tension_ok": False},
         "effects": {"entity:welding-wire.feeding": False}},
        {"id": "seed:feed-tension-ok-restores-feed", "name": "Correct feed tension restores wire feed",
         "conditions": {"entity:feed-tensioner.tension_ok": True},
         "effects": {"entity:welding-wire.feeding": True}},
    ] + list(extra_transitions or [])
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
        ],
        initial_signals=dict(initial_signals or {}),
        transitions=transitions,
    )


def _initial_state(definition):
    return {"signals": dict(definition.initial_signals),
            "components": {c.id: dict(c.initial_state) for c in definition.components}}


def test_verified_repair_validates_the_right_repair():
    definition = _wire_feed_definition()
    outcome = run_verified_repair(definition, _initial_state(definition),
                                  fault_component_id="entity:feed-tensioner",
                                  repair_name="Loosen Feed Tensioner")
    assert outcome.available is True
    assert outcome.verdict == "validated"
    assert outcome.result is not None
    assert len(outcome.result.checks) == 1
    assert outcome.result.checks[0]["passed"] is True
    assert outcome.result.checks[0]["path"] == "components.entity:welding-wire.feeding"
    assert outcome.result.intervention_trace  # a real transition fired
    assert [r["id"] for r in outcome.fault_rules] == ["seed:feed-tension-too-tight"]
    assert [r["id"] for r in outcome.recovery_rules] == ["seed:feed-tension-ok-restores-feed"]


def test_verified_repair_rejects_when_a_second_fault_holds_the_symptom():
    # A second fault (clogged liner) keeps feeding false even after the
    # tension is fixed: the twin propagates the repair and the symptom
    # still does not recover, so the verdict is REJECTED, not unavailable.
    blocker = {"id": "rule:liner-blocks-feed", "name": "Clogged liner blocks feed",
               "conditions": {"entity:feed-tensioner.tension_ok": True},
               "effects": {"entity:welding-wire.feeding": False}}
    definition = _wire_feed_definition(extra_transitions=[blocker])
    outcome = run_verified_repair(definition, _initial_state(definition),
                                  fault_component_id="entity:feed-tensioner")
    assert outcome.available is True
    assert outcome.verdict == "rejected"
    assert outcome.result.checks[0]["passed"] is False


def test_verified_repair_unavailable_with_no_transitions():
    definition = _wire_feed_definition()
    definition.transitions = []
    outcome = run_verified_repair(definition, _initial_state(definition),
                                  fault_component_id="entity:feed-tensioner")
    assert outcome.available is False
    assert "no simulation rules" in outcome.reason
    assert outcome.verdict is None


def test_verified_repair_unavailable_when_component_has_no_fault_rule():
    definition = _wire_feed_definition()
    outcome = run_verified_repair(definition, _initial_state(definition),
                                  fault_component_id="entity:wingnut")
    assert outcome.available is False
    assert "No simulation rule" in outcome.reason


def test_verified_repair_unavailable_when_fault_does_not_reproduce_symptom():
    # The fault rule also needs a start command the twin never receives,
    # so the fault state cannot produce the symptom.
    gated_fault = {"id": "rule:gated", "name": "Gated fault",
                   "conditions": {"entity:feed-tensioner.tension_ok": False, "start_command": True},
                   "effects": {"entity:welding-wire.feeding": False}}
    definition = _wire_feed_definition(
        extra_transitions=[gated_fault], initial_signals={"start_command": False})
    # Replace the plain fault rule with the gated one so the builder only
    # sees the gated fault.
    from backend.simulation.schema import TwinTransition
    definition.transitions = [t for t in definition.transitions
                             if t.id != "seed:feed-tension-too-tight"] + [TwinTransition.model_validate(gated_fault)]
    outcome = run_verified_repair(definition, _initial_state(definition),
                                  fault_component_id="entity:feed-tensioner")
    assert outcome.available is False
    assert "did not reproduce the symptom" in outcome.reason


def test_verified_repair_unavailable_when_no_recovery_transition_fires():
    # A recovery rule exists, but it also needs a start command that never
    # arrives, so the intervention fires no transition at all.
    gated_recovery = {"id": "rule:gated-recovery", "name": "Gated recovery",
                      "conditions": {"entity:feed-tensioner.tension_ok": True, "start_command": True},
                      "effects": {"entity:welding-wire.feeding": True}}
    definition = _wire_feed_definition(
        extra_transitions=[gated_recovery], initial_signals={"start_command": False})
    from backend.simulation.schema import TwinTransition
    definition.transitions = [t for t in definition.transitions
                             if t.id != "seed:feed-tension-ok-restores-feed"] + [TwinTransition.model_validate(gated_recovery)]
    outcome = run_verified_repair(definition, _initial_state(definition),
                                  fault_component_id="entity:feed-tensioner")
    assert outcome.available is False
    assert "No recovery transition fired" in outcome.reason


def test_builder_always_produces_an_assertion():
    definition = _wire_feed_definition()
    request, outcome = build_verification_experiment(
        definition, fault_component_id="entity:feed-tensioner")
    assert request is not None
    assert len(request.assertions) >= 1


# ---------------------------------------------------------------------------
# 6. Expert seed resolution
# ---------------------------------------------------------------------------

def test_expert_seed_resolves_unique_names_and_skips_ambiguous_ones():
    model = UniversalMachineModel(
        entities=[
            MachineEntity(id="entity:wingnut", name="Wingnut", entity_type="component"),
            MachineEntity(id="entity:wire-spool", name="Wire Spool", entity_type="component"),
            MachineEntity(id="entity:feed-tensioner-1", name="Feed Tensioner", entity_type="component"),
            MachineEntity(id="entity:feed-tensioner-2", name="Feed Tensioner", entity_type="component"),
            MachineEntity(id="entity:welding-wire", name="Welding Wire", entity_type="component"),
        ],
    )
    seed = {
        "id": "test-seed",
        "states": [
            {"entity": "Wingnut", "name": "tension_ok", "value": True},
            {"entity": "Feed Tensioner", "name": "tension_ok", "value": True},
        ],
        "rules": [
            {"id": "seed:wingnut", "name": "Wingnut rule",
             "conditions": {"Wingnut.tension_ok": False},
             "effects": {"Wire Spool.spinning_freely": True},
             "evidence": [{"fact": "p10 quote", "page": 10}]},
            {"id": "seed:tensioner", "name": "Tensioner rule",
             "conditions": {"Feed Tensioner.tension_ok": False},
             "effects": {"Welding Wire.feeding": False}},
        ],
    }
    merged, warnings, applied = apply_expert_seed(model, seed)
    # Unique names apply; the duplicated Feed Tensioner is skipped, never guessed.
    assert "entity:wingnut.tension_ok" in applied["states_applied"]
    assert "seed:wingnut" in applied["rules_applied"]
    assert applied["rules_applied"] == ["seed:wingnut"]
    assert any("2 entities named 'Feed Tensioner'" in w for w in warnings)
    wingnut_rule = next(r for r in merged.simulation_rules if r["id"] == "seed:wingnut")
    assert wingnut_rule["conditions"] == {"entity:wingnut.tension_ok": False}
    assert wingnut_rule["effects"] == {"entity:wire-spool.spinning_freely": True}
    # The original model is untouched (merge is into a copy).
    assert model.simulation_rules == [] and model.states == []
