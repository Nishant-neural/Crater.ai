# Crater Refactor — Phase 0 Baseline Freeze

Frozen: 23 Nov 2026. **No Crater source code was changed.** This is a freeze-and-document step only: a working copy extracted from the review zip, plus frozen copies of the benchmark evidence that lives outside the repo.

## 1. Source archive (pristine, untouched)

| Item | Value |
|---|---|
| Zip | `~/workspace/user/files/project-review-phase6-readiness-ingestion-gemini-fix-combined.zip` |
| sha256 | `d6886155a4394a727a32f8e38ac6794d81678ebeb02596c30e7371bacecedb92` |
| Size | 1,081,783 bytes |
| Files in zip | 256 (per `unzip -l`) |
| Working copy | `~/workspace/crater-refactor/` |
| Zip re-verified after extraction | sha256 unchanged — zip is pristine |

## 2. Working-copy verification

| Check | Expected | Actual |
|---|---|---|
| Total files | 256 | **256** ✓ |
| Python files | 171 | **171** ✓ |
| Python `wc -l` total | ~41,285 | **41,285** ✓ |
| All-files `wc -l` total (py + md + json + jsx/js + misc) | — | 75,817 |

**Note on 41,456:** the earlier figure of 41,456 does not match this extraction. The Python total here is 41,285 — **171 lines fewer**. (171 is also the Python file count; that is a coincidence, and the origin of the 41,456 figure was not established. The 41,285 count reconciles exactly from the per-area table below, so 41,285 is the frozen baseline.)

File types in the working copy: 171 py, 45 md, 14 jsx, 13 json, 8 js, 2 .gitignore, 2 .example, 1 toml, 1 svg, 1 .python-version, 1 lock, 1 html.

### Python line counts per top-level area (reconciles to 41,285)

| Area | Path | .py files | Lines |
|---|---|---|---|
| simulation | `backend/simulation/` | 22 | 8,884 |
| knowledge | `backend/knowledge/` | 18 | 5,859 |
| diagnostics | `backend/diagnostics/` | 12 | 3,375 |
| api | `backend/api/` | 15 | 2,893 |
| ingestion | `backend/ingestion/` | 10 | 2,441 |
| retrieval | `backend/retrieval/` | 7 | 477 |
| llm | `backend/llm/` | 3 | 477 |
| db | `backend/db/` | 7 | 1,089 |
| schematic | `backend/schematic/` | 5 | 510 |
| visualization | `backend/visualization/` | 4 | 453 |
| backend root | `backend/config.py` | 1 | 116 |
| **backend subtotal** | | **104** | **26,574** |
| tests | `tests/` | 58 | 11,611 |
| scripts | `scripts/` | 6 | 1,930 |
| benchmark | `benchmark/harness.py` | 1 | 732 |
| root | `gold_set_test.py` | 1 | 355 |
| root | `llm-probe.py` | 1 | 83 |
| **TOTAL** | | **171** | **41,285** |

## 3. Frozen evidence — `~/workspace/crater-refactor/frozen/`

All files below were **copied, not moved**. Hashes were verified to match their sources after copying.

| Frozen file | Source | sha256 |
|---|---|---|
| `miller-report-mock-2026-11-23.json` | `~/workspace/user/files/miller-report-mock-2026-11-23.json` (Nishant's upload) | `59c16ae963bd793f47e700f9b13d7e42d3af034a46aca165fb686ffb7cf4ca26` |
| `miller-report-4830a71b.json` | `~/workspace/user/files/miller-report-4830a71b.json` (Gemini) | `4830a71b07c19dd08df637211eabcd377505083e5b6263ccfd37d2687f1c6805` |
| `c002b-miller.json` | `~/workspace/crater-benchmarks/evaluations/c002b-miller.json` | `dd58f761c8969605e800cc3061f73fba1b15550ecd98a41736b646c530fd95f2` |
| `miller_parser_rows.json` | `~/workspace/crater-benchmarks/evaluations/miller_parser_rows.json` | `504c8138068565b54589a557e883958ef299ddf2e07b1a0a7b6fd5117b2bcd85` |
| `MOCK_LLM_RUN_NOTES.md` | `~/workspace/crater-phase1/mock-llm/MOCK_LLM_RUN_NOTES.md` | `fab6df8d08c4965fcb3b6e2577969691b5534f0c61d1214778dac85182533dbb` |
| `gate0-final.json` | `~/workspace/crater-phase1/mock-llm/gate0-final.json` | `7670c33bda6414c2867e65ff1691a16d8454b4e8a56b78db285ebf81f3f0126d` |

### Muse Miller report — headline (frozen baseline, "beat 22" gate)

- **Source frozen:** the uploaded file `~/workspace/user/files/miller-report-mock-2026-11-23.json` (per the Phase 0 correction). It is **byte-identical** (`cmp` IDENTICAL) to `~/workspace/crater-phase1/mock-llm/miller-report-mock-2026-11-23.json` and to `~/workspace/crater-benchmarks/miller-report-2026-11-22-muse-as-api.json` — all three share sha256 `59c16ae9…ca26`.
- **"Beat 22" is now verifiable:** the audit claim that "the Muse 22/38 report doesn't exist in the files" is WRONG for the workspace — the report exists here in three places. It was only missing from the review zip. No Muse re-run was performed; the contents were verified by reading, not re-running.
- case_id `c002b-miller`, kind `oracle`, base_url `http://localhost:8000`
- product_id `01c956e5-84e5-484c-a280-ee9e15c348a0`, revision_id `30e0e856-62b9-4fd7-ac05-52ee1bf3e742`
- O1/O2/O3: `NOT_RUN_OVER_HTTP` — twin checks need the compiled twin and `backend/simulation/oracle.py`; live mode scores the diagnosis level only.
- **Headline: 22/38 rows in top-5, citations 17/17 full (score 1.0), discriminating test 0/17.**

Per-symptom (rows in top-5 / rows scored, citations cited/total, discriminating-test score):

| Symptom | Top-5 | Citations | Disc. test |
|---|---|---|---|
| miller-01 | 1/8 | 5/5 | 0.0 |
| miller-02 | 0/3 | 4/4 | 0.0 |
| miller-03 | 1/2 | 4/4 | 0.0 |
| miller-04 | 1/1 | 4/4 | 0.0 |
| miller-05 | 0/4 | 4/4 | 0.0 |
| miller-06 | 1/1 | 6/6 | 0.0 |
| miller-07 | 1/1 | 5/5 | 0.0 |
| miller-08 | 0/0 | 4/4 | 0.0 |
| miller-09 | 0/0 | 3/3 | 0.0 |
| miller-10 | 0/0 | 3/3 | 0.0 |
| miller-11 | 0/0 | 4/4 | 0.0 |
| miller-12 | 0/0 | 2/2 | 0.0 |
| miller-13 | 0/0 | 3/3 | 0.0 |
| miller-14 | 4/4 | 4/4 | 0.0 |
| miller-15 | 4/4 | 4/4 | 0.0 |
| miller-16 | 5/6 | 5/5 | 0.0 |
| miller-17 | 4/4 | 5/5 | 0.0 |
| **Total** | **22/38** | **17/17 full** | **0/17** |

All 17 sessions ended `status: active`. Note miller-08 through miller-13 score 0/0 rows — those symptoms contributed no twin-expressible rows to the 38-row denominator.

### Gemini Miller report — headline (comparison baseline)

- **19/38 rows in top-5, citations full 15/17, discriminating test 0/17.** 17 symptoms, 38 rows scored, same product/revision ids as the Muse report.
- **Encoding caveat:** the uploaded file `miller-report-4830a71b.json` is **UTF-16 LE with CRLF line terminators**, not plain UTF-8 JSON — a naive `json.load` fails on it (byte `0xff` at position 0). It must be opened with `encoding='utf-16'`. Its content is identical to `~/workspace/crater-benchmarks/miller-report-2026-11-22-gemini.json` (UTF-8 copy, sha256 `2f7bb64c4c28ed2a1d0c7943ffe75190afe74c55e51bfd6297bb06fcc4b13496`). The frozen copy preserves the original UTF-16 bytes.

Per-symptom (rows in top-5 / rows scored, citations cited/total, discriminating-test score):

| Symptom | Top-5 | Citations | Disc. test |
|---|---|---|---|
| miller-01 | 1/8 | 1/3 | 0.0 |
| miller-02 | 0/3 | 1/1 | 0.0 |
| miller-03 | 0/2 | 1/1 | 0.0 |
| miller-04 | 1/1 | 1/1 | 0.0 |
| miller-05 | 0/4 | 1/1 | 0.0 |
| miller-06 | 1/1 | 6/6 | 0.0 |
| miller-07 | 1/1 | 5/5 | 0.0 |
| miller-08 | 0/0 | 1/1 | 0.0 |
| miller-09 | 0/0 | 3/3 | 0.0 |
| miller-10 | 0/0 | 3/3 | 0.0 |
| miller-11 | 0/0 | 1/1 | 0.0 |
| miller-12 | 0/0 | 2/2 | 0.0 |
| miller-13 | 0/0 | 1/1 | 0.0 |
| miller-14 | 4/4 | 4/4 | 0.0 |
| miller-15 | 4/4 | 0/4 | 0.0 |
| miller-16 | 3/6 | 3/3 | 0.0 |
| miller-17 | 4/4 | 5/5 | 0.0 |
| **Total** | **19/38** | **15/17 full** | **0/17** |

Muse vs Gemini delta: **+3 rows in top-5** (22 vs 19), citations full on 2 more symptoms (17/17 vs 15/17). Discriminating test is 0/17 for both.

## 4. Oracles

### Miller oracle — FOUND

- `~/workspace/crater-benchmarks/evaluations/c002b-miller.json` (frozen copy in `frozen/c002b-miller.json`, sha256 `dd58f761…fd95f2`)
  - version `oracle-vocabulary-miller-v1`, created 2026-11-21, case_id `c002b-miller`
  - Product: Miller Electric, Millermatic 252, Owner's Manual OM-230 693C (2007-11), 52 pp
  - **63 rows total**; denominator: total 63, **twin_expressible 38**, settings 16, technique 9, cross_reference 0, unmappable 0
  - Source: §6-5 Troubleshooting (5 troubles → 21 remedy rows; no cause column, row key is remedy text) + §§8-8 to 8-15
  - Review status: `draft-for-review`, reviewer None
- `~/workspace/crater-benchmarks/evaluations/miller_parser_rows.json` (frozen copy in `frozen/miller_parser_rows.json`, sha256 `504c8138…cd85`) — list of **63** parser rows, joins to the oracle on `row_id`.

### OMNIPRO oracle (35 rows) — MISSING, not invented

No standalone OMNIPRO oracle file was found. Paths searched:

- `~/workspace/crater-benchmarks/evaluations/` — full listing: `c002-lincoln.json`, `c002b-miller.json`, `c003`–`c016` session files, `lincoln_parser_rows.json`, `miller_parser_rows.json`. **No `c001`/OMNIPRO file.**
- `~/workspace/crater-phase1/` — `find -iname '*oracle*'` (excluding `.venv`, `__pycache__`): only test files, scripts, `backend/simulation/oracle.py`, and build notes. No OMNIPRO oracle JSON.
- `grep -rli 'c001'` across `~/workspace/crater-benchmarks` and `~/workspace/crater-phase1`: only hit is `~/workspace/crater-benchmarks/README.md`, which states: **"C001 (OMNIPRO) is the training set, not a benchmark case."**
- Full-workspace scan for any `.json` whose top level has exactly 35 entries: **no match.**
- `find -iname '*omni*'` across both trees found only: `repair_cases_omnipro.json`, `golden_rules/omnipro_wire_feed.json`, `omnipro_edge_list.json`, `omnipro_proof.py` — none is a 35-row oracle (`repair_cases_omnipro.json` is an 11-key dict of repair cases; `omnipro_wire_feed.json` is a 6-key dict of golden rules; `omnipro_edge_list.json` is a 10-key dict).

The "35 rows" for OMNIPRO therefore has **no frozen oracle file in this baseline**. If a 35-row OMNIPRO oracle exists elsewhere, its path was not found in the searched locations; do not assume the number is backed by a file until one is produced.

## 5. Gate 0 evidence (frozen)

- `frozen/MOCK_LLM_RUN_NOTES.md` — mock-LLM test path notes, 23 Nov 2026: backend + Miller benchmark ran end-to-end in the VM with no external LLM provider; a stdlib mock server on `127.0.0.1:18080` stood in for the provider and a Muse-model agent answered every prompt from the prompt alone, never from the answer key. Backend needed **no code change** (env only). Per-call latency ~11s average (Gate 0: 9 turns in 211s; Miller: 17 starts in 413s).
- `frozen/gate0-final.json` — Gate 0 final session state:
  - session_id `9b58f871-72f7-4698-b889-839fbe9f1760`, status `active`
  - product_id `ccc3bae7-edff-42ce-8366-900d4a6523d5` (OMNIPRO), revision_id `3c246c9a-3f0b-4760-9d9b-e5811751c49f`
  - Symptom: "Wire Feed Motor Runs but Wire Does Not Feed Properly"; turns_taken 9; confidence 0.25
  - Top hypothesis: "Insufficient wire feed pressure"; no eliminated hypotheses; current step is a discriminating-test question (`ground_check`, gain 1.06 bits).

## 6. Database — `crater-test.db` is a COPY, not the original

- Original upload (untouched): `~/workspace/user/files/crater-96db6588.db`, 5,980,160 bytes, sha256 `96db6588aea8324794f90b22e801428b2d995ced2bc62a61028676cc2cbbc25c`
- Working copy: `~/workspace/crater-phase1/mock-llm/crater-test.db`, 6,074,368 bytes, sha256 `e6d04aed888182460241f3fb14f6e3c1e317798109b256062edeedd03d6601a7`
- Different hash and larger size (run artifacts added) — consistent with a copy that has been run against. The original was never touched (per `MOCK_LLM_RUN_NOTES.md` and the differing hashes).

Products, revisions, and twins (queried directly from the copy):

| Product | product_id | revision_id | Twin |
|---|---|---|---|
| Harbor Freight OMNIPRO 220 | `ccc3bae7-edff-42ce-8366-900d4a6523d5` | `3c246c9a-3f0b-4760-9d9b-e5811751c49f` | `427ad1be-3e48-49ee-8087-67d482d9d41b`, **active** |
| Miller 220 | `01c956e5-84e5-484c-a280-ee9e15c348a0` | `30e0e856-62b9-4fd7-ac05-52ee1bf3e742` | `a7a67556-495f-488e-8742-1040b7397229`, **active** |
| Linclon Power MIG 210 (spelling as stored in DB) | `1b5b6981-2c30-42f0-8679-af294172156f` | `b082404d-738c-4da4-a2ae-b925ee823ea8` | **none** — product row exists but no twin, so it cannot run |

## 7. What was NOT done

- No Crater source code was modified anywhere (working copy is a verbatim extraction).
- No Muse Miller run was reproduced — the frozen report's contents were verified by reading (22/38, 17/17, 0/17 recomputed from `per_symptom`), and no discrepancy was found.
- The OMNIPRO oracle was not reconstructed or invented; it is recorded as missing above.
