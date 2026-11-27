# Benchmark oracle runner — build notes

Script: `scripts/run_benchmark_oracle.py`
Tests: `tests/test_benchmark_oracle_runner.py` (16 tests, all passing)

## What it is

The benchmark harness (`~/workspace/crater-benchmarks/harness.py`) scores
only the diagnosis level over HTTP and explicitly does NOT run O1/O2/O3
(`o1_o2_o3: "NOT_RUN_OVER_HTTP"`). This script is the other half: it runs
the Phase 3A oracle (`backend/simulation/oracle.py` — pure, no LLM, no
network) against the ACTIVE compiled twin stored in a crater DB, using a
benchmark oracle JSON as ground truth.

```
.venv/bin/python scripts/run_benchmark_oracle.py \
    --db ~/workspace/user/files/crater-10.db \
    --oracle ~/workspace/crater-benchmarks/evaluations/c002-lincoln.json
.venv/bin/python scripts/run_benchmark_oracle.py \
    --db <db> --oracle <oracle-json> [--revision-id <id>]
```

Exit 0 when a report was produced, even if O1 fails (a failing oracle is
a result, not a crash); exit 2 only when it cannot run (no twin, bad
oracle file, bad DB). No LLM calls, no network, no new dependencies.

## JSON shapes — verified by reading the files, not assumed

`c002-lincoln.json` (22 rows) and `c002b-miller.json` (63 rows). Top keys:
`version, created, case_id, product, source, scope_disclaimer, review,
join_rule, categories, tests, cases, rows, answer_key, denominator`.

- Denominators match the rows exactly (counted, not trusted):
  Lincoln `total 22 / twin-expressible 19 / settings 2 / technique 0 /
  cross-reference 0 / unmappable 1`; Miller `total 63 / 38 / 16 / 9 / 0 /
  0`. The runner validates this on every run; a mismatch is a bad
  oracle file (exit 2).
- Each row: `row_id, case_id, symptom, cause, category, fault,
  observables, tests, basis, flags, source_pages, source_table`. Rows
  carry NO `remedy` and NO `case_rank`. Every row's `observables` /
  `tests` sets match its case's sets (checked by the validator).
- Faults: `null`, or `{component, field, value, kind}` — kinds
  `parameter` (fields `input_voltage/state/groove_matches/size_matches/
  gas_flow/alignment/tension/brake_pressure/connector_seated`) and
  `fault_state` (fields `state/thermal_protection/airflow/fault`). In
  both shipped files every `twin_expressible` row has a complete fault
  and no other row carries one; the validator checks that.
- Each case: `symptom, table_page, observables, nominal` (dict
  port → value, used by O1), `required_chain` (empty in both files —
  the chain is defined at run prep against the twin's topology, so
  O5's chain check is vacuous until a run defines one), `tests`.
- `tests` dict maps `test_id -> {description, outcomes, source_page}`.
- Row category `unmappable` (Lincoln has 1) is NOT in `oracle.py`'s
  `CATEGORIES` (the OMNIPRO vocabulary used `unmapped`); `run_oracle`
  treats any non-`twin_expressible` category as `not_scored`, so the
  row lands in the denominator and is reported, never silently scored
  or dropped.

## Decisions

- **Remedy (CORRECTION to the task brief):** the brief said the
  answer key "does NOT cover every row — verify the join yourself".
  Verified: it DOES cover every row — 22/22 Lincoln, 63/63 Miller —
  and its `cause` text matches each row's cause exactly. So the remedy
  is `answer_key[row_id]["corrective_action"]` with `remedy_source`
  from the entry's `remedy_source`, joined on `row_id` per the file's
  own `join_rule`. Where a future file has no answer-key entry for a
  row, the remedy is `""` with source `"answer_key_missing"` and the
  gap is recorded as a problem — never guessed. This choice cannot
  change any score: verified in `oracle.py`, no oracle check reads
  the remedy (it is carried on `OracleRow` only for the report).
- **case_rank:** derived from row order within its case — the 1-based
  index of the row among the file's rows for that `case_id`, in file
  order. The JSON carries no rank, so file order is the only rank
  available; it is informational only (no oracle check reads it).
- **Rows without a fault** stay `fault=None` and are recorded as
  unmappable in the report, never guessed.
- **Observable binding:** generic, unique-match only. A port maps
  when exactly one twin component carries a state field with the
  port's base name (before `@`), or when the port names a twin signal
  exactly. Zero or several matches → unmapped and recorded. This is
  deliberately conservative: it cannot know that OMNIPRO's
  `wire_out@tip` is the Contact Tip's `material_out` field — that is
  reviewed twin knowledge, carried in the 3C runner's
  `omnipro_proof.observable_paths`. The testable core therefore
  accepts a twin-specific mapping as an override, and test (i) uses it
  to reproduce the known 3C numbers.
- **test_models:** NOT passed by default. No test model can be built
  generically from the oracle JSON's `tests` dict — it carries
  descriptions and allowed outcomes, not a mapping from twin state to
  an outcome, and writing one per test would be inventing behaviour.
  Tests therefore read as `unmodelled`, exactly as the 3A baseline
  did. A caller with reviewed per-test models for a specific twin can
  pass them through `run_benchmark_oracle(..., test_models=...)`; the
  CLI has no such option on purpose.
- **DB loading:** same query pattern as `_load_twin_for_session`
  (`DigitalTwin` by `revision_id` + status `active`, latest first,
  `DigitalTwinDefinition.model_validate`), opened read-only via the
  `sqlite:///...?mode=ro&uri=true` URI, never written. One deviation
  forced by reality: the query selects only the columns the runner
  needs (`load_only`). A full-ORM select fails on older DBs such as
  `crater-10.db` with `no such column: digital_twins.library_version`
  — that column postdates the DB. The query pattern is unchanged.
- **Report denominator:** `run_oracle` builds its denominator as a
  `Counter` over the rows, so zero-count categories are absent from
  the printed denominator (Lincoln prints no `technique` /
  `cross_reference` keys). The JSON file's own denominator, zeroes
  included, is what the validator checks.

## Measured results (21 Nov 2026, this VM)

- **New tests: 16, all passing** (`tests/test_benchmark_oracle_runner.py`,
  run standalone twice: 16 passed).
- **Full suite: 469 passed, 0 failed, 0 skipped** (2153.78s; the 395
  warnings are pre-existing `datetime.utcnow()` deprecation warnings).
  Collection at the end of the session is 469 tests, including the 16
  new ones.
- **Baseline caveat, stated plainly:** the brief's "baseline 433
  passed, 0 failed" could not be re-measured cleanly in this session.
  The first full-suite run (started before this build's files existed;
  446 tests collected) was killed by its own 1200s `timeout` at ~32%
  progress, and its truncated output showed a single failure in the
  first ~16% that was never identified (the log was cut by the kill)
  and does not reproduce — the completed full suite afterwards is 469
  passed / 0 failed. Between the early 446 collection and the final
  run, the parallel fix3/fix4 build changed test files in the same
  tree (`test_diagnostics.py`, `test_phase456_followup_walk.py`,
  `test_verify_before_conclusion.py`, `test_diagnosis_robustness.py`,
  new `test_pdf_loader_images.py`), which accounts for the 446 → 469
  difference together with this build's 16 tests. The 469-passed run
  is the definitive current state of the tree; no pre-change baseline
  count is claimed beyond the brief's 433 figure.

- **Proof twin + OMNIPRO vocabulary/registry (test i, via the core
  with the proof twin's own mapping + test models): headline 20/35,
  O1 pass, O3 57/58** — reproduces the known 3C numbers exactly.
  This is the "runner core reproduces known numbers" branch of test
  (i), not the "runs without crashing" fallback: the proof twin could
  be used directly.
- **CLI against the real crater-10 DB** (ACTIVE twin `Harbor Freight
  220 — Rev A Simulation`, id
  `dfbabc72-855c-4589-a0a6-9877f9eff797`, revision
  `0b2b98cd-a3a8-475f-912d-becbf1089daa`, 201 components, 0
  transitions, 4 topology edges):
  - Lincoln: **O2 0/19, O1 fail, O3 0/31**, exit 0.
  - Miller: **O2 0/38, O1 fail, O3 0/71**, exit 0.
  - This is the EXPECTED honest result, not a bug: the twin in the DB
    is the old OMNIPRO snapshot (201 components — duplicates, it
    predates the Phase 2 dedupe fix — and 0 transitions), scored
    against Lincoln/Miller rows whose fault components it does not
    have. The report names exactly what is missing: 10 unmapped
    observables and 11 unmapped fault components for Lincoln
    (several fault names resolve to 2–6 duplicate components each —
    ambiguous, never guessed).
- **CLI against the real Miller twin (21 Nov 2026, new DB
  `~/workspace/user/files/crater-96db6588.db`, created during the
  parallel live-check work): the Miller twin now EXISTS, so this IS a
  real Miller result — `miller 220 — Rev A Simulation`, id
  `a7a67556-495f-488e-8742-1040b7397229`, revision
  `30e0e856-62b9-4fd7-ac05-52ee1bf3e742`, 96 components, 1 transition,
  28 topology edges, run with `--revision-id` against
  `c002b-miller.json`: O2 0/38, O1 fail, O3 0/71, exit 0.** Gaps:
  `observable_unmapped` blocks 38 rows, `unmodelled_test` blocks 38,
  `no_transition_expresses_fault` blocks 27, `unmappable_cause`
  blocks 11. Only 5 fault components are unmapped (Building Circuit
  Breaker, Gas Hose, Input Power, Main Control Board, Trigger) — most
  Miller fault components DO resolve by unique name; the binding
  blocks are the 1 transition (27 rows have a resolved component but
  no transition expresses the fault) and the 10 unmapped observable
  ports. **No Lincoln twin result is claimed: no Lincoln twin exists
  yet** (its ingestion crashed; see below).
- **Synthetic twin + real Lincoln/Miller JSONs (tests ii/iii):** both
  load, validate, and report without crashing; denominators verified
  against the files.

## What the runner cannot do yet

- There is no Lincoln twin yet: Lincoln ingestion crashed on
  21 Nov 2026 on an image-format error (a pixmap the PNG saver cannot
  write); fix4 is building and Lincoln waits for it. Until the twin
  exists, every Lincoln run scores whatever twin IS in the DB against
  Lincoln rows and will honestly read near zero.
- No twin-specific observable mapping or test models exist for
  Lincoln/Miller. The real Miller twin (see above) still scores 0/38
  because no port is mapped and no test is modelled; writing the
  reviewed mapping (which port is which twin field, as
  `omnipro_proof.observable_paths` does for OMNIPRO) and the reviewed
  per-test models (as `omnipro_proof.build_test_models` does), plus
  the per-case `required_chain` against the twin's topology, is the
  work that moves the Miller number — until then O1 stays unmapped,
  tests stay unmodelled, and O5's chain check stays vacuous. The core
  accepts both as arguments; the CLI does not yet take them from a
  file (a `--mapping` option is the obvious next step once the first
  Lincoln/Miller mapping exists).
- The generic observable binding (see `build_observable_paths` in the
  script) can only map a port when exactly one twin component carries
  a state field with the port's base name. Port-qualified ports whose
  field name differs from the port name (OMNIPRO's `wire_out@tip` ->
  `Contact Tip.material_out`) need the twin-specific mapping; the core
  function accepts such a mapping as an override for exactly that
  reason (the test suite uses it to reproduce the known 20/35).
- The CLI prints the report to stdout only; it writes no JSON/Markdown
  report file (the 3A/3C runners do). Easy to add if Nishant wants
  the report kept.

## What Nishant does next

1. Drop `scripts/run_benchmark_oracle.py` and
   `tests/test_benchmark_oracle_runner.py` into his v12 checkout
   (they are written to run from a plain checkout: same `sys.path`
   pattern as the other scripts, no new dependencies).
2. Ingest Lincoln (after fix4 lands), compile + approve its twin,
   then run:
   ` .venv/bin/python scripts/run_benchmark_oracle.py --db <his db>
   --oracle ~/workspace/crater-benchmarks/evaluations/c002-lincoln.json`
   and the same for `c002b-miller.json`. With no `--revision-id` the
   runner uses the most recent ACTIVE twin and prints which one; use
   `--revision-id` to pin a specific twin when the DB holds several
   (the live DB `crater-96db6588.db` already holds the Miller twin
   and a fresh OMNIPRO twin — the Miller run above used
   `--revision-id 30e0e856-62b9-4fd7-ac05-52ee1bf3e742`).
3. Expect a low first number and read the unmapped lists: they are
   the work list (duplicate components to dedupe, missing fields,
   missing transitions, unmodelled tests). For Miller the first real
   number is already measured: **0/38** (see above) — the next work
   is the reviewed observable mapping + test models, not re-running.
4. Do NOT treat the 0/19 Lincoln number above as a Lincoln result —
   it is the old OMNIPRO snapshot scored against Lincoln rows. The
   0/38 Miller number IS a real Miller-twin result.

## Files not touched

`agent.py`, `routes/diagnostics.py`, `backtracking.py`, diagnosis
schemas (the parallel fix3 build), `~/workspace/crater-benchmarks/`,
any `.db` file, and the oracle JSONs. No delivered zip was overwritten
(none was present to overwrite); this build ships as source files only.
