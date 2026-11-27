# OpenRouter fallback — build notes (fix5)

Date: 21 Nov 2026. Builder: fix5 coordinator. No separate fix5 zip is
produced (user decision 21 Nov: ONE combined zip for all five fixes —
this file + the changed files below go into that zip; the parent builds
it). A separate verifier re-runs the suite against the tree.

## What this fix is

Gemini's free tier returns 503 "high demand" constantly for
`gemini-2.5-flash-lite` / `gemini-3.1-flash-lite`, and the diagnosis loop
degrades that to "unavailable". This fix adds an OpenRouter fallback
provider and retries transient Gemini overloads. It does NOT change any
Gemini/Anthropic default model, and it does NOT change the empty-key
behaviour (no key = `(None, False)`, i.e. "unavailable", same as before).

## What changed (exact files)

| File | Change |
|---|---|
| `backend/llm/provider.py` | New `OpenRouterProvider` (OpenAI-compatible chat completions over stdlib `urllib`, zero new dependencies); shared 429/503 retry helpers; `GeminiProvider.complete_result` retries 429/503 (3 attempts, exponential backoff, honours Retry-After); `get_provider_for_model` gains `"openrouter"`; `get_llm_provider` model default is now per-provider via `_default_model_for_provider`. |
| `backend/llm/gateway.py` | `route()` model default is now per-provider (real gap found while wiring: an openrouter route with no model configured silently asked for the Claude model id and would 404). No other gateway logic changed. |
| `backend/config.py` | New `openrouter_api_key` (`OPENROUTER_API_KEY`), `openrouter_base_url` (default `https://openrouter.ai/api/v1`), `openrouter_model` (default below). **Routing change (user decision 21 Nov, supersedes the original opt-in-only brief): `diagnosis_provider` and `rerank_provider` now default to `"openrouter"`.** `llm_provider` is NOT changed (still `"anthropic"`). |
| `tests/test_llm_openrouter_fix5.py` | New: 22 mocked-HTTP tests (see below). |
| `OPENROUTER_FALLBACK_BUILD_NOTES.md` | This file. |

NOT touched (per the brief's boundaries): `backend/diagnostics/`,
`backend/api/routes/`, `backend/ingestion/`, `scripts/`, `.env.example`,
`~/workspace/crater-phase1/llm-probe.py` (outside the v12 tree).

## Routing after this fix (user decision 21 Nov 2026)

- Diagnosis + rerank default to **openrouter** (free model below).
  Reason: the Miller benchmark's live path calls only diagnosis (one LLM
  call per turn) and rerank (one per turn) — 17 Miller sessions is ~34
  calls, inside the OpenRouter free daily cap — and Gemini 503s were
  failing those sessions.
- Extraction, integration, vision, expert **stay on Gemini** by his
  decision (they follow `llm_provider` as before; he accepts Gemini
  there). Their defaults are unchanged.
- Override any task with its per-task env var, e.g.
  `DIAGNOSIS_PROVIDER=gemini` goes back to Gemini for diagnosis only.

## .env lines the user needs

```env
# Free key — create it yourself at https://openrouter.ai (Keys page).
# No key = diagnosis/rerank report "unavailable" (empty-key behaviour,
# same as Gemini with no key). Diagnosis + rerank now default to
# OpenRouter, so this one line is what makes the benchmark path work.
OPENROUTER_API_KEY=sk-or-v1-<paste-your-free-key-here>

# Optional — these are the defaults, shown so you can see/change them:
# OPENROUTER_MODEL=google/gemma-4-31b-it:free
# OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
# LLM_PROVIDER=anthropic        # unchanged; extraction/integration/vision/expert still follow this (Gemini in your .env)
# DIAGNOSIS_PROVIDER=gemini    # only if you want diagnosis back on Gemini
```

## Verified default model (live check, 21 Nov 2026)

Checked with a public `GET https://openrouter.ai/api/v1/models` (no key
needed; 476 models returned). Evidence saved during the build:
`/tmp/or_models.json` (ephemeral; re-fetch to re-verify).

- The brief's suggested default **`meta-llama/llama-3.3-70b-instruct:free`
  is DELISTED** — it is not in the live list. The paid variant
  `meta-llama/llama-3.3-70b-instruct` exists (context 131072,
  `response_format` supported) but bills $0.71/1M tokens, so it must
  not be the default.
- Chosen default: **`google/gemma-4-31b-it:free`** — context window
  **262144**, max completion tokens **32768**, `response_format`
  supported, pricing **$0** (prompt and completion).
- Why this one: of the 11 live `:free` models, it is the largest
  well-known instruction-tuned model with `response_format` support and
  a 262k context (10x the ~25k-token extraction prompt; the 8192-token
  diagnosis ceiling and 12000-token integration ceiling fit inside its
  32768 max completion). The 1M-context `:free` models
  (`inclusionai/ling-3.2-flash`, `baidu/ernie-5.5-flash-preview`,
  `particle-ai/particle-1`, `nvidia/nemotron-3.5-super-vl`) are
  preview/obscure, and the Nemotron free variants do not support
  `response_format` at all. If OpenRouter delists Gemma-4-31B too, set
  `OPENROUTER_MODEL` to another live `:free` id from that same endpoint.

## Provider behaviour (what the code does)

- `OpenRouterProvider` posts to `{base_url}/chat/completions` with
  `response_format: {"type": "json_object"}` whenever a `json_schema`
  is given, plain text otherwise. Temperature is forwarded only when
  the caller passes one. Anthropic-style list content is translated to
  OpenAI parts (images become `image_url` data URLs so nothing is
  silently dropped — a text-only model rejects them loudly instead of
  answering on a partial prompt). Truncation = `finish_reason ==
  "length"`. HTTP timeout 180s.
- Retries (both providers): 429 AND 503, 3 attempts total, backoff 1s
  then 2s, honouring `Retry-After` when present (capped at 120s so a
  pathological header cannot stall an ingestion job).
- A 404/401 (or any other non-retryable HTTP failure) **raises**
  `RuntimeError` with the status in the message, never a silent None.
- Gemini: the retry wraps `generate_content` inside the existing
  `with genai.Client(...)` block, so the client still stays open during
  generation and closes after. Empty-key `(None, False)` behaviour is
  unchanged. No default Gemini routing or model changed.
- Transport is stdlib `urllib.request`: no HTTP client exists anywhere
  in backend code, `pyproject.toml` declares `httpx` only under dev
  optional-deps and does not declare `requests` at all, so this adds
  **zero new dependencies** and works on the Windows laptop with plain
  stdlib. `base_url` is generic, so Groq/Cerebras are a config change
  away (same OpenAI-compatible adapter, different base URL + key).

## Tests (all mocked HTTP — no live calls)

New file `tests/test_llm_openrouter_fix5.py`: **22 tests**, all passing
at build time. OpenRouter: JSON mode sends `response_format`
`json_object`; plain text omits it; truncation flag from
`finish_reason`; empty key returns None with no request; image content
becomes a data-URL part; custom `base_url` honoured; 429 retries once
honouring `Retry-After: 3`; 503 retries with 1s backoff; 503 x3 raises
`RuntimeError` containing `503` after 3 attempts (sleeps 1s, 2s);
404/401 raise containing the status with no retry. Gemini: 503 then
success, 429 then success, 503 x3 raises after 3 attempts,
non-retryable error raises immediately (1 call). Routing: factory
selects openrouter; `get_llm_provider` with `LLM_PROVIDER=openrouter`
uses `openrouter_model`; clean env → diagnosis + rerank resolve to
openrouter with model `google/gemma-4-31b-it:free`, extraction still
follows `llm_provider` (`anthropic`/`claude-sonnet-4-6`, unchanged);
`DIAGNOSIS_PROVIDER=gemini` override wins; `OPENROUTER_MODEL` env sets
the diagnosis model; all other defaults unchanged.

Full suite: baseline per the brief is 433 passed / 0 failed.
Post-change full suite (this build, `pytest tests/ -q`,
`~/workspace/crater-phase1/.venv/bin/python`, log
`/tmp/fix5verify/full_suite.log`): **491 passed, 0 failed, EXIT=0,
26m34s**. The +58 vs the 433 baseline is 22 new fix5 tests plus 36
tests from the other coordinators' work that landed in the same shared
tree while this fix was being built (fix3/fix4/O1-O2-O3 files) — the
suite is a whole-tree count, not a fix5-only count. The pre-change
full-suite run started for this build was still in flight (contended
with three other concurrent full-suite runs in the same tree) when the
tree had to be edited, so it was stopped; the clean pre-change check
that stands is the LLM/routing subset run immediately before editing —
30 passed (`test_llm_provider.py`, `test_phase8e_routing.py`,
`test_ingestion_gemini_fix.py`).

## What was NOT run

- **No live OpenRouter call with the user's key** — he holds the key;
  it was never in this workspace. All provider tests use mocked HTTP.
- No live re-verification of the Gemini model id (unchanged by this fix).
- No ingestion re-run, no benchmark run.

## The one check for the user

After setting `OPENROUTER_API_KEY` in `.env`, from the project root
with the venv active, run the existing probe (already on the laptop at
`~/workspace/crater-phase1/llm-probe.py` — it was NOT modified by this
fix, and it does not yet print `OPENROUTER_API_KEY` status; that is a
follow-up, the route lines and the tiny call below are the check):

```text
python llm-probe.py
```

Confirm: (1) the `diagnosis` and `rerank` route lines read
`provider=openrouter model=google/gemma-4-31b-it:free`, (2) the
`extraction`/`integration` lines still read your Gemini provider, and
(3) the tiny diagnosis call prints `CALL WORKED`. If it prints
`CALL RETURNED None`, the OpenRouter key is empty/missing in `.env`.
A `401` means a bad key; a `404` means the model id was delisted —
re-check `GET https://openrouter.ai/api/v1/models` and set
`OPENROUTER_MODEL` to a live `:free` id.

## Watch out: the OpenRouter free daily cap

Free `:free` models have a daily request cap (size depends on
all-time credits purchased). Diagnosis + rerank are ~2 calls per
benchmark turn. Running the whole benchmark in one day (Miller +
Lincoln + the 13 hand-run cases) is the thing to watch: if calls start
returning 429s late in the day, that is the cap, not a bug — spread
the runs across days or add credits. Extraction stays on Gemini, so
it does not consume the OpenRouter cap.

## Which fixes the tree contains (markers checked at build time)

- **fix3** (diagnosis loop): escalation removal markers in
  `backend/diagnostics/agent.py` (11 `fix-3` mentions), `schema.py`,
  `prompts.py`; verify-before-conclusion gate in `agent.py` (`fix-3`,
  "a conclusion stands only when..."). Its zip
  (`project-review-phase6-readiness-ingestion-gemini-fix3.zip`) already
  exists in `~/workspace/crater-phase1/`.
- **fix4** (pdf_loader): `_is_gray_or_rgb_colorspace` /
  `_to_saveable_pixmap` in `backend/ingestion/pdf_loader.py`. Its zip
  (`...-fix4.zip`) already exists.
- **fix5** (this fix): OpenRouter provider + Gemini retry + new
  diagnosis/rerank defaults, as listed above. **No fix5 zip** — it
  goes into the combined zip.
- **O1/O2/O3 runner**: `scripts/run_benchmark_oracle.py`,
  `BENCHMARK_ORACLE_RUNNER_BUILD_NOTES.md`,
  `tests/test_benchmark_oracle_runner.py` — present in the tree.

## Follow-ups (not done — outside this fix's file boundary)

- `.env.example` still says "Supported providers: anthropic, gemini"
  and has no OpenRouter lines — update it when the combined zip is
  assembled (it was not in this fix's touch list).
- `llm-probe.py` does not print `OPENROUTER_API_KEY` presence and its
  error-reading text is Gemini-specific — worth a small update so the
  probe names the OpenRouter failure modes directly.
