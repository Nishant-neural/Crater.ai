# Ingestion crash + Gemini config fix — build notes (21 Nov 2026)

Approved by blanket delegation from Nishant (21 Nov 2026). Work tree:
`~/workspace/crater-phase1/v12`. Venv: `~/workspace/crater-phase1/.venv`.
Package: `~/workspace/crater-phase1/project-review-phase6-readiness-ingestion-gemini-fix.zip`
(new; the previous `project-review-phase6-readiness-patched.zip` was not touched).
No LLM calls were made for this build.

## Patch — background-job failure logging (21 Nov 2026, same day)

The verifier independently confirmed the fix (PASS on all checks) and
agreed this logging change is safe. It adds one logging call plus three
tests; there is no behavior change.

**Why:** there is no traceback anywhere for Nishant's crash.
`backend/api/routes/ingestion.py`, `_run_job_background`, catches
`Exception as exc`, rolls back, and stores only
`job.error_message = str(exc)[:2000]` — and the module imported no
`logging` at all. **The traceback will land in the server log (the
uvicorn console on Nishant's laptop), not the UI** — the UI only ever
sees the stored `error_message`. Without a log call, the next
background-job crash would again leave no traceback to diagnose from.

**What changed** (`backend/api/routes/ingestion.py`, lines 5, 21, 74–77):
`import logging`, a module logger
(`logger = logging.getLogger(__name__)`), and a single
`logger.exception("Background ingestion job %s failed", job_id)` call
as the first line inside the `_run_job_background` except block (it
must be inside the except block to capture the traceback; the job id
is in the message). Logging only: it does not touch session/job state,
control flow, or the stored `error_message`.

**New tests** (`tests/test_ingestion_gemini_fix.py`, 14 → 17 tests):
- `test_background_job_failure_logs_traceback` — stubs `ingest_pdf`
  to raise, runs `_run_job_background` against a real `IngestionJob`
  row in an in-memory SQLite DB (module `SessionLocal` /
  `load_checkpoint` monkeypatched), and asserts via `caplog` that the
  traceback is logged (`Traceback`, `RuntimeError`, the message, the
  job id) and that the job row is still failed with the same stored
  `error_message` as before the change.
- `test_verification_does_not_crash_on_none_entity_type` and
  `test_verification_does_not_crash_on_none_entity_name` — the
  verifier's probe cases folded in permanently: `verify_machine_model`
  on `model_construct`-built models carrying an entity with
  `entity_type=None` / `name=None` must not raise. Assert no-raise,
  not zero issues (a model like this can legitimately carry warnings,
  e.g. the disconnected-graph warning the verifier observed).

**Verification of this patch:** `tests/test_ingestion_gemini_fix.py`
17 passed; ingestion-route-related files
(`test_ingestion_fixes_v4`, `test_phase1_pipeline`,
`test_pipeline_empty_retry`, `test_phase6_mode_b`) 21 passed; app
smoke import OK (`backend.api.main:app` imports, ingestion router
mounts its 5 routes, logger is `backend.api.routes.ingestion`). A
full-suite re-run was **not** done for this logging-only patch (the
targeted runs above are fast and clean); the expected full-suite total
is 426 + 3 = 429. The re-packed zip below carries the patched files.

## What was fixed

### FIX A — ingestion crash `'NoneType' object has no attribute 'strip'`

**A1 — per-fact filter in `_universal_model_from_result`
(`backend/knowledge/component_extraction.py`, ~lines 747–800, 840–880).**
Relations / behaviors / procedures with a missing required endpoint or name
(None, non-string, or blank after stripping) are dropped *before* the merge
dedupe, so a None endpoint can never reach `.strip()`. The drop is visible,
not silent: one `logger.warning` per call on
`backend.knowledge.component_extraction` carrying the chunk id (when known)
and counts `relations=… behaviors=… procedures=… legacy_relationships=…`
plus a short preview of the dropped items. The chunk's other facts are kept.

- `_universal_model_from_result(result, chunk_id=None)` — new optional
  `chunk_id` parameter. The batch caller threads its chunk id through;
  `extract_chunk_knowledge(content, chunk_id=None)` threads it too, and
  the pipeline's empty-retry call passes the retry chunk's id
  (`backend/ingestion/pipeline.py:452`). The direct single-chunk path has
  no chunk id, so its warning carries counts with `chunk_id=None`.
- Dropped: a relation whose `subject_name` or `object_name` is blank; a
  behavior (raw dict) whose `subject_name` is blank; a procedure whose
  `name` is blank; a legacy relationship with a blank endpoint. A relation
  with `relation_type=None` is *not* dropped — the model normalizer records
  it as `"unspecified"`, the established placeholder for an unstated type.
  A behavior with a missing description is likewise *not* dropped — it keeps
  the normalizer's explicit placeholder, the same pattern used for
  quantities and states.
- Merge-dedupe keys are now None-safe (`(x or "").strip().lower()`) as a
  second layer, so even a None slipping past the filter cannot crash the merge.
- `_legacy_model` (~lines 680–745) gained the same guards for its
  `rel.from_component.lower()` / `proc.name.lower()` calls (skip + warning);
  they protect its standalone callers, since `_universal_model_from_result`
  now passes it a filtered copy.

**Schema change that makes A1 reachable (deviation from the brief's
letter, required by its test (i) — see "Findings" below):
`backend/knowledge/schema.py`, `UniversalRelation` (~lines 51–66).**
`subject_name`, `object_name`, `relation_type` are now `str | None = None`.
Previously a null endpoint failed the *whole chunk* at schema validation,
so the filter in `_universal_model_from_result` could never see it and the
chunk's other facts were lost. Now the bad relation reaches the filter and
only it is dropped.

**A2 — widened `except` around `_universal_model_from_result`
(`backend/knowledge/component_extraction.py`, ~lines 459–470 batch,
~lines 483–495 single).** Both `except ValueError` blocks now catch
`(ValueError, AttributeError)` and convert to `ExtractionError` with the
same message and retryable semantics as before. Deliberately kept narrow:
only the two exception classes this conversion is known to raise
(pydantic `ValidationError` is a `ValueError` subclass). One bad chunk now
fails per-chunk and is recorded in the ledger; it can no longer kill the
whole job as an uncaught `AttributeError`. The pipeline's `run_batch`
already catches only `ExtractionError`, so no pipeline change was needed.

**A3 — audit of the rest of the ingestion path.**
- `backend/knowledge/verification.py` (~lines 58, 68–69, 78, 90): the known
  candidate `r.relation_type.strip().lower()` is now
  `(r.relation_type or "").strip().lower()`; the same guard was applied to
  `e.entity_type` and `e.name` in the same function, which had the identical
  unguarded pattern.
- Checked and left unchanged (with reason):
  - `backend/knowledge/global_integration.py` — its `.strip()` calls use
    `x.get(..., "")` defaults or are guarded by `if not raw`; no unguarded
    None `.strip()` found.
  - `backend/knowledge/validation.py` — already uses `(x or "")` /
    `if e.name` guards throughout.
  - `persist_machine_knowledge` / `persist_extraction`
    (`component_extraction.py`) — no `.strip()` calls on model fields;
    DB columns for names are non-nullable but the inputs are validated
    strings by then.
  - `backend/knowledge/legacy_projection.py::_slug` — inputs are
    required-`str` schema fields; no change.
  - `MachineEntityRelation` (`machine_model.py:151–160`) itself was left
    Optional: it is only exported, never used as a relations element type,
    so tightening it would be cosmetic. The crash surface was the
    `.strip()` call sites, which are now guarded.
- Fail-closed contract unchanged: a chunk failure is recorded in the
  unresolved-facts ledger, never silently skipped; dropped items are
  logged (see A1).

### FIX B — Gemini config traps (`backend/config.py`, ~lines 1–25)

- **B1:** `gemini_api_key` now reads `GEMINI_API_KEY` with `GOOGLE_API_KEY`
  as fallback, via
  `Field(default="", validation_alias=AliasChoices("GEMINI_API_KEY", "GOOGLE_API_KEY"))`.
  Verified with pydantic-settings 2.16.2 in this codebase: Google-only key
  is picked up, Gemini wins when both are set, neither set stays `""`, and
  a `GEMINI_MODEL` env override still works.
- **B2:** default `gemini_model` changed `gemini-3.8-flash` →
  `gemini-2.5-flash-lite`. **The model id could not be verified live
  against Google's model list in this build** (no live probe was run here);
  if the API 404s on it, the error will name the model, and `llm-probe.py`
  on Nishant's laptop remains the decider.
- `.env.example`: `GEMINI_MODEL` updated to `gemini-2.5-flash-lite` and a
  comment added documenting the `GOOGLE_API_KEY` fallback.
- NOT changed, per brief: the anthropic default provider, the fail-closed
  degradation behavior (provider failure degrading to a question step), and
  the extraction rate limiter.

## Findings (deviations / corrections to the brief)

1. **The crash as literally described cannot occur through the validated
   LLM path as it stood.** Verified empirically before patching: a
   `MachineRelation` built with `subject_name=None` is normalized to
   `"Unresolved subject"` by `MachineModelBase`, a `MachineBehavior` with
   `subject_name=None` likewise, and `UniversalRelation` with a null
   endpoint *failed schema validation* (`ValidationError`) before ever
   reaching the merge — i.e. the chunk failed per-chunk as an
   `ExtractionError` (fail-closed, recorded), losing its other facts, but
   did not crash the job with `AttributeError`. The `AttributeError` is
   reachable only for a model built outside validation
   (`model_construct`, direct attribute assignment, a snapshot payload).
   The fix therefore has two layers, both implemented: the A1 filter +
   None-safe dedupe keys + A2 widening (defense in depth for the
   unvalidated-construction route), **and** the `UniversalRelation`
   loosening above so the real LLM path actually reaches the filter and
   keeps the chunk's other facts instead of failing the whole chunk.
   `ExtractedProcedure.name` was deliberately *not* loosened the same way:
   loosening it would have put `None` names into `persist_extraction` /
   `legacy_projection` `Procedure(name=…)` rows and `_legacy_model`'s
   `proc.name.lower()`; procedures still fail the chunk visibly at
   validation (existing behavior, covered by
   `test_procedure_without_type_still_fails_visibly`), while the
   `_universal` filter + `_legacy_model` guards still protect the
   unvalidated-construction route.
2. **No crash log or failing input was present in the repo** — the crash
   site/lines in the brief were taken as given and verified by code
   inspection + the reproduction probes above.

## Tests

New file: `tests/test_ingestion_gemini_fix.py` (14 tests at build time;
17 after the 21 Nov logging patch — see the patch section above).

- (i) Null-endpoint relations: single-chunk (mocked gateway) and batch
  paths drop the two bad relations, keep the good relation, the two
  entities, and the legacy component/procedure; the warning carries
  `relations=2`; direct `_universal_model_from_result` test asserts the
  warning carries `chunk_id=chunk-abc`; a whitespace-only endpoint is
  dropped the same way; a nameless behavior is dropped while the named
  behavior (and the legacy-procedure behavior) is kept.
- (ii) A stubbed `_universal_model_from_result` raising `AttributeError`
  surfaces as `ExtractionError` in both the single-chunk and batch paths
  (batch message names the chunk: "unusable universal fact for c1").
- (iii) Config: `GOOGLE_API_KEY`-only works, `GEMINI_API_KEY` wins when
  both are set, Gemini-only works, neither set gives `""`, default model
  is `gemini-2.5-flash-lite`, and a `GEMINI_MODEL` env override still works.
  Settings are built fresh with `_env_file=None` so a local `.env` cannot
  leak into the precedence checks.
- A3: `verify_machine_model` on a `model_construct`-built model with
  `relation_type=None` does not raise; patch 21 Nov adds the same
  no-raise coverage for an entity with `entity_type=None` and an
  entity with `name=None` (verifier's probe cases, folded in).
- Logging patch 21 Nov: `test_background_job_failure_logs_traceback`
  — a stubbed `ingest_pdf` raising leaves a logged traceback with the
  job id and the job failed with the same stored `error_message`.

Full suite: **426 passed, 0 failed, in 20m17s**
(`~/workspace/crater-phase1/.venv/bin/python -m pytest tests/ -q`,
run 21 Nov 2026 after all changes). Baseline stated in the brief: 412
passed; 412 + 14 new tests = 426, so no pre-existing test was lost and no
new failure appeared. The 21 Nov logging patch adds 3 more tests in the
same file (17 total), so the expected full-suite total is now 429; the
full suite was **not** re-run for that logging-only patch — the targeted
runs in the patch section above (17 + 21 passed, app smoke import OK)
are the verification for it. A targeted run of the directly affected files plus
the oracle suites also passed first (101 passed in 12m47s:
`test_ingestion_gemini_fix`, `test_batch_extraction_fix`,
`test_extraction_hardening`, `test_states_schema`,
`test_verification_and_retrieval`, `test_relation_endpoint_recovery`,
`test_llm_provider`, `test_phase3a_oracle`, `test_phase3b_archetypes`).
The oracle/archetype/compiler tests pass unchanged, so the proof-twin
figures are unregressed (oracle 20/35, 648 transitions, Demo 7 transitions
— Demo 7 is asserted in `tests/test_phase3b_archetypes.py:226`).

## Files changed

| File | Change |
|---|---|
| `backend/config.py` | B1 `AliasChoices` fallback for `gemini_api_key`; B2 default model `gemini-2.5-flash-lite` |
| `backend/knowledge/schema.py` | `UniversalRelation` endpoints/`relation_type` Optional |
| `backend/knowledge/component_extraction.py` | A1 filter + `_is_blank_name`, `chunk_id` threading, None-safe dedupe, `_legacy_model` guards, logger; A2 `(ValueError, AttributeError)` in both call sites |
| `backend/knowledge/verification.py` | A3 `(x or "")` guards on `entity_type`, `name`, `relation_type` |
| `backend/ingestion/pipeline.py` | Empty-retry call passes `chunk_id` |
| `.env.example` | Model id + `GOOGLE_API_KEY` fallback comment |
| `backend/api/routes/ingestion.py` | Patch 21 Nov: `import logging`, module logger, `logger.exception(...)` in the `_run_job_background` except block (lines 5, 21, 74–77). Logging only — no state, flow, or `error_message` change |
| `tests/test_ingestion_gemini_fix.py` | New: 14 regression tests; patch 21 Nov: +3 tests (background-job traceback logging, `verify_machine_model` with `entity_type=None` / `name=None`) — 17 total |
| `INGESTION_GEMINI_FIX_BUILD_NOTES.md` | This file |

## What was NOT done

- No changes to `~/workspace/crater-benchmarks/`, any `.db` file,
  `~/workspace/user/files/`, the benchmark spec, or any delivered zip.
- No new runtime dependencies.
- No changes to the oracle, the simulation engine, the compiler, or the
  tournament. No LLM calls.
- The failed ingestion was **not** re-run (needs Nishant's live run).
- The Gemini model id was **not** verified live against Google's model
  list (see B2 note above).

## Still needs Nishant

1. Run `llm-probe.py` on his laptop to confirm the Gemini fix (key
   fallback + `gemini-2.5-flash-lite` actually accepted by the API).
2. Re-run the failed ingestion and confirm the job completes with the
   bad chunk recorded per-chunk in the ledger (not a job kill).
3. Gate 0.
