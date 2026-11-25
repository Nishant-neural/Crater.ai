"""The OMNIPRO 220 proof canonical model (Phase 3B/3C).

A CONSTRUCTED canonical model, not an ingestion path: eighteen entities
with unique names, typed ports, bound parameters with page citations, and
the human-confirmed edges from ``omnipro_edge_list.json`` (v2 in 3C). It
is the model the 3B/3C gates run the oracle on — the crater-10 snapshot
cannot carry the gate (7 duplicate Feed Tensioners, 0 ports), so the gate
is proven here and the oracle is ALSO run on crater-10 with compiler v3
and reported honestly (see ``benchmark/run_phase3b_oracle.py``).

Every fact in this model cites its manual page. Nothing is invented:
- the machine is the HEALTHY, nominal machine (welder on, trigger held,
  spool loaded, gas bottle full, correct roller fitted) — the oracle's O1
  nominal gate needs the healthy welding state, and the fault runs inject
  the fault from there;
- the trigger's coil_in, the spool/bottle material_in, the cord's
  power_in, the button's coil_in, the valve's control_in, the ground's
  energy/material_in, the liner's energy/gas_in, the tip's gas_in and the
  LCD's warning_in are unmodelled inputs set to their nominal values (the
  mains/breaker/controller are A1/A2/A6 work, done in 3C for the power
  chain; the warning signal is still A6). They are inputs, not behaviour
  claims;
- tension, groove_matches, plug_rating, cord_gauge, input_voltage,
  protection, size_matches, connector_seated and gas_flow carry NO bound
  quantity: the manual gives procedures, not values. The compiler supplies
  the archetype nominal defaults and the binding records the parameters as
  unbound — honestly. The motor rating is likewise unbound: p7's 120/240
  VAC is the machine's input, recorded here on ``unresolved:entity``
  exactly as the real snapshot carries it, and it is NEVER motor-rating
  evidence (§8). The spool/bottle LEVEL is the exception: A9's level is
  required_for_signature, so it is bound by a quantity recording the
  healthy-state level (loaded / full) — the proof twin's premise, not a
  nameplate value.
- 3C changes (see PHASE3C_BUILD_NOTES.md): the wire path is
  feeder -> liner -> gun -> tip -> arc (A8 conductor rows carry it; the 3B
  feeder->gun link fault override is retired); the arc is a proper A7 load
  (Welding Arc) whose effect_out is linked to the Welder's arc port; the
  power chain is cord -> button -> welder -> trigger/motor/lcd; the gas
  chain is bottle -> valve -> gun -> arc; the ground is the arc's return
  path. The gun's wire path moved from the 3B link fault-override into
  proper A8 rows; the link is now an identity passthrough.
"""
from __future__ import annotations

from typing import Any, Callable

from backend.knowledge.machine_model import (
    MachineEntity,
    MachineEvidence,
    MachineFailureMode,
    MachinePort,
    MachineQuantity,
    MachineRelation,
    MachineState,
    UniversalMachineModel,
)

# Stable component ids for the proof twin.
IDS = {
    "welder": "welder",
    "motor": "wire-feed-motor",
    "feeder": "wire-feed-mechanism",
    "gun": "mig-gun",
    "trigger": "trigger",
    "spool": "wire-spool",
    "wingnut": "wingnut",
    "tensioner": "feed-tensioner",
    "wire": "welding-wire",
    # 3C entities.
    "liner": "liner-assembly",
    "tip": "contact-tip",
    "ground": "ground-clamp",
    "cord": "input-power-cord",
    "button": "reset-button",
    "bottle": "shielding-gas-bottle",
    "valve": "gas-valve",
    "arc": "welding-arc",
    "lcd": "lcd-display",
}


def _evidence(page: int, quote: str, source: str) -> MachineEvidence:
    return MachineEvidence(fact=quote, page=page,
                           source_document="owner-manual.pdf", source_type=source)


def _port(port_id: str, entity_id: str, name: str, direction: str,
          domain: str, page: int, quote: str, source: str) -> MachinePort:
    return MachinePort(
        id=port_id, entity_id=entity_id, name=name, direction=direction,
        domain=domain, source_ids=["owner-manual.pdf"],
        evidence=[_evidence(page, quote, source)])


def _a8_ports(entity_id: str, prefix: str, page: int, quote: str,
              source: str) -> list[MachinePort]:
    """The A8 conductor port set (material + energy + gas). Every A8
    instance carries all six ports — the rows fire uniformly only when
    every input field exists (see _CONDUCTOR in archetypes.py). Ports a
    part does not really carry (the liner's gas, the ground's material)
    are unmodelled nominal inputs; nothing consumes their passthrough."""
    return [
        _port(f"port:{prefix}:material-in", entity_id, "material_in", "in",
              "material", page, quote, source),
        _port(f"port:{prefix}:material-out", entity_id, "material_out", "out",
              "material", page, quote, source),
        _port(f"port:{prefix}:energy-in", entity_id, "energy_in", "in",
              "electrical", page, quote, source),
        _port(f"port:{prefix}:energy-out", entity_id, "energy_out", "out",
              "electrical", page, quote, source),
        _port(f"port:{prefix}:gas-in", entity_id, "gas_in", "in",
              "material", page, quote, source),
        _port(f"port:{prefix}:gas-out", entity_id, "gas_out", "out",
              "material", page, quote, source),
    ]


def build_omnipro_proof_model() -> UniversalMachineModel:
    """Build the proof canonical model. Every entity has a unique name;
    every typed port matches an archetype signature (A1/A2/A3/A4/A7/A8/A9/
    A10/A11) or is a link-carried observable (Welder.arc)."""
    entities = [
        MachineEntity(
            id=IDS["welder"], name="Welder", entity_type="machine",
            source_ids=["owner-manual.pdf"],
            typed_ports=[
                _port("port:welder:power-in", IDS["welder"], "power_in", "in",
                      "electrical", 7, "Power Input 120/240 VAC",
                      "owner-manual p7 specifications"),
                _port("port:welder:power-out", IDS["welder"], "power_out", "out",
                      "electrical", 45, "WIRE FEED M",
                      "owner-manual p45 wiring schematic"),
                _port("port:welder:material-out", IDS["welder"], "material_out",
                      "out", "material", 42,
                      "Wire Feed Motor Runs but Wire Does Not Feed Properly",
                      "owner-manual p42 troubleshooting"),
                _port("port:welder:arc", IDS["welder"], "arc", "out",
                      "arc", 42,
                      "Wire Feed Motor Runs but Wire Does Not Feed Properly",
                      "owner-manual p42 troubleshooting"),
            ],
            evidence=[_evidence(7, "OMNIPRO 220", "owner-manual p7 specifications")]),
        MachineEntity(
            id=IDS["motor"], name="Wire Feed Motor", entity_type="motor",
            library_type="dc_motor", source_ids=["owner-manual.pdf"],
            typed_ports=[
                _port("port:motor:power-in", IDS["motor"], "power_in", "in",
                      "electrical", 45, "WIRE FEED M",
                      "owner-manual p45 wiring schematic"),
                _port("port:motor:enable-in", IDS["motor"], "enable_in", "in",
                      "signal", 17, "Press and hold the Trigger to feed the wire",
                      "owner-manual p17 step 27"),
                _port("port:motor:motion-out", IDS["motor"], "motion_out", "out",
                      "mechanical", 42,
                      "Wire Feed Motor Runs but Wire Does Not Feed Properly",
                      "owner-manual p42 troubleshooting"),
                _port("port:motor:heat-out", IDS["motor"], "heat_out", "out",
                      "thermal", 43, "Tripped thermal protection device",
                      "owner-manual p43 troubleshooting"),
            ],
            evidence=[_evidence(42, "Wire Feed Motor Runs but Wire Does Not Feed Properly",
                                "owner-manual p42 troubleshooting")]),
        MachineEntity(
            id=IDS["feeder"], name="Wire Feed Mechanism", entity_type="mechanism",
            library_type="wire_feeder", source_ids=["owner-manual.pdf"],
            typed_ports=[
                _port("port:feeder:motion-in", IDS["feeder"], "motion_in", "in",
                      "mechanical", 42,
                      "Wire Feed Motor Runs but Wire Does Not Feed Properly",
                      "owner-manual p42 troubleshooting"),
                _port("port:feeder:material-in", IDS["feeder"], "material_in", "in",
                      "material", 10, "If Wire Spool can spin freely, Wingnut is too loose",
                      "owner-manual p10 spool setup"),
                _port("port:feeder:material-out", IDS["feeder"], "material_out", "out",
                      "material", 17, "If the wire stops instead of bending",
                      "owner-manual p17 step 27"),
            ],
            evidence=[_evidence(17, "If the wire stops instead of bending",
                                "owner-manual p17 step 27")]),
        MachineEntity(
            id=IDS["gun"], name="MIG Gun", entity_type="tool",
            source_ids=["owner-manual.pdf"],
            typed_ports=_a8_ports(
                IDS["gun"], "gun", 13,
                "Gun Cable Connector fully inserted into the socket on the "
                "Wire Feed mechanism",
                "owner-manual p13 steps 13-14"),
            evidence=[_evidence(42, "Damaged MIG Gun, cable, or liner assembly",
                                "owner-manual p42 troubleshooting")]),
        MachineEntity(
            id=IDS["trigger"], name="Trigger", entity_type="switch",
            library_type="relay", source_ids=["owner-manual.pdf"],
            typed_ports=[
                _port("port:trigger:coil-in", IDS["trigger"], "coil_in", "in",
                      "signal", 17, "Press and hold the Trigger to feed the wire",
                      "owner-manual p17 step 27"),
                _port("port:trigger:power-in", IDS["trigger"], "power_in", "in",
                      "electrical", 43, "Faulty or improperly connected Trigger",
                      "owner-manual p43 case 5"),
                _port("port:trigger:power-out", IDS["trigger"], "power_out", "out",
                      "electrical", 17, "Press and hold the Trigger to feed the wire",
                      "owner-manual p17 step 27"),
            ],
            evidence=[_evidence(17, "Press and hold the Trigger to feed the wire",
                                "owner-manual p17 step 27")]),
        MachineEntity(
            id=IDS["spool"], name="Wire Spool", entity_type="spool",
            source_ids=["owner-manual.pdf"],
            typed_ports=[
                _port("port:spool:material-in", IDS["spool"], "material_in", "in",
                      "material", 10, "If Wire Spool can spin freely, Wingnut is too loose",
                      "owner-manual p10 spool setup"),
                _port("port:spool:material-out", IDS["spool"], "material_out", "out",
                      "material", 10, "If Wire Spool can spin freely, Wingnut is too loose",
                      "owner-manual p10 spool setup"),
                _port("port:spool:supply-out", IDS["spool"], "supply_out", "out",
                      "material", 10, "If Wire Spool can spin freely, Wingnut is too loose",
                      "owner-manual p10 spool setup"),
            ],
            evidence=[_evidence(10, "If Wire Spool can spin freely, Wingnut is too loose",
                                "owner-manual p10 spool setup")]),
        MachineEntity(
            id=IDS["wingnut"], name="Wingnut", entity_type="fastener",
            source_ids=["owner-manual.pdf"],
            evidence=[_evidence(10, "Replace the Spacer over the Spool Spindle and secure "
                                    "Spool in place with the Wingnut",
                                "owner-manual p10 step 5")]),
        MachineEntity(
            id=IDS["tensioner"], name="Feed Tensioner", entity_type="part",
            source_ids=["owner-manual.pdf"],
            evidence=[_evidence(42, "Feed Tensioner is too tight",
                                "owner-manual p42 troubleshooting")]),
        MachineEntity(
            id=IDS["wire"], name="Welding Wire", entity_type="material",
            source_ids=["owner-manual.pdf"],
            evidence=[_evidence(17, "If the wire stops instead of bending",
                                "owner-manual p17 step 27")]),
        # ---- 3C entities ----
        MachineEntity(
            id=IDS["liner"], name="liner assembly", entity_type="part",
            source_ids=["owner-manual.pdf"],
            typed_ports=_a8_ports(
                IDS["liner"], "liner", 42,
                "Gun liner is clogged or worn",
                "owner-manual p42 troubleshooting"),
            evidence=[_evidence(42, "Gun liner is clogged or worn",
                                "owner-manual p42 troubleshooting")]),
        MachineEntity(
            id=IDS["tip"], name="Contact Tip", entity_type="part",
            source_ids=["owner-manual.pdf"],
            typed_ports=_a8_ports(
                IDS["tip"], "tip", 41,
                "Contact Tip hole even and circular, not deformed/enlarged",
                "owner-manual p41 contact tip inspection"),
            evidence=[_evidence(42, "Incorrect Contact Tip or liner size or excessive wear",
                                "owner-manual p42 troubleshooting")]),
        MachineEntity(
            id=IDS["ground"], name="Ground Clamp", entity_type="part",
            source_ids=["owner-manual.pdf"],
            typed_ports=_a8_ports(
                IDS["ground"], "ground", 43,
                "Poor connection with workpiece",
                "owner-manual p43 troubleshooting"),
            evidence=[_evidence(43, "Poor connection with workpiece",
                                "owner-manual p43 troubleshooting")]),
        MachineEntity(
            id=IDS["cord"], name="Input Power Cord", entity_type="part",
            source_ids=["owner-manual.pdf"],
            typed_ports=[
                _port("port:cord:power-in", IDS["cord"], "power_in", "in",
                      "electrical", 7, "Power Input 120/240 VAC",
                      "owner-manual p7 specifications"),
                _port("port:cord:power-out", IDS["cord"], "power_out", "out",
                      "electrical", 7, "Power Input 120/240 VAC",
                      "owner-manual p7 specifications"),
                _port("port:cord:material-out", IDS["cord"], "material_out", "out",
                      "material", 7, "Power Input 120/240 VAC",
                      "owner-manual p7 specifications"),
            ],
            evidence=[_evidence(7, "Power Input 120/240 VAC",
                                "owner-manual p7 specifications")]),
        MachineEntity(
            id=IDS["button"], name="Reset Button", entity_type="switch",
            source_ids=["owner-manual.pdf"],
            typed_ports=[
                _port("port:button:coil-in", IDS["button"], "coil_in", "in",
                      "signal", 43, "Circuit breaker has tripped due to high input amperage",
                      "owner-manual p43 troubleshooting"),
                _port("port:button:power-in", IDS["button"], "power_in", "in",
                      "electrical", 43, "Circuit breaker has tripped due to high input amperage",
                      "owner-manual p43 troubleshooting"),
                _port("port:button:power-out", IDS["button"], "power_out", "out",
                      "electrical", 43, "Circuit breaker has tripped due to high input amperage",
                      "owner-manual p43 troubleshooting"),
            ],
            evidence=[_evidence(43, "Circuit breaker has tripped due to high input amperage",
                                "owner-manual p43 troubleshooting")]),
        MachineEntity(
            id=IDS["bottle"], name="Shielding Gas Bottle", entity_type="tank",
            source_ids=["owner-manual.pdf"],
            typed_ports=[
                _port("port:bottle:material-in", IDS["bottle"], "material_in", "in",
                      "material", 43, "Shielding gas bottle is empty",
                      "owner-manual p43 troubleshooting"),
                _port("port:bottle:material-out", IDS["bottle"], "material_out", "out",
                      "material", 43, "Shielding gas bottle is empty",
                      "owner-manual p43 troubleshooting"),
                _port("port:bottle:supply-out", IDS["bottle"], "supply_out", "out",
                      "material", 43, "Shielding gas bottle is empty",
                      "owner-manual p43 troubleshooting"),
            ],
            evidence=[_evidence(43, "Shielding gas bottle is empty",
                                "owner-manual p43 troubleshooting")]),
        MachineEntity(
            id=IDS["valve"], name="Gas Valve", entity_type="valve",
            source_ids=["owner-manual.pdf"],
            typed_ports=[
                _port("port:valve:fluid-in", IDS["valve"], "fluid_in", "in",
                      "material", 43, "Not enough or too much shielding gas",
                      "owner-manual p43 troubleshooting"),
                _port("port:valve:control-in", IDS["valve"], "control_in", "in",
                      "signal", 43, "Not enough or too much shielding gas",
                      "owner-manual p43 troubleshooting"),
                _port("port:valve:fluid-out", IDS["valve"], "fluid_out", "out",
                      "material", 43, "Not enough or too much shielding gas",
                      "owner-manual p43 troubleshooting"),
            ],
            evidence=[_evidence(43, "Not enough or too much shielding gas",
                                "owner-manual p43 troubleshooting")]),
        MachineEntity(
            id=IDS["arc"], name="Welding Arc", entity_type="arc",
            source_ids=["owner-manual.pdf"],
            typed_ports=[
                _port("port:arc:energy-in", IDS["arc"], "energy_in", "in",
                      "electrical", 45, "WIRE FEED M",
                      "owner-manual p45 wiring schematic"),
                _port("port:arc:material-in", IDS["arc"], "material_in", "in",
                      "material", 42, "Wire Feeds, but Arc Does Not Ignite",
                      "owner-manual p43 troubleshooting"),
                _port("port:arc:gas-in", IDS["arc"], "gas_in", "in",
                      "material", 13, "preventing shielding gas from reaching the welding arc",
                      "owner-manual p13 step 14 note"),
                _port("port:arc:ground-in", IDS["arc"], "ground_in", "in",
                      "electrical", 43, "Poor connection with workpiece",
                      "owner-manual p43 troubleshooting"),
                _port("port:arc:effect-out", IDS["arc"], "effect_out", "out",
                      "arc", 42, "Welding Arc Not Stable",
                      "owner-manual p42 troubleshooting"),
                _port("port:arc:weld-quality", IDS["arc"], "weld_quality", "out",
                      "weld_quality", 43, "Porosity in the Weld Metal",
                      "owner-manual p43 troubleshooting"),
            ],
            evidence=[_evidence(42, "Welding Arc Not Stable",
                                "owner-manual p42 troubleshooting")]),
        MachineEntity(
            id=IDS["lcd"], name="LCD Display", entity_type="display",
            source_ids=["owner-manual.pdf"],
            typed_ports=[
                _port("port:lcd:power-in", IDS["lcd"], "power_in", "in",
                      "electrical", 43, "LCD Display Does Not Light When Welder is Switched On",
                      "owner-manual p43 troubleshooting"),
                _port("port:lcd:warning-in", IDS["lcd"], "warning_in", "in",
                      "signal", 43, "Tripped thermal protection device",
                      "owner-manual p43 troubleshooting"),
                _port("port:lcd:display-out", IDS["lcd"], "display_out", "out",
                      "display", 43, "LCD Display Does Not Light When Welder is Switched On",
                      "owner-manual p43 troubleshooting"),
            ],
            evidence=[_evidence(43, "LCD Display Does Not Light When Welder is Switched On",
                                "owner-manual p43 troubleshooting")]),
    ]
    ports = [port for entity in entities for port in entity.typed_ports]

    def _rel(subject: str, subject_name: str, relation_type: str,
            obj: str, object_name: str, description: str,
            page: int, quote: str, source: str) -> MachineRelation:
        return MachineRelation(
            subject_id=IDS[subject], subject_name=subject_name,
            relation_type=relation_type, object_id=IDS[obj],
            object_name=object_name, description=description,
            evidence=[_evidence(page, quote, source)])

    relations = [
        # Power chain: cord -> button -> welder -> trigger / motor / lcd.
        _rel("cord", "Input Power Cord", "supplies", "button", "Reset Button",
             "The Input Power Cord supplies mains power to the Reset Button (breaker).",
             7, "Power Input 120/240 VAC", "owner-manual p7 specifications"),
        _rel("button", "Reset Button", "supplies", "welder", "Welder",
             "The Reset Button (breaker) is in series between the cord and the Welder.",
             43, "Circuit breaker has tripped due to high input amperage",
             "owner-manual p43 troubleshooting"),
        _rel("welder", "Welder", "powers", "trigger", "Trigger",
             "The Welder's switched power feeds the Trigger.",
             43, "Faulty or improperly connected Trigger",
             "owner-manual p43 case 5"),
        _rel("welder", "Welder", "powers", "motor", "Wire Feed Motor",
             "The Welder's switched power feeds the Wire Feed Motor.",
             45, "WIRE FEED M", "owner-manual p45 wiring schematic"),
        _rel("welder", "Welder", "powers", "lcd", "LCD Display",
             "The Welder's switched power feeds the LCD Display.",
             43, "LCD Display Does Not Light When Welder is Switched On",
             "owner-manual p43 troubleshooting"),
        _rel("welder", "Welder", "powers", "ground", "Ground Clamp",
             "The Welder's ground connection is the return path (the Ground Clamp).",
             43, "Poor connection with workpiece",
             "owner-manual p43 troubleshooting"),
        # Wire chain: trigger -> motor -> feeder -> liner -> gun -> tip -> arc.
        _rel("trigger", "Trigger", "enables", "motor", "Wire Feed Motor",
             "The Trigger enables the Wire Feed Motor (p17 step 27).",
             17, "Press and hold the Trigger to feed the wire",
             "owner-manual p17 step 27"),
        _rel("trigger", "Trigger", "supplies", "gun", "MIG Gun",
             "The Trigger's switched power is the welding current for the MIG Gun.",
             17, "Press and hold the Trigger to feed the wire",
             "owner-manual p17 step 27"),
        _rel("motor", "Wire Feed Motor", "drives", "feeder", "Wire Feed Mechanism",
             "The Wire Feed Motor drives the Wire Feed Mechanism (p42 case 1 premise).",
             42, "Wire Feed Motor Runs but Wire Does Not Feed Properly",
             "owner-manual p42 troubleshooting"),
        _rel("spool", "Wire Spool", "supplies", "feeder", "Wire Feed Mechanism",
             "The Wire Spool supplies wire to the Wire Feed Mechanism (p10).",
             10, "If Wire Spool can spin freely, Wingnut is too loose",
             "owner-manual p10 spool setup"),
        _rel("feeder", "Wire Feed Mechanism", "feeds", "liner", "liner assembly",
             "The Wire Feed Mechanism feeds wire into the liner assembly.",
             42, "Gun liner is clogged or worn",
             "owner-manual p42 troubleshooting"),
        _rel("liner", "liner assembly", "feeds", "gun", "MIG Gun",
             "The liner assembly (inside the gun cable) delivers wire to the MIG Gun.",
             42, "Gun liner is clogged or worn",
             "owner-manual p42 troubleshooting"),
        _rel("gun", "MIG Gun", "feeds", "tip", "Contact Tip",
             "The MIG Gun delivers wire to the Contact Tip.",
             42, "Incorrect Contact Tip or liner size or excessive wear",
             "owner-manual p42 troubleshooting"),
        _rel("gun", "MIG Gun", "supplies", "tip", "Contact Tip",
             "The MIG Gun carries the welding current to the Contact Tip.",
             45, "WIRE FEED M", "owner-manual p45 wiring schematic"),
        _rel("tip", "Contact Tip", "arcs_at", "arc", "Welding Arc",
             "The Contact Tip delivers wire and current to the welding arc.",
             43, "Wire Feeds, but Arc Does Not Ignite",
             "owner-manual p43 troubleshooting"),
        _rel("gun", "MIG Gun", "supplies", "arc", "Welding Arc",
             "The MIG Gun delivers shielding gas to the welding arc (p13 step 14 note).",
             13, "preventing shielding gas from reaching the welding arc",
             "owner-manual p13 step 14 note"),
        _rel("ground", "Ground Clamp", "returns", "arc", "Welding Arc",
             "The Ground Clamp is the arc's return path.",
             43, "Poor connection with workpiece",
             "owner-manual p43 troubleshooting"),
        _rel("arc", "Welding Arc", "arcs_at", "welder", "Welder",
             "The welding arc is on the Welder (machine root).",
             42, "Welding Arc Not Stable",
             "owner-manual p42 troubleshooting"),
        # Gas chain: bottle -> valve -> gun.
        _rel("bottle", "Shielding Gas Bottle", "supplies", "valve", "Gas Valve",
             "The Shielding Gas Bottle supplies gas to the gas valve.",
             43, "Shielding gas bottle is empty",
             "owner-manual p43 troubleshooting"),
        _rel("valve", "Gas Valve", "supplies", "gun", "MIG Gun",
             "The gas valve delivers shielding gas to the MIG Gun.",
             43, "Not enough or too much shielding gas",
             "owner-manual p43 troubleshooting"),
    ]
    states = [
        # Nominal operating points for the unmodelled inputs (the healthy
        # welding state): the welder is on, the trigger is held, the spool
        # is loaded, the bottle is full, the gas is on. These are inputs,
        # not behaviour claims.
        MachineState(entity_id=IDS["trigger"], name="coil_in", value="high",
                     source_ids=["owner-manual.pdf"],
                     evidence=[_evidence(17, "Press and hold the Trigger to feed the wire",
                                         "owner-manual p17 step 27")]),
        MachineState(entity_id=IDS["spool"], name="material_in", value="normal",
                     source_ids=["owner-manual.pdf"],
                     evidence=[_evidence(10, "If Wire Spool can spin freely, Wingnut is too loose",
                                         "owner-manual p10 spool setup")]),
        MachineState(entity_id=IDS["cord"], name="power_in", value="normal",
                     source_ids=["owner-manual.pdf"],
                     evidence=[_evidence(7, "Power Input 120/240 VAC",
                                         "owner-manual p7 specifications")]),
        MachineState(entity_id=IDS["button"], name="coil_in", value="high",
                     source_ids=["owner-manual.pdf"],
                     evidence=[_evidence(43, "Circuit breaker has tripped due to high input amperage",
                                         "owner-manual p43 troubleshooting")]),
        MachineState(entity_id=IDS["bottle"], name="material_in", value="normal",
                     source_ids=["owner-manual.pdf"],
                     evidence=[_evidence(43, "Shielding gas bottle is empty",
                                         "owner-manual p43 troubleshooting")]),
        MachineState(entity_id=IDS["valve"], name="control_in", value="high",
                     source_ids=["owner-manual.pdf"],
                     evidence=[_evidence(43, "Not enough or too much shielding gas",
                                         "owner-manual p43 troubleshooting")]),
        MachineState(entity_id=IDS["ground"], name="material_in", value="normal",
                     source_ids=["owner-manual.pdf"],
                     evidence=[_evidence(43, "Poor connection with workpiece",
                                         "owner-manual p43 troubleshooting")]),
        MachineState(entity_id=IDS["ground"], name="energy_in", value="normal",
                     source_ids=["owner-manual.pdf"],
                     evidence=[_evidence(43, "Poor connection with workpiece",
                                         "owner-manual p43 troubleshooting")]),
        MachineState(entity_id=IDS["ground"], name="gas_in", value="normal",
                     source_ids=["owner-manual.pdf"],
                     evidence=[_evidence(43, "Poor connection with workpiece",
                                         "owner-manual p43 troubleshooting")]),
        MachineState(entity_id=IDS["liner"], name="energy_in", value="normal",
                     source_ids=["owner-manual.pdf"],
                     evidence=[_evidence(42, "Gun liner is clogged or worn",
                                         "owner-manual p42 troubleshooting")]),
        MachineState(entity_id=IDS["liner"], name="gas_in", value="normal",
                     source_ids=["owner-manual.pdf"],
                     evidence=[_evidence(42, "Gun liner is clogged or worn",
                                         "owner-manual p42 troubleshooting")]),
        MachineState(entity_id=IDS["tip"], name="gas_in", value="normal",
                     source_ids=["owner-manual.pdf"],
                     evidence=[_evidence(42, "Incorrect Contact Tip or liner size or excessive wear",
                                         "owner-manual p42 troubleshooting")]),
        MachineState(entity_id=IDS["lcd"], name="warning_in", value="absent",
                     source_ids=["owner-manual.pdf"],
                     evidence=[_evidence(43, "Tripped thermal protection device",
                                         "owner-manual p43 troubleshooting")]),
        # Unmodelled machine settings (nominal values; the settings rows
        # that change them are not twin-expressible).
        MachineState(entity_id=IDS["welder"], name="mode", value="correct",
                     source_ids=["owner-manual.pdf"],
                     evidence=[_evidence(43, "Machine is in the incorrect mode",
                                         "owner-manual p43 troubleshooting")]),
        MachineState(entity_id=IDS["welder"], name="current_setting", value="correct",
                     source_ids=["owner-manual.pdf"],
                     evidence=[_evidence(42, "Not enough current",
                                         "owner-manual p42 troubleshooting")]),
        MachineState(entity_id=IDS["ground"], name="polarity", value="correct",
                     source_ids=["owner-manual.pdf"],
                     evidence=[_evidence(43, "Polarity is incorrect for the application",
                                         "owner-manual p43 troubleshooting")]),
    ]
    quantities = [
        # p7 machine input voltage, recorded exactly as the real snapshot
        # carries it: on no component. It is NEVER motor-rating evidence.
        MachineQuantity(id="quantity:p7:input-voltage", entity_id="unresolved:entity",
                        name="Power Input", value="120/240", unit="VAC",
                        source_kind="spec_page",
                        evidence=[_evidence(7, "Power Input 120/240 VAC",
                                            "owner-manual p7 specifications")]),
        # The motor rating is unbound: no nameplate/spec value for the
        # motor itself exists in the manual. Recorded as a quantity with
        # no value so the gap is visible, not silently absent.
        MachineQuantity(id="quantity:motor:rating", entity_id=IDS["motor"],
                        name="rating", value=None, unit="V",
                        source_kind=None,
                        evidence=[_evidence(7, "Power Input 120/240 VAC (machine input, "
                                              "not the motor rating)",
                                            "owner-manual p7 specifications")]),
        # A9 storage level: required_for_signature, so it must be bound
        # (quantities only — states do not count as binding evidence).
        # These record the healthy-state level (spool loaded, bottle
        # full) — the proof twin's premise, not a nameplate value.
        MachineQuantity(id="quantity:spool:level", entity_id=IDS["spool"],
                        name="level", value="normal", unit=None,
                        source_kind="setup",
                        evidence=[_evidence(10, "Wire Spool installation (spool loaded)",
                                            "owner-manual p10 spool setup")]),
        MachineQuantity(id="quantity:bottle:level", entity_id=IDS["bottle"],
                        name="level", value="normal", unit=None,
                        source_kind="setup",
                        evidence=[_evidence(43, "Shielding gas bottle (full in the healthy machine)",
                                            "owner-manual p43 troubleshooting")]),
    ]
    failure_modes = [
        MachineFailureMode(
            id="fm:omni-01-a", name="Insufficient wire feed pressure",
            symptoms=["Wire Feed Motor Runs but Wire Does Not Feed Properly"],
            possible_causes=["Insufficient wire feed pressure."],
            diagnostic_test="Feed wire against wood 2-3 inches away; the wire should bend, not stop.",
            entity_ids=[IDS["tensioner"]], entity_names=["Feed Tensioner"],
            evidence=[_evidence(42, "1. Insufficient wire feed pressure.",
                                "owner-manual p42 troubleshooting")]),
        MachineFailureMode(
            id="fm:omni-01-b", name="Incorrect wire feed roller size",
            symptoms=["Wire Feed Motor Runs but Wire Does Not Feed Properly"],
            possible_causes=["Incorrect wire feed roller size."],
            diagnostic_test="Check the Feed Roller groove matches the wire size marked on the spool.",
            entity_ids=[IDS["feeder"]], entity_names=["Wire Feed Mechanism"],
            evidence=[_evidence(42, "2. Incorrect wire feed roller size.",
                                "owner-manual p42 troubleshooting")]),
        MachineFailureMode(
            id="fm:omni-01-c", name="Damaged MIG Gun, cable, or liner assembly",
            symptoms=["Wire Feed Motor Runs but Wire Does Not Feed Properly"],
            possible_causes=["Damaged MIG Gun, cable, or liner assembly."],
            diagnostic_test="Have a qualified technician inspect these parts and replace as necessary.",
            entity_ids=[IDS["gun"]], entity_names=["MIG Gun"],
            evidence=[_evidence(42, "3. Damaged MIG Gun, cable, or liner assembly.",
                                "owner-manual p42 troubleshooting")]),
        MachineFailureMode(
            id="fm:omni-01-d", name="Feed Tensioner is too tight",
            symptoms=["Wire Feed Motor Runs but Wire Does Not Feed Properly"],
            possible_causes=["Feed Tensioner is too tight."],
            diagnostic_test="Loosen Feed Tensioner so it applies only enough pressure to prevent "
                            "continued spinning after the Gun Trigger is released.",
            entity_ids=[IDS["tensioner"]], entity_names=["Feed Tensioner"],
            evidence=[_evidence(42, "4. Feed Tensioner is too tight.",
                                "owner-manual p42 troubleshooting")]),
    ]
    return UniversalMachineModel(
        entities=entities, relations=relations, ports=ports,
        quantities=quantities, states=states, failure_modes=failure_modes,
        simulation_rules=[],
        evidence=[_evidence(42, "Wire Feed Motor Runs but Wire Does Not Feed Properly",
                            "owner-manual p42 troubleshooting")],
    )


def observable_paths(definition: Any) -> dict[str, str]:
    """Port-qualified observable mapping for the proof twin (v3 §6.2).
    All 17 vocabulary observable ports, mapped to twin state paths.
    Values are the vocabulary's exact values (the oracle reads by path,
    with no value translation) — so each mapping is to the field that
    carries that value:
    - wire_out@tip is the Contact Tip's material_out (3C: the tip is a
      separate A8 component; the vocabulary's owner was corrected from
      MIG Gun to Contact Tip);
    - arc is the Welder's arc port (the A7 effect_out, linked arc->welder);
    - welder_on is the Trigger's switched power_out (the welder functions
      only when the trigger's switched power is present);
    - weld_quality is the arc's weld_quality output;
    - ground_path / tip_condition are the A8 observable fields on the
      Ground Clamp / Contact Tip;
    - polarity_ok is the Ground Clamp's polarity field (the vocabulary's
      owner is the Ground Clamp);
    - input_voltage / cord_ok / bottle_level are the parameter fields the
      injections set (Welder.input_voltage, cord.cord_gauge, bottle.level).
    """
    by_name = {c.name: c.id for c in definition.components}
    return {
        "wire_out@feeder": f"{by_name['Wire Feed Mechanism']}.material_out",
        "wire_out@tip": f"{by_name['Contact Tip']}.material_out",
        "arc": f"{by_name['Welder']}.arc",
        "motor_motion": f"{by_name['Wire Feed Motor']}.motion_out",
        "spool_state": f"{by_name['Wire Spool']}.state",
        "gas_flow@gun": f"{by_name['MIG Gun']}.gas_out",
        "ground_path": f"{by_name['Ground Clamp']}.ground_path",
        "welder_on": f"{by_name['Trigger']}.power_out",
        "lcd": f"{by_name['LCD Display']}.display_out",
        "power_at_unit": f"{by_name['Welder']}.power_in",
        "tip_condition": f"{by_name['Contact Tip']}.tip_condition",
        "weld_quality": f"{by_name['Welding Arc']}.weld_quality",
        "bottle_level": f"{by_name['Shielding Gas Bottle']}.level",
        "polarity_ok": f"{by_name['Ground Clamp']}.polarity",
        "input_voltage": f"{by_name['Welder']}.input_voltage",
        "cord_ok": f"{by_name['Input Power Cord']}.cord_gauge",
        "current_setting": f"{by_name['Welder']}.current_setting",
    }


def build_test_models(definition: Any) -> dict[str, Callable[[dict[str, Any]], Any]]:
    """Discriminating tests as functions on twin state (v3 §6.2), from
    the vocabulary artifact — no invented values; an unmodelled test
    stays unmodelled, never guessed. The technique-only tests
    (workpiece_clean_check, gun_distance_check, wire_clean_check) have
    no model: they are only in technique rows, which are not scored.

    - step27_wood_test (p17 step 27): tension high -> unmodelled (the
      manual gives no wood-test outcome for too-tight tension); tension
      excess -> no_wire (the wire piles up at the feeder, never reaches
      the wood); tip absent + feeder normal -> no_wire; feeder low ->
      stops (covers tension low, groove false, spool tangled, starved
      feeder); feeder normal -> bends.
    - post_release_spin (p42 remedy 4 + p10): tension low -> spins
      (inferred, flagged); tension high -> no_spin (inferred, flagged).
    - groove_check (p12): groove false -> mismatch; true -> matches.
    - cable_check (p42): the gun's fault — bent/loose/damaged/open ->
      bent/loose/damaged/damaged; else straight.
    - liner_check (p42): the liner's fault/size — clogged/damaged/
      wrong_size; else clear.
    - spool_check (p42): the spool's fault — tangled; else ok.
    - connector_seated (p13): the gun's connector_seated — false -> no.
    - tip_size_check (p42): the tip's size_matches, or a worn tip (its
      hole is enlarged, so the size does not match) -> mismatch.
    - tip_condition (p41/p42): the tip's tip_condition field.
    - ground_check (p43): the ground's ground_path field.
    - polarity_check (p43): the ground's polarity field.
    - gas_flow_check (p43): the gun's gas_out — low/absent/ok.
    - bottle_level_check (p43): the bottle's level — empty/low/ok.
    - outlet_power_check (p43): the cord's power_in, or a tripped
      breaker (the breaker is upstream of the outlet) -> absent.
    - outlet_connection_check (p43): the cord's fault — not_connected -> no.
    - input_voltage_check (p7/p43): the Welder's input_voltage, or an
      active protection (low- or over-voltage protection implies the
      voltage was low) -> low.
    - mode_check (p43): the Welder's mode field.
    - thermal_warning_check (p43): the Welder's fault — thermal_trip ->
      present.
    - trigger_check (p43): the Trigger's fault — faulty -> faulty.
    - breaker_check (p43): the Reset Button's fault, or an absent cord
      power_in (an unpowered outlet implies the breaker tripped) -> tripped.
    - plug_rating_check (p7/p43): the cord's plug_rating field.
    - cord_check (p42/p43): the cord's fault/cord_gauge — not_seated /
      wrong_gauge / ok.
    - current_setting_check (p42): the Welder's current_setting field.
    """
    by_name = {c.name: c.id for c in definition.components}
    feeder_id = by_name["Wire Feed Mechanism"]
    gun_id = by_name["MIG Gun"]
    tip_id = by_name["Contact Tip"]
    liner_id = by_name["liner assembly"]
    spool_id = by_name["Wire Spool"]
    ground_id = by_name["Ground Clamp"]
    cord_id = by_name["Input Power Cord"]
    button_id = by_name["Reset Button"]
    bottle_id = by_name["Shielding Gas Bottle"]
    welder_id = by_name["Welder"]
    trigger_id = by_name["Trigger"]

    def _comp(context: dict[str, Any], component_id: str) -> dict[str, Any]:
        return ((context.get("state") or {}).get("components") or {}).get(component_id) or {}

    def step27_wood_test(context: dict[str, Any]) -> Any:
        feeder = _comp(context, feeder_id)
        tip = _comp(context, tip_id)
        if feeder.get("tension") == "high":
            return "unmodelled"
        if feeder.get("tension") == "excess":
            return "no_wire"
        if tip.get("material_out") == "absent" and feeder.get("material_out") == "normal":
            return "no_wire"
        if feeder.get("material_out") == "low":
            return "stops"
        if feeder.get("material_out") == "normal":
            return "bends"
        return "unmodelled"

    def post_release_spin(context: dict[str, Any]) -> Any:
        tension = _comp(context, feeder_id).get("tension")
        if tension == "low":
            return "spins"
        if tension == "high":
            return "no_spin"
        return "not_applicable"

    def groove_check(context: dict[str, Any]) -> Any:
        groove = _comp(context, feeder_id).get("groove_matches")
        if groove is False:
            return "mismatch"
        if groove is True:
            return "matches"
        return "not_applicable"

    def cable_check(context: dict[str, Any]) -> Any:
        fault = _comp(context, gun_id).get("fault")
        if fault == "bent":
            return "bent"
        if fault == "loose":
            return "loose"
        if fault in ("damaged", "open"):
            return "damaged"
        return "straight"

    def liner_check(context: dict[str, Any]) -> Any:
        liner = _comp(context, liner_id)
        if liner.get("fault") == "clogged":
            return "clogged"
        if liner.get("fault") == "damaged":
            return "damaged"
        if liner.get("size_matches") is False:
            return "wrong_size"
        return "clear"

    def spool_check(context: dict[str, Any]) -> Any:
        if _comp(context, spool_id).get("fault") == "tangled":
            return "tangled"
        return "ok"

    def connector_seated(context: dict[str, Any]) -> Any:
        if _comp(context, gun_id).get("connector_seated") is False:
            return "no"
        return "yes"

    def tip_size_check(context: dict[str, Any]) -> Any:
        tip = _comp(context, tip_id)
        if tip.get("size_matches") is False or tip.get("fault") == "worn":
            return "mismatch"
        return "matches"

    def tip_condition(context: dict[str, Any]) -> Any:
        return _comp(context, tip_id).get("tip_condition")

    def ground_check(context: dict[str, Any]) -> Any:
        return _comp(context, ground_id).get("ground_path")

    def polarity_check(context: dict[str, Any]) -> Any:
        return _comp(context, ground_id).get("polarity")

    def gas_flow_check(context: dict[str, Any]) -> Any:
        gas_out = _comp(context, gun_id).get("gas_out")
        if gas_out == "low":
            return "low"
        if gas_out == "absent":
            return "absent"
        return "ok"

    def bottle_level_check(context: dict[str, Any]) -> Any:
        level = _comp(context, bottle_id).get("level")
        if level == "empty":
            return "empty"
        if level == "low":
            return "low"
        return "ok"

    def outlet_power_check(context: dict[str, Any]) -> Any:
        cord = _comp(context, cord_id)
        button = _comp(context, button_id)
        if cord.get("power_in") == "absent" or button.get("fault") == "tripped":
            return "absent"
        return "present"

    def outlet_connection_check(context: dict[str, Any]) -> Any:
        if _comp(context, cord_id).get("fault") == "not_connected":
            return "no"
        return "yes"

    def input_voltage_check(context: dict[str, Any]) -> Any:
        welder = _comp(context, welder_id)
        if welder.get("input_voltage") == "low" or welder.get("protection") == "active":
            return "low"
        return "normal"

    def mode_check(context: dict[str, Any]) -> Any:
        return _comp(context, welder_id).get("mode")

    def thermal_warning_check(context: dict[str, Any]) -> Any:
        if _comp(context, welder_id).get("fault") == "thermal_trip":
            return "present"
        return "absent"

    def trigger_check(context: dict[str, Any]) -> Any:
        if _comp(context, trigger_id).get("fault") == "faulty":
            return "faulty"
        return "ok"

    def breaker_check(context: dict[str, Any]) -> Any:
        cord = _comp(context, cord_id)
        button = _comp(context, button_id)
        if button.get("fault") == "tripped" or cord.get("power_in") == "absent":
            return "tripped"
        return "ok"

    def plug_rating_check(context: dict[str, Any]) -> Any:
        return _comp(context, cord_id).get("plug_rating")

    def cord_check(context: dict[str, Any]) -> Any:
        cord = _comp(context, cord_id)
        if cord.get("fault") == "not_seated":
            return "not_seated"
        if cord.get("cord_gauge") == "wrong_gauge":
            return "wrong_gauge"
        return "ok"

    def current_setting_check(context: dict[str, Any]) -> Any:
        return _comp(context, welder_id).get("current_setting")

    return {"step27_wood_test": step27_wood_test,
            "post_release_spin": post_release_spin,
            "groove_check": groove_check,
            "cable_check": cable_check,
            "liner_check": liner_check,
            "spool_check": spool_check,
            "connector_seated": connector_seated,
            "tip_size_check": tip_size_check,
            "tip_condition": tip_condition,
            "ground_check": ground_check,
            "polarity_check": polarity_check,
            "gas_flow_check": gas_flow_check,
            "bottle_level_check": bottle_level_check,
            "outlet_power_check": outlet_power_check,
            "outlet_connection_check": outlet_connection_check,
            "input_voltage_check": input_voltage_check,
            "mode_check": mode_check,
            "thermal_warning_check": thermal_warning_check,
            "trigger_check": trigger_check,
            "breaker_check": breaker_check,
            "plug_rating_check": plug_rating_check,
            "cord_check": cord_check,
            "current_setting_check": current_setting_check}


def initial_twin_state(definition: Any) -> dict[str, Any]:
    """The twin's nominal state as a run_experiment initial state."""
    return {"components": {c.id: dict(c.initial_state) for c in definition.components},
            "signals": dict(definition.initial_signals or {})}


def snapshot_model_for_tournament() -> dict[str, Any]:
    """A snapshot-shaped model (failure modes + procedures) for the
    tournament's verify_single_cause, built from the registry: the case
    symptom is the failure-mode name, the cause texts are the registry's.
    The new fault_injection-first path does not need it, but the legacy
    fallback (causes with no injection) does."""
    import json
    from pathlib import Path
    registry_path = Path(__file__).resolve().parents[1] / "knowledge" / "repair_cases_omnipro.json"
    with open(registry_path, encoding="utf-8") as handle:
        registry = json.load(handle)
    failure_modes: list[dict[str, Any]] = []
    procedures: list[dict[str, Any]] = []
    for case in registry.get("cases") or []:
        failure_modes.append({
            "name": case.get("symptom") or "",
            "symptoms": [case.get("symptom") or ""],
            "entity_ids": [],
        })
        for cause in case.get("causes") or []:
            procedures.append({"name": cause.get("fix") or "",
                               "steps": [cause.get("fix") or ""]})
    return {"failure_modes": failure_modes, "procedures": procedures}
