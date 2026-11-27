# Phase 2 build notes — canonical model + Feed Tensioner dedupe + session restore

**Date:** 13 Nov 2026. **Base:** Phase 1 (`project-review-phase1-patched.zip`, this tree).
**Package:** `~/workspace/crater-phase1/project-review-phase2-patched.zip` (this tree).
**Design:** `phase2-implementation.md` in this goal folder.

## The dedupe cause found (Phase 0, design §2)

Read the code before writing the fix, as instructed. The v9 narrow veto
was **not** the cause — it was never reached on the live run.

In `backend/knowledge/code_first_integration.py`, `_group_entities`
grouped by **(normalized name, normalized entity_type)** and blocked a
merge with `_props_conflict`, which returned True when **any** non-empty
property differed — including the paraphrased `function` prose
("Applies tension to wire feed" vs "Applies pressure to wire feed").
The v9 narrow veto (`_hard_identity_conflict`: only part_number /
location_description / voltage) exists **only** in `apply_adjudication`,
the LLM adjudication path, which runs only if
`build_code_first_payload` emits `conflicting_same_name` clusters and
the LLM call succeeds. On the live run the merge was left to that
optional, fallible LLM call, which never fired — so five Feed Tensioner
rows stayed five entities. A second split: the grouping key included
entity type, so the one `entity_type="tensioner"` row could never merge
with the five `"component"` rows.

The same bug lived in the legacy path: `_merge_rows_by_identity` in
`backend/knowledge/global_integration.py` used the same any-property
`_props_conflict`.

**Fix (shared rule, both paths):** new `backend/knowledge/identity.py`
— `hard_identity_conflict` (part_number / location_description /
voltage only; empty values are not a claim), `entity_types_compatible`
(identical, or either side generic `component`/`other`/`unknown`;
two different specific types block). Grouping is now by normalized
name only; a merge is blocked only by hard-identity conflict, type
incompatibility, `_part_compatible`, or `_same_list_distinct`.
`_props_conflict` in `global_integration.py` now delegates to the
shared rule. Canonical ids are unique within a name group (index
suffix fallback — the known Welder collision can no longer arise).
Merges are never silent: the merged entity keeps all `source_ids` +
evidence and records `merged_from`.

**Result:** the crater-9 shape (6 rows) merges to **1 Feed Tensioner**
with all 6 source ids, no cluster, no conflict. The 4 expert-seed
rules then resolve at the compiler level (see below).

## What Phase 2 now does

1. **Typed ports** (`backend/knowledge/machine_model.py`):
   `MachineEntity.typed_ports: list[MachinePort]`; `MachinePort` gains
   `domain`/`unit`. The old `ports: list[str]` is a validator-derived
   view, not a second truth. `code_first_integration.py` attaches
   `typed_ports` to payload entities (so the snapshot JSON carries
   them); `global_integration._model_from_payload` builds ports first
   and falls back to deriving typed ports from the model-level port
   list, so v12 snapshots load and compile unchanged (0-transition
   warning preserved).
2. **Parameters as first-class quantities**: `MachineQuantity` gains
   `range_min`/`range_max`/`source_kind` + `is_bound` (value non-empty).
   `source_kind` is derived from the quantity's evidence in
   `_model_from_payload` (`spec_page` / `parameter_manual` / `data_book`
   / `nameplate` via `backend/knowledge/provenance.py`). Unbound stays
   unbound; coverage names it.
3. **`library_type` field** (`backend/knowledge/library_types.py`):
   alias map (wire_feeder / dc_motor / relay) with unique-match-only
   binding — `bind_library_type` on name only; `assign_library_types`
   sets it in place and returns a `{bound, ambiguous}` report.
   Deliberately not bound: "Feed Tensioner" (a part of the feeder),
   bare "Motor", name "K1" with type relay. The coarse 7-bucket type
   is untouched. The 3 contracts themselves are Phase 3, not built.
4. **Source + precedence on every fact** (`provenance.py`,
   `evidence.py`): `MachineEvidence` gains `source_revision`,
   `model_variant`, `precedence` (all optional, None = unknown, never
   invented). `precedence_for_source` — explicit origin wins, then MTB
   markers, then manufacturer manual → `oem`, else `generic`
   (MTB 3 > OEM 2 > generic 1). `parse_source_doc` extracts the IB
   number. `MachineConflict` gains `precedence: dict[str, str]`.
   `pipeline._stamp_provenance` stamps precedence (derived from the
   document title) + revision label + product model on every new
   evidence item and fills missing fields on existing evidence.
   Conflicts are recorded with precedence, never silently resolved.
   *Bug found while testing:* the IB regex had a trailing `\b`, so
   real Mitsubishi filenames (`ib1501808engc_...`) never matched.
   Fixed to `r"\bIB[-\s]?(\d{7})(?!\d)"` — matches with or without a
   separator, still rejects 8-digit runs.
5. **Coverage computed from the model** (`repair_cases.model_coverage`):
   entities with typed ports / library_type, quantities bound/unbound
   (unbound named), failure modes with resolved entity_ids, conflicts.
6. **Frontend session restore**: new `GET /diagnose/sessions`
   (`backend/api/routes/diagnostics.py`, registered **before**
   `/{session_id}` so "sessions" is never read as a session id;
   `product_id`/`revision_id` filters, most recent first). Client
   `listDiagnostics` (`api/client.js`, re-exported in
   `api/diagnostics.js`). `DiagnosticWorkspace.jsx` shows a "Past
   sessions" card on the Diagnose view (symptom, status, turns,
   confidence, date) with Restore — restoring loads the full session
   via `getDiagnostic` + `getDiagnosticContext` and clears local
   verification/tournament/outcome state, which is not persisted
   (only timeline entries are). A "Sessions" button in the session
   header leaves the session open on the backend and returns to the
   list. No new backend endpoint was needed for the restore itself:
   `GET /diagnose/{session_id}` already existed.
7. **Machine Model view** (`MachineModelViewer.jsx`): EntityDetail
   shows a `library:` badge, typed-port chips (name · direction ·
   domain · unit, falling back to the old string ports for v12
   snapshots), and a Parameters section (value + unit + source_kind,
   or "unbound"). Evidence cards show `precedence` and `source_revision`
   when recorded.

## Seed-resolution result after dedupe

Using the real seed file (`backend/simulation/golden_rules/omnipro_wire_feed.json`,
Harbor Freight / OMNIPRO 220) through the real `apply_expert_seed` +
`compile_machine_model`:
- After dedupe: **4/4 seed states applied, 4/4 seed rules applied,
  0 skipped; the twin compiles with 4 transitions**
  (`seed:feed-tension-too-tight`, `seed:feed-tension-ok-restores-feed`,
  `seed:wingnut-loose-spool-spins`, `seed:wingnut-tight-stops-spool-spin`).
- Regression test with 7 duplicate Feed Tensioners: the tensioner
  state and both tensioner rules skip with "7 entities named
  'Feed Tensioner'; ambiguous", never guessed; the Wingnut rules still
  resolve. This proves the dedupe fix is what unblocks the seed.

## Tests

Baseline (verified before building, same as Phase 1): **217 passed,
2 failed, 1 collection error** — both failures in
`tests/test_phase8a_integration.py` and the collection error in
`tests/test_phase8a_hardening.py` (`invalid_fact_keys` import) are
pre-existing on pristine v12.

Final: **243 passed, 2 failed, 1 collection error** — the same 2
pre-existing failures and 1 pre-existing collection error; no new
failures, no degradation. New tests: **26 passing** —
`tests/test_phase2_canonical_model.py` (13), `tests/test_phase2_dedupe.py`
(8), `tests/test_phase2_sessions.py` (4), plus
`test_canonical_ids_unique_within_name_group` in
`tests/test_integration_code_first.py`.

Two pre-existing tests asserted the old (wrong) behaviour and were
updated, as the task allows — both are named explicitly here:
- `test_adjudication_merge_allowed_when_only_function_differs`: now
  asserts the five tensioner rows merge in code with no cluster and
  no LLM call, keeping all 5 source ids and `merged_from` (5).
- `test_adjudication_merge_skipped_when_canonical_ids_collide`:
  rewritten — name-only grouping merges the universal/legacy Welder
  rows by part number and the id-uniqueness fallback guarantees
  distinct ids, so the collision no longer arises (2 welders,
  `entity:welder-57812` + `entity:welder-2`); adjudication merge then
  resolves them by id + source rows with no source row lost.
The v9 guards that must not break still pass unchanged:
`test_adjudication_merge_still_vetoed_on_location_or_voltage`,
`test_exact_duplicates_merge…`, `test_prose_fan_does_not_merge_into_first_parts_fan`,
`test_unique_relation_rewrites…`, `test_merge_same_name_with_conflict_keeps_two_fans`,
`test_merge_same_name_no_conflict_merges_welder_duplicates`.

Frontend: `npm ci` + `npm run build` (vite 8.3.0) succeeds — 33 modules,
no errors. `node_modules/` and `dist/` were removed before packaging;
they are regenerable from the pinned lockfile.

The v8/v9/v10 offline suites (15/15, 17/17, 27/27) are **not scripts
in the repo** — they were run by the author against a crater-8 DB copy
— so they are not runnable here. Their pytest equivalents are the test
files above, and the full suite passes with no new failures.

## Files changed / added

Backend (new): `backend/knowledge/identity.py`,
`backend/knowledge/library_types.py`, `backend/knowledge/provenance.py`.
Backend (edited): `backend/knowledge/machine_model.py`,
`backend/knowledge/evidence.py`, `backend/knowledge/code_first_integration.py`,
`backend/knowledge/global_integration.py`,
`backend/knowledge/repair_cases.py`, `backend/ingestion/pipeline.py`,
`backend/api/routes/diagnostics.py`.
Frontend (edited): `frontend/src/api/client.js`,
`frontend/src/api/diagnostics.js`,
`frontend/src/components/DiagnosticWorkspace.jsx`,
`frontend/src/components/MachineModelViewer.jsx`.
Tests: `tests/test_phase2_canonical_model.py`,
`tests/test_phase2_dedupe.py`, `tests/test_phase2_sessions.py` (new);
`tests/test_integration_code_first.py` (2 tests updated, 1 added).

## What was NOT built (per design §1)

Library v1 contracts + compiler v2, backtracking walk, verify/tournament
ordering, adjustment-type cause mapping ("insufficient pressure" is not
a component), Mode B on-demand path, STEP import, OCR, any new
dependency, any new LLM call. Budget held: **0 new LLM calls**.

## What was not verified (needs Nishant's side)

- End-to-end ingestion with his LLM + Qdrant (same as Phase 1: the
  venv has no sentence-transformers / qdrant / LLM deps).
- The live check below, on a fresh OMNIPRO upload/job (not Retry).
- The new frontend cards in a running browser — `vite build` verifies
  compilation only, not rendering.

## Live check (design §6, verbatim)

1. Compile the twin: expect **1 Feed Tensioner** and the **4 seed transitions**
   (the dedupe fix unblocking the seed) — report the transition count.
2. Diagnose p42 as before; navigate Diagnose → Twin → Diagnose and confirm
   the session is still there, with its timeline and conclusion intact.
3. Run the tournament on the session: Feed Tensioner should now resolve
   (no longer "matches 7 twin components").
4. Inspect a p42 component in the Machine Model view: typed ports and bound
   parameters visible, with source page + precedence shown.


---

# REFACTOR Phase 2 — contracts first, before any container split
# (23 Nov 2026; appended below the original project's Phase 2 notes of
# 13 Nov 2026, which are preserved above — same filename, different
# phase numbering. Do not delete either section.)

Date: 23 Nov 2026. Working copy only: `~/workspace/crater-refactor/`.
Governing: the amendment (four resolutions), the six per-container
audits, the original problem-list/Docker plan, and
`PHASE1_STEP6_BUILD_NOTES.md` (Phase 1 gate). No implementation code
was changed here. No container was split. No oracle gate run was
performed.

## Standing rule — read first (Nishant, 23 Nov 2026 15:49 IST)

**Before running any test with Muse as the LLM backend, tell Nishant
first — he will have another LLM do it instead. Do NOT run any
Muse-as-backend gate run autonomously.** That means: no mock-server
run with model `muse-spark`, no Muse tokens spent on a gate run,
until he supplies the other LLM. This rule overrides any earlier
instruction to run gates with Muse as the backend. The Phase 3
post-extraction oracle gates are therefore READY AND WAITING for his
other LLM — they are not run as part of this contracts work, and no
Phase 3 gate may be started with Muse as the backend without him
being told first.

## The accepted-tie decision

Nishant approved moving on after Phase 1 (23 Nov 2026). The Phase 1
gate as written FAILED on one criterion — Miller **22/38 ties** the
frozen Muse baseline, it does not beat 22 — and the tie is **accepted
for proceeding**. Everything else passed: Miller beats Gemini 19/38
by 3; OMNIPRO case 1 stays 4/4; OMNIPRO 24/35 overall; discriminating
test 0/17 → 17/17 Miller, 8/9 OMNIPRO (omni-04 at 0.0, a picker-test
string-match miss, not a gate criterion); citations 79/79 Miller,
35/35 OMNIPRO; single-guess 0/17.

The known ranking/scorer bottleneck is a follow-up, NOT a blocker
for the split: the miller-16 split-cause loss (Phase 1 Step 4 split a
combined "gas solenoid valve not operating, or loose or broken wires
to gas solenoid" cause into two and only one survived the top 5 —
−1, cancelling the miller-02 +1), plus the HL.P and generic-service
rows (8 unrankable Miller rows that are real rows, not rankable
causes). Do NOT redo Phase 1 ranking work in Phase 2 or Phase 3.

## Contracts written (the deliverable)

All in `contracts/` in this tree; the full contract document is
`contracts/README.md` (§0–§12), with OpenAPI-ish schemas per
boundary. All four YAML files parse as YAML (verified with PyYAML in
this pass).

- `contracts/README.md` — the contract document: the 8+1 split,
  cross-cutting rules (no shared in-process calls/imports/memory, no
  cross-service DB reads, stateless services, idempotency keys,
  retries with backoff, circuit breakers, gateway rate limits,
  /health + /ready per service with no silent fallbacks), every
  cross-service contract, the data decisions, the oracle relocation
  fix, the extraction order, and the accepted-tie decision.
- `contracts/openapi-gateway.yaml` — public /v1/ surface, error
  envelope, health/readiness, rate limits, API-key auth. Traefik v3
  + FastAPI shim 250–350 lines, hard cap 400, no business logic.
- `contracts/openapi-ingestion.yaml` — jobs API (POST /v1/jobs 202,
  GET /v1/jobs/{id}, retry, lazy-ingest, revision status), knowledge
  lookup (failure-modes, model, entities, facts, integrate, verify),
  Mode B, TableRow schema. Table parser is an internal module — no
  POST /v1/tables/parse.
- `contracts/openapi-diagnosis.yaml` — POST /v1/diagnose/start,
  /{session_id}/respond, sessions list/get/context, outcome
  (record-only), POST /v1/query. Server-side rules: 3–5 hypotheses
  never 1, conclusion only with VALIDATED, turn-8 ending object.
- `contracts/openapi-twin-verifier.yaml` — POST /v1/verify → 202 +
  job, GET /v1/verify/jobs/{job_id}, twin compile/approve/
  experiments/hypotheses/context/visual, verdicts
  VALIDATED/REJECTED/CANNOT_EXPRESS/NOT_VERIFIED with the
  rules_used requirement on VALIDATED and the CANNOT_EXPRESS
  terminal path.

Contract summary (each defined in `contracts/README.md` with its
governing source):

1. **Diagnosis ↔ ingestion (§3):** diagnosis reads knowledge only
   via ingestion's HTTP API (`GET /v1/revisions/{id}/failure-modes`,
   `/model`, `/entities`, `/facts`, `/integrate`, `/verify`); no
   cross-service DB reads, no shared imports. Ingestion jobs API
   (202 + SHA-256 dedupe, 409 on key+SHA mismatch, Postgres
   progress with Miller `table_rows_parsed` = 38). Qdrant: ingestion
   sole writer, diagnosis sole search reader. TableRow is the unit
   of truth (`rankable`/`rankable_reason`, `twin_expressible`).
2. **Diagnosis ↔ twin, async (§5):** `POST /v1/verify` → 202 +
   job; poll `GET /v1/verify/jobs/{job_id}`. Verdicts
   VALIDATED/REJECTED/CANNOT_EXPRESS/NOT_VERIFIED; no VALIDATED
   without `rules_used`, trace/transition ids and source evidence;
   CANNOT_EXPRESS terminal and explained (which of the three cases
   + what is missing); timeouts 2 s/hypothesis, 10 s/job, timeout →
   NOT_VERIFIED "twin verification timed out". Archetype library
   frozen `3c-1.0.0` on every result. Diagnosis never blocks a turn
   on the twin.
3. **Gateway ↔ all services (§2) and frontend ↔ gateway (§6):**
   all public routes under `/v1/`; `/health` liveness, `/ready`
   aggregation (503 naming the failed check); uniform error
   envelope; API-key auth (OIDC deferred); interim rate limits
   (diagnosis 10/min + 20 burst, reads 60/min, uploads 5/hour).
   Frontend talks ONLY to the gateway, generated OpenAPI client,
   runtime-injected base URL. Re-ordered Step 3: `StartDiagnosticRequest`
   accepts `observations` only after the diagnosis schema change
   lands — before that, 422, never silently dropped. Expert routes
   `/v1/expert/...` CUT (404, nothing mounted). Learning routes stay
   mounted but gated: 403 `learning_deferred` in production v1
   until the pilot produces field outcomes (≥20).
4. **Cross-cutting (§1):** no shared in-process calls/imports/
   shared memory/cross-service DB reads; stateless services
   (Postgres is the system of record, Redis holds no durable
   state); idempotency keys on mutating endpoints; retries with
   exponential backoff (max 3, idempotent calls only); circuit
   breakers failing fast with 503 + named upstream; explicit
   timeouts on every call; `/health` + `/ready` per service —
   readiness fails when CrossEncoder/Qdrant/twin not ready, no
   silent fallbacks, 503 with a reason (Qdrant down → 503
   `retrieval_degraded`; CrossEncoder unloaded → `/ready` fails;
   twin down → NOT_VERIFIED + stated reason).
5. **Data decisions (§8):** Postgres replaces SQLite before the
   next ingestion round — **re-ingest from PDFs, not dump/restore**;
   Alembic replaces `migrations.py`; `extra="forbid"` per service.
   Celery + Redis replaces BackgroundTasks everywhere (broker db 0,
   result backend db 1; Redis Streams NOT adopted). Qdrant sparse
   vectors / Postgres FTS replaces BM25 (no Python BM25 code
   remains; `?degraded=true` debug flag only).
6. **Oracle relocation pre-step (§9):** `load_fault_injections` /
   `normalize_text` already extracted into
   `backend/simulation/oracle_shared.py` (Phase 1 Step 6, verified
   in this pass: `repair_actions.py:31` already imports from it,
   `oracle.py` re-exports). Phase 3 moves `oracle.py` + the five
   benchmark scripts that import it to harness-scorer; no production
   module may import from harness-scorer after the move.

## Per-service extraction order for Phase 3

(Corrected: the gateway audit's Step 4 order "table-parser,
ingestion, diagnosis-agent…" is superseded — table-parser is not a
service.)

1. **Gateway first** — /v1/ + thin shim inside the monolith,
   silent BM25 fallback killed, `llm/gateway.py` →
   `model_router.py`. Oracle gate after this step (waiting on
   Nishant's other LLM, per the standing rule above).
2. **Ingestion** — Celery + Redis, Postgres jobs/progress, TableRow
   as data; re-ingest from PDFs against Postgres.
3. **Diagnosis-agent** — retrieval inside; LangGraph + Postgres
   checkpoints; thin async verify client; §2.5 public-surface
   removals (verify-repair, verify-hypotheses, early-tournament,
   upstream-walk, next-test, guided-checks, safety/fleet-patterns
   GETs) land here.
4. **Twin-verifier** — async verify jobs on Celery + Postgres;
   oracle.py moves out to harness-scorer.
5. **Frontend last** — generated OpenAPI client, expert UI cut,
   learning UI gated.

**MINUS table-parser: it is not a service and never appears in the
extraction order.** Full oracle run after each single extraction, so
a regression is attributable to exactly one move — each such run
also waits on Nishant's other LLM per the standing rule.

## Superseded audit lines (recorded, not silently dropped)

- Gateway audit: Redis Streams recommendation (§7/§10) → Celery +
  Redis everywhere (amendment resolution 2).
- Gateway audit: `POST /v1/tables/parse` + "table-parser" first in
  the extraction order → table parser is an ingestion-internal
  module (amendment resolution 4).
- Gateway audit route table: `/v1/expert/...` kept ("expert KEEP")
  → CUT with the trio (amendment resolution 3).
- Gateway + diagnosis audits: standalone `POST /v1/retrieve`
  retrieval service → retrieval is an internal module of
  diagnosis-agent; no such contract (decision by Nishant, 23 Nov).
- Diagnosis audit: `POST /v1/sessions` + `/turns` redesign → NOT
  adopted for v1; the existing `/v1/diagnose/...` surface is
  versioned instead (a redesign would be `/v2/`).

## What was NOT done here

- No implementation code changed; no service extracted; no
  container built; no Celery/Redis, Postgres migration, or twin
  change started.
- No oracle gate run performed — and none may be run with Muse as
  the LLM backend until Nishant supplies the other LLM (standing
  rule above).
- No Phase 1 ranking/scorer work (known follow-up, not a blocker).
