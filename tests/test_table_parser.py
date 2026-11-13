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
