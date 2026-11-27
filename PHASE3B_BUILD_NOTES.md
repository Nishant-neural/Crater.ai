# Phase 3B build notes — archetype engine + compiler v3 (13 Nov 2026)

## Scope (what was built)

- **Archetype library** `backend/simulation/archetypes.py` (v3b-1.0.0): 11
  archetypes; only A2 switch, A3 converter, A4 transmission populated and
  reviewed. The other 8 (A1 source, A5 sensor, A6 controller, A7 load,
  A8 conductor, A9 storage, A10 valve, A11 interface) are empty schema
  entries (ports/states declared, no rows) so binding can honestly say
  "no match" / "bound, no rows yet".
- **Compiler v3** `backend/simulation/compiler.py`: §8 unique-match-on-evidence
  binding (via `binding.py`), one generic conditional transition per
  propagation row (conditions incl. explicit fault, effects incl. next
  state), compiler-generated unknown-setting rows for uncovered input
  combos, reviewed topology links (via `edges.py` +
  `omnipro_edge_list.json`), seed boolean→parameter translation with
  subsumption records, nominal fixpoint stored as `initial_state`,
  lint (conflicts/invalid → quarantine; settle via the real engine),
  topological deterministic ordering (producers before consumers),
  metadata (library version, bindings, coverage, lint, settle, seed
  translation, link records). No engine change, no new LLM calls.
- **Lint** `backend/simulation/lint.py`: table exclusion/totality,
  generated totality rows (greedy minimal cover), whole-twin conflict /
  invalid / ordering checks, settle check via the real engine.
- **RepairAction + ExpectedTrace + FailureToBehaviour**
  `backend/simulation/repair_actions.py` +
  `backend/simulation/repair_actions_omnipro.json` (case-1 4 actions,
  procedures cited from p42/p17/p12 only). Verifier path
  `run_verified_repair_action` in `verification.py` (new path; the legacy
  rule-derived path is untouched). Tournament `verify_single_cause`
  resolves via `fault_injection` first; the legacy name-match path runs
  only where no injection exists.
- **Oracle change**: the 3A Welder O4 exemption is removed — `arc` now
  has a proper port owner (the gun→arc link).
- **Proof twin** `backend/simulation/omnipro_proof.py`: constructed
  canonical OMNIPRO model (9 unique entities, typed ports, human-confirmed
  edges), observable mapping + the 3 case-1 test models. Runner
  `scripts/run_phase3b_oracle.py`.
- **Tests** `tests/test_phase3b_archetypes.py`: 22 tests (tables, lint,
  binding, voltage rule, compiler v3, seed translation, Demo regression,
  `for_steps`, RepairAction, ExpectedTrace, fault_injection wiring,
  oracle case-1 gate).

## Gate results (verified 13 Nov 2026)

- Full suite: **294 passed, 2 failed, 0 collection errors**. The 2 failures
  are the pre-existing `tests/test_phase8a_integration.py` ones from the
  272 baseline (no new failures).
- Demo Motor Drive: **exactly 7 transitions**.
- Oracle on the proof twin: **headline 4/35, O1 gate pass, case 1 = 4/4
  (100%)**, O3 **12/58** (6 case-1 pairs + 6 from other cases' evaluable
  rows). Per-case: case 1 4/4; cases 2–9 0 (their archetypes are not
  populated yet — that is 3C work, not a 3B failure).
- Oracle on crater-10 with compiler v3: **headline 0/35, O1 fail, O3 0/58**,
  gaps topology_gap 35 / observable_unmapped 35 / unmodelled_test 35 /
  unmappable_cause 29 / no_transition_expresses_fault 6, twin 201
  components, 0 transitions — exactly the 3A baseline. The blockers are
  the snapshot's duplicates + 0 ports, not the archetype engine.
- Tournament/verify on the proof twin: all 4 case-1 causes **validated**
  via the RepairAction path (trace shows the required chain); **Wingnut
  validated** via the seed's rule-11 overrides (see deviation 1).
- 0 new runtime LLM calls. Persisted twin untouched (crater-10.db opened
  read-only; the proof twin is in-memory only).
- Reports: `phase3b-oracle-report.json` / `.md` in the goal files folder.

## Gap list, 3A → 3B (proof twin)

| gap | 3A (crater-10) | 3B (proof twin) |
|---|---|---|
| topology_gap | 35 | 16 |
| observable_unmapped | 35 | 31 |
| unmodelled_test | 35 | 31 |
| unmappable_cause | 29 | 15 |
| no_transition_expresses_fault | 6 | 5 |
| o2_mismatch | — (no evaluable rows) | 8 |
| o5_thin_trace | — | 8 |
| o3_indistinguishable | — | 5 |

The new o2/o5/o3 gaps appear only because cases 2–3 rows are now
evaluable (their fault instances resolve to the feeder/gun/spool) but
their archetypes (A9 spool, A8 gun) are not populated — that is the 3C
work list, measured, not hidden.

## Edge list (reviewed, `omnipro_edge_list.json`)

| edge | sources |
|---|---|
| spool → feeder (material) | p10 spool setup; p42 case 2 cause 4 |
| trigger → motor (switch_to_enable) | p17 step 27; p43 case 5 cause 3; p45 schematic |
| motor → feeder (identity) | p42 case 1 premise; p45 schematic |
| feeder → gun (material_to_tip threshold + fault override) | p13 steps 13–14; p42 case 1 cause 3; p42 case 2 cause 1 |
| gun → arc (material_to_arc threshold) | p13 step 14 note; p42 case 1 |

Full Appendix A reference chain: mains → breaker → trigger → relay →
motor → feeder → wire → arc. Case-1 required O5 chain starts at the
motor (Amendments Fix 3); relay/source enter with cases 5/6 (3C). No
edge was invented: an edge not in this list does not exist for the
compiler.

## Deviations from the design (with reasons)

1. **Wingnut is NOT validated from archetype tables in 3B.** The gate
   phrase "Feed Tensioner + Wingnut VALIDATED from archetype tables"
   cannot literally cover the Wingnut: A9 storage is an empty schema
   entry in 3B by the task's own scope rule, so there is no A9
   vocabulary to translate the Wingnut seed rules into. The Wingnut is
   validated through the same verify path from the translated-seed
   overrides (fault rule `seed:wingnut-loose-spool-spins`, recovery
   `seed:wingnut-tight-stops-spool-spin`, symptom
   `wire-spool.spinning_freely`); the Tensioner is validated from the A4
   tables + RepairAction. A9 rows arrive in 3C.
2. **O1 scope clarified (not lowered).** Design v3 §6.3: O1 fails when
   "the twin predicts a symptom with no fault". The 3A implementation
   counted an *unmapped* port as an O1 failure — correct for the 3A
   baseline (nothing mapped) but wrong for 3B, where the twin models
   case 1 only: an unmapped port is a coverage gap, not a hallucination.
   O1 now checks every *mapped* nominal port (a non-nominal read still
   fails it) and fails when *no* nominal port is mapped at all (this
   preserves the 3A baseline exactly: crater-10, 0 mapped, O1 fail).
   Unmapped ports are reported in O1's detail and in the gap list.
3. **Transition ordering is topological, not id-sorted.** The design's
   lint requires "deterministic ordering" without fixing the order. A
   plain id sort puts every archetype row before every link, and the
   engine's `for_steps` streak bookkeeping freezes a condition's streak
   at its first-pass value within a step() call — so a consumer whose
   condition first becomes true mid-step (its producer fired later in
   the same pass) cannot fire until the next step() call, and the
   motor → feeder → wire chain never settles in one step. The compiler
   now orders producers before consumers (Kahn's algorithm, id
   tie-break); the lint verifies the list equals that canonical order.
   The engine is untouched. The demo twin (hand-built, already in
   dependency order) is unaffected.
4. **A3 M-no-power constrains `enable_in=high`.** As written in v3 §4.2
   the row (`power_in=low` → motion low) overlapped M2b/M2c (power low
   + enable absent/low) with different outputs, and the lint
   quarantined it as conflicting. A motor with low power and no enable
   is off (M2b/M2c), not running degraded — degraded running needs the
   enable asserted. The row's note records this.
5. **§8 voltage rule tightened.** `binding.parameter_evidence` counted
   a unit-family match alone as evidence, so an "input voltage"
   quantity recorded on the motor would have counted as motor-rating
   evidence (voltage is the rating parameter's unit family). A family
   match now counts only when the quantity's name does not name a
   different quantity (input / supply / mains / line). The p7 quantity
   is still on `unresolved:entity` in the real snapshot, so this was a
   latent hole, not a live mis-binding. Test added.
6. **3A test twin updated for the O4 change.** `tests/test_phase3a_oracle.py`
   `_twin` gains the gun→welder topology edge: with the Welder exemption
   gone, a fault that flips the arc with no path to the Welder is a
   spurious output — which is exactly what O4 is for. The stub's fault
   transition claims the arc changes, so the stub must declare the edge.
7. **Two latent bugs fixed while testing (both pre-existing in this
   session's 3B code, neither in v12):** `binding.bind_archetype`
   shadowed the `parameter_evidence` function with a local list of the
   same name (binding always raised TypeError); `lint._combo_key`
   sorted tuples mixing bools and strings (TypeError on
   `groove_matches` combos).
8. **M3 streak deferred (as designed).** The converter's stalled →
   overheated streak is not implemented in 3B; no oracle row needs it.
   The engine's `for_steps` mechanism is unit-tested on a hand-built twin.
9. **Case-3 tension-high conflict recorded, not fixed.** `transmission.t3-high`
   (tension high → material_out low, case 1(d)) conflicts with the
   case-3 "excess pressure" expectation (tension high → material_out
   high) for the same injection. One deterministic row cannot satisfy
   both; case 1(d) is the 3B gate. Case 3 is a 3C registry/vocabulary
   decision (distinguish the injection), not a row fix.

## Things NOT verified (need the user / a live run)

- End-to-end ingestion with the user's LLM + Qdrant (no live run was done;
  the proof twin is constructed, not ingested).
- Any live app check (the consolidated Phase 1+2 live check — fresh
  OMNIPRO upload/job — is still outstanding and is the precondition for a
  3B proof on a fresh live snapshot; nothing here replaces it).
- The 3A baseline report files were not touched (the runner writes
  `phase3b-oracle-report.*` only).

## Open items for 3C

- Populate A1/A8/A9/A7/A10 only as oracle rows require them; move the
  gun's wire path from the link fault-override into proper A8 rows
  (re-run the 3B gate: case 1 must stay 100%).
- Resolve the case-3 tension-high conflict (registry/vocabulary decision).
- M800V alarm-observable mapping (≥10 codes, ≥3 families).
- Re-measure the 9-case rate + the 3A gap list.
