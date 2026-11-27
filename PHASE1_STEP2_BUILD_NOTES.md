# Phase 1 — Step 2 build notes: CrossEncoder reranker + retrieval hardening (P-05, P-06)

Date: 23 Nov 2026. Scope: Step 2 of the diagnosis audit only (audit Step 1
"Reranker and retrieval hardening"). Gate: the new reranker tests + the
existing retrieval/diagnosis tests + the Miller smoke check below. No prompt
work (Step 3), no ingestion Step 1 code touched, no Celery/Redis, no
container split.

Governing: amendment resolution 1 (CrossEncoder NOW, model
cross-encoder/ms-marco-MiniLM-L-6-v2, whole text, /ready fails if the model
has not loaded) overrules infra's "remove reranker, re-add only if Miller
regresses". Infra P-06 governs the Qdrant failure mode.

## What was wrong

- `reranker.py` (55 lines) was an LLM call scoring `text[:1000]` per
  candidate. The Miller §6-5 table chunk starts at char 3,418 in the old
  chunking, so the reranker scored it 20/100 on Spot Timer prose (P-05);
  on LLM failure it silently fell back to fusion order.
- `hybrid.py` caught every semantic-search exception and fell back to
  BM25-only with a log warning ("Semantic retrieval unavailable; using
  BM25 only"). A live run ingested through exactly this path with Qdrant
  down and looked valid while running keyword-only (P-06).
- The zip's `hybrid.py` lacks the v12 fix6 guard: a reranker exception
  propagated uncaught to POST /diagnose/start as HTTP 500.
- There was no /ready endpoint at all in the zip (infra audit).

## What changed

### `backend/retrieval/reranker.py` (55 -> 137 lines)
- Local `sentence_transformers.CrossEncoder`, model
  `cross-encoder/ms-marco-MiniLM-L-6-v2` (new `settings.reranker_model`).
  No provider key, no LLM call, no `gateway.complete("rerank")` caller
  remains anywhere.
- Whole text: `pairs = [(query, text) for _, text in candidates]` -- the
  `text[:1000]` cut is deleted. (The model's own 512-token window still
  applies inside `CrossEncoder.predict`; that is a property of the chosen
  model, verified `max_seq_length = 512`, not a cut in this code. A
  2,885-char atomic table chunk therefore still gets its first ~512
  tokens scored; the wire-speed remedies at offset 2,469 are beyond the
  model window but the chunk still ranks first on its trouble text --
  see smoke check. A sliding-window scorer is NOT built here.)
- `rerank(query, candidates, top_k) -> list[str]` signature unchanged.
  New `rerank_with_scores()` returns the `[(chunk_id, score)]` pairs the
  audit's "return scores" requires; `rerank()` delegates to it.
- Load failure is a readiness failure: `_load_model()` raises
  `RerankerUnavailableError`, cached per process (a retry needs a
  restart -- model files do not appear spontaneously, and readiness
  probes stay cheap instead of re-attempting a network fetch per probe).
  `rerank()` NEVER silently returns fusion order. Helpers:
  `ensure_reranker_loaded()`, `is_reranker_ready()`, `reranker_status()`,
  `_reset_reranker_state()` (tests).

### `backend/retrieval/hybrid.py` (96 -> 132 lines; CRLF preserved)
- New `RetrievalUnavailableError(reason)`. A `semantic_search` exception
  now raises it -- the silent BM25-only fallback is deleted.
- BM25 DECISION (recorded, see below): BM25 stays a first-class leg of
  the normal fusion path (RRF k=60 kept per both audits). Only the
  *fallback* on semantic failure is removed. The full bm25.py removal
  (Qdrant native sparse) is a separate infra migration needing a
  re-index, not a fallback delete.
- fix6 guard ported from v12 (verbatim in behaviour): `try rerank()`
  except `RerankerUnavailableError: raise` (load failure must stay loud)
  except `Exception:` log + return fusion order truncated to
  `final_top_k`, so one bad predict cannot 500 a session start.

### `backend/retrieval/_proxy_env.py` (new, 47 lines) + `embeddings.py` (+7)
- Environment quirk found while loading the model (blocks the gate, so
  fixed here, not worked around in tests): this VM's NO_PROXY contains
  bracketed IPv6 literals (`[::1]`, `[fd8b:4f84:7d32:99::1]` ...).
  huggingface_hub builds a global httpx client that parses every
  NO_PROXY entry as a URL pattern, and httpx raises
  `InvalidURL: Invalid port: ':1]'` on them -- even for a fully cached
  load, even with HF_HUB_OFFLINE=1. Verified both failure modes live.
  `sanitize_proxy_env_for_httpx()` drops only the bracketed/bare-IPv6
  entries in-process before the first hub call; every other entry and
  the HTTP(S)_PROXY settings are untouched. Called in
  `reranker._load_model()` and in `SentenceTransformerEmbeddings.__init__`
  (same crash blocks live `semantic_search`). A deploy with a clean
  NO_PROXY, or with the model baked into the image, is unaffected
  (the helper is a no-op there).

### `backend/api/readiness.py` (new, 86 lines) + `backend/api/main.py` (+42)
- New GET /ready (200 / 503): database SELECT 1, Qdrant
  `collection_exists` + `get_collection`, reranker model loaded.
  Each check returns `{"ready": bool, "detail": reason}`. The first
  /ready probe warms the model (downloads on first load if the cache is
  empty); later probes are fast. /health stays process-up. `lifespan()`
  is unchanged (init_db only).
- Exception handlers in main.py: `RetrievalUnavailableError` and
  `RerankerUnavailableError` -> HTTP 503
  `{"detail": {"code": "retrieval_unavailable"|"reranker_unavailable",
  "reason": ...}}`. One handler covers POST /diagnose/start,
  POST /diagnose/{id}/respond (both run retrieval inside run_turn) and
  POST /query.

### `backend/config.py` (+4 lines)
- `reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"`.
  The old `rerank_model` / `rerank_provider` (OpenRouter) entries in
  `model_routes` are now dead (no callers) but left in place; removing
  them belongs to the prompt-step cleanup, not this step.

### Tests
- `tests/test_reranker.py` (new, 340 lines, 11 tests): whole-text
  scoring via a stub CrossEncoder (captured pairs equal the full text,
  tail past char 1000 decides the ranking); scores returned best-first;
  empty candidates never load the model; load failure raises and is
  cached (constructor runs once) and fails `check_reranker()`; /ready
  maps checks to 200/503 (stubbed checks); Qdrant failure raises
  `RetrievalUnavailableError` and BM25 never runs; fix6 guard returns
  fusion order on a transient rerank exception but propagates a load
  failure; ONE real-model test ranks the §6-5 table chunk first for
  "Low, high, or erratic wire speed." (deciding remedy at offset 2,469).
- `tests/test_phase1_hardening.py`: the old
  `test_hybrid_retrieval_uses_bm25_when_semantic_search_fails` (which
  asserted the deleted fallback) is replaced by
  `test_hybrid_retrieval_fails_loudly_when_semantic_search_fails`
  (raises, BM25 stub asserts it is never called).

### `scripts/smoke_reranker_miller.py` (new, 164 lines)
- Manual smoke check (see Verification). Seeds an in-memory SQLite DB
  with real Miller chunks, stubs `semantic_search`/`bm25_search`, runs
  the real CrossEncoder through live `hybrid_retrieve()`.

## Environment setup (not repo code)
- The phase-1 venv did NOT have sentence-transformers (declared in
  pyproject, locked 6.0.1, but never installed): installed CPU torch
  2.15.0+cpu from the PyTorch CPU index, then
  `sentence-transformers==6.0.1` from PyPI (transformers 5.24.0,
  huggingface-hub 1.33.0, tokenizers 0.23.3, ...).
- The CrossEncoder model (~90 MB) is now cached in
  `~/.cache/huggingface/hub/models--cross-encoder--ms-marco-MiniLM-L-6-v2`.
  First download took ~3 min (one xet-read-token retry); cached loads
  take ~17 s in-process (torch import dominates).

## Verification

### Gate suites (all green)
`~/workspace/crater-phase1/.venv/bin/python -m pytest tests/test_reranker.py tests/test_phase1_hardening.py tests/test_verification_and_retrieval.py tests/test_diagnostics.py tests/test_diagnosis_robustness.py -q`
-> **46 passed in 26 s**.
Wider retrieval/diagnosis-adjacent set:
`tests/test_phase1_pipeline.py tests/test_phase4_backtracking.py tests/test_hypothesis_tournament.py tests/test_phase8e_routing.py tests/test_llm_openrouter_fix5.py`
-> **60 passed in 42 s**.
Step 1 gate suites re-run (no regression):
`tests/test_table_parser.py tests/test_source_router.py tests/test_phase1_pipeline.py tests/test_chunking.py`
-> **41 passed in 3.5 s** (same as Step 1).

### Smoke check (manual, real model)
`~/workspace/crater-phase1/.venv/bin/python scripts/smoke_reranker_miller.py`
-> **SMOKE PASS** (30 s):
- Query "Low, high, or erratic wire speed."; §6-5 table chunk 2,885
  chars, deciding remedy at offset 2,469.
- Real CrossEncoder scores: s65 -5.877, distractor -9.422, third -9.680.
  `hybrid_retrieve` returns s65 first even though the stubbed fusion
  order had the distractor first -- the reranker overturned fusion.
- `CrossEncoder.predict` received text lengths [1162, 2885, 842, ...];
  longest 2,885 chars (old cut was 1,000).
- Qdrant-down call raises `RetrievalUnavailableError: semantic
  retrieval unavailable: Qdrant is unavailable`.
- GET /ready -> 503 `not_ready`: database green, qdrant red
  ("[Errno 111] Connection refused" -- no Qdrant runs in this VM),
  reranker green, model loaded.
- No `crater.db` created in the working copy (smoke DB went to /tmp).

### Zip pristine
sha256 `d6886155a4394a727a32f8e38ac6794d81678ebeb02596c30e7371bacecedb92`
(unchanged after all work).

## Decisions / open points (for the parent)
1. **BM25 kept in normal fusion, fallback deleted.** The task allowed
   keeping BM25 as an explicit labelled fallback only if the diagnosis
   audit explicitly required it; it does not -- it requires the 503.
   Deleting BM25 from fusion as well would have left RRF over a single
   list and risked a silent retrieval regression with no sparse
   replacement in place (that replacement needs a Qdrant re-index).
   If the parent wants the stricter reading (no BM25 anywhere in the
   live path), that is a one-line change in `hybrid_retrieve` plus
   deleting `bm25.py`, but it should be gated on the Miller oracle run,
   not asserted here.
2. **Model 512-token window.** Whole text reaches the model, but
   ms-marco-MiniLM-L-6-v2 truncates internally at 512 tokens. The
   smoke still ranks the §6-5 chunk first on its trouble text, but a
   chunk whose ONLY discriminating text is past ~512 tokens would not
   be scored on it. Not addressed here (audit chose this model).
3. **No live Qdrant in this VM**, so the smoke stubs `semantic_search`.
   A true end-to-end check (real Qdrant + real embeddings + real
   /diagnose/start) still needs a Qdrant server; none is installed
   (no binary, no Docker) and the mock-LLM runs were BM25-only for
   the same reason. Note the consequence: with this step landed, a
   live diagnose call in this VM now correctly 503s instead of
   silently running BM25-only.
4. **No ?degraded=true flag.** Infra allows BM25-only behind an
   explicit debug flag; the diagnosis audit does not require it and
   the task conditions keeping BM25 on the diagnosis audit, so no flag
   was added. Easy to add later if wanted.

## What was NOT done (Step 3+ or later)
- No prompt rewrite, no `_load_product_knowledge` deletion, no
  rank-then-ask (audit Step 2 / task Step 3).
- No `final_top_k` change (still 8; the audit's "final top 5" belongs
  to the prompt step's budget work).
- No Qdrant sparse / prefetch migration, no payload indexes, no API
  key / timeout / retry hardening on the Qdrant client (infra items,
  need provisioning decisions).
- No Celery/Redis, no container split, no Postgres migration.
- No ingestion Step 1 code touched (table_parser / source_router /
  chunking / pipeline unchanged; Step 1 suites re-verified green).
