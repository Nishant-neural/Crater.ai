"""Phase 0 scorer-reporting tests for benchmark/harness.py.

Proves the three reporting fixes WITHOUT changing any scoring rule:
(a) 0/0 symptoms and unrankable rows are reported separately from the
    rankable-rows headline; (b) citations are a separate axis from
    causes, and score_citations no longer pretends to verify page
    numbers unless page data is supplied; (c) normalize_text treats
    punctuation as a word boundary, so hyphenated causes are not
    penalised.

Pure / hermetic: no server, no network, no oracle files on disk — the
synthetic oracle below is built inline. The real Miller oracle is
exercised separately by `harness.py --dry-run`, which asserts the
same breakdown structure on the on-disk oracle.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))

import harness
from harness import (
    cause_matches,
    fault_matches,
    format_headline,
    normalize_text,
    score_citations,
    score_discriminating_test,
    score_top5,
    summarize_oracle_breakdown,
)


def _synthetic_oracle():
    """Mini oracle mirroring Miller's shape: one rankable symptom, one
    0/0 symptom (no twin-expressible rows), and unrankable rows."""
    return {
        "denominator": {"total": 6, "twin_expressible": 2,
                        "settings": 3, "technique": 1,
                        "cross_reference": 0, "unmappable": 0},
        "cases": {
            "syn-01": {"symptom": "Unit does not weld."},
            "syn-02": {"symptom": "Bead is ropey."},
        },
        "rows": [
            {"row_id": "syn-r1", "case_id": "syn-01",
             "category": "twin_expressible",
             "cause": "Have Factory Authorized Service Agent check "
                      "fan-on-demand circuit.",
             "fault": {"component": "Fan-On-Demand Circuit",
                       "field": "fault", "value": "faulty",
                       "kind": "fault_state"}},
            {"row_id": "syn-r2", "case_id": "syn-01",
             "category": "twin_expressible",
             "cause": "Be sure line disconnect switch is On.",
             "fault": {"component": "Line Disconnect Switch",
                       "field": "state", "value": "off",
                       "kind": "parameter"}},
            {"row_id": "syn-r3", "case_id": "syn-02",
             "category": "settings", "cause": "Excessive heat input.",
             "fault": None},
            {"row_id": "syn-r4", "case_id": "syn-02",
             "category": "settings", "cause": "Insufficient heat input.",
             "fault": None},
            {"row_id": "syn-r5", "case_id": "syn-02",
             "category": "settings", "cause": "Wrong gas.",
             "fault": None},
            {"row_id": "syn-r6", "case_id": "syn-02",
             "category": "technique", "cause": "Workpiece dirty.",
             "fault": None},
        ],
    }


def _synthetic_per_symptom():
    """Scores for the synthetic oracle: syn-01 scores 2/2 on causes
    (both rows in top 5) with 0/2 citations — the Miller-15 Gemini
    shape (4/4 causes, 0/4 citations) at small scale. syn-02 has no
    rankable rows, so it is 0/0 on causes but still ran a session
    with 1 cited claim."""
    return {
        "syn-01": {"rows_scored": 2, "rows_in_top5": 2,
                   "citations": {"cited": 0, "total": 2,
                                 "score": 0.0, "page_verified": False}},
        "syn-02": {"rows_scored": 0, "rows_in_top5": 0,
                   "citations": {"cited": 1, "total": 1,
                                 "score": 1.0, "page_verified": False}},
    }


def test_zero_row_symptoms_reported_separately():
    """(a) The 0/0 symptom is named, not hidden in the headline."""
    breakdown = summarize_oracle_breakdown(
        _synthetic_oracle(), _synthetic_per_symptom())
    assert breakdown["causes_top5"] == {"in_top5": 2, "rankable_rows": 2}
    assert breakdown["symptoms"]["rankable"] == ["syn-01"]
    assert breakdown["symptoms"]["rankable_count"] == 1
    assert breakdown["symptoms"]["zero_rows"] == ["syn-02"]
    assert breakdown["symptoms"]["zero_row_count"] == 1
    assert breakdown["unrankable_rows"]["total"] == 4
    assert breakdown["unrankable_rows"]["by_category"] == {
        "settings": 3, "technique": 1}
    assert breakdown["unrankable_rows"]["row_ids"] == [
        "syn-r3", "syn-r4", "syn-r5", "syn-r6"]
    assert breakdown["denominator"]["total"] == 6
    headline = format_headline(breakdown)
    assert "causes 2/2 rankable rows" in headline
    assert "syn-02" in headline  # the 0/0 symptom is named in the headline
    assert "unrankable rows excluded from causes: 4/6" in headline


def test_causes_and_citations_are_separate_axes():
    """(b) 2/2 causes with 0/2 citations stays visible as two numbers.

    This is the Miller-15 Gemini shape (4/4 causes, 0/4 citations):
    a merged headline would hide the citation failure."""
    breakdown = summarize_oracle_breakdown(
        _synthetic_oracle(), _synthetic_per_symptom())
    assert breakdown["causes_top5"] == {"in_top5": 2, "rankable_rows": 2}
    assert breakdown["citations"] == {"cited": 1, "claims": 3,
                                      "page_verified": False}
    # The per-symptom split is what makes the Miller-15 shape visible:
    syn01 = _synthetic_per_symptom()["syn-01"]
    assert syn01["rows_in_top5"] == syn01["rows_scored"] == 2
    assert syn01["citations"]["cited"] == 0
    assert syn01["citations"]["total"] == 2
    headline = format_headline(breakdown)
    assert "causes 2/2 rankable rows" in headline
    assert "citations 1/3 claims" in headline
    assert "page NOT verified" in headline


def test_score_citations_default_is_chunk_id_presence_only():
    """(b) Without page data, the old chunk-id rule holds exactly and
    the result says so (page_verified=False)."""
    hyps = [
        {"cause": "A", "supporting_evidence_chunk_ids": ["chunk-1"]},
        {"cause": "B", "supporting_evidence_chunk_ids": []},
    ]
    assert score_citations(hyps) == {
        "score": 0.5, "cited": 1, "total": 2, "page_verified": False}
    assert score_citations([]) == {
        "score": 0.0, "cited": 0, "total": 0, "page_verified": False}


def test_score_citations_page_check_when_pages_supplied():
    """(b) With a chunk-id -> page mapping, a chunk without a page
    does not count as cited. The mapping is the only new input; no
    other behaviour changes."""
    hyps = [
        {"cause": "A", "supporting_evidence_chunk_ids": ["chunk-1"]},
        {"cause": "B", "supporting_evidence_chunk_ids": ["chunk-2"]},
        {"cause": "C", "supporting_evidence_chunk_ids": ["chunk-3"]},
    ]
    pages = {"chunk-1": 12, "chunk-2": None}  # chunk-3 absent entirely
    assert score_citations(hyps, chunk_pages=pages) == {
        "score": 1 / 3, "cited": 1, "total": 3, "page_verified": True}


def test_normalize_text_hyphen_is_a_word_boundary():
    """(c) Punctuation becomes a space, not nothing."""
    assert normalize_text("Faulty fan-on-demand circuit") == \
        "faulty fan on demand circuit"
    assert normalize_text("Fan-On-Demand Circuit") == "fan on demand circuit"
    assert normalize_text("HL.P 001 appears on meters.") == \
        "hl p 001 appears on meters"
    assert normalize_text("poor_connection") == "poor connection"
    assert normalize_text("  spaced   out  ") == "spaced out"
    assert normalize_text("") == ""


def test_hyphenated_cause_matches_spaced_phrasing():
    """(c) String match no longer penalises hyphenation either way."""
    assert cause_matches("Faulty fan-on-demand circuit",
                         "Faulty fan on demand circuit")
    assert cause_matches("Faulty fan on demand circuit",
                         "Faulty fan-on-demand circuit")
    # And the fault-aware rule that saves the Miller-15 row is intact:
    # the ranked cause names the structured fault (component + value).
    assert score_top5(
        ["Faulty fan-on-demand circuit"],
        "Have Factory Authorized Service Agent check fan-on-demand circuit.",
        {"component": "Fan-On-Demand Circuit", "field": "fault",
         "value": "faulty", "kind": "fault_state"}) == {
             "rank": 1, "score": 1.0, "in_top5": True}


def test_hyphenated_cause_counts_in_discriminating_test():
    """(c) The discriminating test compares normalised phrases, so a
    hyphenated cause must be found in step text that writes it
    spaced. Under the old deleter this scored 0."""
    step = {"type": "action",
            "content": "Run a test to separate faulty fan on demand circuit "
                       "from main control board faulty.",
            "rationale": ""}
    ranked = ["Faulty fan-on-demand circuit", "Main Control Board faulty"]
    assert score_discriminating_test(step, ranked)["score"] == 1.0


def test_fault_aware_rule_unchanged():
    """The fault-matching rule itself is untouched: component without
    value, antonym value, substring value, and boolean faults still
    score 0; the same fault in different words still scores 1."""
    miller01_fault = {"component": "Line Disconnect Switch",
                      "field": "state", "value": "off", "kind": "parameter"}
    miller01_remedy = "Be sure line disconnect switch is On (see Section 4-13)."
    assert score_top5(["Line disconnect switch is off"],
                      miller01_remedy, miller01_fault)["score"] == 1.0
    assert score_top5(["Check the line disconnect switch"],
                      miller01_remedy, miller01_fault)["score"] == 0.0
    assert score_top5(["Line disconnect switch is off"],
                      miller01_remedy,
                      {**miller01_fault, "value": "on"})["score"] == 0.0
    assert score_top5(["Input power is below normal"],
                      "Connect unit to proper input voltage or check for low "
                      "line voltage (see Section 4-13).",
                      {"component": "Input Power", "field": "input_voltage",
                       "value": "low", "kind": "parameter"})["score"] == 0.0
    assert score_top5(["Incorrect wire feed rolls."],
                      "Change to correct size drive rolls (see Section 6-3).",
                      {"component": "Wire Feed Rolls",
                       "field": "groove_matches", "value": False,
                       "kind": "parameter"})["score"] == 0.0
    # fault_matches itself: unchanged entry point, same answers.
    assert fault_matches("Line disconnect switch is off", miller01_fault)
    assert not fault_matches("Line disconnect switch is on", miller01_fault)


def test_glued_spelling_no_longer_matches_hyphenated():
    """Documents the one intentional behaviour change from the
    normalize_text fix: 'wirefeed' (glued) no longer matches
    'wire-feed' (hyphenated), because neither normalised string
    contains the other. Both sides are normalised the same way, so a
    match now requires the same word boundaries on both sides."""
    assert not cause_matches("wirefeed problem", "wire-feed problem")
    assert normalize_text("wirefeed problem") == "wirefeed problem"
    assert normalize_text("wire-feed problem") == "wire feed problem"


if __name__ == "__main__":
    # Standalone runner (no pytest needed): runs every test_* above.
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {name}: {exc}")
        else:
            print(f"PASS {name}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
