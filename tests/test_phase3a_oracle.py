"""Phase 3A oracle harness tests (design v3 §6, §10 3A; Amendments 13 Nov 2026).

Hermetic except where marked: the parser-expansion test reads the real
48-page OMNIPRO manual PDF, and the vocabulary tests read the reviewed
vocabulary artifact — both are skipped (not failed) when the files are
absent, because neither lives inside this repo. No LLM, no network, no
Qdrant anywhere in this file.
"""
import json
import os
import re
from pathlib import Path

import pytest

from backend.simulation.oracle import (
    OracleRow,
    build_oracle_set,
    expand_cause_rows,
    fault_injection_to_fault,
    load_fault_injections,
    load_vocabulary,
    run_oracle,
    validate_vocabulary,
)
from backend.simulation.schema import DigitalTwinDefinition

TREE = Path(__file__).resolve().parents[1]
VOCAB_PATH = Path(os.environ.get(
    "ORACLE_VOCABULARY_PATH",
    str(Path.home() / "workspace" / "goals" /
        "crater-ai-diagnosis-and-machine-model-workflow-improvement" / "files" /
        "oracle_vocabulary_v1.json")))
REGISTRY_PATH = TREE / "backend" / "knowledge" / "repair_cases_omnipro.json"
MANUAL_PATH = Path(os.environ.get(
    "ORACLE_MANUAL_PATH",
    str(Path.home() / "workspace" / "project-review-v3" / "docs" / "owner-manual.pdf")))


def _vocab():
    return load_vocabulary(VOCAB_PATH)


def _case1_rows(vocab):
    rows = {r["cause"]: r for r in vocab["rows"] if r["case_id"] == "omni-01-wire-not-feeding"}
    return rows


# ---------------------------------------------------------------------------
# Ground truth: deterministic parser expansion (p42 -> 25, p43 -> 20)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not MANUAL_PATH.exists(), reason="OMNIPRO manual PDF not present")
def test_parser_expands_to_45_cause_rows():
    import pdfplumber
    from backend.ingestion.table_parser import parse_troubleshooting_tables
    counts = {}
    expanded = []
    with pdfplumber.open(MANUAL_PATH) as pdf:
        for page_no in (42, 43):
            model = parse_troubleshooting_tables(
                pdf.pages[page_no - 1].extract_tables(),
                source_document="owner-manual.pdf", page=page_no, chunk_id=f"p{page_no}")
            assert model is not None
            rows = expand_cause_rows(model.failure_modes, model.procedures)
            counts[page_no] = len(rows)
            expanded.extend(rows)
    assert counts == {42: 25, 43: 20}, counts
    assert len(expanded) == 45
    symptoms = {row["symptom"] for row in expanded}
    assert len(symptoms) == 9


def test_expand_cause_rows_splits_numbered_causes():
    from backend.ingestion.table_parser import parse_troubleshooting_markdown
    md = (
        "| Problem | Possible Cause | Solution |\n"
        "|---|---|---|\n"
        "| Wire does not feed | 1. Tension too low. 2. Roller wrong size. | Adjust tension. |\n"
    )
    model = parse_troubleshooting_markdown(md, page=42)
    rows = expand_cause_rows(model.failure_modes, model.procedures)
    assert [r["cause"] for r in rows] == ["Tension too low.", "Roller wrong size."]


# ---------------------------------------------------------------------------
# Vocabulary: schema, categories, spin correction, inference flags
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not VOCAB_PATH.exists(), reason="oracle vocabulary JSON not present")
def test_vocabulary_validates_and_totals_35_5_4_1():
    vocab = _vocab()
    assert validate_vocabulary(vocab) == []
    from collections import Counter
    counts = Counter(row["category"] for row in vocab["rows"])
    assert counts == {"twin_expressible": 35, "settings": 5,
                      "technique": 4, "cross_reference": 1}, counts
    assert len(vocab["rows"]) == 45
    assert "field data is the only real ground truth" in vocab["scope_disclaimer"].lower() \
        or "only real ground truth" in vocab["scope_disclaimer"]


def test_vocabulary_rejects_bad_rows():
    vocab = {"version": "x", "cases": {"c1": {"observables": ["a"], "tests": ["t"]}},
             "tests": {"t": {"outcomes": ["yes"]}}, "rows": []}
    assert validate_vocabulary(vocab)  # no rows at all
    bad = {"version": "x",
           "cases": {"c1": {"observables": ["a"], "tests": ["t"]}},
           "tests": {"t": {"outcomes": ["yes"]}},
           "rows": [{"case_id": "c1", "cause": "X", "category": "twin_expressible",
                     "fault": None, "observables": {"a": "normal"},
                     "tests": {"t": "maybe"}}]}
    errors = validate_vocabulary(bad)
    assert any("not in" in e for e in errors)
    assert any("without a complete fault injection" in e for e in errors)


@pytest.mark.skipif(not VOCAB_PATH.exists(), reason="oracle vocabulary JSON not present")
def test_case1_spin_encoding_is_corrected_not_backwards():
    """Amendments item 1: spinning after release means tension too LOW
    (p42 remedy: correct tension PREVENTS spinning). (a) spins (inferred,
    flagged); (d) wood test unmodelled, spin no_spin (inferred, flagged)."""
    vocab = _vocab()
    rows = _case1_rows(vocab)
    low = rows["Insufficient wire feed pressure."]
    assert low["tests"]["step27_wood_test"] == "stops"
    assert low["tests"]["post_release_spin"] == "spins"
    assert any("inference" in flag for flag in low["flags"])
    high = rows["Feed Tensioner is too tight."]
    assert high["tests"]["step27_wood_test"] == "unmodelled"
    assert high["tests"]["post_release_spin"] == "no_spin"
    assert any("inference" in flag for flag in high["flags"])
    assert high["tests"]["post_release_spin"] != "keeps_spinning"
    # The (a)/(d) pair separates on the spin check, not the wood test.
    assert low["tests"]["post_release_spin"] != high["tests"]["post_release_spin"]
    assert low["tests"]["step27_wood_test"] != high["tests"]["step27_wood_test"] \
        or low["tests"]["step27_wood_test"] == "unmodelled"


@pytest.mark.skipif(not VOCAB_PATH.exists(), reason="oracle vocabulary JSON not present")
def test_every_inferred_value_is_flagged():
    """An unflagged inference is a bug (Amendments item 1). Every row
    whose tests carry 'unmodelled' must say so in its flags, and every
    row with an inference flag must name what was inferred."""
    vocab = _vocab()
    for row in vocab["rows"]:
        if "unmodelled" in row["tests"].values():
            assert any("unmodelled" in flag for flag in row["flags"]), row["cause"]
        for flag in row["flags"]:
            assert "inference" in flag or "unmodelled" in flag or "assumption" in flag \
                or "two-direction" in flag or "indistinguishable" in flag \
                or "covers two" in flag or "one cause" in flag, flag


# ---------------------------------------------------------------------------
# fault_injection: the registry mapping (Amendments item 5)
# ---------------------------------------------------------------------------

def test_registry_fault_injection_covers_all_45_causes():
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    total = with_injection = 0
    for case in registry["cases"]:
        for cause in case["causes"]:
            total += 1
            assert "fault_injection" in cause, cause["cause"]
            injection = cause["fault_injection"]
            if injection is None:
                continue
            with_injection += 1
            assert injection["instance"]
            assert ("parameter" in injection) ^ ("fault_state" in injection), injection
    assert total == 45
    assert with_injection == 35  # the twin-expressible headline set


def test_registry_case1_cause_a_is_tension_low_on_the_feeder():
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    case1 = next(c for c in registry["cases"] if c["id"] == "omni-01-wire-not-feeding")
    first = case1["causes"][0]
    assert first["fault_injection"] == {
        "instance": "Wire Feed Mechanism", "parameter": "tension", "value": "low"}
    fourth = case1["causes"][3]
    assert fourth["fault_injection"] == {
        "instance": "Wire Feed Mechanism", "parameter": "tension", "value": "high"}
    # Cause 4's test is a diagnostic spin check now, not a remedy phrase.
    assert "Loosen the tensioner until" not in fourth["test"]
    assert "spin" in fourth["test"]


def test_fault_injection_translation():
    assert fault_injection_to_fault(
        {"instance": "Wire Feed Mechanism", "parameter": "tension", "value": "low"}) == {
        "component": "Wire Feed Mechanism", "field": "tension",
        "value": "low", "kind": "parameter"}
    assert fault_injection_to_fault({"instance": "MIG Gun", "fault_state": "open"}) == {
        "component": "MIG Gun", "field": "fault", "value": "open", "kind": "fault_state"}
    assert fault_injection_to_fault(None) is None


@pytest.mark.skipif(not VOCAB_PATH.exists(), reason="oracle vocabulary JSON not present")
def test_oracle_set_uses_registry_fault_mapping():
    vocab = _vocab()
    injections = load_fault_injections(REGISTRY_PATH)
    assert len(injections) == 45
    expanded = [{
        "symptom": vocab["cases"][r["case_id"]]["symptom"], "cause": r["cause"],
        "remedy": "", "remedy_source": "parser_procedure", "source_pages": [42],
    } for r in vocab["rows"]]
    rows, problems = build_oracle_set(expanded, vocab, fault_injections=injections)
    assert problems == [], problems
    assert len(rows) == 45
    row_a = next(r for r in rows if r.cause == "Insufficient wire feed pressure.")
    assert row_a.fault == {"component": "Wire Feed Mechanism", "field": "tension",
                           "value": "low", "kind": "parameter"}
    settings = [r for r in rows if r.category == "settings"]
    assert settings and all(r.fault is None for r in settings)


@pytest.mark.skipif(not VOCAB_PATH.exists(), reason="oracle vocabulary JSON not present")
def test_oracle_set_reports_registry_vocabulary_disagreement():
    vocab = json.loads(VOCAB_PATH.read_text(encoding="utf-8"))
    for row in vocab["rows"]:
        if row["cause"] == "Insufficient wire feed pressure.":
            row["fault"] = {"component": "Feed Tensioner", "field": "tension",
                            "value": "low", "kind": "parameter"}
    injections = load_fault_injections(REGISTRY_PATH)
    expanded = [{
        "symptom": vocab["cases"][r["case_id"]]["symptom"], "cause": r["cause"],
        "remedy": "", "remedy_source": "parser_procedure", "source_pages": [42],
    } for r in vocab["rows"]]
    _, problems = build_oracle_set(expanded, vocab, fault_injections=injections)
    assert any("disagreement" in p for p in problems)


def test_oracle_set_keeps_unmapped_parser_rows_in_denominator():
    vocab = {"version": "x",
             "cases": {"c1": {"case_rank": 1, "symptom": "S", "observables": ["a"],
                              "nominal": {"a": "normal"}, "observable_components": {"a": "X"},
                              "required_chain": [], "tests": []}},
             "tests": {}, "rows": []}
    expanded = [{"symptom": "S", "cause": "Something unlisted.", "remedy": "",
                 "remedy_source": "parser_procedure", "source_pages": [42]}]
    rows, problems = build_oracle_set(expanded, vocab)
    assert len(problems) == 1
    assert rows[0].category == "unmapped"


# ---------------------------------------------------------------------------
# O1–O5 on small synthetic twins
# ---------------------------------------------------------------------------

CASE1 = {
    "case_rank": 1, "symptom": "Wire Feed Motor Runs but Wire Does Not Feed Properly",
    "observables": ["wire_out@feeder", "wire_out@tip", "arc", "motor_motion"],
    "nominal": {"wire_out@feeder": "normal", "wire_out@tip": "normal",
                "arc": "normal", "motor_motion": "normal"},
    "observable_components": {"wire_out@feeder": "Wire Feed Mechanism",
                              "wire_out@tip": "MIG Gun", "arc": "Welder",
                              "motor_motion": "Wire Feed Motor"},
    "required_chain": [{"label": "motor", "component": "Wire Feed Motor"},
                       {"label": "feeder", "component": "Wire Feed Mechanism"},
                       {"label": "wire", "component": "MIG Gun"}],
    "tests": ["step27_wood_test", "post_release_spin", "groove_check"],
}
CASES = {"omni-01-wire-not-feeding": CASE1}
OBS_PATHS = {"wire_out@feeder": "feeder.feeding", "wire_out@tip": "gun.wire_at_tip",
             "arc": "welder.arc", "motor_motion": "motor.motion"}


def _twin(transitions, *, topology=True, arc_initial="normal"):
    components = [
        {"id": "motor", "name": "Wire Feed Motor", "component_type": "actuator",
         "initial_state": {"motion": "normal"}},
        {"id": "feeder", "name": "Wire Feed Mechanism", "component_type": "actuator",
         "initial_state": {"tension": "normal", "feeding": "normal"}},
        {"id": "gun", "name": "MIG Gun", "component_type": "connector",
         "initial_state": {"wire_at_tip": "normal"}},
        {"id": "welder", "name": "Welder", "component_type": "other",
         "initial_state": {"arc": arc_initial}},
    ]
    metadata = {"topology": [
        {"subject_id": "motor", "object_id": "feeder", "relation_type": "mechanical"},
        {"subject_id": "feeder", "object_id": "gun", "relation_type": "mechanical"},
        # 3B: the gun->welder edge. The arc is no longer exempt from O4
        # (it has a proper port owner via the gun->arc link), so the
        # synthetic twin must declare the topology the fault transition
        # claims — a fault that flips the arc with no path to the Welder
        # is a spurious output, which is exactly what O4 is for.
        {"subject_id": "gun", "object_id": "welder", "relation_type": "mechanical"},
    ] if topology else {"topology": []}}
    return DigitalTwinDefinition(
        name="synthetic case-1 twin", components=components,
        transitions=transitions, metadata=metadata)


def _row(cause, fault, observables, tests):
    return OracleRow(
        row_id=f"row-{cause[:8]}", case_id="omni-01-wire-not-feeding", case_rank=1,
        symptom=CASE1["symptom"], cause=cause, remedy="", remedy_source="case_registry",
        source_pages=[42], category="twin_expressible", fault=fault,
        observables=observables, tests=tests)


def _case1_row_a():
    return _row(
        "Insufficient wire feed pressure.",
        {"component": "Wire Feed Mechanism", "field": "tension", "value": "low",
         "kind": "parameter"},
        {"wire_out@feeder": "low", "wire_out@tip": "absent",
         "arc": "absent", "motor_motion": "normal"},
        {"step27_wood_test": "stops", "post_release_spin": "spins",
         "groove_check": "matches"})


def _case1_row_d():
    return _row(
        "Feed Tensioner is too tight.",
        {"component": "Wire Feed Mechanism", "field": "tension", "value": "high",
         "kind": "parameter"},
        {"wire_out@feeder": "low", "wire_out@tip": "absent",
         "arc": "absent", "motor_motion": "normal"},
        {"step27_wood_test": "unmodelled", "post_release_spin": "no_spin",
         "groove_check": "matches"})


def _tension_transitions():
    """One generic conditional transition per fault value (the twin's
    own behaviour — the 3A stub has no propagation engine). Each fires
    three state changes, matching case 1's required chain length."""
    return [
        {"id": "t-low", "name": "tension low slips",
         "conditions": {"feeder.tension": "low"},
         "effects": {"feeder.feeding": "low", "gun.wire_at_tip": "absent",
                     "welder.arc": "absent"}},
        {"id": "t-high", "name": "tension high slips",
         "conditions": {"feeder.tension": "high"},
         "effects": {"feeder.feeding": "low", "gun.wire_at_tip": "absent",
                     "welder.arc": "absent"}},
    ]


def test_o1_nominal_pass_and_fail():
    row = _case1_row_a()
    report = run_oracle(_twin(_tension_transitions()), [row], CASES,
                        observable_paths=OBS_PATHS)
    assert report.o1.passed
    report_bad = run_oracle(_twin(_tension_transitions(), arc_initial="absent"),
                            [row], CASES, observable_paths=OBS_PATHS)
    assert not report_bad.o1.passed
    assert any("o1_nominal_fail" in reason for reason in report_bad.o1.reasons)
    assert report_bad.headline["o1_gate"] == "fail"


def test_o2_match_and_mismatch():
    row = _case1_row_a()
    report = run_oracle(_twin(_tension_transitions()), [row], CASES,
                        observable_paths=OBS_PATHS)
    assert report.rows[0].outcome == "pass"
    assert report.rows[0].checks["O2"].passed
    wrong = _row("Insufficient wire feed pressure.",
                 {"component": "Wire Feed Mechanism", "field": "tension",
                  "value": "low", "kind": "parameter"},
                 {"wire_out@feeder": "normal", "wire_out@tip": "normal",
                  "arc": "normal", "motor_motion": "normal"},
                 {"step27_wood_test": "stops", "post_release_spin": "spins",
                  "groove_check": "matches"})
    report_wrong = run_oracle(_twin(_tension_transitions()), [wrong], CASES,
                              observable_paths=OBS_PATHS)
    assert report_wrong.rows[0].outcome == "fail"
    assert any("o2_mismatch" in reason
               for reason in report_wrong.rows[0].checks["O2"].reasons)


def test_o3_case1_pair_indistinguishable_on_wood_test_alone():
    """With only the wood test modelled, (a) and (d) are indistinguishable:
    both read stops on the wood test is wrong for (d) — it is unmodelled —
    so the pair fails O3. With the spin check modelled too, they separate."""
    rows = [_case1_row_a(), _case1_row_d()]
    wood_only = {
        "step27_wood_test": lambda ctx: (
            "stops" if ctx["state"]["components"]["feeder"].get("tension") == "low"
            else "unmodelled"),
    }
    report = run_oracle(_twin(_tension_transitions()), rows, CASES,
                        observable_paths=OBS_PATHS, test_models=wood_only)
    assert all(r.checks["O3"].passed is False for r in report.rows)
    assert report.discriminability["pairs_separated"] == 0
    with_spin = dict(wood_only)
    with_spin["post_release_spin"] = lambda ctx: (
        "spins" if ctx["state"]["components"]["feeder"].get("tension") == "low"
        else "no_spin")
    with_spin["groove_check"] = lambda ctx: "matches"
    report2 = run_oracle(_twin(_tension_transitions()), rows, CASES,
                         observable_paths=OBS_PATHS, test_models=with_spin)
    assert all(r.checks["O3"].passed for r in report2.rows)
    assert report2.discriminability["pairs_separated"] == 1
    assert all(r.outcome == "pass" for r in report2.rows)
    assert report2.headline == {"passed": 2, "denominator": 2, "rate": 1.0,
                                "o1_gate": "pass",
                                "o1_gate_note": "O1 nominal gate passed."}


def test_o4_spurious_output_fails():
    """A fault transition that also flips an upstream observable (motor
    motion) is a spurious output: the motor is not downstream of the feeder."""
    spurious = [
        {"id": "t-low", "name": "tension low slips + motor stops",
         "conditions": {"feeder.tension": "low"},
         "effects": {"feeder.feeding": "low", "gun.wire_at_tip": "absent",
                     "welder.arc": "absent", "motor.motion": "absent"}},
    ]
    report = run_oracle(_twin(spurious), [_case1_row_a()], CASES,
                        observable_paths=OBS_PATHS)
    assert not report.rows[0].checks["O4"].passed
    assert any("o4_spurious_output" in reason
               for reason in report.rows[0].checks["O4"].reasons)


def test_o5_thin_one_boolean_trace_fails():
    """A single boolean flip is not a simulation of the wire-feed path:
    case 1 requires >= 3 state changes (motor -> feeder -> wire)."""
    thin = [
        {"id": "t-low", "name": "one boolean flips",
         "conditions": {"feeder.tension": "low"},
         "effects": {"feeder.feeding": "low"}},
    ]
    report = run_oracle(_twin(thin), [_case1_row_a()], CASES,
                        observable_paths=OBS_PATHS)
    assert not report.rows[0].checks["O5"].passed
    assert any("o5_thin_trace" in reason
               for reason in report.rows[0].checks["O5"].reasons)


def test_o5_topology_gap_fails_when_motor_feeder_edge_missing():
    report = run_oracle(_twin(_tension_transitions(), topology=False),
                        [_case1_row_a()], CASES, observable_paths=OBS_PATHS)
    assert not report.rows[0].checks["O5"].passed
    assert any("topology_gap" in reason
               for reason in report.rows[0].checks["O5"].reasons)
    assert report.gap_list[0]["reason"] == "topology_gap"


def test_unmappable_cause_fails_and_stays_in_denominator():
    missing = _row("Insufficient wire feed pressure.",
                   {"component": "No Such Part", "field": "tension",
                    "value": "low", "kind": "parameter"},
                   {"wire_out@feeder": "low", "wire_out@tip": "absent",
                    "arc": "absent", "motor_motion": "normal"},
                   {"step27_wood_test": "stops", "post_release_spin": "spins",
                    "groove_check": "matches"})
    report = run_oracle(_twin(_tension_transitions()), [missing], CASES,
                        observable_paths=OBS_PATHS)
    assert report.rows[0].outcome == "fail"
    assert any("unmappable_cause" in reason for reason in report.rows[0].reasons)
    assert report.headline == {"passed": 0, "denominator": 1, "rate": 0.0,
                               "o1_gate": "pass",
                               "o1_gate_note": "O1 nominal gate passed."}


def test_unmapped_observables_fail_without_guessing():
    report = run_oracle(_twin(_tension_transitions()), [_case1_row_a()], CASES)
    assert report.rows[0].outcome == "fail"
    assert any("observable_unmapped" in reason for reason in report.rows[0].reasons)
    assert not report.o1.passed


def test_harness_is_deterministic():
    rows = [_case1_row_a(), _case1_row_d()]
    models = {
        "step27_wood_test": lambda ctx: (
            "stops" if ctx["state"]["components"]["feeder"].get("tension") == "low"
            else "unmodelled"),
        "post_release_spin": lambda ctx: (
            "spins" if ctx["state"]["components"]["feeder"].get("tension") == "low"
            else "no_spin"),
        "groove_check": lambda ctx: "matches",
    }
    twin = _twin(_tension_transitions())
    first = run_oracle(twin, rows, CASES, observable_paths=OBS_PATHS,
                       test_models=models).to_json()
    second = run_oracle(twin, rows, CASES, observable_paths=OBS_PATHS,
                        test_models=models).to_json()
    assert first == second


def test_harness_makes_no_llm_calls():
    """The harness path imports no LLM client and opens no network:
    its source references no provider SDK."""
    source = (TREE / "backend" / "simulation" / "oracle.py").read_text(encoding="utf-8")
    for banned in ("anthropic", "google_genai", "google.genai", "openai",
                   "requests.", "httpx", "urllib"):
        assert banned not in source, banned
    assert "LLM" in source  # only in comments/docstrings saying "no LLM"
    assert re.search(r"\bimport (requests|httpx|urllib)\b", source) is None
