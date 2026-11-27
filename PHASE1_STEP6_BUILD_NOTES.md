# Phase 1 — Step 6 build notes: oracle gate (audit Step 5 / Phase 1 gate)

Date: 23 Nov 2026. Working copy only: `~/workspace/crater-refactor/`.
Governing: diagnosis-agent audit Step 5 (oracle gate), the amendment
four-resolutions, Phase 0 frozen baseline (`BASELINE.md`), and Steps 1–5
build notes. This step ran the gate only — no implementation code was
changed here, and no container split was started.

## What Step 6 ran

The full Phase 1 tree (Steps 1–5: deterministic table parsing, CrossEncoder
reranker, whole-row ~4,700-char prompt, rank-then-ask, LangGraph loop with
Postgres checkpoints, safety in `run_turn`, twin as thin async client,
CUTs) was scored live over HTTP against the fixed scorer
(`benchmark/harness.py`, Phase 0 fix: punctuation replaced with a space,
`score_citations` page check opt-in, headline reports rankable vs
zero-row symptoms and unrankable rows separately).

- Backend: uvicorn on `http://127.0.0.1:8000` (`gate6-run/backend.log`),
  26 × `POST /diagnose/start` 200 OK (17 Miller + 9 OMNIPRO), then a
  clean shutdown. `/health` 200, `/ready` 200 with Qdrant up.
- Qdrant: local instance on the run's own data dir
  (`gate6-run/qdrant/`, `gate6-run/qdrant.log`), shut down after the run
  (the later smoke test correctly shows `/ready` 503 with Qdrant down).
- LLM: mock server (`gate6-run/mock-server.log`, model `muse-spark`),
  104 queue files = 52 requests = 26 sessions × 2 calls (rank, then ask).
  Same "Muse as the API" path as the frozen baseline run.
- DB: `gate6-run/crater-gate6.db` (+ `crater-gate6_checkpoints.db`).
- Reports frozen (not re-run in this completion pass):
  `frozen/miller-report-phase1-2026-11-23.json`
  (sha256 `9b21f7a86af04cd686dd7943ac6ca48bd799b1c12b29aed2d027f6bcb94cf11d`)
  and `frozen/omnipro-report-phase1-2026-11-23.json`
  (sha256 `cd0a4bc4d1482114cc22c51834b5a8d22d0e3e4187fc9d4080967e83fb33bf35`).
- O1/O2/O3: NOT run over HTTP — the reports record
  "NOT_RUN_OVER_HTTP — O1/O2/O3 are twin checks needing the compiled
  twin and backend/simulation/oracle.py; live mode scores the diagnosis
  level only." The twin side is still 0/38 by construction (see Phase 0
  Miller mapping: 0/38 rows expressible against the ingested twin).

All gate numbers below were recomputed in this completion pass from the
two frozen report files (per-row `in_top5`, per-symptom citation and
discriminating-test scores, ranked-cause counts) — not copied from the
Step 6 worker's summary. They agree with it.

## Gate numbers — Miller (c002b-miller)

Headline (from the report): "causes 22/38 rankable rows across 11
rankable symptoms (6 symptoms with 0 rankable rows: miller-08…miller-13);
citations 79/79 claims (page NOT verified, chunk-id presence only when
not page-verified); unrankable rows excluded from causes: 25/63
(settings 16, technique 9)".

| Symptom | Causes in top 5 | Ranked | Citations | Discriminating test |
|---|---|---|---|---|
| miller-01 | 1/8 | 5 | 5/5 | 1.0 |
| miller-02 | 1/3 | 4 | 4/4 | 1.0 |
| miller-03 | 1/2 | 5 | 5/5 | 1.0 |
| miller-04 | 1/1 | 5 | 5/5 | 1.0 |
| miller-05 | 0/4 | 5 | 5/5 | 1.0 |
| miller-06 | 1/1 | 5 | 5/5 | 1.0 |
| miller-07 | 1/1 | 3 | 3/3 | 1.0 |
| miller-08 | 0/0 | 5 | 5/5 | 1.0 |
| miller-09 | 0/0 | 4 | 4/4 | 1.0 |
| miller-10 | 0/0 | 5 | 5/5 | 1.0 |
| miller-11 | 0/0 | 5 | 5/5 | 1.0 |
| miller-12 | 0/0 | 5 | 5/5 | 1.0 |
| miller-13 | 0/0 | 3 | 3/3 | 1.0 |
| miller-14 | 4/4 | 5 | 5/5 | 1.0 |
| miller-15 | 4/4 | 5 | 5/5 | 1.0 |
| miller-16 | 4/6 | 5 | 5/5 | 1.0 |
| miller-17 | 4/4 | 5 | 5/5 | 1.0 |
| **Total** | **22/38** | — | **79/79** | **17/17** |

Single-guess sessions: **0/17** (smallest ranking is 3 causes:
miller-07, miller-13). Baseline Muse run: also 0 single-guess sessions.

## Gate numbers — OMNIPRO (c001-omnipro, reconstructed oracle)

Headline: "causes 24/35 rankable rows across 9 rankable symptoms
(0 symptoms with 0 rankable rows: none); citations 35/35 claims (page
NOT verified); unrankable rows excluded from causes: 0/35".
The oracle is the Phase 1 reconstruction
(`frozen/omnipro-oracle-35.json`, `frozen/omnipro-oracle-35-RECONSTRUCTION.md`)
— there was no pre-existing 35-row OMNIPRO oracle file in the zip, so
"no OMNIPRO regression" can only be judged against the previously
measured case 1 (4/4), not against a prior 24/35-style total.

| Case | Causes in top 5 | Ranked | Citations | Discriminating test |
|---|---|---|---|---|
| omni-01-wire-not-feeding | 4/4 | 4 | 4/4 | 1.0 |
| omni-02-wire-stops | 5/6 | 5 | 5/5 | 1.0 |
| omni-03-birds-nest | 4/4 | 4 | 4/4 | 1.0 |
| omni-04-arc-not-stable | 0/5 | 4 | 4/4 | **0.0** |
| omni-05-does-not-function | 2/4 | 4 | 4/4 | 1.0 |
| omni-06-lcd-dark | 5/5 | 5 | 5/5 | 1.0 |
| omni-07-no-arc | 2/3 | 3 | 3/3 | 1.0 |
| omni-08-porosity | 0/2 | 3 | 3/3 | 1.0 |
| omni-09-weak-arc | 2/2 | 3 | 3/3 | 1.0 |
| **Total** | **24/35** | — | **35/35** | **8/9** |

Single-guess sessions: 0/9. The one discriminating-test miss is
omni-04: the step is a picker test ("check the gun and ground cable
connections are tight", gain 0.96 bits) but it scores 0.0 — its
`separates` list names only one ranked cause and the step
content+rationale does not name two ranked causes in the form the
harness rule requires (the rationale names the registry's cause
wording — "Loose MIG Gun cable or ground cable." — not the ranked
"Loose electrode cable or ground cable."). The Miller tests are all
ranking-fallback questions, which is why they score 1.0; the OMNIPRO
picker tests use registry wording, which can miss the harness's
string match.

## Gate verdict, criterion by criterion

| Criterion (as written) | Result | Verdict |
|---|---|---|
| Miller beats Gemini 19/38 | 22/38 | **PASS** (tie with Muse baseline, beats Gemini by 3) |
| Miller beats Muse 22/38 | 22/38 | **FAIL — tie, not a beat** |
| OMNIPRO case 1 stays 4/4 | 4/4 | **PASS** |
| Discriminating test 17/17 (Miller) | 17/17, score 1.0 every symptom | **PASS** (baseline was 0/17) |
| Single-guess sessions 0/17 (Miller) | 0/17 | **PASS** |

**Overall gate verdict: FAIL as written, on the "beat 22" criterion
alone.** Everything else passes. The gate does not pass by rewording:
22/38 ties the frozen Muse baseline, it does not beat it. OMNIPRO's
discriminating test (8/9, omni-04 at 0.0) was not part of the written
gate criteria, but it is recorded here because it is the same class of
failure the Miller criterion was written to catch.

## Why it ties: miller-16 loss cancels the miller-02 gain

Row-level diff against the frozen Muse baseline
(`frozen/miller-report-mock-2026-11-23.json`, 22/38, citations 69/69,
discriminating 0/17) shows exactly two changed rows, net zero:

- **miller-02 GAINED (+1).** Oracle row "Connect work clamp to get
  good metal to metal contact." was unranked in the baseline and is
  rank 1 in Phase 1 ("Work clamp has a poor connection"). The
  baseline's rank-1 cause, "Poor metal to metal contact at work
  clamp", did not match the oracle remedy text under the fault-aware
  matcher; the Phase 1 phrasing does. (The other two miller-02 rows
  — contact tip, HL.P 003 — stay missed in both runs.)
- **miller-16 LOST (−1): the split-cause loss.** The baseline's 5th
  ranked cause was a COMBINED cause: "Gas solenoid valve not
  operating, or loose or broken wires to gas solenoid" — one ranked
  line that matched both oracle rows at rank 5. Phase 1's
  rank-then-ask split it into separate structured causes, and only
  one survived in the top 5: "Gas solenoid valve not operating."
  (rank 5). "Loose or broken wires to gas solenoid." is unranked in
  Phase 1. The other miller-16 rows are unchanged (4/6 in Phase 1
  vs 5/6 in the baseline; "Incorrect primary voltage…" missed in
  both). This is a real cost of the Step 4 split, not a scorer
  artifact: the old single-call output merged two solenoid faults
  into one line and the matcher credited both.

Everything else is unchanged row-for-row, including the hard rows:
miller-01 stays 1/8, miller-05 stays 0/4, miller-14/15/17 stay 4/4.
Citations improved in count (79/79 vs 69/69) because the rankings
grew to a full 5 causes in more sessions — the denominator is claims,
not rows — but the page is still NOT verified in either run.

## Step 6 worker error, and what was re-run to complete the record

The Step 6 worker errored on a runtime restart after the gate run
itself had finished and the reports were frozen, but before it wrote
these notes. No error text survives in `gate6-run/`; the observable
fact is the suite re-run log `gate6-run/suites-rerun.log` (46 lines):
step1 41 passed, step2 46 passed, then step2-wider starts and the log
ends mid-output (dots, no summary, no `EXIT:` line) — steps 3–5 and
the smoke were never re-run in Step 6. The gate run itself is intact:
both frozen reports, `backend.log`, `miller-run.log`,
`omnipro-run.log`, `mock-server.log`, `qdrant.log` and the mock-LLM
queue are all complete, and the gate numbers were recomputed from the
frozen reports in this pass, so the worker error touched the record,
not the gate.

Completion re-run (this pass, 23 Nov 2026 09:36–09:41 UTC): the
original `gate6-run/rerun_suites.sh` was NOT run as-is — it truncates
its log (`: > $LOG`). A modified copy (`/tmp/rerun_step6_complete.sh`)
appended to a new log, `gate6-run/suites-rerun-complete.log` (194
lines); the partial `suites-rerun.log` was left untouched. Step 1 and
Step 2 were not re-run (already passed in Step 6); step2-wider was
re-run because it was the interrupted one.

| Suite | Result | Pre-Step-6 |
|---|---|---|
| step2-wider (60) | 60 passed | 60 (interrupted in Step 6, now complete) |
| step3-52 | 52 passed | 52 |
| adj54 | 54 passed | 54 |
| s29 | 29 passed | 29 |
| s5 (follow-up walk) | 5 passed | 5 |
| s22 | 22 passed | 22 |
| step4-71 | 71 passed | 71 |
| step5-26 (incl. Postgres checkpoint) | 26 passed | 26 |
| phase3d | 5 passed | 5 |
| phase8a | 4 passed | 4 |
| Reranker smoke | SMOKE PASS | PASS |

The step5 Postgres checkpoint test ran live, not skipped: a fresh
pgserver instance was started for this re-run (unprivileged user
`pgtest`, created for the purpose; socket `/tmp/pgdata-step6`, port
5432, user `postgres`, db `postgres` — Step 5 used `/tmp/pgrun:55433`,
same user/db, pgserver chose this socket; the venv needed `a+rX` on
the `pgserver`/`psutil`/`platformdirs`/`fasteners` packages before
`pgtest` could import them). DSN
`host=/tmp/pgdata-step6 port=5432 user=postgres dbname=postgres`.
Verified after the run: the `checkpoints` table holds 13 rows, so
the Postgres resume test executed. The smoke's `/ready` 503 is
expected here: Qdrant was shut down after the gate run, so the smoke
reports `qdrant unreachable: Connection refused` while database and
reranker are ready.

## Open items (carried forward, verified against the tree in this pass)

- **`twin_verification.py` is 293 lines, not ~35.** The audit's
  "~35-line thin client" was not met literally (Step 5 notes): the
  four kept pure helpers (~90 lines), the HTTP 202+poll loop, the
  sync wrapper and the defence-in-depth demotion live in the same
  module; the client itself is ~190 lines. The in-process tournament
  execution it replaced is gone from the module.
- **Endpoints not deleted.** `verify-repair` (`routes/diagnostics.py:1069`),
  `verify-hypotheses` (:1257), `early-tournament` (:1151),
  `upstream-walk` (:1220), `next-test` (:992), `guided-checks`
  (:1029), safety GET (:1337), fleet-patterns GET (:1353) all still
  live. Live tests use them; removal is deferred to an
  API-versioning step (Step 5 decision).
- **Verify job store is in-memory.** `routes/verify.py:32` `_JOBS`
  dict; no job survives a restart. Postgres + Redis at the split.
- **`visual.py` not moved.** Still `backend/simulation/visual.py`
  (111 lines), endpoint `routes/simulation.py:105-114`
  (`GET /simulation/twins/{id}/visual`). It moves with its endpoint
  to gateway/frontend rendering; there is no gateway container yet.
- **`uv.lock` stale.** `pyproject.toml` declares langgraph,
  langgraph-checkpoint-postgres/sqlite and psycopg (3 mentions);
  `uv.lock` contains 0 langgraph mentions. Not updated in Step 5.
- **MiniLM 512-token window.** Whole text reaches the CrossEncoder,
  but text past ~512 tokens is not scored on
  (`backend/retrieval/reranker.py:7`). Unchanged since Step 2.
- **`get_client()` NO_PROXY crash still open.**
  `backend/retrieval/vector_store.py:16-17` builds `QdrantClient`
  with no `sanitize_proxy_env_for_httpx()` call before it; the
  sanitize runs only in `embeddings.py` and `reranker.py`. A
  bracketed-IPv6 NO_PROXY entry crashes Qdrant client construction
  (`Invalid port: ':1]'`). Found in Step 3, test-only stub applied
  there, production path still open. No live Qdrant in this VM, so
  it changes no outcome here.
- **No live Qdrant in this VM** (outside the gate run's own
  instance, which was shut down afterwards). A live diagnose call
  still correctly 503s on `/ready` when Qdrant is down.
- **Full `oracle.py` move deferred.** Only the pre-step landed:
  `backend/simulation/oracle_shared.py` (47 lines,
  `normalize_text` + `load_fault_injections`); `oracle.py`
  re-exports them. The move to harness-scorer was deferred to
  Step 6 prep and is still open.
- **Other carried-forward items:** remedy-sentence causes ending
  in "." render with doubled punctuation (Step 4, left as-is);
  late-turn prompts with 5 verdict lines unmeasured (Step 3);
  `db/models.py` Expert/ExpertInterview models kept while the
  capture trio was cut, so outcomes can still be recorded
  `pending_review` with no review UI (Step 5); confidence is now
  computed, not asked (Step 5, done).

## What was NOT done here

- No Phase 1 implementation code was modified in this completion
  pass. No oracle gate re-run (the frozen reports stand; the gate
  numbers were recomputed from them, not re-measured). No ranking
  work, no container split, no Celery/Redis, no twin-core changes.
  The gate verdict is unchanged: **FAIL on "beat 22" (22/38 ties)**.
