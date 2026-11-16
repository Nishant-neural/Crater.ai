"""3C oracle-fix regression tests.

The first 3C oracle run measured headline 0/35 (O1 pass, O3 57/58) and the
fixes below moved it to 20/35. These tests pin the fixes so a later change
cannot silently reintroduce them. The 20/35 headline itself is a MEASURED
rate (recorded in phase3c-oracle-report.json), not a test target — no test
here asserts a headline number.
"""

import json
from pathlib import Path

from backend.simulation import archetypes as arch
from backend.simulation.archetypes import ARCHETYPES
from backend.simulation.lint import lint_archetype_table, generate_unknown_rows
from backend.simulation.omnipro_proof import build_omnipro_proof_model
from backend.simulation import edges as edges_mod

GOAL_FILES = Path(__file__).resolve().parents[3] / "goals" / \
    "crater-ai-diagnosis-and-machine-model-workflow-improvement" / "files"
REGISTRY = Path(__file__).resolve().parent.parent / "backend" / "knowledge" / \
    "repair_cases_omnipro.json"
VOCABULARY = GOAL_FILES / "oracle_vocabulary_v1.json"


def _row(archetype_id, row_id):
    arch_def = ARCHETYPES[archetype_id]
    for row in arch_def.propagation_table:
        if row.row_id == row_id:
            return row
    raise AssertionError(f"{archetype_id} has no row {row_id}")


def test_all_populated_archetypes_still_lint_clean():
    # The 3C oracle fix added 1 A1 row, 1 A4 row, 5 A7 rows and 4 A8 rows
    # (plus 1 A7 + 1 A8 triple-deviation row). Every populated archetype
    # must still have zero conflicts and zero invalid rows.
    for archetype_id, archetype in arch.populated_archetypes().items():
        rows = list(archetype.propagation_table) + generate_unknown_rows(archetype)
        lint = lint_archetype_table(archetype, rows)
        assert lint["invalid"] == [], f"{archetype_id}: {lint['invalid']}"
        assert lint["conflicts"] == [], f"{archetype_id}: {lint['conflicts'][:2]}"


def test_transmission_motion_low_feeds_slowly_not_unknown():
    # 3C oracle fix: the A4 had no row for motion_in=low, so case 9's feeder
    # fell to a generated totality row (material_out=unknown), which
    # propagated as unknown all the way to the arc.
    row = _row("transmission", "transmission.t-motion-low.slow")
    assert row.inputs["motion_in"] == "low"
    assert row.outputs["material_out"] == "low"
    assert row.next_state == "feeding"


def test_load_double_deviation_rows():
    # A fault on the gun/tip degrades TWO of the arc's inputs at once, so
    # the arc saw a combination no single-deviation row covers (unknown).
    absent_absent = _row("load", "load.l-energy-absent-material-absent.absent")
    assert absent_absent.outputs["effect_out"] == "absent"
    low_absent = _row("load", "load.l-energy-low-material-absent.unstable")
    assert low_absent.outputs["effect_out"] == "unstable"
    absent_low = _row("load", "load.l-energy-absent-material-low.unstable")
    assert absent_low.outputs["effect_out"] == "unstable"
    low_low = _row("load", "load.l-energy-low-material-low.unstable")
    assert low_low.outputs["effect_out"] == "unstable"
    # The triple deviation (case 3(c): no wire + low gas).
    mat_absent_gas_low = _row("load", "load.l-material-absent-gas-low.absent")
    assert mat_absent_gas_low.outputs["effect_out"] == "absent"


def test_conductor_double_deviation_rows():
    absent_absent = _row("conductor",
                         "conductor.c-energy-absent-material-absent.absent")
    assert absent_absent.outputs["material_out"] == "absent"
    assert absent_absent.outputs["energy_out"] == "absent"
    low_low = _row("conductor", "conductor.c-energy-low-material-low.low")
    assert low_low.outputs["material_out"] == "low"
    assert low_low.outputs["energy_out"] == "low"
    low_absent = _row("conductor",
                      "conductor.c-energy-low-material-absent.absent")
    assert low_absent.outputs["material_out"] == "absent"
    assert low_absent.outputs["energy_out"] == "low"
    mat_absent_gas_low = _row("conductor",
                              "conductor.c-material-absent-gas-low.absent")
    assert mat_absent_gas_low.outputs["material_out"] == "absent"
    assert mat_absent_gas_low.outputs["gas_out"] == "low"


def test_source_very_low_distinguishes_case5b_from_case9a():
    # 3C oracle fix: the same injection (input_voltage=low) gave two
    # different expected symptoms. Case 5(b) is now very_low (the welder
    # does not function at all); case 9(a) stays low (a weak arc).
    assert "very_low" in arch.QUALITATIVE_DOMAINS["electrical"]
    very_low = _row("source", "source.s-input-very-low.off")
    assert very_low.inputs["input_voltage"] == "very_low"
    assert very_low.outputs["power_out"] == "absent"
    low = _row("source", "source.s-input-low.low")
    assert low.inputs["input_voltage"] == "low"
    assert low.outputs["power_out"] == "low"


def test_registry_5b_and_9b_fault_values_are_declared_domain_values():
    reg = json.loads(REGISTRY.read_text())
    voc = json.loads(VOCABULARY.read_text())
    for case in reg["cases"]:
        for idx, cause in enumerate(case["causes"]):
            letter = chr(ord("a") + idx)
            fi = cause.get("fault_injection") or {}
            if case["id"] == "omni-05-does-not-function" and letter == "b":
                assert fi["value"] == "very_low", fi
            if case["id"] == "omni-09-weak-arc" and letter == "b":
                assert fi["value"] == "wrong_gauge", fi
    for row in voc["rows"]:
        if row["case_id"] == "omni-05-does-not-function" and \
                "insufficient" in row["cause"].lower():
            assert row["fault"]["value"] == "very_low", row["fault"]
        if row["case_id"] == "omni-09-weak-arc" and \
                "gauge" in row["cause"].lower():
            assert row["fault"]["value"] == "wrong_gauge", row["fault"]


def test_feeder_to_liner_threshold_restored():
    # 3C oracle fix: the A8 rows pass a low material input through as low,
    # so the tip read 'low' and the arc 'unstable' where the vocabulary
    # expects 'absent' (cases 1/2). The 3B threshold is restored on the
    # feeder->liner edge as material_to_liner.
    edge_list = edges_mod.load_edge_list()
    feeder_to_liner = next(
        e for e in edge_list["edges"] if e["id"] == "edge:feeder-to-liner")
    assert feeder_to_liner["value_map"] == "material_to_liner"
    assert edge_list["value_maps"]["material_to_liner"] == {
        "absent": "absent", "low": "absent", "normal": "normal",
        "high": "absent", "unknown": "unknown",
    }
    assert edge_list["version"] == "omnipro-edge-list-v3"


def test_required_chains_have_direct_declared_edges():
    # 3C oracle fix: the 3A required chains were written for the 3B proof
    # twin (direct motor->feeder->gun and cord->trigger->welder edges) and
    # the 3C topology adds intermediates, so every row carried a
    # topology_gap. Each case's chain must now be a path of direct declared
    # edges in the 3C edge list.
    voc = json.loads(VOCABULARY.read_text())
    model = build_omnipro_proof_model()
    by_name = {e.name: e.id for e in model.entities}
    edge_list = edges_mod.load_edge_list()
    declared = {(by_name[e["source_component"]], by_name[e["target_component"]])
                for e in edge_list["edges"]}
    for case_id, case in voc["cases"].items():
        chain = [by_name[link["component"]]
                 for link in case["required_chain"]]
        for src, dst in zip(chain, chain[1:]):
            assert (src, dst) in declared, \
                f"{case_id}: no declared edge {src} -> {dst}"
