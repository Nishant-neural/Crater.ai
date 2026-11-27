# OMNIPRO 35-row oracle — reconstruction record

**Verdict: RECONSTRUCTED — faithfully, from the reviewed vocabulary. No row was invented, duplicated, or guessed.**

File built: `frozen/omnipro-oracle-35.json` (35 rows, all `twin_expressible`), 23 Nov 2026,
from the reviewed OMNIPRO vocabulary (see §1). Every row below traces to one manual
troubleshooting cause, one repair-case entry, and one fault injection.

## 0. What this file is — and is not

- This file is the **twin-expressible subset (35 rows)** of the OMNIPRO oracle, in the
  Miller oracle's schema. It is the denominator behind the known 3C numbers:
  **O2 20/35, O1 pass, O3 57/58** (`PHASE3C_BUILD_NOTES.md`; `phase3c-oracle-report.json`).
- It is **NOT the full OMNIPRO oracle**. The full manual-faithful oracle in Miller schema
  would be **45 rows**: these 35 plus 5 `settings`, 4 `technique`, 1 `cross_reference` rows
  (§5). Those 10 rows exist in the vocabulary file with full expected vectors; they were
  left out only because this deliverable is defined as the 35-row twin-expressible set.
  Do not cite this file's denominator (`total 35`) as the manual's cause count — the
  manual's troubleshooting tables have **45 cause rows** (p42: 25, p43: 20).
- The `categories` text carried verbatim from the vocabulary (§3) still quotes the full
  45-row counts ("settings … (5 rows)" etc.) while this file holds 0 rows in those
  categories. That is a known wart of the subset, not an error in the counts.
- Review status is `draft-for-review`, reviewer `null` — carried verbatim from the
  vocabulary. Reformatting is not a review; a reviewer still has to sign it off.

## 1. Per-source count table

| Source | Contributes | Count | Used in this file |
|---|---|---|---|
| `~/workspace/goals/crater-ai-diagnosis-and-machine-model-workflow-improvement/files/oracle_vocabulary_v1.json` (oracle-vocabulary-v1, created 2026-11-13) | 45 vocabulary rows with cause, fault, observables, tests, basis, flags, remedy; 9 cases; 26 tests | 45 rows: **35 twin_expressible**, 5 settings, 4 technique, 1 cross_reference | **35 rows verbatim** (cause/fault/observables/tests/basis/flags/remedy); cases, tests, categories, product, source, scope, review, join_rule verbatim |
| `backend/knowledge/repair_cases_omnipro.json` | 9 cases, 45 causes (p42: omni-01 4, omni-02 6, omni-03 4, omni-04 8, omni-09 3 = 25; p43: omni-05 5, omni-06 5, omni-07 4, omni-08 6 = 20); **35 causes carry a `fault_injection`, 10 do not** | 45 causes | Cross-check only: the 35 fault-injection causes match the vocabulary's 35 twin-expressible rows **35/35 on fault** (component/field/value, after normalising the registry's `fault_state` form to the vocabulary's `field: fault` form); the 10 without a fault injection are exactly the 10 non-twin-expressible vocabulary rows |
| `backend/simulation/repair_actions_omnipro.json` (+ `repair_actions.py`, which loads it and the registry by fixed relative paths) | 20 reviewed RepairActions in 7 of 9 cases (omni-01 4, omni-02 4, omni-03 1, omni-04 4, omni-05 2, omni-06 3, omni-08 2; none for omni-07, omni-09) | 20 actions | Cross-check only: **all 20 fall inside the 35 rows**; 15 of the 35 rows have no reviewed RepairAction yet. Contributes 0 oracle rows (actions are remedies/verify traces, not oracle rows) |
| `backend/simulation/omnipro_edge_list.json` | 20 topology edges across all 9 cases | 20 edges | 0 rows. Supports the cases' `required_chain` (kept verbatim) |
| `backend/simulation/golden_rules/omnipro_wire_feed.json` | 1 seed, 4 rules, 4 states (Feed Tensioner / Wingnut, p10 + p42) | 4 rules | 0 rows |
| `backend/simulation/omnipro_proof.py` | Proof twin: 18 entities, 17 observable ports, 23 test models | — | 0 rows. The twin the 20/35 was measured against; its observable/test models are what the oracle scores, not oracle content |
| `~/workspace/crater-benchmarks/` | No OMNIPRO oracle file. README: "C001 (OMNIPRO) is the training set, not a benchmark case." Evaluations hold `c002-lincoln.json`, `c002b-miller.json`, `c003`–`c016` session files (other machines), `lincoln_parser_rows.json`, `miller_parser_rows.json` — no OMNIPRO parser-rows file. `harness.py` mentions the OMNIPRO oracle only in comments. `tools/build_oracles.py` built the Lincoln/Miller oracles by mirroring the OMNIPRO vocabulary's conventions | 0 rows | 0 rows; `c001-omnipro` case_id taken from the README's C001 label (see §3) |
| OMNIPRO manual — `~/workspace/project-review-v3/docs/owner-manual.pdf` (Harbor Freight OMNIPRO 220, Item 57812, 48 pp) | Troubleshooting tables pp. 42–43, heading "Troubleshooting – MIG / Flux-Cored Welding" (no section number); p42: 5 problems → 25 causes; p43: 4 problems → 20 causes | 45 causes | Source of `source_pages` ([42]/[43]; printed page == PDF page — verified: `pdftotext -f 42` returns p42 content, and `_load_oracle_set` parses `pdf.pages[page_no - 1]`) and `source_table` (heading verbatim from p42; p43 carries no repeated heading, the table spans both pages) |
| `PHASE3C_BUILD_NOTES.md` + goals `phase3c-oracle-report.json` | Measured proof-twin run: headline **20/35**, O1 pass, O3 **57/58**; first 3C run 0/35 | — | Corroboration only: per-case denominators 4/6/4/5/4/5/3/2/2 match this file exactly, and the O3 pair count 58 is reproduced from them (6+15+6+10+6+10+3+1+1) |

## 2. Row-by-row mapping (35 rows, vocabulary file order)

`letter` is the cause letter within its repair case (registry order). `registry cause` is
the `repair_cases_omnipro.json` wording; `cause` is the vocabulary/parser-exact wording
(they differ on 13 rows — the vocabulary follows the parser's exact text per its
`join_rule`; see §4). `fault` is the vocabulary fault, which agrees with the registry's
`fault_injection` on all 35 rows.

| row_id | case | letter | p. | registry cause | cause (vocabulary / parser-exact) | fault |
|---|---|---|---|---|---|---|
| omni-01 | omni-01-wire-not-feeding | a | 42 | Insufficient wire feed pressure. | Insufficient wire feed pressure. | Wire Feed Mechanism / tension = 'low' (parameter) |
| omni-02 | omni-01-wire-not-feeding | b | 42 | Incorrect wire feed roller size. | Incorrect wire feed roller size. | Wire Feed Mechanism / groove_matches = False (parameter) |
| omni-03 | omni-01-wire-not-feeding | c | 42 | Damaged MIG Gun, cable, or liner assembly. | Damaged MIG Gun, cable, or liner assembly. | MIG Gun / fault = 'open' (fault_state) |
| omni-04 | omni-01-wire-not-feeding | d | 42 | Feed Tensioner is too tight. | Feed Tensioner is too tight. | Wire Feed Mechanism / tension = 'high' (parameter) |
| omni-05 | omni-02-wire-stops | a | 42 | Gun cable is severely bent and Wire Feed mechanism cannot feed wire. | Gun cable is severely bent and Wire Feed mechanism cannot feed wire. | MIG Gun / fault = 'bent' (fault_state) |
| omni-06 | omni-02-wire-stops | b | 42 | Gun liner is clogged or worn. | Gun liner is clogged or worn. | liner assembly / fault = 'clogged' (fault_state) |
| omni-07 | omni-02-wire-stops | c | 42 | Gun liner is too small for welding wire being used. | Gun liner is too small for welding wire being used. | liner assembly / size_matches = False (parameter) |
| omni-08 | omni-02-wire-stops | d | 42 | Wire is tangled on the spool. | Wire is tangled on the spool. | Wire Spool / fault = 'tangled' (fault_state) |
| omni-09 | omni-02-wire-stops | e | 42 | Wire is not making contact with Feed Rollers. | Wire is not making contact with Feed Rollers. | Wire Feed Mechanism / groove_matches = False (parameter) |
| omni-10 | omni-02-wire-stops | f | 42 | Feed Roller is not making enough contact with wire or is crushing flux-cored wire. | Feed Roller is not making enough contact with wire or is crushing flux-cored wire. | Wire Feed Mechanism / tension = 'low' (parameter) |
| omni-11 | omni-03-birds-nest | a | 42 | Excess wire feed pressure. | Excess wire feed pressure. | Wire Feed Mechanism / tension = 'excess' (parameter) |
| omni-12 | omni-03-birds-nest | b | 42 | Incorrect Contact Tip size. | Incorrect Contact Tip size. | Contact Tip / size_matches = False (parameter) |
| omni-13 | omni-03-birds-nest | c | 42 | MIG Gun Cable Connector not fully inserted into Wire Feed mechanism. | MIG Gun Cable Connector not fully inserted into Wire Feed mechanism. | MIG Gun / connector_seated = False (parameter) |
| omni-14 | omni-03-birds-nest | d | 42 | Damaged liner. | Damaged liner. | liner assembly / fault = 'damaged' (fault_state) |
| omni-15 | omni-04-arc-not-stable | b | 42 | Incorrect Contact Tip or liner size or excessive wear. | Incorrect Contact Tip or liner size or excessive wear. | Contact Tip / fault = 'worn' (fault_state) |
| omni-16 | omni-04-arc-not-stable | d | 42 | Loose MIG Gun cable or ground cable. | Loose MIG Gun cable or ground cable. | MIG Gun / fault = 'loose' (fault_state) |
| omni-17 | omni-04-arc-not-stable | e | 42 | Damaged MIG Gun or loose connection within Gun. | Damaged MIG Gun or loose connection within Gun. | MIG Gun / fault = 'damaged' (fault_state) |
| omni-18 | omni-04-arc-not-stable | g | 42 | Gas coverage may be insufficient or too high. | Gas coverage may be insufficient or too high. | MIG Gun / gas_flow = 'low' (parameter) |
| omni-19 | omni-04-arc-not-stable | h | 42 | Poor connection with workpiece. | Poor connection with workpiece. | Ground Clamp / fault = 'poor_connection' (fault_state) |
| omni-20 | omni-05-does-not-function | a | 43 | Tripped thermal protection device. | Tripped thermal protection device. | Welder / fault = 'thermal_trip' (fault_state) |
| omni-21 | omni-05-does-not-function | b | 43 | Circuit supplies insufficient voltage or amperage. | Circuit supplies insufficient input voltage or amperage. | Welder / input_voltage = 'very_low' (parameter) |
| omni-22 | omni-05-does-not-function | c | 43 | Faulty or improperly connected Trigger. | Faulty or improperly connected Trigger. | Trigger / fault = 'faulty' (fault_state) |
| omni-23 | omni-05-does-not-function | d | 43 | Low or over-voltage protection is active. | Machine is in low- or over-voltage protection. | Welder / protection = 'active' (parameter) |
| omni-24 | omni-06-lcd-dark | a | 43 | Unit not connected to outlet properly. | Unit is not connected to outlet properly. | Input Power Cord / fault = 'not_connected' (fault_state) |
| omni-25 | omni-06-lcd-dark | b | 43 | Outlet is unpowered; breaker or GFCI tripped. | Outlet is unpowered. | Input Power Cord / power_in = 'absent' (parameter) |
| omni-26 | omni-06-lcd-dark | c | 43 | Plug rating is wrong. | Plug does not have correct rating. | Input Power Cord / plug_rating = 'wrong' (parameter) |
| omni-27 | omni-06-lcd-dark | d | 43 | Circuit breaker tripped from high input amperage. | Circuit breaker has tripped due to high input amperage. | Reset Button / fault = 'tripped' (fault_state) |
| omni-28 | omni-06-lcd-dark | e | 43 | Input Power Cord is not seated. | Input Power Cord is not seated properly. | Input Power Cord / fault = 'not_seated' (fault_state) |
| omni-29 | omni-07-no-arc | a | 43 | Improper ground connection. | Improper ground connection. | Ground Clamp / fault = 'poor_connection' (fault_state) |
| omni-30 | omni-07-no-arc | b | 43 | Improperly sized Contact Tip. | Improperly sized Contact Tip. | Contact Tip / size_matches = False (parameter) |
| omni-31 | omni-07-no-arc | c | 43 | Excessively worn Contact Tip. | Excessively worn Contact Tip. | Contact Tip / fault = 'worn' (fault_state) |
| omni-32 | omni-08-porosity | a | 43 | Empty shielding-gas bottle. | Shielding gas bottle is empty. | Shielding Gas Bottle / level = 'empty' (parameter) |
| omni-33 | omni-08-porosity | b | 43 | Wrong gas flow. | Not enough or too much shielding gas. | MIG Gun / gas_flow = 'low' (parameter) |
| omni-34 | omni-09-weak-arc | a | 42 | Incorrect line voltage. | Incorrect line voltage. | Welder / input_voltage = 'low' (parameter) |
| omni-35 | omni-09-weak-arc | b | 42 | Improper gauge or length of cord. | Improper gauge or length of cord. | Input Power Cord / cord_gauge = 'wrong_gauge' (parameter) |

Per-case row counts: omni-01 4, omni-02 6, omni-03 4, omni-04 5, omni-05 4, omni-06 5,
omni-07 3, omni-08 2, omni-09 2 (= 35). Same-symptom pairs for O3: 58.

Six fault values repeat across different symptoms (rows are per case+cause, as in Miller,
where e.g. Main Control Board `faulty` also repeats — so these are not duplicate rows):
Wire Feed Mechanism `tension=low` (omni-01, omni-02); `groove_matches=false` (omni-01,
omni-02); Contact Tip `size_matches=false` (omni-03, omni-07); Contact Tip `fault=worn`
(omni-04, omni-07); MIG Gun `gas_flow=low` (omni-04, omni-08); Ground Clamp
`fault=poor_connection` (omni-04, omni-07). Distinct fault tuples among the 35: 29.

## 3. Derived vs directly sourced (every field)

**Copied verbatim from `oracle_vocabulary_v1.json`:** every row's `cause`, `category`,
`fault`, `observables`, `tests`, `basis`, `flags`; the one row's `notes` (omni-06,
"Outlet is unpowered." — 3C correction on the fault location, kept verbatim); the
`answer_key` `corrective_action` (the vocabulary `remedy`); `cases` (all 9, including
`case_rank`, `nominal`, `observable_components`, `required_chain` — the 3C topology
chains, unlike Miller's empty `required_chain`), `tests` (26), `categories`, `product`,
`source`, `scope_disclaimer`, `review`, `join_rule`.

**Derived mechanically (no judgement about manual content):**

- `row_id` `omni-01`…`omni-35`, sequential in vocabulary file order — the direct analogue
  of Miller's `miller-01`…`miller-63`. The vocabulary itself has no row ids; the runtime
  3A/3C join instead used `oracle-row-01`…`oracle-row-45` (see `backend/simulation/oracle.py`
  `build_oracle_set`). Either scheme is a label, not content.
- `symptom` per row, from `cases[case_id].symptom`.
- `source_pages` `[42]` / `[43]`, from the case's `table_page` (printed == PDF page, §1).
- `source_table` `"Troubleshooting – MIG / Flux-Cored Welding (p. 42/43)"` — the PDF p42
  heading verbatim + page (Miller's form is `"6-5 Troubleshooting (p. 31)"`; the OMNIPRO
  manual has no troubleshooting section number).
- `answer_key[row_id].remedy_source = "registry_remedy"` — the remedy text is the
  registry's (`repair_cases_omnipro.json`), per the vocabulary's `join_rule` (the parser's
  remedies fragment across pdfplumber table rows on p42/p43, so they are misaligned with
  causes). Miller's equivalent value is `"remedy_column_only"`.
- `case_id = "c001-omnipro"` — from `crater-benchmarks/README.md` ("C001 (OMNIPRO) is the
  training set"); the vocabulary file itself has `case_id: null`.
- `version = "oracle-vocabulary-omnipro-v1"` — Miller analogue of
  `"oracle-vocabulary-miller-v1"` (vocabulary file's own version is `"oracle-vocabulary-v1"`).
- `created = "2026-11-23"` — the date of this reconstruction; the source vocabulary was
  created 2026-11-13.
- `denominator = {total 35, twin_expressible 35, settings 0, technique 0, cross_reference 0,
  unmappable 0}` — the subset denominator; see §0 for the 45-row caveat.
- `registry_cause` dropped from rows (Miller rows carry no registry wording) and `remedy`
  moved off the row into `answer_key` (Miller rule: rows carry NO remedy). The registry
  join is preserved by this table (§2).

**Known single discrepancy (not hidden):** on one row the vocabulary remedy differs from
the registry fix — omni-05 cause d, "Machine is in low- or over-voltage protection.":
vocabulary `"Check the input voltage is in range, then press the Reset Button on the back."`
vs registry `"…on the back of the unit."` The vocabulary (reviewed) text was kept verbatim;
the difference is the dropped trailing "of the unit".

**Deliberately NOT done:** the 10 non-twin-expressible rows were not merged in, no
observable/test vector was computed from the proof twin (that would make the oracle
circular — the oracle must stay an independent manual-derived artefact), and no cause
text was re-transcribed from the PDF (the vocabulary's parser-exact text stands).

## 4. Why the registry and vocabulary cause texts differ (13 rows)

The registry paraphrases some causes; the vocabulary uses the deterministic parser's exact
manual text (its `join_rule`: cause text is the parser's exact text). The 13 differing
rows: omni-05 b ("Circuit supplies insufficient voltage or amperage." → "…insufficient
input voltage or amperage."), omni-05 d, omni-05 e, omni-06 a–e, omni-08 a, b, d, e, f.
Faults agree on all 35 rows regardless of wording (verified programmatically).
The `notes`/`flags` fields carry the vocabulary author's judgement where the manual is
silent or covers two directions in one cause row — 11 of the 35 rows carry `flags`.

## 5. The 10 rows NOT in this file (full 45-row oracle)

All 10 exist in `oracle_vocabulary_v1.json` with full expected vectors. To build the full
45-row Miller-schema oracle, add these rows (same mechanical derivation as §3) and set
the denominator to total 45 / twin_expressible 35 / settings 5 / technique 4 /
cross_reference 1 / unmappable 0:

| case | category | cause |
|---|---|---|
| omni-04-arc-not-stable | cross_reference | Wire not feeding properly. |
| omni-04-arc-not-stable | settings | Incorrect wire feed speed. |
| omni-04-arc-not-stable | settings | Incorrect polarity for process being run. |
| omni-05-does-not-function | settings | Machine is in the incorrect mode. |
| omni-07-no-arc | technique | Dirty Contact Tip. |
| omni-08-porosity | technique | Dirty workpiece. |
| omni-08-porosity | technique | Gun is being used too far away from workpiece. |
| omni-08-porosity | settings | Polarity is incorrect for the application. |
| omni-08-porosity | technique | Dirty welding wire is introducing contamination into the weld. |
| omni-09-weak-arc | settings | Not enough current. |

These 10 are exactly the 10 repair-case causes with no `fault_injection` (verified).

## 6. Verification performed (23 Nov 2026)

- Row count exactly 35; `row_id` unique and sequential `omni-01`…`omni-35`.
- Top-level keys identical to `frozen/c002b-miller.json` (14 keys, same order).
- Denominator matches the rows exactly (total 35 / twin_expressible 35 / rest 0).
- Every row's `observables`/`tests` key sets match its case's sets (the benchmark-runner
  validator rule, `BENCHMARK_ORACLE_RUNNER_BUILD_NOTES.md`).
- Every twin-expressible row has a complete fault (`component`/`field`/`value`/`kind`);
  no other row carries one (vacuous here — all 35 are twin-expressible).
- `answer_key` covers 35/35 rows; every `corrective_action` is the vocabulary remedy verbatim.
- Per-case counts 4/6/4/5/4/5/3/2/2 reproduce the 3C O3 pair count 58 and match the
  3C report's per-case denominators.
- Cross-checks: registry fault injections agree 35/35; all 20 RepairActions fall inside
  the 35; manual PDF (48 pp) p42/p43 cause counts 25 + 20 = 45 confirmed by direct read.
- NOT run: the benchmark oracle runner CLI against a live DB (no `.venv` in
  `crater-refactor`, and the runner scores a twin rather than validating the file — the
  file-level checks above are the documented validator rules). A runner run against the
  Miller twin or a fresh OMNIPRO twin is still open, and would be the first live check
  of this file.

## 7. What the OMNIPRO gate therefore is

- The **35-row gate stands, as the twin-expressible headline gate**: O2 20/35 (proof twin,
  measured), O1 pass, O3 57/58 — reproducible from this file's rows.
- The **Miller-only framing is retired**: the OMNIPRO oracle is no longer missing. What was
  missing was a standalone frozen file — the content lived split across the vocabulary
  (goals files dir, outside the repo), the registry, and the runtime parser join.
  This file freezes the 35-row subset; the 10 remaining rows are listed in §5 for the
  45-row file if Nishant wants the full oracle frozen too.
- Open: reviewer sign-off (`draft-for-review`, reviewer null); a live runner check of this
  file against a real twin; and whether to also freeze the 45-row full oracle.

## 8. Reconciliation against the re-uploaded zip — 23 Nov 2026 (appended, history above unchanged)

Nishant re-uploaded the project zip and said: check this zip, it should have it, if not
reconstruct it. This section records the check. It does not rewrite §§0–7; where it
corrects a detail there, it says so explicitly.

### 8.1 Zip verification

- Zip: `~/workspace/user/files/project-review-phase6-readiness-ingestion-gemini-fix-combined.zip`,
  sha256 `d6886155a4394a727a32f8e38ac6794d81678ebeb02596c30e7371bacecedb92` — **matches
  the frozen baseline hash exactly** (byte-identical to the frozen baseline).
- **256 files. No OMNIPRO oracle file in the zip**: no `evaluations/` directory, no `c001`,
  no 35-row file, no `frozen/omnipro-oracle-35.json`. The zip was extracted pristine to
  `/tmp/zip-src` (and in full to `/tmp/zip-full` for the re-run in §8.4) and the working
  tree was diffed against it: the 7 OMNIPRO sources below are **byte-identical** between
  zip and working tree.
- OMNIPRO sources actually in the zip (5 files + 2 code files):
  `backend/knowledge/repair_cases_omnipro.json` (9 cases, 45 causes),
  `backend/simulation/repair_actions_omnipro.json` (20 actions),
  `backend/simulation/omnipro_edge_list.json` (20 edges),
  `backend/simulation/golden_rules/omnipro_wire_feed.json` (1 seed, 4 rules),
  `backend/simulation/oracle.py` (fault-injection registry loader + `build_oracle_set` join),
  `backend/simulation/omnipro_proof.py` (constructed 18-entity proof model),
  `backend/simulation/repair_actions.py` (loads the actions + registry by fixed relative paths).
  No manual PDF is in the zip; the manual cited below
  (`~/workspace/project-review-v3/docs/owner-manual.pdf`, Harbor Freight OMNIPRO 220,
  Item 57812, 48 pp) lives outside it.

### 8.2 What was already in the zip vs what was reconstructed

- **Already in the zip:** every row's identity — case, symptom, cause (in paraphrase form
  on 9 rows, verbatim on 26), fault injection, remedy, test text, table page — plus the
  20 reviewed RepairActions, the 20 topology edges, the 4 golden rules, the join/validation
  code (`oracle.py`), and the proof twin the numbers were measured against
  (`omnipro_proof.py`).
- **Reconstructed (this file):** the standalone 35-row file in Miller schema — `row_id`
  `omni-01`…`omni-35`, `answer_key` (remedy moved off the row, Miller rule), denominator
  `{total 35, twin_expressible 35, settings 0, technique 0, cross_reference 0,
  unmappable 0}`, `case_id` `c001-omnipro`, and the expected observable/test vectors per
  row. The vectors' *keys* are all modelled by zip sources (see §8.3); their *values*
  were authored in the vocabulary file and are corroborated, not re-derived, by the
  re-run in §8.4.
- The outside vocabulary file (`oracle_vocabulary_v1.json`, 45 rows) remains the discovery
  source — it is where the 35 rows were first assembled in one place — but it is no longer
  the only provenance: every row now carries its own zip citations in `zip_sources`.

### 8.3 Per-row zip-traceability audit (zip sources only, row by row)

Audited each of the 35 rows against the extracted zip files (scripted, not by eye;
`/tmp/audit_omnipro.py`, results in `/tmp/audit_results.json`):

| Check | Result |
|---|---|
| Fault matches a registry `fault_injection` in the same case | **35/35** — the earlier 35/35 claim is verified, not taken on trust. All 35 non-null registry injections map onto exactly these 35 rows; 0 registry injections lack a row. |
| Cause wording identical to the registry | 26/35. The other 9 rows differ in wording only (see below) — the underlying cause is the same registry cause in the same case. |
| Remedy (`answer_key.corrective_action`) identical to the registry `fix` | 34/35. The 1 exception is omni-23 (see below). |
| Symptom identical to the registry case symptom | 35/35 up to one apostrophe: omni-03's symptom is `Bird’s` (U+2019, as printed in the manual) in this file vs `Bird's` (ASCII) in the registry. |
| `source_pages` identical to the registry `table_page` | 35/35. |
| RepairAction coverage | 20/20 actions map to rows in this file; 20 distinct rows have one, 15 rows have none — as §1 said. |
| Edge-list coverage | All 9 cases have reviewed edges (3–7 per case); every row cites its case's edge ids. |
| Golden rules | omni-04 directly (rule `seed:feed-tension-too-tight` is p42 cause 4); omni-01 via its remedy rule + the p10 wingnut analogue its `basis` cites; omni-08 via the p10 tangling rule (supporting, not direct). |
| **Vocabulary-only rows** | **0/35. No row is supported only by the outside vocabulary file.** |

**The 9 wording rows (cause text is manual-exact; the registry paraphrases):**
omni-21, omni-23, omni-24, omni-25, omni-26, omni-27, omni-28, omni-32, omni-33.
Each was checked verbatim against the manual PDF p42/p43 text (`pdftotext -f 42 -l 43`):
all 35 frozen causes — and all 45 vocabulary causes — appear verbatim in the PDF, while
exactly 13 registry causes do not (these 9 + the 4 paraphrases among the 10 non-twin
rows: omni-05 e, omni-08 d, e, f). So the file's wording is the manual's wording, and the
registry is the paraphrase — the reverse of what a reader might assume. §4's "13 rows"
is across all 45 causes; within these 35 it is 9.
**omni-23 remedy:** the one remedy difference is also in the file's favour against the
manual — p43 solution 4 reads "press Reset Button on back of machine" (no "of the unit");
the vocabulary/file text follows the manual and the registry adds the phrase.

**Field-level honesty (what the zip does NOT supply per row):** the expected
observable/test *values* and the `basis`/`flags` inferences are vocabulary-authored.
What the zip does supply for them: every one of the 17 observable keys used by the rows
is mapped by the proof twin's `observable_paths`, and 23 of the 26 test keys have a
model in `build_test_models`. The 3 unmodelled test keys — `gun_distance_check`,
`wire_clean_check`, `workpiece_clean_check` — appear only on omni-32/omni-33; the
harness records them as `unmodelled_test` gaps (both in the 14 Nov report and in the
§8.4 re-run), never as failures. The 10 non-twin rows (§5) are exactly the 10 registry
causes whose `fault_injection` is `null` — re-verified, not assumed.

**Change made in this reconciliation:** a `zip_sources` field was added to every row of
`frozen/omnipro-oracle-35.json` (2–4 entries per row: registry case + cause letter with
fault/fix/test/page; RepairAction id where one exists; the case's edge-list ids; golden
rule where applicable; wording notes on the 9 rows + omni-23). Verified by diff: no other
field, key, or value in the file changed; top-level keys, `answer_key` coverage (35/35),
and denominator are unchanged. `oracle.py`'s `build_oracle_set` / `load_fault_injections`
is the join mechanism for all rows and is cited here rather than repeated per row.

### 8.4 O1/O2/O3 recomputation (independent re-run, 23 Nov 2026)

Re-ran the zip's own harness (`oracle.run_oracle`, zip code from `/tmp/zip-full`) against
the zip's proof twin (compiled from `omnipro_proof.py` + the golden-rules seed, exactly
as `scripts/run_phase3c_oracle.py` does it: 18 components, 648 transitions, 20 topology
pairs, 0 unresolved), feeding it the frozen file's rows with faults taken from the
**registry** (not from the file's fault field, so the run is independent of the field
under test). The frozen file's faults were already verified 35/35 against the registry
in §8.3, so the two inputs coincide. Full re-run output in `/tmp/recomputed_proof_report.json`.

| Number | Recomputed | 14 Nov phase3c report (`proof_oracle`) | Match? |
|---|---|---|---|
| O1 nominal gate | pass | pass | yes |
| Headline (rows passing O2+O3+O4+O5) | **20/35** (rate 0.5714) | 20/35 | yes |
| O2 alone (fault → symptom vector) | 22/35 | 22/35 (derived from its rows array) | yes |
| O3 discriminability | **57/58** (rate 0.9828) | 57/58 | yes |
| Per-case passed | 4/4, 4/6, 1/4, 4/5, 2/4, 3/5, 0/3, 2/2, 0/2 | identical | yes |
| Per-row O2/O3/O4/O5 + outcome | 35 rows compared | 0 mismatches | yes |

Two clarifications the re-run pins down. First, "O2 20/35" as quoted in §§0/7 is shorthand
for the **headline** count (rows passing all of O2–O5); O2 alone passes 22/35, and the
other two failures are O3 — the single indistinguishable pair omni-25/omni-27 (omni-06
"Outlet is unpowered." vs "Circuit breaker has tripped…"), whose actual vectors the twin
cannot separate. Second, O3 57/58 is derivable from the file alone: the 58 same-symptom
pairs computed from the file's per-case counts (6+15+6+10+6+10+3+1+1) have distinct
**expected** vectors on 57 pairs, with the same single indistinguishable pair
(omni-25, omni-27) — so the file-only O3 and the twin-actual O3 agree. Distinct fault
tuples among the 35: 29 (6 repeat, as §2 lists). The 13 O2 failures are
omni-06, omni-07, omni-12, omni-13, omni-14, omni-16, omni-20, omni-23, omni-29,
omni-30, omni-31, omni-34, omni-35 — identical to the report's `o2_mismatch` gap list
once its parser-order `oracle-row-NN` ids are mapped through its own rows array
(the report numbers rows in parser/PDF order, not vocabulary order, which is why a naive
ordinal mapping misaligns).

**Conclusion:** the faithful zip-only reconstruction yields **all 35 rows** — nothing was
padded, nothing was dropped, and no row is vocabulary-only. The file stands as the first
Phase 1 deliverable and the oracle gate uses it, labelled as reconstructed. The one
correction to §§0–7 is terminological: "O2 20/35" is the headline (all-checks) count;
O2 alone is 22/35.

