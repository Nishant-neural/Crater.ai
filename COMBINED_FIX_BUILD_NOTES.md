# Combined fix build notes — fix3 + fix3 extension + fix4 + O1/O2/O3 runner + fix5 + harness scorer fix

Date: 21 Nov 2026. Zip: `project-review-phase6-readiness-ingestion-gemini-fix-combined.zip`
(one zip replacing the separate fix3 / fix4 / fix5 zips — deploy this whole zip, not the pieces).

This file is the single read-me for the combined zip. The six per-fix notes are
also in the zip and stay the detailed record; where they disagree with this file,
this file wins — see §7 for the two corrections.

## 0. What this zip is

Six independently built and verified fixes, packaged together so you deploy once:

| # | Fix | What it fixes, in one line |
|---|---|---|
| 1 | Fix3 — diagnosis loop | A diagnosis could conclude before the twin had verified the top theory; tests could repeat; sessions force-escalated at 8 turns. |
| 2 | Fix3 extension | The cannot-express path missed one shape: component matched in the twin, but no simulation rule describes a fault in it. |
| 3 | Fix4 — Lincoln PDF crash | Lincoln ingestion crashed on a spot-colour image (page 51, xref 915). |
| 4 | O1/O2/O3 runner | The benchmark harness scores only the chat side; nothing ran the twin oracle (O1/O2/O3) against a real DB twin. |
| 5 | Fix5 — OpenRouter fallback | Gemini's free tier 503s on diagnosis calls, which killed live benchmark runs. |
| 6 | Harness scorer fix | The harness scored Miller 3/38 when the diagnosis was right — remedy text can never string-match a fault phrasing. |

Expected full-suite count in this tree: **494 passed, 0 failed**
(433 fix2 baseline + 13 fix3 + 2 extension + 7 fix4 + 16 runner + 22 fix5
+ 1 from fix3's `test_diagnostics.py` rewrite, 4 → 5 collected).
The combined full-suite run for this zip is in §8.

## 1. Fix3 — diagnosis loop (verify before conclusion)

Per-fix notes: `VERIFY_BEFORE_CONCLUSION_BUILD_NOTES.md`.

What it fixes: the diagnosis agent could conclude on its top-ranked cause
without the twin ever verifying it, could ask the same discriminating test
twice, and force-escalated at 8 turns regardless of what the model thought.
Now: a conclusion about a cause is demoted unless the twin verified it
(`VALIDATED`); pre-turn tournament verdicts are visible in the LLM prompt
before concluding; a discriminating test is never repeated for the same cause;
escalation is removed entirely — inconclusive sessions end with best guesses,
the next test, and what's missing; and where the twin cannot express the
cause at all, the session ends plainly instead of looping.

Files (all under the v12 tree):

- `backend/diagnostics/twin_verification.py` (NEW) — the verify-before-conclusion gate.
- `backend/diagnostics/agent.py` — gate wiring, cannot-express ending, no-repeat tests.
- `backend/diagnostics/prompts.py` — pre-turn verdicts in the prompt.
- `backend/diagnostics/schema.py` — verdict / cannot-express fields.
- `backend/db/models.py` — session fields the gate needs.
- `backend/diagnostics/backtracking.py` — backtracking honours the gate.
- `backend/api/routes/diagnostics.py` — `respond()` + cannot-express surfacing.
- `backend/simulation/tournament.py` — `cannot_express` signal on rows.
- `tests/test_verify_before_conclusion.py` (NEW, 13 tests — 12 gate/no-repeat/escalation + 1 added during verification).
- `tests/test_diagnostics.py`, `tests/test_diagnosis_robustness.py`,
  `tests/test_phase456_followup_walk.py`, `tests/test_hypothesis_tournament.py` — updated.
- `VERIFY_BEFORE_CONCLUSION_BUILD_NOTES.md` (NEW).

Verification (at build time): baseline 433 passed / 0 failed; post-change full
suite **492 passed, 0 failed**. Proof-twin numbers re-measured unchanged:
oracle 20/35, 648 transitions, 18 components, Demo 7 transitions.

## 2. Fix3 extension — matched component with no fault rule

Per-fix notes: `VERIFY_BEFORE_CONCLUSION_BUILD_NOTES.md` §8.

What it fixes: one cannot-express shape was still demoting instead of ending
plainly — a cause whose component IS matched in the twin, but no simulation
rule describes a fault in it (`build_verification_experiment` returns
available False, "No simulation rule in this twin describes a fault in
entity:…"). The tournament's legacy branch returned that dict with no
`cannot_express` key, so `run_tournament` normalised it to False and the gate
demoted-and-asked-the-next-cause instead of ending the session with the plain
cannot-express ending.

Files:

- `backend/simulation/tournament.py` — `cannot_express` signal only (the
  `cannot_express` flag on the no-match registry path + `row_cannot_express()`
  helper; the gate's cannot-express branch itself is untouched).
- Docstrings only: `backend/diagnostics/agent.py` (gate docstring) and
  `backend/diagnostics/twin_verification.py` (`cannot_express_cause`
  docstring) — both now list all three cannot-express shapes.
- `tests/test_verify_before_conclusion.py` — 2 new tests
  (`test_matched_component_with_no_fault_rule_carries_cannot_express` and the
  ambiguous-cause boundary test pinning `cannot_express` False there).

Verification (at build time): full suite **494 passed, 0 failed**.
Known gap left open, see §6: no test pins the OTHER legacy-path unavailable
reasons staying `cannot_express` False.

## 3. Fix4 — Lincoln PDF image crash

Per-fix notes: `PDF_IMAGE_FIX_BUILD_NOTES.md`.

What it fixes: Lincoln `operator-manual.pdf` ingestion crashed with
`FzErrorArgument` at page 51, xref 915 — a `Separation(DeviceCMYK, Black)`
spot-colour image that PyMuPDF's pixmap path cannot write as PNG. The loader
now converts such images to an alpha-free DeviceRGB PNG instead of crashing;
an image that still cannot load is skipped with a warning naming page + xref,
and the page's text still loads.

Files:

- `backend/ingestion/pdf_loader.py` — colourspec conversion + skip-with-warning.
- `tests/test_pdf_loader_images.py` (NEW, 7 tests).
- `PDF_IMAGE_FIX_BUILD_NOTES.md` (NEW).

Verification (at build time): 7 new tests, all passing. Real-PDF extraction:
Lincoln 104 pp — was CRASH, now no crash: 41 pages with images, 68 listings,
56 unique PNGs, all valid, all alpha-free; page-51 image 1946x1250 DeviceRGB.
Miller unchanged (9 listings, 6 unique files). OMNIPRO unchanged (34 listings
over 10 pages). Full suite in the fix4-only tree: 469 passed — see the
correction in §7(a): that tree's baseline was NOT 433, it was 462 collected
because fix3's files had already landed in the shared tree; the correct
statement is 433 fix2 baseline + 7 = 440 for fix4 alone, 494 in this
combined tree.

## 4. O1/O2/O3 benchmark oracle runner

Per-fix notes: `BENCHMARK_ORACLE_RUNNER_BUILD_NOTES.md`.

What it is: the benchmark harness scores only the diagnosis level over HTTP
and explicitly does NOT run O1/O2/O3. This script is the other half — it runs
the Phase 3A oracle (pure, no LLM, no network) against the ACTIVE compiled
twin in a crater DB, using a benchmark oracle JSON as ground truth:

- O1 — the healthy twin acts healthy.
- O2 — each manual fault shows its documented symptom.
- O3 — different faults give different symptoms.

Files:

- `scripts/run_benchmark_oracle.py` (NEW).
- `tests/test_benchmark_oracle_runner.py` (NEW, 16 tests).
- `BENCHMARK_ORACLE_RUNNER_BUILD_NOTES.md` (NEW).

Verification (at build time): 16 tests, all passing. Runner core reproduces
the known 3C numbers on the proof twin exactly: O2 20/35, O1 pass, O3 57/58.
CLI against the real crater-10 DB (old OMNIPRO snapshot twin, 201 components,
0 transitions): Lincoln O2 0/19, O1 fail, O3 0/31; Miller O2 0/38, O1 fail,
O3 0/71 — expected honest results, not a bug: that twin predates the Phase 2
dedupe fix and has no transitions. CLI against the real Miller twin (96
components, 1 transition): O2 0/38, O1 fail, O3 0/71, exit 0. No Lincoln twin
result is claimed — no Lincoln twin exists yet (its ingestion crashed; fix4
above is what unblocks it). Exit 0 when a report was produced even if O1
fails; exit 2 only when it cannot run (no twin, bad oracle file, bad DB).

## 5. Fix5 — OpenRouter fallback + Gemini 503/429 retry

Per-fix notes: `OPENROUTER_FALLBACK_BUILD_NOTES.md`.

What it fixes: Gemini's free tier 503s on diagnosis calls, which killed live
benchmark runs. Diagnosis + rerank now default to OpenRouter (free tier);
extraction / integration / vision / expert stay on Gemini, by your decision.
Both providers now retry 429 AND 503 (3 attempts, 1s then 2s backoff,
honouring `Retry-After` capped at 120s); a 404/401 raises instead of
silently returning None.

Files:

- `backend/llm/provider.py` — `OpenRouterProvider` (stdlib `urllib`, zero new
  dependencies) + Gemini retry.
- `backend/llm/gateway.py` — routing for the new provider.
- `backend/config.py` — `openrouter_api_key` / `openrouter_base_url` /
  `openrouter_model`, diagnosis + rerank default `openrouter`.
- `tests/test_llm_openrouter_fix5.py` (NEW, 22 tests, all mocked HTTP).
- `OPENROUTER_FALLBACK_BUILD_NOTES.md` (NEW).
- Assembly follow-ups in THIS zip (not in the fix5 zip): `.env.example`
  (OpenRouter lines added; see §6) and `llm-probe.py` (prints
  `OPENROUTER_API_KEY` status; see §6).

Default model — verified live 21 Nov 2026 by `GET
https://openrouter.ai/api/v1/models` (476 models, no key needed):
**`google/gemma-4-31b-it:free`** — context 262144, max completion 32768,
`response_format` supported, $0. The brief's suggested
`meta-llama/llama-3.3-70b-instruct:free` is **DELISTED** (only the paid
variant remains, $0.71/1M tokens — it must not be the default). Re-verified
for this combined zip (21 Nov 2026, same endpoint): Gemma-4-31B `:free`
still listed with `response_format` in `supported_parameters`, and
`llama-3.3-70b-instruct:free` still absent. See §7(b) for the correction to
the fix5 notes' Nemotron claim — the Gemma pick still stands.

Verification (at build time): 22 new tests, all passing (mocked HTTP — no
live calls). Full suite in the fix5 build tree: 491 passed, 0 failed (that
tree was mid-merge with the other fixes, so 491 ≠ the combined 494; the
combined count is in §8).

## 6. Harness scorer fix — fault-aware top-5 matching

Per-fix notes (in the zip under `benchmark/`):
`benchmark/HARNESS_SCORER_FIX_NOTES.md`.

What it fixes: the Miller oracle's §6-5 rows have no cause column in the
manual, so their `cause` field holds the manual's remedy text (e.g.
"Be sure line disconnect switch is On"). The app ranked the same fault as
"Line disconnect switch is off" — the old string matcher could never match
remedy text against a fault phrasing, so those rows scored 0 even when the
diagnosis was right. A ranked cause now also matches when the row's
structured `fault` (component AND value) both appear as whole-word token
sequences in it. Antonyms stay distinct (`off` never matches `on`),
substrings can't match (`low` never matches `below`), multi-word values must
appear whole and in order. `normalize_text` and the old string match are
untouched, so every row that matched before still matches.

Files (in the zip under `benchmark/` — drop `harness.py` into your
`~/workspace/crater-benchmarks/` folder, replacing the old one; the notes file
goes beside it):

- `benchmark/harness.py` — `fault_matches` + `score_top5(..., fault=None)` +
  `score_session(..., faults=None)` + `--rescore` mode. No oracle JSON,
  manual, transcript, or v12 backend file touched.
- `benchmark/HARNESS_SCORER_FIX_NOTES.md`.

Verification (at build time): re-score of the saved report only, no live
server calls, no live sessions: **Miller top-5 3/38 → 5/38**. Dry-run
15/15, exit 0, with the stub checks unchanged plus new fault-aware
matching guards (component-without-value scores 0, antonym scores 0,
substring scores 0).

## 7. Corrections to the per-fix notes (this file wins)

The per-fix notes are NOT edited; the corrections live here.

### (a) Fix4 baseline count

`PDF_IMAGE_FIX_BUILD_NOTES.md` says "462 pre-existing + 7" (469 in the
fix4-only tree). That 462 was the shared tree at the time — fix3's files
had already landed in it — not the fix2 baseline. Correct statement:
**433 fix2 baseline + 7 = 440 for fix4 alone; 494 in this combined tree.**
The fix4 work itself (7 tests, Lincoln/Miller/OMNIPRO extraction numbers)
is unaffected.

### (b) Fix5 Nemotron claim

`OPENROUTER_FALLBACK_BUILD_NOTES.md` says "the Nemotron free variants do
not support `response_format` at all", and the same sentence is copied in
the comment in `backend/config.py` (not edited here — product code was
frozen for this packaging; fix the comment on the next touch). That is
wrong for one variant. Re-checked live 21 Nov 2026 against
`GET https://openrouter.ai/api/v1/models`:

- `nvidia/nemotron-3.5-super-vl:free` — context 1,000,000, pricing $0,
  **`response_format` IS in `supported_parameters`** (alongside
  `structured_outputs`). So the blanket claim is false for this variant.
- The other free Nemotron variants genuinely do NOT list it:
  `nvidia/nemotron-3-lightning:free`, `nvidia/nemotron-3-ultra-550b-a55b:free`,
  `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free` — no
  `response_format` in their `supported_parameters`.

**The Gemma pick still stands.** `nemotron-3.5-super-vl` is a
vision-language model (VL), not a general instruction-tuned model, and it
is the preview/obscure end of the list; Gemma-4-31B remains the largest
well-known instruction-tuned `:free` model with `response_format` support
and a 262k context. If OpenRouter delists Gemma-4-31B too, re-check the
same endpoint and set `OPENROUTER_MODEL` to another live `:free` id —
super-vl:free is now a legitimate fallback candidate, where the fix5 notes
had ruled the whole Nemotron family out.

## 8. What you need to set (.env)

Copy the new lines from the zip's `.env.example` into your `.env`
(it still said "Supported providers: anthropic, gemini" — now fixed, and
the routing block now shows the real defaults):

```text
OPENROUTER_API_KEY=<your free key from openrouter.ai>
OPENROUTER_MODEL=google/gemma-4-31b-it:free
```

- The key is free: sign up at https://openrouter.ai, then Keys. Nothing
  else in `.env` changes for this zip.
- `DIAGNOSIS_PROVIDER` / `RERANK_PROVIDER` are `openrouter` by default in
  code now; the `.env.example` routing block shows that. To go back to
  Gemini for diagnosis, set `DIAGNOSIS_PROVIDER=gemini` (and
  `RERANK_PROVIDER=gemini` for rerank).
- Extraction / integration / vision / expert stay on Gemini — no change.
- `OPENROUTER_BASE_URL` defaults to `https://openrouter.ai/api/v1`; leave it
  alone unless you point the same adapter at another OpenAI-compatible
  endpoint.

## 9. Your check (after deploying, before any benchmark)

From the project root, venv active:

```text
python llm-probe.py
```

Confirm all three:

1. The `diagnosis` and `rerank` route lines read
   `provider=openrouter model=google/gemma-4-31b-it:free`.
2. The `extraction` / `integration` lines still read your Gemini provider.
3. The `OPENROUTER_API_KEY:` line reads `present, N chars, starts XXXX...`
   (never the key itself), and the tiny diagnosis call prints
   `CALL WORKED: reply='OK' ...`.

Reading failures: `CALL RETURNED None` = the OpenRouter key is empty/missing
in `.env`. `401` = bad key. `404` = the model id was delisted — re-check
`GET https://openrouter.ai/api/v1/models` and set `OPENROUTER_MODEL`.
`429` late in the day = the OpenRouter free daily cap (see §10), not a bug.
The probe was updated in this zip: it prints the OpenRouter key status and
its error hints now name the resolved diagnosis route's provider/model
instead of assuming Gemini.

## 10. Deploy order

1. Deploy this whole zip (it is the fix2 tree + the six fixes). Restart the server.
2. Set `OPENROUTER_API_KEY` in `.env` and run the §9 probe check.
3. Fresh Lincoln ingestion job (fix4 unblocks it — it crashed before).
4. Gate 0 case-1 re-run (fix3 gate + extension now in the loop).
5. Miller harness re-run (with the fixed scorer — expect the re-scored 5/38
   as the floor, not 3/38).

## 11. Known items (not bugs to re-report)

- **OpenRouter free daily cap.** Free `:free` models have a daily request
  cap (size depends on all-time credits purchased). Diagnosis + rerank are
  ~2 calls per benchmark turn. Running the whole benchmark in one day
  (Miller + Lincoln + the 13 hand-run cases) is the thing to watch: if
  calls start returning 429s late in the day, that is the cap, not a bug —
  spread the runs across days or add credits. Extraction stays on Gemini,
  so ingestion is unaffected.
- **§8.7 boundary coverage gap (fix3 extension).** No test pins the other
  legacy-path unavailable reasons ("Fault did not reproduce…", "No recovery
  rule…", "Rules mentioning … have no effects…", etc.) staying
  `cannot_express` False; only the ambiguous-cause boundary is test-pinned.
  Code reading (verifier + builder) confirms the prefix check is narrow
  enough that none of them flips the flag, but a future regression there
  would not be caught by the suite. Small, well-scoped follow-up if wanted.
- **v10 offline test flakes under concurrency.** The v10 offline wrapper
  uses a fixed `/tmp` DB path + fixed session id. Never run two full suites
  concurrently in the same tree; the combined run in §8 was run alone.

## 12. What was NOT run

- No live OpenRouter call with your key — you hold the key; it was never in
  this workspace. All fix5 provider tests use mocked HTTP.
- No ingestion re-run (Lincoln is still waiting on the fresh job in §10).
- No benchmark re-run (Gate 0 / Miller re-run are your §10 steps, after deploy).
- No live diagnosis sessions.

## 13. Combined full-suite run (this zip's tree, run alone)

Run by the packager, 21 Nov 2026, on the final tree, alone (no concurrent
suite — see §11): `~/workspace/crater-phase1/.venv/bin/python -m pytest
tests/ -q`, log `~/workspace/crater-phase1/combined-full-suite.log`:

**494 passed, 0 failed, EXIT=0, 976.78s (16m16s), 408 warnings** (all
pre-existing deprecation warnings).

Matches the expected 494 exactly — no discrepancy from the §0
reconciliation (433 + 13 + 2 + 7 + 16 + 22 + 1).

## 14. Zip contents (relative to the fix2 zip)

Base: the full fix2 file set (242 files), unchanged except the files below.
Added / changed by the six fixes + assembly follow-ups:

Fix3 — `backend/diagnostics/twin_verification.py` (NEW),
`backend/diagnostics/agent.py`, `backend/diagnostics/prompts.py`,
`backend/diagnostics/schema.py`, `backend/db/models.py`,
`backend/diagnostics/backtracking.py`, `backend/api/routes/diagnostics.py`,
`backend/simulation/tournament.py`, `tests/test_verify_before_conclusion.py`
(NEW), `tests/test_diagnostics.py`, `tests/test_diagnosis_robustness.py`,
`tests/test_phase456_followup_walk.py`, `tests/test_hypothesis_tournament.py`,
`VERIFY_BEFORE_CONCLUSION_BUILD_NOTES.md` (NEW).

Fix3 extension — `backend/simulation/tournament.py` (same file as fix3),
docstrings in `backend/diagnostics/agent.py` and
`backend/diagnostics/twin_verification.py`, 2 new tests in
`tests/test_verify_before_conclusion.py`.

Fix4 — `backend/ingestion/pdf_loader.py`,
`tests/test_pdf_loader_images.py` (NEW), `PDF_IMAGE_FIX_BUILD_NOTES.md` (NEW).

O1/O2/O3 runner — `scripts/run_benchmark_oracle.py` (NEW),
`tests/test_benchmark_oracle_runner.py` (NEW),
`BENCHMARK_ORACLE_RUNNER_BUILD_NOTES.md` (NEW).

Fix5 — `backend/llm/provider.py`, `backend/llm/gateway.py`,
`backend/config.py`, `tests/test_llm_openrouter_fix5.py` (NEW),
`OPENROUTER_FALLBACK_BUILD_NOTES.md` (NEW).

Harness scorer fix — `benchmark/harness.py`,
`benchmark/HARNESS_SCORER_FIX_NOTES.md` (both NEW in the zip, under
`benchmark/`; drop `harness.py` into your `crater-benchmarks/` folder).

Assembly follow-ups (this zip) — `.env.example` (OpenRouter lines +
corrected routing defaults), `llm-probe.py` (OpenRouter key status +
route-aware error hints), `COMBINED_FIX_BUILD_NOTES.md` (NEW, this file).

Excluded: no `.db` files, no `__pycache__`, no `/tmp` files, no
`data/ingestion_checkpoints` runtime dir, no `.pytest_cache`.
