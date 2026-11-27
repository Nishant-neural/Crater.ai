# Crater.ai — /v1/ service contracts (Phase 2, contracts first)

Date: 23 Nov 2026. Working copy only: `~/workspace/crater-refactor/`.
Status: **contracts, not code.** No implementation was changed here and no
container was split. These contracts are what Phase 3 extraction must
implement, one service at a time, with a full oracle run after each move.

Governing sources (read first; this document resolves them, it does not
replace them):

- Amendment — four resolutions (23 Nov 2026):
  `~/workspace/your_files/crater-audit-amendment-four-resolutions/crater-audit-amendment-four-resolutions.pdf`
  — CrossEncoder now (done in Phase 1 Step 2); Celery + Redis everywhere,
  no Redis Streams; expert routes CUT with the trio; table parser stays a
  MODULE inside ingestion (no standalone service, no `POST /v1/tables/parse`).
- Six per-container audits: `~/workspace/your_files/crater-audit-*/crater-audit-*.pdf`
  (frontend, gateway, ingestion, diagnosis-agent, twin-verifier, infra).
- Original plan: `~/workspace/your_files/crater-problem-list-docker-plan/crater-problem-list-docker-plan.pdf`
  — §3 "contracts first", service catalogue, repo layout.
- Phase 1 gate: `PHASE1_STEP6_BUILD_NOTES.md` in this tree (Miller 22/38
  tie, OMNIPRO 24/35, discriminating test 17/17 Miller, 8/9 OMNIPRO).

Where an audit draft conflicts with the amendment, the amendment governs.
Where an audit draft conflicts with a decision recorded here, this document
governs for Phase 3 — and the conflict is named in §10 so it is not silently
lost.

## 0. The split being contracted

8 production containers + 1 eval-only container:

| Container | Owns (and only this) |
|---|---|
| frontend | React UI; talks ONLY to the gateway |
| gateway | Traefik v3 + ~250–350-line FastAPI shim, hard cap 400 lines, no business logic |
| ingestion | PDF in → chunks, table rows, FailureModes, canonical knowledge out; table-parser is an internal module; indexing + embedding here |
| diagnosis-agent | The diagnosis loop (LangGraph + Postgres checkpoints); retrieval search / hybrid / rerank / knowledge lookup here, and only here |
| twin-verifier | Compiled twins, archetype library (frozen `3c-1.0.0`), async verification jobs |
| Postgres | Jobs, progress, knowledge, sessions/checkpoints, twin definitions, API keys |
| Qdrant | The vector index (`crater_chunks`); ingestion is the sole writer, diagnosis the sole search reader |
| Redis | Celery broker (db 0) + result backend (db 1) + rate-limit token buckets; holds NO durable state |
| harness-scorer (eval-only) | `benchmark/harness.py`, oracle rows, O1/O2/O3; internal network only, never on the public gateway |

Companion files (OpenAPI-ish schemas for each boundary):

- `openapi-gateway.yaml` — public /v1/ surface, error envelope, health/readiness, rate limits
- `openapi-ingestion.yaml` — jobs API, revision status, failure-mode lookup, Mode B, TableRow
- `openapi-diagnosis.yaml` — diagnose sessions/turns, outcome, knowledge reads via ingestion
- `openapi-twin-verifier.yaml` — twins lifecycle + `POST /v1/verify` async job + verdicts

## 1. Cross-cutting rules (apply to every contract)

### 1.1 No shared anything between services

- **No shared in-process calls, no shared imports, no shared memory.**
  A service may not import another service's Python modules. The only
  shared code allowed is the generated API client from these contracts
  and the small `oracle_shared` module — and that module is being moved
  out of twin-verifier into harness-scorer (§9), not shared further.
- **No cross-service DB reads.** A service reads only its own Postgres
  schema. If diagnosis needs knowledge, it calls ingestion's API
  (§3). If the gateway needs API-key state, it reads the gateway-owned
  keys table, not diagnosis's tables. Qdrant is shared *infrastructure*,
  not a service database: ingestion is the sole writer of the index,
  diagnosis is the sole reader for search (§3.4).
- **Stateless services.** Session state lives in Postgres (diagnosis
  sessions + LangGraph checkpoints; ingestion job progress). Redis holds
  only queue contents, rate-limit buckets, and ephemeral locks — never
  the system of record. A service restart must lose no user-visible
  state; a verify/ingest job survives restart because its row is in
  Postgres (§2.3, §5.2).
- **No truncation at any boundary.** Whole chunks, whole rows, whole
  payloads. A size policy is an explicit rejection with a clear error
  (413 + reason), never a silent cut. (Gateway audit P-04; diagnosis
  audit: no `[:800]`, no `[:1000]` on the diagnosis path.)

### 1.2 Identity, idempotency, retries

- Every request carries `X-Request-ID`. The gateway generates one when
  absent and propagates it to every upstream; every service logs it on
  every line (structured JSON logs) and echoes it in the error envelope
  (§6.1). A user-reported failure must be findable in every service it
  touched.
- Every mutating endpoint honours `Idempotency-Key` (1–255 chars,
  required on job creation; accepted on session creation and verify).
  Same key + same payload → the original response, no duplicate work.
  Same key + different payload (ingestion: different SHA-256) → **409**.
  Ingestion is costly: a duplicate upload must never silently re-run it.
- Retries use exponential backoff with jitter, max 3 attempts for
  inter-service calls (infra audit: Celery tasks 3× exponential
  backoff). Retries are safe only because of idempotency keys; a call
  without a key is never retried automatically.
- **Circuit breakers** (Traefik middleware at the edge; httpx-level
  breakers between services): a dependency that fails repeatedly is
  opened, and callers fail fast with 503 + named upstream instead of
  hanging a turn or a worker. A breaker opening is a metric + a log
  line, never silent.
- **Timeouts are explicit on every call.** Inter-service HTTP: 10 s
  default (Qdrant calls 10 s). LLM provider calls: 180 s. Twin verify:
  2 s per hypothesis, 10 s per job (§5). No provider or service call
  without a stated timeout. A timeout is a 503/504 with a named
  upstream, never a hung worker (monolith debt: 78 of 79 handlers are
  synchronous `def`; the split does not fix that by itself — new
  service code uses async handlers for I/O-bound routes).

### 1.3 Errors — one envelope everywhere

```json
{ "error": { "code": "...", "message": "...",
             "request_id": "...", "upstream": "..." } }
```

- `code` is a stable machine string (`retrieval_degraded`,
  `twin_unavailable`, `learning_deferred`, `rate_limited`, …).
- `message` is a plain sentence, never empty.
- `request_id` is the same `X-Request-ID` from §1.2.
- `upstream` names the failing dependency or service. "Something
  failed" is not an acceptable error.
- HTTP statuses: 400 malformed, 404 unknown id, 409 idempotency/SHA
  conflict, 422 semantic validation (e.g. empty hypotheses, empty
  assertions, symptom required), 429 rate-limited (with `Retry-After`),
  503 dependency down or degraded (with the dependency named).

### 1.4 Health vs readiness — per service, no silent fallbacks

| Endpoint | Contract |
|---|---|
| `GET /health` | Liveness only. 200 while the process is up. Checks NO dependency, so a downstream outage never causes a restart loop. Unversioned (infrastructure, not API). |
| `GET /ready` | 200 only when every dependency that service needs is healthy. Otherwise **503 with a per-dependency breakdown naming which check failed**. Unversioned. |

Readiness checks by service (a failure in any listed check → 503):

| Service | `/ready` checks |
|---|---|
| gateway | Postgres (`SELECT 1`), Qdrant via diagnosis-agent, LLM-key configuration present |
| ingestion | Postgres, Qdrant, Redis (broker reachable) |
| diagnosis-agent | Postgres, Qdrant (collection exists and answers), CrossEncoder model loaded |
| twin-verifier | Postgres, twin store (≥1 approved twin for the requested revision — zero approved twins is a readiness failure, never a silent fallback) |
| frontend | Process up only (static serving); its `/ready` does not gate on the gateway |

**No silent fallbacks, anywhere.** The rules that were silent
degradation in the monolith are now loud failures:

- Qdrant down → diagnosis returns **503 `retrieval_degraded`** and
  `/ready` goes red. BM25-only fallback is DELETED (infra audit:
  `bm25.py` replaced by Qdrant sparse vectors / Postgres FTS; see
  §8.3). BM25-only is allowed only behind an explicit
  `?degraded=true` debug flag, never in production.
- CrossEncoder model not loaded → diagnosis `/ready` fails. Never
  silently fall back to fusion order.
- Twin unavailable / timed out → verify job reports `NOT_VERIFIED`
  with a reason; diagnosis proceeds with verification UNAVAILABLE and
  a stated reason. Never "twin unavailable, conclude anyway" (§5.4).

Acceptance test (gateway audit §8): with Qdrant stopped,
`/v1/query` returns 503 in the standard envelope and `/ready`
returns 503 naming Qdrant — while `/health` still returns 200.

### 1.5 Auth, rate limits, observability (pilot scope)

- **Auth:** API keys now, OIDC deferred. Keys are stored in Postgres
  and validated by the gateway shim as Traefik's forwardAuth target.
  Traefik rejects unauthenticated requests before they reach any
  service. Every route group is authenticated by default once /v1/
  lands; public-by-exception must be named per route, never assumed.
  OIDC / Keycloak is DEFER (multi-customer identity is scalability
  base, not a v1 need). Clerk is not used.
- **Rate limits** (interim guardrails until Nishant sets a Phase 5
  load target; Redis token bucket, shared across gateway replicas):

  | Traffic | Limit | On exceed |
  |---|---|---|
  | Diagnosis, per API key | 10 req/min sustained, 20 burst | 429 + `Retry-After` |
  | Reads, per API key | 60 req/min | 429 + `Retry-After` |
  | Ingestion uploads, per API key | 5 uploads/hour | 429 + `Retry-After` |

- **Observability:** Prometheus metrics from the shim (request count,
  latency, status by route and upstream; `/ready` dependency state).
  Structured JSON logs with `request_id` on every line in every
  service. OpenTelemetry tracing is DEFER to Phase 5 (request IDs +
  structured logs give pilot traceability without a tracing backend).
- **CORS:** origins from environment configuration per deployment.
  No origin list is ever compiled into code again.

## 2. Gateway ↔ all services

The gateway is Traefik v3 (routing, TLS, rate-limit, retry,
circuit-breaker middleware, access logs) + a thin FastAPI shim
(**250–350 lines, hard cap 400**). The shim owns only: `X-Request-ID`
generation/propagation, forwardAuth target (API-key validation against
Postgres), `/health` (liveness), `/ready` (aggregation per §1.4),
the uniform error envelope, and the Prometheus metrics endpoint.
**No business logic.** The shim never inspects a diagnosis, a chunk,
or a twin result. It identifies, limits, traces, and forwards. If a
proposed shim feature pushes past 400 lines, the feature belongs in a
service, not in the gateway. Total gateway container budget
~700–800 lines (shim + settings + rate limiting + auth helper +
Traefik YAML + Dockerfile + tests); drifting above it means service
logic has leaked into the gateway — stop and move it.

### 2.1 Routing

- All public routes live under `/v1/`. Health endpoints stay
  unversioned (`/health`, `/ready`).
- Route, retry, and breaker changes are Traefik configuration
  changes (YAML), with no deploy of Crater code.
- The legacy unversioned paths survive as an alias during migration
  only, then are deleted. An alias kept forever is a second,
  unversioned API.
- `POST /v1/evals` is fenced off from the public gateway entirely:
  harness-scorer reaches services on the internal network only.

### 2.2 Public route table (v1)

Kept verbatim from the monolith's handlers, mounted under `/v1/`
(gateway audit §5). Full schemas in `openapi-gateway.yaml`.

| Group | Source (monolith) | Public path | Owner after split |
|---|---|---|---|
| Diagnostics | `routes/diagnostics.py` (15 endpoints) | `/v1/diagnose/...` | diagnosis-agent |
| Ingestion jobs | `routes/ingestion.py` | `/v1/jobs`, `/v1/revisions/{id}/status` | ingestion |
| Products | `routes/products.py` | `/v1/products/...` | ingestion (products + revisions registry) |
| Machine knowledge | `routes/machine_knowledge.py` | `/v1/knowledge/...` | ingestion (read API; see §3.3) |
| Simulation / twins | `routes/simulation.py`, `routes/digital_twin.py` | `/v1/simulation/...`, `/v1/digital-twins/...` | twin-verifier |
| Verify | `routes/verify.py` | `/v1/verify`, `/v1/verify/jobs/{job_id}` | twin-verifier |
| Mode B | `routes/mode_b.py` | `/v1/mode-b/...` | ingestion (jobs) + diagnosis-agent (diagnose) |
| Query | `routes/query.py` | `/v1/query` | diagnosis-agent |
| Schematics | `routes/schematics.py` | `/v1/schematics/...` | ingestion (schematic reads) |
| Visualization | `routes/visualization.py` | `/v1/visualization/...` | ingestion (reads) |
| Learning | `routes/learning.py` | `/v1/learning/...` — **mounted but gated, §2.4** | diagnosis-agent (DEFER extraction) |
| Expert | `routes/expert.py` | **CUT — §2.3** | — |

### 2.3 CUT: expert routes

`/v1/expert/...` does **not exist** in v1 (amendment resolution 3).
The Phase 5 expert trio — `expert.py`, `expert_schema.py`,
`expert_prompts.py` (470 lines) including its routes — is CUT with
the trio, and the frontend expert UI (`ExpertKnowledge.jsx`) is CUT
with it. Any request to `/v1/expert/...` returns **404** in the
standard envelope; no route is mounted, no handler is kept behind
the gateway.

What stays: **expert knowledge** — FailureMode rows and their
provenance — stays inside diagnosis-agent as data (it is the repair
knowledge the loop ranks), not as a capture surface. Expert
*capture* (interviews) is what was cut. The `Expert` /
`ExpertInterview` models kept in `db/models.py` record outcomes as
`pending_review` with no review UI; that is a known leftover, not a
contract — Phase 3 deletes the models or wires the review surface,
it does not resurrect the routes.

### 2.4 DEFER-retained, gated: learning routes

Learning, fleet, downtime, pm, `field_promotion`,
`library_versions` pass the final cut test (they serve future
scalability: multi-machine / multi-customer is the base), so they
stay **DEFER-retained**: code, route mounts, and frontend callers
are kept; only their extraction is sequenced after the core-loop
groups. But (amendment): while DEFER-retained, the learning routes
stay mounted under `/v1/` **gated, not exposed in production v1**
until the pilot produces the field outcomes they learn from.

Contract:

- Gateway default in the production profile: `/v1/learning/...`
  returns **403 `learning_deferred`** with a plain-sentence reason
  ("learning is deferred until the pilot produces field outcomes").
  The routes remain reachable on the internal network for the
  harness and for a future enablement.
- Enablement is an explicit configuration flag, flipped only after
  ≥20 pilot outcomes exist (the threshold set in the refactor
  decision). It is not flipped by deploying a new container.
- Learning is OUT of the diagnosis turn loop: `POST
  /v1/sessions/{id}/outcome` (diagnosis-agent) is record-only — it
  stores the outcome, it does not feed learning in the loop.

### 2.5 Removed from the public v1 surface (diagnosis-agent)

The following monolith endpoints do **not** appear under `/v1/` after
the split (diagnosis audit §"What leaves this container"; Step 6
notes record them still live in the monolith — removal lands in the
Phase 3 API-versioning step, not in this contracts document):

- `verify-repair`, `verify-hypotheses`, `early-tournament`,
  `upstream-walk` → internal turn steps / async twin calls (§5).
- `next-test`, `guided-checks` → folded into the turn response,
  served by the one surviving test picker.
- Safety GET, fleet-patterns GET → deleted as public endpoints;
  context reads move to gateway / knowledge.

A client calling any of them under `/v1/` gets 404. The turn
response (§4) is the only public surface for tests, safety, and
verification status.

## 3. Diagnosis-agent ↔ ingestion

Diagnosis never imports ingestion code and never reads ingestion's
Postgres tables. Every cross-service read is an HTTP call to
ingestion's API, on the internal network, with `X-Request-ID`
propagated and the §1.2 timeout/retry rules. Full schemas in
`openapi-ingestion.yaml`.

### 3.1 Ingest jobs (diagnosis does not call these; the gateway does — listed here because they define the ingestion boundary)

| Endpoint | Contract |
|---|---|
| `POST /v1/jobs` | Multipart upload, PDF only. Fields: `revision_id`, `doc_type` (enum), `idempotency_key` (1–255 chars, required), `ingest_tier2`, `mode` = `prebuilt \| on_demand`. Returns **202** + job id. SHA-256 dedupe: same key + different SHA → 409; same SHA → reuse the existing job, no re-ingestion. |
| `GET /v1/jobs/{id}` | Job state + full progress object (§3.2). |
| `POST /v1/jobs/{id}/retry` | Resume from Postgres progress, retrying only failed chunks — never a whole-manual restart. Returns 202. |
| `POST /v1/jobs/{id}/lazy-ingest` | Promote deferred Tier 2 content for a revision on demand (Mode B, unseen-fault paths). Returns 202. |
| `GET /v1/revisions/{id}/status` | Revision readiness from recorded progress, not a clock guess. The 5-minute stale heuristic is DELETED. |

Monolith status today: `routes/ingestion.py` already has these shapes
(`POST /ingest` 202, `GET /revisions/{revision_id}/status`, `GET
/jobs/{job_id}`, `POST /jobs/{job_id}/retry`, `POST
/jobs/{job_id}/lazy-ingest`) but runs them on `BackgroundTasks` with
filesystem checkpoints. The v1 contract keeps the shapes and replaces
the execution: Celery + Redis (§8.1), Postgres progress (§3.2).

### 3.2 IngestionJob — progress is data, not a log line

```json
{ "id": "...", "revision_id": "...", "document_id": "...", "sha256": "...",
  "idempotency_key": "...", "doc_type": "...", "mode": "prebuilt|on_demand",
  "status": "queued|processing|completed|failed",
  "progress": { "pages_total": 0, "pages_done": 0,
    "chunks_total": 0, "chunks_done": 0,
    "table_rows_total": 0, "table_rows_parsed": 0,
    "tier2_deferred_chunks": 0, "knowledge_failures": [],
    "integration_done": false },
  "created_at": "...", "updated_at": "..." }
```

- `table_rows_parsed` is checkable against the oracle: for Miller it
  is **38**. Progress is verifiable, not just plausible.
- Uniqueness: `(revision_id, idempotency_key)` is unique (already the
  design in the monolith; preserved verbatim into Postgres).
- Progress lives in Postgres (`ingestion_job_progress`), updated by
  Celery task state + heartbeat. Filesystem checkpoints
  (`checkpoints.py`, 58 lines) are REPLACED, not kept alongside.

### 3.3 Knowledge lookup — the read path diagnosis actually needs

| Endpoint | Contract |
|---|---|
| `GET /v1/revisions/{id}/failure-modes?symptom=...` | Queryable FailureModes for a symptom, backed by `table_rows` (§3.5). This is the read path diagnosis uses; it replaces in-process knowledge imports. |
| `GET /v1/revisions/{id}/model` | Canonical machine model snapshot for the revision (entities, typed ports, quantities, failure modes with resolved entity ids, conflicts with precedence). |
| `GET /v1/revisions/{id}/entities` | Entities of the canonical model. |
| `GET /v1/revisions/{id}/facts` | Facts with provenance (source document, IB/revision, variant, precedence). |
| `POST /v1/revisions/{id}/integrate` | Trigger (re-)integration for a revision. Async: 202 + job. |
| `GET /v1/revisions/{id}/verify` | Knowledge-side verification of extracted facts against source. |

Rules:

- Diagnosis calls these over HTTP; it never opens ingestion's
  tables. A knowledge lookup failure is a 503 with ingestion named
  as upstream — diagnosis does not proceed on partial knowledge in
  silence.
- Provenance rides on every fact and every row: source document,
  source type, page, chunk id, `source_revision`, `model_variant`,
  `precedence` (MTB 3 > OEM 2 > generic 1; unknown is `null`, never
  invented). MTB manuals take precedence over Mitsubishi; functions
  may be unavailable per MTB specs — the contract records source
  type, IB, revision, variant, precedence, never ingests flat.
- **Table parser is internal.** There is no standalone table-parser
  service and no public `POST /v1/tables/parse` (amendment
  resolution 4). Parsing is an ingestion-internal function
  (`table_parser.py` stays a module inside ingestion). If another
  service ever needs parsed tables, it calls ingestion's API, not a
  parser service.

### 3.4 Qdrant — who writes, who reads

- Ingestion owns **indexing and embedding**: it writes chunks +
  vectors (dense) and sparse vectors (§8.3) into collection
  `crater_chunks`, with payload indexes and a 10 s client timeout
  with retries. It is the sole writer.
- Diagnosis-agent owns **retrieval search**: hybrid search (RRF
  k=60, `hybrid_top_k` 40, `final_top_k` 8), reranking, and knowledge
  lookup. It is the sole search reader. There is no standalone
  retrieval service and no public or internal `POST /v1/retrieve`
  contract (gateway audit recheck; diagnosis audit's `/v1/retrieve`
  row is superseded — retrieval is an internal module of
  diagnosis-agent, and the gateway only forwards diagnosis traffic).
- Within diagnosis, the same terms still apply: whole chunks, never
  truncated at any boundary; a semantic outage returns 503, never a
  silent degraded result.

### 3.5 TableRow — the row is the unit of truth

```json
{ "id": "...", "job_id": "...", "document_id": "...", "revision_id": "...",
  "table_shape": "troubleshooting_3col|troubleshooting_2col|alarm_table|spec_table",
  "symptom": "...", "symptom_norm": "...",
  "meter_code": "HL.P 001|null",
  "cause": "...", "cause_index": 0, "remedy": "...", "remedy_index": 0,
  "rankable": true,
  "rankable_reason": "concrete|generic_service_fallback|meter_code|settings|technique",
  "twin_expressible": true,
  "source_document": "...", "source_type": "...", "page": 0, "chunk_id": "...",
  "extraction_method": "deterministic_table_parse", "confidence": 1.0,
  "failure_mode_id": "..." }
```

- `meter_code` attaches to its parent row, never a standalone
  symptom (Miller HL.P rows).
- `rankable` + `rankable_reason` stop the scorer and the ranker from
  treating a generic "take it to a service centre" row, a settings
  row, or a technique row as a concrete cause. Miller: 4 generic
  rows + 4 HL.P rows are real rows, just not rankable causes.
- `twin_expressible: false` for settings/technique rows records at
  ingestion time that the twin cannot express that cause — so
  diagnosis holds its conclusion and says so, instead of waiting
  for a verification pass that can never come.
- `confidence: 1.0` with `extraction_method:
  deterministic_table_parse` separates parsed fact from LLM
  inference. A deterministically parsed row is not a guess and must
  not be scored or quarantined as one.
- Once rows are data, retrieval returns a row instead of a 6,452-char
  chunk, diagnosis ranks causes instead of passages, and the harness
  diffs 38 expected rows against 38 parsed rows exactly.

## 4. Diagnosis-agent — public contract (frontend ↔ gateway ↔ diagnosis)

Full schemas in `openapi-diagnosis.yaml`. The v1 contract versions
the EXISTING monolith surface under `/v1/diagnose/...` — handlers
move verbatim (gateway audit §5). The diagnosis audit's proposed
`POST /v1/sessions` + `POST /v1/sessions/{id}/turns` redesign is
**not adopted for v1**: it would be a second diagnosis API alongside
the one the harness and frontend already use. If it is ever adopted,
it is a new API version (`/v2/`), not a silent rename.

### 4.1 Start a session

`POST /v1/diagnose/start` → 201 (session created + turn 1 run).

Request — `StartDiagnosticRequest`:

```json
{ "product_id": "...", "revision_id": "...",
  "symptom": "...",
  "mode": "prebuilt|on_demand",
  "observations": [ { "input": "...", "input_kind": "symptom|observation|measurement" } ],
  "partial_model": false,
  "idempotency_key": "..." }
```

- **Re-ordered Step 3 (schema change first):** `observations` is
  accepted on start **only after the diagnosis schema change lands**
  (the `RespondRequest`-style observation shape becomes part of
  `StartDiagnosticRequest`). Until that change ships, the frontend
  sends symptom only on start and observations via `/respond`; a
  start request carrying `observations` before the change is
  rejected with **422** (`observations_not_supported_yet`), not
  silently dropped. The frontend must not send observations on start
  until the schema change is live — this ordering is part of the
  contract, not a footnote.
- `mode: on_demand` (Mode B) with no top-case registry for the
  product → **409** carrying `missing_pages` and `wait_message`.
- Unknown product/revision → **404**. Symptom missing/empty → **422**.
- Retrieval failure → **503 `retrieval_degraded`**; the session does
  not proceed on partial evidence in silence.

Response — `DiagnosticSessionView` (turn 1):

```json
{ "session_id": "...", "turn": 1,
  "hypotheses": [ { "cause": "...", "component": "...", "value": "...",
    "confidence": 0.0, "rationale": "...",
    "supporting_evidence_chunk_ids": ["..."],
    "citation_status": "grounded|guess" } ],
  "next_step": { "...": "..." },
  "discriminating_test": { "...": "..." },
  "verification": { "status": "pending|validated|rejected|cannot_express|not_verified|unavailable",
                    "reason": "..." },
  "safety": { "...": "..." },
  "retrieval": { "chunks_returned": 0, "failure_modes_returned": 0, "degraded": false } }
```

Server-side rules enforced on every turn (not in the prompt):

- Hypotheses: **3 to 5, never 1.** A single-guess session is a
  contract violation (Phase 1 gate: 0/17 Miller single-guess).
- `next_step` never repeats anything already in the asked sets
  (`asked_causes`, `asked_tests`).
- Uncited hypotheses are labelled `guess`, never presented as
  grounded. A Miller-15-style zero-citation result is impossible:
  citation intersection with retrieved chunk IDs is enforced.
- A conclusion is allowed only with verification VALIDATED;
  otherwise it is demoted to a question and labelled UNVERIFIED.
- Turn 8 without a validated conclusion returns the `ending`
  object — never a bare stop. (No `escalated` status and no
  auto-escalation: inconclusive sessions end with best guesses,
  next test, what's missing.)

### 4.2 Continue a session

`POST /v1/diagnose/{session_id}/respond` — `RespondRequest`:

```json
{ "input": "...", "input_kind": "symptom|observation|measurement",
  "answer_to_test": "..." }
```

Response: same shape as §4.1, plus `eliminated_hypotheses`,
`asked_causes`, `asked_tests`, and (when the session ends) an
`ending` object:

```json
{ "ending": { "reason": "turn_limit|cannot_express",
  "ranked_guesses": [ "..." ], "discriminating_test": { "...": "..." },
  "missing": "..." } }
```

`cannot_express` here is the twin terminal path (§5.4): the ending
carries ranked best guesses, a discriminating manual test, and what
is missing. A bare "unavailable" is never an ending.

### 4.3 Session reads + outcome

| Endpoint | Contract |
|---|---|
| `GET /v1/diagnose/sessions?product_id=&revision_id=` | List sessions for a product revision, most recent first. Registered before `/{session_id}` so `sessions` is never read as a session id. |
| `GET /v1/diagnose/{session_id}` | Full session state, including asked sets, eliminated hypotheses, verification status. |
| `GET /v1/diagnose/{session_id}/context` | Session context (timeline, evidence) for restore. |
| `POST /v1/diagnose/{session_id}/outcome` | Minimal, record-only. Stores the outcome; does NOT feed learning in the loop (learning deferred, §2.4). |
| `GET /v1/diagnose/{session_id}/outcome` | Read the recorded outcome. |

### 4.4 Query (retrieval read, diagnosis-owned)

`POST /v1/query` — retrieval search over the revision's knowledge.
Request: query text, `product_id`, `revision_id`, `doc_type`,
`top_k` (default 5). Response: whole items (chunks / table rows /
knowledge), `degraded: false`. Retrieval failure → **503
`retrieval_degraded`**. This is the public read of the retrieval
module that lives inside diagnosis-agent (§3.4); it is not a
separate retrieval service.

### 4.5 Diagnosis-agent internals the contract depends on

- **Rank, then ask** (Phase 1 Step 3, kept): turn 1 is two
  structured-output calls — rank (symptom + observations + whole
  retrieved rows → ranked hypotheses), then ask (discriminating
  test from the single picker). The discriminating test is present
  on every turn 1.
- **CrossEncoder reranker** (Phase 1 Step 2, kept):
  `cross-encoder/ms-marco-MiniLM-L-6-v2`, whole text, no `[:1000]`
  truncation, `rerank(query, candidates, top_k)` signature.
  Readiness fails if the model has not loaded (§1.4).
- **LangGraph loop + Postgres checkpoints** (Phase 1 Step 5, kept):
  explicit asked sets, safety inside `run_turn`, twin verification
  via the thin async client (§5). SQLite checkpoints are gone with
  the Postgres move (§8.2).
- **Retrieval inside diagnosis-agent only.** Indexing/embedding
  stay in ingestion. No service other than diagnosis-agent performs
  retrieval search.

## 5. Diagnosis-agent ↔ twin-verifier (async verify)

Full schemas in `openapi-twin-verifier.yaml`. The hard rule of the
split: **verification never blocks diagnosis ranking.** Diagnosis
ranks, asks, and ships its turn. The verdict attaches when the job
completes. Twin audit p.11; monolith shape already in
`routes/verify.py` (202 + poll, in-memory `_JOBS` dict — replaced
by Postgres + Redis at the split, §5.2).

### 5.1 Create a verify job

`POST /v1/verify` → **202**.

Request — `VerifyRequest`:

```json
{ "idempotency_key": "...",
  "twin_id": "...", "revision_id": "...", "session_id": "...",
  "hypotheses": [ { "cause": "...", "repair_name": "...",
    "fault_component_id": "...", "failure_mode_name": "..." } ],
  "timeout_ms": 2000 }
```

- `hypotheses` empty → **422** ("at least one hypothesis is required").
- `timeout_ms` default 2000 (per hypothesis; job cap 10 s — §5.2).
- The idempotency key ensures a retried turn never double-verifies.

Response — 202:

```json
{ "job_id": "...", "status": "queued|running",
  "twin_id": "...", "estimated_ms": 0 }
```

### 5.2 Job execution + polling

`GET /v1/verify/jobs/{job_id}` →

```json
{ "job_id": "...", "status": "queued|running|succeeded|failed",
  "results": [ { "...verdict result, §5.3..." } ],
  "counts": { "validated": 0, "rejected": 0, "cannot_express": 0,
              "not_verified": 0 } }
```

- Unknown job → **404**.
- Implementation: a Celery queue (Redis broker db 0, result backend
  db 1) with the job row in Postgres. A worker runs the existing
  tournament + verification logic on an **isolated copy** of the
  twin. The in-memory `_JOBS` dict dies at the split — no caller may
  assume a job survives a restart today, and after the split a job
  DOES survive a restart (that is the point of the Postgres row).
- Timeouts: **2 s per hypothesis, 10 s per job.** On timeout the job
  reports `NOT_VERIFIED` with reason `"twin verification timed out"`,
  and diagnosis proceeds. A timed-out job is `succeeded` with
  `NOT_VERIFIED` results, not `failed` — a job whose twin was
  unavailable still succeeds, with every result `NOT_VERIFIED` and a
  reason. `failed` is reserved for the job itself erroring (bad
  twin id, worker crash), not for a verdict.
- Diagnosis polls (or receives a callback); it never blocks a turn
  waiting. A session continues with verification UNAVAILABLE and a
  stated reason when the twin is down.

### 5.3 Verdicts — four, and what diagnosis may do with each

Every result carries: `cause`, `verdict`, a plain-sentence `reason`
(never empty), `fault_component_id` where known, `symptom_path`,
`rules_used` (transition ids), `trace`, `twin_id`,
`twin_library_version`, `verified_at`. Plus, for VALIDATED,
`source_ids` and evidence pages.

| Verdict | Meaning | Diagnosis rule |
|---|---|---|
| `VALIDATED` | The fault reproduced the symptom and the repair restored healthy. The result MUST carry the trace, the `rules_used` transition ids, `source_ids`, and evidence pages. **No result without `rules_used` may be VALIDATED — enforced by a contract test.** | Diagnosis may promote the cause (+0.1). A conclusion requires VALIDATED (or the §5.4 terminal path). |
| `REJECTED` | The twin ran the repair and the symptom did not recover. Evidence against the cause — it never deletes the hypothesis. | Diagnosis may demote (×0.5). |
| `CANNOT_EXPRESS` | No component can carry the cause; a matched component has no rule describing the fault; or a reviewed repair action references components the twin lacks. | **Terminal for that cause:** no further observation can make this twin express it. Diagnosis ends that line with ranked best guesses, a discriminating manual test, and what is missing. Never changes ranking. |
| `NOT_VERIFIED` | Ambiguity; a family with no repair-action registry yet; an experiment that could not complete; or a twin unavailable / timed out. Explicitly NOT a rejection and NOT a block on ranking. | Never changes ranking. Diagnosis proceeds. |

- `CANNOT_EXPRESS` and `NOT_VERIFIED` never change ranking.
- A conclusion requires VALIDATED, or the explicit
  `CANNOT_EXPRESS` terminal path — never a bare "twin unavailable,
  conclude anyway".
- Empty assertions return **400**, never validated (closes the
  vacuous-validation trap).

### 5.4 CANNOT_EXPRESS is terminal and explained

This makes today's escape hatch (`agent.py` cannot-express path) a
first-class outcome. The result's `reason` must say, in a plain
sentence, which of the three cases applies (no component / no rule
for the fault / repair references missing components) and what is
missing. Diagnosis then ends that line with ranked best guesses, a
discriminating manual test, and what's missing — it does not wait
for a verification pass that can never come. This is also why
`TableRow.twin_expressible: false` (§3.5) exists: ingestion can
already record that a cause is inexpressible, so the turn loop can
hold its conclusion up front.

### 5.5 Twin lifecycle (twin-verifier owns)

| Endpoint | Contract |
|---|---|
| `POST /v1/twins/{id}/compile` | From a canonical-model snapshot id. Returns twin id, revision, model version, status `draft`, warnings, transition count, binding coverage, seed applied. |
| `POST /v1/twins/{id}/approve` | Moves a draft twin to active. The existing explicit human gate stays. |
| `POST /v1/twins/{id}/experiments` | Keep the existing `ExperimentRequest` shape. Empty assertions → 400, never validated. |
| `POST /v1/twins/{id}/hypotheses` | Keep the existing `HypothesisBatchRequest` shape. |
| `GET /v1/twins/{id}/context`, `GET /v1/twins/{id}/visual` | Twin context + visual (moves with its endpoint to twin-verifier; frontend rendering stays frontend). |
| `GET /v1/revisions/{id}/twin` | The active twin for a revision. |

- **Archetype library frozen: `3c-1.0.0`** (`ARCHETYPE_LIBRARY_VERSION`
  in `backend/simulation/archetypes.py`). Every verify result carries
  `twin_library_version: "3c-1.0.0"`. The library is versioned, not
  edited in place; pin/rollback/recompile live in the DEFER-retained
  learning surface (§2.4), not in the turn loop.
- Twin-verifier owns: compiled twin definitions and transitions, the
  archetype library and its version, verification jobs and their
  results, its own Postgres schema. It does NOT own: canonical-model
  extraction (ingestion), retrieval or the diagnosis session
  (diagnosis-agent), the LLM gateway (no LLM import survives the
  slim), the oracle (harness-scorer), or frontend layout.
- The twin is a deterministic state machine, not a physics solver:
  only transitions from explicit `simulation_rules` (or approved
  library templates). If a library cannot return `CANNOT_EXPRESS`,
  cannot attach trace/transition ids and source evidence, it cannot
  be swapped in. (Twin audit: OpenModelica/FMU cannot emit
  `CANNOT_EXPRESS` — rejected as a swap on those grounds.)

## 6. Frontend ↔ gateway

- The frontend talks **ONLY to the gateway**. No direct calls to
  ingestion, diagnosis-agent, twin-verifier, Postgres, Qdrant, or
  Redis. Base URL is injected at runtime (not compiled in); all
  calls use `/v1/` paths.
- The API client is **generated from the /v1/ OpenAPI schema**
  (orval / openapi-typescript), not hand-written. The hand-written
  `client.js` god-module (150 lines) shrinks to transport only
  (~65 lines: base URL, JSON encode/decode, one error type,
  timeouts, no 404-to-null). The generated client is the contract;
  the frontend cannot drift from it by hand-editing a fetch wrapper.
- Every frontend call: sends `X-Request-ID` (generated per call),
  sends the API key, honours the §1.3 error envelope (shows
  `error.message`, never a bare status code), handles 429 with the
  `Retry-After` wait, and treats 503 as "dependency down, named
  upstream" — not as an empty result. No 404-to-null: a missing
  resource is an error state, not an empty screen.
- Timeouts on every frontend call (the frontend audit: no timeouts
  today). A call that times out surfaces as a retryable error, not a
  hang.
- **Expert UI is CUT** (`ExpertKnowledge.jsx`, `.expert*` CSS) with
  the expert routes (§2.3). **Learning UI is kept but gated** with
  the learning routes (§2.4): it is not exposed in production v1
  until the pilot gate flips.
- Session restore uses `GET /v1/diagnose/sessions` + `GET
  /v1/diagnose/{session_id}` + `GET /v1/diagnose/{session_id}/context`
  (§4.3). Restoring loads the full session and clears local
  verification/tournament/outcome state, which is not persisted
  (only timeline entries are).
- No `/health` or `/ready` use on the client side today (frontend
  audit P-18/P-19): the frontend does not poll readiness; the
  gateway's 503 envelope is the signal.

## 7. Harness-scorer (eval-only) ↔ services

- Harness-scorer is **eval-only and never runs in production**. It
  reaches services on the internal network only; `POST /v1/evals`
  is fenced off from the public gateway entirely (§2.1).
- It calls the same `/v1/` contracts as the frontend (diagnose
  start/respond, verify jobs, ingestion status) — no backdoor
  endpoints, no answer key in the request path
  (`assert_no_answer_key` stays in the harness).
- Oracle gate runs after EVERY Phase 3 extraction, so a regression
  is attributable to exactly one move. The gate is the Phase 1 gate
  as frozen (§11): Miller 22/38, OMNIPRO 24/35, discriminating test
  17/17 Miller / 8/9 OMNIPRO, citations 79/79 Miller / 35/35 OMNIPRO.
- **Standing rule (Nishant, 23 Nov 2026 15:49 IST): before running
  any test with Muse as the LLM backend, tell Nishant first — he
  will have another LLM do it instead. Do NOT run any Muse-as-backend
  gate run autonomously.** No mock-server run with model `muse-spark`,
  no Muse tokens on a gate run, until he supplies the other LLM.
  The post-extraction oracle gates in Phase 3 are therefore READY
  AND WAITING for his other LLM; they are not run as part of this
  contracts work. See `PHASE2_BUILD_NOTES.md` (refactor Phase 2
  section) for the full record.

## 8. Data decisions (decided here, executed in Phase 3)

### 8.1 Celery + Redis replaces BackgroundTasks — everywhere

- Broker: Redis db 0. Result backend: Redis db 1. (Amendment
  resolution 2: Redis Streams was a gateway-only recommendation and
  is NOT adopted. The gateway audit §7/§10 Streams lines are
  superseded.)
- Progress: Postgres `ingestion_job_progress` (Celery task state +
  heartbeat), replacing `BackgroundTasks` and filesystem checkpoints.
- Tasks retry 3× with exponential backoff. Visibility via Flower or
  `celery inspect`. Workers scale independently of the API services.
- Redis holds no durable state by design: queue contents are
  reconstructible from `ingestion_jobs` rows in `queued` /
  `processing` status. That is what makes Redis replaceable
  infrastructure rather than a second database.
- **What this replaces in the monolith:** `ingest_document` (the
  only async handler, `routes/ingestion.py:93`), `retry`, and
  `lazy-ingest` BackgroundTasks paths; the in-memory verify `_JOBS`
  dict (§5.2); the ingestion `checkpoints.py` module.

### 8.2 Postgres replaces SQLite before the next ingestion round

- SQLite is fine for local dev and was to be swapped for Postgres
  in a real pilot. That swap never happened. It happens now, before
  any further ingestion — **re-ingest from PDFs, not dump/restore.**
  There is no SQLite→Postgres migration of ingested data: the next
  ingestion round runs against Postgres from the start, and the
  canonical models are rebuilt from the source PDFs.
- Postgres becomes the default session store as services split
  (`db/session.py` `get_session` shape kept; managed Postgres —
  Neon / Supabase / RDS, pick at build; self-hosted `postgres:16`
  container for dev/eval). Add `check_db()` (`SELECT 1`) for
  `/ready`.
- Migrations: Alembic replaces `migrations.py` (48 lines). Indexes
  are foreign-key / single-column only today — add the composite
  `(revision_id, chunk_type)` index the retrieval path needs.
  Revision scoping survives the move unchanged.
- `config.py`: `extra="ignore"` → `extra="forbid"` per service, so
  a typo'd variable fails startup instead of silently misconfiguring
  the service. Each service documents and validates only the
  variables it actually reads; one `.env.example` becomes one per
  service.
- LangGraph checkpoints: Postgres checkpoints (Phase 1 Step 5, live
  in the monolith via `langgraph-checkpoint-postgres`) are the
  contract; SQLite checkpoints die with the SQLite move.
- Note: `uv.lock` is stale (0 langgraph mentions despite
  `pyproject.toml` declaring langgraph + checkpoint packages).
  Phase 3 regenerates the lock per service; do not carry the stale
  lock into the split.

### 8.3 Qdrant sparse vectors / Postgres FTS replaces BM25

- `bm25.py` (67 lines) is REPLACED — live code, not dead code — by
  Qdrant sparse vectors / full-text index: one retrieval owner
  (diagnosis-agent), and Qdrant supports BM25-style sparse retrieval
  natively. **No Python BM25 code remains**, and the per-query
  full-corpus rebuild dies with it.
- Fallback if Qdrant sparse is unavailable: Postgres full-text
  search (`tsvector` on `chunks.content`), not a resurrected
  `bm25.py`, and not a silent fallback — it is an explicit,
  logged, degraded mode behind the `?degraded=true` debug flag
  only (§1.4).
- Qdrant hardening at the split: API key (`QDRANT_API_KEY`, new env
  var — unauthenticated today), client timeout 10 s with retries,
  payload indexes, pooled client (today `vector_store.py`
  constructs a new `QdrantClient` on every `get_client()` call).
  Also fix the open `get_client()` NO_PROXY crash (Step 6 notes):
  `sanitize_proxy_env_for_httpx()` must run before Qdrant client
  construction in `vector_store.py`, as it already does in
  `embeddings.py` and `reranker.py`.
- Hybrid fusion stays: RRF k=60, `hybrid_top_k` 40, `final_top_k` 8.

## 9. Oracle relocation pre-step (contract fix)

The oracle belongs to harness-scorer, not to twin-verifier — but it
could not move, because kept production code imported two helpers
from it. The pre-step is DONE and is recorded here as a contract
precondition for Phase 3:

1. **Extract first (done, Phase 1 Step 6):**
   `backend/simulation/oracle_shared.py` (47 lines) —
   `normalize_text` + `load_fault_injections`, moved verbatim.
   `backend/simulation/repair_actions.py:31` now imports from
   `oracle_shared`; `oracle.py` re-exports them so existing
   importers keep working. Verified in this pass: the only
   production importer of those two helpers is `repair_actions.py`,
   and it already imports from `oracle_shared`.
2. **Then move (Phase 3, gateway-first order §10):** `oracle.py`
   moves to harness-scorer, and the benchmark scripts that import
   it move with it:
   `scripts/run_benchmark_oracle.py`, `scripts/run_phase3a_baseline.py`,
   `scripts/run_phase3b_oracle.py`, `scripts/run_phase3c_oracle.py`,
   `scripts/run_phase4_offtable.py` (all import `run_oracle` /
   `load_vocabulary` / `normalize_text` from
   `backend.simulation.oracle`). The harness (`benchmark/harness.py`)
   is already harness-scorer-owned. No production module may import
   from harness-scorer after the move — a grep for
   `backend.simulation.oracle` outside harness-scorer is a Phase 3
   gate check.
3. **Scorer note (frozen, Phase 0):** `normalize_text` in the
   harness replaces punctuation with a space (the Phase 0 fix);
   citations are chunk-id presence, page NOT verified; rankable vs
   unrankable rows are reported separately. Those are harness
   behaviours, not service contracts, and they do not change in
   Phase 3.

## 10. Extraction order for Phase 3 (gateway first)

Corrected order — the gateway audit's Step 4 order ("table-parser,
ingestion, diagnosis-agent…") is superseded by the amendment
(table-parser is not a service) and by this decision:

1. **Gateway first** — version the API under `/v1/` + thin shim
   inside the monolith (gateway audit Steps 1–2: middleware,
   `/health` + `/ready`, rename `llm/gateway.py` →
   `model_router.py`, kill the silent BM25 fallback, harness +
   frontend base URL to `/v1/` in the same change). Oracle gate
   after this step (waiting on Nishant's other LLM, §7).
2. **Ingestion** — Celery + Redis, Postgres progress + jobs API,
   TableRow as data, table-parser stays an internal module.
   Re-ingest from PDFs against Postgres (§8.2).
3. **Diagnosis-agent** — retrieval search/hybrid/rerank/knowledge
   lookup inside; LangGraph + Postgres checkpoints; thin async
   verify client; the §2.5 public-surface removals land here.
4. **Twin-verifier** — async verify jobs on Celery + Postgres;
   archetype library frozen `3c-1.0.0`; oracle.py moves out to
   harness-scorer (§9).
5. **Frontend last** — generated OpenAPI client, runtime-injected
   base URL, expert UI cut, learning UI gated.

**MINUS table-parser: it is not a service and never appears in the
extraction order.** Full oracle run after each single extraction, so
a regression is attributable to exactly one move.

## 11. The accepted-tie decision (record)

Nishant approved moving on after Phase 1 (23 Nov 2026). The Phase 1
gate as written FAILED on one criterion — Miller 22/38 ties the
frozen Muse baseline, it does not beat 22 — and the tie is
**accepted for proceeding**. Everything else in the gate passed:
Miller beats Gemini 19/38 by 3; OMNIPRO case 1 stays 4/4; OMNIPRO
24/35 overall; discriminating test 0/17 → 17/17 Miller, 8/9 OMNIPRO
(omni-04 at 0.0, a picker-test string-match miss, not a gate
criterion); citations 79/79 Miller, 35/35 OMNIPRO; single-guess 0/17.

The known ranking/scorer bottleneck is a follow-up, NOT a blocker
for the split: the miller-16 split-cause loss (Phase 1 Step 4 split
a combined "gas solenoid valve not operating, or loose or broken
wires to gas solenoid" cause into two, and only one survived the
top 5 — a real cost of the split, −1, cancelling the miller-02 +1),
plus the HL.P and generic-service rows (8 unrankable Miller rows
that are real rows, not rankable causes). Do NOT redo Phase 1
ranking work in Phase 2 or Phase 3. The ranking/scorer work is
tracked separately and gated on its own numbers.

## 12. What was NOT done here

- No implementation code was changed. No service was extracted. No
  container was built. No Celery/Redis, Postgres migration, or twin
  change was started.
- No oracle gate run was performed in this step — and per the
  standing rule (§7) none may be run with Muse as the LLM backend
  until Nishant supplies the other LLM.
- No ranking/scorer work (see §11).
- The diagnosis audit's `POST /v1/sessions` redesign, the gateway
  audit's Redis Streams recommendation, the gateway audit's
  `POST /v1/tables/parse` contract, and the gateway audit's
  `POST /v1/retrieve` contract are recorded as superseded in §10
  above — they are not implemented, and they are not silently
  dropped from the record either.
