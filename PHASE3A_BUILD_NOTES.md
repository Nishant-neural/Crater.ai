# Phase 3A build notes — oracle harness ONLY (no 3B)

**Date:** 13 Nov 2026. **Base:** Phase 2 (`project-review-phase2-patched.zip`, this tree).
**Package:** `~/workspace/crater-phase1/project-review-phase3a-patched.zip` (this tree).
**Design:** `phase3-design-v3-archetype-oracle.md` — the original Amendments
(13 Nov 2026, four fixes + §12 answers) **and** the second "Amendments —
13 Nov 2026, second-opinion review" section, which supersedes v3 where
they disagree. 3A only was approved; no 3B work is in this build.

## What 3A built (in the order the amendments require)

### 1. `fault_injection` per cause — first deliverable (Amendments item 5)

`backend/knowledge/repair_cases_omnipro.json`: every one of the 45 causes
gained a reviewed `fault_injection` field — `{instance, parameter, value}`
for adjustment/parameter causes (e.g. case 1 cause (a):
`{instance: "Wire Feed Mechanism", parameter: "tension", value: "low"}`),
`{instance, fault_state}` for fault-state causes, `null` for the 10
settings / technique / cross-reference causes. 35 with an injection, 10
without. This field doubles as the oracle's cause-to-fault mapping (the
binding, not the LLM, maps a cause to its injection), and it is what
resolves Phase 0 fix 2 (adjustment-cause mapping) — by replacing the
tournament's name matching for these causes, not by fixing it. Note the
instance is the archetype instance that carries the parameter (tension
lives on the Wire Feed Mechanism), not the part named in the cause (the
Feed Tensioner).

Also corrected in the same file: the registry's test for omni-01 cause 4
was a remedy phrase ("Loosen the tensioner until the spool stops spinning
shortly after the trigger is released"), now stated diagnostically as the
spin check: "Release the Gun Trigger and observe whether the wire
continues to spin afterwards."

### 2. Vocabulary artifact (separate reviewed artifact from archetype rows)

- `~/workspace/goals/crater-ai-diagnosis-and-machine-model-workflow-improvement/files/oracle-vocabulary-v1.md`
- `.../files/oracle_vocabulary_v1.json` (machine-readable, the harness's input)

Per case, per cause row (45 rows): observable vector (port-qualified),
test-outcome vector (each discriminating test as a function on twin
state), category, fault injection, source pages, basis + flags. Categories
verified against the manual: **35 twin-expressible / 5 settings /
4 technique-contamination / 1 cross-reference = 45**. The file states
the scope disclaimer: the oracle proves the twin matches the manual's
claims, not that the machine behaves that way; field data is the only
real ground truth.

**Spin correction (Amendments item 1 — v3 was wrong, the review is
right).** Correct tension *prevents* continued spinning after the Gun
Trigger is released (p42 remedy, quoted), so spinning means tension too
**low** (p10: spool spins freely = wingnut too loose; the seed's
`tension_ok` note agrees). Corrected case-1 encoding: (a) wood test
`stops` (quoted p17), spin check `spins` — inferred from the p42 remedy +
the p10 analogue, flagged; (d) wood test `unmodelled` (the manual states
no wood-test outcome for too-high tension; p17 is a lower-bound check
only), spin check `no_spin` — inferred from the remedy's target state,
monotonic in pressure, flagged. O3 for the (a)/(d) pair passes via the
spin check, not the wood test. **Every inferred value is flagged** (11
flagged rows); an unflagged inference is a bug, and a test
(`test_every_inferred_value_is_flagged`) enforces the flag wording.

Each vocabulary row also carries `registry_cause` (the exact cause text
in the registry — parser and registry wordings differ for cases 5, 6, 8),
so the harness can join registry `fault_injection` to parser cause rows
through the vocabulary as the bridge.

### 3. Oracle harness — `backend/simulation/oracle.py` (pure logic, no LLM)

Loads the oracle set by expanding the deterministic parser output
(failure modes → cause rows; verified: p42 → 5 → 25, p43 → 4 → 20, total
45) joined to the vocabulary and the registry mapping, and runs O1–O5
against a compiled twin dict (components, transitions, topology from
`metadata["topology"]`). Fault injection reuses the existing engine
(`DigitalTwinEngine`, read-only use — `engine.py` untouched). There is
**no propagation engine** in 3A: the twin's own explicit transitions are
the only behaviour that can fire, and a fault the twin cannot express
fails as unmappable instead of being bridged. Per row: pass/fail per
check, expected vs actual vectors, a reason for every failure
(`topology_gap` / `unmappable_cause` / `no_transition_expresses_fault` /
`observable_unmapped` / `unmodelled_test` / `o2_mismatch` /
`o3_indistinguishable` / `o4_spurious_output` / `o5_thin_trace` / …).
Never invents an edge, a parameter value, or a verdict. Deterministic:
same twin + same fault → same JSON (tested: run twice, identical).

Two harness semantics fixed while writing the tests (both are design
decisions, recorded here, not bugs papered over):

- **O2 checks the observable vector only** (v3 §6.3). A test the twin
  cannot model is recorded as `unmodelled_test` on the row and in the
  ranked gap list, and bites in O3 (where separation needs modelled
  tests) — never guessed, never counted as a symptom match. A test the
  vocabulary itself records as `unmodelled` for a cause (case-1 (d)'s
  wood test) is not a gap.
- **O4 exempts machine-root observables** (3A limitation, recorded in
  the code): observables owned by the machine root (`Welder` — arc /
  lcd / welder_on) are machine-level effects that cannot be attributed
  to a chain position until 3B binding gives each observable a proper
  port owner. O4 still catches spurious outputs on distinct chain
  components (tested: a fault transition flipping upstream motor motion
  fails O4).

Gap reasons are measured independently of evaluability, so the ranked
gap list shows every gap that blocks a row (a row blocked by an
unmappable cause is *also* blocked by its missing topology edges,
unmapped observables, and unmodelled tests) — topology gaps first, then
by rows blocked.

### 4. Baseline runner + report

`scripts/run_phase3a_baseline.py` compiles a twin from `crater-10.db`
(read-only) exactly the way the app's compile route does
(`_model_from_payload` → `load_seed_for_product` → `apply_expert_seed` →
`compile_machine_model`) and runs the oracle with NO observable mapping
and NO test models. Reports:
`.../files/phase3a-baseline-report.md` and `.json`.

### 5. Housekeeping folded into 3A (Amendments item 7)

- **Collection error fixed.** `tests/test_phase8a_hardening.py` imported
  `invalid_fact_keys` and `validate_facts` — neither exists in
  `backend/knowledge/validation.py` (it exports `validate_machine_model`,
  `quarantine_machine_model`, `validate_machine_model_with_ledger`). The
  test was wrong, not the module: aligned with the real API, including
  the evidence-revision test, which now uses the real Phase 2 field
  `source_revision` (there is no `revision_id` field on
  `MachineEvidence`). No production function was invented. **Final
  suite has zero collection errors.**
- **Offline suites now run with the suite.** New
  `tests/test_offline_suites_v9_v10.py` runs `~/workspace/verify-v9.py`
  and `~/workspace/verify-tournament-v10.py` as subprocesses and fails
  if either reports a failure (skipped when the scripts/DB are absent).
  Both pass: **verify-v9 17/17, verify-tournament-v10 27/27** (run
  13 Nov 2026). Scope note: the scripts hardcode their import path to
  the `project-review-batch-fix` tree, so they verify that tree, not
  this one; the hermetic equivalents for this tree already run in the
  suite (`test_verified_repair.py`, `test_hypothesis_tournament.py`,
  `test_visual_twin.py`, Phase 2 seed/dedupe tests).

## Tests

Baseline (before this build, reproduced): **243 passed, 2 failed,
1 collection error** — the 2 failures in `tests/test_phase8a_integration.py`
and the collection error in `tests/test_phase8a_hardening.py` were
pre-existing on pristine v12.

Final: **271 passed, 2 failed, 0 collection errors.** The same 2
pre-existing failures (`test_phase8a_integration.py`:
`test_pdf_chunk_modalities_reach_universal_persistence`,
`test_schematic_projection_creates_entities_ports_and_relations`); no new
failures. **28 new tests pass**: `tests/test_phase3a_oracle.py` (22) —
parser expansion 25+20=45, vocabulary schema + 35/5/4/1 totals, spin
correction + inference-flag enforcement, registry `fault_injection`
coverage + case-1 mapping + disagreement reporting, O1 nominal
pass/fail, O2 match/mismatch, O3 case-1 (a)/(d) indistinguishable on the
wood test alone and separated via the spin check, O4 spurious output,
O5 thin one-boolean trace + topology gap, unmappable cause stays in the
denominator, unmapped observables fail without guessing, determinism,
no-LLM-calls; `tests/test_phase8a_hardening.py` (4, rewritten);
`tests/test_offline_suites_v9_v10.py` (2).

## Baseline numbers (crater-10.db, Phase 2 twin, 13 Nov 2026)

- **Headline: 0/35 = 0.0%** (twin-expressible rows). O1 nominal gate: **fail**
  (no observable mapping exists in 3A, so no observable can read nominal).
- Denominator: 35 twin-expressible / 5 settings / 4 technique / 1 cross-reference / 45 total.
- Per-case rates (twin-expressible only): case 1: 0/4; case 2: 0/6; case 3: 0/4;
  case 4: 0/5; case 5: 0/4; case 6: 0/5; case 7: 0/3; case 8: 0/2; case 9: 0/2.
- Discriminability (O3): **0/58 pairs separated**.
- **Ranked gap list:** `topology_gap` blocks 35 rows; `observable_unmapped`
  35; `unmodelled_test` 35; `unmappable_cause` 29; `no_transition_expresses_fault` 6.
- Twin: 201 components, **0 transitions**, 4 topology edges (3 unique:
  Gun Cable Connector → MIG Gun, Trigger → Welder, Ground Clamp → Workpiece;
  the connector→gun edge appears twice). Snapshot: 0 explicit simulation rules.
- **Seed: no expert seed matched this product row.** `load_seed_for_product`
  wants model `OMNIPRO 220`; this DB's product row is model `220`, so no
  seed applied. Separately, applying the seed file directly would still
  yield 0 rules on this snapshot: 7 Feed Tensioners (tensioner rules skip
  as ambiguous) and the Wingnut rules reference `Wire Spool`, which has no
  entity in this snapshot.
- **Duplicates in this pre-Phase-2 twin** (fault instances resolving to ≠1
  component): MIG Gun 6, Contact Tip 5, Ground Clamp 4, Welder 4, Wire Feed
  Mechanism 3, Input Power Cord 0, Wire Spool 0. The consolidated Phase 1+2
  live check (fresh upload on the Phase 2 package) is what removes these;
  this baseline measures the twin as it is.
- **Seed vocabulary mismatch (Amendments item 6 — 3B work, recorded):**
  the seed speaks `tension_ok` (boolean) while the oracle vocabulary speaks
  `tension: low/normal/high`, and the seed's `tension_ok=false` rule is
  labelled "too tight" though false is ambiguous (too low *or* too high).
  3B translates seed rules into the parameter vocabulary at compile time;
  3A uses the seed as-is and records the mismatch as a gap. On this run the
  mismatch does not even arise — no seed matched at all.

This near-zero rate is the expected 3A baseline (v3 §6.6), not a
regression: the twin has no archetypes, no propagation engine, no
observable mapping and no test models. Nothing was tuned to raise it.
The gap list is the 3B work list; 3B is decided from it, not started by
this report.

## Files changed / added

- Backend (new): `backend/simulation/oracle.py`.
- Backend (edited): `backend/knowledge/repair_cases_omnipro.json`
  (`fault_injection` on all 45 causes + omni-01 cause-4 test restated
  diagnostically).
- Scripts (new): `scripts/run_phase3a_baseline.py`.
- Tests (new): `tests/test_phase3a_oracle.py`,
  `tests/test_offline_suites_v9_v10.py`.
- Tests (edited): `tests/test_phase8a_hardening.py` (aligned with the real
  validation API; no production code changed for it).
- Goal files (new, outside the tree): `oracle-vocabulary-v1.md`,
  `oracle_vocabulary_v1.json`, `phase3a-baseline-report.md`,
  `phase3a-baseline-report.json` (in
  `~/workspace/goals/crater-ai-diagnosis-and-machine-model-workflow-improvement/files/`).

## Deviations from the original 3A brief (with reasons)

1. **Parser remedies are not used as oracle remedies.** The deterministic
   parser's solutions column fragments across pdfplumber table rows on
   p42/p43 (e.g. "Follow step 27 on page" / "17." as separate steps), so
   parser remedies are misaligned with causes. The oracle row's remedy
   comes from the case registry (authored from the pdftotext reading of
   the same pages); the cause/symptom/pages are still parser ground
   truth, and no check reads the remedy. Recorded in the vocabulary's
   join rule.
2. **O2 is observables-only; unmodelled tests are gap-list items, not O2
   failures** (see §3 above). The original brief did not specify where
   an unmodelled test fails; v3 §6.3 defines O2 on the observable vector
   and O3 on test outcomes, so this follows v3.
3. **O4 exempts machine-root observables in 3A** (see §3 above). Without
   archetype binding there is no chain position for `Welder`-owned
   observables; guessing an attribution would be worse than exempting
   with the limitation recorded.
4. **The gap list counts independently measured gaps**, so one row can be
   blocked by several gaps at once (topology + unmapped + unmodelled +
   unmappable). A partition would have hidden the topology gap behind
   the mapping gap on 29 rows.
5. **Offline suites converted as subprocess wrappers, not rewritten.**
   Rewriting them against this tree (re-integration + HTTP route) is not
   cheap; the wrappers make the original gate run with the suite, and
   the hermetic equivalents already cover this tree.

## What was NOT built (per the brief + Amendments items 2–4, 6)

No archetypes, no propagation engine, no compiler v3, no UI, no LLM
calls anywhere. Not built, recorded for 3B: the engine-as-propagator
revision (compiler emits one generic conditional transition per row;
the engine's fixpoint propagates); the no-retraction/totality
requirement on rows (oracle lint checks exclusion/totality/settling);
the verifier's healthy-value fix (compiler v3 runs the nominal fixpoint
once and stores it as `initial_state`); the seed boolean → parameter
translation at compile time. The harness uses the current twin as-is.
No new dependencies. No change to `engine.py`, `diagnostics/agent.py`,
`tournament.py`, `verification.py`, the extraction pipeline, or the
v12 diagnosis ceiling (8192).

## What was not verified (needs Nishant's side)

- End-to-end ingestion with his LLM + Qdrant (same as Phases 1–2: the
  venv has no sentence-transformers / qdrant / LLM deps).
- The consolidated Phase 1+2 live check (fresh OMNIPRO upload/job, not
  Retry) — still outstanding, and it is the precondition for a 3B proof
  on a fresh run, not for 3A.
- The baseline report was generated from `crater-10.db`, a v12-era DB
  (pre-Phase-2 dedupe); re-running the baseline script on a fresh
  Phase 2 DB would change the duplicate counts, not the headline —
  the headline stays 0 until 3B supplies archetypes.

## Verification fix — seed product match (author, 13 Nov 2026, after the builder's report)

During verification a second, independent cause of the Phase 0 "0
transitions, no seed" symptom was found: `load_seed_for_product` never
matched under the app's compile route. The seed file stores model
"OMNIPRO 220"; the products table stores family "OMNIPRO" and model
"220" separately, and `simulation.py` passed only the model — so no
seed ever loaded on a real run (crater-10.db), even though the
combined-form tests passed. The Phase 2 seed proof called
`apply_expert_seed` directly and never exercised the match path.
Fix (3 lines + test): `load_seed_for_product(manufacturer, model,
family=None)` now also matches "family model"; the compile route and
the baseline runner pass the family. New regression test
`test_seed_matches_split_family_and_model` in
`tests/test_phase2_dedupe.py`. Full suite after the fix: 272 passed, 2
failed (same pre-existing), 0 collection errors. Baseline re-run after
the fix: headline unchanged at 0/35 — the seed now matches on
crater-10.db but its rules still skip (7 Feed Tensioners ambiguous;
Wire Spool has no entity), which is exactly the pre-Phase-2 shape the
baseline measures. The baseline report was regenerated. The 3A package
was repackaged with this fix; the consolidated Phase 1+2 live check
should run on the 3A package, which now contains the fix the live
check's "expect 4 transitions" depends on.
