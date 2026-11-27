# Phase 1 — Step 3 build notes: whole rows, one-copy ~4,700-char prompt (audit Step 2)

Date: 23 Nov 2026. Scope: Step 3 of the diagnosis audit only (audit
p.8 prompt/retrieval budgets, p.13 Step 2 "Whole rows, one-copy
prompt"). Gate: the new prompt-budget tests + the existing
diagnosis/diagnostics tests + the Step 1 and Step 2 gate suites. No
Step 4 work (no rank-then-ask split, no server-enforced 3–5
hypotheses, no discriminating-test picker changes), no ingestion
Step 1 or reranker Step 2 code touched except the test stub below,
no Celery/Redis, no container split.

Governing: diagnosis-agent audit pp.8, 13 (prompt ~4,700 chars /
~1,250 tokens; evidence top 5 whole items; no [:800], [:1000],
1,200-char cut; gate: prompt at or below about 5,000 chars) and the
amendment (CrossEncoder NOW — already landed in Step 2). The Step 2
notes record that the audit's "final top 5" belongs to this step's
budget work, and that the dead rerank_model/rerank_provider entries
belong to this step's cleanup.

## What was wrong

- The diagnosis prompt carried THREE copies of the same facts:
  evidence chunks (each cut to 800 chars), the
  `_load_product_knowledge` component/failure-mode list (25 + 12
  lines, token-overlap ranked, ~half duplicates), and the canonical
  graph JSON dump via `_knowledge_context` (up to 8 items × 1,200
  chars). Worst case ~33,600 chars; typical ~20,000 (audit p.8).
- The `[:800]` evidence cut lost exactly the rows that matter: the
  Miller §6-5 table sat at character 3,418 of its old chunk.
- The system prompt alone was 3,342 chars with empty blocks — 70%
  of the whole ~4,700-char budget before any evidence.
- `final_top_k` was still 8 and `hybrid_top_k` 40 (audit target: 5
  and 15), so up to 8 cut chunks fed the prompt.
- Dead `rerank_model` / `rerank_provider` OpenRouter entries
  remained in `model_routes` (Step 2 leftover); the reranker is a
  local CrossEncoder since Step 2.

## What changed

### `backend/diagnostics/prompts.py` (80 -> 56 lines)
- `DIAGNOSTIC_SYSTEM_PROMPT` rewritten to 1,596 chars with empty
  blocks (was 3,342). All 7 fail-closed rules kept in substance —
  no invented specs, cite a chunk_id, most-informative question
  over guessing, honest confidence, safety flags + explicit
  confirmation, "not enough evidence" acceptable, conclude only on
  VALIDATED, never repeat an asked question — plus the one-call
  JSON shape (eliminated_hypotheses sketched as "[same shape as
  hypotheses]" to save ~100 chars; the code schema
  `_DIAGNOSIS_OUTPUT_SCHEMA` still enforces the full shape).
- The `{knowledge_block}` placeholder is gone. Docstring records
  the budget and the one-copy rule.

### `backend/diagnostics/agent.py` (1,131 -> 1,098 lines)
- DELETED: `_load_product_knowledge`, `_knowledge_context`,
  `_compact_state_json`, `_query_tokens`, `_ranked_lines`,
  `_TOKEN_RE`, `_MAX_COMPONENT_LINES` (25),
  `_MAX_FAILURE_MODE_LINES` (12), `_MAX_KNOWLEDGE_ITEM_CHARS`
  (1,200). Nothing else in the codebase called them (verified by
  grep); `Component` / `FailureMode` imports removed.
- NEW `build_diagnosis_prompt(state, evidence,
  verification_block="")`: system + typed state summary + whole
  evidence rows + verification block. No knowledge block.
- NEW `_state_summary(state)`: typed summary (symptoms; latest 3
  observations/measurements; hypotheses with confidences;
  eliminated causes; confidence + turns; current step), bounded by
  item count, never by cutting text. Turn-1 Miller state: 106
  chars (old `_compact_state_json`: 255).
- NEW `_select_evidence_items` + `_render_evidence_block`:
  whole items or nothing, in rerank order, under
  `_PROMPT_EVIDENCE_BUDGET_CHARS = 2500`. The top-ranked item is
  ALWAYS included whole (a large table chunk must never fall off a
  size cut — the Miller §6-5 failure); each further item is
  included whole only while the running total stays in budget, else
  dropped whole, never cut. `hybrid_retrieve` already returns at
  most `final_top_k` (5) items.
- `_run_llm_turn(state, evidence, verification_block="")`: the
  `knowledge_block` parameter is gone; the `[:800]` evidence cut
  is gone. `_repair_json`: the `raw[:12000]` cut on the repair-call
  input is gone too (raw output is bounded by the 8,192-token
  route ceiling anyway).
- `run_turn`: retrieval is now `hybrid_retrieve` directly — the
  canonical-knowledge leg (`retrieve_revision_context`) is no
  longer run for the turn at all, since its only use was the
  deleted prompt dump. (The query route still uses
  `retrieve_revision_context` for its own response shape;
  `backend/retrieval/hybrid.py` and `knowledge.py` are untouched.)
- Budget constants: `_PROMPT_EVIDENCE_BUDGET_CHARS = 2500`,
  `_PROMPT_TOTAL_BUDGET_CHARS = 5000` (audit gate).

### `backend/config.py` (133 -> 132 lines)
- `hybrid_top_k: 40 -> 15`, `final_top_k: 8 -> 5` (audit p.8).
- DELETED: `rerank_model` / `rerank_provider` fields, the
  `"rerank"` entry from `model_routes`, and `"rerank"` from
  `task_max_tokens`. No caller used them (verified by grep; the
  gateway falls back to `llm_provider` defaults for unknown
  tasks, and no code routes a "rerank" task any more).
  `reranker_model` (local CrossEncoder) is untouched.

### Tests
- `tests/test_diagnosis_prompt_budget.py` (new, 211 lines, 9
  tests): one-copy/whole-rows (tail markers survive, each chunk
  counted exactly once, no knowledge-block labels); Miller turn-1
  measured prompt (see Verification); typed state summary ≤ 800
  chars, no JSON braces; whole-or-nothing selection (huge second
  item dropped whole; huge FIRST item always kept whole); grep
  gate (no `[:800]` / `[:1000]` / 1,200-char cut / deleted helpers
  / `knowledge_block` in agent.py + prompts.py); retrieval budgets
  15/5; rerank route removed.
- `tests/test_diagnosis_robustness.py`: `_run_llm_turn` calls
  drop the deleted 3rd arg (7 calls); the compact-state test is now
  a state-summary test (summary has no chunk text; the prompt
  carries the 5,000-char chunk whole, exactly once);
  `test_load_product_knowledge_…` replaced by
  `test_run_turn_prompt_has_no_knowledge_block` (deleted helpers
  are gone); `test_run_turn_…` now stubs `hybrid_retrieve`
  (returns `[chunk]`) and asserts the captured turn-1 prompt
  carries the evidence once with no knowledge block.
- `tests/test_verify_before_conclusion.py`: autouse fixture stubs
  `hybrid_retrieve` (was `retrieve_revision_context`).
- `tests/test_phase8e_routing.py`, `tests/test_llm_openrouter_fix5.py`:
  rerank-route assertions replaced by "no rerank route" assertions
  (test renamed to
  `test_clean_env_diagnosis_defaults_to_openrouter_rerank_route_removed`).
- `tests/test_phase456_followup_walk.py`: autouse fixture stubs
  `hybrid_retrieve` to `[]` — see "Follow-up-walk failure" below.

## Verification

### Measured Miller turn-1 prompt (real §6-5 chunk)
State: symptom "Low, high, or erratic wire speed.", turn 0.
Evidence: whole §6-5 table chunk (2,885 chars, rebuilt row-for-row
from `frozen/miller_parser_rows.json`, 21 rows — §6-5 proper is 21
rows, 18 twin-expressible; 38 is across ALL Miller troubleshooting
tables) + a 940-char distractor that does not fit the budget and is
dropped whole. Verification block: no-hypotheses-yet (148 chars).

- **Prompt: 4,762 chars, ~1,190 tokens** (system 1,596 + state 106
  + evidence 2,912 + verification 148). Inside the ~4,700-char
  target and below the audit's ~5,000-char gate.
- Whole §6-5 chunk present (`table in prompt`, tail 200 chars
  present, offset 2,469 deciding-remedy slice present); chunk
  content occurs exactly once; no "Known product knowledge" /
  "Canonical knowledge" strings.

### Gate suites (all green)
`~/workspace/crater-phase1/.venv/bin/python -m pytest … -q`
- New Step 3 + diagnosis suites:
  `tests/test_diagnosis_prompt_budget.py tests/test_diagnosis_robustness.py tests/test_diagnostics.py tests/test_verify_before_conclusion.py`
  -> **52 passed**.
- Step 1 gate: `tests/test_table_parser.py tests/test_source_router.py tests/test_phase1_pipeline.py tests/test_chunking.py`
  -> **41 passed** (same as Step 1).
- Step 2 gate: `tests/test_reranker.py tests/test_phase1_hardening.py tests/test_verification_and_retrieval.py tests/test_diagnostics.py tests/test_diagnosis_robustness.py`
  -> **46 passed** (same as Step 2).
- Step 2 wider: `tests/test_phase1_pipeline.py tests/test_phase4_backtracking.py tests/test_hypothesis_tournament.py tests/test_phase8e_routing.py tests/test_llm_openrouter_fix5.py`
  -> **60 passed** (same as Step 2).
- Adjacent: `tests/test_verify_before_conclusion.py tests/test_phase3a_oracle.py tests/test_diagnosis_prompt_budget.py tests/test_harness_scorer_reporting.py` -> **54 passed**;
  `tests/test_ingestion_gemini_fix.py tests/test_llm_provider.py` -> **29 passed**;
  `tests/test_phase456_followup_walk.py` -> **5 passed** (after the
  stub below); `tests/test_phase4_steals.py tests/test_phase5_learning.py` -> **22 passed**.
- Smoke: `scripts/smoke_reranker_miller.py` -> **SMOKE PASS**
  (s65 still first; /ready 503 on Qdrant-down as before).

### Grep proof (diagnosis prompt path)
`grep` over `backend/diagnostics/agent.py` +
`backend/diagnostics/prompts.py` for `[:800]`, `[:1000]`,
`_MAX_KNOWLEDGE_ITEM_CHARS`, `_load_product_knowledge`,
`_knowledge_context`, `_compact_state_json`, `knowledge_block`,
`… (truncated)` -> **no matches**. The only `[:1000]` left in
the retrieval tree is a Step 2 docstring in
`backend/retrieval/reranker.py:7` ("the old text[:1000] cut is
deleted") — words, not code. Count-slices elsewhere
(`candidates[:settings.final_top_k]`, `hypotheses[:max_live]`)
are top-k counts, not text truncation, and are out of the prompt
path; `%.200s` log truncations are log-only.

### Zip pristine
sha256 `d6886155a4394a727a32f8e38ac6794d81678ebeb02596c30e7371bacecedb92`
(unchanged after all work).

## Follow-up-walk failure found by the extra sweep (not Step 3)
`tests/test_phase456_followup_walk.py` failed 2/5 on first run
after this step — but NOT because of this step. The failure was
`RetrievalUnavailableError: semantic retrieval unavailable:
Invalid port: ':1]'` from `QdrantClient()` construction inside
`semantic_search`: qdrant-client builds an httpx client that parses
every NO_PROXY entry, and this VM's NO_PROXY contains bracketed
IPv6 literals (`[::1]`, `[fd8b:4f84:7d32:99::1]`). Step 2's
`sanitize_proxy_env_for_httpx()` runs only in
`SentenceTransformerEmbeddings.__init__` and `reranker._load_model()`,
never before `vector_store.get_client()`. The test passed at Step 1
time only because the pre-Step-2 `hybrid_retrieve` caught the
exception and silently fell back to BM25-only — the fallback Step 2
deliberately deleted. So the test was already broken on the Step 2
tree (Step 2's verification never ran this file); Step 3 changed
nothing on that path (same `hybrid_retrieve` call; only the
`final_top_k` value differs, and the crash happens before any
top-k use). Fix applied here, test-only: the file now stubs
`hybrid_retrieve` to `[]`, matching how the other diagnosis tests
stub retrieval — the file tests walk wiring with the LLM already
stubbed, not live retrieval. 5 passed after the stub.
OPEN (not fixed here — out of Step 3 scope, touches Step 2 code):
`vector_store.get_client()` still crashes on bracketed-IPv6
NO_PROXY before any sanitize runs. With no Qdrant in this VM it
changes no outcome here, but a live run needs either a clean
NO_PROXY or the sanitize call in `get_client()`.

## Decisions / open points (for the parent)
1. **First-item-always exception.** The evidence budget (2,500) is
   smaller than the Miller §6-5 chunk (2,885). Strictly dropping it
   would repeat the old failure in reverse — a turn with no
   evidence. So the top-ranked item is always included whole and
   the budget binds the rest. Consequence: a turn whose top item
   is huge AND whose state/verification blocks are large can exceed
   5,000 chars (e.g. late turns with 5 verdict lines). Measured
   turn-1 is 4,762; later turns are unmeasured.
2. **System prompt 1,596 chars vs audit ~1,200.** The 7 fail-closed
   rules + JSON shape do not fit in ~1,200 without stubbing the
   rules. The total still lands at 4,762, so the overrun is
   absorbed by the small turn-1 state/verification blocks.
3. **MiniLM 512-token window unchanged** (Step 2 caveat carries
   over): whole text reaches the reranker, but text past ~512
   tokens is not scored on.
4. **No live Qdrant in this VM**, so no end-to-end diagnose call
   was run; a live call still correctly 503s (Step 2 behaviour).

## What was NOT done (Step 4 or later)
- No rank-then-ask two-call split, no server-enforced 3–5
  hypotheses, no discriminating-test picker changes, no citation
  intersection, no structured component/value fields (audit Step 3
  / Phase 1 Step 4).
- No ingestion Step 1 code touched; no reranker Step 2 code touched
  (only the follow-up-walk test stub above).
- No Celery/Redis, no container split, no Postgres migration.
- No `retrieve_revision_context` / `knowledge.py` removal: the
  query route still uses them; only the diagnosis turn stopped
  calling them.
