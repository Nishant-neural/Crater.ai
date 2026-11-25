"""Deterministic table parsers (Phase 1, Step 1 of the ingestion audit).

Troubleshooting tables (OMNIPRO p42/p43 3-col shape, Miller §6-5
2-col Trouble|Remedy shape, Miller §§8-8..8-15 2-col Possible
Causes|Corrective Actions shape, Miller §8-17 3-col Problem|Probable
Cause|Remedy shape) and narrow spec tables (p7 shape) are parsed by
code into typed rows -- first as :class:`TableRow` (the audit's
first-class row: symptom, cause, remedy, meter code, rankable and
twin-expressible flags, source page/table, provenance), then, for the
existing pipeline path, into a ``UniversalMachineModel`` (one
``MachineFailureMode`` per symptom, one repair ``MachineProcedure``
per symptom). No LLM re-types cell values (Docling/TableFormer
principle: table structure is matched back to source cells, never
re-typed by a model).

Pure functions, no LLM, no I/O. Input is either the page's pdfplumber
tables or the chunk's Markdown (fallback). Output is a list of
``TableRow`` and/or a ``UniversalMachineModel`` that rejoins the
existing pipeline path.

Structural check: header found, >= 1 data row, causes non-empty (or,
for spec tables, >= 1 numeric row parsed). Anything else returns
``None`` / ``[]`` -- the chunk falls back to the LLM path, never
silently dropped.

2-column tables have no cause column, so the cause is derived from
the remedy text verbatim (flagged ``cause_from_remedy``) -- never by
an LLM. Meter codes (``HL.P 001`` ...) attach to their parent row as
``meter_code``; they never stand alone as symptoms (P-09b).

Rankable / twin-expressible (verified against the frozen oracles,
``frozen/c002b-miller.json`` + ``benchmark/evaluations/
miller_parser_rows.json`` and ``frozen/omnipro-oracle-35.json`` +
the OMNIPRO vocabulary's 45 rows):
- ``twin_expressible`` is False only for settings rows (a setting
  value, not a fault), technique rows (technique / contamination)
  and cross-reference rows (routes to another case). The cause
  category is decided by deterministic phrase patterns over the
  cause text (see :func:`classify_cause`); the 63-row Miller set
  and the 45-row OMNIPRO set are both reproduced exactly.
- ``rankable`` follows ``twin_expressible``. CORRECTION to the
  ingestion audit (pp. 11, 14), per the 23 Nov 2026 harness
  addendum (``benchmark/HARNESS_SCORER_FIX_NOTES.md``, Phase 0
  addendum (a), verified against the oracle): Miller's 4 HL.P rows
  (miller-04/05/06/11) and 5 pure "Have Factory Authorized Service
  Agent check ..." rows (miller-07/08/14/15/21) are all
  twin_expressible WITH structured faults, i.e. rankable, and stay
  in the 38-row denominator. The genuinely unrankable rows are the
  25 settings (16) + technique (9) rows. So a meter code or a
  service-agent referral on a row does NOT make it unrankable;
  ``rankable_reason`` ``meter_code`` /
  ``generic_service_fallback`` fire only for a row whose cause is
  nothing but a meter-code mention / a bare referral with no named
  component -- a shape that occurs in neither oracle.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from backend.knowledge.evidence import MachineEvidence
from backend.knowledge.machine_model import (
    MachineFailureMode,
    MachineProcedure,
    MachineQuantity,
    UniversalMachineModel,
)
from backend.knowledge.provenance import precedence_for_source

EXTRACTION_METHOD = "deterministic_table_parse"

_NUMBERED_RE = re.compile(r"(?:^|\n)\s*\d+[.)]\s+")
# Leading numeric token, optionally a range: "86", "120", "50 - 500", "0.025".
_LEADING_NUMBER_RE = re.compile(r"^\s*(?:up to\s+)?(\d[\d.,]*(?:\s*[–—-]\s*\d[\d.,]*)?)\s*(.*)$", re.S)

# Meter codes such as "HL.P 001" on a welder's meters. Canonical form
# is "HL.P NNN" (three digits, zero-padded). The code attaches to its
# parent TableRow; it is never a symptom of its own (P-09b).
_METER_CODE_RE = re.compile(r"\bHL\.P\s*0*(\d{1,3})\b", re.IGNORECASE)
# A remedy that routes the reader to another troubleshooting section
# ("See first Troubleshooting section above (case 1)") makes the row
# a cross-reference, not a fault of its own.
_CROSS_REF_REMEDY_RE = re.compile(
    r"see\s+(?:the\s+)?(?:first|above|other)\b.*\btroubleshooting\s+section\b",
    re.IGNORECASE,
)
# A bare service-agent referral naming no component and no action
# beyond the referral itself. Miller's five service-agent rows all
# name a component (power switch, board, fan circuit), so none of
# them matches this -- they stay rankable, per the oracle correction
# above. This fires only for a content-less referral.
_BARE_SERVICE_REFERRAL_RE = re.compile(
    r"^have\s+(?:a\s+)?factory\s+authorized\s+service\s+agent\s+"
    r"check\s+(?:the\s+)?(?:unit|machine|welder)\.?$",
    re.IGNORECASE,
)

# Cause-category patterns for classify_cause(). Phrase-precise on
# purpose: "Incorrect line voltage." is twin_expressible (a wrong
# supply is a fault) while "Voltage too high." is settings (a value
# out of range), and "Gas coverage may be insufficient or too high."
# is twin_expressible (a gas fault, not a setting). Every pattern
# below was checked against all 63 Miller + all 45 OMNIPRO oracle
# causes; see tests/test_table_parser.py::test_cause_category_matches_both_oracles.
_SETTINGS_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bjumper\s+links?\b",                    # input jumper links position
        r"\bfront\s+panel\s+settings\b",
        r"\bvoltage\s+setting\b",                  # incorrect voltage setting ...
        r"\bheat\s+input\b",                       # excessive / insufficient heat input
        r"\b(wire\s+feed\s+speed|voltage|electrode\s+extension|stickout)\b.*\btoo\s+(high|low|long)\b",
        r"\bstickout\b",
        r"\bwrong\s+gas\.?$",                      # "Wrong gas." = wrong gas type (a setting);
                                               # "Wrong gas flow." is a flow fault (twin),
                                               # not a gas-type setting
        r"\bvalve\s+not\s+open\b|\bflowmeter\s+not\s+adjusted\b",
        r"\bextends?\b.*\b(beyond|out\s+of)\b.*\bnozzle\b",  # wire extends too far out of nozzle
        r"\bincorrect\s+(wire\s+feed\s+speed|polarity|mode)\b",
        r"\bpolarity\s+is\s+incorrect\b",
        r"\bnot\s+enough\s+current\b",
    )
]
_TECHNIQUE_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bdirty\b(?!\s+or\s+damaged\b)",           # dirty workpiece / wire / tip --
                                               # but "Dirty or damaged (kinked) liner"
                                               # is a component fault (twin), not
                                               # contamination alone
        r"contaminat",                             # contamination
        r"\bimproper\s+(joint\s+preparation|weld(?:ing)?\s+technique|technique)\b",
        r"\bunsteady\s+hand\b",
        r"\btoo\s+far\s+(away\s+)?from\s+(?:the\s+)?workpiece\b",
    )
]

# Cause categories. "twin_expressible" is the scored headline set;
# the other three are recorded with their expected text, not scored
# as fault rows (Miller oracle categories, verbatim).
CAUSE_CATEGORIES = ("twin_expressible", "settings", "technique", "cross_reference")
RANKABLE_REASONS = (
    "concrete",
    "generic_service_fallback",
    "meter_code",
    "settings",
    "technique",
    "cross_reference",
)


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug or "item"


def _norm_space(text: str) -> str:
    # Dehyphenate words split across lines ("oper-\nates" ->
    # "operates"), exactly as the oracle builder's clean() does
    # (tools/parse_welder_oracles.py). A spaced dash ("size -
    # Replace") is kept: only a hyphen directly after a letter,
    # at a line break, is joined.
    text = re.sub(r"([A-Za-z])-\n\s*", r"\1", text or "")
    return re.sub(r"\s+", " ", text).strip()


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


def extract_meter_code(text: str) -> str | None:
    """First meter code in ``text``, canonicalised ("HL.P 001"), or None."""
    match = _METER_CODE_RE.search(text or "")
    if not match:
        return None
    return f"HL.P {int(match.group(1)):03d}"


def classify_cause(
    cause: str,
    remedy: str = "",
    symptoms_in_table: tuple[str, ...] = (),
) -> str:
    """Cause category for one parsed cause (deterministic, no LLM).

    ``"twin_expressible"`` unless the cause is a setting value, a
    technique / contamination issue, or a cross-reference to another
    case (remedy routes to another troubleshooting section, or the
    cause restates another symptom in the same table). Reproduces
    the Miller oracle categories (38 / 16 / 9 / 0) and the OMNIPRO
    vocabulary categories (35 / 5 / 4 / 1) exactly.
    """
    text = _norm_space(cause).lower().rstrip(".")
    if _CROSS_REF_REMEDY_RE.search(remedy or ""):
        return "cross_reference"
    for symptom in symptoms_in_table:
        if symptom and _norm_space(symptom).lower().rstrip(".") == text:
            return "cross_reference"
    if any(p.search(text) for p in _SETTINGS_PATTERNS):
        return "settings"
    if any(p.search(text) for p in _TECHNIQUE_PATTERNS):
        return "technique"
    return "twin_expressible"


def _rankable_for(category: str, cause: str, meter_code: str | None) -> tuple[bool, str]:
    """(rankable, rankable_reason) for a row of the given category.

    Rankable follows twin_expressible: settings / technique /
    cross-reference rows are real rows, just not rankable causes.
    A meter code or a service-agent referral does NOT make a row
    unrankable on its own (oracle correction, see module docstring);
    those reasons fire only when the cause carries no other content.
    """
    if category in ("settings", "technique", "cross_reference"):
        return False, category
    cause_norm = _norm_space(cause)
    if meter_code:
        # Only a cause that is nothing but the meter code ("HL.P 001
        # appears on meters.") is unrankable for the code alone. Every
        # Miller HL.P row carries a reset / check instruction beyond
        # the code, so those rows stay rankable (oracle correction).
        rest = _METER_CODE_RE.sub("", cause_norm).strip(" .")
        if not rest:
            return False, "meter_code"
    if _BARE_SERVICE_REFERRAL_RE.match(cause_norm):
        return False, "generic_service_fallback"
    return True, "concrete"


# ---------------------------------------------------------------------------
# TableRow -- the audit's first-class row (contracts, TableRow).
# ---------------------------------------------------------------------------

@dataclass
class TableRow:
    """One deterministically parsed troubleshooting / alarm table row.

    ``meter_code`` attaches to the parent row, never a standalone
    symptom. ``rankable`` / ``twin_expressible`` are set at parse
    time so diagnosis holds its conclusion (instead of waiting for a
    twin verification that can never come) and the scorer never
    treats a settings / technique row as a concrete cause.
    ``confidence`` is 1.0 with ``extraction_method`` deterministic:
    a parsed row is not a guess and must not be scored or
    quarantined as one. Provenance rides on every row (source
    document / type / table / page / chunk + MTB > OEM > generic
    precedence rank from ``backend.knowledge.provenance``).
    """

    table_shape: str  # troubleshooting_3col | troubleshooting_2col | alarm_table | spec_table
    symptom: str
    symptom_norm: str
    cause: str
    cause_index: int
    remedy: str
    remedy_index: int | None
    meter_code: str | None
    rankable: bool
    rankable_reason: str  # concrete | generic_service_fallback | meter_code | settings | technique | cross_reference
    twin_expressible: bool
    cause_category: str  # twin_expressible | settings | technique | cross_reference
    source_document: str | None
    source_type: str  # router label: troubleshooting_table | troubleshooting_table_2col | alarm_table
    source_table: str | None
    page: int | None
    chunk_id: str | None
    extraction_method: str = EXTRACTION_METHOD
    confidence: float = 1.0
    failure_mode_id: str = ""
    remedy_source: str = ""  # remedy_column | remedy_column_only | corrective_actions_column | solutions_column
    flags: list[str] = field(default_factory=list)
    provenance_precedence: str = "generic"  # mtb (3) > oem (2) > generic (1)
    id: str = ""

    def __post_init__(self) -> None:
        if not self.symptom_norm:
            self.symptom_norm = _norm_space(self.symptom)
        if not self.failure_mode_id:
            self.failure_mode_id = f"failure:{_slug(self.symptom_norm)}"
        if not self.id:
            self.id = f"tablerow:{_slug(self.symptom_norm)}:{self.cause_index:02d}"
        if self.provenance_precedence == "generic" and self.source_document:
            self.provenance_precedence = precedence_for_source(self.source_document)


def _make_row(
    *,
    table_shape: str,
    symptom: str,
    cause: str,
    cause_index: int,
    remedy: str,
    remedy_index: int | None,
    remedy_source: str,
    source_document: str | None,
    source_type: str,
    source_table: str | None,
    page: int | None,
    chunk_id: str | None,
    symptoms_in_table: tuple[str, ...] = (),
    flags: list[str] | None = None,
) -> TableRow:
    category = classify_cause(cause, remedy, symptoms_in_table)
    meter_code = extract_meter_code(cause) or extract_meter_code(remedy)
    rankable, rankable_reason = _rankable_for(category, cause, meter_code)
    return TableRow(
        table_shape=table_shape,
        symptom=_norm_space(symptom),
        symptom_norm=_norm_space(symptom),
        cause=_norm_space(cause),
        cause_index=cause_index,
        remedy=_norm_space(remedy),
        remedy_index=remedy_index,
        meter_code=meter_code,
        rankable=rankable,
        rankable_reason=rankable_reason,
        twin_expressible=category == "twin_expressible",
        cause_category=category,
        source_document=source_document,
        source_type=source_type,
        source_table=source_table,
        page=page,
        chunk_id=chunk_id,
        remedy_source=remedy_source,
        flags=list(flags or []),
    )


def repair_doubled_letters(text: str) -> tuple[str, bool]:
    """Repair the Miller §8-17 print artifact where every letter of a
    word is doubled ("wwiirree" -> "wire"). A word is repaired only
    if it has even length >= 4 and every adjacent pair is identical;
    the repair is then unambiguous. Ported verbatim from
    tools/parse_welder_oracles.py (the reviewed oracle builder)."""
    repaired = False

    def fix_word(word: str) -> str:
        nonlocal repaired
        if len(word) >= 4 and len(word) % 2 == 0 and all(
            word[i] == word[i + 1] for i in range(0, len(word), 2)
        ):
            repaired = True
            return word[::2]
        return word

    return " ".join(fix_word(w) for w in text.split(" ")), repaired


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
    """3-col header: Problem + Possible Cause or Probable Cause.

    "Probable Cause" is Miller §8-17's wording; the old test
    required "possible cause" only, so §8-17 classified as `other`.
    """
    header = " ".join(row).lower()
    return "problem" in header and ("possible cause" in header or "probable cause" in header)


def _is_troubleshooting_2col_header(row: list[str]) -> bool:
    """Miller §6-5 header: Trouble + Remedy."""
    header = " ".join(row).lower()
    return "trouble" in header and "remedy" in header


def _is_cause_action_header(row: list[str]) -> bool:
    """Miller §§8-8..8-15 header: Possible Causes + Corrective Actions."""
    header = " ".join(row).lower()
    return "possible cause" in header and "corrective action" in header


def _is_alarm_header(row: list[str]) -> bool:
    header = " ".join(row).lower()
    return "alarm" in header and any(
        word in header for word in ("code", "message", "cause", "remedy")
    )


def _detect_table_shape(rows: list[list[str]]) -> str | None:
    """Shape of one table from its header rows, or None.

    Scans every row (the header is not always row 0: §§8-8..8-15
    tables carry a defect title row above the header). 3-col is
    checked first -- a "Possible Causes" header without "Problem"
    is the 2-col cause/action shape, not the 3-col shape.
    """
    for row in rows:
        if _is_separator_row(row):
            continue
        if _is_troubleshooting_header(row):
            return "troubleshooting_3col"
        if _is_troubleshooting_2col_header(row):
            return "troubleshooting_2col"
        if _is_cause_action_header(row):
            return "troubleshooting_2col"
        if _is_alarm_header(row):
            return "alarm_table"
    return None


def _header_index(rows: list[list[str]], predicate) -> int | None:
    return next(
        (i for i, row in enumerate(rows) if not _is_separator_row(row) and predicate(row)),
        None,
    )


def _symptoms_in(rows: list[list[str]], header_idx: int, symptom_col: int = 0) -> tuple[str, ...]:
    """Distinct symptom texts in a table (for cross-reference checks)."""
    seen: list[str] = []
    for row in rows[header_idx + 1:]:
        if _is_separator_row(row) or len(row) <= symptom_col:
            continue
        symptom = _norm_space(row[symptom_col])
        if symptom and symptom not in seen:
            seen.append(symptom)
    return tuple(seen)


# ---------------------------------------------------------------------------
# Troubleshooting tables -- TableRow builders (one row per cause).
# ---------------------------------------------------------------------------

def parse_troubleshooting_3col_rows(
    rows: list[list[str]],
    *,
    source_document: str | None,
    page: int | None,
    chunk_id: str | None,
    source_table: str | None = None,
    source_type: str = "troubleshooting_table",
) -> list[TableRow]:
    """3-col Problem | Possible/Probable Cause | Remedy -> TableRows.

    OMNIPRO p42/p43 shape: one numbered item per cause in the cause
    cell, paired by number with the solutions cell. Miller §8-17
    shape: one (unnumbered) cause per table row; an empty Problem
    cell continues the previous problem. The doubled-letter print
    artifact in two §8-17 Problem cells is repaired and flagged.
    """
    header_idx = _header_index(rows, _is_troubleshooting_header)
    if header_idx is None:
        return []
    symptoms = _symptoms_in(rows, header_idx)
    out: list[TableRow] = []
    symptom: str | None = None
    cause_index_by_symptom: dict[str, int] = {}
    for row in rows[header_idx + 1:]:
        if _is_separator_row(row) or _is_troubleshooting_header(row):
            continue
        if len(row) < 3:
            continue
        problem_cell, causes_cell, solutions_cell = row[0], row[1], row[2]
        flags: list[str] = []
        if problem_cell:
            fixed, was_repaired = repair_doubled_letters(_norm_space(problem_cell))
            if was_repaired:
                flags.append(
                    "print_artifact_repaired: doubled-letter overprint in "
                    f"the Problem cell repaired ({_norm_space(problem_cell)!r} -> {fixed!r})"
                )
            symptom = fixed
        if not symptom or not causes_cell:
            continue
        # The as-printed grouping: this row's Problem cell is empty,
        # so the table groups it under the previous problem. Flagged
        # (as the reviewed oracle builder does) because a
        # primary-voltage cause under a no-gas-flow problem is
        # surprising but is what the PDF prints.
        if not problem_cell and causes_cell.startswith("Incorrect primary voltage"):
            flags.append(
                "grouping_as_printed: this row's Problem cell is empty, "
                "so the table groups it under 'Wire feeds, but no gas "
                "flows.'; a primary-voltage cause under a no-gas-flow "
                "problem is surprising but is what the PDF prints"
            )
        causes = _split_numbered(causes_cell)
        solutions = _split_numbered(solutions_cell)
        for i, cause in enumerate(causes):
            idx = cause_index_by_symptom.get(symptom, 0)
            cause_index_by_symptom[symptom] = idx + 1
            out.append(_make_row(
                table_shape="troubleshooting_3col",
                symptom=symptom,
                cause=cause,
                cause_index=idx,
                remedy=solutions[i] if i < len(solutions) else "",
                remedy_index=i if i < len(solutions) else None,
                remedy_source="solutions_column" if solutions else "remedy_column",
                source_document=source_document,
                source_type=source_type,
                source_table=source_table,
                page=page,
                chunk_id=chunk_id,
                symptoms_in_table=symptoms,
                flags=flags,
            ))
    return out


def parse_troubleshooting_2col_rows(
    rows: list[list[str]],
    *,
    source_document: str | None,
    page: int | None,
    chunk_id: str | None,
    source_table: str | None = None,
    source_type: str = "troubleshooting_table_2col",
) -> list[TableRow]:
    """2-col Trouble | Remedy (Miller §6-5) -> TableRows.

    One row per table row: the Trouble cell carries forward onto
    continuation rows (empty Trouble cell), the Remedy cell is one
    row. There is no cause column, so the cause is the remedy text
    verbatim (flagged ``cause_from_remedy``) -- never an LLM guess.
    """
    header_idx = _header_index(rows, _is_troubleshooting_2col_header)
    if header_idx is None:
        return []
    out: list[TableRow] = []
    symptom: str | None = None
    cause_index_by_symptom: dict[str, int] = {}
    for row in rows[header_idx + 1:]:
        if _is_separator_row(row) or _is_troubleshooting_2col_header(row):
            continue
        if len(row) < 2:
            continue
        trouble_cell, remedy_cell = row[0], row[1]
        if trouble_cell:
            symptom = _norm_space(trouble_cell)
        if not remedy_cell or symptom is None:
            continue
        idx = cause_index_by_symptom.get(symptom, 0)
        cause_index_by_symptom[symptom] = idx + 1
        out.append(_make_row(
            table_shape="troubleshooting_2col",
            symptom=symptom,
            cause=remedy_cell,
            cause_index=idx,
            remedy=remedy_cell,
            remedy_index=idx,
            remedy_source="remedy_column_only",
            source_document=source_document,
            source_type=source_type,
            source_table=source_table,
            page=page,
            chunk_id=chunk_id,
            flags=["cause_from_remedy: section 6-5 has no cause column; "
                   "the row key is the remedy text verbatim"],
        ))
    return out


def parse_cause_action_2col_rows(
    rows: list[list[str]],
    *,
    source_document: str | None,
    page: int | None,
    chunk_id: str | None,
    source_table: str | None = None,
    source_type: str = "troubleshooting_table_2col",
) -> list[TableRow]:
    """2-col Possible Causes | Corrective Actions (§§8-8..8-15) -> TableRows.

    The symptom is the defect named in the title row above the
    header ("Excessive Spatter - scattering of ..." -> the defect
    name before the definition dash). One row per Possible Cause; a
    cause whose Corrective Actions cell spans continuation rows
    (empty cause cell) has those actions joined onto the one row.
    """
    header_idx = _header_index(rows, _is_cause_action_header)
    if header_idx is None:
        return []
    symptom: str | None = None
    for row in rows[:header_idx]:
        if _is_separator_row(row):
            continue
        title = _norm_space(row[0]) if row else ""
        if title:
            symptom = re.split(r"\s+[−–—-]\s+", title, maxsplit=1)[0].strip()
    if not symptom:
        return []
    out: list[TableRow] = []
    current: TableRow | None = None
    for row in rows[header_idx + 1:]:
        if _is_separator_row(row) or _is_cause_action_header(row):
            continue
        if len(row) < 2:
            continue
        cause_cell, action_cell = row[0], row[1]
        if cause_cell:
            current = _make_row(
                table_shape="troubleshooting_2col",
                symptom=symptom,
                cause=cause_cell,
                cause_index=len(out),
                remedy=action_cell,
                remedy_index=len(out),
                remedy_source="corrective_actions_column",
                source_document=source_document,
                source_type=source_type,
                source_table=source_table,
                page=page,
                chunk_id=chunk_id,
            )
            out.append(current)
        elif action_cell and current is not None:
            # Continuation row: join the extra actions onto the row.
            current.remedy = _norm_space(current.remedy + " " + action_cell)
    return out


# ---------------------------------------------------------------------------
# Alarm tables -- TableRow builder.
# ---------------------------------------------------------------------------

_ALARM_CODE_RE = re.compile(r"^[A-Z]\d{2,4}\b")


def parse_alarm_rows(
    rows: list[list[str]],
    *,
    source_document: str | None,
    page: int | None,
    chunk_id: str | None,
    source_table: str | None = None,
    source_type: str = "alarm_table",
) -> list[TableRow]:
    """Alarm table (Alarm Code | Message | Cause | Remedy) -> TableRows.

    The symptom is the alarm code + message ("M01 0001 Dog overrun");
    the code never stands alone as a symptom without its message.
    A meter code in the cause / remedy attaches to the row the same
    way as in troubleshooting tables.
    """
    header_idx = _header_index(rows, _is_alarm_header)
    if header_idx is None:
        # No header row: accept a code-like first column as the shape
        # signal (Mitsubishi-style alarm tables fragment in pdfplumber).
        header_idx = -1
        if not any(
            len(row) >= 2 and _ALARM_CODE_RE.match(_norm_space(row[0]))
            for row in rows
            if not _is_separator_row(row)
        ):
            return []
    out: list[TableRow] = []
    for row in rows[header_idx + 1:]:
        if _is_separator_row(row) or _is_alarm_header(row):
            continue
        if len(row) < 2:
            continue
        code, message = _norm_space(row[0]), _norm_space(row[1])
        cause = _norm_space(row[2]) if len(row) > 2 else ""
        remedy = _norm_space(row[3]) if len(row) > 3 else ""
        if not code:
            continue
        symptom = f"{code} {message}".strip()
        if not cause:
            cause = message or code
        out.append(_make_row(
            table_shape="alarm_table",
            symptom=symptom,
            cause=cause,
            cause_index=len(out),
            remedy=remedy,
            remedy_index=len(out) if remedy else None,
            remedy_source="remedy_column",
            source_document=source_document,
            source_type=source_type,
            source_table=source_table,
            page=page,
            chunk_id=chunk_id,
        ))
    return out


# ---------------------------------------------------------------------------
# TableRow -> UniversalMachineModel (the existing pipeline path).
# ---------------------------------------------------------------------------

def table_rows_to_model(
    rows: list[TableRow],
    *,
    source_document: str | None = None,
    page: int | None = None,
    chunk_id: str | None = None,
) -> UniversalMachineModel | None:
    """One FailureMode + one repair Procedure per symptom in ``rows``.

    Causes keep table order; remedies become procedure steps in the
    same order. Rows are grouped by symptom, so the 2-col §6-5 shape
    (one row per remedy) and the 3-col OMNIPRO shape (numbered causes
    in one cell) produce the same model shape as before.
    """
    if not rows:
        return None
    grouped: dict[str, list[TableRow]] = {}
    for row in rows:
        grouped.setdefault(row.symptom_norm, []).append(row)
    model = UniversalMachineModel()
    for symptom_norm, group in grouped.items():
        source_type = group[0].source_type
        fm_id = group[0].failure_mode_id
        proc_id = f"procedure:repair-{_slug(symptom_norm)}"
        solutions = [r.remedy for r in group if r.remedy]
        model.failure_modes.append(MachineFailureMode(
            id=fm_id,
            name=symptom_norm,
            symptoms=[symptom_norm],
            possible_causes=[r.cause for r in group],
            repair_procedure_id=proc_id if solutions else None,
            evidence=[_evidence(
                f"Failure mode {symptom_norm}",
                source_type, source_document, page, chunk_id,
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
                    source_type, source_document, page, chunk_id,
                )],
            ))
    return model


# ---------------------------------------------------------------------------
# Troubleshooting tables -- model builders (backwards-compatible API).
# ---------------------------------------------------------------------------

def parse_troubleshooting_rows(
    rows: list[list[str]],
    *,
    source_document: str | None,
    page: int | None,
    chunk_id: str | None,
) -> UniversalMachineModel | None:
    """One row -> one failure mode + one repair procedure (3-col).

    Header detection tolerates pdfplumber fragmentation: the first header
    row anywhere in the rows wins, separator and repeated-header rows are
    skipped, and data rows are everything after it with >= 3 cells.
    Implemented on TableRows so the 3-col and 2-col shapes share one
    model-building path.
    """
    table_rows = parse_troubleshooting_3col_rows(
        rows, source_document=source_document, page=page, chunk_id=chunk_id,
    )
    if not table_rows:
        return None
    return table_rows_to_model(
        table_rows, source_document=source_document, page=page, chunk_id=chunk_id,
    )


def parse_troubleshooting_markdown(
    content: str,
    *,
    source_document: str | None = None,
    page: int | None = None,
    chunk_id: str | None = None,
) -> UniversalMachineModel | None:
    """Parse troubleshooting Markdown of any supported shape (3-col,
    2-col Trouble|Remedy, 2-col Possible Causes|Corrective Actions).
    None = fall back to LLM."""
    rows = parse_table_rows(
        content, "troubleshooting_table",
        source_document=source_document, page=page, chunk_id=chunk_id,
    )
    if not rows:
        return None
    return table_rows_to_model(
        rows, source_document=source_document, page=page, chunk_id=chunk_id,
    )


def parse_troubleshooting_2col_markdown(
    content: str,
    *,
    source_document: str | None = None,
    page: int | None = None,
    chunk_id: str | None = None,
) -> UniversalMachineModel | None:
    rows = parse_table_rows(
        content, "troubleshooting_table_2col",
        source_document=source_document, page=page, chunk_id=chunk_id,
    )
    if not rows:
        return None
    return table_rows_to_model(
        rows, source_document=source_document, page=page, chunk_id=chunk_id,
    )


def _tables_to_row_lists(tables) -> list[list[list[str]]]:
    """One normalised row list per pdfplumber table."""
    out: list[list[list[str]]] = []
    for table in tables or []:
        out.append([
            [_norm_space(c) if c is not None else "" for c in row]
            for row in table or []
        ])
    return out


def parse_troubleshooting_tables(
    tables,
    *,
    source_document: str | None = None,
    page: int | None = None,
    chunk_id: str | None = None,
) -> UniversalMachineModel | None:
    rows = parse_tables_to_rows(
        tables, "troubleshooting_table",
        source_document=source_document, page=page, chunk_id=chunk_id,
    )
    if not rows:
        return None
    return table_rows_to_model(
        rows, source_document=source_document, page=page, chunk_id=chunk_id,
    )


def parse_troubleshooting_2col_tables(
    tables,
    *,
    source_document: str | None = None,
    page: int | None = None,
    chunk_id: str | None = None,
) -> UniversalMachineModel | None:
    rows = parse_tables_to_rows(
        tables, "troubleshooting_table_2col",
        source_document=source_document, page=page, chunk_id=chunk_id,
    )
    if not rows:
        return None
    return table_rows_to_model(
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
# Dispatch: TableRow entry points, then the model entry point for the
# pipeline branch. The router label decides the shape; a label/content
# mismatch returns [] / None (LLM fallback), never a mis-parsed row --
# router/parser disagreement is P-01, so the two must never silently
# cover for each other.
# ---------------------------------------------------------------------------

def parse_table_rows(
    content: str,
    source_type: str,
    *,
    source_document: str | None = None,
    page: int | None = None,
    chunk_id: str | None = None,
    source_table: str | None = None,
) -> list[TableRow]:
    """Parse one table chunk's Markdown into TableRows by router label.

    ``"troubleshooting_table"`` shape-detects across the 3-col and
    2-col troubleshooting shapes (the direct-parse helper);
    ``"troubleshooting_table_2col"`` parses only the 2-col shapes;
    ``"alarm_table"`` parses alarm rows. [] = fall back to LLM.
    """
    rows = markdown_to_rows(content)
    if source_type == "troubleshooting_table":
        shape = _detect_table_shape(rows)
        if shape == "troubleshooting_3col":
            return parse_troubleshooting_3col_rows(
                rows, source_document=source_document, page=page,
                chunk_id=chunk_id, source_table=source_table,
            )
        if shape == "troubleshooting_2col":
            if any(not _is_separator_row(r) and _is_troubleshooting_2col_header(r) for r in rows):
                return parse_troubleshooting_2col_rows(
                    rows, source_document=source_document, page=page,
                    chunk_id=chunk_id, source_table=source_table,
                )
            return parse_cause_action_2col_rows(
                rows, source_document=source_document, page=page,
                chunk_id=chunk_id, source_table=source_table,
            )
        return []
    if source_type == "troubleshooting_table_2col":
        shape = _detect_table_shape(rows)
        if shape != "troubleshooting_2col":
            return []
        if any(not _is_separator_row(r) and _is_troubleshooting_2col_header(r) for r in rows):
            return parse_troubleshooting_2col_rows(
                rows, source_document=source_document, page=page,
                chunk_id=chunk_id, source_table=source_table,
            )
        return parse_cause_action_2col_rows(
            rows, source_document=source_document, page=page,
            chunk_id=chunk_id, source_table=source_table,
        )
    if source_type == "alarm_table":
        return parse_alarm_rows(
            rows, source_document=source_document, page=page,
            chunk_id=chunk_id, source_table=source_table,
        )
    return []


def parse_tables_to_rows(
    tables,
    source_type: str,
    *,
    source_document: str | None = None,
    page: int | None = None,
    chunk_id: str | None = None,
    source_table: str | None = None,
) -> list[TableRow]:
    """Parse pdfplumber tables into TableRows, one table at a time.

    Shape is detected per table (a chunk is one table after Step 1
    chunking, but a page may still carry several tables of different
    shapes). Consecutive 3-col tables are parsed joined, so a Problem
    that continues across a page break (§8-17, PDF pp. 48-49) still
    carries forward; 2-col carry-forward (current Trouble) is per
    table.
    """
    out: list[TableRow] = []
    row_lists = _tables_to_row_lists(tables)
    if source_type == "troubleshooting_table_2col":
        for row_list in row_lists:
            shape = _detect_table_shape(row_list)
            if shape != "troubleshooting_2col":
                continue
            if any(not _is_separator_row(r) and _is_troubleshooting_2col_header(r) for r in row_list):
                out.extend(parse_troubleshooting_2col_rows(
                    row_list, source_document=source_document, page=page,
                    chunk_id=chunk_id, source_table=source_table,
                ))
            else:
                out.extend(parse_cause_action_2col_rows(
                    row_list, source_document=source_document, page=page,
                    chunk_id=chunk_id, source_table=source_table,
                ))
        return out
    if source_type == "troubleshooting_table":
        # 3-col tables may span pages: consecutive 3-col row lists
        # are parsed joined, so an empty Problem cell on the second
        # page still carries the previous problem forward. Input
        # order is preserved: a 2-col table between two 3-col tables
        # flushes the joined 3-col run first.
        three_col: list[list[str]] = []

        def _flush_three_col() -> None:
            nonlocal three_col
            if three_col:
                out.extend(parse_troubleshooting_3col_rows(
                    three_col, source_document=source_document, page=page,
                    chunk_id=chunk_id, source_table=source_table,
                ))
                three_col = []

        for row_list in row_lists:
            shape = _detect_table_shape(row_list)
            if shape == "troubleshooting_3col":
                three_col.extend(row_list)
            else:
                _flush_three_col()
                if shape == "troubleshooting_2col":
                    if any(not _is_separator_row(r) and _is_troubleshooting_2col_header(r) for r in row_list):
                        out.extend(parse_troubleshooting_2col_rows(
                            row_list, source_document=source_document, page=page,
                            chunk_id=chunk_id, source_table=source_table,
                        ))
                    else:
                        out.extend(parse_cause_action_2col_rows(
                            row_list, source_document=source_document, page=page,
                            chunk_id=chunk_id, source_table=source_table,
                        ))
        _flush_three_col()
        return out
    if source_type == "alarm_table":
        for row_list in row_lists:
            out.extend(parse_alarm_rows(
                row_list, source_document=source_document, page=page,
                chunk_id=chunk_id, source_table=source_table,
            ))
        return out
    return []


def parse_table_chunk(
    content: str,
    source_type: str,
    *,
    source_document: str | None = None,
    page: int | None = None,
    chunk_id: str | None = None,
) -> UniversalMachineModel | None:
    """Parse a table chunk by its router label. None = fall back to LLM."""
    if source_type in ("troubleshooting_table", "troubleshooting_table_2col", "alarm_table"):
        rows = parse_table_rows(
            content, source_type,
            source_document=source_document, page=page, chunk_id=chunk_id,
        )
        if not rows:
            return None
        return table_rows_to_model(
            rows, source_document=source_document, page=page, chunk_id=chunk_id,
        )
    if source_type == "spec_table":
        return parse_spec_markdown(
            content, source_document=source_document, page=page, chunk_id=chunk_id,
        )
    return None
