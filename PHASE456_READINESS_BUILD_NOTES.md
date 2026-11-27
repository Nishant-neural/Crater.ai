# Readiness follow-up build notes — ChatGPT product-readiness review, items A–F

Date: 14 Nov 2026. Tree: `~/workspace/crater-phase1/v12`. Package:
`~/workspace/crater-phase1/project-review-phase6-readiness-patched.zip`
(NEW file; the Phase 6 and follow-up packages are untouched).
No engine rewrite, no solver, no OCR, no auth/multi-tenancy/Postgres/
workers (hardening is docs only, item F), no Phase 7/8, no plugin, no
reverse propagation, no Modelica/z3/CAD/autonomous control/universal
support/fleet-learning infrastructure. No new runtime LLM calls beyond
the v12 diagnosis budget except the DraftLLM slot (item C, via the
existing gateway only).

## Scope (adopted from the review of the Phase 6 package)

The review reviewed the PHASE 6 package, not the follow-up — its
criticism that the upstream walk was unwired is SUPERSEDED (walk is live
since the follow-up build: POST /diagnose/{id}/upstream-walk, suite 398)
and was not re-done. Baseline reproduced first (pristine tree, `-x` run
before any change: 306 passed, then the known phase8a failure, no other
failure before it; task-stated full baseline 398 passed, 2 pre-existing
failures, 0 collection errors; oracle 20/35, O1 pass, O3 57/58; Demo
Motor Drive 7 transitions; crater-10 0/35). A full-suite re-run started
before the item A edits raced them and was killed as contaminated; the
final full suite below is the post-change gate.

## Item A — Reviewed RepairActions for oracle-passing rows, cases 2–9

**Before:** 4 RepairActions (case 1 only); cases 2–9 returned
available=False with "no reviewed RepairAction yet" (verified by reading
`backend/simulation/repair_actions_omnipro.json` + the filed 3C report).

**After:** 20 RepairActions, all VALIDATED on the proof twin with the
note "trace shows the required chain" (probe + new tests, see below).
Per-case counts (before → after): case 1: 4 → 4; case 2: 0 → 4 (a, d,
e, f); case 3: 0 → 1 (a); case 4: 0 → 4 (b, e, g, h); case 5: 0 → 2
(b, c); case 6: 0 → 3 (a, c, e); case 7: 0 → 0; case 8: 0 → 2 (a, b);
case 9: 0 → 0. The 4 case-1 actions are byte-identical (the generator
script asserts parsed equality).

**Method (case-1 pattern, same review discipline):** procedure refs with
page evidence from the registry fix text (verbatim, p42/p43 + p17 step
27 / p12 / p13 / p7 where the remedy cites them); state_effects restoring
the faulted field to nominal; postconditions verified to differ in the
fault state (measured, `/tmp/probe_repairs.py` output); required_tests
from the vocabulary row; ExpectedTrace required chain taken from the
measured intervention trace (transition ids from the compiled proof
twin), one forbidden guard per action (motor motion_out `absent` for
wire-path cases; gun material_out `absent` for gas cases; welder
power_in `absent` for power cases), terminal conditions (Welder.arc
`normal`, plus Trigger.power_out `normal` for cases 5b/5c, LCD
display_out `lit` for cases 6a/6c/6e, weld_quality `normal` for cases
8a/8b). Preconditions + safety_requirements on every action (lockout
line + one work-specific line, in the case-1 register; safety lines are
work practice, not manual quotes — the manual evidence lives in
procedure_refs).

**Gate:** every new RepairAction cause returns VALIDATED through
`run_verified_repair_action` on the proof twin; oracle headline
unchanged (20/35 — see the re-run below); no-degradation suite (final
count below).

**Rows with NO RepairAction (by design — a RepairAction on a failing row
would certify a repair the twin cannot reproduce):**

- Oracle-failing rows: 2b (liner clogged), 2c (liner too small), 3b (tip
  size), 3c (connector), 3d (liner damaged), 4d (loose cable), 5a
  (thermal trip), 5d (protection active), 6b (outlet unpowered), 6d
  (breaker tripped), 7a (ground), 7b (tip size), 7c (tip worn), 9a (line
  voltage), 9b (cord gauge). 6b/6d pass O2 but fail O3 (indistinguishable
  pair): no action until the O3 gap is resolved, because the diagnosis
  cannot tell the two causes apart.
- Null-injection rows (settings / technique / cross-reference — the twin
  cannot express the fault): 4a (cross-ref to case 1), 4c, 4f, 5e, 7d,
  8c, 8d, 8e, 8f, 9c.

**Tests:** `tests/test_phase456_readiness_repairs.py` (4 tests: per-case
counts + id set; no action on the 25 failing/unexpressible rows; all 16
new actions VALIDATED with the trace note; preconditions/safety on all
20). Two existing tests that pinned the 3B scope (`len(actions) == 4`)
were updated to 20 with the full id set — scope change by design, not a
behaviour change.

## Item B — Oracle-gap fixes (genuinely fixable ones only)

All 15 gaps (13 O2 + 2 O3) re-examined against the filed 3C report, the
vocabulary artifact, and the filed O5 traces (which archetype row fires
for each failing row, and which passing row shares it). **Result: 0
fixed, 15 wont-fix.** No check was weakened; no expected vector was
edited to match the twin; no reverse propagation was implemented
(forward-only by design); the honest A6 gap was not touched.

| Gap | Rows | Verdict + reason |
|---|---|---|
| Back-pressure: `wire_out@feeder` expected `low`, got `normal` | 06, 07, 08, 10, 11 | wont-fix — needs reverse propagation; the engine is forward-only by design. The vocabulary expectation is an inference ("a clogged liner resists the wire, so the feeder slips"), not a manual fact (3C notes). Editing the expected vector to `normal` would be editing expected vectors to match the twin — forbidden. |
| Ground path: `ground_path` expected `poor`, got `good` (also `ground_check`) | 18 | wont-fix — disjunctive cause ("Loose MIG Gun cable or ground cable"): the registry injection models the gun half, the vocabulary expects the ground half; a gun fault cannot set the separate Ground Clamp's field and no reviewed edge links them (a loose gun cable does not make the ground clamp poor). Changing the injection to the ground clamp would misrepresent the gun half. |
| Case-9 arc: `arc` expected `low`, got `unstable` | 23, 24 | wont-fix — the firing A7 row (`load.l-energy-low-material-absent.unstable`) is shared with PASSING row 19 (gun damaged, expects `unstable`); changing it to `low` fixes 23/24 and breaks 19. The arc cannot distinguish low supply voltage from gun damage on its inputs. (Also the feeder→liner threshold low→absent, the documented 3C trade-off.) |
| LCD warning: `lcd` expected `warning`, got `blank` | 26, 29 | wont-fix — the honest A6 gap: the LCD's `warning_in` is an unmodelled controller input; the manual gives no logic map. Do not touch, per the task. |
| Case-7 tip/arc | 36, 37, 38 | wont-fix — same-injection different-symptom conflicts resolved to the majority (passing) reading; each fix breaks a passing row: 36 fires `load.l-ground-low.unstable`, shared with passing row 22 (expects `unstable`); 37 fires `conductor.c-size.wrong-size` (tip `absent`), shared with passing row 06 (expects `absent`); 38 fires `conductor.c-worn.worn` + `load.l-energy-absent-material-low.unstable`, shared with passing row 16 (expects tip `low`, arc `unstable`, tip_size_check `mismatch`). |
| O3 indistinguishable pair | 32, 34 | wont-fix — identical expected vectors by the vocabulary author's own design (row 32 basis: "the remedy points at the breaker/GFCI, so the breaker check reads tripped"; row 34 basis acknowledges the same vector). Distinguishing them needs an input-amperage / panel measurement the twin does not model, and the manual's 6b cause text itself includes a tripped breaker. |

**Gate:** oracle headline must not drop below 20/35 — re-run measured
**20/35, O1 pass, O3 57/58** (identical to the filed 3C report; see
Measured results). No rise was manufactured: zero gaps fixed, zero
vectors edited.

## Item C — Real LLM wired into the DraftLLM slot

- `backend/simulation/drafting.py`: new `gateway_draft_llm(brief)` —
  the existing gateway, task `"drafting"`, `gateway.complete_result`
  with `json_schema=DraftArchetype.model_json_schema()`, temperature
  0.0, `max_tokens=8192` (the diagnosis ceiling; the `"drafting"` task
  has no entry in `settings.model_routes`, so the gateway default would
  be 2000 tokens — too small for a full archetype JSON). No new
  provider, no new dependency, no new API-key handling.
- `draft_archetype(brief, llm=None)` now defaults to `gateway_draft_llm`
  (previously raised `NotImplementedError`); passing `llm` explicitly
  still uses it (tests, other models).
- Fail-closed: no provider configured → `RuntimeError` (never a
  fabricated draft); truncated output → `RuntimeError` (a partial
  archetype must never reach review); unparseable output → `ValueError`
  (one repair retry for trailing commas, mirroring check 1); non-object
  JSON → `ValueError`. The returned dict is schema-validated downstream
  by the pre-review gate (check 1).
- **Tests:** `tests/test_phase456_readiness_drafting.py` (8 tests, all
  with a STUBBED gateway — never a live LLM call): call shape (task,
  schema, temperature, max_tokens), default path, fenced-JSON parsing,
  no-provider / truncated / unparseable / non-object errors, stubbed
  draft passes schema check. The 3D test that pinned
  `NotImplementedError` was replaced by a gateway-stub test asserting
  the call shape.
- **NOT claimed:** no real draft was run. The first REAL draft + the
  15-minute review measurement stays a LIVE CHECK for the user (needs
  his LLM credentials + a chosen first Layer 2 type — still his open
  decision). The supported-scope doc states automatic learning is not an
  advertised capability until a real draft passes review.

## Item D — The 2 pre-existing failing tests (diagnosis + fix)

Both were TEST bugs, not product bugs. Both fixed in the test file;
no product code changed.

1. **`test_pdf_chunk_modalities_reach_universal_persistence`** — the
   test monkeypatched `pipeline.page_to_chunks`,
   `pipeline.extract_chunk_knowledge`, and `pipeline.validate_machine_model`,
   none of which is on the pipeline's path any more (Phase 1 rewrote
   chunking to document-level `pages_to_chunks` and extraction to the
   batch `extract_chunk_knowledge_batch`; per-chunk
   `extract_chunk_knowledge` survives only as the empty-batch retry).
   Fixed by patching the current names (`pages_to_chunks`,
   `extract_chunk_knowledge_batch` returning per-chunk models with
   distinct entities). The expectation also changed, 3 → 2 entities:
   diagram chunks are deliberately excluded from text extraction (the
   pipeline indexes diagram content only after schematic extraction as
   a node/edge summary), and this run disables the schematic pass — so
   text + table reach persistence (2 entities) and the diagram chunk is
   present as a Chunk row contributing no knowledge entity (asserted).
   (`Chunk` rows carry `document_id`, not `revision_id` — the first
   version of the fix asserted on a non-existent column and was
   corrected.)
2. **`test_schematic_projection_creates_entities_ports_and_relations`**
   — the fixture supplied pin-less nodes but asserted 2 ports. The
   projection deliberately creates ports ONLY for real extracted pins
   ("Real pins only" — the old synthetic `"terminal"` port made a
   netlist untrustworthy and hid the fact that no pins were extracted).
   Fixed by giving each node one real pin in the fixture
   (`ExtractedPort(name="inlet"/"outlet")`); the assertions (2 entities,
   2 ports, `connected_to` relation, `schematic` evidence) are unchanged.

Result: `tests/test_phase8a_integration.py` **5 passed** (was 3 passed,
2 failed).

## Item E — Frontend production build

`npm ci && npm run build` in `frontend/` on the current tree: **PASS**.
`npm ci` added 27 packages in 2 s; `vite build` (vite 8.3.0) transformed
33 modules and built in 315 ms: `dist/index.html` 0.50 kB,
`dist/assets/index-CJGoA3_W.js` 323.38 kB, `dist/assets/index-rpSIf0J6.css`
33.19 kB. No fix needed. This closes the review's stale claim (it said the
frontend build was never verified — the Phase 2 notes record a passing
build, compilation only, rendering not verified — but it was never
re-verified on the current tree) with fresh evidence on the current
tree. `node_modules/` and `dist/` were removed before packaging.

## Item F — Docs (no code)

1. **Supported-machine boundary:** new file
   `files/supported-scope-v1.md` in the goal files folder + a short note
   in `plan.md`. OMNIPRO 220, the 9 cases, per-case oracle rates as
   re-measured after items A and B (headline 20/35 = 57.1%, O1 pass, O3
   57/58; case 1 fully verified; cases 2–9 status after item A; cases 7
   and 9 with no reviewed RepairAction), and what is NOT promised (no
   universal simulation, no guaranteed physical repair, no autonomous
   control, no automatic learning until a real draft passes review, 3D
   drafting not operational until the first real draft, no second family,
   no live-integration proof, no production hardening). Every rate
   matches a measured number.
2. **Hardening track spec:** the review's Track C list written into
   `plan.md` as the hardening track definition (auth, authorization,
   multi-tenancy, Postgres, workers, storage, observability,
   reliability, security, deployment). DOCS ONLY — no hardening build.

## Measured results

- Oracle re-run (`scripts/run_phase3c_oracle.py`, out-dir
  `/tmp/oracle-readiness` — the filed 3C report was NOT overwritten):
  headline **20/35 = 57.1%**, O1 pass, O3 **57/58 = 98.3%**; per-case
  4/4, 4/6, 1/4, 4/5, 2/4, 3/5, 0/3, 2/2, 0/2 — byte-identical to the
  filed 3C report except the verify section. Proof twin 18 components /
  648 transitions / 20 edges. Demo Motor Drive **7 transitions**.
  crater-10 **0/35**, O1 fail. Gap list identical (o2_mismatch 13,
  o3_indistinguishable 2, unmodelled_test 2).
- Tournament/verify samples (re-run): case 1 a–d VALIDATED (unchanged);
  **case 2a, 3a, 4b, 6a, 8a now VALIDATED** via the new RepairActions
  with "trace shows the required chain" (were available=False in the
  filed report); case 5a, 7a, 9a still available=False with "no reviewed
  RepairAction yet" (their first causes fail the oracle — correct).
  Wingnut VALIDATED via rule-11 (unchanged).
- RepairAction verify probe (all 20 actions, full contracts):
  **20/20 VALIDATED**, trace note "trace shows the required chain" for
  every one; the 25 failing/unexpressible rows carry no action.
- Full suite: **412 passed, 0 failed, 0 collection errors** (929.69 s,
  15m29s, 329 warnings, all pre-existing deprecation warnings). 412 =
  398 baseline + 12 new tests (4 repairs + 8 drafting); the 2
  pre-existing phase8a failures are fixed by item D and the 1 failure
  found in the first final run (a 3B test whose premise went stale
  under item A — see deviation 6) is fixed. First final run: 411 passed,
  1 failed; after the deviation-6 fix, re-run: 412 passed, 0 failed.

## Files changed (in `~/workspace/crater-phase1/v12`)

- `backend/simulation/repair_actions_omnipro.json` — v2 → v3: 16 new
  reviewed actions (case-1 4 unchanged, asserted by the generator).
- `backend/simulation/drafting.py` — `gateway_draft_llm`,
  `DRAFTING_TASK`/`DRAFTING_MAX_TOKENS`/`DRAFTING_INSTRUCTION`,
  `draft_archetype` defaults to the gateway; module + protocol
  docstrings updated. No other drafting machinery changed.
- `tests/test_phase456_readiness_repairs.py` — new, 4 tests (item A).
- `tests/test_phase456_readiness_drafting.py` — new, 8 tests (item C).
- `tests/test_phase3d_layer2_layer3.py` — the `NotImplementedError`
  test replaced by a gateway-stub test; module docstring updated.
- `tests/test_phase3b_archetypes.py` — count/id-set test updated 4 → 20
  (item A scope change).
- `tests/test_phase5_learning.py` — count assertion updated 4 → 20
  (item A scope change).
- `tests/test_phase8a_integration.py` — both pre-existing failures
  fixed (item D, test-side only; CRLF preserved).
- `PHASE456_READINESS_BUILD_NOTES.md` — this file.
- Goal files (docs only): `files/supported-scope-v1.md` (new),
  `files/plan.md` (readiness result section + hardening track).

## Deviations (with reasons)

1. **Item B fixed 0 of 15 gaps.** Every gap's fix is either a design
   change the task forbids (reverse propagation), the honest A6 gap the
   task forbids touching, or ambiguous / break-a-passing-row (same
   A7/A8 row shared with a passing row; disjunctive cause; identical
   expected vectors by design). Per the task, ambiguous gaps are left
   and recorded wont-fix rather than forced.
2. **The filed 3C oracle report was NOT overwritten.** The re-run wrote
   to `/tmp/oracle-readiness/` so the 3C baseline record survives; the
   re-run's numbers are recorded here and in `plan.md`. (The oracle
   runner's default out-dir is the goal files folder; it was invoked
   with `--out-dir /tmp/oracle-readiness`.)
3. **Baseline full-suite re-run killed as contaminated.** It started
   before the item A edits and raced them (`-x` pristine run: 306
   passed, then the known phase8a failure, no other failure before it).
   The final full suite below is the post-change gate.
4. **`draft_archetype` now calls the gateway by default** instead of
   raising `NotImplementedError`. This changes the 3D test that pinned
   the old behaviour (replaced, see item C). The fail-closed property
   is preserved: no provider → `RuntimeError`, never a fabricated draft.
5. **Item D test 1 asserts 2 entities, not 3.** The diagram chunk is
   deliberately excluded from text extraction by current pipeline design;
   asserting 3 would require changing the product to extract diagram
   placeholders as knowledge, which the pipeline explicitly fixed
   against. The test now asserts the exclusion explicitly.
6. **A 3B test's premise went stale under item A and was fixed after
   the first final run.** `test_cause_with_injection_but_no_action_is_
   unavailable_not_rejected` picked the first case-2 cause with a
   fault_injection (case 2a) and asserted available=False — but item A
   gave 2a a reviewed RepairAction, so it now VALIDATES. The test's
   intent (injection present, action absent → unavailable, not
   rejected) is preserved: it now selects the first case-2 cause with
   an injection and NO reviewed action (2b, liner clogged) and asserts
   the letter is "b" so a future scope change fails loudly instead of
   silently passing on a different cause. First final run: 411 passed,
   1 failed (this test); after the fix, re-run: 412 passed, 0 failed.

## Not run (live checks — need the user)

End-to-end ingestion with the user's LLM + Qdrant; any live app check
(fresh OMNIPRO upload/job, session restore, tournament, verify verdicts,
Machine Model view); a real repair outcome through a new RepairAction
(field YES/NO); a real Mode B cold start end-to-end; first REAL LLM
draft through the gateway slot + the 15-minute review measurement
(needs his LLM credentials + a chosen first Layer 2 type — still his
open decision); validating on unseen real cases (needs pilot cases).
See the final report's live-check list.
