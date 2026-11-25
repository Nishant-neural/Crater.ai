"""Phase 1 source-router unit tests (phase1-implementation.md §6.1)."""
from backend.ingestion.source_router import (
    SourceType,
    classify_source_type,
    merge_page_source_types,
    normalize_source_type,
)

_TROUBLE_TABLE = [
    [["Problem", "Possible Causes", "Likely Solutions"],
     ["Wire does not feed", "1. Low pressure\n2. Wrong roller", "1. Increase pressure\n2. Flip roll"]],
]
_ALARM_TABLE = [
    [["Alarm Code", "Message", "Cause", "Remedy"],
     ["M01 0001", "Dog overrun", "Servo error", "Check axis"]],
]
_SPEC_TABLE = [
    [["Parameter", "MIG", "TIG"],
     ["Input voltage", "120 VAC", "120 VAC"],
     ["Output current", "86 A", "86 A"]],
]


def test_troubleshooting_header_labels_table():
    assert classify_source_type(42, "Troubleshooting", _TROUBLE_TABLE, []) is SourceType.TROUBLESHOOTING_TABLE


def test_alarm_code_first_column_labels_alarm_table():
    assert classify_source_type(1, "Alarms", _ALARM_TABLE, []) is SourceType.ALARM_TABLE


def test_spec_rows_with_heading_label_spec_table():
    assert classify_source_type(7, "Specifications\n", _SPEC_TABLE, []) is SourceType.SPEC_TABLE


def test_step_dense_prose_labels_procedure():
    text = "Setup\n1. First do this.\n2. Then do that.\n3. Finally check.\n"
    assert classify_source_type(10, text, [], []) is SourceType.PROCEDURE


def test_maintenance_heading_wins_over_procedure():
    text = "Maintenance\n1. First do this.\n2. Then do that.\n3. Finally check.\n"
    assert classify_source_type(10, text, [], []) is SourceType.MAINTENANCE_PROCEDURE


def test_image_only_page_labels_diagram():
    assert classify_source_type(5, "", [], ["page5.png"]) is SourceType.DIAGRAM


def test_ambiguous_page_labels_other():
    assert classify_source_type(5, "Some random prose without steps.", [], []) is SourceType.OTHER


def test_normalisation_gate_reroutes_label_without_table():
    # Label claims troubleshooting but there is no table structure.
    assert normalize_source_type("troubleshooting_table", tables=[], text="random prose") is SourceType.OTHER
    assert normalize_source_type("alarm_table", tables=[], text="random prose") is SourceType.OTHER
    assert normalize_source_type("spec_table", tables=[], text="random prose") is SourceType.OTHER
    # With a real table the label survives.
    assert normalize_source_type("troubleshooting_table", tables=_TROUBLE_TABLE) is SourceType.TROUBLESHOOTING_TABLE
    assert normalize_source_type("nonsense", tables=_TROUBLE_TABLE) is SourceType.OTHER


def test_cross_page_chunk_rule_first_label_plus_list():
    first, all_labels = merge_page_source_types(["procedure", "troubleshooting_table", "procedure"])
    assert first == "procedure"
    assert all_labels == ["procedure", "troubleshooting_table"]
    assert merge_page_source_types([]) == ("other", ["other"])


# ---------------------------------------------------------------------------
# Step 1 (Phase 1, P-01): 2-column troubleshooting tables must route to
# the deterministic parser, never to `other` (the LLM fallback that
# truncated Miller §6-5 to 4 causes shown of 8 true for miller-01).
# ---------------------------------------------------------------------------
_MILLER_6_5_TABLE = [
    [["Trouble", "Remedy"],
     ["No weld output; wire does not feed.", "Be sure line disconnect switch is On (see Section 4-13)."],
     ["", "Replace building line fuse or reset circuit breaker if open (see Section 4-13)."]],
]
_MILLER_8_8_TABLE = [
    [["Excessive Spatter", ""],
     ["Possible Causes", "Corrective Actions"],
     ["Wire feed speed too high.", "Select lower wire feed speed."],
     ["Voltage too high.", "Select lower voltage range."]],
]
_MILLER_8_17_TABLE = [
    [["Problem", "Probable Cause", "Remedy"],
     ["Wire feed motor operates, but wire does not feed.", "Too little pressure on wire feed rolls.", "Increase pressure setting on wire feed rolls."],
     ["", "Incorrect wire feed rolls.", "Check size stamped on wire feed rolls, replace to match wire size and type if necessary."]],
]


def test_miller_6_5_two_col_labels_troubleshooting_2col():
    assert classify_source_type(35, "Troubleshooting", _MILLER_6_5_TABLE, []) is SourceType.TROUBLESHOOTING_TABLE_2COL


def test_miller_cause_action_table_labels_troubleshooting_2col():
    # Title row above the header: the router must look past it.
    assert classify_source_type(45, "Troubleshooting", _MILLER_8_8_TABLE, []) is SourceType.TROUBLESHOOTING_TABLE_2COL


def test_miller_8_17_probable_cause_labels_troubleshooting_table():
    # "Probable Cause" is §8-17's wording; the old header test
    # required "possible cause" only, so this classified as `other`.
    assert classify_source_type(48, "Troubleshooting", _MILLER_8_17_TABLE, []) is SourceType.TROUBLESHOOTING_TABLE


def test_normalisation_gate_for_2col_label():
    assert normalize_source_type("troubleshooting_table_2col", tables=_MILLER_6_5_TABLE) is SourceType.TROUBLESHOOTING_TABLE_2COL
    assert normalize_source_type("troubleshooting_table_2col", tables=_MILLER_8_8_TABLE) is SourceType.TROUBLESHOOTING_TABLE_2COL
    assert normalize_source_type("troubleshooting_table_2col", tables=[], text="random prose") is SourceType.OTHER
    # A 3-col label still survives on a 3-col table, and a 2-col
    # label does not survive on a 3-col table (router/parser must
    # never silently cover for each other -- P-01).
    assert normalize_source_type("troubleshooting_table", tables=_MILLER_8_17_TABLE) is SourceType.TROUBLESHOOTING_TABLE
    assert normalize_source_type("troubleshooting_table_2col", tables=_MILLER_8_17_TABLE) is SourceType.OTHER
