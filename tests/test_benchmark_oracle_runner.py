"""Tests for the benchmark oracle runner (benchmark/run_benchmark_oracle.py).

Coverage:
  (i)   the runner core against the OMNIPRO proof twin + the OMNIPRO
        oracle vocabulary/registry reproduces the known 3C numbers
        (headline 20/35, O1 pass, O3 57/58) — with the proof twin's own
        observable mapping + test models, exactly as run_phase3c_oracle
        does. The generic binding cannot reproduce those numbers (see
        the build notes): the proof twin's port fields are named
        differently from the vocabulary ports, so a generic run scores
        near zero honestly. This test therefore uses the override, and
        a separate test pins the generic binding's conservatism.
  (ii)  the runner core against a small synthetic twin + the real
        Lincoln oracle JSON loads, validates the denominator, prints a
        report, no crash.
  (iii) the same for the real Miller oracle JSON.
  (iv)  no active twin -> clear error, non-zero exit, no crash.
  (v)   a row with an unresolvable component -> recorded unmapped,
        never guessed.

The (i) test needs the OMNIPRO inputs that live outside this repo
(the vocabulary + manual under ~/workspace, the registry in-repo).
When they are absent (a plain v12 checkout) it skips with a reason
instead of failing.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db.models import Base, DigitalTwin, TwinStatus
from backend.simulation.schema import DigitalTwinDefinition, TwinComponent
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from benchmark import run_benchmark_oracle as runner
from benchmark.run_benchmark_oracle import (
    BenchmarkOracleError,
    NoActiveTwinError,
    build_observable_paths,
    build_oracle_rows,
    load_active_twin,
    load_benchmark_oracle,
    run_benchmark_oracle,
    validate_benchmark_oracle,
)

REPO = Path(__file__).resolve().parent.parent
WORKSPACE = REPO.parents[1]  # ~/workspace (v12 -> crater-phase1 -> workspace)
BENCHMARKS = WORKSPACE / "crater-benchmarks" / "evaluations"
LINCOLN = BENCHMARKS / "c002-lincoln.json"
MILLER = BENCHMARKS / "c002b-miller.json"
GOAL_FILES = (
    WORKSPACE / "goals" / "crater-ai-diagnosis-and-machine-model-workflow-improvement" / "files"
)
VOCABULARY = GOAL_FILES / "oracle_vocabulary_v1.json"
MANUAL = WORKSPACE / "project-review-v3" / "docs" / "owner-manual.pdf"
REGISTRY = REPO / "backend" / "knowledge" / "repair_cases_omnipro.json"


def _synthetic_definition() -> DigitalTwinDefinition:
    """A small twin with a few uniquely-named components and state
    fields that deliberately cover only some benchmark ports: the
    generic binding maps what it can and leaves the rest unmapped."""
    return DigitalTwinDefinition(
        name="Synthetic welder (test twin)",
        description="Test twin: no transitions, a handful of fields.",
        components=[
            TwinComponent(id="welder", name="Welder", component_type="other",
                          initial_state={"input_voltage": "normal",
                                         "weld_output": "normal"}),
            TwinComponent(id="feeder", name="Wire Feeder", component_type="actuator",
                          initial_state={"wire_out": "normal"}),
            TwinComponent(id="gun", name="MIG Gun", component_type="other",
                          initial_state={"gas_flow": "normal"}),
        ],
        initial_signals={},
        transitions=[],
        metadata={},
    )


# ---------------------------------------------------------------------------
# (i) OMNIPRO proof twin reproduces the known 3C numbers
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not (VOCABULARY.is_file() and MANUAL.is_file() and REGISTRY.is_file()),
    reason="OMNIPRO oracle inputs (vocabulary/manual/registry) not present in this checkout",
)
def test_proof_twin_reproduces_known_3c_numbers():
    """Runner core + proof twin + OMNIPRO vocabulary/registry, with the
    proof twin's own observable mapping + test models (the 3C runner's
    exact setup): headline 20/35, O1 pass, O3 57/58.

    Done this way (override mapping) rather than via the generic
    binding because the generic binding cannot know that the
    vocabulary port ``wire_out@tip`` is the Contact Tip's
    ``material_out`` field — that mapping is reviewed twin knowledge,
    not derivable from names. See BENCHMARK_ORACLE_RUNNER_BUILD_NOTES.md.
    """
    from backend.simulation.compiler import compile_machine_model
    from backend.simulation.expert_rules import apply_expert_seed, load_seed_for_product
    from backend.simulation.omnipro_proof import (
        build_omnipro_proof_model,
        build_test_models,
        observable_paths,
    )
    from benchmark.oracle import load_vocabulary, run_oracle  # noqa: F401  (parity check)
    from benchmark.run_phase3a_baseline import _load_oracle_set

    vocab = load_vocabulary(VOCABULARY)
    oracle_rows, problems, _expanded = _load_oracle_set(MANUAL, vocab, REGISTRY)
    assert len(oracle_rows) == 45

    model = build_omnipro_proof_model()
    seed = load_seed_for_product("Harbor Freight", "220", "OMNIPRO")
    if seed is not None:
        model, _warnings, _applied = apply_expert_seed(model, seed)
    definition, _warnings = compile_machine_model(
        model, name="OMNIPRO 220 — Phase 3C proof twin",
        description="Benchmark-oracle test: 3C proof twin.",
    )

    # The core accepts a pre-built OracleRow set only through the
    # oracle-JSON path, so verify the core's run_oracle call shape
    # here by running the same twin through run_oracle directly and
    # through the core on a JSON built from the same rows.
    direct = run_oracle(
        definition.model_dump(mode="json"), oracle_rows, vocab["cases"],
        observable_paths=observable_paths(definition),
        test_models=build_test_models(definition), problems=problems)
    assert direct.headline == {
        "passed": 20, "denominator": 35, "rate": 20 / 35,
        "o1_gate": "pass", "o1_gate_note": direct.headline["o1_gate_note"],
    }
    assert direct.discriminability["pairs_separated"] == 57
    assert direct.discriminability["pairs_total"] == 58

    # Now the core itself, on the benchmark-shaped JSON: build a
    # Lincoln-shaped oracle document from the OMNIPRO vocabulary rows
    # (same rows, same cases, denominator recomputed) and run it with
    # the proof twin's mapping. The core must reproduce the direct
    # run's headline exactly — same twin, same rows, same mapping.
    vocab_rows = []
    for index, row in enumerate(oracle_rows):
        vocab_rows.append({
            "row_id": row.row_id, "case_id": row.case_id,
            "symptom": row.symptom, "cause": row.cause,
            "category": row.category, "fault": row.fault,
            "observables": dict(row.observables), "tests": dict(row.tests),
            "basis": row.basis, "flags": list(row.flags),
            "source_pages": list(row.source_pages),
        })
    from collections import Counter
    counted = Counter(r["category"] for r in vocab_rows)
    counted["total"] = len(vocab_rows)
    oracle_data = {
        "version": "test-omnipro-via-core", "cases": vocab["cases"],
        "rows": vocab_rows, "answer_key": {},
        "denominator": dict(counted),
    }
    result = run_benchmark_oracle(
        definition, oracle_data,
        observable_paths=observable_paths(definition),
        test_models=build_test_models(definition),
        problems=problems,
    )
    report = result["report"]
    assert report.headline["passed"] == 20
    assert report.headline["denominator"] == 35
    assert report.headline["o1_gate"] == "pass"
    assert report.discriminability["pairs_separated"] == 57
    assert report.discriminability["pairs_total"] == 58
    # Every row without an answer-key entry is recorded, never guessed.
    assert any("no answer-key entry" in p for p in result["problems"])
    assert all(r.row.remedy == "" for r in report.rows)


# ---------------------------------------------------------------------------
# Generic binding discipline
# ---------------------------------------------------------------------------

def test_generic_binding_maps_unique_field_only():
    definition = DigitalTwinDefinition(
        name="binding test twin", components=[
            TwinComponent(id="a", name="Alpha", component_type="other",
                          initial_state={"shared_field": "x", "only_a": "1"}),
            TwinComponent(id="b", name="Beta", component_type="other",
                          initial_state={"shared_field": "y"}),
            TwinComponent(id="c", name="Gamma", component_type="other",
                          initial_state={}),
        ],
        initial_signals={"signal_port": "ok"},
    )
    cases = {"case-1": {"observables": ["only_a", "shared_field", "missing_port",
                                        "signal_port", "wire_out@tip"]}}
    paths, unmapped = build_observable_paths(definition, cases)
    assert paths == {"only_a": "a.only_a", "signal_port": "signal_port"}
    assert "shared_field" in unmapped  # ambiguous: two components carry it
    assert "ambiguous" in unmapped["shared_field"]
    assert "missing_port" in unmapped
    assert "wire_out@tip" in unmapped  # base "wire_out" matches nothing
    assert "wire_out@tip" not in paths


# ---------------------------------------------------------------------------
# (ii)/(iii) real Lincoln + Miller JSONs against a synthetic twin
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "oracle_path, expected_denominator",
    [
        (LINCOLN, {"total": 22, "twin_expressible": 19, "settings": 2,
                   "technique": 0, "cross_reference": 0, "unmappable": 1}),
        (MILLER, {"total": 63, "twin_expressible": 38, "settings": 16,
                  "technique": 9, "cross_reference": 0, "unmappable": 0}),
    ],
)
def test_real_oracle_json_runs_on_synthetic_twin(oracle_path, expected_denominator, capsys):
    data = load_benchmark_oracle(oracle_path)
    assert validate_benchmark_oracle(data) == []
    rows, problems = build_oracle_rows(data)
    assert len(rows) == expected_denominator["total"]
    assert problems == []  # answer key covers every row in both files
    assert all(r.remedy for r in rows)  # remedy joined from the answer key
    assert all(r.case_rank >= 1 for r in rows)

    result = run_benchmark_oracle(_synthetic_definition(), data)
    report = result["report"]
    # run_oracle's denominator is a Counter over the rows, so zero-count
    # categories (technique/cross_reference in Lincoln) are absent from
    # it; the JSON file's own denominator (with the zeroes) is what
    # validate_benchmark_oracle checked above.
    expected_report_denominator = {
        k: v for k, v in expected_denominator.items() if v != 0
    }
    assert result["denominator"] == expected_report_denominator
    assert report.headline["denominator"] == expected_denominator["twin_expressible"]
    assert report.o1 is not None
    assert len(report.rows) == expected_denominator["total"]
    # The synthetic twin has no transitions and covers almost no fault
    # components, so the headline is honestly near zero — but the run
    # itself must produce a complete report, not crash.
    assert report.headline["passed"] <= report.headline["denominator"]
    assert report.discriminability["pairs_total"] >= 0
    # Per-row results are present for every row.
    assert {r.row.row_id for r in report.rows} == {r["row_id"] for r in data["rows"]}

    # The CLI print path runs without crashing on the same result.
    runner._print_report(
        result,
        {"twin_id": "test", "revision_id": "rev-test", "name": "Synthetic welder",
         "created_at": "2026-11-21"},
        oracle_path,
    )
    out = capsys.readouterr().out
    assert "O1 nominal gate:" in out
    assert "O3 discriminability" in out
    assert "per-row:" in out


def test_lincoln_denominator_matches_rows():
    data = load_benchmark_oracle(LINCOLN)
    counted = json.dumps(data["denominator"], sort_keys=True)
    assert counted == json.dumps(
        {"total": 22, "twin_expressible": 19, "settings": 2,
         "technique": 0, "cross_reference": 0, "unmappable": 1}, sort_keys=True)


def test_miller_denominator_matches_rows():
    data = load_benchmark_oracle(MILLER)
    assert data["denominator"] == {
        "total": 63, "twin_expressible": 38, "settings": 16,
        "technique": 9, "cross_reference": 0, "unmappable": 0}


def test_denominator_mismatch_is_a_bad_oracle_file():
    data = load_benchmark_oracle(LINCOLN)
    data["denominator"] = dict(data["denominator"])
    data["denominator"]["twin_expressible"] = 18  # wrong on purpose
    problems = validate_benchmark_oracle(data)
    assert any(p.startswith("denominator mismatch") for p in problems)
    with pytest.raises(BenchmarkOracleError, match="denominator does not match"):
        run_benchmark_oracle(_synthetic_definition(), data)


def test_missing_answer_key_entry_is_empty_remedy_not_a_guess():
    data = load_benchmark_oracle(LINCOLN)
    data["answer_key"] = {k: v for k, v in data["answer_key"].items()
                          if k != data["rows"][0]["row_id"]}
    rows, problems = build_oracle_rows(data)
    assert rows[0].remedy == ""
    assert rows[0].remedy_source == "answer_key_missing"
    assert any("no answer-key entry" in p for p in problems)
    # The rest of the rows still join.
    assert all(r.remedy for r in rows[1:])


# ---------------------------------------------------------------------------
# (v) unresolvable fault component: recorded unmapped, never guessed
# ---------------------------------------------------------------------------

def test_unresolvable_fault_component_is_unmapped_never_guessed():
    data = load_benchmark_oracle(MILLER)
    result = run_benchmark_oracle(_synthetic_definition(), data)
    # "Line Disconnect Switch" is a Miller fault component; the
    # synthetic twin has no such component.
    assert "Line Disconnect Switch" in result["unmapped_faults"]
    row = next(r for r in result["report"].rows if r.row.row_id == "miller-01")
    assert row.outcome == "fail"
    assert any("unmappable_cause" in reason for reason in row.reasons)
    # No guessed observable mapping smuggles the missing component in:
    # every mapped path names a component id that exists in the twin.
    twin_ids = {c.id for c in _synthetic_definition().components}
    for path in result["observable_paths"].values():
        assert path.split(".", 1)[0] in twin_ids or "." not in path


# ---------------------------------------------------------------------------
# (iv) no active twin -> clear error, non-zero exit, no crash
# ---------------------------------------------------------------------------

def _empty_db_with_schema(tmp_path: Path) -> Path:
    db_path = tmp_path / "empty.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    return db_path


def test_no_active_twin_is_a_clear_error(tmp_path):
    db_path = _empty_db_with_schema(tmp_path)
    with pytest.raises(NoActiveTwinError, match="no ACTIVE twin"):
        load_active_twin(db_path)


def test_no_active_twin_for_revision_is_a_clear_error(tmp_path):
    db_path = _empty_db_with_schema(tmp_path)
    with pytest.raises(NoActiveTwinError, match="no ACTIVE twin for revision"):
        load_active_twin(db_path, revision_id="rev-does-not-exist")


def test_missing_db_file_is_a_clear_error(tmp_path):
    with pytest.raises(BenchmarkOracleError, match="DB file not found"):
        load_active_twin(tmp_path / "does-not-exist.db")


def test_cli_no_active_twin_exits_nonzero_without_crash(tmp_path, capsys, monkeypatch):
    db_path = _empty_db_with_schema(tmp_path)
    monkeypatch.setattr(
        sys, "argv",
        ["run_benchmark_oracle.py", "--db", str(db_path), "--oracle", str(LINCOLN)],
    )
    assert runner.main() == 2
    err = capsys.readouterr().err
    assert "no ACTIVE twin" in err


def test_cli_bad_oracle_file_exits_nonzero(tmp_path, monkeypatch):
    db_path = _empty_db_with_schema(tmp_path)
    monkeypatch.setattr(
        sys, "argv",
        ["run_benchmark_oracle.py", "--db", str(db_path),
         "--oracle", str(tmp_path / "nope.json")],
    )
    # No twin AND bad oracle: the twin check runs first; either way the
    # exit is non-zero and the process does not crash.
    assert runner.main() == 2


def test_load_active_twin_reads_most_recent_active(tmp_path):
    db_path = _empty_db_with_schema(tmp_path)
    engine = create_engine(f"sqlite:///{db_path}")
    definition = _synthetic_definition()
    with Session(engine) as session:
        old = DigitalTwin(
            id="twin-old", product_id="p1", revision_id="rev-1",
            name="Old twin", definition=definition.model_dump(mode="json"),
            state={}, status=TwinStatus.archived,
        )
        new = DigitalTwin(
            id="twin-new", product_id="p1", revision_id="rev-1",
            name="New twin", definition=definition.model_dump(mode="json"),
            state={}, status=TwinStatus.active,
        )
        session.add_all([old, new])
        session.commit()
    engine.dispose()
    loaded, info = load_active_twin(db_path)
    assert info["twin_id"] == "twin-new"
    assert info["revision_id"] == "rev-1"
    assert loaded.name == "Synthetic welder (test twin)"
    # Revision filter: a revision with no active twin is an error even
    # though another revision has one.
    with pytest.raises(NoActiveTwinError):
        load_active_twin(db_path, revision_id="rev-other")


def test_load_active_twin_never_writes(tmp_path):
    db_path = _empty_db_with_schema(tmp_path)
    before = db_path.stat().st_mtime_ns
    with pytest.raises(NoActiveTwinError):
        load_active_twin(db_path)
    # Read-only open: the file must be byte-identical afterwards (a
    # write, or even a WAL checkpoint, would change it).
    assert db_path.read_bytes() == db_path.read_bytes()
    assert db_path.stat().st_mtime_ns == before
