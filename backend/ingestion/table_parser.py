"""Deterministic table parsers (Phase 1).

Troubleshooting tables (p42/p43 shape) and narrow spec tables (p7
shape) are parsed by code into typed facts -- rows become
``MachineFailureMode`` / ``MachineQuantity`` directly, with no LLM
re-typing cell values (Docling/TableFormer principle: table structure
is matched back to source cells, never re-typed by a model).

Pure functions, no LLM, no I/O. Input is either the page's pdfplumber
tables or the chunk's Markdown (fallback). Output is a
``UniversalMachineModel`` that rejoins the existing pipeline path.

Structural check: header found, >= 1 data row, causes non-empty (or,
for spec tables, >= 1 numeric row parsed). Anything else returns
``None`` -- the chunk falls back to the LLM path, never silently
dropped.
"""
from __future__ import annotations

import re

from backend.knowledge.evidence import MachineEvidence
from backend.knowledge.machine_model import (
    MachineFailureMode,
    MachineProcedure,
    MachineQuantity,
    UniversalMachineModel,
)

EXTRACTION_METHOD = "deterministic_table_parse"

_NUMBERED_RE = re.compile(r"(?:^|\n)\s*\d+[.)]\s+")
# Leading numeric token, optionally a range: "86", "120", "50 - 500", "0.025".
_LEADING_NUMBER_RE = re.compile(r"^\s*(?:up to\s+)?(\d[\d.,]*(?:\s*[–—-]\s*\d[\d.,]*)?)\s*(.*)$", re.S)


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug or "item"


def _norm_space(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _evidence(fact: str, source_type: str, source_document, page, chunk_id) -> MachineEvidence:
    return MachineEvidence(
        fact=fact,
        source_document=source_document,
        page=page,
        chunk=chunk_id,
        source_type=source_type,
        confidence=1.0,
        claim_status="observed",
        extraction_method=EXTRACTION_METHOD,
    )


def _split_numbered(text: str) -> list[str]:
    """Split a cell on numbered items ("1. ... 2. ...").

    Falls back to the whole cell as one item when it is not numbered.
    Internal newlines are collapsed to spaces. The Markdown path loses
    the original cell newlines (cells are space-normalised), so an
    inline fallback splits "1. ... 2. ..." on a single line too.
    """
    parts = [p.strip() for p in _NUMBERED_RE.split(text or "") if p.strip()]
    parts = [_norm_space(p) for p in parts]
    if len(parts) > 1:
        return parts
    inline = [
        re.sub(r"^\d{1,2}[.)]\s+", "", p.strip())
        for p in re.split(r"\s+(?=\d{1,2}[.)]\s)", (text or "").strip())
        if p.strip()
    ]
    inline = [_norm_space(p) for p in inline if _norm_space(p)]
    return inline if len(inline) > 1 else parts


# ---------------------------------------------------------------------------
# Markdown -> rows. A table row in chunk Markdown starts on a line
# beginning with "|" and ends on a line ending with "|"; cells may
# contain newlines, so one logical row spans several physical lines.
# ---------------------------------------------------------------------------

def markdown_to_rows(content: str) -> list[list[str]]:
    rows: list[list[str]] = []
    buf: list[str] = []
    for line in (content or "").splitlines():
        stripped = line.strip()
        if not buf:
            if stripped.startswith("|"):
                buf.append(line)
                if stripped.endswith("|"):
                    rows.append(_split_row("\n".join(buf)))
                    buf = []
        else:
            buf.append(line)
            if stripped.endswith("|"):
                rows.append(_split_row("\n".join(buf)))
                buf = []
    return rows


def _split_row(row_text: str) -> list[str]:
    cells = row_text.split("|")
    # Drop the empty cells before the first and after the last "|".
    if cells and cells[0].strip() == "":
        cells = cells[1:]
    if cells and cells[-1].strip() == "":
        cells = cells[:-1]
    return [_norm_space(c) for c in cells]


def _is_separator_row(row: list[str]) -> bool:
    return bool(row) and all(re.fullmatch(r":?-{2,}:?", c.strip()) for c in row)


def _is_troubleshooting_header(row: list[str]) -> bool:
    header = " ".join(row).lower()
    return "problem" in header and "possible cause" in header


# ---------------------------------------------------------------------------
# Troubleshooting tables
# ---------------------------------------------------------------------------

def parse_troubleshooting_rows(
    rows: list[list[str]],
    *,
    source_document: str | None,
    page: int | None,
    chunk_id: str | None,
) -> UniversalMachineModel | None:
    """One row -> one failure mode + one repair procedure.

    Header detection tolerates pdfplumber fragmentation: the first header
    row anywhere in the rows wins, separator and repeated-header rows are
    skipped, and data rows are everything after it with >= 3 cells.
    """
    header_idx = next(
        (i for i, row in enumerate(rows) if _is_troubleshooting_header(row)),
        None,
    )
    if header_idx is None:
        return None
    model = UniversalMachineModel()
    for row in rows[header_idx + 1:]:
        if _is_separator_row(row) or _is_troubleshooting_header(row):
            continue
        if len(row) < 3:
            continue
        symptom, causes_cell, solutions_cell = row[0], row[1], row[2]
        if not symptom:
            continue
        causes = _split_numbered(causes_cell)
        if not causes:
            continue
        solutions = _split_numbered(solutions_cell)
        symptom_norm = _norm_space(symptom)
        fm_id = f"failure:{_slug(symptom_norm)}"
        proc_id = f"procedure:repair-{_slug(symptom_norm)}"
        model.failure_modes.append(MachineFailureMode(
            id=fm_id,
            name=symptom_norm,
            symptoms=[symptom_norm],
            possible_causes=causes,
            repair_procedure_id=proc_id if solutions else None,
            evidence=[_evidence(
                f"Failure mode {symptom_norm}",
                "troubleshooting_table", source_document, page, chunk_id,
            )],
        ))
        if solutions:
            model.procedures.append(MachineProcedure(
                id=proc_id,
                name=f"Repair: {symptom_norm}",
                procedure_type="repair",
                steps=solutions,
                evidence=[_evidence(
                    f"Procedure Repair: {symptom_norm}",
                    "troubleshooting_table", source_document, page, chunk_id,
                )],
            ))
    if not model.failure_modes:
        return None
    return model


def parse_troubleshooting_markdown(
    content: str,
    *,
    source_document: str | None = None,
    page: int | None = None,
    chunk_id: str | None = None,
) -> UniversalMachineModel | None:
    return parse_troubleshooting_rows(
        markdown_to_rows(content),
        source_document=source_document, page=page, chunk_id=chunk_id,
    )


def parse_troubleshooting_tables(
    tables,
    *,
    source_document: str | None = None,
    page: int | None = None,
    chunk_id: str | None = None,
) -> UniversalMachineModel | None:
    rows: list[list[str]] = []
    for table in tables or []:
        for row in table or []:
            rows.append([_norm_space(c) if c is not None else "" for c in row])
    return parse_troubleshooting_rows(
        rows, source_document=source_document, page=page, chunk_id=chunk_id,
    )


# ---------------------------------------------------------------------------
# Spec tables (narrow): name/value rows with a numeric value + optional unit.
# ---------------------------------------------------------------------------

def _parse_spec_value(cell: str) -> tuple[str, str | None] | None:
    """Split a spec value cell into (value, unit).

    Returns None when the cell has no leading number (not a spec value).
    The unit is only taken when the remainder after the leading number
    is short and digit-free; otherwise the whole cell is the value and
    the unit stays unknown rather than guessed.
    """
    match = _LEADING_NUMBER_RE.match(cell or "")
    if not match:
        return None
    # A numbered-list cell ("1. Insufficient pressure. 2. ...") is prose,
    # not a spec value -- the narrow spec parser must not claim it.
    if re.match(r"^\s*\d{1,2}[.)]\s+[A-Z]", cell or "") and len(_norm_space(cell)) > 30:
        return None
    number, rest = match.group(1).strip(), _norm_space(match.group(2))
    if rest and len(rest) <= 20 and not re.search(r"\d", rest):
        return number, rest
    return _norm_space(cell), None


def parse_spec_rows(
    rows: list[list[str]],
    *,
    source_document: str | None,
    page: int | None,
    chunk_id: str | None,
) -> UniversalMachineModel | None:
    model = UniversalMachineModel()
    section = ""
    seen: set[str] = set()
    for row in rows:
        if _is_separator_row(row):
            continue
        non_empty = [(i, c) for i, c in enumerate(row) if c]
        if not non_empty:
            continue
        # A row with a single non-empty cell is a section header
        # ("Mig", "tig", "Stick") -- context for the rows that follow.
        if len(non_empty) == 1:
            section = non_empty[0][1]
            continue
        name = row[0]
        if not name:
            continue
        for col_idx, cell in enumerate(row[1:], start=1):
            parsed = _parse_spec_value(cell)
            if parsed is None:
                continue
            value, unit = parsed
            key = f"{section}|{name}|{col_idx}|{value}"
            if key in seen:
                continue
            seen.add(key)
            qid = f"quantity:{_slug(section + '-' + name) if section else _slug(name)}-{len(model.quantities) + 1}"
            model.quantities.append(MachineQuantity(
                id=qid,
                entity_id="unresolved:entity",
                name=f"{name} ({section})" if section else name,
                value=value,
                unit=unit,
                properties={"section": section, "value_column": col_idx} if section else {"value_column": col_idx},
                evidence=[_evidence(
                    f"Quantity {name}",
                    "spec_table", source_document, page, chunk_id,
                )],
            ))
    if not model.quantities:
        return None
    return model


def parse_spec_markdown(
    content: str,
    *,
    source_document: str | None = None,
    page: int | None = None,
    chunk_id: str | None = None,
) -> UniversalMachineModel | None:
    return parse_spec_rows(
        markdown_to_rows(content),
        source_document=source_document, page=page, chunk_id=chunk_id,
    )


def parse_spec_tables(
    tables,
    *,
    source_document: str | None = None,
    page: int | None = None,
    chunk_id: str | None = None,
) -> UniversalMachineModel | None:
    rows: list[list[str]] = []
    for table in tables or []:
        for row in table or []:
            rows.append([_norm_space(c) if c is not None else "" for c in row])
    return parse_spec_rows(
        rows, source_document=source_document, page=page, chunk_id=chunk_id,
    )


# ---------------------------------------------------------------------------
# Dispatch: one entry point for the pipeline branch.
# ---------------------------------------------------------------------------

def parse_table_chunk(
    content: str,
    source_type: str,
    *,
    source_document: str | None = None,
    page: int | None = None,
    chunk_id: str | None = None,
) -> UniversalMachineModel | None:
    """Parse a table chunk by its router label. None = fall back to LLM."""
    if source_type == "troubleshooting_table":
        return parse_troubleshooting_markdown(
            content, source_document=source_document, page=page, chunk_id=chunk_id,
        )
    if source_type == "spec_table":
        return parse_spec_markdown(
            content, source_document=source_document, page=page, chunk_id=chunk_id,
        )
    return None
