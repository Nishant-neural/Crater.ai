# Phase 3 — twin-verifier extraction build notes

**Date:** 23 Nov 2026. **Working copy only:** `~/workspace/crater-refactor/`.
**Governing:** `contracts/README.md` + `contracts/openapi-twin-verifier.yaml`
(Phase 2), the twin-verifier audit, the amendment (four resolutions),
`PHASE3_INGESTION_BUILD_NOTES.md` and `PHASE3_DIAGNOSIS_BUILD_NOTES.md`
(the diagnosis step deliberately left the twin-verifier work for this
step — the `LocalVerifyBackend` fallback, `_load_twin_for_session`,
and the in-memory verify job store — and listed them exactly; all of
them are migrated here).
**Scope discipline (Nishant, 23 Nov 2026):** containerize the EXISTING
application per the audits; change code ONLY where the audit says to;
no rewriting modules from scratch, no regenerating files that already
exist. This step is the Phase 3 extraction order's step 4 (after
gateway, ingestion, diagnosis-agent). The oracle gate after this
extraction is READY AND WAITING for Nishant's other LLM (standing
rule 23 Nov 15:49 IST — no Muse-as-backend run here).

## What changed (file paths, edits not rewrites)

### 1. Twin-verifier service app + container (`backend/simulation/app.py` — new, `backend/simulation/readiness.py` — new, `docker/twin-verifier/Dockerfile` — new, `docker-compose.yml`, `.env.example`)

- `backend/simulation/app.py` (new, minimal, mirrors the ingestion /
  diagnosis apps): mounts `digital_twin` + `simulation` routers under
  `/v1/` with the legacy bare alias, the `verify` router once;
  `/health` liveness; `/ready` via readiness; the standard
  error-envelope handlers.
- `backend/simulation/readiness.py` (new): checks postgres (SELECT 1),
  twin store (≥1 **active** twin — zero approved twins is a readiness
  failure, never a silent fallback; `session_factory` injectable for
  tests), library (imports archetypes, version must be `3c-1.0.0`,
  plus the `CURRENT_LIBRARY_VERSION` pin), redis (PING db 0 + db 1),
  celery (control.ping). `/ready` 200 only when every check passes,
  else 503 naming the failed checks.
- `docker/twin-verifier/Dockerfile` (new, mirrors the diagnosis
  Dockerfile): python:3.12-slim, curl only, no tesseract, no migration
  step, CMD `uvicorn backend.simulation.app:app`.
- `docker-compose.yml`: new `twin-verifier` service (internal-only,
  DATABASE_URL/REDIS_URL/CELERY_*, depends redis+postgres) and
  `twin-verifier-worker` (same image, `celery -A
  backend.simulation.celery_app:celery_app worker`, depends
  redis+postgres+twin-verifier). `TWIN_VERIFIER_BASE_URL:
  http://twin-verifier:8000` added to **both** `gateway` and
  `diagnosis-agent` (Traefik routes everything to `gateway`; the
  services are internal-only). YAML parses; 10 services.
- `.env.example`: `TWIN_VERIFIER_BASE_URL` comment updated (compose
  `http://twin-verifier:8000`, local default `http://localhost:8000`,
  empty = unconfigured → NOT_VERIFIED degradation).

### 2. Async verify contract on the existing verification code (`backend/api/routes/verify.py` — rewritten small file, `backend/simulation/verify_service.py`, `backend/simulation/tasks.py` — new, `backend/simulation/celery_app.py` — new, `backend/db/models.py`, `alembic/versions/7c2e1a94b5d0_add_verify_jobs.py` — new)

- `backend/db/models.py`: `VerifyJob` model (`verify_jobs` table: id
  PK uuid, twin_id/revision_id/session_id nullable, idempotency_key
  indexed, status String default `"queued"`, request JSON, result
  JSON, counts JSON, error_message Text, created/updated).
- `alembic/versions/7c2e1a94b5d0_add_verify_jobs.py` (new,
  hand-written; down_revision `3f515989a9f9`): upgrade creates the
  table, downgrade drops it. Verified round-trip on SQLite (see §Test
  results): upgrade → `verify_jobs` present, downgrade base → gone,
  re-upgrade → present.
- `backend/api/routes/verify.py` (rewritten, was the in-memory
  `_JOBS` dict): Postgres `VerifyJob` row + Celery dispatch. POST 202:
  422 empty hypotheses; idempotency lookup (same key+payload → reuse,
  no dispatch; same key+different payload → 409); `_queue()` →
  `run_verify_job.delay`, broker outage → 503 naming Redis/Celery;
  202 body `{job_id, status, twin_id, estimated_ms}` where
  estimated_ms = job cap ms. GET reads the row, 404 unknown;
  queued/running → empty results + zero counts. **This closes the
  Phase 1 open item "verify job store in-memory".**
- `backend/simulation/celery_app.py` (new, mirrors the ingestion
  celery app): broker Redis db 0, result db 1, queue `twin-verifier`,
  include `backend.simulation.tasks`.
- `backend/simulation/tasks.py` (new): `run_verify_job` Celery task
  (autoretry 3x, backoff, max_retries 3); `execute_job(session,
  job_id, session_factory=None)`; `run_verify_with_timeout(payload,
  cap_seconds, session=None, session_factory=None)`
  (ThreadPoolExecutor, `future.result(timeout=cap)`); `job_cap_seconds`
  = min(10s, timeout_ms × n_hypotheses) — the 2s/hypothesis, 10s/job
  contract; `timeout_result` (succeeded, all NOT_VERIFIED, reason
  exactly `"twin verification timed out"`). Session ownership rule: a
  passed-in `session` is used as-is, never closed; otherwise a session
  is created from factory/SessionLocal and closed in the worker thread.
- `backend/simulation/verify_service.py` (edited): deleted
  `LocalVerifyBackend`, `load_twin_for_session`, `no_twin_reason`,
  `run_tournament_for_state` (no remaining callers; diagnosis reaches
  the twin only over HTTP now) and the unused backtracking import.
  `_base_row` now derives `source_ids` + `evidence_pages` from the
  `rules_used` rule-summary dicts (plain-string entries skipped);
  no-twin branch rows also carry empty `source_ids`/`evidence_pages`.
  **Contract gap found and closed while testing:** a VALIDATED
  tournament row carried an empty reason (the verified-repair success
  path never sets one), violating "reason never empty" — `map_verdict_row`
  now supplies the contract's plain sentence for VALIDATED / REJECTED
  rows without a reason (and a fallback sentence for
  CANNOT_EXPRESS / NOT_VERIFIED). Docstring updated ("twin-verifier
  service's execution", worker-run).

### 3. Diagnosis-agent's temporary fallback removed (`backend/diagnostics/twin_verification.py`, `backend/diagnostics/agent.py`, `backend/config.py`, `backend/api/routes/diagnostics.py`)

- `backend/diagnostics/twin_verification.py` (edited): removed the
  `backend=` constructor param entirely (test-only fake backends must
  now use `transport=MockTransport`); `_translate_job` and
  `_not_verified_rows` pass through `source_ids`/`evidence_pages`;
  error when unconfigured is now `"no twin verifier configured (no
  base_url)"`. The client itself is NOT thinned (diagnosis notes §4:
  the worker verified thinning to ~35 lines would delete code the loop
  needs).
- `backend/diagnostics/agent.py` `run_turn` (edited): removed the
  LocalVerifyBackend branch; always
  `TwinVerifyClient(base_url=settings.twin_verifier_base_url)`.
- `backend/config.py` (edited): `twin_verifier_base_url` default
  changed from `""` to `"http://localhost:8000"` (matching the
  ingestion/diagnosis defaults); comment updated (empty = no verifier
  configured → client degrades to NOT_VERIFIED).
- `backend/api/routes/diagnostics.py` (edited): removed the
  `backend.simulation.verify_service` import; added `settings`,
  `TwinVerifyClient`, `rerank_with_tournament`, `import httpx`.
  Deleted the `_load_twin_for_session` wrapper.
  `_apply_early_tournament(db, session, state, twin_client=None)`:
  else-branch now calls `client.verify_sync([h.model_dump()...],
  revision_id, session_id, repair_name)` then re-ranks in place via
  diagnosis-owned `rerank_with_tournament` (VALIDATED +0.1 / REJECTED
  ×0.5, same as before); unavailable branch builds the same `reranked`
  shape with `reranked: False`. New `_fetch_twin_definition(revision_id)`
  (GET `{base}/v1/simulation/revisions/{id}/twin`, 2s timeout, None on
  any failure, never raises); `_walk_bindings` now uses it for
  twin-metadata `archetype_bindings` (dict access) and ALWAYS fetches
  canonical-model `library_type` via ingestion HTTP (was elif-only).
  `ARCHETYPES` + `load_edge_list` imports KEPT for the walk's alias
  mapping / reviewed edge list — audit P-07 remainder, see §7.

### 4. Vacuous-validation trap closed (`backend/simulation/agent.py`, `backend/api/routes/simulation.py`)

- `backend/simulation/agent.py` (edited): `run_experiment` raises
  ValueError on empty assertions; `passed = all(...)` (the vacuous
  `final_state != fault_state` fallback at the old line 98 deleted).
  `verification.py:263` already guarantees ≥1 assertion for every
  production `run_experiment` call, so the guard is safe; no test uses
  empty assertions.
- `backend/api/routes/simulation.py` (edited): experiment +
  hypotheses endpoints raise 400 `"Experiment assertions must not be
  empty"` (hypotheses: any experiment with empty assertions → 400).
  Visual endpoint (old lines 105–114) + `layout_twin` import removed
  (see §5); docstring notes the visual move.

### 5. visual.py moves to the gateway shim (`backend/api/visual.py` — moved, `backend/api/main.py`, `tests/test_visual_twin.py`)

- `backend/simulation/visual.py` → `backend/api/visual.py` (`mv`;
  docstring notes the move; `layout_twin` unchanged). The twin
  service must not own presentation (contracts §5.5).
- `backend/api/main.py` (edited): added `GET
  /simulation/twins/{twin_id}/visual` + `GET
  /v1/simulation/twins/{twin_id}/visual` (loads DigitalTwin from DB,
  returns layout via `backend.api.visual.layout_twin`); imports added
  (`layout_twin`, `DigitalTwin`, `get_session`,
  `DigitalTwinDefinition`, `Session`, `Depends`). Frontend move is
  for the frontend step: the frontend still calls the same unversioned
  path via `getTwinVisual` (`frontend/src/api/client.js:35`), so the
  gateway shim keeps it working unchanged.
- `tests/test_visual_twin.py` (edited): imports `layout_twin` from
  `backend.api.visual`; the endpoint test now imports `twin_visual`
  from `backend.api.main`.

### 6. Oracle relocation to harness-scorer (`benchmark/oracle.py` — moved, `benchmark/run_*.py` — moved, test imports)

- `backend/simulation/oracle.py` → `benchmark/oracle.py` (`mv`;
  header notes the harness-scorer move; still imports
  `backend.simulation.engine/schema/oracle_shared` — eval→twin
  coupling, pre-existing pattern, the harness already did this).
- `scripts/run_benchmark_oracle.py`, `run_phase3a_baseline.py`,
  `run_phase3b_oracle.py`, `run_phase3c_oracle.py`,
  `run_phase4_offtable.py` → `benchmark/` (`mv`); imports changed to
  `from benchmark.oracle import ...`; self-path references updated
  (`scripts/...` → `benchmark/...`). The moved scripts insert both the
  repo root and their own dir into sys.path, so their sibling
  top-level imports (`from run_phase3a_baseline import ...`) still work
  when run as scripts.
- `benchmark/harness.py`: comment references `backend/simulation/oracle.py`
  → `benchmark/oracle.py`.
- Tests updated for the move: `tests/test_phase3a_oracle.py`,
  `tests/test_phase3b_archetypes.py`,
  `tests/test_benchmark_oracle_runner.py` (oracle imports → `from
  benchmark.oracle import ...`; runner test imports → `from benchmark
  import run_benchmark_oracle as runner` / `from
  benchmark.run_phase3a_baseline import _load_oracle_set`, scripts
  sys.path insert removed); `tests/test_step5_cuts.py` (now `from
  benchmark import oracle` + `from backend.simulation import
  repair_actions`); `tests/test_phase3a_oracle.py::test_harness_makes_no_llm_calls`
  now reads `benchmark/oracle.py` (it was still reading the old path —
  a regression from the move, caught by the twin suite run and fixed
  here).
- No production import from harness-scorer remains: verified by grep
  — the only `benchmark.oracle` references in `backend/` are none
  (the service test asserts no `backend/**/*.py` contains
  `benchmark.oracle`); `benchmark/oracle.py` itself imports
  `oracle_shared` from the twin, which is the pre-step direction
  (harness reads twin helpers, twin never reads harness).
- `omnipro_proof.py` NOT moved (task item 3 names only oracle.py +
  five scripts; the audit also wanted it moved — see §7).
- `drafting.py` (960) and `review_view.py` (280) CUT per the amendment:
  source files already absent; only stale `__pycache__/*.pyc` remained
  — **deleted the two .pyc files**. No live imports anywhere.
  `field_promotion.py` (200) + `library_versions.py` (103)
  DEFER-retained: kept, still imported.

### 7. What was deliberately NOT done here (deferred, with owner)

- Compile route (`routes/simulation.py`) and `digital_twin` service
  `create_twin` still read Product/Revision/Snapshot via shared
  models (ingestion-owned tables) — schema-split follow-up, same
  precedent as the ingestion and diagnosis steps.
- Upstream walk still reads the frozen archetype library (`ARCHETYPES`)
  + reviewed edge list (`load_edge_list`) in-process (audit P-07
  remainder); no twin-service archetype/edge endpoint exists in the
  contracts — a contract addition for a later step.
- Outcome-flow twin read in `routes/diagnostics.py` (~line 970,
  DEFER-retained Phase 5 learning flow, `DigitalTwin` /
  `DigitalTwinDefinition` imports) deliberately left.
- `omnipro_proof.py` NOT moved to harness-scorer (task named only
  oracle.py + 5 scripts; audit also wanted it moved).
- `db/twin_models.py`, `knowledge/library_types.py` NOT deleted
  (audit says DELETE/dead; left for parent confirmation — same
  precedent as the ingestion step leaving `legacy_projection.py`).
- Contract-shape deltas NOT renamed (ingestion-step precedent):
  monolith serves `/simulation/revisions/{id}/compile`,
  `/simulation/twins/{id}/experiment` (singular), etc., while
  `openapi-twin-verifier.yaml` lists `/twins/{id}/compile`
  (snapshot_id body), `/twins/{id}/experiments` (plural) — versioning
  follow-up.
- `run_tournament_for_state` / `load_twin_for_session` deleted from
  verify_service.py (their re-rank behavior now lives diagnosis-side
  via `TwinVerifyClient` + `rerank_with_tournament`).
- Demo route already gone from `routes/digital_twin.py`;
  `demo_definition` / `create_demo_twin` still in engine/service —
  left alone.

## Non-LLM test results

### Twin suite (run after the phase3a fix)

`pytest tests/test_twin_verifier_service.py tests/test_twin_client.py
tests/test_visual_twin.py tests/test_verification_and_retrieval.py
tests/test_phase7_simulation_agent.py tests/test_phase6_digital_twin.py
tests/test_hypothesis_tournament.py tests/test_verify_before_conclusion.py
tests/test_phase4_backtracking.py tests/test_phase5_learning.py
tests/test_step5_cuts.py tests/test_phase3a_oracle.py
tests/test_phase3b_archetypes.py tests/test_benchmark_oracle_runner.py -q`
— **172 passed, 1 skipped, 7 failed** in 10:37 (the 7 failures are all
in `test_benchmark_oracle_runner.py` and are the known environmental
failures: the Lincoln/Miller oracle JSONs live outside this repo at
`/home/hatch/crater-benchmarks/evaluations/` and do not exist in this
VM — e.g. `BenchmarkOracleError: oracle file not found:
.../c002-lincoln.json`. Same 7 as the diagnosis baseline, unchanged).
An 8th failure in that run (`test_phase3a_oracle.py::test_harness_makes_no_llm_calls`,
still reading the old `backend/simulation/oracle.py` path) was a real
regression from the oracle move, fixed here; `test_phase3a_oracle.py`
alone then passes 22/22.

New suite `tests/test_twin_verifier_service.py` (30 tests, written in
this step): /health 200; /ready 503 naming failed checks and 200 with
mocked checks; `check_twin_store` unit (False empty, False
draft-only, True with 1 active); library frozen 3c-1.0.0; POST 202
queued-before-worker + Postgres row readable via a fresh session +
poll shows queued/empty results/zero counts; no `_JOBS` attr on verify
routes; empty hypotheses 422; unknown job 404; idempotency reuse +
409 conflict; broker outage 503 naming Redis/Celery; poll → VALIDATED
(rules_used contains `seed:feed-tension-too-tight`, trace non-empty,
`evidence_pages == [42]`, library 3c-1.0.0, reason non-empty, counts
validated 1); no twin → succeeded NOT_VERIFIED ("no approved twin");
CANNOT_EXPRESS for `"Zqx unknown cause"` (reason contains "Could not
match the cause" + "cannot express this failure"); timeout
(monkeypatched slow `execute_verify_job` sleeping 0.5s,
`timeout_ms=100`) → succeeded NOT_VERIFIED reason exactly "twin
verification timed out"; compile draft → approve active →
revision-twin read; experiment empty assertions 400, valid experiment
validates (fault tension_ok False → feeding False; intervention True →
feeding True); hypotheses empty assertions 400; `run_experiment`
ValueError on empty assertions; twin app has NO visual route (404
both paths); gateway shim still serves visual (200, 2 nodes, "Feed
Tensioner" first, /v1 alias 200); served OpenAPI surface (expected
paths incl. /v1/verify, simulation lifecycle, digital-twins, /health,
/ready; no visual/oracle paths); contract YAML checks (verify paths,
verdict enum, library enum 3c-1.0.0); oracle moved
(benchmark/oracle.py exists, backend copy gone, 5 scripts moved, no
`backend/**/*.py` contains `benchmark.oracle`); drafting/review_view
CUT verified gone + field_promotion/library_versions kept; Celery task
config (max_retries 3, backoff, autoretry); `job_cap_seconds` math
(1 hyp → 2.0s; 3×500ms → 1.5s; 10 hyp → 10.0s cap; 20 hyp → 10.0s);
task ignores unknown job; task marks failed job on worker error.

First run of the new suite found 3 failures, all fixed in this step
(not built over): (1) `backend/api/main.py` was missing the `Depends`
import for the visual shim; (2) a VALIDATED row carried an empty reason
— the contract gap closed in `map_verdict_row` (§2); (3) the two
Celery-task tests ran against the global engine without `init_db()`
(the `verify_jobs` table did not exist there).

Existing tests updated for the HTTP-only twin (all edits, no
rewrites): `tests/test_twin_client.py` (LocalVerifyBackend tests →
real twin app over HTTP via the test kit; endpoint tests now drive the
twin app with inline dispatch); `tests/test_phase4_backtracking.py`
(early-tournament test passes `twin_client=make_twin_client(...)`);
`tests/test_verify_before_conclusion.py` (db fixture → StaticPool for
the twin app's worker thread; autouse fixture points `run_turn`'s
client at the test-kit twin app; early-tournament test passes the
client explicitly); `tests/test_phase5_learning.py`
(`_drive_turn_to_tournament` monkeypatches `TwinVerifyClient` in both
`backend.diagnostics.agent` and `backend.api.routes.diagnostics` to
the test-kit factory); `tests/test_diagnosis_service.py` (comment
updated); `tests/twin_testkit.py` (new test-only helper:
`make_twin_client(db, monkeypatch)` — TwinVerifyClient over
`httpx.MockTransport` delegating to the real twin app TestClient,
Celery dispatch stubbed to run `execute_job` inline with a fresh
session on the same engine; `stub_inline_dispatch` shared by the
client tests).

### Alembic

`alembic upgrade head` → `verify_jobs` present; `downgrade base` →
gone; `upgrade head` → present again (SQLite, throwaway
`/tmp/alembic-twin-check.db`).

### Full suite

`pytest tests/ -q` — **595 passed, 14 failed, 2 skipped, 5 errors**
in 14:13. Reconciliation against the diagnosis baseline (564 passed /
15 failed / 2 skipped / 5 errors; projected 565/14 after their
one-line fix): 595 − 565 = **+30, exactly the 30 new tests in
`tests/test_twin_verifier_service.py`, all passing**; failures stayed
at 14 with the identical known-environmental set, zero new failures:
7× `test_benchmark_oracle_runner.py` (oracle JSONs outside the repo),
4× `test_phase3c_m800v_sample.py` + 2× `test_phase3c_oracle_fix.py`
(user-side files under `/home/hatch/goals/...`), 1 failed + 5 errors
in `test_reranker.py` (missing `sentence_transformers` in this VM).

### Docker

Images cannot be built in this VM (no Docker installed) — Dockerfile
and compose were written by mirroring the diagnosis-agent files and
`docker-compose.yml` parses (10 services). Build verification is for
the machine with Docker.

## Post-extraction oracle gate — NOT RUN, ready and waiting

Frozen Phase 1 gate (Miller 22/38 tie accepted, OMNIPRO 24/35,
discriminating 17/17 Miller / 8/9 OMNIPRO, citations 79/79 Miller /
35/35 OMNIPRO) — for Nishant's other LLM, per the standing rule
(23 Nov 15:49 IST: tell Nishant before any Muse-as-backend test).
Kill tests to include: twin killed → multi-turn harness still
completes, verdicts NOT_VERIFIED; twin slowed >2s → no turn waits;
/ready red.
