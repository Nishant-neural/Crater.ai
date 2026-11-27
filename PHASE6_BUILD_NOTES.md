# Phase 6 build notes — Mode B on-demand path

Built 14 Nov 2026. Tree: `~/workspace/crater-phase1/v12`. No new runtime
LLM calls (diagnosis 2 calls/turn, tournament 0 — unchanged). No engine
rewrite, no solver, no OCR, no production hardening.

## Scope (plan.md §4 Phase 6)

Mode B for the individual user with a big manual for an unknown machine:
Tier 1 first; diagnosis starts on the partial model while the rest
ingests in the background; progress shown per section; lazy-ingest is
the norm; if a symptom needs a section not yet ingested, say so plainly
with the wait instead of stalling silently; graduation path from
on-demand model to pre-built model when the same family is contracted.

## What was built

### `backend/ingestion/mode_b.py` (new)

- `sections_for_registry(registry)` — the manual's sections from the
  registry: Tier 1 pages (`tier1_pages`), Tier 3 pages (`tier3_pages`),
  everything else in 1..`page_count` as Tier 2. OMNIPRO: Tier 1 =
  [7, 12, 13, 17, 41, 42, 43], Tier 3 = [35–40], Tier 2 = the rest.
- `section_progress(db, job, checkpoint, registry)` — progress per
  section from the job's Chunk rows + checkpoint (`knowledge_done` /
  `tier2_deferred` / `integration_done`), never from the job percent
  alone. A page is extracted when ≥1 chunk covering it is in
  `knowledge_done`; deferred when its chunks are in `tier2_deferred`.
  Tier 3 is always "skipped" (v1 never ingests it). Tier 1 "done"
  when every Tier 1 page has an extracted chunk; Tier 2 "done" only
  when nothing is deferred and every Tier 2 page is extracted.
- `partial_model_available(db, revision_id, checkpoint)` — the partial
  model is the pipeline's own integration snapshot, built from
  whatever knowledge exists (for a Tier-1-first job: Tier 1 knowledge).
  Available when the checkpoint says integration ran AND a snapshot
  row exists for the revision. This module only reads the snapshot,
  never fabricates one.
- `mode_b_status(db, job, checkpoint, registry)` — the progress view:
  job state/stage/percent, per-section progress, partial-model
  availability, Tier 2 deferred count, `ingest_tier2` flag.
- `check_symptom_readiness(db, job, checkpoint, registry, symptom)` —
  can a diagnosis start? Requires the partial model AND, for a
  symptom matching a registry case, that case's pages (Tier 1 pages +
  referenced pages) be extracted. A case whose referenced page is
  Tier 2 (omni-05, p19) is not ready until that page is lazy-ingested.
  A symptom with no matching top case can still start — diagnosis
  proceeds by composition on the partial model (Phase 4), said plainly
  in the note. When not ready, the answer carries a `wait_message`
  naming the section and the missing pages, plus
  `pages_to_lazy_ingest` — never a silent stall.
- `register_mode_b(db, product_id, job_id)` / `get_product_mode(db,
  product_id)` — the `ProductMode` row (mode on_demand | prebuilt).
- `graduate_to_prebuilt(db, product_id, job_id, actor,
  save_checkpoint_fn=None)` — the graduation path. A named actor is
  required. The flip is recorded (mode, actor, timestamp) and the
  job's deferred Tier 2 chunks are released back to pending: the
  checkpoint flag goes on and the deferred list is cleared, with the
  released ids kept under `graduation_released` for the audit trail —
  so the next job run (Retry) ingests them in full through the normal
  job path. The re-ingest itself is not run here: graduation queues
  it, it does not pretend it already happened.

### New table (`backend/db/models.py`)

- `ProductMode` — product_id (PK), mode (on_demand | prebuilt),
  background_job_id, graduated_at, graduated_by, created/updated_at.

### Endpoints (`backend/api/routes/mode_b.py`, new; registered in `backend/api/main.py`)

- `POST /mode-b/start` {revision_id, job_id} — register a product as
  Mode B (on-demand) for one ingest job; returns the initial
  per-section progress. The upload itself is the existing
  `POST /ingest` with `ingest_tier2=false` (Tier 1 first).
- `GET /mode-b/jobs/{job_id}/progress` — per-section progress +
  partial-model availability.
- `POST /mode-b/jobs/{job_id}/diagnose` {symptom} — start a diagnosis
  on the partial model (via the normal `start_session` flow). If the
  symptom needs an un-ingested section, returns available=False with
  the wait message + pages to lazy-ingest, and starts no session.
- `GET /mode-b/products/{product_id}` — current mode.
- `POST /mode-b/products/{product_id}/graduate` {actor, job_id} —
  graduation to pre-built (Mode A).

## Measured results — Phase 6 gate: PASS

- `tests/test_phase6_mode_b.py`: **6 passed** —
  per-section progress (Tier 1 done 7/7, Tier 2 in_progress with
  3 deferred, Tier 3 skipped, partial model available, job still
  processing); case 1 diagnosed from the partial model while the
  background job is still processing and Tier 2 still deferred;
  case-5 symptom (needs Tier 2 p19) returns available=False with the
  wait message naming Tier 2 + p19 and `pages_to_lazy_ingest=[19]`,
  and no session started; diagnose before the partial model waits
  with the Tier 1 message; an unmatched symptom starts by composition
  (note says so); start registers on_demand and graduation flips to
  prebuilt, releases 3 Tier 2 chunks, sets `ingest_tier2=true`, and
  records the actor; graduation without a named actor is refused (422).
- Off-table script to `/tmp/phase6`: **registry 9/9, unseen 4/4,
  intermittent True, pairs True, gate PASS** (unchanged).
- 3C oracle re-run to `/tmp/phase456-phase6`: proof twin **20/35
  headline, O1 pass, O3 57/58**; crater-10 **0/35**; Demo Motor Drive
  **7 transitions**. Reproduces the 3C baseline exactly.
- Full suite: **393 passed, 2 failed, 0 collection errors** (13 min 6 s).
  The 2 failures are the same pre-existing phase8a failures as the
  3C baseline (`test_pdf_chunk_modalities_reach_universal_persistence`,
  `test_schematic_projection_creates_entities_ports_and_relations`).
  393 = 387 after Phase 5 + 6 new Phase 6 tests.

## Deviations (with reasons)

1. **The upload is not a new endpoint.** Mode B start reuses the
   existing `POST /ingest` with `ingest_tier2=false`; `/mode-b/start`
   only registers the product as on-demand for that job. Duplicating
   upload handling would have split the idempotency/checkpoint logic
   across two endpoints.
2. **Graduation queues the full ingest; it does not run it.**
   Releasing the deferred chunks + flipping the flag is the real,
   recorded operation; the re-ingest runs on the next job run (Retry)
   through the normal path. Running a second ingest synchronously
   inside the graduation call would bypass the job queue and the
   checkpoint/resume machinery.
3. **Progress is per-section from Chunk rows + checkpoint, not from
   the job percent.** The job percent is a single pipeline number; the
   section view needs per-page extraction state, which only the Chunk
   rows + `knowledge_done`/`tier2_deferred` carry. When a job has no
   Chunk rows yet, sections honestly report 0 pages done.

## Not run (live checks — need the user)

A real Mode B cold start end-to-end (fresh OMNIPRO upload with
`ingest_tier2=false`, Tier 1 partial model built by the real pipeline
with the user's LLM + Qdrant, case-1 diagnosis while Tier 2 ingests,
lazy-ingest of p19, graduation after a contract) — the gate test
above runs the same code paths at the seam (fabricated job/chunks/
checkpoint/snapshot, stubbed session start), but the live pipeline
run still needs the user. See the final report's live-check list.
