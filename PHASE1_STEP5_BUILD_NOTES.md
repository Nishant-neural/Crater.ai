# Phase 1 — Step 5 Build Notes: Diagnosis — LangGraph loop, safety in run_turn, twin as thin async client, CUTs

Date: 23 Nov 2026. Working copy only: `~/workspace/crater-refactor/`.
Governing docs: diagnosis-agent audit (Step 4 LangGraph loop, Recheck pp.2–3),
twin-verifier audit (thin async client; oracle relocation pre-step), amendment
four-resolutions, frontend/gateway/infra audits (CUTs' route/UI sweep).
Phase 0 frozen (`BASELINE.md`); Steps 1–4 complete and gated.

## What changed

### 1. LangGraph loop with checkpoints (`backend/diagnostics/`)

- **`graph.py` (new, 469 lines)** — `TurnGraphState` TypedDict (all JSON-able;
  DB session / ORM row / client / signatures travel in `config["configurable"]`,
  never in checkpointed state). Nodes in order: safety → retrieve → verify_pre
  → rank → discriminating → ask → verify_post → confidence → gate → safeguards
  → persist. `verify_post` runs the twin client once on the new ranking and
  re-ranks once via `rerank_with_tournament`, appending a `"tournament"`
  timeline entry; degraded turns reuse the pre-turn summary. `persist_node`
  syncs the result, updates the explicit asked sets (union with
  timeline-derived), adds ending/turn timeline entries, and prepends the
  lockout line when safety flagged. `build_diagnosis_graph(checkpointer)`,
  `get_diagnosis_graph(checkpointer)` (process cache).
- **`checkpoints.py` (new, 73 lines)** — `open_checkpointer(database_url)`:
  `PostgresSaver` for `postgresql://` URLs, else file-backed `SqliteSaver`
  next to the app DB (`sqlite:///./crater.db` → `./crater_checkpoints.db`);
  `default_checkpointer()` process-cached.
- **`agent.py` (1,584 → 1,587 lines)** — Step 5 docstring paragraph; imports
  now `TwinVerifyClient` (removed `run_tournament_for_state`,
  `render_verification_block`); `hybrid_retrieve` import restored (tests stub
  `diag_agent.hybrid_retrieve`; the graph node calls it via the agent module).
  `_asked_causes_and_tests` seeds from the explicit sets. `run_turn` replaced
  with one graph invocation (local import of `get_diagnosis_graph` to avoid a
  circular import), `thread_id=f"diagnostic-session:{session.id}"`, default
  twin client `TwinVerifyClient(backend=LocalVerifyBackend(db))`,
  graph/checkpointer/twin_client injectable for tests. `_run_llm_turn` kept
  untouched (Step 4 tests call it directly). `_normalize_ask` changed to
  `.model_dump(mode="json")` — fixes a `NextStepType` enum leaking into
  checkpoints (msgpack serde warning).
- **`schema.py` (105 → 115 lines)** — `asked_causes: list[str]` and
  `asked_tests: list[str]` added to `DiagnosticState` (deferred from Step 4).

### 2. Safety moved into `run_turn`

- The safety call lived at `backend/api/routes/diagnostics.py:807` inside the
  respond handler, so turn 1 was never safety-checked (Recheck bug). It now
  runs as the graph's first node on every turn including turn 1; the
  respond-handler safety block is removed.
- **`safety.py` (152 lines) stays verbatim** per the audit.

### 3. Twin verification as thin async client

- **`twin_verification.py` (164 → 293 lines)** — the in-process module is now
  the thin client. `TwinVerifyClient` (constructed with `backend` or
  `base_url`; `async verify()` + `verify_sync()` via `asyncio.run`, or a
  helper thread with its own loop when one is already running); `_translate_job`
  re-checks the rules_used rule and maps contract verdicts to the loop's
  lowercase `status`; never raises — transport error/timeout/no transport →
  `available: False` with one NOT_VERIFIED row per hypothesis. Pure helpers
  kept (no simulation import): `verdict_for_cause`, `cannot_express_cause`,
  `conclusion_cause`, `render_verification_block`. Note on size: the task's
  "~35 lines" was not met literally — the file is 293 lines because the
  four kept pure helpers (~90 lines), the HTTP 202+poll loop, the sync
  wrapper, the defence-in-depth demotion and the docstrings are in the same
  module; the client itself (`TwinVerifyClient` + `_translate_job` +
  `_not_verified_rows`) is ~190 lines. The in-process tournament execution
  it replaced is gone from this module entirely.
- **`backend/simulation/verify_service.py` (new, 253 lines)** —
  `load_twin_for_session`, `no_twin_reason`, `run_tournament_for_state` moved
  verbatim from old `twin_verification.py`; `map_verdict_row` (VALIDATED
  demoted to NOT_VERIFIED when `rules_used` empty); `execute_verify_job(db,
  payload)` returning `{job_id, status: "succeeded", twin_id, results,
  counts}`; `LocalVerifyBackend` (in-monolith transport, runs the job in the
  calling thread against the turn's DB session — the session is not
  thread-safe, so never bounced to a worker thread).
- **`backend/api/routes/verify.py` (new, 81 lines)** — `POST /v1/verify` →
  202 `{job_id, status, twin_id, estimated_ms}`, `GET
  /v1/verify/jobs/{job_id}`; jobs execute synchronously at creation in this
  phase; job store is an in-memory `_JOBS` dict (documented interim — the
  split moves it to Postgres + Redis); idempotency key reuses an existing job.
- **`backend/api/main.py`** — expert router removed, verify router added.
- Twin core untouched; archetype library frozen at v3c-1.0.0.

### 4. Confidence computed, not asked — DONE

- `compute_confidence(hypotheses, verification, discriminating_test)` in
  `agent.py`, run as the graph's `confidence` node: base 0.50; top VALIDATED
  +0.30, REJECTED −0.25, CANNOT_EXPRESS −0.20; +0.10×(validated−rejected)/n;
  +0.10×min(gain, log2 n)/log2 n; clamped [0.05, 0.95]. The ask call's
  `overall_confidence` is parsed and discarded. Pinned by tests: no
  verification → 0.50 exactly; top VALIDATED + gain 1.0 over 3 hypotheses →
  0.5 + 0.30 + 0.10/log2(3) ≈ 0.863; top REJECTED, no gain → 0.25.

### 5. Oracle pre-step — DONE (extraction only)

- **`backend/simulation/oracle_shared.py` (new, 47 lines)** —
  `normalize_text` + `load_fault_injections` moved verbatim;
  `backend/simulation/oracle.py` re-exports them;
  `backend/simulation/repair_actions.py:31` now imports from `oracle_shared`.
  Full `oracle.py` move to harness-scorer deferred to Step 6 prep (explicitly
  allowed by the task).

### 6. CUTs (cut only if neither core loop nor scalability)

Backend files trashed (recoverable, expiry 2026-12-23):
`backend/diagnostics/closeout.py` (`20261123-085114-5b6f73f794f84f7c94b61402f4040f7d`),
`backend/simulation/drafting.py` (`20261123-085113-eb3617d920084ac6b7bfbce0061e609a`),
`backend/simulation/review_view.py` (`20261123-085114-a84a278f21fd45c081e0c6a26a4efb6c`),
`backend/knowledge/expert.py` (`20261123-085114-051c4770f43b44e1b73cbf4f56f5e37f`),
`backend/knowledge/expert_schema.py` (`20261123-085114-09bc928d937b4433868dda958dda0774`),
`backend/knowledge/expert_prompts.py` (`20261123-085114-62f5b0ca50784e00bf7f182aff896ba2`),
`backend/api/routes/expert.py` (`20261123-085114-185727fd10104a32ba96332064609b16`).
Frontend files trashed: `frontend/src/components/DigitalTwin.jsx`
(`20261123-085145-1ef994f669754159a4e391267c68c10e`),
`ExpertKnowledge.jsx` (`20261123-085145-343727bdcd654246881ef54201e0149a`),
`ProcedureViewer.jsx` (`20261123-085145-e6e9781237424b8b8452c7e69fbd8b61`),
`frontend/src/api/schematic.js` (`20261123-085145-8bc3c3469bb84c21b798a6fe9ebbbba5`),
`frontend/src/api/evidence.js` (`20261123-085145-d9ec3023bf9f45249815275f28bbffd3`)
(the last two were the audit's verified-dead shims, zero callers).

Sweeps: `TwinWorkspace.jsx` (DigitalTwin import + embedding removed);
`DiagnosticWorkspace.jsx` (ExpertKnowledge import, `showExpert` state, overlay
definition, both "Add expert note" buttons, both `{expertOverlay}` renders);
`MachineModelViewer.jsx` (ProcedureViewer import/panel, `selectedProcedureId`
state, `onProcedureFrameChange`, `ProcedureList` `onOpenProcedure` prop, "Open
procedure viewer" button; procedures list itself stays); `api/client.js`
(removed 8 expert functions, `createDemoDigitalTwin`, `getDigitalTwinEvents`;
kept `createDigitalTwin`/`listDigitalTwins`/`getDigitalTwin`/`runDigitalTwinCommand`);
`App.css` (`.expert-overlay` block removed); `routes/learning.py` (closeout
import + `GET /learning/closeout` removed); `routes/digital_twin.py` (`POST
/demo/{product_id}/{revision_id}` + `create_demo_twin` import removed —
`service.create_demo_twin` kept, used by `tests/test_phase6_digital_twin.py`);
`routes/diagnostics.py` (tournament imports re-pointed to
`backend.simulation.verify_service`; `_apply_early_tournament` now also reuses
graph `"tournament"` entries, killing the third tournament run;
respond-handler safety block removed). Grep confirms no remaining references
to the CUT modules/components except the kept `/digital-twins` client calls
and the `Expert*` DB models (see Decisions).

Test files trashed: `tests/test_phase456_readiness_drafting.py` (8 tests,
`20261123-085327-23d9c82986d945bc853a00817bb0677b`),
`tests/test_phase5_expert.py` (4 tests,
`20261123-085327-d13c2edc8bfb40ffbac3d799dd70cd46`); their cut assertions live
in the new `tests/test_step5_cuts.py`.

DEFER (not cut, scalability base): learning, fleet, downtime, pm,
field_promotion, library_versions. `visual.py` NOT moved (no gateway container
yet — it moves with its endpoint, `routes/simulation.py:105-114`).

## Tests

New (26 tests, `tests/test_diagnosis_langgraph.py` 297 lines, 9 tests;
`tests/test_twin_client.py` 279 lines, 11 tests; `tests/test_step5_cuts.py`
161 lines, 6 tests):

- LangGraph: safety runs on turn 1 (guard-bypass symptom leads with the
  lockout line, `safety_check` timeline entry turn 1); safety quiet when no
  trigger; explicit asked sets persist across turns; confidence computed not
  asked (ask returns 0.99, stored 0.50); `compute_confidence` unit pins;
  checkpoint survives restart on SQLite (fresh graph instance resumes:
  `turns_taken` 1, 3 hypotheses, asked tests) and on real Postgres
  (`CRATER_TEST_POSTGRES_DSN`, checkpoint rows in the `checkpoints` table,
  fresh graph resumes); factory selects PostgresSaver/SqliteSaver by URL.
- Twin client: `map_verdict_row` demotes/keeps VALIDATED by `rules_used`;
  client VALIDATED when rules used (proof twin, seed rule
  `seed:feed-tension-too-tight` in `rules_used`); client demotes a backend
  VALIDATED-without-rules; twin-down → `available: False`, all NOT_VERIFIED,
  never raises; no backend → NOT_VERIFIED; async contract matches sync;
  verdict helpers; `POST /v1/verify` 202 + job GET (VALIDATED); idempotency
  key reuses job; unknown job 404.
- CUTs: grep-proof no imports of CUT modules (backend + tests, except the
  `pytest.raises(ModuleNotFoundError)` cut assertion itself); frontend files
  absent + no remaining caller references; deferred scalability base still
  present; CUT routes 404 (`/learning/closeout`, `/digital-twins/demo`,
  `/expert/*`) with kept neighbours answering; no expert router in `main.py`;
  oracle pre-step module exists and old import sites resolve.

Updated: `tests/test_phase5_learning.py` (2 closeout tests replaced with
cut-verification tests, count stays 2; dead `_outcome` helper removed);
`tests/test_phase8a_integration.py` (expert-projection test + import removed,
4 tests remain); `tests/test_phase3d_layer2_layer3.py` (AST-pruned drafting/
review tests, 5 tests remain: 4 field-promotion + points-table).

Environment: installed into `~/workspace/crater-phase1/.venv`: langgraph
1.2.24, langgraph-checkpoint-postgres 3.2.1, langgraph-checkpoint-sqlite 3.1.3,
psycopg 3.3.9 (binary), pgserver 0.1.4. `pyproject.toml` adds
`langgraph>=0.6`, `langgraph-checkpoint-postgres>=0.2`,
`langgraph-checkpoint-sqlite>=0.1`, `psycopg[binary]>=3.2`, `httpx>=0.27`;
**`uv.lock` is now stale (not updated)**. Real Postgres for gate tests:
pgserver binaries, unprivileged user `pgtest`, socket `/tmp/pgrun`, port 55433,
user `postgres`, db `postgres`, trust auth (fix applied: `chmod -R a+rX` on the
pgserver install dirs, root-owned mode 700 blocked `pgtest`).

## Gate (23 Nov 2026)

Command: `~/workspace/crater-phase1/.venv/bin/python -m pytest … -q
-p no:cacheprovider` (with `CRATER_TEST_POSTGRES_DSN="host=/tmp/pgrun
port=55433 user=postgres dbname=postgres"` for the Postgres checkpoint test),
plus `scripts/smoke_reranker_miller.py`.

| Suite | Result | Pre-Step-5 |
|---|---|---|
| Step 1 (41) | 41 passed | 41 |
| Step 2 (46) | 46 passed | 46 |
| Step 3 (115) | 115 passed | 115 |
| Step 4 (71) | 71 passed | 71 |
| Step 2 wider (60) | 60 passed | 60 |
| Adjacent 54 | 54 passed | 54 |
| Adjacent 29 | 29 passed | 29 |
| Adjacent 5 | 5 passed | 5 |
| Adjacent 22 | 22 passed | 22 |
| phase3d | 5 passed | 5 |
| phase8a | 4 passed | 4 |
| **Step 5 new** | **26 passed** (incl. Postgres checkpoint) | — |
| Reranker smoke | SMOKE PASS | PASS |

An earlier background run of Step 1 + Step 2 + wider + adjacent suites
passed 202 before the CUT test updates landed. Zip hash **not verified in
this VM**: no `crater-refactor.zip` file exists here (only phase-patch zips
under `~/workspace/crater-phase1/`); the pristine hash
`d6886155a4394a727a32f8e38ac6794d81678ebeb02596c30e7371bacecedb92` could not
be checked against a file. All work stayed inside
`~/workspace/crater-refactor/` (plus `/tmp` scratch and the venv installs).

## Failures found and fixed during Step 5

1. Turn 2 resumed stale per-turn keys (`result`, `ranked`, `verification`)
   from turn 1's checkpoint, so the ask node skipped and only 3 gateway
   calls were made — fixed by resetting all per-turn scratch keys
   (`evidence`, `verification_block`, `ranked`, `discriminating_test`,
   `verification`, `pre_verification`, `result`, `safety`) in the
   `graph.invoke` input. The durable cross-turn state is the `state` dict
   alone.
2. `NextStepType` enum leaked into checkpoints (msgpack serde warning) —
   fixed with `.model_dump(mode="json")` in `_normalize_ask`.
3. `SqliteSaver.from_conn_string` is a context manager in langgraph 1.2.24
   (no `.setup()` on the returned object) — long-lived test saver built
   directly on a `sqlite3` connection instead.
4. In-memory SQLite + TestClient thread error (`SQLite objects created in a
   thread…`) — fixtures use `StaticPool` + `check_same_thread: False`, the
   same shape as the existing route-test fixtures.
5. The literal cause `"Feed Tensioner is too tight."` hits the registry path
   in the proof twin: its reviewed RepairAction names full-machine components
   (`Wire Feed Mechanism`, `MIG Gun`, `Welder`…) the seed twin lacks, so it
   reads CANNOT_EXPRESS. The cause that VALIDATES against the proof twin is
   the symptom-text cause (`"Wire Feed Motor Runs but Wire Does Not Feed
   Properly"`, `TENSIONER_CAUSE` in `test_verify_before_conclusion.py`).
   The twin-client tests use that cause for the VALIDATED assertions.
6. `_translate_job` reads the contract `verdict` key, not `status`; the
   loop's status vocabulary is lowercase `validated`/`rejected`/`unavailable`
   (NOT_VERIFIED maps to `unavailable`). Tests assert both where they belong.
7. `conclusion_cause`'s `_texts_match` is loose (`"tighten the wingnut"`
   matched the tensioner cause via the `tight` prefix) — the helper test uses
   distinctive content (`"the spool spins freely"`).

## Decisions / open points

- **Endpoints NOT deleted**: `verify-repair`, `verify-hypotheses`,
  `early-tournament`, `upstream-walk`, `next-test`, `guided-checks`, safety
  GET, fleet-patterns GET. Live tests use them (`test_phase5_learning.py`
  verify-repair; `test_phase4_steals.py` GET `/safety`;
  `test_phase4_backtracking.py` early-tournament;
  `test_phase456_followup_walk.py` upstream-walk;
  `test_hypothesis_tournament.py` verify-hypotheses/verify-repair). The loop
  internalizes those steps; endpoint removal is deferred to an
  API-versioning step.
- **Oracle vocabulary read in `routes/diagnostics.py` kept**:
  `_load_vocabulary_signatures`/`_signatures_for_session` still prefer the
  vocab file (used by tests). Removing it belongs with the oracle relocation
  (Step 6 prep).
- **`db/models.py` Expert/ExpertInterview/KnowledgeVersion/ExpertKnowledge
  models kept** (used by `record_outcome` in `routes/diagnostics.py`); only
  the capture trio + routes + UI were cut. Consequence: outcomes can still
  be recorded `pending_review` with no review UI.
- **Verify job store is in-memory** (interim — Postgres + Redis at the split);
  no caller may assume a job survives a restart.
- **Full `oracle.py` move deferred** to Step 6 prep (pre-step done).
- **Carried forward**: remedy-sentence causes ending in "." render with
  doubled punctuation (left as-is in Step 4); MiniLM 512-token window;
  unmeasured late-turn prompts; open `get_client()` NO_PROXY crash
  (test-only stub); no live Qdrant in this VM; `uv.lock` stale.

## What was NOT done (per task)

- No Step 6 work (no oracle gate run). No Celery/Redis. No container split.
  No changes to ingestion Step 1 or reranker Step 2 code. `visual.py` not
  moved (moves with its endpoint). Twin core not rewritten.
