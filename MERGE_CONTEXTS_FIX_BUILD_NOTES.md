# _merge_contexts None-strip crash — fix-2 build notes (21 Nov 2026)

Work tree: `~/workspace/crater-phase1/v12`. Venv: `~/workspace/crater-phase1/.venv`.
Package: `~/workspace/crater-phase1/project-review-phase6-readiness-ingestion-gemini-fix2.zip`
(new; the two earlier zips were not touched).
No LLM calls were made for this build. `backend/knowledge/verification.py`
(the fix-1 guards) was not touched.

## The traceback (verbatim from Nishant's run on the fix-1 package)

```
Traceback (most recent call last):
  File "C:\programming\Crater.ai\backend\api\routes\ingestion.py", line 67, in _run_job_background
    document = ingest_pdf(...)
  File "C:\programming\Crater.ai\backend\ingestion\pipeline.py", line 596, in ingest_pdf
    context, _, _ = build_effective_revision_knowledge_context(session, revision_id)
  File "C:\programming\Crater.ai\backend\knowledge\global_integration.py", line 512, in build_effective_revision_knowledge_context
    merged = _merge_contexts(contexts)
  File "C:\programming\Crater.ai\backend\knowledge\global_integration.py", line 501, in _merge_contexts
    key = key_fields[kind](row)
  File "C:\programming\Crater.ai\backend\knowledge\global_integration.py", line 483, in <lambda>
    "failure_modes": lambda x: (x.get("symptom", "").strip().lower(), x.get("diagnostic_test", "").strip().lower()),
```

This crash site is one the first fix missed: its A3 audit of
`global_integration.py` concluded "no unguarded None `.strip()` found"
because the calls use `x.get(..., "")` defaults. That reasoning is wrong
for exactly this trap, and this build corrects it.

## The `.get` trap

`dict.get(key, "")` returns the default `""` only when the key is
**missing**. When the dict carries the key **explicitly set to `None`**
(`"diagnostic_test": None`), `.get` returns `None` and `None.strip()`
raises `AttributeError: 'NoneType' object has no attribute 'strip'`.

`diagnostic_test` is legitimately Optional (`str | None = None` in
`backend/knowledge/machine_model.py:267` and
`backend/knowledge/schema.py:85`) — a failure mode with no documented
test is normal. The rows come from the database via
`build_revision_knowledge_context`, so an explicit `None` in a persisted
row reaches `_merge_contexts` even though the extraction-path filters
(fix 1, A1) guard the LLM route. The crash runs before the 93% progress
report (`pipeline.py:596` is before `progress(93)`), so the earlier 95%
failure was a different run — LLM extraction varies run to run.

Reproduced before patching: the old lambda expression on
`{"symptom": "Motor hums", "diagnostic_test": None}` raises
`AttributeError: 'NoneType' object has no attribute 'strip'`.

## What changed

`backend/knowledge/global_integration.py`, `_merge_contexts`
(lines 476–486): every `.strip()` in the key lambdas changed from
`x.get("...", "").strip()` to `(x.get("...") or "").strip()`. That is
11 `.strip()` call sites across 8 lambdas:

| Lambda (line) | Fields fixed |
|---|---|
| `universal_entities` (476) | `name` |
| `universal_relations` (477) | `subject_name`, `object_name` |
| `behaviors` (479) | `subject_name`, `description` |
| `legacy_components` (480) | `name` |
| `procedures` (482) | `name` |
| `failure_modes` (483) | `symptom`, `diagnostic_test` |
| `schematic_nodes` (484) | `label` |
| `expert_knowledge` (486) | `title` |

The 3 lambdas with no `.strip()` (`universal_facts`, `legacy_relationships`,
`schematic_edges`) had no trap and are unchanged. The plain
`.get("entity_type", "")` / `.get("relation_type", "")` / etc. second
tuple elements have no `.strip()` and cannot raise; they are unchanged
(a `None` there is a legal tuple-key component).

Behavior change is None-safety only: a `None` field becomes an empty-string
key; rows are kept, never dropped, never rejected. Child-still-shadows-parent
on identical keys is unchanged (covered by a test).

## Whole-backend trap sweep (all sites, with disposition)

Searched the whole `backend/` tree for the exact pattern
`.get(k, "")` / `.get(k, '')` followed by `.strip()` (same-line and
multi-line regex), plus the variants `.get(k).strip()/.lower()` with no
default, `.get(k, "").lower()`, and `x[k].strip()`.

- **FIXED:** `backend/knowledge/global_integration.py:476–486` — the 11
  sites above. This was the **only** occurrence of the exact trap
  pattern anywhere in `backend/`.
- **Confirmed safe, re-checked (not assumed):**
  - `global_integration.py:219` (`_parse_json`) — `if not raw: return {}`
    guards before `raw.strip()`; `None`/empty cannot reach `.strip()`.
  - `global_integration.py:254` (`_ev`) — `if not isinstance(data.get("fact"), str)
    or not data["fact"].strip()`; the `isinstance` guard runs first.
  - `global_integration.py:613` (`_norm_text`) — `str(value or "")`;
    `None` becomes `""` before any string method.
- **Confirmed safe — variant sweeps:**
  - `.get(k).strip()/.lower()` with no default: 0 occurrences in `backend/`.
  - `.get(k, "").lower()` with no `.strip()`: 0 occurrences.
  - `x[k].strip()` (direct subscript): 2 occurrences, both
    `isinstance`-guarded — `component_extraction.py:300`
    (`isinstance(item.get("parameter"), str) and item["parameter"].strip()`)
    and `global_integration.py:254` (above).
  - `.get(...)` with a computed key (not the trap — the `.get` is the
    outer call, the `.strip()` is on the key expression):
    `component_extraction.py:844` (`str(ref).strip()`, `str()` never
    returns `None`) and `diagnostics.py:297` (`str(name).strip()`).
    Both safe.
- **Confirmed safe — other `.strip()` sites on the ingestion /
  integration / diagnosis path** (all already None-safe via
  `(x or "")`, `str(...)`, truthiness guards, or `if ... is not None`
  checks): `pending_links.py:36`, `pipeline.py:259`,
  `pdf_loader.py:149,198`, `ocr.py:31`, `chunking.py` (throughout,
  operates on `str` text), `source_router.py:62,66,100,145,184`,
  `table_parser.py` (throughout, `(text or "")` / guarded),
  `mode_b.py:249,268,271`, `component_extraction.py:539,543,701,725,
  749,840,869–897` (fix-1 `(x or "")` guards),
  `repair_cases.py:120,149`, `identity.py:73`, `provenance.py:44,64,70`,
  `library_types.py:52`, `verification.py:58,68–70,78,90` (fix-1
  guards, untouched), `validation.py:102,105,250,268,302,304,355,361,
  371,375,394,396,400` (`(x or "")` / `str(...)` / `if e.name` guards).
  Two eyeballed individually:
  - `diagnostics.py:888` — `payload.technician_name.strip()`; the
    field is a pydantic `str` with default `"Diagnostic technician"`
    (`diagnostics.py:864`), so `None` cannot reach it.
  - `diagnostics.py:982` — `mode.diagnostic_test.strip().casefold()`,
    immediately preceded by `if not mode.diagnostic_test: continue`,
    so `None`/empty is skipped first.

No other file was changed.

## Tests

`tests/test_ingestion_gemini_fix.py`, 17 → 21 tests (+4, appended as the
FIX2 section):

- (i) `test_merge_contexts_failure_mode_with_none_diagnostic_test_kept`
  — the exact traceback row (`diagnostic_test=None`) merges without
  raising, row kept. `test_merge_contexts_failure_mode_with_none_symptom_kept`
  — a `symptom=None` row and a `diagnostic_test=None` row in one context
  both kept (distinct empty-string keys).
- (ii) `test_merge_contexts_none_in_other_key_fields_kept` — the same
  None-in-field case for every other `.strip()` lambda kind:
  `universal_entities` `name=None`, `universal_relations`
  `subject_name=None` / `object_name=None`, `behaviors`
  `subject_name=None` (and `description=None`), `procedures` `name=None`,
  plus `legacy_components` `name=None`, `schematic_nodes` `label=None`,
  `expert_knowledge` `title=None`. All rows kept.
- `test_merge_contexts_none_fields_still_shadow_on_same_key` — behavior
  beyond None-safety unchanged: a child row with the same key (including
  the empty-string key from a `None` field) still shadows the parent row.

Results:

- `tests/test_ingestion_gemini_fix.py`: **21 passed** (17 before + 4 new).
- Full suite: **433 passed, 0 failed, in 15m38s**
  (`~/workspace/crater-phase1/.venv/bin/python -m pytest tests/ -q`,
  run 21 Nov 2026). Baseline 429 passed; 429 + 4 = 433, so no
  pre-existing test was lost and no new failure appeared.
- Proof-twin numbers (measured 21 Nov 2026, unchanged): oracle **20/35**
  (57.1%, O1 gate pass, O3 57/58), **648 transitions**, Demo **7 transitions** —
  measured by running the proof-twin half of `scripts/run_phase3c_oracle.py`
  in-process (oracle rows from the OMNIPRO manual + vocabulary + registry;
  the crater-10 DB half was deliberately NOT run — see "What was NOT done").
  Targeted oracle run: `test_phase3a_oracle`, `test_phase3b_archetypes`,
  `test_phase3c_oracle_fix`, `test_machine_model_simulation_compiler`:
  **53 passed**.

## Files changed

| File | Change |
|---|---|
| `backend/knowledge/global_integration.py` | `_merge_contexts` key lambdas: 11 `.strip()` sites `x.get(k, "").strip()` → `(x.get(k) or "").strip()` (lines 476–486) |
| `tests/test_ingestion_gemini_fix.py` | +4 FIX2 tests (17 → 21) |
| `MERGE_CONTEXTS_FIX_BUILD_NOTES.md` | This file |

## What was NOT done

- No changes to `~/workspace/crater-benchmarks/`, any `.db` file,
  `~/workspace/user/files/`, the benchmark spec, or any delivered zip.
- No changes to `verification.py` (fix-1 guards stay in place), the
  oracle, the simulation engine, the compiler, or the tournament.
- No new runtime dependencies. No LLM calls.
- The failed ingestion was **not** re-run (needs Nishant's live run).
- The crater-10 half of the 3C oracle runner was **not** re-run: its
  default DB lives under `~/workspace/user/files/`, which this build
  was told not to touch. The proof-twin half (the numbers this fix
  could regress) was measured directly instead.

## Still needs Nishant

1. Re-run the failed ingestion on the fix-2 package and confirm the job
   completes past `build_effective_revision_knowledge_context` (the
   crash was before the 93% progress report).
2. Gate 0.
