"""Build Miller twin Step 1 mapping files from the reviewed oracle + the
compiled Miller twin. Manual data work: every mapping below traces to a
manual section/page read from owners-manual-om-230-693c.pdf (OM-230 693C)
or to the oracle JSON that quotes it. Nothing is LLM-inferred.

Run: python3 build.py   -> writes observable_paths.json,
fault_injections.json, edges.json into OUT_DIR and validates them.
"""
import json, re, sqlite3
from pathlib import Path

WS = Path("/home/hatch/workspace")
OUT = Path("/tmp/crater-miller-mapping")
ORACLE = WS / "crater-benchmarks/evaluations/c002b-miller.json"
PARSER = WS / "crater-benchmarks/evaluations/miller_parser_rows.json"
DB = WS / "crater-phase1/mock-llm/crater-test.db"
TWIN_ID = "a7a67556-495f-488e-8742-1040b7397229"
PRODUCT_ID = "01c956e5-84e5-484c-a280-ee9e15c348a0"
MANUAL = "Owner's Manual OM-230 693C (2007-11), 52 pp, owners-manual-om-230-693c.pdf"

oracle = json.loads(ORACLE.read_text())
parser = {r["row_id"]: r for r in json.loads(PARSER.read_text())}
te_rows = [r for r in oracle["rows"] if r["category"] == "twin_expressible"]
assert len(te_rows) == 38, len(te_rows)

db = sqlite3.connect(DB)
twin_def = json.loads(db.execute(
    "SELECT definition FROM digital_twins WHERE id=?", (TWIN_ID,)).fetchone()[0])
twin_by_id = {c["id"]: c for c in twin_def["components"]}


def norm(s):
    return re.sub(r"\s+", " ", str(s or "").replace("\u2019", "'").replace("\u2018", "'")).strip().rstrip(".").strip().lower()


by_name = {}
for c in twin_def["components"]:
    by_name.setdefault(norm(c["name"]), []).append(c)

# --- verified fault-component resolution (unique normalized-name match) ---
# Hand-checked against the 96 twin components. None = no component of that
# name in the twin (verified, not assumed).
RESOLUTION = {
    "Line Disconnect Switch": "entity:line-disconnect-switch",
    "Building Circuit Breaker": None,
    "Trigger": None,
    "Main Control Board": None,
    "Thermistor T": "entity:thermistor-t",
    "Power Switch": "entity:power-switch",
    "Work Clamp": "entity:work-clamp",
    "Contact Tip": "entity:contact-tip",
    "Input Power": None,
    "Wire Feed Rolls": "entity:wire-feed-rolls",
    "Gun Liner": "entity:gun-liner",
    "MIG Gun": "entity:mig-gun",
    "Wire Spool": "entity:wire-spool",
    "Cable Liner": "entity:cable-liner",
    "Gas Cylinder": "entity:gas-cylinder",
    "Gas Nozzle": "entity:gas-nozzle",
    "Gas Hose": None,
    "Gas Solenoid Valve": "entity:gas-solenoid-valve",
    "Fan-On-Demand Circuit": "entity:fan-on-demand-circuit",
}
RESOLUTION_WHY = {
    "Building Circuit Breaker": "No twin component of this name. Manual 4-12/4-13: the building fuse / circuit breaker is the site's over-current protection for the dedicated branch circuit, external to the unit; the twin's 'disconnect box' is the disconnect device, not the building breaker.",
    "Trigger": "No twin component of this name at all (checked all 96). The manual names the gun trigger throughout (4-6 Gun Trigger Plug, p13; 5-1/5-3; Fig 7-1 p35 RC1 1 TRIG / 2 TRIG (RTN)), but ingestion produced no Trigger entity.",
    "Main Control Board": "No twin component of this name. Figure 7-1 (pp34-35) names it PROCESS CONTROL BOARD PC1; the twin has no PC1 / board component. The main rectifier named in miller-11 is likewise absent.",
    "Input Power": "No twin component of this name. Closest names ('input power cord', 'welding power source', 'Wire Feeder/Power Source') are different components and are not merged on name alone.",
    "Gas Hose": "No twin component of this name. The twin has only 'hoses' (plural, generic); 8-17 p44 names the gas hose between flowmeter and wire feeder and the gas hose in the gun and cable assembly.",
}
for name, cid in RESOLUTION.items():
    cands = by_name.get(norm(name), [])
    if cid is None:
        assert not cands, f"{name} unexpectedly present: {cands}"
    else:
        assert len(cands) == 1 and cands[0]["id"] == cid, f"{name}: {cands}"
# the twin's only normalized-name duplicate (matters for Welding Wire edges)
dups = {k: [c["id"] for c in v] for k, v in by_name.items() if len(v) > 1}
assert dups == {"welding wire": ["entity:welding-wire-extends-from-nozzle-feed-system",
                                  "entity:welding-wire-extends-out-of-nozzle"]}, dups

# The compiled twin's only transition (verified from the twin definition).
ONLY_TRANSITION = twin_def["transitions"]
assert len(ONLY_TRANSITION) == 1 and ONLY_TRANSITION[0]["id"] == "rule:unit-overheat"
assert list(ONLY_TRANSITION[0]["conditions"]) == ["entity:unit.overheated"]

# Nominal (healthy) value of each faulted field, for repair state_effects.
NOMINAL = {
    ("Line Disconnect Switch", "state"): "on",
    ("Wire Feed Rolls", "groove_matches"): True,
    ("Wire Feed Rolls", "tension"): "normal",
    ("Wire Spool", "brake_pressure"): "normal",
    ("MIG Gun", "connector_seated"): True,
    ("MIG Gun", "gas_flow"): "normal",
    ("Cable Liner", "size_matches"): True,
}

# =====================================================================
# 1. observable_paths.json
# =====================================================================
# status: "pending_seed" = component resolves uniquely in the twin, but
# the named field does not exist yet in the compiled twin (only 4 state
# fields exist in the whole 96-component twin: inverter.power,
# shielding-gas-cylinders.valve, Thermistor.open, Unit.overheated).
# These are REVIEWED TARGET paths for the seed step, not a claim about
# the current twin: against twin a7a67556 every one of them reads None
# today. "unmodelled" = no twin component/field can carry the port.
PORTS = {
    "weld_output": {
        "component": "Millermatic 252", "component_id": "entity:millermatic-252",
        "field": "weld_output", "status": "pending_seed",
        "owner_for_o4": "Millermatic 252",
        "sources": ["6-5 Troubleshooting, p31: 'No weld output' / 'Low weld output'",
                    "5-2 Voltmeter And Wire Feed Speed Meter Operation, p26: voltmeter displays actual weld voltage when an arc is established"],
        "note": "Machine-root output. The twin has no Welding Arc component, so weld output is read at the root (the OMNIPRO 'arc' port was likewise carried by the machine root via the arc->welder link).",
    },
    "wire_out@tip": {
        "component": "contact tip", "component_id": "entity:contact-tip",
        "field": "material_out", "status": "pending_seed",
        "owner_for_o4": "contact tip",
        "sources": ["4-14 Threading Welding Wire, p20: 'Press gun trigger until wire comes out of gun'",
                    "8-17 Troubleshooting Guide, p44: 'Check size of contact tip and cable liner'"],
        "note": "Same port-qualified override as OMNIPRO (wire_out@tip is the Contact Tip's material_out, not the gun's).",
    },
    "wire_out@feeder": {
        "component": "Wire Feed Rolls", "component_id": "entity:wire-feed-rolls",
        "field": "material_out", "status": "pending_seed",
        "owner_for_o4": "Wire Feed Rolls",
        "sources": ["8-17 Troubleshooting Guide, p44: 'Wire feed motor operates, but wire does not feed' (feeder output low) and 'Wire curling up in front of the wire feed rolls' (feeder output high)",
                    "6-4 Aligning Drive Rolls and Wire Guide, p30: drive roll groove lines up with wire guide"],
        "note": "OMNIPRO read this port at the Wire Feed Mechanism; Miller's equivalent node is the Wire Feed Rolls (the component 8-17 names). The twin's generic 'Drive Roll' and part-numbered Drive Rolls (Section 9-1, p46) are the interchangeable parts, not the feeder node, and are not used here.",
    },
    "motor_motion": {
        "component": "Drive Motor", "component_id": "entity:drive-motor",
        "field": "motion_out", "status": "pending_seed",
        "owner_for_o4": "Drive Motor",
        "sources": ["8-17 Troubleshooting Guide, p44: symptom premise 'Wire feed motor operates, but wire does not feed' separates motor motion from wire out",
                    "Figure 7-1 Circuit Diagram, p34: drive motor M wired to Process Control Board PC1 (RC2-5/RC2-9)"],
        "note": "OMNIPRO's motor port was Wire Feed Motor.motion_out; the Miller twin's motor component is named Drive Motor.",
    },
    "fan": {
        "component": "Fan Motor", "component_id": "entity:fan-motor",
        "field": "motion_out", "status": "pending_seed",
        "owner_for_o4": "Fan Motor",
        "sources": ["6-5 Troubleshooting, p31: 'Fan motor does not run'",
                    "6-2 Unit Overload, p29: 'wait for unit to cool allowing fan motor to run'",
                    "Figure 7-1 Circuit Diagram, p34: fan motor FM wired across the T1 primary"],
        "note": "The trouble names the fan MOTOR, so the observable reads the motor, not the Cooling Fan (the driven load, downstream of the motor).",
    },
    "wire_speed": {
        "component": "Wire Feed Rolls", "component_id": "entity:wire-feed-rolls",
        "field": "wire_speed", "status": "pending_seed",
        "owner_for_o4": "Wire Feed Rolls",
        "sources": ["6-5 Troubleshooting, p31: 'Low, high, or erratic wire speed'",
                    "5-2 Voltmeter And Wire Feed Speed Meter Operation, p26: wire feed speed meter displays preset wire feed speed (IPM)"],
        "note": "Actual wire speed at the feeder (the causes are roll pressure/size, liner, panel settings). Field name 'wire_speed' is the port's own base name; it is a target field for the seed step, not an existing twin field.",
    },
    "gas_flow@gun": {
        "component": "MIG Gun", "component_id": "entity:mig-gun",
        "field": "gas_out", "status": "pending_seed",
        "owner_for_o4": "MIG Gun",
        "sources": ["8-1 Typical MIG Process Connections, p36: Shielding Gas -> Regulator/Flowmeter -> Wire Feeder/Power Source -> Gun",
                    "8-17 Troubleshooting Guide, p44: 'Wire feeds, but no gas flows'"],
        "note": "Same port-qualified override as OMNIPRO (gas_flow@gun is the MIG Gun's gas_out). The gas nozzle is downstream of the gun (edge:gun-to-nozzle-gas); a plugged nozzle must drive this port absent via the seed rule — see the miller-53 open question in MAPPING_NOTES.md.",
    },
    "meter_display": {
        "component": None, "component_id": None, "field": None, "status": "unmodelled",
        "owner_for_o4": None,
        "sources": ["6-5 Troubleshooting, p31: HL.P 001 / 002 / 003 / 004 appear on the meters",
                    "5-2 Voltmeter And Wire Feed Speed Meter Operation, p26: both meters display 888 at power up, then preset values",
                    "Figure 7-1 Circuit Diagram, pp34-35: meters live on the USER INTERFACE DISPLAY BOARD PC2"],
        "note": "UNMODELLED, explicitly. Three independent reasons: (1) the twin has Voltmeter and Wire Feed Speed Meter components but neither carries a state field; (2) the meters live on the User Interface Display Board PC2 (Figure 7-1), which is not a twin component; (3) the manual never defines what HL.P 001/002/003/004 mean — the fault value is the meter code itself (oracle flags on miller-04/11). Rows carrying this port: miller-01..11 (values 'normal' or the HL.P code). A meter-code field cannot be seeded honestly until the code table is sourced; the manual has none.",
    },
    "weld_quality": {
        "component": None, "component_id": None, "field": None, "status": "unmodelled",
        "owner_for_o4": None,
        "sources": ["8-8..8-15 Troubleshooting, pp41-43: Excessive Spatter, Porosity, Excessive Penetration, Lack Of Penetration, Incomplete Fusion, Burn-Through, Waviness Of Bead, Distortion"],
        "note": "UNMODELLED, explicitly. Weld quality (spatter / porosity / excess_penetration / lack_of_penetration / incomplete_fusion / burn_through / wavy / distorted) is a property of the weld bead / arc. The twin has no Welding Arc or weld-bead component (checked all 96 names). Twin-expressible rows carrying this port: miller-26, miller-28. A weld-quality load would have to be added as a new twin component; that is a modelling decision for review, not a mapping.",
    },
    "arc": {
        "component": None, "component_id": None, "field": None, "status": "unmodelled",
        "owner_for_o4": None,
        "sources": ["8-17 Troubleshooting Guide, p45: 'Welding arc not stable'",
                    "5-2 Voltmeter And Wire Feed Speed Meter Operation, p26: voltmeter displays actual weld voltage when a welding arc is established"],
        "note": "UNMODELLED, explicitly. The twin has no Welding Arc component (checked all 96 names); unlike OMNIPRO there is no arc load to carry the port. Twin-expressible rows carrying this port: miller-59, miller-60, miller-62, miller-63 (values 'normal' / 'unstable').",
    },
}
# per-row port overrides: checked all 38 rows — no row needs a different
# owner for the same port. The one candidate considered (miller-53,
# gas_flow@gun read at the gas nozzle) was rejected: the port is qualified
# @gun and the gun assembly's gas_out (through the nozzle) is the measured
# point per 8-17 'no gas flows'; recorded as an open question instead.
ROW_OVERRIDES = {}

observable_paths = {
    "version": "miller-observable-paths-v1",
    "product": {"manufacturer": "Miller Electric", "model": "Millermatic 252",
                "manual": MANUAL},
    "twin": {"id": TWIN_ID, "name": "miller 220 — Rev A Simulation",
             "components": len(twin_def["components"]),
             "transitions": len(twin_def["transitions"]),
             "note": "Compiled twin in crater-test.db (product 01c956e5-84e5-484c-a280-ee9e15c348a0). "
                     "Only 4 state fields exist in the whole twin (inverter.power, shielding-gas-cylinders.valve, "
                     "Thermistor.open, Unit.overheated); the canonical model declares no ports at all."},
    "note": "REVIEWED TARGET mapping, not a claim about the current twin. "
            "7 of 10 ports have a reviewed target path (status pending_seed: the component resolves uniquely "
            "but the field does not exist yet in the compiled twin, so each reads None against twin "
            "a7a67556 today). 3 ports are unmodelled explicitly (meter_display, weld_quality, arc). "
            "Against the current twin the oracle's O1 gate therefore fails with observable_unmapped on "
            "every port; that is the honest baseline this step records, and the seed step is what changes it. "
            "Printed page numbers are used throughout (oracle source_pages are PDF pages; printed = PDF - 4).",
    "observable_paths": {p: f"{d['component_id']}.{d['field']}" for p, d in PORTS.items()
                         if d["status"] == "pending_seed"},
    "port_detail": PORTS,
    "unmodelled": {p: d["note"] for p, d in PORTS.items() if d["status"] == "unmodelled"},
    "row_overrides": ROW_OVERRIDES,
    "row_overrides_note": "No per-row override is required: no row needs a different owner for the same port. "
                          "Candidate considered and rejected: miller-53 gas_flow@gun at the gas nozzle — see gas_flow@gun note.",
    "observable_components": {p: d["owner_for_o4"] for p, d in PORTS.items()},
    "observable_components_note": "Port owner per port, for the oracle's O4 check (oracle.py reads "
                                  "case.observable_components). Null where the owner component does not exist in the twin.",
    "coverage": {"ports_total": len(PORTS),
                 "target_paths_pending_seed": sum(1 for d in PORTS.values() if d["status"] == "pending_seed"),
                 "unmodelled": sum(1 for d in PORTS.values() if d["status"] == "unmodelled"),
                 "rows_carrying_unmodelled_ports": sorted({r["row_id"] for r in te_rows
                     for p in r["observables"] if PORTS[p]["status"] == "unmodelled"})},
}
(OUT / "observable_paths.json").write_text(json.dumps(observable_paths, indent=2, sort_keys=True) + "\n")

# =====================================================================
# 2. fault_injections.json — one reviewed injection per twin-expressible row
# =====================================================================
# Registry shape (oracle.load_fault_injections / fault_injection_to_fault):
#   {instance, parameter, value} -> parameter fault; {instance, fault_state}
#   -> fault-state fault. "fault" below is the oracle's own {component,
#   field, value, kind} copied verbatim; "registry_injection" is its
#   translation into that registry shape (None where the component is
#   unmapped — there is no instance to inject into).
# Evaluability per oracle.run_oracle: a row is evaluable only if (a) the
# fault component resolves by unique name AND (b) some transition has a
# condition on that component. The twin's only transition is
# rule:unit-overheat (conditions entity:unit.overheated), and no oracle
# fault targets Unit — so 0/38 rows are expressible against the current
# twin. That is recorded per row, not hidden.
fault_injections = {
    "version": "miller-fault-injections-v1",
    "product": {"manufacturer": "Miller Electric", "model": "Millermatic 252",
                "manual": MANUAL},
    "oracle": "crater-benchmarks/evaluations/c002b-miller.json (oracle-vocabulary-miller-v1, 38 twin-expressible rows)",
    "twin": {"id": TWIN_ID, "components": len(twin_def["components"]),
             "transitions": [t["id"] for t in twin_def["transitions"]],
             "only_transition_conditions": ONLY_TRANSITION[0]["conditions"]},
    "note": "One reviewed injection per twin-expressible oracle row (38/38). "
            "Fault, expected observables and expected tests are copied verbatim from the oracle row. "
            "twin_resolution is checked by unique normalized-name match against the compiled twin (same rule as "
            "oracle.resolve_component / expert_rules.py). 27/38 resolve to a unique twin component; 11/38 name no "
            "twin component. 0/38 are expressible: no transition in this twin responds to any faulted component "
            "(the only transition responds to entity:unit.overheated). The seed step must add a transition per "
            "faulted component before any row can score; this file says WHICH component each row needs one for.",
    "coverage": {"rows_total": 38, "injections": 38,
                 "component_resolves_unique": 0, "component_unmapped": 0,
                 "expressible_against_current_twin": 0},
    "rows": {},
}
for r in te_rows:
    fault = r["fault"]
    comp = fault["component"]
    cid = RESOLUTION[comp]
    if cid is None:
        fault_injections["coverage"]["component_unmapped"] += 1
        resolution = {"status": "unmapped_no_component",
                      "component_id": None,
                      "reason": RESOLUTION_WHY[comp],
                      "oracle_flag_note": "The oracle row already flags this as a service-agent check or "
                                          "unmappable-at-run-time cause; see the row's flags."}
        registry_injection = None
    else:
        fault_injections["coverage"]["component_resolves_unique"] += 1
        resolution = {"status": "resolves_unique",
                      "component_id": cid,
                      "twin_name": twin_by_id[cid]["name"],
                      "reason": None}
        if fault["kind"] == "parameter":
            registry_injection = {"instance": comp, "parameter": fault["field"],
                                  "value": fault["value"]}
        else:
            registry_injection = {"instance": comp, "fault_state": fault["value"]}
    expressible_note = (
        "No transition in this twin has a condition on this component; "
        "the only transition (rule:unit-overheat) conditions on entity:unit.overheated. "
        "Seed rule needed for this component before the row can score.")
    if r["row_id"] == "miller-05":
        expressible_note += (" Special case: the twin DOES have a thermal transition, but it is wired to "
                             "entity:thermistor ('Thermistor', open=true) and entity:unit, while this row's fault "
                             "component is entity:thermistor-t ('Thermistor T'). 6-2 p29 says 'Thermistor T in SCR1 "
                             "protects the unit', and Figure 7-1 p34 shows RT1 in the SCR module — the transition "
                             "names the wrong one of the twin's two thermistor components. Review item; not fixed here.")
    fault_injections["rows"][r["row_id"]] = {
        "row_id": r["row_id"],
        "case_id": r["case_id"],
        "symptom": r["symptom"],
        "cause": r["cause"],
        "fault": fault,
        "registry_injection": registry_injection,
        "expected_symptom": r["observables"],
        "expected_tests": r["tests"],
        "basis": r["basis"],
        "flags": r["flags"],
        "twin_resolution": resolution,
        "expressible_against_current_twin": False,
        "expressible_note": expressible_note,
    }
assert len(fault_injections["rows"]) == 38
assert fault_injections["coverage"]["component_resolves_unique"] == 27, fault_injections["coverage"]
assert fault_injections["coverage"]["component_unmapped"] == 11, fault_injections["coverage"]
(OUT / "fault_injections.json").write_text(json.dumps(fault_injections, indent=2, sort_keys=True) + "\n")

# =====================================================================
# 3. edges.json — reviewed topology edges for the Miller family
# =====================================================================
# Shape mirrors omnipro_edge_list.json (v3). Every edge traces to a manual
# section/page read from OM-230 693C (text, or Figure 7-1 read visually
# from the rendered PDF pages). No edge is copied from the OMNIPRO list:
# Miller endpoints are Miller twin components. Ports follow the OMNIPRO
# convention (power_in/power_out, energy_in/energy_out, material_in/
# material_out, gas_in/gas_out, fluid_in/fluid_out, motion_in/motion_out,
# enable_in). The compiled twin declares no ports at all (canonical
# entities have ports=[]), so source/target ports are the REVIEWED target
# ports for the seed step — the old compiled twin quarantined
# edge:gun-to-tip and edge:gun-to-tip-energy for exactly this reason.
def E(eid, src, sport, tgt, tport, vmap, cases, sources, note):
    assert src in twin_by_id and tgt in twin_by_id, (eid, src, tgt)
    return {"id": eid, "source_component": twin_by_id[src]["name"], "source_id": src,
            "source_port": sport, "target_component": twin_by_id[tgt]["name"],
            "target_id": tgt, "target_port": tport, "value_map": vmap,
            "cases": cases, "sources": sources, "note": note}

EDGES = [
    E("edge:receptacle-to-cord", "entity:receptacle-outlet", "power_out",
      "entity:input-power-cord", "power_in", "identity",
      ["miller-01", "miller-02"],
      ["4-13 Selecting A Location And Connecting Input Power, p19: items 2 Plug (NEMA Type 6-50P), 3 Receptacle [NEMA Type 6-50R], 4 Input Power Cord; 'Connect plug to receptacle if hard wiring is not used'"],
      "Site receptacle supplies the input power cord."),
    E("edge:cord-to-disconnect", "entity:input-power-cord", "power_out",
      "entity:line-disconnect-switch", "power_in", "identity",
      ["miller-01"],
      ["4-13 Selecting A Location And Connecting Input Power, p19: items 4 Input Power Cord, 7 Disconnect Device, 9 Disconnect Device Line Terminals; 'Connect input conductors L1 and L2 to disconnect device line terminals'",
       "6-5 Troubleshooting, p31: 'Be sure line disconnect switch is On (see Section 4-13)'"],
      "Cord feeds the line disconnect switch. The twin's 'disconnect box' is the same disconnect device's housing and is NOT used as the node (see warnings)."),
    E("edge:disconnect-to-power-switch", "entity:line-disconnect-switch", "power_out",
      "entity:power-switch", "power_in", "identity",
      ["miller-01", "miller-07"],
      ["4-13 Selecting A Location And Connecting Input Power, p19: 'Remove lockout/tagout device, and place switch in the On position' (the disconnect switch)",
       "Figure 7-1 Circuit Diagram, p34: S1 is the first switch after the input (WHT/BLK), feeding the TE1 jumper links and the T1 primary"],
      "Series order read from the 4-13 procedure sequence plus Figure 7-1 (S1 first). The twin has no transformer/rectifier components between the switches, so no intermediate node is claimed."),
    E("edge:power-switch-to-unit", "entity:power-switch", "power_out",
      "entity:millermatic-252", "power_in", "identity",
      ["miller-01", "miller-07"],
      ["5-2 Voltmeter And Wire Feed Speed Meter Operation, p26: 'Both meters display 888 at unit power up' (after the power switch is turned on)",
       "6-5 Troubleshooting, p31: 'Turn power switch off and back on, if HL.P 001 appears on meters again...'"],
      "The power switch (S1 in Figure 7-1) energises the unit."),
    E("edge:unit-to-voltmeter", "entity:millermatic-252", "power_out",
      "entity:voltmeter", "power_in", "identity",
      ["miller-01", "miller-02"],
      ["5-2 Voltmeter And Wire Feed Speed Meter Operation, p26: 'Both meters display 888 at unit power up'"],
      "Mirrors the OMNIPRO welder->lcd edge: no unit power, no meter display."),
    E("edge:unit-to-wire-feed-speed-meter", "entity:millermatic-252", "power_out",
      "entity:wire-feed-speed-meter", "power_in", "identity",
      ["miller-01", "miller-02", "miller-05"],
      ["5-2 Voltmeter And Wire Feed Speed Meter Operation, p26: 'Both meters display 888 at unit power up'; 'The wire feed speed meter always displays preset wire feed speed (IPM)'"],
      "Mirrors the OMNIPRO welder->lcd edge."),
    E("edge:unit-to-drive-motor", "entity:millermatic-252", "power_out",
      "entity:drive-motor", "power_in", "identity",
      ["miller-01", "miller-05", "miller-14", "miller-15"],
      ["8-17 Troubleshooting Guide, p44: 'Wire feed motor operates, but wire does not feed' (the motor runs on unit power)",
       "Figure 7-1 Circuit Diagram, p34: drive motor M wired to Process Control Board PC1 (RC2-5/RC2-9)"],
      "Unit power feeds the drive (wire feed) motor; the board drives it. No separate board node exists in the twin (PC1 is absent)."),
    E("edge:unit-to-fan-motor", "entity:millermatic-252", "power_out",
      "entity:fan-motor", "power_in", "identity",
      ["miller-04", "miller-15"],
      ["6-2 Unit Overload, p29: 'wait for unit to cool allowing fan motor to run'",
       "Figure 7-1 Circuit Diagram, p34: fan motor FM wired across the T1 primary"],
      "Fan motor runs on unit power."),
    E("edge:fan-circuit-to-fan-motor", "entity:fan-on-demand-circuit", "power_out",
      "entity:fan-motor", "enable_in", "switch_to_enable",
      ["miller-04", "miller-15"],
      ["6-5 Troubleshooting, p31: 'Fan motor does not run. Have Factory Authorized Service Agent check fan-on-demand circuit.'"],
      "The fan-on-demand circuit enables the fan motor. Procedure-sourced: Figure 7-1 labels FM but no separate fan-on-demand block is identifiable in the diagram, so the enable relation rests on the 6-5 remedy alone."),
    E("edge:fan-motor-to-cooling-fan", "entity:fan-motor", "motion_out",
      "entity:cooling-fan", "motion_in", "identity",
      ["miller-04"],
      ["6-2 Unit Overload, p29: 'wait for unit to cool allowing the fan to run' / 'allowing fan motor to run' (fan and fan motor named together)"],
      "The fan motor drives the cooling fan."),
    E("edge:spool-to-rolls", "entity:wire-spool", "material_out",
      "entity:wire-feed-rolls", "material_in", "identity",
      ["miller-05", "miller-14", "miller-15"],
      ["4-14 Threading Welding Wire, p20: items 1 Wire Spool, 2 Welding Wire, 5 Drive Roll — wire is pulled from the spool through the rolls",
       "8-17 Troubleshooting Guide, p44: 'Wire spool brake pressure too high' starves the feed"],
      "The spool supplies wire to the feed rolls."),
    E("edge:motor-to-rolls", "entity:drive-motor", "motion_out",
      "entity:wire-feed-rolls", "motion_in", "identity",
      ["miller-05", "miller-14", "miller-15"],
      ["8-17 Troubleshooting Guide, p44: symptom premise 'Wire feed motor operates, but wire does not feed' — the motor drives the feed; the symptom is a transmission failure, not a motor failure",
       "6-4 Aligning Drive Rolls and Wire Guide, p30: item 5 Drive Gear drives the rolls"],
      "First link of the wire-feed chain (cf. OMNIPRO motor->feeder)."),
    E("edge:rolls-to-wire-guide", "entity:wire-feed-rolls", "material_out",
      "entity:wire-guide", "material_in", "identity",
      ["miller-05", "miller-14", "miller-15", "miller-17"],
      ["6-4 Aligning Drive Rolls and Wire Guide, p30: 'Insert screwdriver, and turn screw in or out until drive roll groove lines up with wire guide'"],
      "Wire leaves the rolls through the wire guide."),
    E("edge:wire-guide-to-liner", "entity:wire-guide", "material_out",
      "entity:cable-liner", "material_in", "material_to_liner",
      ["miller-05", "miller-14", "miller-15", "miller-17"],
      ["4-14 Threading Welding Wire, p20: 'Push wire thru guides into gun'",
       "8-17 Troubleshooting Guide, p44: 'Check size of contact tip and cable liner, replace if necessary'"],
      "Wire enters the cable liner from the guide. Carries the material_to_liner threshold (see value_map_notes): a low or high feeder output does not enter the liner — low = the wire slips and never reaches the tip (miller-44/45/46), high = excess pressure piles the wire up at the feeder (miller-48 bird nesting). The liner node is 'cable liner', not 'gun liner' — see the liner-duplicate warning."),
    E("edge:liner-to-gun", "entity:cable-liner", "material_out",
      "entity:mig-gun", "material_in", "identity",
      ["miller-05", "miller-14", "miller-15", "miller-17"],
      ["4-14 Threading Welding Wire, p20: 'Push wire thru guides into gun'",
       "6-5 Troubleshooting, p31: 'Replace inlet guide, contact tip, and/or liner if necessary'"],
      "The liner (inside the gun cable) delivers wire to the MIG Gun."),
    E("edge:gun-to-tip", "entity:mig-gun", "material_out",
      "entity:contact-tip", "material_in", "identity",
      ["miller-01", "miller-02", "miller-14", "miller-15", "miller-17"],
      ["4-14 Threading Welding Wire, p20: 'Press gun trigger until wire comes out of gun. Reinstall contact tip and nozzle'",
       "8-17 Troubleshooting Guide, p44: 'Check size of contact tip and cable liner'"],
      "The gun delivers wire to the contact tip. RE-PROPOSED here: the compiled twin's metadata quarantined this edge ('source port material_out is not declared on MIG Gun') because the twin declares no ports; the quarantine is a property of the compiled twin, not of the manual."),
    E("edge:unit-to-weld-output-terminal", "entity:millermatic-252", "energy_out",
      "entity:weld-output-terminal", "energy_in", "identity",
      ["miller-01", "miller-02", "miller-03"],
      ["4-4 Connecting To Weld Output Terminals, p13: 'Slide weld cable terminal onto weld output terminal and secure with nut so that weld cable terminal is tight against copper bar'",
       "Figure 7-1 Circuit Diagram, p35: wire 46 POS is the weld output line"],
      "Unit weld output reaches the weld output terminal."),
    E("edge:weld-output-terminal-to-drive-housing", "entity:weld-output-terminal", "energy_out",
      "entity:drive-housing", "energy_in", "identity",
      ["miller-01", "miller-02", "miller-03", "miller-15"],
      ["Figure 7-1 Circuit Diagram, p35: wire 46 POS terminates at MOTOR DRIVE HOUSING"],
      "The diagram's POS weld output connects to the motor drive housing (not to a separate cable node). The weld cable terminal + copper bar (4-4 p13) are the terminal hardware at this same node."),
    E("edge:drive-housing-to-gun-energy", "entity:drive-housing", "energy_out",
      "entity:mig-gun", "energy_in", "identity",
      ["miller-01", "miller-02", "miller-17"],
      ["Figure 7-1 Circuit Diagram, p35: wire 46 POS -> MOTOR DRIVE HOUSING",
       "8-17 Troubleshooting Guide, p44: 'Gun end not inserted into drive housing properly' — the gun seats in the drive housing"],
      "The gun receives weld current through its seating in the drive housing (the diagram's POS connection)."),
    E("edge:gun-to-tip-energy", "entity:mig-gun", "energy_out",
      "entity:contact-tip", "energy_in", "identity",
      ["miller-02", "miller-17"],
      ["6-5 Troubleshooting, p31: 'No Weld Output; wire feeds. Replace contact tip' — the tip conducts the welding current",
       "4-14 Threading Welding Wire, p20: contact tip reinstalled on the gun before welding"],
      "The gun carries welding current to the contact tip. RE-PROPOSED here: the compiled twin's metadata quarantined this edge too ('source port energy_out is not declared on MIG Gun')."),
    E("edge:unit-to-work-cable", "entity:millermatic-252", "energy_out",
      "entity:work-cable", "energy_in", "identity",
      ["miller-02"],
      ["4-5 Installing Work Cable And Clamp, p13: item 3 Negative (-) Output Terminal — 'Connect cable to terminal and cover connection with boot'",
       "Figure 7-1 Circuit Diagram, p35: wire 43 NEG runs to the WORK CLAMP"],
      "The work cable connects the unit's negative output terminal to the clamp (the arc return path)."),
    E("edge:work-cable-to-work-clamp", "entity:work-cable", "energy_out",
      "entity:work-clamp", "energy_in", "identity",
      ["miller-02"],
      ["4-5 Installing Work Cable And Clamp, p13 (procedure title and steps)",
       "8-1 Typical MIG Process Connections, p36: Work Clamp in the process diagram"],
      "Work cable to work clamp."),
    E("edge:work-clamp-to-workpiece", "entity:work-clamp", "energy_out",
      "entity:workpiece", "energy_in", "identity",
      ["miller-02"],
      ["8-1 Typical MIG Process Connections, p36: Work Clamp -> Workpiece in the process diagram",
       "6-5 Troubleshooting, p31: 'Connect work clamp to get good metal to metal contact'"],
      "The clamp is the return path to the workpiece. No workpiece->arc edge exists: the twin has no arc component (see warnings)."),
    E("edge:cylinder-to-regulator", "entity:gas-cylinder", "supply_out",
      "entity:regulator-flowmeter", "fluid_in", "identity",
      ["miller-06", "miller-07", "miller-16"],
      ["8-1 Typical MIG Process Connections, p36: Shielding Gas -> Regulator/Flowmeter",
       "8-17 Troubleshooting Guide, p44: 'Open gas valve at cylinder and adjust flow rate'"],
      "The gas cylinder supplies the regulator/flowmeter."),
    E("edge:regulator-to-feeder", "entity:regulator-flowmeter", "fluid_out",
      "entity:wire-feeder-power-source", "gas_in", "identity",
      ["miller-16"],
      ["8-17 Troubleshooting Guide, p44: 'Check gas hose between flowmeter and wire feeder'"],
      "Gas hose from the flowmeter to the wire feeder. NOTE: 'Wire Feeder/Power Source' is the 8-1 diagram's label for the unit's feeder box; it duplicates the Millermatic 252 root (see warnings). The edge uses the diagram's label because 8-17 names the wire feeder as the hose endpoint."),
    E("edge:feeder-to-solenoid", "entity:wire-feeder-power-source", "gas_out",
      "entity:gas-solenoid-valve", "fluid_in", "identity",
      ["miller-16"],
      ["5-3 Jog Mode, p26: 'gas will shut off after 1 minute' — the unit shuts the shielding gas",
       "Figure 7-1 Circuit Diagram, p34: gas solenoid GS1 wired to Process Control Board PC1 (wires 9/10, RC2-4/RC2-8)"],
      "The solenoid is the feeder's gas valve, board-driven. In-series position read from the 8-17 hose endpoint + 5-3 gas shutoff + Figure 7-1 GS1; the diagram is electrical only and shows no gas hoses, so this edge is procedure-sourced."),
    E("edge:solenoid-to-gun", "entity:gas-solenoid-valve", "fluid_out",
      "entity:mig-gun", "gas_in", "identity",
      ["miller-06", "miller-07", "miller-16"],
      ["8-17 Troubleshooting Guide, p44: 'Gas solenoid valve not operating' -> 'Wire feeds, but no gas flows'",
       "5-3 Jog Mode, p26: 'shielding gas output on MIG gun'"],
      "The solenoid admits shielding gas to the gun."),
    E("edge:gun-to-nozzle-gas", "entity:mig-gun", "gas_out",
      "entity:gas-nozzle", "gas_in", "identity",
      ["miller-07", "miller-16"],
      ["4-14 Threading Welding Wire, p20: 'Remove gun nozzle and contact tip'",
       "8-9 Troubleshooting - Porosity, p41: 'Remove spatter from gun nozzle' (a gas-flow corrective action)",
       "8-17 Troubleshooting Guide, p44: 'Gas nozzle plugged. Clean or replace gas nozzle.'"],
      "Gas leaves the gun through the nozzle. The gas_flow@gun observable reads the gun's gas_out (upstream of the nozzle), so a plugged nozzle must drive that port absent via the seed rule — see the miller-53 open question in MAPPING_NOTES.md."),
]

# Required chains (O5) per case, proposed for the Test 1/1b run prep:
# each is a consecutive run of the edges above. The oracle ships
# required_chain: [] for Miller with a note that it is defined at run prep
# against the twin's topology — this is that definition, reviewed here.
REQUIRED_CHAINS = {
    "miller-01": ["Line Disconnect Switch", "Power Switch", "Millermatic 252", "Drive Motor",
                  "Wire Feed Rolls", "Wire Guide", "cable liner", "MIG Gun", "contact tip"],
    "miller-02": ["Millermatic 252", "Weld Output Terminal", "drive housing", "MIG Gun", "contact tip"],
    "miller-03": ["receptacle outlet", "input power cord", "Line Disconnect Switch", "Power Switch", "Millermatic 252"],
    "miller-04": ["Fan-On-Demand Circuit", "Fan Motor", "Cooling Fan"],
    "miller-05": ["Drive Motor", "Wire Feed Rolls", "Wire Guide", "cable liner", "MIG Gun", "contact tip"],
    "miller-06": ["gas cylinder", "Regulator/Flowmeter", "Wire Feeder/Power Source", "Gas Solenoid Valve", "MIG Gun", "gas nozzle"],
    "miller-07": ["gas cylinder", "Regulator/Flowmeter", "Wire Feeder/Power Source", "Gas Solenoid Valve", "MIG Gun", "gas nozzle"],
    "miller-08": [], "miller-09": [], "miller-10": [], "miller-11": [], "miller-12": [], "miller-13": [],
    "miller-14": ["Drive Motor", "Wire Feed Rolls", "Wire Guide", "cable liner", "MIG Gun", "contact tip"],
    "miller-15": ["Drive Motor", "Wire Feed Rolls", "Wire Guide", "cable liner", "MIG Gun", "contact tip"],
    "miller-16": ["gas cylinder", "Regulator/Flowmeter", "Wire Feeder/Power Source", "Gas Solenoid Valve", "MIG Gun", "gas nozzle"],
    "miller-17": ["Millermatic 252", "Weld Output Terminal", "drive housing", "MIG Gun", "contact tip"],
}
# verify every consecutive pair in every chain is a declared edge
edge_pairs = {(e["source_component"], e["target_component"]) for e in EDGES}
# chains use twin display names; normalise via the edge endpoints
for case, chain in REQUIRED_CHAINS.items():
    for a, b in zip(chain, chain[1:]):
        assert (a, b) in edge_pairs, (case, a, b)

edges_doc = {
    "version": "miller-edge-list-v1",
    "product": {"manufacturer": "Miller Electric", "model": "Millermatic 252",
                "manual": MANUAL},
    "twin": {"id": TWIN_ID, "components": len(twin_def["components"])},
    "status": "reviewed-edge-list",
    "review": {"status": "draft-for-review", "reviewer": None, "reviewed_at": None,
               "note": "Hand-confirmed edges: each edge is recorded here with its manual source (procedure page, "
                       "process diagram 8-1, or Figure 7-1 circuit diagram read visually from the rendered PDF). "
                       "Invent NO edges: an edge not in this list does not exist for the compiler. "
                       "28 edges — power chain, wire chain, weld-current chain (to the tip; no arc node exists), "
                       "ground return (to the workpiece; no arc node exists), gas chain, fan chain, meter power. "
                       "Nothing is copied from the OMNIPRO edge list; where a value_map or port convention matches "
                       "OMNIPRO it is noted as such and re-sourced to the Miller manual."},
    "scope_note": "The twin has no Welding Arc component, so the weld-current chain ends at the contact tip and "
                  "the ground return ends at the workpiece; the arc itself (and weld_quality) cannot be edged. "
                  "The twin declares no ports at all, so source/target ports are reviewed TARGET ports for the "
                  "seed step. The compiled twin's metadata quarantined edge:gun-to-tip and edge:gun-to-tip-energy "
                  "for undeclared ports; both are re-proposed here with their manual sources.",
    "edges": EDGES,
    "value_maps": {
        "identity": {"absent": "absent", "high": "high", "low": "low", "normal": "normal", "unknown": "unknown"},
        "switch_to_enable": {"absent": "absent", "low": "low", "normal": "high", "unknown": "unknown"},
        "material_to_liner": {"absent": "absent", "low": "absent", "normal": "normal", "high": "absent", "unknown": "unknown"},
    },
    "value_map_notes": {
        "switch_to_enable": "As OMNIPRO v3: a closed switch passes power (electrical normal); the motor's enable input is a signal (high = asserted).",
        "material_to_liner": "As OMNIPRO v3 (restored threshold): a low or high feeder output does not reach the liner. Low = the wire slips / feeds too little to advance (miller-44/45/46: feeder low, tip absent); high = excess pressure piles the wire up at the feeder (miller-48 bird nesting: feeder high, tip absent). Only a normal feed enters the liner.",
    },
    "required_chains": REQUIRED_CHAINS,
    "required_chains_note": "Proposed O5 required chains for the Test 1/1b run prep (the oracle ships required_chain: [] "
                            "for Miller). miller-08..miller-13 have no chain: their observable is weld_quality, which has "
                            "no twin component to chain through. miller-02's chain is the weld-current path; its ground-return "
                            "path (Millermatic 252 -> work cable -> work clamp -> workpiece) is a secondary chain, not the required one. "
                            "miller-17's chain is the weld-current path; its wire-slip causes (miller-59/60) follow the miller-14 wire chain.",
    "warnings": [
        "LINER DUPLICATE: the twin carries both 'cable liner' (entity:cable-liner) and 'gun liner' (entity:gun-liner). The manual uses 'cable liner' when it sizes the liner (8-17 miller-47/49/60) and bare 'liner' elsewhere (6-5 p31; 8-17 miller-51 'Dirty or damaged (kinked) liner'); the oracle splits liner faults between the two (miller-19/51 Gun Liner; miller-49/60 Cable Liner). Edges use 'cable liner' only. Do NOT merge the two on name alone (standing rule) — the reviewer must decide whether they are one part or two.",
        "WELDING WIRE AMBIGUOUS: two twin components normalise to 'welding wire' (entity:welding-wire-extends-from-nozzle-feed-system, entity:welding-wire-extends-out-of-nozzle). No edge uses a Welding Wire node; the wire is carried by the material flow itself.",
        "UNIT DUPLICATES: 'Millermatic 252', 'Wire Feeder/Power Source', 'welding power source', 'Unit' and 'Welding Unit' all denote the unit. Edges use Millermatic 252 as the root, except edge:regulator-to-feeder / edge:feeder-to-solenoid, which use 'Wire Feeder/Power Source' because 8-17 names the wire feeder as the gas-hose endpoint and 8-1 labels the box that way. If the reviewer merges them, re-point those two edges to the root.",
        "INLET WIRE GUIDE AMBIGUOUS: two twin components, 'Inlet Wire Guide (056 192)' and '(056 193)' (Section 9-1, p46: different wire sizes). No edge uses either; the wire-guide node is the generic 'Wire Guide' from 6-4 p30.",
        "DISCONNECT BOX: 'disconnect box' and 'Line Disconnect Switch' both cover the 4-13 disconnect device. Edges use the Line Disconnect Switch (the name the oracle's fault uses).",
        "DRIVE ROLL VARIANTS: generic 'Drive Roll' plus five part-numbered Drive Rolls (053 695, 053 697, 053 700, 087 130, 203 526 — Section 9-1, p46) are the interchangeable parts (6-3 p29); the feeder node is 'Wire Feed Rolls' (8-17 wording). No edge uses a Drive Roll component.",
        "NO ARC NODE: no Welding Arc component exists in the twin, so no tip->arc, gun->arc-gas, ground->arc or workpiece->arc edge can be written. Weld output is read at the Millermatic 252 root instead.",
        "NO TRIGGER NODE: no Trigger component exists in the twin (Figure 7-1 p35 shows RC1 1 TRIG / 2 TRIG (RTN) lines to PC1; 4-6 p13 names the Gun Trigger Plug). No trigger edge exists; miller-03 and miller-06 cannot be edged.",
        "NO BOARD NODE: the main control board is PROCESS CONTROL BOARD PC1 (Figure 7-1, pp34-35); the meters live on USER INTERFACE DISPLAY BOARD PC2. Neither is a twin component, so no board edges exist; miller-04/08/11/14/21 cannot be edged. The main rectifier (miller-11) and transformer T1 / SCR module SR1 are likewise absent (the twin's 'inverter' is not T1 — do not conflate).",
        "NO JUMPER-LINK NODE: the input voltage jumper links (TE1 in Figure 7-1 p34; 4-11 p17) have no twin component. miller-13 and miller-20 are settings rows and are not scored.",
        "GAS HOSE: no 'Gas Hose' twin component ('hoses' only). The gas-hose segments named in miller-55 (flowmeter-to-feeder; in the gun and cable assembly) are carried by edge:regulator-to-feeder and the gun/cable assembly edges, not by a hose node.",
        "TERMINAL HARDWARE: 'Weld Cable Terminal', 'Copper Bar' (4-4 p13) and 'gun weld cable' are hardware on the weld-output path, not separate flow nodes; no edges use them. The weld-output path runs unit -> Weld Output Terminal -> drive housing -> gun.",
        "ACCESSORY GUNS: Spoolmatic 15A/30A, XR Aluma-Pro, XR Edge, XR-A, XR-A Python and Spool Gun components are accessory guns (4-6/4-7); the scored path uses the MIG Gun only.",
        "FAN-ON-DEMAND CIRCUIT: Figure 7-1 labels the fan motor FM but no separate fan-on-demand block is identifiable in the diagram; edge:fan-circuit-to-fan-motor rests on the 6-5 remedy alone (flagged on the edge).",
        "OUT OF ORACLE SCOPE: 6-5 continues on printed p32 with a second table ('No wire feed.', 10 remedies including HL.P 005). The oracle's 63 parser rows do not cover p32, so no edges, injections or repair actions are written for it here; that is oracle work, not twin work.",
    ],
}
(OUT / "edges.json").write_text(json.dumps(edges_doc, indent=2, sort_keys=True) + "\n")

# =====================================================================
# 4. repair_actions.json — reviewed repair actions for the top Miller cases
# =====================================================================
# One action per twin-expressible row (38/38). Names, remedies and
# procedure quotes are verbatim manual text (parser remedies cross-checked
# against the manual pages read for this mapping). state_effects set the
# faulted field back to its nominal value; for the 11 rows whose fault
# component is not in the twin, state_effects is null and the action is
# still recorded (the remedy is real — it is a service-agent or
# building-side fix the twin cannot express yet). expected_trace carries
# transition_id null everywhere: no archetype transitions exist yet for any
# Miller component, so no trace step can be named — flagged, not invented.
# The 25 settings/technique rows have no fault injection and therefore no
# repair action here; their remedies live in the oracle's held-out
# answer_key, not in the twin.
REPAIR_NAME = {
    "miller-01": "Turn the line disconnect switch On",
    "miller-02": "Replace the building line fuse or reset the circuit breaker",
    "miller-03": "Secure the gun trigger connections",
    "miller-04": "Power-cycle the unit; if HL.P 001 returns, have a Factory Authorized Service Agent check the unit",
    "miller-05": "Let the unit cool until the fan has run and HL.P 002 clears",
    "miller-06": "Release the trigger or remove the stuck wire causing the short; if HL.P 004 remains, have a Factory Authorized Service Agent check for shorted trigger leads",
    "miller-07": "Have a Factory Authorized Service Agent check the Power switch",
    "miller-08": "Have a Factory Authorized Service Agent check all board connections and the main control board",
    "miller-09": "Connect the work clamp for good metal-to-metal contact",
    "miller-10": "Replace the contact tip",
    "miller-11": "Have a Factory Authorized Service Agent check the main control board and main rectifier (HL.P 003)",
    "miller-12": "Connect the unit to the proper input voltage / check for low line voltage",
    "miller-14": "Have a Factory Authorized Service Agent check the main control board",
    "miller-15": "Have a Factory Authorized Service Agent check the fan-on-demand circuit",
    "miller-17": "Change to the correct size drive rolls",
    "miller-18": "Readjust the drive roll pressure",
    "miller-19": "Replace the inlet guide, contact tip, and/or liner as necessary",
    "miller-21": "Have a Factory Authorized Service Agent check the main control board",
    "miller-26": "Increase the shielding-gas flow at the regulator/flowmeter and prevent drafts near the arc",
    "miller-28": "Increase the shielding-gas flow at the regulator/flowmeter (and clear the nozzle, check hoses, set nozzle distance)",
    "miller-44": "Increase the pressure setting on the wire feed rolls",
    "miller-45": "Check the size stamped on the wire feed rolls; replace to match wire size and type",
    "miller-46": "Decrease the brake pressure on the wire spool",
    "miller-47": "Check and replace the cable, gun, and contact tip if damaged; check the contact tip and cable liner size",
    "miller-48": "Decrease the pressure setting on the wire feed rolls",
    "miller-49": "Check the contact tip size and the cable liner length and diameter; replace if necessary",
    "miller-50": "Loosen the gun securing bolt and push the gun end into the drive housing",
    "miller-51": "Replace the liner",
    "miller-52": "Replace the empty gas cylinder",
    "miller-53": "Clean or replace the gas nozzle",
    "miller-55": "Check the gas hose between the flowmeter and the wire feeder, and the gas hose in the gun and cable assembly",
    "miller-56": "Have a Factory Authorized Service Agent repair the gas solenoid wiring",
    "miller-57": "Have a Factory Authorized Service Agent replace the gas solenoid valve",
    "miller-58": "Check the primary voltage and relink the welding power source for the correct voltage",
    "miller-59": "Adjust the pressure setting on the wire feed rolls; replace worn drive rolls if necessary",
    "miller-60": "Match the liner and contact tip to the wire size and type",
    "miller-62": "Check and tighten all connections (gun weld cable and work cable)",
    "miller-63": "Repair or replace the gun as necessary",
}
# Discriminating oracle test for each row (the check the repair must satisfy).
REPAIR_TESTS = {
    "miller-01": ["disconnect_check"], "miller-02": ["breaker_check"],
    "miller-03": ["trigger_check"], "miller-04": ["power_cycle_check", "board_check"],
    "miller-05": ["thermal_check"], "miller-06": ["trigger_check"],
    "miller-07": ["power_switch_check"], "miller-08": ["board_check"],
    "miller-09": ["ground_check"], "miller-10": ["tip_condition_check"],
    "miller-11": ["board_check"], "miller-12": ["input_voltage_check"],
    "miller-14": ["board_check"], "miller-15": ["fan_circuit_check"],
    "miller-17": ["drive_roll_check"], "miller-18": ["tension_check"],
    "miller-19": ["liner_tip_check"], "miller-21": ["board_check"],
    "miller-26": ["gas_flow_check"], "miller-28": ["gas_flow_check"],
    "miller-44": ["tension_check"], "miller-45": ["drive_roll_check"],
    "miller-46": ["spool_brake_check"], "miller-47": ["gun_restriction_check"],
    "miller-48": ["tension_check"], "miller-49": ["liner_tip_size_check"],
    "miller-50": ["connector_seated_check"], "miller-51": ["liner_check"],
    "miller-52": ["bottle_level_check"], "miller-53": ["nozzle_check"],
    "miller-55": ["gas_line_check"], "miller-56": ["solenoid_check"],
    "miller-57": ["solenoid_check"], "miller-58": ["input_voltage_check"],
    "miller-59": ["tension_check"], "miller-60": ["liner_tip_size_check"],
    "miller-62": ["cable_connection_check"], "miller-63": ["gun_condition_check"],
}
PRECONDITIONS = {
    "miller-01": ["Line disconnect switch placed in the On position (6-5 p31 remedy 1; 4-13 p19).",
                  "Lockout/tagout: 'Disconnect and lockout/tagout input power before connecting input conductors from unit' (4-13 p19)."],
    "miller-02": ["Building line fuse replaced or circuit breaker reset (6-5 p31 remedy 2; 4-12 p18 service guide for fuse/breaker rating).",
                  "Lockout/tagout applies (4-13 p19)."],
    "miller-03": ["Gun trigger connections secured per the welding gun Owner's Manual (6-5 p31 remedy 3)."],
    "miller-04": ["Power switch turned off and back on; HL.P 001 reappears (6-5 p31 remedy 4).",
                  "If the code remains, a Factory Authorized Service Agent checks the unit (6-5 p31 remedy 4)."],
    "miller-05": ["Unit left to cool with the fan running until HL.P 002 clears (6-5 p31 remedy 5; 6-2 p29).",
                  "If the message remains after the unit is cool, a Factory Authorized Service Agent checks for an open Thermistor T (6-2 p29)."],
    "miller-06": ["Trigger released or stuck wire removed to reset HL.P 004 (6-5 p31 remedy 6; 5-2 p26).",
                  "If the message remains, a Factory Authorized Service Agent checks for shorted trigger leads (6-5 p31 remedy 6)."],
    "miller-07": ["A Factory Authorized Service Agent checks the Power switch (6-5 p31 remedy 7)."],
    "miller-08": ["A Factory Authorized Service Agent checks all board connections and the main control board (6-5 p31 remedy 8)."],
    "miller-09": ["Work clamp connected to get good metal to metal contact (6-5 p31 remedy 1)."],
    "miller-10": ["Contact tip replaced per the welding gun Owner's Manual (6-5 p31 remedy 2)."],
    "miller-11": ["A Factory Authorized Service Agent checks the main control board and main rectifier (6-5 p31 remedy 3)."],
    "miller-12": ["Unit connected to the proper input voltage (6-5 p31 remedy 1; 4-13 p19; 4-12 p18 service guide)."],
    "miller-14": ["A Factory Authorized Service Agent checks the main control board (6-5 p31 remedy 3)."],
    "miller-15": ["A Factory Authorized Service Agent checks the fan-on-demand circuit (6-5 p31 remedy 1)."],
    "miller-17": ["Correct size drive rolls fitted (6-5 p31 remedy 2; 6-3 p29).",
                  "Power is off while the drive roll is changed (6-4 p30: 'Turn Off power')."],
    "miller-18": ["Drive roll pressure readjusted using the pressure indicator scale (6-5 p31 remedy 3; 4-14 p20: 'Use pressure indicator scale to set a desired drive roll pressure')."],
    "miller-19": ["Inlet guide, contact tip, and/or liner replaced if necessary (6-5 p31 remedy 4)."],
    "miller-21": ["A Factory Authorized Service Agent checks the main control board (6-5 p31 remedy 6)."],
    "miller-26": ["Shielding gas flow increased at the regulator/flowmeter and drafts near the arc prevented (8-8 p41)."],
    "miller-28": ["Shielding gas flow increased at the regulator/flowmeter; spatter removed from the gun nozzle; gas hoses checked for leaks; nozzle placed 1/4 to 1/2 in (6-13 mm) from the workpiece (8-9 p41)."],
    "miller-44": ["Pressure setting on the wire feed rolls increased (8-17 p44)."],
    "miller-45": ["Size stamped on the wire feed rolls checked; rolls replaced to match wire size and type if necessary (8-17 p44).",
                  "Power is off while the drive roll is changed (6-4 p30: 'Turn Off power')."],
    "miller-46": ["Brake pressure on the wire spool decreased (8-17 p44)."],
    "miller-47": ["Cable, gun, and contact tip checked and replaced if damaged; contact tip and cable liner size checked and replaced if necessary (8-17 p44)."],
    "miller-48": ["Pressure setting on the wire feed rolls decreased (8-17 p44)."],
    "miller-49": ["Contact tip size checked; cable liner length and diameter checked and replaced if necessary (8-17 p44)."],
    "miller-50": ["Gun securing bolt in the drive housing loosened; gun end pushed into the housing just enough so it does not touch the wire feed rolls (8-17 p44)."],
    "miller-51": ["Liner replaced (8-17 p44)."],
    "miller-52": ["Empty gas cylinder replaced (8-17 p44)."],
    "miller-53": ["Gas nozzle cleaned or replaced (8-17 p44)."],
    "miller-55": ["Gas hose between the flowmeter and the wire feeder checked; gas hose in the gun and cable assembly checked (8-17 p44)."],
    "miller-56": ["A Factory Authorized Service Agent repairs the gas solenoid wiring (8-17 p44)."],
    "miller-57": ["A Factory Authorized Service Agent replaces the gas solenoid valve (8-17 p44)."],
    "miller-58": ["Primary voltage checked and the welding power source relinked for the correct voltage (8-17 p44; 4-11 p17 jumper links; 4-12 p18 service guide)."],
    "miller-59": ["Pressure setting on the wire feed rolls adjusted; worn drive rolls replaced if necessary (8-17 p45).",
                  "Power is off while the drive roll is changed (6-4 p30: 'Turn Off power')."],
    "miller-60": ["Liner and contact tip matched to the wire size and type (8-17 p45)."],
    "miller-62": ["All connections checked and tightened at the gun weld cable and the work cable (8-17 p45).",
                  "Power is off before connecting to weld output terminals (4-4 p13: 'Turn off power before connecting to weld output terminals')."],
    "miller-63": ["Gun repaired or replaced as necessary (8-17 p45)."],
}
# Second procedure ref (verbatim manual text) where the remedy alone is thin.
EXTRA_REFS = {
    "miller-17": [{"page": 29, "quote": "The drive roll consists of two different sized grooves.",
                   "source": "6-3 Changing Drive Roll and Wire Inlet Guide, p29"}],
    "miller-18": [{"page": 20, "quote": "Use pressure indicator scale to set a desired drive roll pressure.",
                   "source": "4-14 Threading Welding Wire, p20"}],
    "miller-45": [{"page": 29, "quote": "The drive roll consists of two different sized grooves.",
                   "source": "6-3 Changing Drive Roll and Wire Inlet Guide, p29"}],
    "miller-59": [{"page": 20, "quote": "Feed wire to check drive roll pressure. Tighten knob enough to prevent slipping.",
                   "source": "4-14 Threading Welding Wire, p20"}],
}
# Source section/page of the remedy for each row (printed pages).
REMEDY_SOURCE = {}
for rid in ["miller-%02d" % i for i in range(1, 22)]:
    REMEDY_SOURCE[rid] = (31, "6-5 Troubleshooting (p. 31)")
REMEDY_SOURCE.update({"miller-26": (41, "8-8 Troubleshooting - Excessive Spatter (p. 41)"),
                      "miller-28": (41, "8-9 Troubleshooting - Porosity (p. 41)")})
for rid in ["miller-%02d" % i for i in range(44, 59)]:
    REMEDY_SOURCE[rid] = (44, "8-17 Troubleshooting Guide For Semiautomatic Welding Equipment (p. 44)")
for rid in ["miller-59", "miller-60", "miller-62", "miller-63"]:
    REMEDY_SOURCE[rid] = (45, "8-17 Troubleshooting Guide For Semiautomatic Welding Equipment (p. 45)")
# Paths that must read nominal after the repair: the observable-owner paths
# (pending_seed) plus the repaired field. transition_id is null throughout
# — no archetype transition exists yet for any Miller component.
RESTORE_PATHS = {
    "miller-01": ["entity:line-disconnect-switch.power_out", "entity:power-switch.power_out",
                  "entity:drive-motor.motion_out", "entity:wire-feed-rolls.material_out",
                  "entity:wire-guide.material_out", "entity:cable-liner.material_out",
                  "entity:mig-gun.material_out", "entity:contact-tip.material_out"],
    "miller-02": ["entity:millermatic-252.energy_out", "entity:weld-output-terminal.energy_out",
                  "entity:drive-housing.energy_out", "entity:mig-gun.energy_out",
                  "entity:contact-tip.energy_out"],
    "miller-05": ["entity:thermistor-t.fault"],
    "miller-07": ["entity:power-switch.power_out", "entity:millermatic-252.power_in"],
    "miller-09": ["entity:work-clamp.energy_out"],
    "miller-10": ["entity:contact-tip.material_out", "entity:contact-tip.energy_out"],
    "miller-12": ["entity:millermatic-252.power_in"],
    "miller-15": ["entity:fan-motor.motion_out", "entity:cooling-fan.motion_in"],
    "miller-17": ["entity:wire-feed-rolls.material_out", "entity:wire-guide.material_out",
                  "entity:cable-liner.material_out", "entity:mig-gun.material_out",
                  "entity:contact-tip.material_out"],
    "miller-18": ["entity:wire-feed-rolls.material_out", "entity:wire-guide.material_out",
                  "entity:cable-liner.material_out", "entity:mig-gun.material_out",
                  "entity:contact-tip.material_out"],
    "miller-19": ["entity:cable-liner.material_out", "entity:mig-gun.material_out",
                  "entity:contact-tip.material_out"],
    "miller-26": ["entity:mig-gun.gas_out"],
    "miller-28": ["entity:mig-gun.gas_out"],
    "miller-44": ["entity:wire-feed-rolls.material_out", "entity:wire-guide.material_out",
                  "entity:cable-liner.material_out", "entity:mig-gun.material_out",
                  "entity:contact-tip.material_out"],
    "miller-45": ["entity:wire-feed-rolls.material_out", "entity:wire-guide.material_out",
                  "entity:cable-liner.material_out", "entity:mig-gun.material_out",
                  "entity:contact-tip.material_out"],
    "miller-46": ["entity:wire-feed-rolls.material_out", "entity:wire-guide.material_out",
                  "entity:cable-liner.material_out", "entity:mig-gun.material_out",
                  "entity:contact-tip.material_out"],
    "miller-47": ["entity:mig-gun.material_out", "entity:contact-tip.material_out"],
    "miller-48": ["entity:wire-feed-rolls.material_out", "entity:wire-guide.material_out",
                  "entity:cable-liner.material_out", "entity:mig-gun.material_out",
                  "entity:contact-tip.material_out"],
    "miller-49": ["entity:cable-liner.material_out", "entity:mig-gun.material_out",
                  "entity:contact-tip.material_out"],
    "miller-50": ["entity:mig-gun.material_out", "entity:contact-tip.material_out"],
    "miller-51": ["entity:cable-liner.material_out", "entity:mig-gun.material_out",
                  "entity:contact-tip.material_out"],
    "miller-52": ["entity:gas-cylinder.supply_out", "entity:regulator-flowmeter.fluid_out",
                  "entity:gas-solenoid-valve.fluid_out", "entity:mig-gun.gas_out"],
    "miller-53": ["entity:mig-gun.gas_out", "entity:gas-nozzle.gas_in"],
    "miller-56": ["entity:gas-solenoid-valve.fluid_out", "entity:mig-gun.gas_out"],
    "miller-57": ["entity:gas-solenoid-valve.fluid_out", "entity:mig-gun.gas_out"],
    "miller-58": ["entity:mig-gun.gas_out"],
    "miller-59": ["entity:wire-feed-rolls.material_out", "entity:wire-guide.material_out",
                  "entity:cable-liner.material_out", "entity:mig-gun.material_out",
                  "entity:contact-tip.material_out"],
    "miller-60": ["entity:cable-liner.material_out", "entity:mig-gun.material_out",
                  "entity:contact-tip.material_out"],
    "miller-62": ["entity:mig-gun.energy_out", "entity:contact-tip.energy_out"],
    "miller-63": ["entity:mig-gun.energy_out", "entity:mig-gun.material_out",
                  "entity:contact-tip.material_out"],
}

actions = []
for r in te_rows:
    rid = r["row_id"]
    fault = r["fault"]
    cid = RESOLUTION[fault["component"]]
    page, src = REMEDY_SOURCE[rid]
    refs = [{"page": page, "quote": parser[rid]["remedy"], "source": src}] + EXTRA_REFS.get(rid, [])
    if cid is None:
        state_effects = None
        unmappable_note = ("Fault component %r is not in the twin (%s); the repair is recorded from the manual, "
                           "but no twin state can carry it yet." % (fault["component"], RESOLUTION_WHY[fault["component"]][:120]))
    elif fault["kind"] == "parameter":
        nominal = NOMINAL.get((fault["component"], fault["field"]))
        assert nominal is not None, (rid, fault)
        state_effects = {f"{fault['component']}.{fault['field']}": nominal}
        unmappable_note = None
    else:
        if rid in ("miller-04", "miller-11"):
            # fault value is the HL.P meter code itself, not a component state
            state_effects = None
            unmappable_note = ("Fault value %r is the meter code itself (the manual never defines it); "
                               "there is no component state to restore. The repair is the power-cycle / "
                               "service-agent check in the remedy." % fault["value"])
        else:
            state_effects = {f"{fault['component']}.fault": "none"}
            unmappable_note = None
    nominals = oracle["cases"][r["case_id"]]["nominal"]
    mapped_nominals = {p: v for p, v in nominals.items() if PORTS[p]["status"] == "pending_seed"}
    postconditions = dict(mapped_nominals)
    if state_effects:
        postconditions.update(state_effects)
    if cid is None or rid in ("miller-04", "miller-11"):
        trace = {"status": "unmappable",
                 "reason": unmappable_note,
                 "required_state_changes": [],
                 "terminal_conditions": nominals}
    else:
        trace = {"status": "pending_archetype",
                 "reason": "No archetype transition exists yet for any Miller component, so no trace step can be "
                           "named (transition_id null throughout). The paths below are the REVIEWED target paths "
                           "that must read nominal after the repair, once the seed step exists.",
                 "required_state_changes": [{"path": p, "transition_id": None, "value": "normal"}
                                            for p in RESTORE_PATHS.get(rid, [])],
                 "terminal_conditions": nominals}
    safety = ["Follow your plant's lockout procedure before anyone works on this machine. This is not a safety assessment."]
    if rid in ("miller-01", "miller-02", "miller-12", "miller-58"):
        safety.append("Disconnect and lockout/tagout input power before connecting input conductors (4-13 p19).")
    if rid == "miller-62":
        safety.append("Turn off power before connecting to weld output terminals; failure to properly connect weld cables may cause excessive heat and start a fire (4-4 p13).")
    actions.append({
        "id": f"repair:{rid}",
        "case_id": r["case_id"],
        "fault_injection_ref": rid,
        "name": REPAIR_NAME[rid],
        "cause": r["cause"],
        "fault": fault,
        "procedure_refs": refs,
        "preconditions": PRECONDITIONS[rid],
        "state_effects": state_effects,
        "unmappable_note": unmappable_note,
        "postconditions": postconditions,
        "required_tests": REPAIR_TESTS[rid],
        "required_tests_note": "Oracle test ids (c002b-miller.json tests). No test models exist yet for Miller; "
                               "these are the checks the repair must satisfy once test models are built.",
        "expected_trace": trace,
        "safety_requirements": safety,
    })
assert len(actions) == 38
assert all(a["id"] for a in actions)
repair_doc = {
    "version": "miller-repair-actions-v1",
    "product": {"manufacturer": "Miller Electric", "model": "Millermatic 252",
                "manual": MANUAL},
    "oracle": "crater-benchmarks/evaluations/c002b-miller.json (38 twin-expressible rows)",
    "note": "Reviewed repair actions, one per twin-expressible oracle row (38/38), covering all 17 Miller cases. "
            "Remedies are verbatim manual text (cross-checked against the manual pages). 27/38 actions restore a "
            "twin state (state_effects set); 11/38 name a fault component the twin does not have "
            "(miller-02, 03, 04, 06, 08, 11, 12, 14, 21, 55, 58) and carry state_effects null with the reason. "
            "expected_trace transition ids are null throughout — no archetype transitions exist yet for Miller; "
            "that is Step 2/3 work, not invented here. The 25 settings/technique rows have no repair action here "
            "(no fault injection; remedies in the oracle's held-out answer_key).",
    "coverage": {"rows_total": 38, "actions": len(actions),
                 "with_state_effects": sum(1 for a in actions if a["state_effects"]),
                 "unmappable_no_twin_state": sum(1 for a in actions if not a["state_effects"]),
                 "cases_covered": sorted({a["case_id"] for a in actions})},
    "actions": actions,
}
(OUT / "repair_actions.json").write_text(json.dumps(repair_doc, indent=2, sort_keys=True) + "\n")

# =====================================================================
# Validation
# =====================================================================
for name in ("observable_paths.json", "fault_injections.json", "edges.json", "repair_actions.json"):
    d = json.loads((OUT / name).read_text())
    print(f"{name}: OK, top-level keys={sorted(d.keys())}")
# every edge endpoint is a real twin component id
for e in edges_doc["edges"]:
    assert e["source_id"] in twin_by_id and e["target_id"] in twin_by_id
# every fault-injection component_id is a real twin component id
for row in fault_injections["rows"].values():
    cid = row["twin_resolution"]["component_id"]
    if cid:
        assert cid in twin_by_id
# every observable target path component exists
for p, path in observable_paths["observable_paths"].items():
    assert path.split(".", 1)[0] in twin_by_id, (p, path)
# every restore path component exists
for a in actions:
    for sc in a["expected_trace"].get("required_state_changes", []):
        assert sc["path"].split(".", 1)[0] in twin_by_id, (a["id"], sc["path"])
print("ALL VALIDATION PASSED")
print("observable ports:", len(PORTS), "| pending_seed:",
      sum(1 for d in PORTS.values() if d['status'] == 'pending_seed'),
      "| unmodelled:", sum(1 for d in PORTS.values() if d['status'] == 'unmodelled'))
print("edges:", len(EDGES), "| fault injections:", len(fault_injections["rows"]),
      "| repair actions:", len(actions))
