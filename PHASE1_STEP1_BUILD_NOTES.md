# Phase 1 — Step 1 build notes: deterministic 2-col + 3-col table parsing (P-01, P-02, P-09b)

Date: 23 Nov 2026. Scope: Step 1 of the ingestion audit only. Gate: the three
pytest suites + the 38-row verification below. No retrieval, diagnosis, twin,
or frontend code touched. No Celery/Redis work started (infra, later); the
existing job/checkpoint mechanism is unchanged.

## What was wrong (audit + problem list)

- `table_parser.py` parsed only the 3-col Problem | Possible Cause | Remedy
  shape (OMNIPRO p42/p43). Miller §6-5 is 2-col Trouble | Remedy, so it
  classified as `other` in the router and fell back to per-chunk LLM
  extraction: 1 remedy per trouble from the table chunk, 3 from the text
  chunk (P-01). Miller §8-17's "Probable Cause" header failed the old
  "possible cause" header test too, and §§8-8..8-15 (Possible Causes |
  Corrective Actions, title row above the header) were invisible to the
  router's first-row-only header scan.
- Tables were grouped per page into one chunk up to 15,000 chars (P-02):
  the Miller table sits at character 3,418 of chunk 62f72eda, past every
  downstream cut.
- Zero HL.P handling anywhere in backend/ or tests/ (P-09b, verified by
  the audit's recheck).

## What changed

### `backend/ingestion/table_parser.py` (345 -> 1,190 lines)
- New first-class `TableRow` dataclass (audit contract): `table_shape`,
  `symptom` / `symptom_norm`, `cause` / `cause_index`, `remedy` /
  `remedy_index`, `remedy_source`, `meter_code`, `rankable` /
  `rankable_reason`, `twin_expressible`, `cause_category`, `source_document`
  / `source_type` / `source_table` / `page` / `chunk_id`, `extraction_method`
  (`deterministic_table_parse`), `confidence` (1.0), `failure_mode_id`,
  `flags`, `provenance_precedence` (MTB 3 > OEM 2 > generic 1, from
  `backend.knowledge.provenance`), stable `id`.
- New builders, all deterministic, no LLM:
  - `parse_troubleshooting_2col_rows` — §6-5 Trouble | Remedy. One row
    per table row; Trouble carries forward onto continuation rows; cause
    is the remedy text verbatim, flagged `cause_from_remedy`.
  - `parse_cause_action_2col_rows` — §§8-8..8-15 Possible Causes |
    Corrective Actions. Symptom from the defect title row above the
    header; continuation rows (empty cause cell) join their actions
    onto the current row.
  - `parse_troubleshooting_3col_rows` — OMNIPRO p42/p43 (numbered items
    in the cause cell, paired by number with solutions) and Miller §8-17
    (one cause per row, empty Problem carries forward). Includes the
    doubled-letter print-artifact repair (ported from the reviewed
    `tools/parse_welder_oracles.py`) and the `grouping_as_printed` flag
    for the primary-voltage row.
  - `parse_alarm_rows` — Alarm Code | Message | Cause | Remedy; symptom
    is "code + message", never the code alone.
- `classify_cause(cause, remedy)` — deterministic cause category
  (`twin_expressible` / `settings` / `technique` / `cross_reference`)
  from phrase-precise patterns. Reproduces the Miller oracle categories
  (38/16/9) and the OMNIPRO vocabulary categories (35/5/4/1) exactly,
  verified in code against both oracles (see Verification). Phrase
  precision matters: "Incorrect line voltage." is twin_expressible (a
  wrong supply is a fault) while "Voltage too high." is settings; "Wrong
  gas." is settings but "Wrong gas flow." is twin_expressible; "Dirty or
  damaged (kinked) liner." is twin_expressible while "Dirty welding
  wire." is technique. Cross-reference fires when the paired remedy
  routes to another troubleshooting section ("See first Troubleshooting
  section above").
- `rankable` follows `twin_expressible`, with a CORRECTION to the audit
  (pp. 11, 14), per the 23 Nov 2026 harness addendum
  (`benchmark/HARNESS_SCORER_FIX_NOTES.md`, Phase 0 addendum (a),
  verified against the oracle): Miller's 4 HL.P rows (miller-04/05/06/11)
  and 5 pure "Have Factory Authorized Service Agent check ..." rows
  (miller-07/08/14/15/21) are twin_expressible WITH structured faults,
  i.e. rankable, and stay in the 38-row denominator. The genuinely
  unrankable rows are the 25 settings (16) + technique (9) rows.
  `rankable_reason` `meter_code` / `generic_service_fallback` therefore
  fire only for a row whose cause is nothing but a meter-code mention /
  a bare referral naming no component -- a shape in neither oracle.
- `extract_meter_code` — `HL.P 001` canonical form; attaches to the
  parent row, never a standalone symptom.
- `parse_table_rows` / `parse_tables_to_rows` (new TableRow entry points;
  shape detected per table; consecutive 3-col tables parse joined so a
  Problem carries across a page break) and `table_rows_to_model`
  (one FailureMode + one repair Procedure per symptom). The existing
  model builders (`parse_troubleshooting_rows/markdown/tables`,
  `parse_table_chunk`, spec parsers) are reimplemented on TableRows with
  unchanged signatures and behaviour; `parse_table_chunk` is label-strict
  (a label/content mismatch returns None -> LLM fallback, never a
  mis-parsed row -- router/parser disagreement is P-01).
- `_norm_space` now dehyphenates letter-hyphen line breaks exactly as
  the oracle builder's `clean()` does ("oper-\nates" -> "operates");
  spaced dashes are kept.

### `backend/ingestion/source_router.py` (218 -> ~250 lines)
- New `SourceType.TROUBLESHOOTING_TABLE_2COL`. Header scan now covers
  the first TWO rows per table (title row above the §§8 header), the
  3-col test accepts "Probable Cause" (§8-17), and the 2-col test covers
  Trouble | Remedy and Possible Causes | Corrective Actions. The
  normalisation gate validates the 2-col label the same way. Routing
  and parsing can no longer disagree on shape (P-01).

### `backend/ingestion/pipeline.py` (+6 lines)
- `_phase1_deterministic_model` accepts `troubleshooting_table_2col`
  and `alarm_table` in addition to `troubleshooting_table` /
  `spec_table`. Nothing else changed; no job/checkpoint/Celery work.

### `backend/ingestion/chunking.py` (373 -> ~400 lines)
- `_grouped_table_chunks` replaced by `_atomic_table_chunks`: one table
  = one chunk (`chunking="atomic_table"`, `table_index`, `table_group`,
  rows/cols, section/page_kind/source_type). A single table over the
  retrieval cap splits into row groups via `_split_table_markdown`
  (header repeated per group, never a split row). The old name stays as
  a backward-compatible alias. Text chunking is UNCHANGED (large-context
  extraction sizing stays until Step 2 builds the second stream).

### `backend/config.py` (+13 lines; extraction values untouched)
- New `retrieval_chunk_max_chars = 4096` (~1,024 tokens) and
  `retrieval_chunk_target_chars = 2048`, governing tables only in
  Step 1. `chunk_size_chars` (12000) / `chunk_max_chars` (15000) /
  `chunk_min_chars` (2500) / `chunk_overlap_chars` (1000) are unchanged
  (a test pins their literal values).

### `backend/knowledge/repair_cases_miller.json` (new, 17 cases / 63 causes)
- Miller Millermatic 252 registry, generated from the frozen oracle
  (`frozen/c002b-miller.json` + `benchmark/evaluations/miller_parser_rows.json`).
  Tier 1 = troubleshooting tables (PDF pp. 35, 45-49). 11 cases carry
  twin-expressible rows; miller-08..13 carry settings/technique only.
  `load_miller_registry()` added to `repair_cases.py`; `load_registry()`
  (OMNIPRO) is unchanged, and the Miller registry is NOT wired into
  `_phase1_registry_for_product` in this step (deterministic parsing is
  not registry-gated; Tier-3 gating for Miller is a later wiring call).

### `scripts/verify_miller_table_parse.py` (new)
- Live-PDF verification (not a pytest test; the PDFs live outside the
  repo): parses the real Miller + OMNIPRO PDFs with pdfplumber and diffs
  row-for-row against the frozen oracles. Exit 0 = all strict diffs pass.

## Verification

### Gate suites (all green)
`~/workspace/crater-phase1/.venv/bin/python -m pytest tests/test_table_parser.py tests/test_source_router.py tests/test_phase1_pipeline.py tests/test_chunking.py -q`
-> **41 passed** (baseline was 25; +16 new tests: 8 table-parser, 4 router,
3 chunking, 1 pipeline).

### 38-row verification (live PDF + frozen oracles)
`~/workspace/crater-phase1/.venv/bin/python scripts/verify_miller_table_parse.py`
-> exit 0:
- Miller: **63 TableRows** (per-row strict diff on symptom/cause/remedy/
  page vs `benchmark/evaluations/miller_parser_rows.json`, zero mismatches),
  **38 twin_expressible** (categories 38/16/9, matching
  `frozen/c002b-miller.json` denominator exactly), meter codes
  HL.P 001/002/003/004 attached to miller-04/05/06/11, no meter code as
  a symptom. §6-5 slice: **21 rows, 18 twin_expressible**.
- OMNIPRO: **45 TableRows**, categories 35/5/4/1, cause set exactly equal
  to the 45-row vocabulary (0 missing, 0 extra).
- Note on the "§6-5 = 38 rows" shorthand: §6-5 proper is 21 rows; 38 is
  the twin-expressible count across all Miller troubleshooting tables
  (§6-5 21 + §§8-8..8-15 22 + §8-17 20 = 63). The tests assert both
  numbers, so the shorthand cannot silently become a false claim.

### Classifier check (before tests were written)
`classify_cause` vs oracle categories: Miller 63/63, OMNIPRO vocabulary
45/45, OMNIPRO registry wording 45/45 — zero mismatches.

### Full suite
A whole-`tests/` run was started but killed after ~11 min with no
failure observed: several pre-existing simulation tests take ~38-40 s
each (`test_phase456_followup_walk`, `test_phase4_backtracking`,
`test_phase5_learning` fixtures). Instead, every file that imports a
changed module was run and passes:
- Gate suites: 41 passed (above).
- Ingestion-adjacent subset (ingestion fixes, Gemini fix, empty-retry,
  large-manual, extraction hardening, batch extraction, phase8a
  integration, pdf-loader images, mode-B, extraction-performance
  config, phase1 hardening, phase2 canonical model + dedupe):
  **135 passed in 13 s**.
- `tests/test_phase3a_oracle.py` (imports `parse_troubleshooting_tables`
  / `parse_troubleshooting_markdown`): **22 passed in 1.4 s**.
- `test_phase456_followup_walk` + `test_phase4_backtracking` +
  `test_phase4_steals` + `test_diagnostics` + `test_phase5_learning` +
  `test_harness_scorer_reporting`: **60 passed in 198 s** (slow tests
  are pre-existing, ~38-40 s each).
- Known pre-existing environmental failure, NOT caused by this step:
  `tests/test_benchmark_oracle_runner.py::test_real_oracle_json_runs_on_synthetic_twin`
  hardcodes `/home/hatch/crater-benchmarks/evaluations/...`, a path
  that does not exist in this VM (benchmarks live under
  `~/workspace/crater-benchmarks/`); it fails with "oracle file not
  found" before any of this step's code runs.
- Pristine zip re-verified after all work: sha256
  `d6886155a4394a727a32f8e38ac6794d81678ebeb02596c30e7371bacecedb92` (unchanged).

## What was NOT done / left out (Step 2+ or later)
- No DB `table_rows` table (audit Step 5, jobs infra): TableRows are
  first-class in memory and flow into FailureModes through the existing
  persist path; Postgres persistence lands with the jobs API.
- No Docling/TableFormer swap (Phase 2, only after the oracle gate, with
  an A/B row-for-row diff): pdfplumber stays behind the RawPage wrapper.
- No two-stream text chunking (Step 2): text keeps the 12k extraction
  sizing; only tables got the retrieval cap.
- No structured-output extraction (Step 3), no LLM re-emission removal
  (Step 4), no expert-trio cut (Step 4), no Celery/Redis (Step 5).
- OMNIPRO remedy alignment on the live PDF is NOT strictly diffed:
  the vocabulary's join_rule records that pdfplumber remedies fragment
  across rows on pp. 42-43 and are misaligned with causes (the frozen
  oracle therefore carries registry remedies). Causes + categories are
  exact; remedy fragmentation is a known pre-existing limitation.
- The persisted `FailureMode.symptom` is stored doubled as
  "<name>: <symptoms>" by `persist_machine_knowledge` (pre-existing;
  the pipeline test matches on substring like the OMNIPRO test does).
