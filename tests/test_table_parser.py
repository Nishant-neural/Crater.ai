"""Phase 1 deterministic table parser + registry tests (§6.2, §6.5)."""
from backend.ingestion.chunking import _table_to_markdown
from backend.ingestion.table_parser import (
    EXTRACTION_METHOD,
    parse_spec_markdown,
    parse_table_chunk,
    parse_troubleshooting_markdown,
    parse_troubleshooting_tables,
)
from backend.knowledge.repair_cases import (
    TIER_1,
    TIER_3,
    coverage_for_registry,
    load_registry,
    tier_for_chunk,
)
from backend.knowledge.machine_model import UniversalMachineModel

# p42 shape: one symptom row with the 4 case-1 causes, numbered.
_P42_TABLE = [
    ["Problem", "Possible Causes", "Likely Solutions"],
    [
        "Wire Feed Motor Runs but Wire Does Not Feed Properly",
        "1. Insufficient wire feed pressure.\n2. Incorrect wire feed roller size.\n"
        "3. Damaged MIG Gun, cable, or liner assembly.\n4. Feed Tensioner is too tight.",
        "1. Increase wire feed pressure properly (step 27, page 17).\n"
        "2. Flip roll to correct size (page 12).\n"
        "3. Have a qualified technician inspect and replace as necessary.\n"
        "4. Loosen Feed Tensioner.",
    ],
]
_P42_MD = _table_to_markdown(_P42_TABLE)

_P7_MD = _table_to_markdown([
    ["Parameter", "MIG", "TIG"],
    ["Input voltage", "120 VAC", "120 VAC"],
    ["Output current", "86 A", "86 A"],
])


def test_p42_troubleshooting_table_parses_to_case1_failure_mode():
    model = parse_troubleshooting_markdown(_P42_MD, source_document="owner-manual.pdf", page=42, chunk_id="c1")
    assert model is not None
    assert len(model.failure_modes) == 1
    fm = model.failure_modes[0]
    assert fm.name == "Wire Feed Motor Runs but Wire Does Not Feed Properly"
    assert fm.possible_causes == [
        "Insufficient wire feed pressure.",
        "Incorrect wire feed roller size.",
        "Damaged MIG Gun, cable, or liner assembly.",
        "Feed Tensioner is too tight.",
    ]
    assert fm.evidence[0].source_type == "troubleshooting_table"
    assert fm.evidence[0].extraction_method == EXTRACTION_METHOD
    assert fm.evidence[0].page == 42
    # Repair procedure carried alongside, from the Likely Solutions cell.
    assert len(model.procedures) == 1
    assert len(model.procedures[0].steps) == 4


def test_p42_parses_from_pdfplumber_tables_too():
    model = parse_troubleshooting_tables([_P42_TABLE], source_document="owner-manual.pdf", page=42, chunk_id="c1")
    assert model is not None
    assert len(model.failure_modes[0].possible_causes) == 4


def test_malformed_table_falls_back_not_dropped():
    # No header -> None (LLM fallback), never a partial model.
    assert parse_troubleshooting_markdown("| a | b |\n| --- | --- |\n| x | y |", page=42, chunk_id="c2") is None
    assert parse_table_chunk("no table here", "troubleshooting_table", page=42, chunk_id="c2") is None
    assert parse_table_chunk(_P42_MD, "procedure", page=42, chunk_id="c2") is None


def test_p7_spec_rows_parse_to_quantities():
    model = parse_spec_markdown(_P7_MD, source_document="owner-manual.pdf", page=7, chunk_id="c3")
    assert model is not None
    values = {(q.name, q.value, q.unit) for q in model.quantities}
    assert ("Input voltage", "120", "VAC") in values
    assert ("Output current", "86", "A") in values
    assert all(q.evidence[0].source_type == "spec_table" for q in model.quantities)
    assert all(q.evidence[0].extraction_method == EXTRACTION_METHOD for q in model.quantities)


def test_dispatch_by_router_label():
    assert parse_table_chunk(_P42_MD, "troubleshooting_table", page=42, chunk_id="c1") is not None
    assert parse_table_chunk(_P7_MD, "spec_table", page=7, chunk_id="c3") is not None
    assert parse_table_chunk(_P42_MD, "spec_table", page=42, chunk_id="c1") is None


def test_registry_loads_nine_cases_each_citing_pages():
    registry = load_registry()
    assert len(registry.cases) == 9
    assert {c.rank for c in registry.cases} == set(range(1, 10))
    for case in registry.cases:
        assert case.table_page in (42, 43)
        assert case.tier1_pages
        for cause in case.causes:
            assert cause.fix or cause.test
    assert registry.tier1_pages == [7, 12, 13, 17, 41, 42, 43]
    assert registry.tier3_pages == [35, 36, 37, 38, 39, 40]


class _FakeChunk:
    def __init__(self, page_number=None, pages=None, extra=None):
        self.page_number = page_number
        self.extra = extra or {}
        if pages is not None:
            self.extra["pages"] = pages


def test_tier_gating_tier1_wins_and_tier3_only_when_all_pages_tier3():
    registry = load_registry()
    assert tier_for_chunk(_FakeChunk(page_number=42), registry) == TIER_1
    assert tier_for_chunk(_FakeChunk(page_number=35), registry) == TIER_3
    assert tier_for_chunk(_FakeChunk(pages=[35, 42]), registry) == TIER_1
    assert tier_for_chunk(_FakeChunk(pages=[35, 36]), registry) == TIER_3
    assert tier_for_chunk(_FakeChunk(page_number=44), registry) != TIER_3  # Tier 2, not gated


def test_coverage_for_registry_against_parsed_p42_model():
    registry = load_registry()
    snapshot = parse_troubleshooting_markdown(_P42_MD, source_document="owner-manual.pdf", page=42, chunk_id="c1")
    coverage = coverage_for_registry(registry, snapshot)
    case1 = next(c for c in coverage if c["symptom"] == "Wire Feed Motor Runs but Wire Does Not Feed Properly")
    assert case1["total_causes"] == 4
    assert case1["bound_causes"] == 4
    assert case1["coverage"] == 1.0
    # An empty snapshot binds nothing (coverage is honest, not assumed).
    empty = coverage_for_registry(registry, UniversalMachineModel())
    assert all(c["bound_causes"] == 0 for c in empty)


# ---------------------------------------------------------------------------
# Step 1 (Phase 1): Miller 2-col tables + TableRow. Fixtures are built
# from the frozen oracle files in the repo -- no text invented:
#   benchmark/evaluations/miller_parser_rows.json (63 parser rows)
#   frozen/c002b-miller.json (63 oracle rows, 38 twin_expressible)
#   frozen/omnipro-oracle-35.json (35 twin-expressible OMNIPRO rows)
# NOTE on "§6-5 = 38 rows": §6-5 proper (Trouble | Remedy, PDF p. 35)
# is 21 rows, 18 twin-expressible. The 38 is the twin-expressible
# count across ALL Miller troubleshooting tables (§6-5 + §§8-8..8-15
# + §8-17 = 63 parser rows). The tests assert both numbers, so the
# loose "§6-5 = 38" shorthand can never silently become a claim
# that §6-5 alone yields 38 rows.
# ---------------------------------------------------------------------------
import json as _json
import re as _re
from collections import Counter as _Counter
from pathlib import Path as _Path

from backend.ingestion.table_parser import (
    classify_cause,
    extract_meter_code,
    parse_alarm_rows,
    parse_cause_action_2col_rows,
    parse_table_rows,
    parse_tables_to_rows,
    parse_troubleshooting_2col_rows,
    parse_troubleshooting_3col_rows,
    table_rows_to_model,
)
from backend.knowledge.repair_cases import load_miller_registry

_REPO = _Path(__file__).resolve().parent.parent


def _miller_parser_rows():
    return _json.loads((_REPO / "benchmark/evaluations/miller_parser_rows.json").read_text())


def _miller_oracle():
    return _json.loads((_REPO / "frozen/c002b-miller.json").read_text())


def _miller_6_5_markdown(rows):
    """§6-5 Trouble | Remedy Markdown, pdfplumber-shaped: the Trouble
    cell is filled on the first row of a symptom, empty after."""
    lines = ["| Trouble | Remedy |", "| --- | --- |"]
    current = None
    for r in rows:
        trouble = r["symptom"] if r["symptom"] != current else ""
        current = r["symptom"]
        lines.append(f"| {trouble} | {r['remedy']} |")
    return "\n".join(lines)


def _miller_cause_action_markdown(symptom, rows):
    """§§8-8..8-15 Possible Causes | Corrective Actions Markdown, with
    the defect title row above the header (as pdfplumber extracts it)."""
    lines = [
        f"| {symptom} | |",
        "| --- | --- |",
        "| Possible Causes | Corrective Actions |",
        "| --- | --- |",
    ]
    for r in rows:
        lines.append(f"| {r['cause']} | {r['remedy']} |")
    return "\n".join(lines)


def _miller_8_17_markdown(rows):
    """§8-17 Problem | Probable Cause | Remedy Markdown. The doubled-
    letter print artifact in the miller-44 / miller-48 Problem cells
    is reproduced from the frozen parser rows' own flags (the text
    between (' and ' ->), not invented."""
    artifact = {}
    for r in rows:
        for flag in r.get("flags") or []:
            m = _re.search(r"repaired \('(.*)' -> ", flag)
            if m:
                artifact[r["row_id"]] = m.group(1)
    lines = ["| Problem | Probable Cause | Remedy |", "| --- | --- | --- |"]
    current = None
    for r in rows:
        if r["symptom"] != current:
            problem = artifact.get(r["row_id"], r["symptom"])
            current = r["symptom"]
        else:
            problem = ""
        lines.append(f"| {problem} | {r['cause']} | {r['remedy']} |")
    return "\n".join(lines)


def _miller_all_rows():
    """(rows_6_5, rows_cause_action_by_symptom, rows_8_17) from frozen."""
    prows = _miller_parser_rows()
    rows_65 = [r for r in prows if r["row_id"] <= "miller-21"]
    rows_817 = [r for r in prows if r["row_id"] >= "miller-44"]
    mid = [r for r in prows if "miller-22" <= r["row_id"] <= "miller-43"]
    by_symptom = {}
    for r in mid:
        by_symptom.setdefault(r["symptom"], []).append(r)
    return rows_65, by_symptom, rows_817


def test_miller_6_5_two_col_parses_to_21_rows_with_meter_codes():
    rows_65, _, _ = _miller_all_rows()
    md = _miller_6_5_markdown(rows_65)
    rows = parse_table_rows(
        md, "troubleshooting_table_2col",
        source_document="owners-manual-om-230-693c.pdf", page=35, chunk_id="c65",
    )
    assert len(rows) == 21
    assert sum(1 for r in rows if r.twin_expressible) == 18
    assert all(r.table_shape == "troubleshooting_2col" for r in rows)
    assert all(r.cause == r.remedy for r in rows)
    assert all("cause_from_remedy" in " ".join(r.flags) for r in rows)
    assert all(r.remedy_source == "remedy_column_only" for r in rows)
    assert all(r.extraction_method == EXTRACTION_METHOD and r.confidence == 1.0 for r in rows)
    assert all(r.source_table is None for r in rows)
    assert all(r.page == 35 and r.chunk_id == "c65" for r in rows)
    # HL.P meter codes attach to the parent row, never stand alone.
    by_cause = {r.cause: r for r in rows}
    assert by_cause["HL.P 001 appears on meters. Turn power switch off and back on, if HL.P 001 appears on meters again, have Factory Authorized Service Agent check unit."].meter_code == "HL.P 001"
    assert extract_meter_code("HL.P 002 appears on meters. Thermistor T is detecting an overheating condition. Wait for unit to cool allowing the fan to run. After unit is cool, if HL.P 002 message remains, have Factory Authorized Service Agent check for an open Thermistor T (see Section 6-2).") == "HL.P 002"
    assert by_cause["HL.P 004 appears on meters. Reset message by releasing the trigger or removing stuck wire causing short circuit (see Section 5-2) . If message remains, have Factory Authorized Service Agent check for shorted trigger leads."].meter_code == "HL.P 004"
    assert by_cause["HL.P 003 appears on meters, have Factory Authorized Service Agent check main control board and main rectifier."].meter_code == "HL.P 003"
    assert {r.meter_code for r in rows if r.meter_code} == {"HL.P 001", "HL.P 002", "HL.P 003", "HL.P 004"}
    assert not any(r.symptom.startswith("HL.P") for r in rows)
    # Per the 23 Nov 2026 harness correction, HL.P rows and the
    # service-agent rows are rankable (structured faults in the oracle).
    assert all(r.rankable == r.twin_expressible for r in rows)
    assert all(r.rankable_reason == "concrete" for r in rows if r.rankable)
    # Per-symptom counts: 8 / 3 / 3 / 1 / 6.
    counts = _Counter(r.symptom for r in rows)
    assert counts == _Counter({
        "No weld output; wire does not feed.": 8,
        "No Weld Output; wire feeds.": 3,
        "Low weld output.": 3,
        "Fan motor does not run.": 1,
        "Low, high, or erratic wire speed.": 6,
    })


def test_miller_full_troubleshooting_parses_to_63_rows_38_twin_expressible():
    prows = _miller_parser_rows()
    oracle = _miller_oracle()
    want_cat = {r["row_id"]: r["category"] for r in oracle["rows"]}
    assert oracle["denominator"] == {"total": 63, "twin_expressible": 38, "settings": 16, "technique": 9, "cross_reference": 0, "unmappable": 0}

    rows_65, by_symptom, rows_817 = _miller_all_rows()
    parsed = []
    parsed.extend(parse_table_rows(
        _miller_6_5_markdown(rows_65), "troubleshooting_table_2col",
        source_document="owners-manual-om-230-693c.pdf", page=35, chunk_id="c65",
        source_table="6-5 Troubleshooting (p. 31)",
    ))
    for symptom, srows in by_symptom.items():
        parsed.extend(parse_table_rows(
            _miller_cause_action_markdown(symptom, srows), "troubleshooting_table_2col",
            source_document="owners-manual-om-230-693c.pdf", page=45, chunk_id="c8x",
        ))
    parsed.extend(parse_table_rows(
        _miller_8_17_markdown(rows_817), "troubleshooting_table",
        source_document="owners-manual-om-230-693c.pdf", page=48, chunk_id="c817",
        source_table="8-17 Troubleshooting Guide for Semiautomatic Welding Equipment",
    ))
    assert len(parsed) == 63
    assert _Counter(r.cause_category for r in parsed) == _Counter(
        {"twin_expressible": 38, "settings": 16, "technique": 9}
    )
    assert sum(1 for r in parsed if r.twin_expressible) == 38
    assert sum(1 for r in parsed if r.rankable) == 38
    # Row-for-row diff against the frozen parser rows (order = table order).
    for got, exp in zip(parsed, prows):
        assert got.symptom == exp["symptom"], exp["row_id"]
        assert got.cause == exp["cause"], exp["row_id"]
        assert got.remedy == exp["remedy"], exp["row_id"]
        assert got.twin_expressible == (want_cat[exp["row_id"]] == "twin_expressible"), exp["row_id"]
        assert got.cause_category == want_cat[exp["row_id"]], exp["row_id"]
    # §8-17 print-artifact repair and as-printed grouping, from the flags.
    repaired = [r for r in parsed if any("print_artifact_repaired" in f for f in r.flags)]
    assert len(repaired) == 2
    assert {r.symptom for r in repaired} == {
        "Wire feed motor operates, but wire does not feed.",
        "Wire curling up in front of the wire feed rolls (bird nesting).",
    }
    grouped = [r for r in parsed if any("grouping_as_printed" in f for f in r.flags)]
    assert len(grouped) == 1
    assert grouped[0].symptom == "Wire feeds, but no gas flows."
    assert grouped[0].cause.startswith("Incorrect primary voltage")
    # Provenance: OEM owner manual, rank 2.
    assert all(r.provenance_precedence == "oem" for r in parsed)


def test_miller_rows_build_17_failure_modes():
    from backend.ingestion.table_parser import markdown_to_rows as _md_rows
    rows_65, by_symptom, rows_817 = _miller_all_rows()
    parsed = []
    parsed.extend(parse_troubleshooting_2col_rows(
        _md_rows(_miller_6_5_markdown(rows_65)),
        source_document="owners-manual-om-230-693c.pdf", page=35, chunk_id="c65",
    ))
    for symptom, srows in by_symptom.items():
        parsed.extend(parse_cause_action_2col_rows(
            _md_rows(_miller_cause_action_markdown(symptom, srows)),
            source_document="owners-manual-om-230-693c.pdf", page=45, chunk_id="c8x",
        ))
    parsed.extend(parse_troubleshooting_3col_rows(
        _md_rows(_miller_8_17_markdown(rows_817)),
        source_document="owners-manual-om-230-693c.pdf", page=48, chunk_id="c817",
    ))
    model = table_rows_to_model(parsed)
    assert model is not None
    assert len(model.failure_modes) == 17
    counts = {fm.name: len(fm.possible_causes) for fm in model.failure_modes}
    assert counts == {
        "No weld output; wire does not feed.": 8,
        "No Weld Output; wire feeds.": 3,
        "Low weld output.": 3,
        "Fan motor does not run.": 1,
        "Low, high, or erratic wire speed.": 6,
        "Excessive Spatter": 6,
        "Porosity": 5,
        "Excessive Penetration": 1,
        "Lack Of Penetration": 3,
        "Incomplete Fusion": 3,
        "Burn-Through": 1,
        "Waviness Of Bead": 2,
        "Distortion": 1,
        "Wire feed motor operates, but wire does not feed.": 4,
        "Wire curling up in front of the wire feed rolls (bird nesting).": 4,
        "Wire feeds, but no gas flows.": 7,
        "Welding arc not stable.": 5,
    }
    assert len(model.procedures) == 17


def test_cause_category_matches_both_oracles():
    # Miller: all 63 parser rows (cause + remedy) vs oracle categories.
    oracle = _miller_oracle()
    want_cat = {r["row_id"]: r["category"] for r in oracle["rows"]}
    for pr in _miller_parser_rows():
        assert classify_cause(pr["cause"], pr["remedy"]) == want_cat[pr["row_id"]], pr["row_id"]
    # OMNIPRO: the 35 frozen twin rows classify twin_expressible...
    omni = _json.loads((_REPO / "frozen/omnipro-oracle-35.json").read_text())
    assert len(omni["rows"]) == 35
    for r in omni["rows"]:
        assert classify_cause(r["cause"]) == "twin_expressible", r["cause"]
    # ...and the 10 non-twin vocabulary rows (verbatim wording from
    # oracle_vocabulary_v1.json; the frozen 35-row file holds only the
    # twin subset) classify settings / technique / cross-reference.
    non_twin = [
        ("Wire not feeding properly.", "cross_reference",
         "See first Troubleshooting section above (case 1)."),
        ("Incorrect wire feed speed.", "settings", ""),
        ("Incorrect polarity for process being run.", "settings", ""),
        ("Machine is in the incorrect mode.", "settings", ""),
        ("Polarity is incorrect for the application.", "settings", ""),
        ("Not enough current.", "settings", ""),
        ("Dirty Contact Tip.", "technique", ""),
        ("Dirty workpiece.", "technique", ""),
        ("Gun is being used too far away from workpiece.", "technique", ""),
        ("Dirty welding wire is introducing contamination into the weld.", "technique", ""),
    ]
    for cause, want, remedy in non_twin:
        assert classify_cause(cause, remedy) == want, cause


def _omnipro_page_markdown(page_cases):
    """One 3-col Markdown table for a manual page, from registry cases:
    one row per case, causes/solutions as numbered lists."""
    lines = ["| Problem | Possible Causes | Likely Solutions |", "| --- | --- | --- |"]
    for case in page_cases:
        causes = "\n".join(f"{i + 1}. {c.cause}" for i, c in enumerate(case.causes))
        solutions = "\n".join(f"{i + 1}. {c.fix}" for i, c in enumerate(case.causes))
        lines.append(f"| {case.symptom} | {causes} | {solutions} |")
    return "\n".join(lines)


def test_omnipro_p42_p43_parse_to_45_rows_no_regression():
    registry = load_registry()
    p42 = [c for c in registry.cases if c.table_page == 42]
    p43 = [c for c in registry.cases if c.table_page == 43]
    assert len(p42) + len(p43) == 9
    rows = []
    rows.extend(parse_table_rows(
        _omnipro_page_markdown(p42), "troubleshooting_table",
        source_document="owner-manual.pdf", page=42, chunk_id="omni42",
    ))
    rows.extend(parse_table_rows(
        _omnipro_page_markdown(p43), "troubleshooting_table",
        source_document="owner-manual.pdf", page=43, chunk_id="omni43",
    ))
    assert len(rows) == 45
    assert _Counter(r.cause_category for r in rows) == _Counter(
        {"twin_expressible": 35, "settings": 5, "technique": 4, "cross_reference": 1}
    )
    assert sum(1 for r in rows if r.twin_expressible) == 35
    cross = [r for r in rows if r.cause_category == "cross_reference"]
    assert len(cross) == 1 and cross[0].cause == "Wire not feeding properly."
    assert cross[0].symptom == "Welding Arc Not Stable"
    # The model built from the same rows still covers every case 100%.
    model = table_rows_to_model(rows)
    assert model is not None and len(model.failure_modes) == 9
    coverage = coverage_for_registry(registry, model)
    assert all(c["coverage"] == 1.0 for c in coverage)
    assert sum(c["bound_causes"] for c in coverage) == 45


def test_alarm_table_parses_to_rows_with_code_in_symptom():
    rows = parse_alarm_rows(
        [
            ["Alarm Code", "Message", "Cause", "Remedy"],
            ["M01 0001", "Dog overrun", "Servo error", "Check axis"],
            ["M01 0002", "Overload", "Motor overloaded", "Reduce load"],
        ],
        source_document="alarm-manual.pdf", page=3, chunk_id="a1",
    )
    assert len(rows) == 2
    assert rows[0].table_shape == "alarm_table"
    assert rows[0].symptom == "M01 0001 Dog overrun"
    assert rows[0].cause == "Servo error"
    assert rows[0].remedy == "Check axis"
    assert rows[0].rankable and rows[0].twin_expressible
    assert rows[1].symptom == "M01 0002 Overload"
    # Alarm dispatch through the chunk entry point.
    from backend.ingestion.table_parser import parse_table_chunk as _ptc
    md = "| Alarm Code | Message | Cause | Remedy |\n| --- | --- | --- | --- |\n| M01 0001 | Dog overrun | Servo error | Check axis |"
    model = _ptc(md, "alarm_table", page=3, chunk_id="a1")
    assert model is not None and len(model.failure_modes) == 1


def test_miller_registry_loads_17_cases_tier1():
    registry = load_miller_registry()
    assert len(registry.cases) == 17
    assert sum(len(c.causes) for c in registry.cases) == 63
    assert {c.rank for c in registry.cases} == set(range(1, 18))
    assert registry.tier1_pages == [35, 45, 46, 47, 48, 49]
    assert registry.tier3_pages == []
    for case in registry.cases:
        assert case.tier1_pages
        for cause in case.causes:
            assert cause.fix
    from backend.knowledge.repair_cases import (
        TIER_1,
        TIER_2,
        registry_matches_product,
        tier_for_chunk,
    )
    assert registry_matches_product(registry, "Miller Electric", "Millermatic", "252")
    assert not registry_matches_product(registry, "Harbor Freight", "OMNIPRO", "220")
    assert tier_for_chunk(_FakeChunk(page_number=35), registry) == TIER_1
    assert tier_for_chunk(_FakeChunk(page_number=48), registry) == TIER_1
    assert tier_for_chunk(_FakeChunk(page_number=7), registry) == TIER_2


def test_table_row_provenance_precedence():
    from backend.ingestion.table_parser import _make_row
    row = _make_row(
        table_shape="troubleshooting_3col", symptom="S", cause="C",
        cause_index=0, remedy="R", remedy_index=0, remedy_source="remedy_column",
        source_document="owner-manual.pdf", source_type="troubleshooting_table",
        source_table=None, page=42, chunk_id="c1",
    )
    assert row.provenance_precedence == "oem"
    assert row.extraction_method == EXTRACTION_METHOD
    assert row.confidence == 1.0
    mtb = _make_row(
        table_shape="troubleshooting_3col", symptom="S", cause="C",
        cause_index=0, remedy="R", remedy_index=0, remedy_source="remedy_column",
        source_document="MTB builder manual IB-1501610.pdf", source_type="troubleshooting_table",
        source_table=None, page=1, chunk_id="c1",
    )
    assert mtb.provenance_precedence == "mtb"
