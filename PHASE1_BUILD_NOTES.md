# Phase 1 build notes — source-type router + top cases + Tier 1

**Date:** 13 Nov 2026. **Base:** v12 (`project-review-v12-patched.zip`).
**Package:** `~/workspace/project-review-phase1-patched.zip` (this tree).
**Design:** `phase1-implementation.md` in this goal folder.

## What was found in the overnight build, and fixed here

The delegated build had written the router, table parser and case
registry, but stalled on a syntax error in
`backend/knowledge/repair_cases.py` (unmatched `]` in
`coverage_for_case`), and had not wired the parser/registry into the
pipeline or written any tests. This run fixed the syntax error, wired
the pipeline, fixed two parser bugs the new tests exposed (numbered
cause cells on one line; the spec parser claiming numbered prose),
and added the §6 tests.

## What Phase 1 now does

1. **Source-type router** (`backend/ingestion/source_router.py`):
   deterministic page labels (`troubleshooting_table`, `alarm_table`,
   `spec_table`, `procedure`, `maintenance_procedure`, `diagram`,
   `parameter_reference`, `other`), attached in `pdf_loader.py` and
   carried on every chunk's `extra["source_type"]` (+ `source_types`
   list for cross-page chunks). Normalisation gate re-routes labels
   with no supporting table structure. Zero LLM calls.
2. **Deterministic table parser** (`backend/ingestion/table_parser.py`):
   troubleshooting rows → `MachineFailureMode` + repair procedure;
   narrow spec rows → `MachineQuantity`; evidence carries
   `source_type` + `extraction_method="deterministic_table_parse"`.
   Malformed tables return `None` and fall back to the LLM path.
3. **Pipeline wiring** (`backend/ingestion/pipeline.py`): table chunks
   labelled troubleshooting/spec are parsed deterministically and
   skip the LLM batch entirely; they rejoin at provenance →
   quarantine → persist. Provenance now upgrades a generic LLM
   `"text"` source_type to the router label (specific labels kept).
4. **Case registry + Tier gating** (`backend/knowledge/repair_cases.py`
   + `repair_cases_omnipro.json`): the 9 OMNIPRO cases as data;
   **Tier-3 pages (pp35–40) are never extracted into the model**
   (registry-product only; other products keep v12 behaviour);
   per-case coverage computed from a snapshot on demand.

## Deliberate deviation from the design, to flag

Design §5 says Tier-2 extraction should default off with an on-demand
flag (a Phase 4/6 trigger). This build gates **Tier 3 only** and
leaves Tier 2 on the v12 LLM path. Gating Tier 2 by default would
thin every OMNIPRO snapshot to ~7 pages with no job flag to widen it
— a large untested behaviour change. Tier-2 gating needs that flag
first; it is a Phase 4/6 decision, not smuggled into Phase 1.

Also out of scope, as designed: the three Phase 0 fixes (Feed
Tensioner dedupe, adjustment-cause mapping, frontend session
restore), library v1/compiler v2, backtracking walk, Mode B flow,
OCR, STEP import. No new LLM calls anywhere in Phase 1; table chunks
stop making LLM calls.

## Verification (this environment, 13 Nov 2026)

- New tests: `tests/test_source_router.py` (9),
  `tests/test_table_parser.py` (8), `tests/test_phase1_pipeline.py` (2)
  — **19 passed**.
- Full suite: **217 passed, 2 failed, 1 collection error**
  (`test_phase8a_hardening.py` imports a name that no longer exists).
  Baseline before this build's pipeline wiring was 198 passed with
  the same 2 failures; both failures reproduce on the pristine v12
  zip, so they are pre-existing, not Phase 1 regressions.
- Real manual check (`owner-manual.pdf`, 48 pp, loaded here):
  p42 → `troubleshooting_table`, parses to 5 failure modes
  (cases 1, 2, 3, 4, 9 with 4/6/4/8/3 causes);
  p43 → `troubleshooting_table`, 4 failure modes (cases 5, 6, 7, 8);
  p7 → `spec_table`, 28 quantities. All 9 registry cases covered.
- Not verifiable here: end-to-end ingestion with the user's LLM +
  Qdrant, and the live check. `gold_set_test.py` needs a real DB +
  LLM and was not run.

## Live check for Nishant (fresh OMNIPRO upload/job, not Retry)

1. Job inspection: p42/p43 chunks carry
   `source_type="troubleshooting_table"`; p7 chunks `spec_table`;
   no Tier-3 page (pp35–40) extracted into the model.
2. Snapshot: case 1's failure mode has exactly its 4 causes with
   page citations; p7 quantities present with values + units.
3. Diagnose the p42 symptom as before — same hypotheses/questions
   as v12; per-case coverage for case 1 shows all 4 causes bound.
4. Compare the job's extraction call count with the v12 baseline —
   expect fewer LLM calls (table chunks no longer go through the LLM).
