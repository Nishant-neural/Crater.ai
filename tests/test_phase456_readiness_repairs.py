"""Readiness item A — reviewed RepairActions for the oracle-passing rows
of cases 2-9.

The 3B scope (4 case-1 actions only) is superseded here: 16 new reviewed
actions (case 2 a/d/e/f, case 3 a, case 4 b/e/g/h, case 5 b/c, case 6 a/c/e,
case 8 a/b) join them, each following the case-1 pattern (procedure refs +
page evidence, ExpectedTrace required chain from the measured intervention
trace, forbidden guard, terminal conditions, preconditions, safety).
Every action must VALIDATE through run_verified_repair_action on the proof
twin with the "trace shows the required chain" note. Rows the oracle does
NOT pass carry NO RepairAction — asserted here per row.

The proof twin compiles once per module (~40 s); the 20 verifies run
against it.
"""
from collections import Counter

import pytest

from backend.simulation.compiler import compile_machine_model
from backend.simulation.edges import load_edge_list_for_product
from backend.simulation.expert_rules import apply_expert_seed, load_seed_for_product
from backend.simulation.omnipro_proof import build_omnipro_proof_model, initial_twin_state
from backend.simulation.repair_actions import (
    find_repair_action,
    load_registry_causes,
    load_repair_actions,
    render_repair_action_safety_first,
)
from backend.simulation.verification import run_verified_repair_action

# (case_id, letter) of every oracle-PASSING row outside case 1, per the
# filed 3C report (headline 20/35; O2 and O3 both pass).
PASSING_NEW = {
    ("omni-02-wire-stops", "a"), ("omni-02-wire-stops", "d"),
    ("omni-02-wire-stops", "e"), ("omni-02-wire-stops", "f"),
    ("omni-03-birds-nest", "a"),
    ("omni-04-arc-not-stable", "b"), ("omni-04-arc-not-stable", "e"),
    ("omni-04-arc-not-stable", "g"), ("omni-04-arc-not-stable", "h"),
    ("omni-05-does-not-function", "b"), ("omni-05-does-not-function", "c"),
    ("omni-06-lcd-dark", "a"), ("omni-06-lcd-dark", "c"),
    ("omni-06-lcd-dark", "e"),
    ("omni-08-porosity", "a"), ("omni-08-porosity", "b"),
}

# Every other twin-expressible cause: no RepairAction. (Null-injection
# rows — settings / technique / cross-reference — have no fault the twin
# can express; the oracle-failing rows are listed with their reason in
# PHASE456_READINESS_BUILD_NOTES.md.)
EXPECTED_NO_ACTION = {
    ("omni-02-wire-stops", "b"), ("omni-02-wire-stops", "c"),
    ("omni-03-birds-nest", "b"), ("omni-03-birds-nest", "c"),
    ("omni-03-birds-nest", "d"),
    ("omni-04-arc-not-stable", "a"), ("omni-04-arc-not-stable", "c"),
    ("omni-04-arc-not-stable", "d"), ("omni-04-arc-not-stable", "f"),
    ("omni-05-does-not-function", "a"), ("omni-05-does-not-function", "d"),
    ("omni-05-does-not-function", "e"),
    ("omni-06-lcd-dark", "b"), ("omni-06-lcd-dark", "d"),
    ("omni-07-no-arc", "a"), ("omni-07-no-arc", "b"),
    ("omni-07-no-arc", "c"), ("omni-07-no-arc", "d"),
    ("omni-08-porosity", "c"), ("omni-08-porosity", "d"),
    ("omni-08-porosity", "e"), ("omni-08-porosity", "f"),
    ("omni-09-weak-arc", "a"), ("omni-09-weak-arc", "b"),
    ("omni-09-weak-arc", "c"),
}


@pytest.fixture(scope="module")
def proof_definition():
    model = build_omnipro_proof_model()
    seed = load_seed_for_product("Harbor Freight", "220", "OMNIPRO")
    assert seed is not None
    model, _warnings, applied = apply_expert_seed(model, seed)
    assert len(applied["rules_applied"]) == 4
    definition, _warnings = compile_machine_model(
        model, name="OMNIPRO 220 — readiness proof twin (test)",
        edge_list=load_edge_list_for_product("Harbor Freight", "220", "OMNIPRO"))
    return definition


def test_repair_action_counts_per_case():
    actions = load_repair_actions()
    assert len(actions) == 20
    counts = Counter(a.case_id for a in actions)
    assert counts == {
        "omni-01-wire-not-feeding": 4,
        "omni-02-wire-stops": 4,
        "omni-03-birds-nest": 1,
        "omni-04-arc-not-stable": 4,
        "omni-05-does-not-function": 2,
        "omni-06-lcd-dark": 3,
        "omni-08-porosity": 2,
    }
    assert {a.id for a in actions} == {
        f"repair:{case_id}-{letter}"
        for case_id, letter in PASSING_NEW
    } | {f"repair:omni-01-{letter}" for letter in "abcd"}


def test_no_repair_action_on_oracle_failing_or_unexpressible_rows():
    for case_id, letter in sorted(EXPECTED_NO_ACTION):
        assert find_repair_action(f"{case_id}:{letter}") is None, \
            f"{case_id}:{letter} must not carry a RepairAction"


def test_every_new_repair_action_validates_on_the_proof_twin(proof_definition):
    causes = {(c["case_id"], c["cause_letter"]): c
              for c in load_registry_causes()}
    for case_id, letter in sorted(PASSING_NEW):
        action = find_repair_action(f"{case_id}:{letter}")
        assert action is not None
        assert action.expected_trace is not None
        assert action.procedure_refs, f"{action.id} has no procedure refs"
        outcome = run_verified_repair_action(
            proof_definition, initial_twin_state(proof_definition),
            action=action,
            fault_injection=causes[(case_id, letter)]["fault_injection"])
        assert outcome.available, (action.id, outcome.reason)
        assert outcome.verdict == "validated", (action.id, outcome.reason)
        assert outcome.trace_note == "trace shows the required chain", \
            (action.id, outcome.trace_note)


def test_all_repair_actions_carry_preconditions_and_safety():
    for action in load_repair_actions():
        assert action.preconditions, f"{action.id} has no preconditions"
        assert action.safety_requirements, \
            f"{action.id} has no safety_requirements"
        rendered = render_repair_action_safety_first(action)
        assert rendered["order"] == ["Safety", "Preconditions", "Steps",
                                     "Expected trace"]
        assert rendered["Safety"], "safety section is always present"
        assert rendered["Steps"], "procedure refs carry the page evidence"
