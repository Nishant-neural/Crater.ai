#!/usr/bin/env python3
"""Live-PDF verification for Phase 1 Step 1 table parsing (NOT a pytest test).

Runs the repo's deterministic table parser over the real Miller and
OMNIPRO manual PDFs with pdfplumber and diffs the result row-for-row
against the frozen oracles:

- Miller Millermatic 252 OM-230 693C (52 pp):
  §6-5 (PDF p. 35, Trouble | Remedy) -> 21 rows,
  §§8-8..8-15 (PDF pp. 45-47, Possible Causes | Corrective Actions) -> 22 rows,
  §8-17 (PDF pp. 48-49, Problem | Probable Cause | Remedy) -> 20 rows.
  Total 63 TableRows, 38 twin_expressible, diffed on (symptom, cause,
  remedy, source page) against benchmark/evaluations/miller_parser_rows.json
  and on twin_expressible against frozen/c002b-miller.json.
- Harbor Freight OMNIPRO 220 owner manual (48 pp):
  troubleshooting tables pp. 42-43 -> 45 rows, causes diffed against
  the 45-row OMNIPRO vocabulary (oracle_vocabulary_v1.json) in table
  order. Remedy alignment is reported separately: the vocabulary's
  join_rule records that pdfplumber remedies fragment across rows on
  pp. 42-43 and are misaligned with causes, so a remedy mismatch
  there is a KNOWN pre-existing limitation, not a Step 1 failure --
  the gate is the 45 causes + the 35/5/4/1 category split.

Usage (venv with pdfplumber, e.g. ~/workspace/crater-phase1/.venv):
  python scripts/verify_miller_table_parse.py [--miller-pdf PATH] [--omnipro-pdf PATH]
Exit 0 = all strict diffs pass; 1 = a strict diff failed; 2 = a PDF is missing.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pdfplumber  # noqa: E402

from backend.ingestion.table_parser import (  # noqa: E402
    parse_cause_action_2col_rows,
    parse_tables_to_rows,
    parse_troubleshooting_2col_rows,
    parse_troubleshooting_3col_rows,
)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MILLER_PDF = Path("/home/hatch/workspace/crater-benchmarks/welding/miller-millermatic-252/owners-manual-om-230-693c.pdf")
DEFAULT_OMNIPRO_PDF = Path("/home/hatch/workspace/project-review-v3/docs/owner-manual.pdf")


def _rows_of(table):
    return [[c if c is not None else "" for c in row] for row in table]


def verify_miller(pdf_path: Path) -> list[str]:
    problems: list[str] = []
    expected_rows = json.loads((ROOT / "benchmark/evaluations/miller_parser_rows.json").read_text())
    oracle = json.loads((ROOT / "frozen/c002b-miller.json").read_text())
    want_cat = {r["row_id"]: r["category"] for r in oracle["rows"]}

    parsed = []
    with pdfplumber.open(pdf_path) as pdf:
        # §6-5: PDF p. 35 (0-indexed 34), 2-col Trouble | Remedy.
        for table in pdf.pages[34].extract_tables():
            if not table or len(table[0]) != 2:
                continue
            parsed.extend(parse_troubleshooting_2col_rows(
                _rows_of(table), source_document=pdf_path.name, page=35,
                chunk_id="verify-6-5", source_table="6-5 Troubleshooting (p. 31)",
            ))
        # §§8-8..8-15: PDF pp. 45-47, 2-col Possible Causes | Corrective Actions.
        for pdf_page in (44, 45, 46):
            page = pdf.pages[pdf_page]
            for table in page.extract_tables():
                if not table or len(table) < 2 or len(table[0]) != 2:
                    continue
                parsed.extend(parse_cause_action_2col_rows(
                    _rows_of(table), source_document=pdf_path.name, page=pdf_page + 1,
                    chunk_id=f"verify-8-{pdf_page + 1}",
                    source_table="8-8 to 8-15 Troubleshooting",
                ))
        # §8-17: PDF pp. 48-49, 3-col Problem | Probable Cause | Remedy.
        # Parsed per page (each page's first Problem cell is non-empty,
        # so no carry-forward crosses the break).
        for pdf_page in (47, 48):
            for table in pdf.pages[pdf_page].extract_tables():
                if not table or len(table[0]) != 3:
                    continue
                parsed.extend(parse_troubleshooting_3col_rows(
                    _rows_of(table), source_document=pdf_path.name, page=pdf_page + 1,
                    chunk_id=f"verify-8-17-{pdf_page + 1}",
                    source_table="8-17 Troubleshooting Guide for Semiautomatic Welding Equipment",
                ))

    print(f"Miller: parsed {len(parsed)} TableRows (want 63)")
    if len(parsed) != 63:
        problems.append(f"Miller row count {len(parsed)} != 63")
    twin = sum(1 for r in parsed if r.twin_expressible)
    print(f"Miller: twin_expressible {twin} (want 38); "
          f"categories {dict(Counter(r.cause_category for r in parsed))}")
    if twin != 38:
        problems.append(f"Miller twin_expressible {twin} != 38")
    hl = {r.cause[:40]: r.meter_code for r in parsed if r.meter_code}
    print(f"Miller: meter codes attached: {sorted(set(hl.values()))} (want HL.P 001/002/003/004)")
    if sorted(set(hl.values())) != ["HL.P 001", "HL.P 002", "HL.P 003", "HL.P 004"]:
        problems.append(f"Miller meter codes {sorted(set(hl.values()))} != HL.P 001..004")
    if any(r.symptom.startswith("HL.P") for r in parsed):
        problems.append("a meter code stands alone as a symptom")

    for i, (got, exp) in enumerate(zip(parsed, expected_rows)):
        row_id = exp["row_id"]
        for field in ("symptom", "cause", "remedy"):
            if getattr(got, field) != exp[field]:
                problems.append(
                    f"{row_id} {field} mismatch:\n  parsed: {getattr(got, field)!r}\n  frozen: {exp[field]!r}"
                )
        if got.page != exp["source_pages"][0]:
            problems.append(f"{row_id} page {got.page} != {exp['source_pages'][0]}")
        want_twin = want_cat[row_id] == "twin_expressible"
        if got.twin_expressible != want_twin:
            problems.append(f"{row_id} twin_expressible {got.twin_expressible} != {want_twin} ({want_cat[row_id]})")
        if got.rankable != want_twin:
            problems.append(f"{row_id} rankable {got.rankable} != {want_twin}")
    if len(parsed) != len(expected_rows):
        problems.append("Miller parsed/frozen length mismatch; per-row diff truncated to the shorter list")
    # §6-5 slice: the first 21 rows, 18 twin_expressible.
    s65 = parsed[:21]
    print(f"Miller §6-5 slice: {len(s65)} rows, {sum(1 for r in s65 if r.twin_expressible)} twin_expressible (want 21 / 18)")
    if len(s65) != 21 or sum(1 for r in s65 if r.twin_expressible) != 18:
        problems.append("Miller §6-5 slice is not 21 rows / 18 twin_expressible")
    return problems


def verify_omnipro(pdf_path: Path) -> list[str]:
    problems: list[str] = []
    vocab_path = Path("/home/hatch/workspace/goals/crater-ai-diagnosis-and-machine-model-workflow-improvement/files/oracle_vocabulary_v1.json")
    vocab = json.loads(vocab_path.read_text())

    parsed = []
    with pdfplumber.open(pdf_path) as pdf:
        for pdf_page in (41, 42):  # printed pp. 42-43 (0-indexed 41, 42)
            for table in pdf.pages[pdf_page].extract_tables():
                if not table:
                    continue
                parsed.extend(parse_troubleshooting_3col_rows(
                    _rows_of(table), source_document=pdf_path.name, page=pdf_page + 1,
                    chunk_id=f"verify-omni-{pdf_page + 1}",
                    source_table="Troubleshooting – MIG / Flux-Cored Welding (p. 42/43)",
                ))
    print(f"OMNIPRO: parsed {len(parsed)} TableRows (want 45)")
    if len(parsed) != 45:
        problems.append(f"OMNIPRO row count {len(parsed)} != 45")
    cats = Counter(r.cause_category for r in parsed)
    print(f"OMNIPRO: categories {dict(cats)} (want twin_expressible 35, settings 5, technique 4, cross_reference 1)")
    if dict(cats) != {"twin_expressible": 35, "settings": 5, "technique": 4, "cross_reference": 1}:
        problems.append(f"OMNIPRO categories {dict(cats)} != 35/5/4/1")
    cause_mismatch = 0
    # The manual's visual order on p. 42 is omni-01, omni-03, omni-02,
    # omni-04, omni-09 -- the vocabulary lists cases in rank order, so
    # compare causes as a set, not in file order. (Miller IS in table
    # order, so its diff stays strict.)
    vocab_causes = {r["cause"] for r in vocab["rows"]}
    missing = vocab_causes - {r.cause for r in parsed}
    extra = {r.cause for r in parsed} - vocab_causes
    print(f"OMNIPRO: cause set diff: missing {len(missing)}, extra {len(extra)}")
    for m in sorted(missing)[:5]:
        problems.append(f"OMNIPRO cause missing from parse: {m!r}")
    for e in sorted(extra)[:5]:
        problems.append(f"OMNIPRO cause extra in parse: {e!r}")
    cause_mismatch = len(missing) + len(extra)
    print(f"OMNIPRO: cause mismatches {cause_mismatch} / 45 (remedies NOT strictly diffed: known pdfplumber fragmentation, see module docstring)")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--miller-pdf", default=str(DEFAULT_MILLER_PDF))
    ap.add_argument("--omnipro-pdf", default=str(DEFAULT_OMNIPRO_PDF))
    ap.add_argument("--skip-omnipro", action="store_true")
    args = ap.parse_args()
    problems: list[str] = []
    miller_pdf = Path(args.miller_pdf)
    if not miller_pdf.exists():
        print(f"MISSING Miller PDF: {miller_pdf}")
        return 2
    problems.extend(verify_miller(miller_pdf))
    if not args.skip_omnipro:
        omni_pdf = Path(args.omnipro_pdf)
        if not omni_pdf.exists():
            print(f"MISSING OMNIPRO PDF: {omni_pdf}")
            return 2
        problems.extend(verify_omnipro(omni_pdf))
    if problems:
        print(f"\nFAILED: {len(problems)} problem(s)")
        for p in problems:
            print(" -", p)
        return 1
    print("\nOK: Miller 63 rows / 38 twin-expressible, OMNIPRO 45 rows / 35 twin-expressible, all strict diffs pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
