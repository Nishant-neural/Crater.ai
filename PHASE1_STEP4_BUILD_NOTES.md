# Phase 1 — Step 4 build notes: rank-then-ask (audit Step 3)

Date: 23 Nov 2026. Scope: Step 4 of the diagnosis audit only (audit
p.6 "Prompt assembly: one call becomes two", p.13 Step 3 "Rank, then
ask", P-11/P-12/P-14/P-16). Gate: the new rank-then-ask tests + the
existing diagnosis/diagnostics tests + the Step 1, Step 2 and Step 3
gate suites. No Step 5 work (no LangGraph loop, no Postgres
checkpoints, no safety move, no twin async client, no CUTs of
closeout/drafting/review_view/expert trio), no ingestion Step 1 or
reranker Step 2 code touched.

Governing: diagnosis-agent audit pp.6, 9, 11, 13 (two structured
calls; server enforces 3–5 hypotheses, structured component and
value, citation intersection, discriminating_test on every turn 1
from the single picker) and the amendment (CrossEncoder NOW — already
landed in Step 2; nothing in this step reopens it).

## What was wrong

- One prompt returned hypotheses, eliminations, confidence and the
  next step together (P-11). `_normalize_result` accepted a
  1-hypothesis list; Gemini returned a single guess in 7 sessions
  (P-03) and nothing on the server stopped it.
- Citations were LLM-filled `supporting_evidence_chunk_ids` with no
  server-side validation; `_normalize_result` never checked that a
  cited chunk id was retrieved this turn. Miller-15: Gemini returned
  4 of 4 causes and 0 of 4 citations (P-16). `cite_hypotheses` was
  never called in `run_turn` (P-15).
- The discriminating-test picker existed
  (`backtracking.select_discriminating_test`, information gain − 0.1
  × cost − 0.2 × safety) but the loop never used it: the routes-level
  rewrite ran only when a twin existed, and the agent endings called
  `unavailable_with_attempt` with `discriminating_test=None` in both
  endings (P-09, P-12). Frozen baseline: discriminating test 0/17 in
  both runs. The harness scores `start(symptom)` only, and
  `POST /diagnose/start` never runs the routes-level tournament, so
  the fix had to live in the agent's `run_turn`, not the routes.
- Hypotheses carried no structured component/value (P-14): the rank
  was free text the scorer could not join to a row.

## What changed

### `backend/diagnostics/schema.py` (84 -> 105 lines)
- `Hypothesis`: `component: str = ""`, `value: str = ""` (P-14),
  `citation_status: str = "guess"` — server-computed, never
  model-asserted: `"grounded"` when at least one cited chunk id
  survived intersection, `"guess"` otherwise.
- `DiagnosticState`: `discriminating_test: dict | None = None` —
  the turn's test, set on every successful turn including turn 1,
  so the API response carries it (audit p.11 response shape).
- Asked sets (`asked_causes` / `asked_tests` on the schema) are NOT
  added here: they belong to the LangGraph step (audit Step 4 /
  Phase 1 Step 5). Asked-tracking stays timeline-derived this step.

### `backend/diagnostics/prompts.py` (56 -> 88 lines)
- `DIAGNOSTIC_SYSTEM_PROMPT` replaced by two prompts (the old name
  is gone; only `agent.py` used it):
  - `RANK_SYSTEM_PROMPT` (1,266 chars before the placeholders):
    rank only — no invented specs, cite only chunk_ids shown in the
    Evidence block, rank 3–5 most-likely-first, component/value from
    the retrieved row, honest confidence. JSON shape is hypotheses +
    eliminated only.
  - `ASK_SYSTEM_PROMPT` (1,296 chars): decide the next step from the
    ranking + discriminating-test candidate — most informative
    question over guessing, safety flags, conclude only on VALIDATED,
    never repeat an asked question. JSON shape is overall_confidence
    + next_step only.
- All 7 fail-closed rules are kept across the two prompts (rules
  1/2/4/5 on the rank call, 3/5/6/7 on the ask call).

### `backend/diagnostics/agent.py` (1,098 -> 1,584 lines)
- Schemas: `_DIAGNOSIS_OUTPUT_SCHEMA` deleted; `_RANK_OUTPUT_SCHEMA`
  (required: hypotheses, eliminated_hypotheses) and
  `_ASK_OUTPUT_SCHEMA` (required: overall_confidence, next_step).
  `_MIN/_MAX_RANKED_HYPOTHESES = 3/5`.
- `build_diagnosis_prompt` is now the rank call's prompt (same name,
  same one-copy whole-rows behaviour); new `build_ask_prompt` (ranked
  block + discriminating-test candidate + verification block, NO
  evidence block — the ranking carries the citations) and
  `_state_summary(..., include_hypotheses=False)` for the ask call
  so the ranking is not duplicated.
- `_normalize_result` split into `_normalize_ranked` +
  `_normalize_ask`, sharing `_valid_hypotheses`:
  - Citation intersection (P-16): cited ids are intersected with
    the chunk ids actually prompted this turn; unretrieved ids are
    dropped with a warning, and a hypothesis with zero surviving
    citations is kept and labelled `guess` (P-15), never dropped.
    Intersection is against the *prompted* chunk ids (the evidence
    the model saw), not all retrieved — a model cannot legitimately
    cite a chunk that was dropped whole by the budget and never shown.
  - Component grounding (P-14): a non-empty component must occur in
    the prompted evidence text; one that does not is blanked (the
    hypothesis is kept, the field quarantined). `value` is kept as
    supplied — it is a fault-state word, not a part name.
  - Count enforcement: >5 is cut to the top 5 in ranked order;
    <3 (including an empty ranking) returns None, so the caller
    spends one repair call and then degrades — a single guess is
    never persisted as the ranking.
- `_repair_json(raw, schema, instruction)` generalized (was
  single-schema); `_call_with_one_repair` is one structured call
  with the lenient-parse ladder + exactly one repair attempt.
  `_NoProviderError` distinguishes a missing provider (reported
  plainly, as before) from unparseable output.
- `_run_llm_turn(state, evidence, verification_block, signatures)`:
  rank call → discriminating test → ask call. Rank failure degrades
  with the previous ranking (and a test for it, when one exists);
  ask failure keeps the new ranking with the default question.
  Result keeps the old shape plus `discriminating_test`.
- Discriminating test (P-12): `_signatures_for_product` loads the
  product-matched repair-case registry (OMNIPRO or Miller, by
  manufacturer/family/model) and builds signatures via
  `signatures_from_registry` — the oracle vocabulary JSON is never
  consulted (P-17/18: it is the scorer's answer key). The single
  picker `backtracking.select_discriminating_test` runs first,
  excluding already-asked tests. When it returns None,
  `_fallback_discriminating_test` names the leading not-yet-asked
  cause as the check to run, with the top two ranked causes as the
  pair it separates, labelled `source: "ranking_fallback"` — it
  selects no test among candidates by gain, so it is not a second
  picker. This is the normal Miller path: the Miller registry
  carries no per-cause test (0/63 causes), so the picker can never
  split a Miller ranking; the OMNIPRO registry carries 45/45.
- `_attach_discriminating_test`: a question/action step that does
  not already carry the test text becomes the canonical
  discriminating-test question (test + leading cause to check in the
  content; the separated causes in the rationale, so the step
  content+rationale names ≥2 ranked causes for the harness rule).
  The separated causes live in the rationale, not the content: the
  asked-tracking reads step content, and a cause named only as
  "separated by this test" was not itself checked. Conclusion steps
  are never rewritten. A step that already carries the test is left
  alone.
- Endings (P-09): the turn-bound ending
  (`_apply_forced_safeguards`) and the cannot-express ending
  (`_cannot_express_ending`, threaded through
  `_apply_conclusion_gate`) now receive this turn's
  discriminating_test instead of None. Verified: no OMNIPRO registry
  test string and neither fallback text contains "?", so the
  cannot-express ending still asks no question.
- `run_turn`: loads signatures, passes them to `_run_llm_turn`,
  stores `state.discriminating_test`, records the test on the turn's
  timeline entry. `_asked_causes_and_tests` now also counts the
  agent's own turn-entry tests (dict form) and walk-entry tests
  (plain-string form) as asked-and-answered.
- Confidence is still model-supplied (coerced/clamped as before):
  the audit's "confidence is computed, not asked" line is NOT
  implemented here — deriving it from the test split + tournament
  needs the tournament to run on the *new* ranking inside the turn,
  which today runs pre-turn (old ranking) and post-turn in the
  routes. Flagged as open for Step 5.

## Tests

- New `tests/test_diagnosis_rank_then_ask.py` (480 lines, 19 tests):
  two-call split (schemas, temperature 0.2, no per-call cap, rank
  prompt carries evidence / ask prompt carries none); 6→5 cut;
  single guess repaired then rejected (previous hypotheses kept,
  no ask call); 2→3 via repair accepted; empty ranking rejected;
  citation intersection (retrieved survives, unretrieved dropped,
  zero-citation labelled guess, none dropped); component/value kept
  when grounded, ungrounded component blanked with hypothesis kept;
  ask failure keeps the ranking; picker splits OMNIPRO case 1 from
  registry signatures (feed-against-wood test, gain 0.439 bits);
  picker returns None for Miller and the fallback names the test;
  product-matched signatures (OMNIPRO 45, Miller 63, unknown 0);
  turn 1 end-to-end carries a discriminating test and scores 1.0
  under `score_discriminating_test` (unknown product, OMNIPRO
  picker path, Miller fallback path); Miller turn-1 smoke (below);
  turn-bound ending carries the test; cannot-express ending carries
  the test and asks no question.
- Updated for the split (same test counts, behaviour intentionally
  changed): `tests/test_diagnosis_robustness.py` (VALID_TURN →
  VALID_RANK 3 hypotheses + VALID_ASK; two-call scripts; schema
  assertions per call; wrong-shape test exercises the rank repair),
  `tests/test_diagnosis_prompt_budget.py` (one-copy test stubs
  rank+ask; Miller turn-1 measured number 4,762 → 4,486),
  `tests/test_verify_before_conclusion.py` (`_turn` →
  `_rank`/`_ask`/`_turn_script`; every ranking padded to 3;
  tournament-verdicts test reads messages[2]; all-causes-checked
  now takes 4 turns because the ranking carries 3 live hypotheses).

## Verification

### Measured Miller turn-1 smoke (real §6-5 chunk, in-memory DB)
Symptom "Low, high, or erratic wire speed.", evidence = whole §6-5
table chunk (2,885 chars, chunk `s65`), rank hand-written from its
rows (the stub stands in for the model; the test proves the plumbing
around it). Product row: Miller Electric / Millermatic / 252.

- **Gateway calls: 2** (rank, then ask). **Rank prompt: 4,513 chars**
  (Step 3 measured 4,762 for the old single call; the rank system
  prompt is shorter) — inside the ~4,700 target, below the 5,000
  gate. **Ask prompt: 2,414 chars.**
- **Hypotheses: 3**, all citing `["s65"]`, all `citation_status:
  "grounded"`, components `"line disconnect switch"`, values `""`.
- **discriminating_test:** ranking fallback (picker cannot split —
  Miller registry has no per-cause test), test names the leading
  cause, `separates` = the top two remedies.
- **Harness `score_discriminating_test`: 1.0** (was 0/17 frozen).
  Step is a question; content+rationale name two ranked causes.

### Gate suites (all green, same counts as pre-Step-4)
`~/workspace/crater-phase1/.venv/bin/python -m pytest … -q`
- New Step 4 + Step 3/diagnosis suites:
  `tests/test_diagnosis_rank_then_ask.py tests/test_diagnosis_prompt_budget.py tests/test_diagnosis_robustness.py tests/test_diagnostics.py tests/test_verify_before_conclusion.py`
  -> **71 passed** (19 new + 52 Step 3).
- Step 1 gate: `tests/test_table_parser.py tests/test_source_router.py tests/test_phase1_pipeline.py tests/test_chunking.py`
  -> **41 passed** (same as Step 1).
- Step 2 gate: `tests/test_reranker.py tests/test_phase1_hardening.py tests/test_verification_and_retrieval.py tests/test_diagnostics.py tests/test_diagnosis_robustness.py`
  -> **46 passed** (same as Step 2).
- Step 2 wider: `tests/test_phase1_pipeline.py tests/test_phase4_backtracking.py tests/test_hypothesis_tournament.py tests/test_phase8e_routing.py tests/test_llm_openrouter_fix5.py`
  -> **60 passed** (same as Step 2).
- Adjacent: `tests/test_verify_before_conclusion.py tests/test_phase3a_oracle.py tests/test_diagnosis_prompt_budget.py tests/test_harness_scorer_reporting.py` -> **54 passed**;
  `tests/test_ingestion_gemini_fix.py tests/test_llm_provider.py` -> **29 passed**;
  `tests/test_phase456_followup_walk.py` -> **5 passed**;
  `tests/test_phase4_steals.py tests/test_phase5_learning.py` -> **22 passed**.
- Smoke: `scripts/smoke_reranker_miller.py` -> **SMOKE PASS**
  (s65 still first; /ready 503 on Qdrant-down as before).

### Grep proof
`grep` over `backend/diagnostics/agent.py` +
`backend/diagnostics/prompts.py` for `[:800]`, `[:1000]`,
`_MAX_KNOWLEDGE_ITEM_CHARS`, `_load_product_knowledge`,
`_knowledge_context`, `_compact_state_json`, `knowledge_block`
-> **no matches**. `select_discriminating_test` is imported once in
`agent.py` and called once (in `_discriminating_test_for_ranking`) —
one picker, no second picker built. No `langgraph` / `checkpoint`
mentions in the changed files (no Step 5 work).

### Zip pristine
sha256 `d6886155a4394a727a32f8e38ac6794d81678ebeb02596c30e7371bacecedb92`
(unchanged after all work).

## Failures found and fixed during the step
1. `test_phase456_followup_walk` failed 1/5 on first run after this
   step: `AttributeError: 'str' object has no attribute 'get'` in
   `_asked_causes_and_tests` — upstream-walk timeline entries store
   `discriminating_test` as a plain string, not a dict, and the new
   asked-tracking read it as a dict. Fixed in `agent.py`: dict
   entries read `.get("test")`, string entries count as the test
   name itself. 5 passed after the fix. The fix is in Step 4's own
   new code (the asked-tracking extension), not Step 1/2 code.

## Decisions / open points (for the parent)
1. **Computed confidence deferred.** The audit's "confidence is
   computed, not asked" is not implemented (see above); the ask call
   still returns `overall_confidence`, coerced/clamped server-side.
   Needs the tournament on the new ranking inside the turn — a Step 5
   (LangGraph) design decision.
2. **Asked sets deferred.** Explicit `asked_causes` / `asked_tests`
   on the schema belong to Step 5; timeline-derived tracking stays,
   extended only to count the agent's own tests.
3. **Citation intersection is against prompted chunks**, not all
   retrieved — a model cannot legitimately cite a chunk the budget
   dropped whole and never showed it.
4. **Miller discriminating tests are fallback questions**, not
   manual-cited tests: the Miller registry has no per-cause test to
   feed the picker. A per-family test table for Miller (from the
   manual's own procedures) would let the picker split Miller
   rankings for real; that is registry data work, not agent work.
5. **Cosmetic wart:** causes that are remedy sentences ending in "."
   render as "…(see Section 4-13).? Report the result…" in the
   attached question and "…setting.." in the separates list. Left
   as-is: the tests pin the full cause string in the step content
   and the harness normalizes punctuation, so stripping the period
   would churn assertions for typography.
6. **Routes-level rewrite untouched.** `respond()` still rewrites
   the step with the picker's test (without the separated cause
   names) when a twin exists; `start()` has no such rewrite, which
   is why the agent-side attach is what makes turn 1 score. Aligning
   the routes rewrite with the agent's format is a later step.
7. **Carried forward from Step 3:** system prompt 1,596 chars is now
   two prompts (rank 1,266 + ask 1,296); late-turn prompts with 5
   verdict lines unmeasured; MiniLM 512-token window unchanged; the
   `get_client()` NO_PROXY crash is still open (test-only stub in
   the follow-up-walk file, Step 3 note); no live Qdrant in this VM.

## What was NOT done (Step 5 or later)
- No LangGraph loop, no Postgres checkpoints, no safety move into
  `run_turn`, no twin async client, no CUTs of closeout/drafting/
  review_view/expert trio.
- No ingestion Step 1 code touched; no reranker Step 2 code touched.
- No `retrieve_revision_context` / `knowledge.py` removal; no oracle
  vocabulary use anywhere in the request path.
