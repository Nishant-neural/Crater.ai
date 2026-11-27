# Miller Twin Step 1 — Mapping Notes (MANUAL DATA WORK, not code)

**Product:** Miller Electric Millermatic 252 — Owner's Manual OM-230 693C (2007-11), 52 pp
(`crater-benchmarks/welding/miller-millermatic-252/owners-manual-om-230-693c.pdf`).
**Oracle:** `crater-benchmarks/evaluations/c002b-miller.json` (oracle-vocabulary-miller-v1,
38 twin-expressible rows of 63) + `miller_parser_rows.json` (63 rows).
**Twin under test:** `miller 220 — Rev A Simulation`, id `a7a67556-495f-488e-8742-1040b7397229`
(product `01c956e5-84e5-484c-a280-ee9e15c348a0`), 96 components, **1 transition**
(`rule:unit-overheat`: `entity:unit.overheated` → `entity:thermistor.open`).
**Reference shape copied:** OMNIPRO proof data — `golden_rules/omnipro_wire_feed.json`,
`omnipro_edge_list.json` (v3), `repair_actions_omnipro.json`, `backend/simulation/oracle.py`
(O1–O5) and `backend/simulation/omnipro_proof.py` (`observable_paths`, `build_test_models`).

**Page convention:** printed page numbers are used throughout (the manual's own pagination).
The oracle's `source_pages` are PDF pages; printed = PDF − 4 (e.g. oracle p35 = printed p31).

**Method.** Every entry traces to a manual section/page read for this mapping — 6-5 (p31),
6-2/6-3/6-4 (pp29–30), 4-4/4-5 (p13), 4-11 (p17), 4-12 (p18), 4-13 (p19), 4-14 (p20),
5-1/5-2/5-3 (pp24–26), 8-1 (p36), 8-8–8-15 (pp41–43), 8-17 (pp44–45), Section 9-1 (p46),
and Figure 7-1 Circuit Diagram (pp34–35), which has no extractable text and was read visually
from the rendered PDF pages. Faults, expected observables, expected tests and remedies are
copied **verbatim** from the oracle rows (verified programmatically: 0 mismatches on all 38).
Component resolution uses the oracle's own rule — unique normalized-name match against the
compiled twin (same as `oracle.resolve_component` / `expert_rules.py`); ambiguous or duplicate
matches are skipped and named below, never merged. No twin code was touched; nothing was
recompiled. The builder script (`build.py` in this directory) re-validates every component id
against the twin and fails loudly on any mismatch.

## Headline (honest baseline)

- **Fault injections: 38/38 rows covered.** 27/38 fault components resolve to a unique twin
  component; 11/38 name no twin component (listed below). **0/38 are expressible against the
  current twin**: the only transition responds to `entity:unit.overheated`, and no oracle fault
  targets `Unit`. The seed step must add one transition per faulted component; this mapping says
  which component each row needs one for.
- **Observable paths: 10 ports.** 7 have a reviewed *target* path (status `pending_seed` — the
  component resolves uniquely but the field does not exist yet in the compiled twin, so each
  reads `None` against twin `a7a67556` today). 3 are **unmodelled explicitly**: `meter_display`
  (HL.P codes), `weld_quality`, `arc`. Against the current twin the oracle's O1 gate therefore
  fails with `observable_unmapped` on every port. That is the recorded baseline, not a defect in
  this mapping — the seed step is what changes it.
- **Edges: 28**, all Miller endpoints, none copied from the OMNIPRO list. Required O5 chains are
  proposed for 11 of 17 cases (the other 6 have no chain: see below).
- **Repair actions: 38/38**, covering 11 cases (miller-01–07, 14–17 — the cases that have
  twin-expressible rows). 27 restore a twin state; 11 carry `state_effects: null` with the reason.
  No archetype transition ids are invented anywhere (`transition_id: null`, `pending_archetype`).

## File-by-file source

### `observable_paths.json` — 10 ports, 7 target paths, 3 unmodelled
| Port | Target path | Manual source |
|---|---|---|
| `weld_output` | `entity:millermatic-252.weld_output` (pending_seed) | 6-5 p31; 5-2 p26 (voltmeter shows actual weld voltage when an arc is established) |
| `wire_out@tip` | `entity:contact-tip.material_out` (pending_seed) | 4-14 p20; 8-17 p44 — same override as OMNIPRO |
| `wire_out@feeder` | `entity:wire-feed-rolls.material_out` (pending_seed) | 8-17 p44 (feeder low / feeder high); 6-4 p30 |
| `motor_motion` | `entity:drive-motor.motion_out` (pending_seed) | 8-17 p44 symptom premise; Fig 7-1 p34 (motor M to PC1) |
| `fan` | `entity:fan-motor.motion_out` (pending_seed) | 6-5 p31 "Fan motor does not run"; 6-2 p29; Fig 7-1 p34 (FM across T1 primary) |
| `wire_speed` | `entity:wire-feed-rolls.wire_speed` (pending_seed) | 6-5 p31; 5-2 p26 (meter displays preset wire feed speed, IPM) |
| `gas_flow@gun` | `entity:mig-gun.gas_out` (pending_seed) | 8-1 p36; 8-17 p44 — same override as OMNIPRO |
| `meter_display` | **unmodelled** | 6-5 p31 (HL.P codes); 5-2 p26; Fig 7-1 pp34–35 (meters on PC2) |
| `weld_quality` | **unmodelled** | 8-8–8-15 pp41–43 |
| `arc` | **unmodelled** | 8-17 p45; 5-2 p26 |

No per-row override is required (checked all 38 rows; `row_overrides: {}`). The file also carries
`observable_components` (port owner per port, for the oracle's O4 check).

### `fault_injections.json` — 38/38 rows
Each row: oracle `fault` / expected observables / expected tests verbatim, plus
`registry_injection` in the `{instance, parameter, value}` / `{instance, fault_state}` shape
(`oracle.fault_injection_to_fault` is its inverse), plus `twin_resolution` and
`expressible_against_current_twin: false` with the reason.

### `edges.json` — 28 edges
Power chain (receptacle → cord → disconnect → power switch → unit → meters/motor/fan),
wire chain (spool → rolls → wire guide → cable liner → gun → tip, motor → rolls),
weld-current chain (unit → weld output terminal → drive housing → gun → tip),
ground return (unit → work cable → work clamp → workpiece),
gas chain (cylinder → regulator/flowmeter → wire feeder → solenoid → gun → nozzle),
fan chain (circuit → fan motor → cooling fan).
Value maps: `identity`, `switch_to_enable` (fan circuit), `material_to_liner` (OMNIPRO v3
threshold, re-sourced: miller-44/45/46 low never reaches the tip; miller-48 high piles up at
the feeder). Required O5 chains proposed for miller-01–07, 14–17; miller-08–13 have none
(their observable is `weld_quality`, which has no twin component to chain through).

### `repair_actions.json` — 38/38 actions
Remedy quotes are verbatim manual text (verified: 0 mismatches against the parser remedies).
`state_effects` restore the faulted field to nominal for 27 rows; 11 rows are `null` (below).
`expected_trace` transition ids are `null` throughout — no archetype transitions exist yet for
any Miller component. The 25 settings/technique rows have no repair action here (no fault
injection; their remedies live in the oracle's held-out `answer_key`).

## Rows I could NOT map (11/38 fault components unmapped)

| Rows | Oracle fault component | Why (manual-grounded) |
|---|---|---|
| miller-02 | Building Circuit Breaker | No twin component. 4-12/4-13: the building fuse/breaker is the site's over-current protection for the dedicated branch circuit, external to the unit. The twin's `disconnect box` is the disconnect device, not the building breaker. |
| miller-03, miller-06 | Trigger (loose / shorted) | No twin component of this name at all (all 96 checked). The manual names the trigger throughout (4-6 Gun Trigger Plug p13; 5-1/5-3; Fig 7-1 p35 RC1 1 TRIG / 2 TRIG (RTN)), but ingestion produced no Trigger entity. |
| miller-04, 08, 11, 14, 21 | Main Control Board | No twin component. Figure 7-1 (pp34–35) names it PROCESS CONTROL BOARD PC1; the twin has no PC1/board component. miller-11 also names the main rectifier, likewise absent. miller-04/11 carry HL.P 001/003 as the fault value — the code itself, not a diagnosis (the manual never defines the codes). |
| miller-12, miller-58 | Input Power | No twin component of this name. Closest names (`input power cord`, `welding power source`, `Wire Feeder/Power Source`) are different components and are not merged on name alone. |
| miller-55 | Gas Hose | No twin component of this name. The twin has only `hoses` (generic plural); 8-17 p44 names the gas hose between flowmeter and wire feeder and the gas hose in the gun and cable assembly. |

## Ports I could NOT map (3/10 unmodelled)

- **`meter_display` (HL.P codes)** — rows miller-01–11. Three independent reasons: the twin's
  Voltmeter and Wire Feed Speed Meter carry no state field; the meters live on the User Interface
  Display Board PC2 (Figure 7-1), which is not a twin component; and the manual never defines
  what HL.P 001/002/003/004 mean. A meter-code field cannot be seeded honestly until a code table
  is sourced; the manual has none.
- **`weld_quality`** — rows miller-26, miller-28 (twin-expressible; plus settings/technique rows).
  No Welding Arc / weld-bead component exists in the twin. Adding a weld-quality load is a
  modelling decision for review, not a mapping.
- **`arc`** — rows miller-59, miller-60, miller-62, miller-63. No Welding Arc component exists
  in the twin (unlike OMNIPRO). The weld-current chain therefore ends at the contact tip and
  the ground return ends at the workpiece.

## Warnings / ambiguities (skipped, not guessed)

- **Liner duplicate.** The twin carries both `cable liner` and `gun liner`. The manual uses
  "cable liner" when it sizes the liner (8-17 miller-47/49/60) and bare "liner" elsewhere (6-5
  p31; 8-17 miller-51); the oracle splits liner faults between the two (miller-19/51 Gun Liner;
  miller-49/60 Cable Liner). Edges use `cable liner` only. Do NOT merge them on name alone —
  the reviewer must decide whether they are one part or two.
- **Welding Wire ambiguous.** Two twin components normalise to `welding wire`
  (`entity:welding-wire-extends-from-nozzle-feed-system`,
  `entity:welding-wire-extends-out-of-nozzle`) — the twin's only normalized-name duplicate.
  No edge uses a Welding Wire node.
- **Unit duplicates.** `Millermatic 252`, `Wire Feeder/Power Source`, `welding power source`,
  `Unit`, `Welding Unit` all denote the unit. Edges use Millermatic 252 as root, except the
  two gas edges that use `Wire Feeder/Power Source` because 8-17 names the wire feeder as the
  gas-hose endpoint. If the reviewer merges them, re-point those two edges to the root.
- **Inlet Wire Guide ambiguous.** Two components, `Inlet Wire Guide (056 192)` / `(056 193)`
  (Section 9-1 p46, different wire sizes). No edge uses either; the wire-guide node is the
  generic `Wire Guide` from 6-4 p30.
- **Thermistor mismatch (miller-05).** The twin's only transition is wired to
  `entity:thermistor` ("Thermistor", `open=true`) and `entity:unit`, while the oracle fault
  names "Thermistor T" (`entity:thermistor-t`). 6-2 p29 says "Thermistor T in SCR1 protects the
  unit", and Figure 7-1 p34 shows RT1 in the SCR module — the transition names the wrong one
  of the twin's two thermistor components. Review item; not fixed here (no code changes in
  this step).
- **Drive Roll variants.** Generic `Drive Roll` plus five part-numbered Drive Rolls (053 695,
  053 697, 053 700, 087 130, 203 526 — Section 9-1 p46) are the interchangeable parts (6-3
  p29); the feeder node is `Wire Feed Rolls` (8-17 wording). No edge uses a Drive Roll.
- **Disconnect box** vs Line Disconnect Switch (same 4-13 device); **terminal hardware**
  (Weld Cable Terminal, Copper Bar, gun weld cable — 4-4 p13) are hardware on the weld-output
  path, not flow nodes; **accessory guns** (Spoolmatic/XR/Spool Gun) are not the scored path.
- **Old twin quarantine.** The compiled twin's metadata quarantined `edge:gun-to-tip` and
  `edge:gun-to-tip-energy` ("source port not declared on MIG Gun") because the twin declares
  no ports at all (canonical entities have `ports: []`). Both edges are re-proposed here with
  manual sources; the quarantine is a property of the compiled twin, not of the manual.
- **Out of oracle scope: 6-5 p32.** Section 6-5 continues on printed p32 with a second table
  ("No wire feed.", 10 remedies including HL.P 005 "Wire feed malfunction. Check wire feed
  delivery system."). The oracle's 63 parser rows do not cover p32, so nothing is mapped for
  it here; that is oracle work, not twin work.
- **miller-58 grouping.** As printed, the 8-17 table groups "Incorrect primary voltage
  connected to welding power source" under "Wire feeds, but no gas flows." (its Problem cell
  is empty). Surprising but what the PDF prints; recorded as printed (oracle flag).

## Open questions for the reviewer

1. **miller-53 / gas nozzle vs gun gas_out.** `gas_flow@gun` reads the MIG Gun's `gas_out`
   (upstream of the nozzle). A plugged nozzle must drive that port absent via the seed rule.
   Alternative: a per-row override reading the port at the gas nozzle for miller-53. I did NOT
   take the override (the port is qualified @gun); decide in review.
2. **Liner: one part or two?** (`cable liner` vs `gun liner` — see warnings.) This decides
   whether miller-19/51 and miller-49/60 share one seed rule set or two.
3. **Unit merge?** (`Millermatic 252` vs `Wire Feeder/Power Source` etc.) This decides whether
   the two gas edges get re-pointed to the root.
4. **Thermistor T vs Thermistor.** The existing `rule:unit-overheat` transition names
   `entity:thermistor`; the manual and oracle name Thermistor T. Confirm which twin component
   the thermal seed rules should use.
5. **New components needed before any row can score:** Trigger, Main Control Board (PC1),
   Welding Arc (for `arc` + `weld_quality`), meter-code field / PC2 (for `meter_display`),
   Gas Hose, Input Power / Building Circuit Breaker. Adding them is a modelling decision, not
   part of this mapping.

## What was NOT done (per task rules)

- No `backend/simulation/` code touched; the twin was not recompiled; no seed rules written.
- No mapping inferred from a component name alone; every edge/injection/action cites the
  manual section/page it was read from.
- No LLM was used to invent a mapping. (The oracle JSON and parser JSON were used as the
  verbatim source for faults/observables/remedies, cross-checked against the manual pages.)

## Files in this directory (copy into `~/workspace/crater-refactor/families/miller/`)

- `observable_paths.json` — 10 ports (7 pending_seed target paths, 3 unmodelled), incl. `observable_components` for O4
- `fault_injections.json` — 38/38 rows (27 resolve unique, 11 unmapped, 0 expressible today)
- `edges.json` — 28 edges, `required_chains` for 11/17 cases, `value_maps`, `warnings`
- `repair_actions.json` — 38/38 actions (27 with state effects, 11 null), all 17 cases' top rows
- `build.py` — the builder + validator that produced the four JSON files (re-run to reproduce)
