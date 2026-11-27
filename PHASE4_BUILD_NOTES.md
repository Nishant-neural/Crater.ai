# Phase 4 build notes — backtracking diagnostics (+ steals items 1–4)

Built 14 Nov 2026. Tree: `~/workspace/crater-phase1/v12`. No new runtime
LLM calls (diagnosis 2 calls/turn, tournament 0 — unchanged). No engine
rewrite, no solver, no OCR, no production hardening.

## Scope (plan.md §4 Phase 4 + corrected loop order, 13 Nov 2026)

- (a) Upstream walk from a symptom along reviewed topology, using
  archetype states + propagation-table consequences, generating ranked
  hypotheses for unseen/off-table faults by composition, not lookup.
- (b) Next-question selection: the discriminating test is the test that
  splits the remaining fault signatures — information gain, plus test
  cost and safety, which the signature alone does not capture (v3 §13).
- (c) CORRECTED LOOP ORDER: hypotheses ranked → tournament runs EARLY
  and re-ranks them → agent asks the discriminating physical test for
  the top remaining cause → measurement → grounded conclusion → verify
  the chosen repair. Verify/tournament are NOT gated behind the
  grounded conclusion.
- (d) Unavailable-with-attempt: a bare unavailable is a miss — it
  still carries ranked best guesses with confidence/evidence, the
  discriminating test, and what is missing.
- (e) Lazy-ingest: Tier 2 on-demand ingest (job flag; Phase 1
  deliberately left Tier 2 on the v12 LLM path — flag decided here,
  Option C from `tier2-flag-decision-v1.md`).
- (f) Edge cases (scenario §4): two simultaneous faults (rule out
  singles first, then pairs from the top of the ranking) and
  intermittent faults ("sometimes" keeps a hypothesis at low
  confidence instead of ruling it out).
- Steals from Anthropic's Claude for Manufacturing
  `breakdown-diagnosis` skill (items 1–4): sibling/fleet search,
  citation rule, fixed-shape hypothesis presentation, safety
  implication leads. See `PHASE45_STEALS_BUILD_NOTES.md`.

## What was built

### `backend/diagnostics/backtracking.py` (new; pure deterministic logic)

- `FaultSignature`, `signatures_from_vocabulary()` (reviewed oracle
  vocabulary rows), `signatures_from_registry()` (scope-only).
- `upstream_walk()` — BFS backwards along reviewed topology; one
  hypothesis per component first (round-robin), then remaining failure
  modes; preferred loss-of-input mode per archetype
  (`_PREFERRED_MODE_BY_ARCHETYPE`); `max_hypotheses=6`,
  `max_depth=6`, `exclude` for downstream-of-symptom components
  (the arc→welder feedback edge would otherwise name the Welding Arc
  as a "producer" of the welder). Confidence prior 0.5 − 0.05/depth,
  floor 0.15. Walk-generated discriminating test, archetype-shaped.
- `select_discriminating_test()` — information gain (bits,
  confidence-weighted entropy) − 0.1×cost − 0.2×safety;
  `TEST_COST`/`TEST_SAFETY` tables; unknown test defaults cost 2,
  safety 1; "unmodelled"/"not_applicable" outcomes never split a pair;
  None when nothing splits.
- `apply_test_answer()` — known differing outcome rules a hypothesis
  out; unmodelled outcome keeps it at low confidence (×0.5, floor
  0.15); "not sure" halves confidences, rules nothing out;
  "sometimes" (intermittent) rules nothing out, caps all confidences
  at 0.3, flags `intermittent: True`; "yes" confirms the leading
  hypothesis, "no" rules it out.
- `generate_pair_hypotheses()` — pairs from top 3 singles only,
  confidence = product × 0.5, flagged `pair: True`.
- `unavailable_with_attempt()` — never bare; with no guesses at all
  returns one 0.1-confidence "unknown upstream fault" guess plus
  missing info.
- `rerank_with_tournament()` — VALIDATED first (+0.1, cap 0.95),
  REJECTED demoted (×0.5, floor 0.15, never eliminated), unavailable
  unchanged; stable order within a status.
- `cite_hypotheses()` (steal 2) — every hypothesis carries citations
  (manual case+page, twin trace, prior outcome); no citation ⇒
  labelled `"guess — reasoning, not evidence"`, shown not omitted.
  Citations live in API responses/timeline, not in `DiagnosticState`
  (the `Hypothesis` model has no citation field).
- `present_hypotheses()` / `render_hypothesis_markdown()` (steal 3)
  — every hypothesis under the same three headings in order:
  Evidence / First check / Expected fix; a missing expected fix
  shows as an empty heading.
- `_texts_match()` — loose cause-text match (normalized containment
  either way, OR ≥2 distinctive shared tokens covering ≥0.5 of the
  shorter text; generic tokens excluded). Needed because the registry
  paraphrases causes while the vocabulary quotes the manual, and
  because "Incorrect wire feed roller size" vs "Insufficient wire
  feed pressure" must NOT match (they share only generic words).

### `backend/diagnostics/fleet.py` (new; steal 1)

`sibling_search(db, product_id, symptom, revision_id=None)`. Queries
this asset's FailureMode + DiagnosticOutcome rows, then same-model
sibling history, matching symptom via `_texts_match`. Returns counts
with the query behind each, records with machine identity named, and
a fleet pattern when the same confirmed cause recurs on ≥2 distinct
assets. When the asset has no history, `fallback_used` states plainly
"manual + same-model siblings".

Deviation — sibling identity: `Product` is unique on
(manufacturer, family, model), so two physical machines of the same
model are the same Product row. Same-model siblings are therefore
the other revisions of the same product and the other diagnostic
sessions on that product; other models of the same family are
returned separately as `cousin_records`, labelled "family cousin,
other model", never as siblings. Outcomes are matched to a symptom
through their session's recorded symptoms (outcomes store no
symptom of their own).

### `backend/diagnostics/safety.py` (new; steal 4)

`check_safety(evidence)` — flag only from sourced evidence
(`manual_safety_note`, `canonical_model_safety_fact`,
`technician_observation`, `diagnosis_evidence`); unsourced text is
ignored and reported as ignored, never sets the flag. Triggers (the
skill's list): seized/locked rotating part; defeated/bypassed/failed
guard or interlock; stored energy; hydraulic/pneumatic pressure to
bleed; electrical fault; fault cleared by restart now repeating.
Uncertain stored energy still flags (hedging never cancels it).
Returns `lockout_line` first in every case plus the "this is not a
safety assessment" disclaimer. `safety_evidence_from_state()` builds
the evidence list from a session state (symptoms/observations/
measurements as technician observations, `safety_constraints` as
canonical-model safety facts).

### `backend/api/routes/diagnostics.py` (CRLF — patched via Python bytes scripts)

- `POST /diagnose/{session_id}/early-tournament` (new) — runs the
  tournament early, re-ranks hypotheses in the persisted session
  state, appends a timeline entry (`kind: "early_tournament"`),
  returns results/counts/re-ranked hypotheses/discriminating test,
  plus cited `presentation` (Evidence / First check / Expected fix),
  `fleet` (sibling search, advisory — failures logged, never break
  the response), and `safety`. When no twin exists it returns
  available=False plus an `attempt`.
- `POST /diagnose/{id}/respond` — now applies the early tournament
  best-effort after `run_turn` while the session is still active
  (no twin = no-op; failures logged, never break the turn). If
  signatures split the remaining hypotheses, the discriminating test
  replaces the LLM's question content. Safety: a flagged safety
  implication prepends the lockout line to the next step (never
  below it) and records a `safety_check` timeline entry.
- `POST /diagnose/{id}/verify-repair` and `verify-hypotheses` attach
  an `attempt` (ranked guesses + discriminating test + missing)
  whenever the twin cannot evaluate a cause.
- `GET /diagnose/{id}/safety` (new) and
  `GET /diagnose/{id}/fleet-patterns` (new).
- Helpers: `_load_vocabulary_signatures()`,
  `_signatures_for_session()` (vocabulary first, registry scope
  fallback), `_attempt_for_state()`, `_load_twin_for_session()`,
  `_apply_early_tournament()`. Module docstring records the
  corrected order. Existing endpoints unchanged in shape.

### Tier 2 flag — Option C (from `tier2-flag-decision-v1.md`, recommended)

- `backend/knowledge/repair_cases.py`: `RepairCaseRegistry.tier2_default: bool = True`;
  `backend/knowledge/repair_cases_omnipro.json` gained `"tier2_default": true`.
- `backend/ingestion/checkpoints.py`: checkpoint defaults now include
  `"tier2_deferred": []` and `"ingest_tier2": True`.
- `backend/ingestion/pipeline.py`: `ingest_pdf(..., ingest_tier2=None, lazy_pages=None)`;
  helpers `_chunk_pages()`, `tier2_partition(chunks, registry, ingest_tier2, lazy_pages)`
  (flag on or no registry = extract all, exactly Phase 1 behaviour;
  flag off = Tier-2 chunks deferred except those covering
  `lazy_pages`; Tier 1/3 never deferred here),
  `lazy_ingest_summary(session, job_id, pages)` (condensed summary
  only — page numbers, tiers, source types, counts — never raw chunk
  content). Deferred chunks are recorded in the checkpoint as
  `tier2_deferred`, NOT `knowledge_done`, so Retry respects the
  deferral; a lazy release re-enters `pending_knowledge` and the
  cleared list is persisted.
- `backend/api/routes/ingestion.py`: `POST /ingest` gained
  `ingest_tier2: bool | None = Form(default=None)`; explicit value
  wins, else registry default, else True; resolved flag saved to the
  checkpoint at job creation; `_run_job_background` passes it
  through. New endpoint `POST /ingest/jobs/{job_id}/lazy-ingest`
  (`LazyIngestRequest(pages)`) — releases only chunks covering the
  requested pages from `tier2_deferred`, queues the bounded job;
  returns condensed summary; 409 if job processing, 422 if no
  pages, no-op response if nothing released.

### `scripts/run_phase4_offtable.py` (new)

Builds the proof twin (compiler v3) and measures: (1) 9 registry
cases (registry hypotheses + vocabulary-signature discriminating
test); (2) 4 constructed unseen faults with all 4 case-1 table causes
ruled out (motor-no-power / Wire Feed Motor, relay-trigger-dead /
Trigger, reset-button-stuck-open / Reset Button, input-cord-unpowered /
Input Power Cord) via the upstream walk; (3) scenario §4 edge cases
(intermittent "sometimes", pairs from top of ranking). Writes
`phase4-offtable-report.json/.md` to the out-dir.

## Measured results — Phase 4 gate: PASS

- Off-table script to `/tmp/phase4-final`:
  **registry 9/9, unseen 4/4 (off-table attempt rate 100%),
  intermittent True, pairs True, gate PASS.**
- `tests/test_phase4_backtracking.py`: **19 passed** (incl. the
  early-tournament endpoint test compiling the proof twin once:
  4 validated, discriminating test selected, timeline entry persisted).
- `tests/test_phase4_steals.py`: **12 passed** (fleet search +
  pattern, citation rule, fixed-shape presentation, safety triggers
  incl. uncertain stored energy and unsourced-text rejection, safety
  endpoint).
- Full suite: **377 passed, 2 failed, 0 collection errors** (12 min 30 s).
  The 2 failures are the same pre-existing phase8a failures as the
  3C baseline (`test_pdf_chunk_modalities_reach_universal_persistence`,
  `test_schematic_projection_creates_entities_ports_and_relations`).
  377 = 346 baseline + 31 new Phase 4 tests (19 backtracking + 12 steals).
- 3C oracle re-run to `/tmp/phase456-phase4`: proof twin **20/35
  headline, O1 pass, O3 57/58**, per-case 4/4, 4/6, 1/4, 4/5, 2/4,
  3/5, 0/3, 2/2, 0/2; crater-10 **0/35, O1 fail**; Demo Motor Drive
  **7 transitions**. Reproduces the 3C baseline exactly — no
  degradation.

## Deviations (with reasons)

1. **Walk candidate pool 6, live cap 5.** The session live cap stays
   5 (plan §1 / tournament `MAX_HYPOTHESES`); the walk candidate
   pool is the full upstream chain (6 components on OMNIPRO — the
   input cord is 5 hops from the feeder), top 5 become live, the rest
   are ranked reserves carried in the attempt. Without round-robin
   one-hypothesis-per-component-first, the walk spent all 5 slots on
   the motor's 4 failure modes + spool and never reached
   trigger/button/cord.
2. **Sibling = revision/session, not separate Product.** See fleet.py
   section above: `Product` uniqueness makes separate same-model
   Product rows impossible; family cousins are returned separately
   and labelled.
3. **Citations live in API responses, not in `DiagnosticState`.**
   The `Hypothesis` model has no citation field; citations are
   re-derived per response from the same signatures, so they cannot
   go stale.
4. **`_texts_match` with generic-token exclusion** (see above) —
   needed because registry paraphrases and vocabulary quotes differ,
   and naive token overlap falsely matched unrelated causes.
5. **Safety evidence sources are a closed list.** A bare string with
   no source is ignored and reported; the flag is never set on a
   feeling or an LLM guess.

## Bugs found and fixed during the build (do not reintroduce)

1. Walk spent all 5 slots on the motor's failure modes; cord (5 hops)
   unreachable — fixed by round-robin + preferred loss-of-input mode +
   `MAX_WALK_DEPTH = 6`, `MAX_WALK_CANDIDATES = 6`.
2. Registry cause text ≠ vocabulary cause text (paraphrase) silently
   dropped signatures for cases 6 and 8 — fixed by `_texts_match`.
3. Arc→welder feedback edge made the plain reverse walk name the
   Welding Arc as a "producer" of the welder — fixed by `exclude`.
4. `_norm` did not normalize underscores, so preferred-mode matching
   picked "burnt_out" over "no power" for the motor — fixed.
5. `_texts_match` at 0.5 token overlap without stopwords falsely
   matched "Incorrect wire feed roller size" ↔ "Insufficient wire feed
   pressure" — fixed with generic-token exclusion + ≥2 distinctive tokens.
6. `_norm("not_sure")` became "not sure", so the NOT SURE branch missed
   and hypotheses were wrongly ruled out — fixed by checking
   `answer_norm == "not sure"`.
7. Test-only: `test_tier2_lazy_pages_release_only_those_pages` used p17
   (a Tier 1 page) — changed to p15 (Tier 2). Test-only:
   `DigitalTwin` row in the endpoint test needs `product_id` and `name`
   (non-nullable).

## Not run (live checks — need the user)

End-to-end ingestion with the user's LLM + Qdrant; any live app check
(fresh OMNIPRO upload/job, session restore, tournament, verify
verdicts, Machine Model view). See the final report's live-check list.
