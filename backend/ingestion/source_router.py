"""Deterministic source-type router (Phase 1).

Classify each page/section first (cheap regex/keyword over text and
tables the loader already extracted), then dispatch to the cheapest
correct handler. Pure functions, no LLM, no I/O.

The vocabulary reuses the existing ``MachineEvidence.source_type``
field, so the label flows end to end (prompt -> evidence -> DB column)
without a schema change.

Detection order (see phase1-implementation.md §3):
  1. Table present + header match -> troubleshooting_table / alarm_table /
     spec_table.
  2. No table: step density -> procedure (maintenance heading ->
     maintenance_procedure).
  3. Image-heavy, little text -> diagram.
  4. Parameter-number pattern dominant -> parameter_reference.
  5. Else other.

Normalisation gate (SDF lesson): a label is validated in code before it
selects a handler. A page labelled ``troubleshooting_table`` with no
detectable table, or an ``alarm_table`` with no code-like first column,
is re-routed to ``other``/``procedure``, never trusted raw.

Classification never drops a page's facts: ``other`` still goes through
the normal LLM extraction path.
"""
from __future__ import annotations

import re
from enum import Enum

# A table header / first column that looks like a Mitsubishi-style alarm
# code (M01, S03, T01, P01, Y01, Z01, ...). Alarm entries are
# code-structured (e.g. "M01 0001 Dog overrun"), so a code-like first
# column is the reliable signal, not the word "alarm" alone.
_ALARM_CODE_RE = re.compile(r"^[A-Z]\d{2,4}\b")
# A parameter reference such as #3028 / #2713, as used by the Mitsubishi
# Parameter Manual to cross-reference alarm entries.
_PARAMETER_RE = re.compile(r"#\d{3,4}\b")
_STEP_RE = re.compile(r"^\s*(?:step\s*)?\d+[.)]\s+", re.I)
_SPEC_HEADING_RE = re.compile(r"(?m)^\s*specifications\s*$", re.I)
_MAINTENANCE_HEADING_RE = re.compile(r"(?m)^\s*maintenance\s*$", re.I)
# A leading numeric token (optionally a range) at the start of a cell,
# e.g. "86 VDC", "120 VAC / 60 Hz", "50 - 500 IPM", "40% @ 100 A".
_NUMERIC_CELL_RE = re.compile(r"^\s*(?:up to\s+)?\d[\d.,]*", re.I)


class SourceType(str, Enum):
    TROUBLESHOOTING_TABLE = "troubleshooting_table"
    ALARM_TABLE = "alarm_table"
    SPEC_TABLE = "spec_table"
    PROCEDURE = "procedure"
    MAINTENANCE_PROCEDURE = "maintenance_procedure"
    DIAGRAM = "diagram"
    PARAMETER_REFERENCE = "parameter_reference"
    OTHER = "other"


def _norm(text: str) -> str:
    """Lowercase, collapse all whitespace (incl. newlines inside a cell)."""
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def _cell_text(cell) -> str:
    return "" if cell is None else str(cell).strip()


def _header_text(tables) -> str:
    """Concatenated header text of the page's tables.

    pdfplumber fragments one logical table into many small tables, so
    the header is matched on the concatenated first rows, not on one
    table object.
    """
    parts: list[str] = []
    for table in tables or []:
        if not table:
            continue
        parts.append(" ".join(_cell_text(c) for c in table[0]))
    return _norm(" ".join(parts))


def _first_column_cells(tables) -> list[str]:
    cells: list[str] = []
    for table in tables or []:
        for row in table or []:
            if row:
                cells.append(_cell_text(row[0]))
    return cells


def _has_troubleshooting_header(tables) -> bool:
    header = _header_text(tables)
    return "problem" in header and "possible cause" in header


def _has_alarm_codes(tables) -> bool:
    for cell in _first_column_cells(tables):
        if _ALARM_CODE_RE.match(cell.strip()):
            return True
    return False


def _has_alarm_header(tables) -> bool:
    header = _header_text(tables)
    return "alarm" in header and any(
        word in header for word in ("code", "message", "cause", "remedy")
    )


def _looks_like_spec_rows(tables) -> bool:
    """Name/value rows: a non-numeric name cell plus a numeric value cell."""
    hits = 0
    for table in tables or []:
        for row in table or []:
            if len(row) < 2:
                continue
            name, value = _cell_text(row[0]), _cell_text(row[1])
            if not name or _NUMERIC_CELL_RE.match(name):
                continue
            if _NUMERIC_CELL_RE.match(value):
                hits += 1
    return hits >= 2


def _step_line_count(text: str) -> int:
    return sum(1 for ln in (text or "").splitlines() if _STEP_RE.match(ln))


def _has_spec_heading(text: str) -> bool:
    lower = (text or "").lower()
    return bool(_SPEC_HEADING_RE.search(lower) or "technical data" in lower)


def classify_source_type(
    page_number: int,
    text: str,
    tables,
    image_paths,
) -> SourceType:
    """Deterministic label for one page. Never raises on odd input."""
    tables = tables or []
    image_paths = image_paths or []
    stripped = (text or "").strip()
    lower = (text or "").lower()

    # 1. Tables first: the header / first column decides.
    if tables:
        if _has_troubleshooting_header(tables):
            return SourceType.TROUBLESHOOTING_TABLE
        if _has_alarm_codes(tables) or _has_alarm_header(tables):
            return SourceType.ALARM_TABLE
        if _has_spec_heading(text) and _looks_like_spec_rows(tables):
            return SourceType.SPEC_TABLE

    # 2. Step-dense prose -> procedure (maintenance heading wins).
    if _step_line_count(text) >= 3:
        if _MAINTENANCE_HEADING_RE.search(lower):
            return SourceType.MAINTENANCE_PROCEDURE
        return SourceType.PROCEDURE

    # 3. Image-heavy, little text -> diagram (same rule as classify_page).
    if not stripped and image_paths:
        return SourceType.DIAGRAM
    if image_paths and len(stripped) < 300:
        return SourceType.DIAGRAM

    # 4. Parameter-number pattern dominant.
    if len(_PARAMETER_RE.findall(text or "")) >= 3:
        return SourceType.PARAMETER_REFERENCE

    return SourceType.OTHER


def normalize_source_type(label, tables=None, text: str = "") -> SourceType:
    """Normalisation gate: validate a label before it selects a handler.

    Accepts a SourceType or its string value (and unknown strings, which
    become ``other``). A table label with no supporting table structure
    is re-routed, never trusted raw.
    """
    try:
        parsed = SourceType(str(label).strip().lower()) if label is not None else SourceType.OTHER
    except ValueError:
        return SourceType.OTHER
    tables = tables or []
    if parsed is SourceType.TROUBLESHOOTING_TABLE:
        if not tables or not _has_troubleshooting_header(tables):
            return (
                SourceType.PROCEDURE
                if _step_line_count(text) >= 3
                else SourceType.OTHER
            )
    if parsed is SourceType.ALARM_TABLE:
        if not tables or not (_has_alarm_codes(tables) or _has_alarm_header(tables)):
            return SourceType.OTHER
    if parsed is SourceType.SPEC_TABLE:
        if not tables or not _looks_like_spec_rows(tables):
            return SourceType.OTHER
    return parsed


def merge_page_source_types(page_source_types: list[str]) -> tuple[str, list[str]]:
    """Chunk-level rule for cross-page text chunks.

    Label from the chunk's first page; if the chunk spans pages with
    different labels, the full list is kept so the mix is auditable.
    Returns (first_label, unique_labels_in_order).
    """
    labels = [str(s or SourceType.OTHER.value) for s in (page_source_types or [])]
    if not labels:
        return SourceType.OTHER.value, [SourceType.OTHER.value]
    seen: list[str] = []
    for label in labels:
        if label not in seen:
            seen.append(label)
    return seen[0], seen
