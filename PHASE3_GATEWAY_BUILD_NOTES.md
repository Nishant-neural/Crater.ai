# Phase 3 — gateway extraction build notes (Step 1 + container files)

**Date:** 23 Nov 2026. **Working copy only:** `~/workspace/crater-refactor/`.
**Governing:** `contracts/README.md` + `contracts/openapi-gateway.yaml`
(Phase 2), the amendment (four resolutions), the gateway audit draft.
**Scope discipline (Nishant, 23 Nov 2026):** containerize the EXISTING
application per the audits; change code ONLY where the audit says to;
no rewriting modules from scratch, no regenerating files that already
exist. This step is the audit's **Step 1** (version the API + thin shim
inside the monolith) and **Step 2** (kill the silent fallback — already
done in Phase 1 Step 2, verified here, not redone), plus the minimal
container files the split requires (gateway Dockerfile, docker-compose
with Traefik). Traefik arrives as configuration now; the service split
(ingestion, diagnosis-agent, twin-verifier) comes after the oracle gate.

## What was STOPPED / changed in approach

An earlier from-scratch direction (a brand-new shim written fresh) was
stopped by Nishant's correction mid-task. Nothing from it had been
written — only reads had happened — so there was nothing to undo. The
work below reuses the existing `backend/api/main.py`, the existing
route handlers, and the existing `backend/llm/gateway.py` code, moved
and trimmed per the audit, instead of generating a new shim.

## What changed (file paths)

### 1. Rename: `backend/llm/gateway.py` → `backend/llm/model_router.py`
Audit Step 1 / §3: the name collided with the HTTP gateway. The
routing logic is kept verbatim; only the module name and the docstring
changed. The `gateway` singleton object keeps its name so the five
callers (`backend/schematic/vision_extraction.py`,
`backend/knowledge/global_integration.py`,
`backend/knowledge/component_extraction.py`,
`backend/knowledge/code_first_integration.py`,
`backend/diagnostics/agent.py` — all `from backend.llm import gateway`)
needed no edit. Updated imports: `backend/llm/__init__.py`,
`tests/test_llm_openrouter_fix5.py` (2 sites),
`tests/test_phase8e_routing.py` (2 sites). Verified: no remaining
`backend.llm.gateway` reference anywhere in the tree.

### 2. Process-local Gemini limiter DELETED (`model_router.py`)
Audit §4: `_GeminiExtractionRateLimiter` and both call sites are gone.
Replaced by the gateway Redis token bucket (§6 below) plus the
provider retries in `backend/llm/provider.py`. A note records the
deletion in place. `settings.extraction_gemini_rpm` stays in
`backend/config.py` (a test asserts the field exists) but nothing
reads it any more.

### 3. Provider transport urllib → httpx (`backend/llm/provider.py`)
Audit §3. `OpenRouterProvider` now uses `httpx.Client(timeout=180.0)`
(explicit 180 s, `LLM_PROVIDER_TIMEOUT_SECONDS`); `urllib` is gone
from the file. Retry behaviour is unchanged (429/503, 3 attempts,
exponential backoff honouring Retry-After, capped 120 s); non-retryable
failures still raise `RuntimeError` with the status in the message.
Missing key is now an explicit error, not a silent `(None, False)`:
new `LLMKeyMissingError` raised by all three providers (Anthropic,
Gemini, OpenRouter) before any SDK import / client construction /
request. Callers already treat a raised error as a loud failure
(extraction wraps it in `ExtractionError`; diagnosis degrades the
turn via `_NoProviderError` / the repair-call catch), so no caller
change was needed.

### 4. Silent BM25 fallback — verified already gone, NOT redone
Audit Step 2 names `backend/retrieval/hybrid.py:57-62`. The fallback
was already deleted in Phase 1 Step 2: `hybrid_retrieve` raises
`RetrievalUnavailableError` on semantic outage and the reranker
propagates `RerankerUnavailableError` instead of falling back to
fusion order (amendment resolution 1). No edit made. The full
`bm25.py` removal (Qdrant sparse / Postgres FTS) is a separate infra
migration needing a re-index — not this step (contracts §8.3).

### 5. Staleness hack DELETED (`backend/api/routes/ingestion.py`)
Audit §4 + contracts §3.1: the 5-minute stale heuristic in
`get_revision_ingestion_status` (mutating `processing` jobs to
`failed` on a clock guess) is deleted, replaced by a note. Revision
readiness now comes from recorded progress only. The retry endpoint's
own "job already running" 409 guard was NOT touched (it is a
different check, not the audit-named lines). No test asserted the
stale behaviour (grep for `stale` in `tests/` is empty).

### 6. Gateway shim inside the monolith (`backend/api/main.py`, edited)
Audit Step 1 / §2, contracts §1–§2. The existing 52-line edge file
was trimmed and extended in place (still the same file, same
lifespan, same routers):
- **All routers mounted under `/v1/` verbatim**, plus the legacy
  unversioned alias (migration only, then deleted — an alias kept
  forever is a second API). The verify router already carries its
  own `/v1/verify` prefix, so it is mounted once. 132 paths in the
  generated OpenAPI (was 80 endpoints unversioned).
- **CORS from environment** (`CORS_ORIGINS`); no origin list is
  compiled into code any more (audit §4). Empty means no CORS
  middleware; the local-dev list lives in `.env.example`.
- **`/health` unchanged** (liveness only, checks nothing).
  **`/ready`** now also checks LLM-key configuration
  (`backend/api/readiness.py`: new `check_llm_keys()` — the key for
  the diagnosis provider must be present; the key value is never
  returned). The reranker check stays until diagnosis-agent owns
  its own `/ready` at the split.
- **Uniform error envelope everywhere** (contracts §1.3):
  `{"error": {"code", "message", "request_id", "upstream"}}`.
  The two existing handlers (retrieval / reranker 503s) now emit it
  with a named upstream (`qdrant` / `reranker`); added handlers for
  `HTTPException`, `StarletteHTTPException` (unknown routes), and
  `RequestValidationError` (422s previously leaked FastAPI's
  `{"detail": [...]}` shape).
- **`/auth/verify`** — the Traefik forwardAuth target (audit §7):
  200 while auth is off (local dev default, one loud warning),
  200 with a valid `X-Api-Key` once `GATEWAY_AUTH_ENABLED` is on,
  401 in the standard envelope otherwise.
- **`/metrics`** — Prometheus text from `backend/api/metrics.py`.
- **`/v1/expert/...` → explicit 404** in the standard envelope
  (amendment resolution 3; contracts §2.3). No `routes/expert.py`
  exists in this tree, so there was no route file to delete; the
  handler exists only so the CUT is a deliberate 404, not
  FastAPI's default shape. The `Expert` / `ExpertInterview` models
  stay in `db/models.py` as data (contracts §2.3: expert *knowledge*
  stays in diagnosis-agent; expert *capture* is what was cut).

### 7. New shim modules (audit-budgeted, nothing more)
- `backend/api/middleware.py` (~120-line budget, audit Step 1):
  `RequestIDMiddleware` (generate/propagate/echo `X-Request-ID`,
  structured JSON log per request, Prometheus observation),
  `LearningGateMiddleware` (403 `learning_deferred` for
  `/v1/learning/...` and the legacy alias unless
  `learning_enabled` — contracts §2.4, flipped only after ≥20
  pilot outcomes), `RateLimitMiddleware` (429 + `Retry-After`),
  and the `error_envelope()` helper.
- `backend/api/rate_limit.py` (~60-line budget, contracts §1.5):
  token bucket — diagnosis 10/min + 20 burst, reads 60/min,
  uploads 5/hour, per API key (else client IP). Redis-backed via
  an atomic Lua script when `REDIS_URL` is set; process-local
  fallback with a warning when Redis is absent (single replica),
  and a runtime fallback to the local bucket if Redis errors —
  a request never fails because Redis did.
- `backend/api/metrics.py` (~30 lines): request count / latency
  by route, method, status.
- `backend/api/auth.py` (~60-line budget, audit §7): key
  validation — `GATEWAY_API_KEYS` env bootstrap list, then an
  `api_keys` Postgres table if one exists (it does not yet; the
  table + migration land with the Postgres move, contracts §8.2).
  Auth is off by default so the local run is unchanged.

### 8. Config (`backend/config.py`, `.env.example`)
New gateway fields only: `cors_origins` (default `""`),
`learning_enabled` (default `False`),
`gateway_auth_enabled` (default `False`),
`gateway_api_keys` (default `""`), `redis_url` (default `""`).
`extra="ignore"` was NOT flipped to `"forbid"` in the monolith:
`.env.example` itself carries `RERANK_PROVIDER` / `RERANK_MODEL`
entries that no `Settings` field declares, so flipping now would
break startup for anyone using the example env. `extra="forbid"`
lands per service at the split, as the audit's config-split row
directs. `.env.example` gained the gateway section documenting
all five new variables.

### 9. Harness + frontend to `/v1/` (audit Step 1, same change)
- `benchmark/harness.py`: `live_run` appends `/v1` to the base URL
  (the `--base-url` flag stays the host); docstring updated.
- `frontend/src/api/client.js:2`: default `API_BASE` is now
  `http://localhost:8000/v1`. All frontend paths are relative, so
  no other frontend file changed. `VITE_API_BASE` still overrides
  the whole base; the legacy alias keeps a host-only override
  working during migration. `npm ci` + `npm run build` pass;
  `node_modules/` and `dist/` were removed afterwards (regenerable).

### 10. `uv.lock` staleness FIXED (`pyproject.toml`, `uv.lock`)
Contracts §8.2: the lock had 0 langgraph mentions vs 3 in
`pyproject.toml`. `uv lock` regenerated: langgraph,
langgraph-checkpoint-postgres, langgraph-checkpoint-sqlite are now
present (30 mentions); `uv lock --check` passes. Also added the
audit §3 gateway deps to `pyproject.toml`: `redis>=5.0`,
`prometheus-client>=0.20` (`httpx` was already a main dependency).
Lock grew 3,004 → 3,779 lines (langgraph tree + the two new deps).

### 11. Container files (minimal new files the split requires)
- `docker/gateway/Dockerfile` (~30 lines): builds the monolith
  image for now (the shim still lives inside it) with
  `uv sync --frozen`; the slim per-service dep set lands when the
  shim moves out. `/health` healthcheck.
- `docker-compose.yml`: `traefik` (v3, :80 + dashboard :8080
  local only), `gateway` (builds the Dockerfile; `QDRANT_URL`,
  `REDIS_URL`, `CORS_ORIGINS`, auth/learning flags from env),
  `postgres:16`, `qdrant`, `redis:7-alpine`. Postgres is
  provisioned but NOT wired to the app yet — `DATABASE_URL` stays
  commented until the ingestion step (contracts §8.2: re-ingest
  from PDFs against Postgres, not dump/restore). The monolith's
  local run (`uvicorn` directly) is untouched.
- `docker/traefik/traefik.yml` (static) + `docker/traefik/dynamic.yml`
  (routers/middlewares): retry 3 attempts, circuit breaker
  `NetworkErrorRatio() > 0.5`, forwardAuth to the shim's
  `/auth/verify`, load-balancer healthcheck on `/health` only
  (never `/ready` — a downstream outage must not drain the
  gateway). Rate limits are deliberately NOT in Traefik (its
  middleware is per-second only; the contract needs 10/min and
  5/hour) — they live in the shim (§7). YAML validated.

## What was deliberately NOT done here (deferred, with owner)
- **§2.5 public-surface removals** — `verify-repair`,
  `verify-hypotheses`, `early-tournament`, `upstream-walk`,
  `next-test`, `guided-checks`, safety GET, fleet-patterns GET —
  all **KEPT mounted under `/v1/` verbatim**. Contracts §2.5 /
  §10: the removals land in the **diagnosis-agent extraction**,
  not the gateway step. Verified present in the generated OpenAPI
  (132 paths). A client calling them under `/v1/` still reaches
  the monolith handlers today.
- **`visual.py` move** — `backend/simulation/visual.py` was NOT
  moved or deleted. Gateway side only: its endpoint
  (`GET /twins/{id}/visual` in `routes/simulation.py`) is reachable
  under `/v1/` via the mount (`/v1/simulation/twins/{id}/visual`,
  in the OpenAPI). Ownership transfers to **twin-verifier** in its
  extraction (contracts §5.5 lists `/v1/twins/{id}/visual` there);
  the final path change lands then.
- **Learning routes** — kept mounted, gated 403 (see §6/§7), not
  removed; extraction sequenced after the core loop (DEFER-retained).
- **Frontend expert UI cut** (`ExpertKnowledge.jsx`) — no such file
  exists in this tree (already absent); nothing to cut. The
  frontend learning UI gating belongs to the frontend step (last).
- **Celery + Redis job queue, Postgres move, Alembic** — ingestion /
  infra steps, not gateway. Redis here serves only the rate-limit
  bucket.
- **Idempotency on diagnose start / `observations` on start** —
  `StartDiagnosticRequest` has neither yet; contracts §4.1 makes
  `observations` a 422 until the diagnosis schema change lands.
  That is diagnosis-agent work.
- **Per-service `pyproject.toml` / slim gateway deps** — land when
  the shim moves out of the monolith (audit §3).

## Non-LLM test results

Run in a throwaway venv (`~/workspace/.gw-venv`, removed after the
run) with the project's declared deps except `sentence-transformers`
/ torch (lazy imports; not installed here, so CrossEncoder tests
cannot run in this environment — see below). No LLM call was made
anywhere in this step; no mock-server run; no Muse tokens spent.

- **New `tests/test_gateway_shim.py`: 14 passed.** /health 200,
  request-ID generate/echo/propagate, unknown-route 404 envelope,
  expert 404 envelope (both prefixes), learning 403 default +
  gate opens when enabled, validation 422 envelope, /v1/ + legacy
  diagnose routes both resolve, /ready 503 naming the failed
  checks (`llm_keys` naming `openrouter`, `qdrant`), llm-keys
  check unit test, forwardAuth allow-when-off / 401 / allow-with-key,
  diagnosis burst 20 then 429 with Retry-After ≥ 1, /metrics
  serves Prometheus text.
- **LLM provider/routing tests: 31 passed**
  (`tests/test_llm_openrouter_fix5.py`,
  `tests/test_llm_provider.py`, `tests/test_phase8e_routing.py`) —
  including the rewritten httpx mocks (timeout asserted 180.0) and
  the three updated missing-key tests (now asserting
  `LLMKeyMissingError`, no request made).
- **Targeted neighbours: 42 passed**
  (`test_phase1_hardening`, `test_phase456_readiness_repairs`,
  `test_verification_and_retrieval`, `test_diagnosis_robustness`,
  `test_phase1_pipeline`) plus the reranker file's load-failure
  tests that can run without the model.
- **Full suite: 528 passed, 15 failed, 2 skipped, 5 errors**
  (13 min 4 s, `pytest tests/ -q`). **No regression from this step:**
  every failure is a missing file or a missing package in this
  throwaway venv, none of them in code this step touched —
  - 7× `tests/test_benchmark_oracle_runner.py`: `BenchmarkOracleError:
    oracle file not found: /home/hatch/crater-benchmarks/evaluations/
    c002-lincoln.json` (and `c002b-miller.json`) — oracle JSONs that
    live outside the repo and do not exist in this VM.
  - 4× `tests/test_phase3c_m800v_sample.py` + 2×
    `tests/test_phase3c_oracle_fix.py`: `FileNotFoundError` for
    `/home/hatch/goals/crater-ai-diagnosis-and-machine-model-
    workflow-improvement/files/m800v-alarm-observable-sample-v1.json`
    and `oracle_vocabulary_v1.json` — user-side files absent here.
  - 1× `tests/test_diagnosis_langgraph.py::
    test_checkpointer_factory_selects_postgres_for_postgres_url`:
    `ModuleNotFoundError: No module named 'langgraph.checkpoint.postgres'`
    — the venv installed the latest langgraph-checkpoint-postgres
    (new import path), not the version the repo's lock pins; this
    step did not touch `backend/diagnostics/checkpoints.py`.
  - 1 failed + 5 errors in `tests/test_reranker.py`: missing
    `sentence_transformers` in this venv (see above).
  - 2 skipped (pre-existing skips).
  For reference, the Phase 2 baseline on Nishant's machine was 243
  passed / 2 failed (pre-existing phase8a) / 1 collection error;
  this run's extra failures are all environment absences above,
  not code changes — no test failed on an assertion in code this
  step touched.
- **Live smoke (TestClient, no LLM):** /health 200; /ready 503
  with per-dependency breakdown (qdrant unreachable, reranker
  model not loaded, no LLM key — all named); /v1/expert 404
  envelope; /v1/learning/review 403; /auth/verify 200 (auth off);
  /metrics 200; /v1/diagnose/start and legacy /diagnose/start both
  reach validation (422, now in the envelope).
- **Frontend:** `npm ci` + `npm run build` pass.
- **YAML:** `docker-compose.yml`, `docker/traefik/*.yml` parse.
- **Not runnable here:** the 5 reranker tests + 1 real-CrossEncoder
  test error with `ModuleNotFoundError: No module named
  'sentence_transformers'` — the venv has no torch. That is an
  environment limitation, not a regression: the same tests need
  the model on Nishant's laptop. The full-suite failures (15) are
  likewise all missing external files / venv package drift — see
  the full-suite entry above. No test failed on an assertion in
  code this step touched.
- **Stray-file hygiene:** the readiness DB probe creates a 0-byte
  `./crater.db` in the repo cwd (the default dev DB); the shim
  test redirects the probe to a tmp file and the repo file was
  removed after each manual smoke run. The venv
  (`~/workspace/.gw-venv`) was removed after the run.

## Post-extraction oracle gate — NOT RUN, ready and waiting

**The post-extraction oracle gate was NOT run.** Per Nishant's
standing rule (23 Nov 2026 15:49 IST), no test may run with Muse as
the LLM backend until he is told first — he will have another LLM do
it instead. No mock-server run with model `muse-spark` was started
and no Muse tokens were spent on a gate run in this step.

**The gate is ready and waiting for Nishant's other LLM.** The
frozen Phase 1 gate to re-run after this extraction: Miller 22/38
(tie accepted for proceeding), OMNIPRO 24/35, discriminating test
17/17 Miller / 8/9 OMNIPRO, citations 79/79 Miller / 35/35 OMNIPRO.
Harness path is updated (`benchmark/harness.py` now calls
`/v1/diagnose/...`); the kill-test to include is the audit §8
acceptance: with Qdrant stopped, `/v1/query` returns 503 in the
standard envelope and `/ready` returns 503 naming Qdrant, while
`/health` still returns 200. Do NOT redo Phase 1 ranking work in
the gate — the tie and the ranking bottleneck are a separate
follow-up, not a blocker for the split.
