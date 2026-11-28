"""Phase 3B tests: archetype tables, compiler v3, binding, RepairAction,
ExpectedTrace, fault_injection wiring, lint, the §8 voltage rule, seed
translation, the Demo Motor Drive regression, and the oracle case-1 gate.

The oracle gate test builds its rows from the reviewed vocabulary
artifact (the parser join is exercised by ``benchmark/run_phase3b_oracle.py``
and by the 3A tests, not duplicated here).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.knowledge.machine_model import MachineEntity, MachinePort, MachineQuantity
from backend.simulation import archetypes as arch
from backend.simulation.archetypes import ARCHETYPES, ARCHETYPE_LIBRARY_VERSION
from backend.simulation.binding import bind_model, parameter_evidence
from backend.simulation.compiler import compile_machine_model
from backend.simulation.edges import load_edge_list_for_product
from backend.simulation.engine import DigitalTwinEngine, demo_definition
from backend.simulation.expert_rules import apply_expert_seed, load_seed_for_product
from backend.simulation.lint import (
    check_settles,
    generate_unknown_rows,
    lint_archetype_table,
    lint_transitions,
)
from backend.simulation.omnipro_proof import (
    build_omnipro_proof_model,
    initial_twin_state,
    observable_paths,
    snapshot_model_for_tournament,
    build_test_models,
)
from benchmark.oracle import (
    OracleRow,
    load_vocabulary,
    run_oracle,
)
from backend.simulation.repair_actions import (
    check_expected_trace,
    find_registry_cause,
    find_repair_action,
    load_registry_causes,
    load_repair_actions,
    parse_trace_messages,
    resolve_action,
)
from backend.simulation.schema import DigitalTwinDefinition
from backend.simulation.tournament import verify_single_cause
from backend.simulation.verification import run_verified_repair_action

REPO = Path(__file__).resolve().parents[1]
VOCAB_PATH = (Path.home() / "workspace" / "goals" /
              "crater-ai-diagnosis-and-machine-model-workflow-improvement" / "files" /
              "oracle_vocabulary_v1.json")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _proof_definition():
    model = build_omnipro_proof_model()
    seed = load_seed_for_product("Harbor Freight", "220", "OMNIPRO")
    assert seed is not None
    model, _warnings, applied = apply_expert_seed(model, seed)
    assert len(applied["rules_applied"]) == 4
    definition, _warnings = compile_machine_model(
        model, name="OMNIPRO 220 — Phase 3B proof twin (test)",
        edge_list=load_edge_list_for_product("Harbor Freight", "220", "OMNIPRO"))
    return definition, model


def _case1_oracle_rows():
    """Case-1 oracle rows, built from the reviewed vocabulary artifact."""
    vocab = load_vocabulary(VOCAB_PATH)
    rows: list[OracleRow] = []
    for index, raw in enumerate(vocab.get("rows") or []):
        if raw.get("case_id") != "omni-01-wire-not-feeding":
            continue
        rows.append(OracleRow(
            row_id=f"oracle-row-{index + 1:02d}",
            case_id=raw["case_id"], case_rank=1,
            symptom="Wire Feed Motor Runs but Wire Does Not Feed Properly",
            cause=raw["cause"], remedy=raw.get("remedy") or "",
            remedy_source="case_registry",
            source_pages=[42], category=raw["category"],
            fault=dict(raw.get("fault") or {}),
            observables=dict(raw.get("observables") or {}),
            tests=dict(raw.get("tests") or {}),
            basis=raw.get("basis") or "", flags=list(raw.get("flags") or [])))
    assert len(rows) == 4
    return rows, vocab


class _FakeState:
    hypotheses: list = []
    current_step = None


# ---------------------------------------------------------------------------
# Archetype tables + lint
# ---------------------------------------------------------------------------

def test_only_a2_a3_a4_populated():
    # 3C supersedes the 3B scope: A1 source, A7 load, A8 conductor, A9
    # storage, A10 valve and A11 interface are now populated as oracle rows
    # required them (cases 5–9). Only A5 sensor and A6 controller stay
    # empty schema entries — A6 is the honest gap (controller outputs stay
    # unknown where the manual gives no logic map), and no oracle row needs
    # a sensor.
    populated = arch.populated_archetypes()
    assert set(populated) == {"switch", "converter", "transmission",
                              "source", "load", "conductor", "storage",
                              "valve", "interface"}
    assert len(ARCHETYPES) == 11
    for archetype_id in ("sensor", "controller"):
        assert ARCHETYPES[archetype_id].propagation_table == []
        assert ARCHETYPES[archetype_id].ports, f"{archetype_id} must declare its ports"


def test_declared_rows_are_valid_and_exclusive_and_total():
    for archetype_id, archetype in arch.populated_archetypes().items():
        rows = list(archetype.propagation_table) + generate_unknown_rows(archetype)
        lint = lint_archetype_table(archetype, rows)
        assert lint["invalid"] == [], f"{archetype_id}: {lint['invalid']}"
        assert lint["conflicts"] == [], f"{archetype_id}: {lint['conflicts'][:2]}"
        assert lint["uncovered"] == [], f"{archetype_id}: {len(lint['uncovered'])} uncovered"


def test_generated_totality_rows_set_unknown_only():
    for archetype in arch.populated_archetypes().values():
        for row in generate_unknown_rows(archetype):
            assert row.next_state == "unknown"
            assert all(value == "unknown" for value in row.outputs.values())
            assert "not a behaviour claim" in " ".join(row.sources)


def test_archetype_library_version_pinned():
    # 3C: the library version bumped when the oracle-fix rows landed
    # (A1 very_low, A4 motion-low, A7/A8 double- and triple-deviation rows).
    assert ARCHETYPE_LIBRARY_VERSION == "3c-1.0.0"
    for archetype in ARCHETYPES.values():
        assert archetype.version == ARCHETYPE_LIBRARY_VERSION


# ---------------------------------------------------------------------------
# Binding (+ §8 voltage rule)
# ---------------------------------------------------------------------------

def test_proof_model_binds_a2_a3_a4_and_leaves_the_rest_unbound():
    # 3C supersedes the 3B scope: the proof twin now has 18 entities and
    # 15 bind (source 2, converter 1, transmission 1, conductor 4, switch 2,
    # storage 2, valve 1, load 1, interface 1). Only the three parts with no
    # archetype signature stay unbound (Wingnut, Feed Tensioner, Welding
    # Wire — no ports recorded, no bound parameters).
    model = build_omnipro_proof_model()
    bindings = bind_model(model)
    by_name = {rec["entity_name"]: rec for rec in bindings.values()}
    assert by_name["Trigger"]["archetype_id"] == "switch"
    assert by_name["Reset Button"]["archetype_id"] == "switch"
    assert by_name["Wire Feed Motor"]["archetype_id"] == "converter"
    assert by_name["Wire Feed Mechanism"]["archetype_id"] == "transmission"
    assert by_name["Input Power Cord"]["archetype_id"] == "source"
    assert by_name["Welder"]["archetype_id"] == "source"
    assert by_name["MIG Gun"]["archetype_id"] == "conductor"
    assert by_name["liner assembly"]["archetype_id"] == "conductor"
    assert by_name["Contact Tip"]["archetype_id"] == "conductor"
    assert by_name["Ground Clamp"]["archetype_id"] == "conductor"
    assert by_name["Wire Spool"]["archetype_id"] == "storage"
    assert by_name["Shielding Gas Bottle"]["archetype_id"] == "storage"
    assert by_name["Gas Valve"]["archetype_id"] == "valve"
    assert by_name["Welding Arc"]["archetype_id"] == "load"
    assert by_name["LCD Display"]["archetype_id"] == "interface"
    for name in ("Wingnut", "Feed Tensioner", "Welding Wire"):
        assert by_name[name]["archetype_id"] is None, name
        assert by_name[name]["unresolved_reason"]
    # The feeder binds on its port signature; tension/groove are
    # archetype nominal defaults, not bound model values.
    feeder = by_name["Wire Feed Mechanism"]
    assert feeder["evidence"]["parameters_bound"] == []


def test_machine_input_voltage_is_never_motor_rating_evidence():
    model = build_omnipro_proof_model()
    motor = next(e for e in model.entities if e.name == "Wire Feed Motor")
    converter = ARCHETYPES["converter"]
    rating = next(p for p in converter.parameters if p.name == "rating")
    # The p7 quantity lives on no component: never evidence.
    p7 = next(q for q in model.quantities if q.name == "Power Input")
    assert parameter_evidence(motor.id, p7, rating) is None
    # Even recorded ON the motor, "input voltage" is a different
    # quantity from the motor rating: the voltage unit family alone
    # does not identify it.
    input_voltage = MachineQuantity(
        id="q:test:input-voltage", entity_id=motor.id, name="input voltage",
        value="120/240", unit="VAC")
    assert parameter_evidence(motor.id, input_voltage, rating) is None
    # A quantity that IS the rating (name match) counts.
    rating_quantity = MachineQuantity(
        id="q:test:rating", entity_id=motor.id, name="rating",
        value="24", unit="VDC")
    evidence = parameter_evidence(motor.id, rating_quantity, rating)
    assert evidence is not None
    assert evidence["quantity_value"] == "24"


def test_binding_coverage_recorded_on_the_compiled_twin():
    definition, _model = _proof_definition()
    coverage = definition.metadata["binding_coverage"]
    assert coverage["entities"] == 18
    assert coverage["bound"] == 15
    assert coverage["by_archetype"] == {"source": 2, "converter": 1,
                                        "transmission": 1, "conductor": 4,
                                        "switch": 2, "storage": 2, "valve": 1,
                                        "load": 1, "interface": 1}
    assert definition.metadata["archetype_library_version"] == "3c-1.0.0"


# ---------------------------------------------------------------------------
# Compiler v3
# ---------------------------------------------------------------------------

def test_demo_motor_drive_still_seven_transitions():
    assert len(demo_definition().transitions) == 7


def test_proof_twin_has_archetype_and_link_transitions():
    definition, _model = _proof_definition()
    ids = [t.id for t in definition.transitions]
    archetype_ids = [i for i in ids if i.startswith("archetype:")]
    link_ids = [i for i in ids if i.startswith("link:")]
    seed_ids = [i for i in ids if i.startswith("seed:")]
    # A2 (9 rows) + A3 (6 rows) + A4 (9 rows) + A1/A7/A8/A9/A10/A11 (3C) +
    # generated totality rows, links (20 edges x values), seed wingnut rules only.
    # 3C: the archetype transition ids carry the component
    # (archetype:{row}@{component}) — multi-instance archetypes (two A1,
    # four A8) no longer collapse in order_transitions' by-id dedup.
    assert archetype_ids, "no archetype transitions emitted"
    assert any(i == "archetype:transmission.t1.feeding@wire-feed-mechanism"
               for i in archetype_ids)
    assert any(i == "archetype:converter.m1.running@wire-feed-motor"
               for i in archetype_ids)
    assert any(i == "archetype:switch.r1.closed@trigger"
               for i in archetype_ids)
    assert any(i == "archetype:source.s1.on@welder" for i in archetype_ids)
    assert any(i == "archetype:source.s1.on@input-power-cord"
               for i in archetype_ids)
    assert any(i == "archetype:conductor.c-nominal.ok@mig-gun"
               for i in archetype_ids)
    assert any(i == "archetype:conductor.c-nominal.ok@liner-assembly"
               for i in archetype_ids)
    assert any(i == "archetype:conductor.c-nominal.ok@contact-tip"
               for i in archetype_ids)
    assert any(i == "archetype:load.l-nominal.normal@welding-arc"
               for i in archetype_ids)
    # 3C edge list v2: the 3B feeder->gun link (with its fault override)
    # and the gun->arc threshold link are retired — the A8/A7 rows carry
    # the gun/tip/arc path now. The wire chain is feeder -> liner -> gun
    # -> tip -> arc, and the arc -> welder link carries the arc observable.
    assert "link:edge:feeder-to-liner:normal" in link_ids
    assert "link:edge:liner-to-gun:normal" in link_ids
    assert "link:edge:gun-to-tip:normal" in link_ids
    assert "link:edge:tip-to-arc:normal" in link_ids
    assert "link:edge:arc-to-welder:normal" in link_ids
    assert "link:edge:motor-to-feeder:normal" in link_ids
    # The tensioner seed rules are subsumed by the A4 rows; only the
    # Wingnut rules survive as rule-11 manual overrides.
    assert set(seed_ids) == {"seed:wingnut-loose-spool-spins",
                             "seed:wingnut-tight-stops-spool-spin"}
    # Deterministic ordering: the canonical topological order (producers
    # before consumers, id tie-break) — NOT plain id order, which puts
    # every archetype row before every link and never settles the chain.
    from backend.simulation.lint import order_transitions
    canonical, _cycles = order_transitions(definition.transitions)
    assert ids == [t.id for t in canonical]
    assert definition.metadata["lint"]["ordered"] is True


def test_seed_translation_recorded():
    definition, _model = _proof_definition()
    translation = definition.metadata["seed_translation"]
    subsumed = {item["rule_id"]: item for item in translation["subsumed"]}
    assert subsumed["seed:feed-tension-too-tight"]["archetype_row"] == "transmission.t3-high.slipping"
    assert subsumed["seed:feed-tension-too-tight"]["tension"] == "high"
    assert subsumed["seed:feed-tension-ok-restores-feed"]["archetype_row"] == "transmission.t1.feeding"
    assert subsumed["seed:feed-tension-ok-restores-feed"]["tension"] == "normal"
    untranslated = {item["rule_id"] for item in translation["untranslated"]}
    assert untranslated == {"seed:wingnut-loose-spool-spins",
                            "seed:wingnut-tight-stops-spool-spin"}


def test_nominal_fixpoint_stored_as_initial_state():
    definition, _model = _proof_definition()
    by_id = {c.id: c.initial_state for c in definition.components}
    assert by_id["wire-feed-mechanism"]["material_out"] == "normal"
    assert by_id["wire-feed-mechanism"]["tension"] == "normal"
    assert by_id["wire-feed-mechanism"]["groove_matches"] is True
    assert by_id["mig-gun"]["material_out"] == "normal"
    assert by_id["welder"]["arc"] == "normal"
    assert by_id["wire-feed-motor"]["motion_out"] == "normal"
    assert by_id["wire-feed-motor"]["heat_out"] == "normal"
    assert by_id["trigger"]["power_out"] == "normal"
    assert definition.metadata["nominal_fixpoint"].startswith("nominal fixpoint run once")


def test_compiler_lint_clean_and_settles():
    definition, _model = _proof_definition()
    lint = lint_transitions(definition)
    assert lint["conflicts"] == []
    assert lint["invalid"] == []
    assert lint["ordered"] is True
    settle = definition.metadata["settle"]
    assert settle["settled"] is True, settle["runs"]


def test_check_settles_catches_a_non_settling_twin():
    # A two-transition oscillator: A sets x=1 when x=0, B sets x=0 when
    # x=1. The real engine flips it every step; the lint must report
    # did-not-settle rather than silently truncating.
    definition = DigitalTwinDefinition(
        name="oscillator", components=[
            {"id": "x", "name": "X", "component_type": "other",
             "initial_state": {"value": 0}}],
        transitions=[
            {"id": "a", "name": "set 1", "conditions": {"x.value": 0},
             "effects": {"x.value": 1}},
            {"id": "b", "name": "set 0", "conditions": {"x.value": 1},
             "effects": {"x.value": 0}},
        ])
    result = check_settles(definition)
    assert result["settled"] is False


def test_for_steps_mechanism_on_a_hand_built_twin():
    # The engine's for_steps streak (deferred M3 in 3B): a condition
    # held for 2 consecutive step() calls fires on the second call,
    # not the first.
    definition = DigitalTwinDefinition(
        name="streak", components=[
            {"id": "m", "name": "Motor", "component_type": "actuator",
             "initial_state": {"stalled": True, "overheated": False}}],
        transitions=[
            {"id": "s1", "name": "overheat after 2 stalled steps",
             "conditions": {"m.stalled": {"equals": True, "for_steps": 2}},
             "effects": {"m.overheated": True}},
        ])
    engine = DigitalTwinEngine(definition)
    engine.step()
    assert engine.state["components"]["m"]["overheated"] is False
    engine.step()
    assert engine.state["components"]["m"]["overheated"] is True


# ---------------------------------------------------------------------------
# RepairAction + ExpectedTrace + fault_injection wiring
# ---------------------------------------------------------------------------

def test_repair_actions_load_and_resolve():
    actions = load_repair_actions()
    # Readiness item A: 4 case-1 actions + 16 reviewed actions for the
    # oracle-passing rows of cases 2-9 (cases 7/9 and the oracle-failing
    # rows carry none). The id set is pinned; see
    # tests/test_phase456_readiness_repairs.py for the per-case counts
    # and the no-action rows.
    assert len(actions) == 20
    assert {a.id for a in actions} == {
        "repair:omni-01-a", "repair:omni-01-b", "repair:omni-01-c", "repair:omni-01-d",
        "repair:omni-02-wire-stops-a", "repair:omni-02-wire-stops-d",
        "repair:omni-02-wire-stops-e", "repair:omni-02-wire-stops-f",
        "repair:omni-03-birds-nest-a",
        "repair:omni-04-arc-not-stable-b", "repair:omni-04-arc-not-stable-e",
        "repair:omni-04-arc-not-stable-g", "repair:omni-04-arc-not-stable-h",
        "repair:omni-05-does-not-function-b", "repair:omni-05-does-not-function-c",
        "repair:omni-06-lcd-dark-a", "repair:omni-06-lcd-dark-c",
        "repair:omni-06-lcd-dark-e",
        "repair:omni-08-porosity-a", "repair:omni-08-porosity-b"}
    definition, _model = _proof_definition()
    for action in actions:
        resolved, problems = resolve_action(action, definition)
        assert problems == [], (action.id, problems)
        assert resolved["state_effects"]
        assert resolved["postconditions"]
        assert resolved["expected_trace"] is not None


def test_case1_repairs_validate_through_the_verifier():
    definition, _model = _proof_definition()
    twin_state = initial_twin_state(definition)
    for cause in load_registry_causes():
        if cause["case_id"] != "omni-01-wire-not-feeding":
            continue
        ref = f"{cause['case_id']}:{cause['cause_letter']}"
        action = find_repair_action(ref)
        assert action is not None, ref
        outcome = run_verified_repair_action(
            definition, twin_state, action=action,
            fault_injection=cause["fault_injection"])
        assert outcome.available is True, (ref, outcome.reason)
        assert outcome.verdict == "validated", (ref, outcome.reason, outcome.result)
        assert outcome.trace_note == "trace shows the required chain"
        assert outcome.repair_action_id == action.id


def test_verify_single_cause_uses_fault_injection_first():
    definition, _model = _proof_definition()
    twin_state = initial_twin_state(definition)
    snapshot = snapshot_model_for_tournament()
    for cause in load_registry_causes():
        if cause["case_id"] != "omni-01-wire-not-feeding":
            continue
        row = verify_single_cause(
            definition, twin_state, snapshot, _FakeState(),
            cause["cause"], cause["fix"])
        assert row["available"] is True, (cause["cause_letter"], row["reason"])
        assert row["verdict"] == "validated", (cause["cause_letter"], row["reason"])
        assert row["repair_action_id"] == f"repair:omni-01-{cause['cause_letter']}"
        assert row["failure_to_behaviour"]["behaviour_owner_instance"] in (
            "Wire Feed Mechanism", "MIG Gun")
        assert row["failure_to_behaviour"]["fault_template_row_ids"], row


def test_cause_with_injection_but_no_action_is_unavailable_not_rejected():
    # A cause with a reviewed fault_injection but no reviewed RepairAction:
    # the tournament must say the twin cannot evaluate the repair, not
    # reject a repair it never ran. Case 2a used to be the example; since
    # the readiness follow-up added reviewed RepairActions for the
    # oracle-passing rows, the first case-2 cause without one is 2b
    # (liner clogged — its oracle row fails, so it carries no action).
    definition, _model = _proof_definition()
    twin_state = initial_twin_state(definition)
    snapshot = snapshot_model_for_tournament()
    with_action = {(a.case_id, a.cause_letter) for a in load_repair_actions()}
    cause = next(c for c in load_registry_causes()
                 if c["case_id"] == "omni-02-wire-stops"
                 and c["fault_injection"]
                 and (c["case_id"], c["cause_letter"]) not in with_action)
    assert cause["cause_letter"] == "b", cause
    row = verify_single_cause(
        definition, twin_state, snapshot, _FakeState(),
        cause["cause"], cause["fix"])
    assert row["available"] is False
    assert row["verdict"] is None
    assert "no reviewed RepairAction" in row["reason"]


def test_expected_trace_missing_step_is_unavailable():
    definition, _model = _proof_definition()
    entries = parse_trace_messages([
        "transmission row t1.feeding [wire-feed-mechanism]: "
        "wire-feed-mechanism.material_out 'low' → 'normal'",
    ])
    check = check_expected_trace(
        entries, {"components": {}}, {
            "required": [
                {"path": "wire-feed-mechanism.material_out", "value": "normal",
                 "transition_id": "archetype:transmission.t1.feeding@wire-feed-mechanism"},
                {"path": "welder.arc", "value": "normal",
                 "transition_id": "link:edge:arc-to-welder:normal"}],
            "forbidden": [], "terminal": {}},
        definition)
    assert check["satisfied"] is False
    assert "welder.arc" in check["missing"]


def test_expected_trace_forbidden_change_is_reported():
    definition, _model = _proof_definition()
    entries = parse_trace_messages([
        "converter row m2.power-absent: wire-feed-motor.motion_out 'normal' → 'absent'",
    ])
    check = check_expected_trace(
        entries, {"components": {}}, {
            "required": [], "forbidden": [
                {"path": "wire-feed-motor.motion_out", "value": "absent",
                 "transition_id": None}],
            "terminal": {}},
        definition)
    assert check["forbidden_hit"] is not None
    assert check["forbidden_hit"]["path"] == "wire-feed-motor.motion_out"


def test_find_registry_cause_matches_case_text():
    cause = find_registry_cause("Insufficient wire feed pressure.")
    assert cause is not None
    assert cause["case_id"] == "omni-01-wire-not-feeding"
    assert cause["fault_injection"] == {"instance": "Wire Feed Mechanism",
                                        "parameter": "tension", "value": "low"}


# ---------------------------------------------------------------------------
# Oracle gate: case 1 = 4/4 on the proof twin
# ---------------------------------------------------------------------------

def test_oracle_case1_is_100_percent_on_the_proof_twin():
    definition, _model = _proof_definition()
    rows, vocab = _case1_oracle_rows()
    report = run_oracle(
        definition.model_dump(mode="json"), rows, vocab["cases"],
        observable_paths=observable_paths(definition),
        test_models=build_test_models(definition))
    assert report.o1.passed, report.o1.reasons
    assert report.headline["passed"] == 4
    assert report.headline["denominator"] == 4
    assert report.headline["rate"] == 1.0
    assert report.discriminability["pairs_separated"] == 6
    assert report.discriminability["pairs_total"] == 6
    for row_result in report.rows:
        assert row_result.outcome == "pass", (row_result.row.cause, row_result.reasons)
        assert row_result.checks["O2"].passed
        assert row_result.checks["O3"].passed
        assert row_result.checks["O4"].passed
        assert row_result.checks["O5"].passed
