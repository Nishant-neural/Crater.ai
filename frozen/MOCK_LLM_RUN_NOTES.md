# Mock-LLM test path — build & run notes (23 Nov 2026)

## What this is

The Crater backend + Miller benchmark ran end-to-end in the VM with **no
external LLM provider**. A small mock server stood in for the provider, and
a Muse-model agent answered every prompt the backend sent — genuinely,
from the prompt alone, never from the answer key.

## How it works

- `mock_server.py` (Python stdlib only, `127.0.0.1:18080`): `POST
  /v1/chat/completions` writes the full request JSON to
  `queue/req-<n>.json`, polls every 0.5s for `queue/resp-<n>.json`, and
  returns its `"content"` string as
  `{"choices":[{"message":{"content": ...},"finish_reason":"stop"}]}` —
  exactly what `OpenRouterProvider` parses. `response_format`,
  `temperature` and `max_tokens` are accepted and ignored.
  `GET /healthz` reports received/answered/pending counts.
- The answering agent polls the queue, reads each prompt, and writes the
  response file. Rerank calls get back a bare JSON array of integer
  scores; diagnosis calls get back the NextStep JSON object
  (`hypotheses` / `eliminated_hypotheses` / `overall_confidence` /
  `next_step`). It never opens anything under
  `~/workspace/crater-benchmarks/evaluations/` (oracle/answer-key files),
  the saved Miller reports, or the harness scoring code.
- The backend needed **no code change at all** — env only:
  `OPENROUTER_BASE_URL=http://127.0.0.1:18080/v1`,
  `OPENROUTER_MODEL=muse-spark`, `OPENROUTER_API_KEY=dummy`,
  `DIAGNOSIS_PROVIDER=openrouter`, `RERANK_PROVIDER=openrouter`,
  `DATABASE_URL=sqlite:////home/hatch/workspace/crater-phase1/mock-llm/crater-test.db`.

## Reproduce

```bash
# 1. Mock server (from this dir)
python3 mock_server.py            # 127.0.0.1:18080, logs to stdout
# 2. Backend (from ~/workspace/crater-phase1/v12)
DATABASE_URL="sqlite:////home/hatch/workspace/crater-phase1/mock-llm/crater-test.db" \
OPENROUTER_BASE_URL="http://127.0.0.1:18080/v1" OPENROUTER_MODEL="muse-spark" \
OPENROUTER_API_KEY="dummy" DIAGNOSIS_PROVIDER="openrouter" RERANK_PROVIDER="openrouter" \
~/workspace/crater-phase1/.venv/bin/python -m uvicorn backend.api.main:app --host 127.0.0.1 --port 8000
# 3. Answering agent on the queue (see the coordinator brief for its rules),
#    then the Gate 0 driver and the Miller harness below.
# 4. Stop the answerer: touch STOP in this dir.
```

DB: a **copy** of `~/workspace/user/files/crater-96db6588.db`
(`crater-test.db` here). The uploaded original was never touched. It
holds OMNIPRO (product `ccc3bae7…`, revision `3c246c9a…`) and Miller
(product `01c956e5…`, revision `30e0e856…`), both twins ACTIVE. Lincoln
has a product row but no twin, so it cannot run.

## Timeout decision

No backend timeout change was needed. The mock polls up to 170s (under
the backend's 180s `urlopen` timeout); the harness allows 120s per
`/diagnose/start`. Actual per-call latency was ~11s on average (Gate 0:
9 turns in 211s; Miller: 17 starts in 413s), so nothing came close to a
limit. The provider's 180s timeout in `provider.py` is untouched.

## Qdrant / retrieval

Qdrant is **not** running in this VM (nothing answers on :6333). The
backend log confirms the expected path on every turn: "Semantic
retrieval unavailable; using BM25 only". All retrieval in both runs was
BM25-only. No attempt was made to stand Qdrant up.

## Gate 0 — OMNIPRO case-1 diagnosis (23 Nov 2026)

- Symptom: "Wire Feed Motor Runs but Wire Does Not Feed Properly".
  9 turns, 18 mock calls (9 rerank + 9 diagnosis), all answered.
  **No provider error, no 500** (backend log: 0 errors, 0 × 500).
- Hypotheses stayed grounded: the 4 case-1 causes, confidence honestly
  0.25 (every observation was an honest "I don't know — I cannot check
  this right now"; there is no machine here to observe). A different
  discriminating test was picked each turn, no repeats.
- The turn-bound ending **did fire**: the timeline has
  `diagnosis_unavailable` entries at turns 8 and 9 ("Diagnosis ended
  without a twin-validated conclusion").
- **Finding (product gap, not a mock artifact):** the ending never
  reaches the user. After `run_turn` sets the step to the
  unavailable-attempt text, the endpoint's post-turn tournament
  (`_apply_early_tournament`, `backend/api/routes/diagnostics.py`)
  overwrites the step content with a discriminating-test question. Its
  guard only protects the cannot-express ending (`not _unexpressible`);
  the turn-bound ending gets rewritten even though the comment in
  `run_turn` says it is "never rewritten". The session stays `active`
  (status only flips on a conclusion), so a user who keeps answering is
  asked more tests after the diagnosis already ended. The session was
  left at 9 turns / active; continuing to answer would just repeat the
  pattern. Full trace: `gate0-run.log`, final view `gate0-final.json`.

## Miller harness — Test 1b with the model as the API (23 Nov 2026)

Command (venv python, stdout saved):

```bash
python3 harness.py --case c002b-miller --base-url http://localhost:8000 \
  --product-id 01c956e5-84e5-484c-a280-ee9e15c348a0 \
  --revision-id 30e0e856-62b9-4fd7-ac05-52ee1bf3e742
```

Report: `miller-report-mock-2026-11-23.json` (in this dir). 17 symptoms,
34 mock calls, all answered; 18 `POST /diagnose/start` in the backend
log (1 Gate 0 + 17 Miller), 0 × 500.

Headline (harness-scored, fault-aware matcher):

- **Top-5 ranked causes: 22/38 (57.9%)** — previous best was 5/38
  fault-aware (run #1, Gemini, fix2) and 2/38 (run #2, OpenRouter, cut
  short by the cap). Per symptom: miller-01 1/8, -02 0/3, -03 1/2,
  -04 1/1, -05 0/4, -06 1/1, -07 1/1, -14 4/4, -15 4/4, -16 5/6,
  -17 4/4. (miller-08–13 score 0 rows by design — no twin-expressible
  rows for those symptoms.)
- **Citations: 17/17 symptoms at 1.0** — every ranked hypothesis carried
  an evidence chunk id. (Run #1: 12/17; run #2: 4/17.)
- **Discriminating test: 0/17.** The scorer counts 1 only when the step
  text names at least two distinct top-5 causes by their full phrase
  (`score_discriminating_test`). The model's questions name at most one
  cause (e.g. miller-04 names "Faulty fan-on-demand circuit"), and the
  endpoint's discriminating-test rewrite names the *test*, not the
  causes — so the scorer cannot see them. The questions are often
  genuinely discriminating in practice; they just don't frame it as
  "this test separates cause A from cause B". Same weakness as runs
  #1/#2 (1/17 and 0/17), not a regression.
- All 17 sessions `active` (single start call each, as the harness does).

## What the numbers mean

With the model as the API, these scores measure the **system** —
retrieval (BM25-only here), the tournament/gating, the question flow,
and the scoring — not a production provider. The jump from 5/38 to
22/38 is mostly the model being a far stronger reasoner than the
free-tier models *and* every session actually getting answered (no 503s,
no cap). The provider can be swapped back later; the numbers will move
with the provider. What this run proves is that the backend, the loop,
and the benchmark harness all work end-to-end without any external
provider in the path.

## Answering-agent report (from the agent itself)

- 52/52 requests answered: 26 rerank + 26 diagnosis, 0 JSON-repair calls.
  Gate 0 = req 1–18 (9 turns); Miller = req 19–52 (17 symptoms × 2 calls).
  No fallbacks used; no request left unanswered. The agent never opened
  the oracle/answer-key files, the saved Miller reports, or the harness
  scoring code.
- **One incident — req-1 (Gate 0 turn-1 rerank):** the agent's first
  response file was written as a bare JSON array instead of the required
  `{"content": "..."}` wrapper. The mock server returned HTTP 500, and
  the backend fell back to the pre-rerank candidate order for that call
  (the fix6 rerank guard working as designed — the turn proceeded on
  BM25 order instead of crashing). The agent caught the mistake by
  reading `mock_server.py`, fixed the format for all 51 later responses,
  and did not overwrite `resp-1.json` (already consumed). So Gate 0's
  first turn ran un-reranked; everything after it was reranked normally.
- The agent's own observation from the prompts: in Gate 0 the twin
  verification block listed all four manual causes UNAVAILABLE every
  turn (ambiguous `Wire Feed Mechanism`/`Welder` components, missing
  liner assembly / Wire Feed Motor components) — the OMNIPRO twin's
  single transition cannot express them, so no conclusion was ever
  possible and the agent kept asking new untested checks at honest low
  confidence. Miller sessions each ended after the single turn-0
  diagnosis call, as the harness does.

## What was NOT run

- Lincoln (no twin — ingestion crashed; nothing to run against).
- O1/O2/O3 (not run over HTTP by design; the harness says so itself).
- No ingestion/extraction (the DB was already ingested; no new manual).
- No Gemini / OpenRouter / Groq calls anywhere in either run.
- The 13 hand-run session tests and Test 5 (not part of this run).

## Audit trail

- Queue: `queue/req-<n>.json` + `queue/resp-<n>.json`, n = 1–52
  (18 Gate 0 + 34 Miller), all answered, none pending. The builder's
  verification pair is archived at `queue/_verification/`.
- Logs: `mock-server.log`, `backend.log`, `gate0-run.log`,
  `miller-run.log`. Gate 0 final view: `gate0-final.json`.
- Servers were left running at the end of the run (mock :18080,
  backend :8000, pids in `mock-server.pid` / `backend.pid`); stop them
  with `kill $(cat mock-server.pid backend.pid)` when done.
