# Phase 3C build notes — archetype population (cases 5–9) + M800V alarm mapping

Date: 13 Nov 2026. Tree: `~/workspace/crater-phase1/v12`. No new runtime LLM calls.
Design: `phase3-design-v3-archetype-oracle.md` (v3 + all three Amendments sections).
Reports: `phase3c-oracle-report.json` / `.md` in the goal files folder.
M800V sample: `m800v-alarm-observable-sample-v1.json` / `.md` in the goal files folder.

## Scope

Populate further archetypes ONLY as oracle rows require them (A1 source + A8
conductor for cases 5–6/9; A9 storage for case 3 spool + case 8 gas bottle;
A7 load for case 7 arc; A10 valve for case 8 gas flow — never speculatively),
plus the M800V alarm-observable mapping on a sampled set. Re-measure the 9-case
oracle rate and the M800V sample rate with per-case/per-family breakdowns;
re-measure the 3A gap list. No targets were set — the rates below are measured.

## Measured rates (proof twin, compiler v3)

| Case | Rate | Notes |
|---|---|---|
| omni-01 wire-not-feeding | 4/4 (100%) | 3B gate still holds |
| omni-02 wire-stops | 4/6 (67%) | 2 fail on back-pressure (feeder) |
| omni-03 birds-nest | 1/4 (25%) | 3 fail on back-pressure (feeder) |
| omni-04 arc-not-stable | 4/5 (80%) | 1 fails on ground_path (documented) |
| omni-05 does-not-function | 2/4 (50%) | 2 fail on lcd=warning (A6 gap) |
| omni-06 lcd-dark | 3/5 (60%) | O2 is 5/5; 2 fail O3 (indistinguishable pair) |
| omni-07 no-arc | 0/3 (0%) | Documented O2 mismatches (see below) |
| omni-08 porosity | 2/2 (100%) | |
| omni-09 weak-arc | 0/2 (0%) | Arc reads unstable vs low (threshold trade-off) |
| **Headline** | **20/35 = 57.1%** | Was 0/35 in the 3A baseline and in the first 3C run |

- O1 nominal gate: **pass** (all 17 mapped observables read nominal with no fault).
- O3 discriminability: **57/58 pairs separated (98.3%)**. The one inseparable pair is
  oracle-row-32 vs oracle-row-34 (case 6: outlet unpowered vs breaker tripped — the
  vocabulary gives them identical expected vectors; see below).
- Proof twin: 18 components, 648 transitions, 20 topology edges, settle True.
  (The 646 figure in an earlier draft of these notes was a miscount; the
  runner's report — `phase3c-oracle-report.md/.json` — records 648.)
- crater-10 (read-only baseline snapshot, compiler v3): **0/35, O1 fail, O3 0/58** —
  exactly the 3A baseline. The blockers are the snapshot's duplicates + 0 ports, not
  the archetype engine.
- Demo Motor Drive: exactly **7 transitions**.
- Tournament/verify: all 4 case-1 causes **validated** via the RepairAction path;
  **Wingnut validated** via the seed's rule-11 overrides. The case 2–9 samples are
  available=False with the reason "no reviewed RepairAction yet" — correct: the
  tournament verifies a REPAIR, and 3C did not write RepairActions for cases 2–9
  (only the 4 case-1 actions exist). The twin can express the fault but not the repair.

## Gap list, 3A → 3C (proof twin)

| Gap | 3A baseline | 3B proof twin | 3C proof twin |
|---|---|---|---|
| topology_gap | 35 | 16 | **0** (required chains rewritten to the 3C topology) |
| observable_unmapped | 35 | 31 | **0** (all 17 ports mapped) |
| unmodelled_test | 35 | 31 | **2** (technique-only tests in case 8; O2 still passes) |
| unmappable_cause | 29 | 15 | **0** (see note) |
| no_transition_expresses_fault | 6 | 5 | **0** |
| o2_mismatch | — | 8 | **13** (all documented, see below) |
| o3_indistinguishable | — | 5 | **2** (rows 32/34) |
| o5_thin_trace | — | 8 | **0** |

Note on unmappable_cause: the 3C gap list no longer reports it because every
twin-expressible row's fault instance now resolves (the proof twin has unique
entities). The 10 null-injection causes (5 settings / 4 technique / 1
cross-reference) are still outside the 35-row denominator, as in 3A.

## The 13 O2 mismatches (all documented, not hidden)

1. **Back-pressure (5 rows: 06, 07, 08, 10, 11).** The vocabulary expects
   `wire_out@feeder=low` when a downstream component (tip/liner/gun) is blocked —
   the basis is the vocabulary author's inference ("a clogged liner resists the
   wire, so the feeder slips"). The twin's propagation is forward-only by design
   (the engine's `step()` propagates downstream); a downstream blockage cannot
   make the upstream feeder read low. Modelling it needs reverse propagation, a
   design change not in 3C scope. The tip and arc are correct for these rows; only
   the feeder port mismatches.
2. **Ground path (1 row: 18, case 4(d)).** The vocabulary expects
   `ground_path=poor`, but the gun's loose fault cannot set the separate Ground
   Clamp component's ground_path. A gun fault cannot reach another component's
   field. Documented.
3. **Case 9 arc (2 rows: 23, 24).** The feeder→liner threshold (below) makes the
   arc read `absent` where the vocabulary expects `low` (a weak arc with the wire
   reaching the tip slowly). The twin cannot distinguish a slipping feeder from a
   slow motor on the feeder's `material_out` alone; the threshold is the majority
   reading (6 rows) and case 9 is the documented minority.
4. **LCD warning (2 rows: 26, 29, case 5(a)/(d)).** The LCD's `warning_in` is an
   unmodelled A6 controller input. The welder's thermal-trip / protection fault
   should raise a warning, but no controller logic map exists in the manual, so the
   controller output stays unknown (the honest A6 gap). The arc, welder_on and
   motor_motion are correct for these rows.
5. **Case 7 tip/arc (3 rows: 36, 37, 38).** Row 36 (ground poor): the twin picks
   `unstable` (the case-4 reading for the same injection); the vocabulary expects
   `absent`. Row 37 (tip size): the twin picks `tip=absent` (the blocked reading,
   matching the majority of the size rows); the vocabulary expects `normal`. Row 38
   (tip worn): the twin picks `tip=low` and `arc=unstable`; the vocabulary expects
   `normal` and `absent`. All three are same-injection-different-symptom conflicts
   resolved to the majority reading, documented in the archetype rows' sources.

## The O3 pair (rows 32/34, case 6)

Oracle-row-32 (outlet unpowered) and oracle-row-34 (breaker tripped) have
identical expected vectors (lcd blank, power_at_unit absent; outlet_power absent,
breaker tripped). The twin reproduces them faithfully, so O2 passes for both but
O3 correctly flags them as indistinguishable. This is a vocabulary limitation (two
causes with identical modelled vectors), not a twin defect. The distinguishing
information would be at the panel (is the breaker tripped, or is the outlet dead
for another reason?) — the vocabulary does not model it.

## The 3C oracle fix (after the first 3C run measured 0/35)

The first 3C oracle run measured headline **0/35** (O1 pass, O3 57/58). The
failures clustered into six fixes, all in the archetypes / edge list / registry /
vocabulary (no engine, compiler or oracle-harness change except the edge list):

1. **Feeder→liner threshold restored** (`omnipro_edge_list.json` v2 → v3). The
   3C v2 note retired the 3B `material_to_tip` threshold on the assumption that
   the A8 rows carry it. The oracle measured that they do not: A8
   `c-material-low` passes a low material input through as low, so the tip read
   `low` and the arc `unstable` where the vocabulary expects `absent` (cases 1/2:
   a low feed means the wire does not reach the tip). The threshold is restored
   as `material_to_liner` on the feeder→liner edge: low→absent, high→absent,
   normal→normal. Sources: p42 case 1, p17 step 27, p42 case 2 cause 2. This
   fixed 6 rows (cases 1(a)/(b)/(d), 2(d)/(e)/(f)) and is the documented
   trade-off for case 9 (above).
2. **A4 motion-low row** (`transmission.t-motion-low.slow`). The A4 had no row for
   `motion_in=low`, so case 9's feeder fell to a generated totality row
   (`material_out=unknown`), which propagated as unknown all the way to the arc.
   A transmission fed slowly feeds slowly: `material_out=low`, still feeding.
3. **A7/A8 double-deviation rows.** A fault on the gun/tip degrades TWO of the
   next component's inputs at once (the fault row sets both `material_out` and
   `energy_out`), so the downstream component saw a combination no
   single-deviation row covers and fell to a generated totality row
   (`unknown`). Added: A7 `l-energy-absent-material-absent` (→absent),
   `l-energy-low-material-absent` (→unstable), `l-energy-absent-material-low`
   (→unstable), `l-energy-low-material-low` (→unstable); A8
   `c-energy-absent-material-absent`, `c-energy-low-material-low`,
   `c-energy-low-material-absent`; plus the triple-deviation rows A7
   `l-material-absent-gas-low` and A8 `c-material-absent-gas-low` (case 3(c):
   no wire + low gas). All are exclusive with the single-deviation rows (each
   constrains the other input to nominal there), so the lint still reports zero
   conflicts.
4. **Case 5(b) vs 9(a): `input_voltage=very_low`.** The same injection
   (`Welder input_voltage=low`) gave two different expected symptoms: case 5(b)
   "Circuit supplies insufficient input voltage" (the welder does not function at
   all) and case 9(a) "Incorrect line voltage" (a weak arc). The registry +
   vocabulary decision distinguishes them (same pattern as the 3C
   `tension=excess` decision): case 5(b) is now `input_voltage=very_low` (new
   value in the electrical domain + A1 row `s-input-very-low.off` →
   `power_out=absent`); case 9(a) stays `low`. This fixed case 5(b) (welder_on,
   lcd and arc all correct now).
5. **Case 9(b) cord_gauge: `wrong` → `wrong_gauge`.** The registry/vocabulary
   fault value was `wrong`, which is not a declared value of the `cord_gauge`
   domain ([ok, wrong_gauge]), so no A1 row matched and the fault run produced
   a zero-length trace. Corrected to `wrong_gauge`.
6. **Required chains rewritten to the 3C topology.** The 3A chains were written
   for the 3B proof twin (direct motor→feeder→gun and cord→trigger→welder
   edges). The 3C topology adds intermediates (liner, tip, button, valve, arc),
   so every row carried a `topology_gap`. Each case's chain is now a path that
   exists in the 3C edge list, with length ≤ the case's minimum fault-run trace
   length: case 1/2 motor→feeder→liner→gun→tip→arc; case 3/4
   feeder→liner→gun→tip→arc; case 5 cord→button→welder→trigger; case 6
   cord→button→welder→lcd; case 7 tip→arc→welder; case 8
   bottle→valve→gun→arc→welder; case 9 cord→button→welder.

After these fixes the lint still reports **zero conflicts, zero invalid** on every
populated archetype (A1 11 rows, A2 9, A3 6, A4 10, A7 14, A8 24, A9 4, A10 5,
A11 6; generated totality rows: source 28, switch 8, converter 14, transmission
34, load 29, conductor 47, storage 7, valve 6, interface 5).

## Archetypes populated in 3C (9 of 11)

A1 source (11 rows), A2 switch (9), A3 converter (6), A4 transmission (10),
A7 load (14), A8 conductor (24), A9 storage (4), A10 valve (5), A11 interface
(6). A5 sensor and A6 controller stay empty schema entries — A6 is the honest
gap (controller outputs stay unknown where the manual gives no logic map), and
no oracle row needs a sensor. Binding on the proof twin: **15/18 bound (83.3%)**
(source 2, converter 1, transmission 1, conductor 4, switch 2, storage 2,
valve 1, load 1, interface 1); unresolved: Wingnut, Feed Tensioner, Welding
Wire (no archetype signature matches — no ports recorded, no bound parameters).

## M800V alarm → observable sample

Source: M800V/M80V Series Alarm Manual, IB-1501808(ENG)-C, 222 pp (READ-ONLY).
Sample: **12 alarm codes across 4 families** — M (Operation Errors), T (Stop
Codes), S (Servo/Spindle Alarms), Z (System Alarms). Artifact:
`m800v-alarm-observable-sample-v1.json` / `.md`.

- **O2 lookup: 12/12 (100%).** Every sampled alarm maps to at least one concrete
  non-nominal observable (not just the alarm code itself).
- **Fault known: 10 known, 1 partial, 1 unknown.** Partial: T01 0102 NC not ready
  (the cause is another alarm). Unknown: Z02 System error (the manual gives no
  cause — "The operation result is illegal. Remedy: Contact the service
  center." — the controller output with no stated logic; the observable is the
  alarm itself, the fault stays unknown).
- **O3 discriminability: 65/66 pairs separated (98.5%).** The one indistinguishable
  pair is M01 0006 vs T01 0105 (both "H/W stroke end axis exists", same observable
  `stroke_end=hw_end` and same remedy). The remedy is recorded as canonical check
  ids as well as free text, because the manual words the same remedy differently
  in the M and T sections — comparing free text would hide the duplicate.
- **Near miss:** M01 0001 vs M01 0002 share the observable
  (`axis_ref_return=failed`) but differ on the remedy/check (dog length / return
  speed vs encoder rotation), so they are separated on the full vector.

This is a lookup mapping, not a twin: no M800V twin exists in 3C, so there is no
oracle run on M800V alarms. The rates are lookup/discriminability rates on the
sampled mapping, not twin-verified rates. An M800V twin would need the same
archetype treatment as OMNIPRO (A6 controller rows only where the manual states
a logic map) plus the MTB manuals, which take precedence over Mitsubishi's.

## Deviations from the design (3C)

1. **The feeder→liner threshold is a link map, not an A8 row.** The 3C v2 edge
   list retired the 3B threshold on the assumption that the A8 rows carry it; the
   oracle measured that they do not (A8 `c-material-low` passes low through).
   The threshold is restored on the feeder→liner edge (`material_to_liner`).
   Reason: the threshold is a property of the wire path (a low feed does not
   enter the liner), not of a single conductor — and the A8 row cannot distinguish
   a slipping feeder from a slow motor, because both present `material_in=low`
   downstream. The link map is the reviewed, per-edge place for it.
2. **`very_low` added to the electrical domain.** The domain now has 5 values
   ([absent, very_low, low, normal, high]). This grows the totality enumeration
   for every electrical port (A1/A2/A3/A7/A8) and makes the proof-twin compile
   slower (~40s CPU, was ~30s). The cost is accepted: the alternative was leaving
   case 5(b) as a documented mismatch, and the `very_low` distinction follows the
   same pattern as the 3C `tension=excess` decision (distinguish the injection
   where the manual gives two different symptoms for it).
3. **Case 2–9 verify samples are unavailable (no RepairAction).** The 3C runner
   samples one twin-expressible cause per case 2–9 through
   `verify_single_cause`. All return available=False with "no reviewed
   RepairAction yet". That is the designed behaviour (the tournament verifies a
   REPAIR; without a reviewed RepairAction there is no repair to apply), not a
   twin defect. 3C did not write RepairActions for cases 2–9. The earlier
   expectation that the samples would "fall back to the single-assertion path"
   was wrong: the single-assertion path is in the verifier
   (`run_verified_repair_action`), not in the tournament's `verify_single_cause`.
4. **Back-pressure is not modelled.** The vocabulary's feeder=low expectation for
   a downstream blockage (rows 06, 07, 08, 10, 11) is an inference, not a manual
   fact, and the twin's propagation is forward-only by design. Reverse
   propagation would be a design change; it is recorded as a gap, not fixed.
5. **3B tests updated for the 3C scope** (`tests/test_phase3b_archetypes.py`):
   `test_only_a2_a3_a4_populated` (now 9 of 11 populated),
   `test_archetype_library_version_pinned` (3c-1.0.0),
   `test_proof_model_binds_a2_a3_a4_and_leaves_the_rest_unbound` (15/18 bound),
   `test_binding_coverage_recorded_on_the_compiled_twin` (18 entities, 15 bound).
   These are scope updates, not behaviour changes.

## Files changed / added in 3C

- `backend/simulation/archetypes.py` — A1/A7/A8/A9/A10/A11 populated (3C); oracle
  fix: electrical domain + `very_low`, A1 `s-input-very-low`, A4 `t-motion-low`,
  A7 double/triple rows, A8 double/triple rows. Version `3c-1.0.0`.
- `backend/simulation/omnipro_edge_list.json` — v2 → v3 (20 edges; feeder→liner
  carries `material_to_liner`; review note updated).
- `backend/simulation/omnipro_proof.py` — 18 entities, 20 relations, 17 observable
  ports, 23 test models (unchanged by the oracle fix).
- `backend/simulation/repair_actions_omnipro.json` — v2 (4 case-1 actions, unchanged).
- `backend/knowledge/repair_cases_omnipro.json` — case 5(b) `very_low`, case 9(b)
  `wrong_gauge` (oracle fix); case 3(a) `excess`, case 6(b) cord `power_in` (3C).
- `scripts/run_phase3c_oracle.py` — proof twin + crater-10 + tournament/verify
  (case 1 + one sample per case 2–9) + Wingnut.
- `tests/test_phase3c_oracle_fix.py` — 8 tests pinning the oracle fixes.
- `tests/test_phase3c_m800v_sample.py` — 4 tests recomputing the M800V rates.
- Goal files folder: `oracle_vocabulary_v1.json` (required chains, 5(b) `very_low`,
  9(b) `wrong_gauge`, review note), `phase3c-oracle-report.json` / `.md`,
  `m800v-alarm-observable-sample-v1.json` / `.md`.

## Full suite (final, after the 3C tests landed)

- **306 passed, 2 failed, 0 collection errors** (308 tests total, 526 s).
- The 2 failures are the pre-existing `test_phase8a_integration.py` ones
  (`test_pdf_chunk_modalities_reach_universal_persistence`,
  `test_schematic_projection_creates_entities_ports_and_relations`) — the
  same 2 that failed at the 272 baseline. No new failures.
- The 12 new 3C tests (8 in `test_phase3c_oracle_fix.py`, 4 in
  `test_phase3c_m800v_sample.py`) all pass. The 22 3B tests pass (re-run
  separately: 22 passed in 470 s — slow because each compiles the proof
  twin at ~40 s CPU).

## Things NOT verified (need the user / a live run)

- End-to-end ingestion with the user's LLM + Qdrant (no live run was done; the
  proof twin is constructed, not ingested).
- Any live app check. The consolidated Phase 1+2 live check (fresh OMNIPRO
  upload/job) is still outstanding and is the precondition for a 3C proof on a
  fresh live snapshot; nothing here replaces it.
- The M800V rates are lookup/discriminability rates on a 12-alarm sample, not
  twin-verified rates (no M800V twin exists).
