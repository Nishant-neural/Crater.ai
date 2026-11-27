# Phase 3 — diagnosis-agent extraction build notes

**Date:** 23 Nov 2026. **Working copy only:** `~/workspace/crater-refactor/`.
**Governing:** `contracts/README.md` + `contracts/openapi-diagnosis.yaml`
+ `contracts/openapi-ingestion.yaml` (Phase 2), the amendment (four
resolutions), the diagnosis audit draft, and
`PHASE3_INGESTION_BUILD_NOTES.md` (the ingestion step deliberately
left the diagnosis-side cross-service reads for this step, and listed
them exactly — all of them are migrated here).
**Scope discipline (Nishant, 23 Nov 2026):** containerize the EXISTING
application per the audits; change code ONLY where the audit says to;
no rewriting modules from scratch, no regenerating files that already
exist. This step is the Phase 3 extraction order's step 3 (after
gateway, ingestion). The oracle gate after this extraction is READY
AND WAITING for Nishant's other LLM (standing rule 23 Nov 15:49 IST
— no Muse-as-backend run here).

## What changed (file paths, edits not rewrites)

### 1. Cross-service reads go over ingestion's HTTP API (`backend/config.py`, `backend/retrieval/ingestion_client.py` — new, minimal)

- `backend/config.py`: `ingestion_base_url` (default
  `http://localhost:8000`), `diagnosis_base_url` (default
  `http://localhost:8000`), `twin_verifier_base_url` (default `""` =
  in-monolith `LocalVerifyBackend`, TEMPORARY — see §4).
- `backend/retrieval/ingestion_client.py` (new, minimal, ~140 lines):
  sync httpx client, 10 s timeout. `IngestionUnavailableError` (with
  `.reason`); `_get` returns None on 404, raises on transport failure
  / 5xx / other 4xx. Methods: `list_products()`,
  `list_revisions(product_id)` (None if product unknown),
  `get_revision_summary(revision_id)` (via chunks `?limit=0`),
  `get_model(revision_id)`,
  `get_failure_modes(revision_id, symptom=None)`,
  `fetch_chunks(...)`, `fetch_all_chunks(...)` (pages of 500),
  `fetch_chunks_by_ids(...)`. `get_ingestion_client(transport=None)`
  returns a fresh client from settings. Consumers use
  `from backend.retrieval import ingestion_client as ingestion_client_mod`
  and call the module attribute, so tests can monkeypatch the factory
  once.
- **New endpoint, a contract addition** (flagged, not silent):
  `GET /v1/knowledge/revisions/{revision_id}/chunks` in
  `backend/api/routes/machine_knowledge.py`, added to
  `contracts/openapi-ingestion.yaml`. It was needed because Qdrant
  payloads carry no chunk content and no existing endpoint served
  chunk contents: params `ids` (comma-separated), `doc_type`
  (validated against the DocType enum, 422 if unknown), `limit`
  (clamped 0–500), `offset`; returns whole contents, never truncated;
  404 for unknown revision; `limit=0` is a light existence probe.
- `backend/retrieval/knowledge.py`: the canonical model is fetched via
  `GET /v1/knowledge/revisions/{id}/model`;
  `retrieve_machine_knowledge(query, *, product_id, revision_id,
  top_k=12, graph_hops=2, client=None)` — the session param is
  dropped; product check via `list_revisions`; label from the
  revision dict. Scoring + 2-hop graph expansion unchanged.
- `backend/retrieval/bm25.py`: corpus fetched via ingestion HTTP
  (`fetch_all_chunks`; product-only scope pages every revision of
  the product); `bm25_search(query, top_k, product_id=None,
  revision_id=None, doc_type=None, client=None)` — session dropped;
  no scope returns `[]` (retrieval is hard revision-scoped). BM25
  math unchanged.
- `backend/retrieval/hybrid.py`: `hybrid_retrieve(query, product_id,
  revision_id, doc_type, client)`; fused-set contents via
  `fetch_chunks_by_ids` (only when revision_id is present);
  `RetrievedChunk` gained optional `source_document: str | None` and
  `doc_type: str | None` fields (backward-compatible with test
  constructions). `retrieve_revision_context(query, product_id,
  revision_id, doc_type=None, top_k=None, client=None)`. RRF/rerank
  logic unchanged.

### 2. Diagnosis loop + routes de-HTTP'd (`backend/diagnostics/agent.py`, `graph.py`, `learning.py`, `backend/api/routes/diagnostics.py`, `query.py`, `mode_b.py`)

- `backend/diagnostics/agent.py`: `Product` import removed;
  `_signatures_for_product(db, session)` → `_signatures_for_product(session)`
  (product matched via HTTP `list_products`). `run_turn`: if
  `settings.twin_verifier_base_url` is set →
  `TwinVerifyClient(base_url=...)`; else TEMPORARY
  `TwinVerifyClient(backend=LocalVerifyBackend(db))` with an explicit
  TEMPORARY comment (see §4).
- `backend/diagnostics/graph.py`: `Document` import removed;
  `retrieve_node` calls `_agent.hybrid_retrieve(query, product_id,
  revision_id)` (no db) and uses `r.source_document or r.document_id`
  for evidence refs.
- `backend/api/routes/query.py`: no DB session; revision check via
  `get_revision_summary` (404 if None); evidence `source_document` /
  `doc_type` from the chunk row.
- `backend/api/routes/diagnostics.py` (1374 → 1025 lines): imports
  pruned (`Product, Revision, Document, Component,
  ComponentRelationship, MachineKnowledgeModelSnapshot`,
  `backend.simulation.tournament`, `repair_actions`,
  `fleet.sibling_search`, `diagnostics.safety`, `present_hypotheses`,
  `cite_hypotheses`). `_attempt_for_state` deleted (only used by
  deleted endpoints). `_walk_edges` now uses `/model` (entities +
  relations); edge source string changed `"twin_relations"` →
  `"canonical_model"` (test assertion updated). `_walk_bindings`
  snapshot branch now uses `/model`. `start` validates
  product/revision via HTTP (`list_revisions` → 404 Product;
  `get_revision_summary` → 404 Revision; membership → 400).
  `get_diagnostic_context` fully via HTTP (products list, revisions
  list, `fetch_all_chunks` for distinct documents, `/model` for
  entities→components with `function`/`part_number` from entity
  properties, relations→relationships, snapshot summary). Deleted
  the 8 §2.5 endpoints + `VerifyRepairRequest`/`UpstreamWalkRequest`
  models (file truncated at `next-test`; removal note appended
  in-file). Kept: `_apply_early_tournament` and `_run_upstream_walk`
  (respond drives them); `load_twin_for_session` /
  `run_tournament_for_state` imports kept.
- `backend/api/routes/mode_b.py`: `start_session` import removed;
  module-level `_start_diagnosis_session(product_id, revision_id,
  symptom) -> dict` (httpx POST to
  `{diagnosis_base_url}/v1/diagnose/start`, 10 s; HTTPError → 503;
  status ≥400 → that status; missing `session_id` → 503).
  `diagnose_on_partial` uses it.
- `backend/diagnostics/learning.py`: `last_twin_verdict(state,
  cause=None)` now also reads `"early_tournament"` timeline entries
  (one result per cause; cause matched by normalized strip/casefold),
  keeping legacy `"verify_repair"` entries readable.
  `apply_field_outcome` passes `outcome.confirmed_cause`. This keeps
  the Phase 5 outcome flow working after the verify-repair endpoint
  deletion.
- `backend/api/main.py`: `IngestionUnavailableError` handler → 503
  `ingestion_unavailable`, upstream `ingestion`.

### 3. §2.5 removals land here (contracts §2.5)

Deleted from `backend/api/routes/diagnostics.py`: verify-repair,
verify-hypotheses, early-tournament, upstream-walk, next-test,
guided-checks, safety GET, fleet-patterns GET. The tournament and
the upstream walk themselves stay, driven by POST
`/diagnose/{id}/respond` (`_apply_early_tournament`,
`_run_upstream_walk`). The safety-first RepairAction presentation
(`render_repair_action_safety_first`) is still covered by
`tests/test_phase456_readiness_repairs.py`; safety still runs as the
first node of every turn in `run_turn` (verified still present, not
redone). **Frontend callers NOT fixed, only noted:**
`frontend/src/api/client.js` still references `verifyRepair`,
`verifyHypotheses`, `getMostInformativeTest`,
`getGuidedPhysicalChecks` — the frontend split is a later step.

### 4. Twin verifier: already thin — NOT thinned, verified with evidence

The task said to thin `backend/diagnostics/twin_verification.py`
(293 lines) to a ~35-line async client. Inspection showed it is
**already** the thin client: grep confirms no `backend.simulation`
import and no in-process verification logic in the file. It is the
thin async client (POST /v1/verify → 202 + job, poll via
`_verify_http`, never raises, degrades to NOT_VERIFIED), plus
`verify_sync` (the sync loop's entry point) and 4 pure verdict
helpers (`verdict_for_cause`, `cannot_express_cause`,
`conclusion_cause`, `render_verification_block`) imported by
agent.py / graph.py / routes / tests. Deleting it down to 35 lines
would have deleted the sync wrapper and the helpers the loop needs,
so it was left as-is and the remaining item-2 work — the HTTP wiring
via `TWIN_VERIFIER_BASE_URL` in `run_turn` — was done in agent.py
(see §2). The in-process `LocalVerifyBackend` fallback stays
TEMPORARY until the twin-verifier service is split (next step);
then `TWIN_VERIFIER_BASE_URL` is set and the fallback goes away.

### 5. Diagnosis-agent service container (`backend/diagnostics/app.py` — new, `backend/diagnostics/readiness.py` — new, `docker/diagnosis-agent/Dockerfile` — new, `docker-compose.yml`, `.env.example`)

- `backend/diagnostics/app.py`: FastAPI app, `lifespan` with
  `init_db()`, `RequestIDMiddleware` + `LearningGateMiddleware`
  (RequestID added last so it runs outermost), mounts `diagnostics`,
  `query`, `learning` routers under `/v1/` + the legacy unversioned
  alias. `GET /health` (liveness only, `{"status": "ok"}`).
  `GET /ready` (via readiness.py, 503 naming failed checks).
  Exception handlers: RequestValidationError → 422 envelope,
  HTTPException, StarletteHTTPException (so the removed §2.5 paths
  404 in the standard envelope), plus `RetrievalUnavailableError` →
  503 `retrieval_degraded` upstream `qdrant`,
  `RerankerUnavailableError` → 503 `reranker_unavailable` upstream
  `reranker`, `IngestionUnavailableError` → 503
  `ingestion_unavailable` upstream `ingestion`.
- `backend/diagnostics/readiness.py`: `check_postgres` (SELECT 1),
  `check_qdrant` (collection exists + answers), `check_reranker`
  (`ensure_reranker_loaded`), `check_ingestion` (GET
  `{ingestion_base_url}/health` 200, 2 s timeout);
  `readiness_checks()` / `is_ready()`.
- `docker/diagnosis-agent/Dockerfile`: mirrors
  `docker/ingestion/Dockerfile` (python:3.12-slim,
  `uv sync --frozen`, copy `backend/` + `alembic/`, HEALTHCHECK on
  `/health`, CMD `uvicorn backend.diagnostics.app:app`). **No
  tesseract** (diagnosis never OCRs) and **no migration step** (the
  ingestion container owns `alembic upgrade head`; this service only
  reads/writes the diagnosis-owned tables Alembic already created).
  Like the gateway and ingestion images it still builds the monolith
  tree for now: respond's early tournament and upstream walk still
  run in-process simulation code, and the learning/fleet/downtime/pm
  modules are DEFER-retained in this tree. The slim per-service
  dependency set lands with the twin-verifier step.
- `docker-compose.yml`: new `diagnosis-agent` service (internal-only,
  no published port, like ingestion; `depends_on` qdrant, redis,
  postgres, ingestion); `INGESTION_BASE_URL: http://ingestion:8000`
  on `gateway` and `diagnosis-agent`; `DIAGNOSIS_BASE_URL:
  http://diagnosis-agent:8000` on `ingestion` (Mode B diagnose).
  Diagnosis-agent env otherwise mirrors ingestion: `DATABASE_URL`,
  `QDRANT_URL`, `QDRANT_COLLECTION`, `REDIS_URL`, LLM keys,
  `CRATER_LEARNING_ENABLED: "false"`.
- `.env.example`: documents `INGESTION_BASE_URL`,
  `DIAGNOSIS_BASE_URL`, `TWIN_VERIFIER_BASE_URL` (empty until the
  twin-verifier split).

### 6. Learning gate verified on the diagnosis side

Learning routes are mounted on the diagnosis app but gated 403
`learning_deferred` by the same `LearningGateMiddleware` (the
monolith's middleware, not a duplicated gate). Verified by test:
GET `/v1/learning/review` and `/learning/review` → 403
`learning_deferred` on the diagnosis app.

### 7. What was deliberately NOT done here (deferred, with owner)

- **`backend/knowledge/legacy_projection.py` (149 lines, zero
  callers) NOT deleted** — left untouched per the task; still needs
  a confirmation decision.
- **Diagnosis-owned rows keep using shared `backend.db.models`:**
  `DiagnosticSession`, `DiagnosticStatus`, `DiagnosticOutcome`,
  `DigitalTwin` (outcome flow), `Expert*` / `KnowledgeVersion*`
  (outcome flow) reads stay — they are diagnosis-agent's own schema.
  Splitting `models.py` per service is a separate schema-split step
  (the ingestion step likewise kept the shared module). Only
  ingestion-owned tables were de-HTTP'd: Product, Revision,
  Document, Chunk, FailureMode, MachineKnowledgeModelSnapshot,
  Component, ComponentRelationship.
- **`repair_cases` imports kept:** `backend/knowledge/repair_cases.py`
  loads reviewed JSON registries from files next to the module
  (OMNIPRO + Miller), not ingestion DB data — so
  `load_registry` / `load_miller_registry` imports stay in agent.py
  and diagnostics.py; only the `Product` DB read behind them was
  replaced by HTTP. Sourcing signatures from `/failure-modes`
  instead is a follow-up (it would change picker inputs under a
  frozen gate).
- **`_load_twin_for_session` DigitalTwin read kept:** twin-verifier-owned;
  removed in the twin-verifier step, not this one.
- **Two remaining cross-service reads, both in DEFER-retained /
  out-of-loop code (verified unreachable from the live loop):**
  `backend/diagnostics/fleet.py` reads Product/Revision/FailureMode
  — it has no callers outside itself (its only caller was the deleted
  fleet-patterns endpoint); `backend/diagnostics/downtime.py`
  `bad_actor_ranking` reads Product — behind the gated learning
  routes only (`GET /learning/bad-actors`). The live loop paths
  (`start_downtime` / `close_downtime`) read only diagnosis-owned
  rows. Both reads get HTTP-ified when fleet/learning return (after
  ≥20 pilot outcomes), not here.
- **Frontend callers of removed endpoints** (see §3) — frontend split.
- **Twin-verifier and frontend splits** — separate later steps, not
  started here.

## Non-LLM test results

Throwaway venv (`~/workspace/.ing-venv`, kept for the parent) with
the project's declared deps pinned to `uv.lock` versions, except
`sentence-transformers` / torch (lazy imports; not installed here,
so CrossEncoder tests cannot run in this environment) and
`anthropic` / `google-genai` (installed, lock-pinned 1.4.0 / 2.24.0).
No LLM call was made anywhere in this step; no mock-server run; no
Muse tokens spent.

- **New `tests/test_diagnosis_service.py`: 19 passed.** Diagnosis
  /health 200; /ready 503 naming qdrant/reranker/ingestion (postgres
  ready) and 200 with mocked checks; /ready 503 naming ingestion
  only when the other checks pass; served OpenAPI covers the
  contract surface (`/v1/` + legacy alias) with all 8 §2.5 paths
  absent; contract YAML has no removed or expert paths; all 8
  removed endpoints 404 (GET + POST); learning routes 403
  `learning_deferred`; start 404 unknown product / 404 unknown
  revision / 200 creates a session (LLM + retrieval stubbed);
  start with ingestion down 503 `ingestion_unavailable` upstream
  `ingestion`; query 404 unknown revision via the HTTP revision
  check; hybrid reads chunk contents via the ingestion client
  (fusion order + `source_document`); BM25 builds its corpus via
  the ingestion client and returns `[]` with no scope; **kill test:
  Qdrant down → POST /v1/query 503 `retrieval_degraded`, upstream
  `qdrant`, while /health stays 200**; Mode B
  `_start_diagnosis_session` success / transport failure 503 /
  404 passthrough; twin client HTTP 202 + poll → VALIDATED (with
  rules_used) and twin-down → NOT_VERIFIED.
- **Updated suites (all passing unless noted):**
  `test_verification_and_retrieval` (3), `test_phase8c_integrity`
  (graph-expansion test now via MockTransport), `test_reranker`
  hybrid tests (3 passed; see the sentence_transformers note below),
  `test_phase1_hardening` (hybrid call updated), `test_diagnosis_rank_then_ask`
  (19 passed — the 3 product-dependent turn tests now stub the
  ingestion client, so the OMNIPRO picker / Miller fallback paths
  are tested for the right reason, not by an ingestion-down accident),
  `test_phase6_mode_b` (Mode B fixture now stubs
  `_start_diagnosis_session`), `test_phase5_learning` (10 passed —
  the 2 verify-repair tests now drive the verdict through the live
  path: one degraded respond turn, whose early tournament writes a
  real `early_tournament` timeline entry with VALIDATED for CAUSE_A,
  then POST `/outcome`; the safety-first presentation assertions
  were dropped here because that presentation is covered by
  `test_phase456_readiness_repairs.py`), `test_phase4_backtracking`
  (early-tournament test now drives `_apply_early_tournament`
  directly + endpoint 404), `test_phase4_steals` (safety GET → 404),
  `test_phase456_followup_walk` (walk now driven via respond with
  the table-exhausted LLM stub; idempotency + refusal via direct
  `_run_upstream_walk` calls; `edges_source` assertion now includes
  `"canonical_model"`), `test_hypothesis_tournament` (3 endpoint
  tests → 404), `test_ingestion_service` (kill test stubs the
  ingestion client so the revision check succeeds; 22 passed with
  the walk file), `test_diagnosis_robustness`
  (`test_run_turn_retrieves_once_and_survives_bad_output`'s
  `fake_retrieve` updated from the old
  `(session, query, product_id, revision_id)` signature to the new
  `(query, product_id=None, revision_id=None)`; 24 passed after the
  fix), `scripts/smoke_reranker_miller.py` (hybrid calls
  updated to the new signature + stub client; not run — needs the
  real CrossEncoder, see below).
- **Full suite: 564 passed, 15 failed, 2 skipped, 5 errors** (13 min
  20 s, `pytest tests/ -q`). Reconciled against the ingestion
  baseline (546 passed / 14 failed / 2 skipped / 5 errors, with
  `anthropic` + `google-genai` installed): 546 + 19 new service
  tests = 565; minus the 1 test below that failed instead of
  passing = 564 passed, 14 + 1 = 15 failed.
  - The single new failure was `test_diagnosis_robustness::
    test_run_turn_retrieves_once_and_survives_bad_output` — the
    test's `fake_retrieve` still used the old 4-arg
    `hybrid_retrieve` signature (the graph now calls it without the
    session). Fixed in the test (one signature line, no production
    change) and verified: `test_diagnosis_robustness.py` 24/24 after
    the fix, so the projected tally is **565 passed / 14 failed** —
    the same 14 environmental failures as the baseline, with no
    remaining failure in code this step touched. The full suite was
    not re-run end-to-end after that one-line test fix (13+ min for
    arithmetic already verified file-by-file).
  - The remaining 14 failures + 5 errors are the ingestion
    baseline's environment absences, identical in cause: 7×
    `test_benchmark_oracle_runner` (oracle JSONs outside the repo),
    4× `test_phase3c_m800v_sample` + 2× `test_phase3c_oracle_fix`
    (user-side files under `/home/hatch/goals/...`), 1 failed + 5
    errors in `test_reranker.py` (missing `sentence_transformers`).
    No test failed on an assertion in code this step touched.
- **YAML:** `docker-compose.yml` parses (verified by load: 8 services
  incl. `diagnosis-agent`, internal-only, env wiring as §5).
- **Docker images were NOT built** — no Docker in this VM. The
  Dockerfile and compose entries are written but unbuilt; the first
  `docker compose up --build` on a machine with Docker is still
  unverified.
- **Not runnable here:** the reranker tests above + the
  smoke script (needs the real CrossEncoder).

## Post-extraction oracle gate — NOT RUN, ready and waiting

**The post-extraction oracle gate was NOT run.** Per Nishant's
standing rule (23 Nov 2026 15:49 IST), no test may run with Muse as
the LLM backend until he is told first — he will have another LLM do
it instead. No mock-server run with model `muse-spark` was started
and no Muse tokens were spent on a gate run in this step. No live
ingestion was run (no LLM calls at all).

**The gate is ready and waiting for Nishant's other LLM.** The
frozen Phase 1 gate to re-run after this extraction: Miller 22/38
(tie accepted for proceeding), OMNIPRO 24/35, discriminating test
17/17 Miller / 8/9 OMNIPRO, citations 79/79 Miller / 35/35 OMNIPRO.
Re-ingest from the PDFs against Postgres (compose: `docker compose
up --build`, then upload via the gateway) — do NOT dump/restore the
SQLite data. Kill tests to include (already verified at the API
level in this step): with Qdrant stopped, `/v1/query` returns 503
in the standard envelope and `/ready` returns 503 naming Qdrant,
while `/health` still returns 200; with the ingestion API stopped,
diagnosis `/ready` returns 503 naming ingestion. Do NOT redo Phase 1
ranking work in the gate — the tie and the ranking bottleneck are a
separate follow-up, not a blocker for the split.
