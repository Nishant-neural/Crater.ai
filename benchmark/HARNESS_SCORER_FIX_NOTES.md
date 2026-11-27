# Harness scorer fix — fault-aware top-5 matching

Date: 2026-11-21. Changed files: `harness.py` only (plus this note).
No oracle JSON, manual, transcript, or v12 backend file was touched.
No live server calls, no live sessions — the re-score used only the
saved report `miller-report-2026-11-21.json` and the oracle JSON.

## Why

The Miller oracle's §6-5 rows have no cause column in the manual, so
their `cause` field holds the manual's remedy text, e.g. miller-01:
"Be sure line disconnect switch is On (see Section 4-13)." The app
ranked the same fault as "Line disconnect switch is off" — the old
string matcher could never match remedy text against a fault phrasing,
so those rows scored 0 even when the diagnosis was right. Every one
of the 38 twin-expressible Miller rows carries a structured `fault`
object (component / field / value / kind); the 25 settings/technique
rows have `fault: null`.

## The matching rule

A ranked cause matches an oracle row if EITHER condition holds:

1. **String match (unchanged).** The existing `cause_matches` logic:
   `normalize_text()` (lowercase, curly quotes → `'`, whitespace
   collapsed, everything but `[a-z0-9 ]` stripped) on both sides, then
   equality or either side containing the other. `normalize_text` was
   not modified, so every row that matched before still matches.
2. **Fault match (new, `fault_matches`).** The row's structured fault
   matches when the fault's component AND its value both appear as
   whole-word token sequences in the ranked cause.

### Exact fault normalization

Fault matching does NOT use `normalize_text`. It uses its own
tokenizer, `_fault_tokens`: lowercase, then split on every run of
non-alphanumeric characters, so `-`, `_`, `.`, `/`, whitespace and all
other punctuation are word boundaries. This differs from
`normalize_text` on purpose: `normalize_text` deletes punctuation
without a space, so `fan-on-demand` becomes `fanondemand` and
`poor_connection` becomes `poorconnection` there. The fault tokenizer
gives `["fan", "on", "demand", "circuit"]` and
`["poor", "connection"]`, which is what a ranked cause actually says.

Component and value are each matched as a **contiguous token
subsequence** of the ranked cause's tokens (`_contains_tokens`).
Whole-token comparison is what keeps near-misses apart:

- **Antonyms are distinct tokens.** Value `off` matches only the
  token `off`, never `on`; `low` never `high`; `open` never `closed`.
  A ranked cause saying the switch is *on* does not match a fault
  value of `off`.
- **Substrings of other words cannot match.** Value `low` matches
  only the token `low`, never `below`; `off` never `official` or
  `offer`; `open` never `opened`; `tripped` never `trip`; `faulty`
  never `fault`. (Guard-tested in dry-run: "Input power is below
  normal" does not match value `low`.)
- **Multi-word values must appear whole and in order.** `HL.P 001`
  tokenizes to `["hl", "p", "001"]` and needs all three tokens,
  contiguously, in the ranked cause. A partial mention does not match.
- **Underscored values split on the underscore.**
  `poor_connection` → `["poor", "connection"]`,
  `wiring_fault` → `["wiring", "fault"]`, so the natural phrasing
  "poor connection" matches.
- **Component without the value does NOT match.** A ranked cause that
  names the component but not the fault value scores 0 — otherwise
  distinct faults on one component (Wire Feed Rolls tension low vs
  high vs groove mismatch; Gas Solenoid Valve wiring fault vs faulty)
  would collapse into one. (Guard-tested: "Check the line disconnect
  switch" does not match the miller-01 fault.)
- **Boolean fault values never fault-match.** Five Miller rows and
  three Lincoln rows have boolean values (`groove_matches`,
  `size_matches`, `connector_seated` = `false`). There is no surface
  wording for `false` — it never appears in a ranked cause — so
  `fault_matches` returns False for any non-string value, and `False`
  is never coerced to the string `"false"`. Those rows stay
  string-match only.
- **Missing/empty component or value, or `fault: null`, means no
  fault match** (string-match only). Session cases pass no fault, so
  they are unaffected.

### Known limitation

Negation is NOT understood: token presence is all that is checked,
so a ranked cause phrased "switch is not off" would still match a
fault value of `off`. No ranked cause in the saved Miller report uses
such a negated phrasing. Every new match below is listed with its
ranked cause and fault so a reader can check each one by eye.

### Plumbing

- `cause_matches(hyp, true, fault=None)`, `rank_of_true_cause(...,
  fault=None)`, `score_top5(..., fault=None)` — the fault parameter
  is optional; `fault=None` means string-match only, exactly as
  before. Citations and discriminating-test scorers untouched.
- `live_run`'s `score_session(view, true_causes, faults=None)` takes
  a parallel faults list; the oracle branch passes
  `[r.get("fault") for r in rows]`; the session branch passes nothing.
- The fix is in the shared pure functions, so it applies identically
  to live runs and to `--rescore`.

## Re-score mode

`python3 harness.py --rescore <report.json>` (added to `harness.py`,
no separate script). It reads the saved report's per-symptom
`ranked_causes` and the oracle JSON for the report's `case_id`,
re-scores every twin-expressible row with the new matcher, and prints
a JSON summary: `top5_before`, `top5_after`, `newly_matched_rows`,
`regressions_previously_matching_now_not` (exit 1 if non-empty), and
per-row before/after ranks. The saved report file is never modified.
Only oracle reports are accepted (`--rescore` refuses other cases).

Run: `python3 harness.py --rescore miller-report-2026-11-21.json`
(exit 0).

## Before / after — Miller top-5

| | top-5 |
|---|---|
| Before (saved report) | **3 / 38** |
| After (fault-aware re-score) | **5 / 38** |

The 3 previously matching rows still match, at the same ranks:
miller-26 rank 5, miller-28 rank 1, miller-52 rank 2. No regressions.
No duplicate cause strings within any symptom (so the saved
per-cause "before" lookup is unambiguous per row).

### Newly matched rows (2)

1. **miller-01** (symptom miller-01), rank **1**
   - Ranked cause: "Line disconnect switch is off"
   - Fault: `{"component": "Line Disconnect Switch", "field": "state", "value": "off", "kind": "parameter"}`
   - Oracle `cause` (remedy text, never string-matches): "Be sure line
     disconnect switch is On (see Section 4-13)."
   - Check: tokens `line disconnect switch` + `off` are both present
     in the ranked cause; the old matcher failed because remedy text
     ("be sure … is On") and fault phrasing ("… is off") share no
     containing string.
2. **miller-15** (symptom miller-04), rank **1**
   - Ranked cause: "Faulty fan-on-demand circuit"
   - Fault: `{"component": "Fan-On-Demand Circuit", "field": "fault", "value": "faulty", "kind": "fault_state"}`
   - Oracle `cause` (remedy text): "Have Factory Authorized Service
     Agent check fan-on-demand circuit."
   - Check: tokens `fan on demand circuit` + `faulty` are both
     present. Note the hyphen handling: the fault tokenizer splits
     `Fan-On-Demand` into `fan on demand`, matching the ranked
     cause's wording; `normalize_text` would have produced
     `fanondemand` and missed it.

### Why nothing else newly matched (spot-checkable)

Only 7 of the 17 symptoms have any ranked causes at all; the other
10 ranked `[]`, so no fault can match there. Of the non-empty ones:

- miller-03 ("Unit not connected to proper input voltage or low line
  voltage"): fault component is `Input Power`, value `low`. The
  ranked cause says "input voltage", not "input power" — component
  absent, so no match despite the word `low` being present. This is
  the conservative rule working as intended.
- miller-05 ("Incorrect front panel settings"): none of the four
  faults (Wire Feed Rolls groove/tension, Gun Liner clogged, Main
  Control Board faulty) is named — no match.
- miller-06 / miller-07: the ranked lists never name `MIG Gun` (the
  fault component for the shielding-gas rows); those rows already
  matched by string.
- miller-16: ranked "Gas cylinder valve not open or flowmeter not
  adjusted" does not name `Gas Solenoid Valve`, `Gas Nozzle`,
  `Gas Hose`, or `Input Power`; "Gas cylinder empty" already matched
  by string (rank 2).

The other 33 twin-expressible rows stay unmatched, mostly because
their symptoms returned no ranked causes at all — a coverage problem
in the live run, not a scorer problem. Citations (12/17 at 1.0) and
discriminating test (1/17) are unchanged by this fix.

## What was NOT run

- No live server calls and no live diagnosis sessions. The only
  inputs were the saved report and the oracle JSON on disk.
- No re-ingestion, no v12 backend changes, no manual or transcript
  changes.

## Dry-run result

`python3 harness.py --case <id> --dry-run` for all 15 cases, exit 0
each:

c002-lincoln, c002b-miller, c003-haas, c004-acoem,
c006-dow-corning, c007-hotgas-fan, c008-cooling-fan, c009-mycom,
c010-fs-elliott, c011-hydrocracking, c012-vertical-turbine,
c013-resonance, c014-hpac, c015-jaw-crusher, c016-cake-lock — all OK.

Each dry-run still reports "stub checks passed: perfect scores full;
wrong cause scores 0 on top-5; no citations scores 0 on citations"
(the stubs pass `fault=None`, so they exercise string-match only and
are unaffected). A new dry-run check, "fault-aware matching guards
passed", also runs per case and asserts: same fault in different
words scores 1 at rank 1; component without value scores 0; antonym
value (`on` vs `off`) scores 0; substring value (`low` vs `below`)
scores 0; underscored value (`poor_connection` vs "poor connection")
scores 1; boolean fault value scores 0; the wrong-cause stub scores 0
even with a fault supplied.

## Oracle integrity

sha256 before and after the change (identical — oracle JSONs were
only read, never written):

- `evaluations/c002b-miller.json`
  `dd58f761c8969605e800cc3061f73fba1b15550ecd98a41736b646c530fd95f2`
- `evaluations/c002-lincoln.json`
  `229d82d33df4ced726d1caef12b004bbfed49e77434068f3794ff2b73d4bd18d`
- `miller-report-2026-11-21.json` (also read-only)
  `ec12d185160f2959e1c0b2e4de111087d65eff395e229e8fb1a3017a9154cebf`

---

# Phase 0 addendum — separate reporting axes (23 Nov 2026)

Changed files: `benchmark/harness.py` and new
`tests/test_harness_scorer_reporting.py`. No oracle JSON, manual,
transcript, or v12 backend file touched. No live server calls, no
re-ingestion. The fault-aware matching rule (`fault_matches` /
`_fault_tokens`) is unchanged.

## Defect verification (each checked in code / oracle before changing)

(a) VERIFIED. Miller symptoms miller-08..miller-13 have 0
twin-expressible rows (their 11 rows are all settings/technique), and
live reports recorded them only as `rows_scored: 0, rows_in_top5: 0`
inside `per_symptom` — invisible in the `19/38`-style headline.
NOT VERIFIED as stated: the task's "(4 generic service-agent rows +
4 HL.P meter-code rows)" as *unrankable* rows does not match the
oracle. The oracle has 4 HL.P rows (miller-04/05/06/11) and 5 pure
"Have Factory Authorized Service Agent check ..." rows
(miller-07/08/14/15/21) — all 9 are twin_expressible WITH structured
faults, i.e. rankable, and stay in the 38-row causes denominator.
The genuinely unrankable rows are the 25 settings (16) + technique
(9) rows, and that is what is now reported as unrankable. Do not
relabel the HL.P / service-agent rows as unrankable without changing
the oracle categories — that would change the scoring denominator.

(b) VERIFIED. `score_citations`' docstring promised a page-number
check ("the session evidence includes a page number for that chunk")
that the code never performed — it checked chunk ids only, and the
function never even received page data, so the promise was
unimplementable as written. Miller-15 in
miller-report-2026-11-22-gemini.json is the proof case: 4/4 causes
(rows_in_top5 4/4) with 0/4 citations, previously only visible by
digging into `per_symptom`. Fix chosen: correct the docstring AND
implement the page check as an opt-in `chunk_pages` mapping
(chunk id -> page); without it the old chunk-id rule holds exactly
and the result carries `page_verified: False`. No page data is
plumbed from the live API view, so live runs still report
chunk-id presence only, now honestly labelled.

(c) VERIFIED. `normalize_text` deleted punctuation:
`normalize_text("Faulty fan-on-demand circuit")` was
`'faulty fanondemand circuit'`. Fix: punctuation is replaced with a
space, so hyphenated and spaced phrasings normalise identically.
This supersedes the earlier note in this file that normalize_text
"must stay byte-identical" — that constraint is lifted by this fix,
and the module + `_fault_tokens` docstrings are updated accordingly.
One intentional behaviour change, documented in the code and tested:
a glued spelling ('wirefeed') no longer matches a hyphenated/spaced
one ('wire-feed' / 'wire feed').

## What changed in harness.py

- `normalize_text` (~L145): `re.sub(r"[^a-z0-9 ]", " ", ...)` —
  punctuation becomes a space instead of being deleted.
- `score_citations` (~L271): new optional `chunk_pages` param;
  result gains `page_verified` (False when no page data supplied).
  Default behaviour (chunk-id presence) is byte-identical.
- New `summarize_oracle_breakdown(oracle, per_symptom)` (~L334) and
  `format_headline(breakdown)` (~L408): causes_top5 over rankable
  rows only, citations over claims (separate denominator),
  rankable vs zero-row symptom lists, unrankable rows by category
  with row ids, oracle denominator copied verbatim.
- `live_run` oracle branch (~L547): report gains `breakdown` +
  `headline`. `rescore_report` (~L760): same, using re-scored
  ("after") top-5 counts and the saved report's citations.
- `dry_run` (~L644, ~L779): structural breakdown check on the real
  oracle (denominators + zero-row list, no live scores) and hyphen
  normalisation guards, including a discriminating test naming a
  hyphenated cause.

## Regression evidence

- `pytest tests/test_harness_scorer_reporting.py`: 9/9 pass (also
  runnable standalone: `python3 tests/test_harness_scorer_reporting.py`).
- `harness.py --dry-run` for all 15 cases: exit 0 each.
- `--rescore` on all 4 saved Miller reports: no regressions from the
  normalize_text change (before == after in 3 reports; the 2026-11-21
  report still shows the pre-existing fault-aware delta 3 -> 5/38,
  0 regressions, 2 newly matched — same as documented before this fix).
  Gemini headline now reads: causes 19/38 rankable rows across 11
  rankable symptoms (6 with 0 rankable rows: miller-08..miller-13);
  citations 39/45 claims (page NOT verified); unrankable 25/63.
- Oracle integrity: c002b-miller.json sha256 unchanged
  (dd58f761c8969605e800cc3061f73fba1b15550ecd98a41736b646c530fd95f2).
