"""Phase 3B archetype library — the library's contents (design v3 §2–§4).

One archetype is a *behaviour shape*: a fixed port signature (domains +
directions), a fixed state set, a propagation table shape, and a fault
vocabulary. A specific part is an archetype + bound parameters + its own
failure modes. The list is a candidate list (v3 §2.4); the oracle is
what tests it.

Only three archetypes are populated and reviewed in 3B — A2 ``switch``,
A3 ``converter``, A4 ``transmission``. 3C populates A1 ``source``, A7
``load``, A8 ``conductor``, A9 ``storage``, A10 ``valve`` and A11
``interface`` — only as oracle rows require them, never speculatively.
A5 ``sensor`` and A6 ``controller`` stay empty schema entries (A6 is the
honest gap: where the manual gives no logic map, controller outputs stay
unknown).

Propagation rows are data, not code. The compiler instantiates rows;
the engine executes the transitions the compiler emits from them
(v3 §5 as revised by the second Amendments section: the engine is the
propagator; the compiler emits ONE generic conditional transition per
row, never per-fault traces).

Row format (v3 §4.1, with the Amendments corrections):
- ``inputs``: a partial map, field -> qualitative value. Fields are
  input ports, parameters, or the special ``fault`` field. A port not
  listed is "don't care" for this row.
- ``fault``: ``"none"`` or a fault-state id from the archetype's fault
  vocabulary. A fault row overrides the nominal row for the same inputs.
- ``outputs``: output-port values this row produces. Every output port
  must be covered per (inputs, fault) combination the table covers;
  uncovered combinations are ``"unknown"``, never a silent default —
  the compiler generates explicit unknown-setting rows for them
  (``backend/simulation/lint.py``) and the lint verifies totality.
- ``next_state``: the component state after this row fires.
- ``sources`` / ``cases``: every row cites where it came from (manual
  page, archetype review id) and which top cases it serves. A row with
  no source fails review.

Qualitative values (v3 §3): per-domain ordered sets, plus ``"unknown"``
in every domain. ``unknown`` propagates as ``unknown``; it never becomes
``normal`` or ``absent`` by default. A fault override is NOT ``unknown``:
it is a named fault state with a defined output.

Spin correction (second Amendments, item 1 — v3 was wrong): correct
tension *prevents* continued spinning after the Gun Trigger is released
(p42 remedy), so spinning after release means tension too LOW (p10).
Case-1 encoding: (a) tension low — wood test ``stops`` (quoted p17),
spin ``spins`` (inferred, flagged in the vocabulary); (d) tension high
— wood test ``unmodelled``, spin ``no_spin`` (inferred, flagged). The
A4 rows encode tension high as ``material_out=low`` (case 1(d)). The
case-3 "excess pressure" row expected ``material_out=high`` for the SAME
injection (tension=high) in 3B — that conflict is RESOLVED in 3C by the
registry/vocabulary decision: case 3(a)'s injection is now
``tension=excess`` (a distinct value), served by ``transmission.t-excess``
(``material_out=high``). See PHASE3C_BUILD_NOTES.md.

Streaks (v3 §4.1 / §5.3): the converter's stalled -> overheated streak
(M3) is NOT implemented in 3B. No oracle row needs it, and composing a
``for_steps`` streak row with the stalled row's state effect needs a
state guard the current row format does not express cleanly; the
engine's ``for_steps`` mechanism itself is unit-tested separately
(``tests/test_phase3b_archetypes.py``). The stalled row sets heat
``normal``; overheating after N stalled steps is deferred, recorded in
the row's note.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ARCHETYPE_LIBRARY_VERSION = "3c-1.0.0"

#: Qualitative value sets per domain (v3 §3), plus "unknown" everywhere.
#: The labels are shared; their meanings are per-domain.
QUALITATIVE_DOMAINS: dict[str, list[Any]] = {
    # "very_low" is an input-voltage magnitude (3C oracle fix): the manual
    # gives the same injection (Welder input_voltage low) two different
    # symptoms — case 5(b) "Circuit supplies insufficient input voltage"
    # (the welder does not function at all) and case 9(a) "Incorrect line
    # voltage" (a weak arc). One deterministic row cannot satisfy both, so
    # the registry/vocabulary decision distinguishes them (like the 3C
    # tension=excess decision): case 5(b) is input_voltage=very_low (the
    # welder gets no usable power), case 9(a) stays input_voltage=low (the
    # welder runs, the arc is weak). See PHASE3C_BUILD_NOTES.md.
    "electrical": ["absent", "very_low", "low", "normal", "high"],
    "mechanical": ["absent", "low", "normal", "high"],
    "material": ["absent", "low", "normal", "high"],
    "signal": ["absent", "low", "high"],
    "thermal": ["normal", "high", "absent"],
    "level": ["empty", "low", "normal"],
    "boolean": [True, False],
    # "excess" is a tension value (3C: the case-3 'excess wire feed
    # pressure' injection is distinguished from case-1(d) 'tensioner too
    # tight'). It lives in the qualitative domain because the tension
    # parameter's domain is qualitative; the A8 gas_flow parameter shares
    # the domain but never takes 'excess'.
    "qualitative": ["low", "normal", "high", "excess"],
    # 3C domains, added only as oracle rows require them. The value
    # labels are taken from the vocabulary artifact (oracle_vocabulary_v1)
    # so the twin produces exactly the values the oracle expects.
    # arc: the welding arc's effect — "unstable" is a real arc value
    # (case 4), not a material value, so it gets its own domain.
    "arc": ["absent", "low", "normal", "unstable"],
    # display: the LCD's display_out (cases 5/6).
    "display": ["blank", "lit", "warning"],
    # weld_quality: the weld metal's quality (case 8).
    "weld_quality": ["normal", "degraded"],
    # ground_path: the ground clamp's ground-path quality (cases 4/7).
    "ground_path": ["good", "poor"],
    # tip_condition: the Contact Tip's condition (case 7).
    "tip_condition": ["ok", "wrong_size", "worn"],
    # correctness: plug rating (case 6) — "correct"/"wrong" per the
    # plug_rating_check test vocabulary.
    "correctness": ["correct", "wrong"],
    # cord_gauge: the cord's gauge/length (case 9) — "ok"/"wrong_gauge"
    # per the cord_ok observable and the cord_check test vocabulary.
    "cord_gauge": ["ok", "wrong_gauge"],
    # protection: the Welder's low-/over-voltage protection (case 5(d)).
    "protection": ["none", "active"],
}

#: Unit families for the §8 same-quantity rule (third Amendments, item 4):
#: a bound parameter counts as evidence only when it measures the same
#: quantity on the same component. Machine input voltage (p7 120/240 VAC)
#: is NEVER motor-rating evidence.
UNIT_FAMILIES: dict[str, set[str]] = {
    "voltage": {"v", "vac", "vdc", "volt", "volts"},
    "current": {"a", "amp", "amps", "ampere", "amperes"},
    "length": {"in", "inch", "inches", "mm", "cm", "m", "ft", "feet"},
    "pressure": {"psi", "bar", "kpa", "mpa"},
    "flow": {"cfh", "l/min", "lpm"},
    "speed": {"ipm", "rpm", "m/min"},
    "weight": {"lb", "lbs", "kg", "g"},
    "temperature": {"c", "f", "degc", "degf"},
}


class ArchetypePort(BaseModel):
    name: str
    direction: Literal["in", "out"]
    domain: str
    unit: str | None = None
    required: bool = True


class ArchetypeParameter(BaseModel):
    """A parameter slot on the archetype.

    ``nominal_default`` is the archetype-level nominal operating point
    (e.g. tension ``normal``) used by the compiler's nominal fixpoint
    when the model leaves the parameter unbound. It is NOT binding
    evidence and NOT a spec value: a rating has no nominal default and
    stays unbound until a nameplate/spec value for the component itself
    exists. ``required_for_signature`` means the parameter must be bound
    on the entity (from model data, never from the nominal default) for
    the entity to signature-match this archetype at all — this is what
    keeps A9 ``storage`` (level) distinct from A8 ``conductor`` (§2.4
    item 3). ``distinguishing`` marks a parameter that separates two
    archetypes sharing a port signature.
    """

    name: str
    domain: str
    values: list[Any] = Field(default_factory=list)
    unit_family: str | None = None
    nominal_default: Any = None
    has_nominal_default: bool = False
    required_for_signature: bool = False
    distinguishing: bool = False
    source: str = ""


class PropagationRow(BaseModel):
    row_id: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    # "none" or a fault-state id; None means "don't care" (used only by
    # compiler-generated totality rows — a generated row whose condition
    # does not constrain the fault must not claim fault == "none").
    fault: str | None = "none"
    outputs: dict[str, Any] = Field(default_factory=dict)
    next_state: str
    sources: list[str] = Field(default_factory=list)
    cases: list[str] = Field(default_factory=list)
    note: str = ""


class FailureModeDef(BaseModel):
    name: str
    fault_state: str | None = None
    parameter: str | None = None
    discriminating_test: str = ""
    expected_signature: dict[str, Any] = Field(default_factory=dict)
    cases: list[str] = Field(default_factory=list)


class Archetype(BaseModel):
    archetype_id: str
    version: str = ARCHETYPE_LIBRARY_VERSION
    human_name: str
    aliases: list[str] = Field(default_factory=list)
    ports: list[ArchetypePort] = Field(default_factory=list)
    states: list[str] = Field(default_factory=list)
    parameters: list[ArchetypeParameter] = Field(default_factory=list)
    propagation_table: list[PropagationRow] = Field(default_factory=list)
    failure_modes: list[FailureModeDef] = Field(default_factory=list)
    evidence_requirements: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    validation_history: dict[str, Any] = Field(
        default_factory=lambda: {"validated_count": 0, "rejected_count": 0,
                                 "field_instances": 0})

    @property
    def input_fields(self) -> dict[str, str]:
        """Input field name -> domain (input ports + parameters + fault)."""
        fields = {p.name: p.domain for p in self.ports if p.direction == "in"}
        fields.update({p.name: p.domain for p in self.parameters})
        return fields

    @property
    def output_ports(self) -> dict[str, str]:
        return {p.name: p.domain for p in self.ports if p.direction == "out"}

    @property
    def fault_values(self) -> list[str]:
        """Enumerable fault values for totality: "none" plus every fault
        state a row or failure mode names. Fault strings outside this set
        have no row by design — they read as uncovered, never guessed."""
        values = {"none"}
        for row in self.propagation_table:
            values.add(row.fault)
        for mode in self.failure_modes:
            if mode.fault_state:
                values.add(mode.fault_state)
        return sorted(values)

    def input_domain(self) -> dict[str, list[Any]]:
        """Enumerable input domain per input field (for the totality lint).

        Ports take their domain's values + "unknown"; parameters take
        their declared values + "unknown"; ``fault`` takes fault_values.
        """
        domain: dict[str, list[Any]] = {}
        for name, dom in self.input_fields.items():
            values = list(QUALITATIVE_DOMAINS.get(dom, []))
            if "unknown" not in values:
                values.append("unknown")
            domain[name] = values
        domain["fault"] = list(self.fault_values)
        return domain


# ---------------------------------------------------------------------------
# A2 — switch (relay / trigger / contactor / breaker)
# v3 §4.2 rows R1–R2b + stuck-open / stuck-closed fault overrides.
# ---------------------------------------------------------------------------
_SWITCH = Archetype(
    archetype_id="switch",
    human_name="switch",
    aliases=["relay", "trigger switch", "contactor", "breaker", "reset button"],
    ports=[
        ArchetypePort(name="coil_in", direction="in", domain="signal"),
        ArchetypePort(name="power_in", direction="in", domain="electrical"),
        ArchetypePort(name="power_out", direction="out", domain="electrical"),
    ],
    states=["open", "closed", "stuck_open", "stuck_closed", "unknown"],
    parameters=[],
    propagation_table=[
        PropagationRow(
            row_id="switch.r1.closed",
            inputs={"coil_in": "high", "power_in": "normal"},
            fault="none", outputs={"power_out": "normal"}, next_state="closed",
            sources=["v3 §4.2 R1", "owner-manual p43 case 5 (trigger/relay path)"],
            cases=["omni-05", "omni-06"]),
        PropagationRow(
            row_id="switch.r2.coil-absent",
            inputs={"coil_in": "absent"},
            fault="none", outputs={"power_out": "absent"}, next_state="open",
            sources=["v3 §4.2 R2"],
            cases=["omni-05"]),
        PropagationRow(
            row_id="switch.r2b.coil-low",
            inputs={"coil_in": "low"},
            fault="none", outputs={"power_out": "absent"}, next_state="open",
            sources=["v3 §4.2 R2 (signal low = not asserted; §3 signal domain)"],
            cases=["omni-05"]),
        PropagationRow(
            row_id="switch.r2c.power-absent",
            inputs={"power_in": "absent"},
            fault="none", outputs={"power_out": "absent"}, next_state="open",
            sources=["v3 §4.2 R2b"],
            cases=["omni-05", "omni-06"]),
        PropagationRow(
            row_id="switch.r2d.power-low",
            inputs={"power_in": "low", "coil_in": "high"},
            fault="none", outputs={"power_out": "low"}, next_state="closed",
            sources=["3C: a switch with a low input passes the degraded power "
                     "through (it does not invent a normal output, and it does "
                     "not open). Required by case 9(a)/(b) (input voltage low / "
                     "cord gauge wrong -> the arc reads low, not absent) and by "
                     "case 5(b) (the trigger passes the low voltage on; the "
                     "motor's enable-low row then turns the motor off)."],
            cases=["omni-05", "omni-09"]),
        PropagationRow(
            row_id="switch.r-faulty",
            inputs={},
            fault="faulty", outputs={"power_out": "absent"}, next_state="open",
            sources=["3C: owner-manual p43 case 5 cause 3 (Faulty or improperly "
                     "connected Trigger). A faulty trigger does not close, so no "
                     "power reaches the motor or the welding circuit."],
            cases=["omni-05"]),
        PropagationRow(
            row_id="switch.r-tripped",
            inputs={},
            fault="tripped", outputs={"power_out": "absent"}, next_state="open",
            sources=["3C: owner-manual p43 case 6 cause 4 (Circuit breaker has "
                     "tripped due to high input amperage). A tripped breaker is "
                     "an open switch. The Reset Button binds to A2 (alias)."],
            cases=["omni-06"]),
        PropagationRow(
            row_id="switch.r-stuck-open",
            inputs={},
            fault="stuck_open", outputs={"power_out": "absent"}, next_state="stuck_open",
            sources=["v3 §4.2 R-stuck-open (fault overrides the input: a stuck-open "
                     "switch outputs absent regardless of its coil input)"],
            cases=["omni-05"]),
        PropagationRow(
            row_id="switch.r-stuck-closed",
            inputs={},
            fault="stuck_closed", outputs={"power_out": "normal"}, next_state="stuck_closed",
            sources=["v3 §4.2 R-stuck-closed"],
            cases=["omni-05"]),
    ],
    failure_modes=[
        FailureModeDef(name="stuck_open", fault_state="stuck_open",
                       discriminating_test="coil signal present but no power out",
                       cases=["omni-05"]),
        FailureModeDef(name="stuck_closed", fault_state="stuck_closed",
                       discriminating_test="coil signal absent but power out present",
                       cases=["omni-05"]),
        FailureModeDef(name="faulty", fault_state="faulty",
                       discriminating_test="trigger_check (p43 case 5)",
                       cases=["omni-05"]),
        FailureModeDef(name="tripped", fault_state="tripped",
                       discriminating_test="breaker_check (p43 case 6)",
                       cases=["omni-06"]),
        FailureModeDef(name="no coil signal", fault_state=None,
                       discriminating_test="trigger_check (p43 case 5)",
                       cases=["omni-05"]),
    ],
    evidence_requirements={"min_ports": ["coil_in", "power_in", "power_out"]},
    provenance={"author": "Phase 3B (hand-written core)",
                "authoring_layer": "layer1-hand-written",
                "review": "3B review — see PHASE3B_BUILD_NOTES.md",
                "sources": ["phase3-design-v3-archetype-oracle.md §4.2"]},
)

# ---------------------------------------------------------------------------
# A3 — converter (DC motor / AC motor / fan / pump / spindle / solenoid)
# v3 §4.2 rows M1–M2b + M-no-power + M-stalled. M3 streak deferred (see
# module docstring). Rating stays unbound: p7's 120/240 VAC is the
# machine's input, not the motor's DC rating (third Amendments, item 4).
# ---------------------------------------------------------------------------
_CONVERTER = Archetype(
    archetype_id="converter",
    human_name="converter",
    aliases=["dc motor", "motor", "wire feed motor", "feed motor", "fan",
             "pump", "spindle", "solenoid"],
    ports=[
        ArchetypePort(name="power_in", direction="in", domain="electrical"),
        ArchetypePort(name="enable_in", direction="in", domain="signal"),
        ArchetypePort(name="motion_out", direction="out", domain="mechanical"),
        ArchetypePort(name="heat_out", direction="out", domain="thermal"),
    ],
    states=["off", "running", "stalled", "overheated", "unknown"],
    parameters=[
        ArchetypeParameter(name="rating", domain="electrical", values=[],
                           unit_family="voltage",
                           source="nameplate or spec value for the motor itself; "
                                  "machine input voltage (p7 120/240 VAC) is NEVER "
                                  "rating evidence (third Amendments, item 4)"),
    ],
    propagation_table=[
        PropagationRow(
            row_id="converter.m1.running",
            inputs={"power_in": "normal", "enable_in": "high"},
            fault="none", outputs={"motion_out": "normal", "heat_out": "normal"},
            next_state="running",
            sources=["v3 §4.2 M1", "owner-manual p42 case 1 premise (motor runs)"],
            cases=["omni-01", "omni-02", "omni-05"]),
        PropagationRow(
            row_id="converter.m2.power-absent",
            inputs={"power_in": "absent"},
            fault="none", outputs={"motion_out": "absent", "heat_out": "absent"},
            next_state="off",
            sources=["v3 §4.2 M2 (power absent vs enable absent kept apart, so "
                     "backtracking can distinguish case 5 from a trigger fault)"],
            cases=["omni-05", "omni-06"]),
        PropagationRow(
            row_id="converter.m2b.enable-absent",
            inputs={"enable_in": "absent"},
            fault="none", outputs={"motion_out": "absent", "heat_out": "absent"},
            next_state="off",
            sources=["v3 §4.2 M2b"],
            cases=["omni-05"]),
        PropagationRow(
            row_id="converter.m2c.enable-low",
            inputs={"enable_in": "low"},
            fault="none", outputs={"motion_out": "absent", "heat_out": "absent"},
            next_state="off",
            sources=["v3 §4.2 M2b (signal low = not asserted; §3 signal domain)"],
            cases=["omni-05"]),
        PropagationRow(
            row_id="converter.m-no-power.power-low",
            inputs={"power_in": "low", "enable_in": "high"},
            fault="none", outputs={"motion_out": "low", "heat_out": "normal"},
            next_state="running",
            sources=["v3 §4.2 M-no-power (degraded running)"],
            cases=["omni-09"],
            note="3B fix (13 Nov 2026): the row constrains enable_in=high. "
                 "Without it the row overlapped M2b/M2c (power low + enable "
                 "absent/low) with different outputs (motion low vs absent) "
                 "and the lint quarantined it as conflicting. A motor with "
                 "low power and no enable is off (M2b/M2c), not running "
                 "degraded — degraded running needs the enable asserted."),
        PropagationRow(
            row_id="converter.m-stalled",
            inputs={"power_in": "normal", "enable_in": "high"},
            fault="stalled", outputs={"motion_out": "absent", "heat_out": "normal"},
            next_state="stalled",
            sources=["v3 §4.2 M-stalled (immediate row only)"],
            cases=["omni-01"],
            note="M3 streak (stalled for N steps -> overheated) deferred in 3B: "
                 "no oracle row needs it; see module docstring."),
    ],
    failure_modes=[
        FailureModeDef(name="no power", fault_state=None,
                       discriminating_test="input_voltage_check (p7)",
                       cases=["omni-05", "omni-09"]),
        FailureModeDef(name="stalled", fault_state="stalled",
                       discriminating_test="motor_motion reads absent with power present",
                       cases=["omni-01"]),
        FailureModeDef(name="overheated", fault_state="overheated",
                       discriminating_test="thermal_warning_check (p43 case 5)",
                       cases=["omni-05"],
                       expected_signature={}),
        FailureModeDef(name="burnt_out", fault_state="burnt_out",
                       discriminating_test="motor never runs with power present",
                       cases=[]),
    ],
    evidence_requirements={"min_ports": ["power_in", "enable_in", "motion_out"]},
    provenance={"author": "Phase 3B (hand-written core)",
                "authoring_layer": "layer1-hand-written",
                "review": "3B review — see PHASE3B_BUILD_NOTES.md",
                "sources": ["phase3-design-v3-archetype-oracle.md §4.2"]},
)

# ---------------------------------------------------------------------------
# A4 — transmission (wire feeder / gearbox / belt drive / lead screw)
# v3 §4.2 rows T1–T3 + T-jam + T-idle, with the corrected spin encoding.
# ---------------------------------------------------------------------------
_TRANSMISSION = Archetype(
    archetype_id="transmission",
    human_name="transmission",
    aliases=["wire feeder", "wire feed mechanism", "wire feed unit",
             "wire feed assembly", "gearbox", "belt drive", "lead screw", "roller"],
    ports=[
        ArchetypePort(name="motion_in", direction="in", domain="mechanical"),
        ArchetypePort(name="material_in", direction="in", domain="material"),
        ArchetypePort(name="material_out", direction="out", domain="material"),
    ],
    states=["feeding", "slipping", "jammed", "idle", "unknown"],
    parameters=[
        ArchetypeParameter(
            name="tension", domain="qualitative",
            values=["low", "normal", "high", "excess"],
            nominal_default="normal", has_nominal_default=True,
            source="p17 step 27 operational definition (wire bends = tension OK; "
                   "wire stops = too low); p42 remedy 4 (correct tension prevents "
                   "continued spinning after trigger release). 3C: 'excess' added — "
                   "the case-3 'excess wire feed pressure' injection is distinguished "
                   "from the case-1(d) 'tensioner too tight' injection (both were "
                   "tension=high, but the manual gives them different feeder outputs: "
                   "high vs low). See the registry/vocabulary decision in "
                   "PHASE3C_BUILD_NOTES.md and the T-excess row."),
        ArchetypeParameter(
            name="groove_matches", domain="boolean", values=[True, False],
            nominal_default=True, has_nominal_default=True,
            source="p12 Feed Roller groove must match the wire size on the spool; p7 sizes"),
    ],
    propagation_table=[
        PropagationRow(
            row_id="transmission.t1.feeding",
            inputs={"motion_in": "normal", "material_in": "normal",
                    "tension": "normal", "groove_matches": True},
            fault="none", outputs={"material_out": "normal"}, next_state="feeding",
            sources=["v3 §4.2 T1", "owner-manual p17 step 27", "p12 roller"],
            cases=["omni-01", "omni-02"],
            note="3C: T1 now constrains material_in=normal. The spool can starve "
                 "the feeder (case 2(d): spool tangled -> material_in=low), so the "
                 "nominal row must say the material is present; the starved rows "
                 "(T-material-low / T-material-absent) own the low/absent input."),
        PropagationRow(
            row_id="transmission.t2-low.slipping",
            inputs={"motion_in": "normal", "material_in": "normal", "tension": "low"},
            fault="none", outputs={"material_out": "low"}, next_state="slipping",
            sources=["v3 §4.2 T2-low", "owner-manual p42 case 1 cause 1; p17 step 27 "
                     "(wire stops = tension too low, quoted)"],
            cases=["omni-01", "omni-02"]),
        PropagationRow(
            row_id="transmission.t2-groove.slipping",
            inputs={"motion_in": "normal", "material_in": "normal",
                    "groove_matches": False},
            fault="none", outputs={"material_out": "low"}, next_state="slipping",
            sources=["v3 §4.2 T2-groove", "owner-manual p42 case 1 cause 2; p12"],
            cases=["omni-01", "omni-02"]),
        PropagationRow(
            row_id="transmission.t3-high.slipping",
            inputs={"motion_in": "normal", "material_in": "normal", "tension": "high"},
            fault="none", outputs={"material_out": "low"}, next_state="slipping",
            sources=["v3 §4.2 T3-high", "owner-manual p42 case 1 cause 4 "
                     "(Feed Tensioner too tight); remedy: loosen so it applies only "
                     "enough pressure to prevent continued spinning after release"],
            cases=["omni-01"],
            note="RESOLVED in 3C (was the known conflict in 3B): the case-3 "
                 "'excess wire feed pressure' injection is now tension=excess "
                 "(registry + vocabulary decision, see PHASE3C_BUILD_NOTES.md), "
                 "so T3-high (tension=high -> low, case 1(d)) and T-excess "
                 "(tension=excess -> high, case 3(a)) no longer share an injection."),
        PropagationRow(
            row_id="transmission.t-excess.overfeeding",
            inputs={"motion_in": "normal", "material_in": "normal",
                    "tension": "excess", "groove_matches": True},
            fault="none", outputs={"material_out": "high"}, next_state="feeding",
            sources=["3C: owner-manual p42 case 3 cause 1 (Excess wire feed "
                     "pressure). The registry/vocabulary decision distinguishes "
                     "this injection from case 1(d): excess = the wire feeds too "
                     "much and piles up (bird's nest); high = the tensioner is "
                     "overtightened and the wire slips."],
            cases=["omni-03"]),
        PropagationRow(
            row_id="transmission.t-material-low.starved",
            inputs={"motion_in": "normal", "material_in": "low",
                    "tension": "normal", "groove_matches": True},
            fault="none", outputs={"material_out": "low"}, next_state="slipping",
            sources=["3C: a transmission cannot output what it does not receive. "
                     "Required by case 2(d) (spool tangled -> the spool's "
                     "material_out=low -> the feeder's material_in=low -> the "
                     "feeder's material_out=low)."],
            cases=["omni-02"]),
        PropagationRow(
            row_id="transmission.t-material-absent.starved",
            inputs={"motion_in": "normal", "material_in": "absent",
                    "tension": "normal", "groove_matches": True},
            fault="none", outputs={"material_out": "absent"}, next_state="idle",
            sources=["3C: a transmission with no material input cannot feed. "
                     "Completeness row for the starved input (no oracle row "
                     "injects it directly; the totality lint covers the rest)."],
            cases=["omni-02"]),
        PropagationRow(
            row_id="transmission.t-motion-low.slow",
            inputs={"motion_in": "low", "material_in": "normal",
                    "tension": "normal", "groove_matches": True},
            fault="none", outputs={"material_out": "low"}, next_state="feeding",
            sources=["3C oracle fix: v3 §4.2 — the converter's M2b-low row "
                     "gives motion_out=low when the motor runs on low power, "
                     "but the transmission had no row for a slow motion input, "
                     "so the combination fell to a generated totality row and "
                     "the feeder read unknown (which propagated as unknown all "
                     "the way to the arc in case 9). A transmission fed slowly "
                     "feeds slowly: material_out=low, still feeding. Required "
                     "by case 9(a)/(b) (input voltage low / cord gauge wrong)."],
            cases=["omni-09"]),
        PropagationRow(
            row_id="transmission.t-jam.jammed",
            inputs={"motion_in": "normal"},
            fault="jammed", outputs={"material_out": "absent"}, next_state="jammed",
            sources=["v3 §4.2 T-jam"],
            cases=["omni-02", "omni-03"]),
        PropagationRow(
            row_id="transmission.t-idle.idle",
            inputs={"motion_in": "absent"},
            fault="none", outputs={"material_out": "absent"}, next_state="idle",
            sources=["v3 §4.2 T-idle"],
            cases=["omni-01", "omni-05"]),
    ],
    failure_modes=[
        FailureModeDef(name="slipping", fault_state=None, parameter="tension",
                       discriminating_test="p17 step 27: feed wire against wood 2-3in "
                       "away; bends = tension OK, stops = tension too low",
                       expected_signature={"observables": {"wire_out": "low", "arc": "absent"},
                                           "test_outcomes": {"step27_wood_test": "stops"}},
                       cases=["omni-01", "omni-02"]),
        FailureModeDef(name="jammed", fault_state="jammed",
                       discriminating_test="liner_check / cable_check (p42)",
                       cases=["omni-02", "omni-03"]),
        FailureModeDef(name="worn", fault_state="worn",
                       discriminating_test="liner_check (p42)",
                       cases=["omni-02"]),
        FailureModeDef(name="mis-adjusted", fault_state="mis-adjusted",
                       discriminating_test="p17 step 27",
                       cases=["omni-01"]),
    ],
    evidence_requirements={"min_ports": ["motion_in", "material_out"],
                           "min_parameters": ["tension"]},
    provenance={"author": "Phase 3B (hand-written core)",
                "authoring_layer": "layer1-hand-written",
                "review": "3B review — see PHASE3B_BUILD_NOTES.md",
                "sources": ["phase3-design-v3-archetype-oracle.md §4.2"]},
)


# ---------------------------------------------------------------------------
# A1 — source (mains input / power cord / power supply / Welder power stage)
# 3C: cases 5–6/9. The Welder binds to A1 as its power stage (it takes
# mains power in and produces switched welding power out); the Input Power
# Cord binds to A1 as a pure source (its power_in is the mains, an
# unmodelled nominal input). Both instances carry all A1 parameters as
# fields (unmodelled nominal inputs where not applicable) so the rows fire
# uniformly — the same pattern as A8's gas ports (see _CONDUCTOR).
# ---------------------------------------------------------------------------
_SOURCE = Archetype(
    archetype_id="source",
    human_name="source",
    aliases=["mains input", "power source", "psu", "battery", "power supply"],
    ports=[
        ArchetypePort(name="power_in", direction="in", domain="electrical"),
        ArchetypePort(name="power_out", direction="out", domain="electrical"),
        ArchetypePort(name="material_out", direction="out", domain="material"),
    ],
    states=["on", "off", "low", "unknown"],
    parameters=[
        ArchetypeParameter(
            name="plug_rating", domain="correctness", values=["correct", "wrong"],
            nominal_default="correct", has_nominal_default=True,
            source="p43 case 6 cause 3 (Plug does not have correct rating); p7 specs"),
        ArchetypeParameter(
            name="cord_gauge", domain="cord_gauge", values=["ok", "wrong_gauge"],
            nominal_default="ok", has_nominal_default=True,
            source="p42 case 9 cause 2 (Improper gauge or length of cord); "
                   "the cord_ok observable reads ok/wrong_gauge"),
        ArchetypeParameter(
            name="input_voltage", domain="electrical",
            values=["absent", "very_low", "low", "normal", "high"],
            nominal_default="normal", has_nominal_default=True,
            source="p43 case 5 cause 2 (Circuit supplies insufficient input "
                   "voltage); p42 case 9 cause 1 (Incorrect line voltage); p7 specs"),
        ArchetypeParameter(
            name="protection", domain="protection", values=["none", "active"],
            nominal_default="none", has_nominal_default=True,
            source="p43 case 5 cause 4 (Machine is in low- or over-voltage "
                   "protection)"),
    ],
    propagation_table=[
        PropagationRow(
            row_id="source.s1.on",
            inputs={"power_in": "normal", "plug_rating": "correct",
                    "cord_gauge": "ok", "input_voltage": "normal",
                    "protection": "none"},
            fault="none", outputs={"power_out": "normal", "material_out": "normal"},
            next_state="on",
            sources=["3C: nominal source (mains present, plug and cord correct, "
                     "line voltage normal, no protection active)"],
            cases=["omni-05", "omni-06", "omni-09"]),
        PropagationRow(
            row_id="source.s-power-absent.off",
            inputs={"power_in": "absent", "plug_rating": "correct",
                    "cord_gauge": "ok", "input_voltage": "normal",
                    "protection": "none"},
            fault="none", outputs={"power_out": "absent", "material_out": "normal"},
            next_state="off",
            sources=["3C: owner-manual p43 case 6 cause 2 (Outlet is unpowered). "
                     "The 3C vocabulary correction puts this fault on the cord's "
                     "power_in (the outlet), not the Welder's link-target power_in."],
            cases=["omni-06"]),
        PropagationRow(
            row_id="source.s-power-low.low",
            inputs={"power_in": "low", "plug_rating": "correct",
                    "cord_gauge": "ok", "input_voltage": "normal",
                    "protection": "none"},
            fault="none", outputs={"power_out": "low", "material_out": "normal"},
            next_state="low",
            sources=["3C: a source with a low input passes the degraded power "
                     "through (it does not invent a normal output). Required by "
                     "case 9(b) (cord gauge wrong -> the cord's power_out=low -> "
                     "the Welder's power_in=low -> the arc reads low)."],
            cases=["omni-09"]),
        PropagationRow(
            row_id="source.s-plug-wrong.off",
            inputs={"power_in": "normal", "plug_rating": "wrong",
                    "cord_gauge": "ok", "input_voltage": "normal",
                    "protection": "none"},
            fault="none", outputs={"power_out": "absent", "material_out": "normal"},
            next_state="off",
            sources=["3C: owner-manual p43 case 6 cause 3 (Plug does not have "
                     "correct rating) — the wrong plug cannot deliver power."],
            cases=["omni-06"]),
        PropagationRow(
            row_id="source.s-cord-wrong.low",
            inputs={"power_in": "normal", "plug_rating": "correct",
                    "cord_gauge": "wrong_gauge", "input_voltage": "normal",
                    "protection": "none"},
            fault="none", outputs={"power_out": "low", "material_out": "normal"},
            next_state="low",
            sources=["3C: owner-manual p42 case 9 cause 2 (Improper gauge or "
                     "length of cord) — the wrong cord delivers degraded power, "
                     "so the arc reads low (not absent)."],
            cases=["omni-09"]),
        PropagationRow(
            row_id="source.s-input-low.low",
            inputs={"power_in": "normal", "plug_rating": "correct",
                    "cord_gauge": "ok", "input_voltage": "low",
                    "protection": "none"},
            fault="none", outputs={"power_out": "low", "material_out": "normal"},
            next_state="low",
            sources=["3C: owner-manual p42 case 9 cause 1 (Incorrect line "
                     "voltage) — the arc reads low. RESOLVED in the 3C oracle "
                     "fix: the same injection was case 5(b) (Circuit supplies "
                     "insufficient input voltage), which expects the welder to "
                     "not function at all (arc absent, welder_on absent). The "
                     "registry/vocabulary decision distinguishes them (like the "
                     "3C tension=excess decision): case 5(b) is now "
                     "input_voltage=very_low (the s-input-very-low row below), "
                     "case 9(a) stays input_voltage=low. See "
                     "PHASE3C_BUILD_NOTES.md."],
            cases=["omni-09"]),
        PropagationRow(
            row_id="source.s-input-very-low.off",
            inputs={"power_in": "normal", "plug_rating": "correct",
                    "cord_gauge": "ok", "input_voltage": "very_low",
                    "protection": "none"},
            fault="none", outputs={"power_out": "absent", "material_out": "normal"},
            next_state="off",
            sources=["3C oracle fix: owner-manual p43 case 5 cause 2 (Circuit "
                     "supplies insufficient input voltage) — the welder does not "
                     "function at all (welder_on absent, lcd blank, arc absent). "
                     "The registry/vocabulary decision distinguishes this injection "
                     "from case 9(a) (Incorrect line voltage, input_voltage=low, "
                     "weak arc): insufficient voltage is below the level at which "
                     "the welder runs at all. See PHASE3C_BUILD_NOTES.md."],
            cases=["omni-05"]),
        PropagationRow(
            row_id="source.s-protection.off",
            inputs={"power_in": "normal", "plug_rating": "correct",
                    "cord_gauge": "ok", "input_voltage": "normal",
                    "protection": "active"},
            fault="none", outputs={"power_out": "absent", "material_out": "normal"},
            next_state="off",
            sources=["3C: owner-manual p43 case 5 cause 4 (Machine is in low- "
                     "or over-voltage protection) — the protection cuts the power."],
            cases=["omni-05"]),
        PropagationRow(
            row_id="source.s-thermal-trip.off",
            inputs={},
            fault="thermal_trip", outputs={"power_out": "absent",
                                           "material_out": "normal"},
            next_state="off",
            sources=["3C: owner-manual p43 case 5 cause 1 (Tripped thermal "
                     "protection device) — the thermal trip cuts the power."],
            cases=["omni-05"]),
        PropagationRow(
            row_id="source.s-not-connected.off",
            inputs={},
            fault="not_connected", outputs={"power_out": "absent",
                                            "material_out": "normal"},
            next_state="off",
            sources=["3C: owner-manual p43 case 6 cause 1 (Unit is not connected "
                     "to outlet properly)."],
            cases=["omni-06"]),
        PropagationRow(
            row_id="source.s-not-seated.off",
            inputs={},
            fault="not_seated", outputs={"power_out": "absent",
                                         "material_out": "normal"},
            next_state="off",
            sources=["3C: owner-manual p43 case 6 cause 5 (Input Power Cord is "
                     "not seated properly)."],
            cases=["omni-06"]),
    ],
    failure_modes=[
        FailureModeDef(name="thermal_trip", fault_state="thermal_trip",
                       discriminating_test="thermal_warning_check (p43 case 5)",
                       cases=["omni-05"]),
        FailureModeDef(name="not_connected", fault_state="not_connected",
                       discriminating_test="outlet_connection_check (p43 case 6)",
                       cases=["omni-06"]),
        FailureModeDef(name="not_seated", fault_state="not_seated",
                       discriminating_test="cord_check (p43 case 6)",
                       cases=["omni-06"]),
        FailureModeDef(name="low input voltage", fault_state=None,
                       parameter="input_voltage",
                       discriminating_test="input_voltage_check (p7)",
                       cases=["omni-05", "omni-09"]),
        FailureModeDef(name="protection active", fault_state=None,
                       parameter="protection",
                       discriminating_test="thermal_warning_check (p43 case 5)",
                       cases=["omni-05"]),
    ],
    evidence_requirements={"min_ports": ["power_in", "power_out"]},
    provenance={"author": "Phase 3C (hand-written core)",
                "authoring_layer": "layer1-hand-written",
                "review": "3C review — see PHASE3C_BUILD_NOTES.md",
                "sources": ["phase3-design-v3-archetype-oracle.md §2.1"]},
)

# ---------------------------------------------------------------------------
# A7 — load (welding arc). 3C: cases 4/7/9.
#
# SCOPE DECISION (3C): the 3C A7 is the welding-arc load. Its gas_in,
# ground_in and weld_quality ports are arc-specific — the arc's stability
# genuinely depends on the shielding gas and on the ground (the return
# path), and the weld metal's quality is the arc's effect. A general load
# (heater, lamp) would not need them; that generalisation is a later
# refinement if a non-arc load ever needs A7. The gas_in/ground_in ports
# are required so the rows fire uniformly (a component without them could
# not use the gas/ground rows at all).
#
# The arc's energy_in comes from the tip (the welding current), its
# material_in from the tip (the wire), its gas_in from the gun, and its
# ground_in from the ground clamp. The effect_out is the arc; the
# weld_quality is the weld metal (degraded whenever the arc is not
# normal — a non-normal arc makes a degraded weld).
# ---------------------------------------------------------------------------
_LOAD = Archetype(
    archetype_id="load",
    human_name="load",
    aliases=["welding arc", "arc", "heater", "lamp", "spindle load"],
    ports=[
        ArchetypePort(name="energy_in", direction="in", domain="electrical"),
        ArchetypePort(name="material_in", direction="in", domain="material"),
        ArchetypePort(name="gas_in", direction="in", domain="material"),
        ArchetypePort(name="ground_in", direction="in", domain="electrical"),
        ArchetypePort(name="effect_out", direction="out", domain="arc"),
        ArchetypePort(name="weld_quality", direction="out", domain="weld_quality",
                      required=False),
    ],
    states=["normal", "absent", "low", "unstable", "unknown"],
    parameters=[],
    propagation_table=[
        PropagationRow(
            row_id="load.l-nominal.normal",
            inputs={"energy_in": "normal", "material_in": "normal",
                    "gas_in": "normal", "ground_in": "normal"},
            fault="none", outputs={"effect_out": "normal", "weld_quality": "normal"},
            next_state="normal",
            sources=["3C: nominal arc (energy, wire, gas and ground all normal)"],
            cases=["omni-01", "omni-02", "omni-03", "omni-04", "omni-05",
                   "omni-06", "omni-07", "omni-08", "omni-09"]),
        PropagationRow(
            row_id="load.l-energy-absent.absent",
            inputs={"energy_in": "absent", "material_in": "normal",
                    "gas_in": "normal", "ground_in": "normal"},
            fault="none", outputs={"effect_out": "absent",
                                   "weld_quality": "degraded"},
            next_state="absent",
            sources=["3C: no welding current, no arc. Required by case 7(b)/(c) "
                     "(tip size/worn -> the tip passes no energy) and case 5(c) "
                     "(trigger faulty -> the tip gets no energy)."],
            cases=["omni-05", "omni-07"]),
        PropagationRow(
            row_id="load.l-energy-low.low",
            inputs={"energy_in": "low", "material_in": "normal",
                    "gas_in": "normal", "ground_in": "normal"},
            fault="none", outputs={"effect_out": "low", "weld_quality": "degraded"},
            next_state="low",
            sources=["3C: degraded welding current -> a weak arc. Required by "
                     "case 9(a)/(b) (input voltage low / cord gauge wrong). The "
                     "arc's energy_in is the effective welding energy; the "
                     "ground/gas inputs stay nominal here, which is what "
                     "distinguishes a weak arc (case 9) from an unstable one "
                     "(case 4, where the ground or gas is degraded)."],
            cases=["omni-09"]),
        PropagationRow(
            row_id="load.l-material-absent.absent",
            inputs={"energy_in": "normal", "material_in": "absent",
                    "gas_in": "normal", "ground_in": "normal"},
            fault="none", outputs={"effect_out": "absent",
                                   "weld_quality": "degraded"},
            next_state="absent",
            sources=["3C: no wire at the tip, no arc. Required by case 1(c) "
                     "(gun open) and cases 2/3 (wire blocked upstream)."],
            cases=["omni-01", "omni-02", "omni-03"]),
        PropagationRow(
            row_id="load.l-material-low.unstable",
            inputs={"energy_in": "normal", "material_in": "low",
                    "gas_in": "normal", "ground_in": "normal"},
            fault="none", outputs={"effect_out": "unstable",
                                   "weld_quality": "degraded"},
            next_state="unstable",
            sources=["3C: the wire arrives poorly (low) -> the arc is unstable, "
                     "not absent. Required by case 4(b) (tip worn -> wire_out@tip "
                     "low, arc unstable)."],
            cases=["omni-04"]),
        PropagationRow(
            row_id="load.l-gas-low.unstable",
            inputs={"energy_in": "normal", "material_in": "normal",
                    "gas_in": "low", "ground_in": "normal"},
            fault="none", outputs={"effect_out": "unstable",
                                   "weld_quality": "degraded"},
            next_state="unstable",
            sources=["3C: insufficient shielding gas -> the arc is unstable and "
                     "the weld is porous. Required by case 4(g) (gas flow low) "
                     "and case 8(b) (not enough shielding gas -> weld degraded)."],
            cases=["omni-04", "omni-08"]),
        PropagationRow(
            row_id="load.l-gas-absent.unstable",
            inputs={"energy_in": "normal", "material_in": "normal",
                    "gas_in": "absent", "ground_in": "normal"},
            fault="none", outputs={"effect_out": "unstable",
                                   "weld_quality": "degraded"},
            next_state="unstable",
            sources=["3C: no shielding gas -> the arc is unstable (it still "
                     "ignites; the weld is porous). Required by case 8(a) "
                     "(gas bottle empty -> weld_quality degraded). The arc "
                     "itself is not observed in case 8, but the weld_quality "
                     "output is."],
            cases=["omni-08"]),
        PropagationRow(
            row_id="load.l-ground-low.unstable",
            inputs={"energy_in": "normal", "material_in": "normal",
                    "gas_in": "normal", "ground_in": "low"},
            fault="none", outputs={"effect_out": "unstable",
                                   "weld_quality": "degraded"},
            next_state="unstable",
            sources=["3C: a poor ground (high resistance) degrades the return "
                     "path -> the arc is unstable. Required by case 4(h) (poor "
                     "connection with workpiece). NOTE: the same injection is "
                     "case 7(a) (improper ground -> arc absent); the twin picks "
                     "'unstable' (the degraded reading), so case 7(a) is a "
                     "documented O2 mismatch on the arc port."],
            cases=["omni-04", "omni-07"]),
        PropagationRow(
            row_id="load.l-ground-absent.absent",
            inputs={"energy_in": "normal", "material_in": "normal",
                    "gas_in": "normal", "ground_in": "absent"},
            fault="none", outputs={"effect_out": "absent",
                                   "weld_quality": "degraded"},
            next_state="absent",
            sources=["3C: a fully open ground -> no complete circuit, no arc. "
                     "Completeness row (no oracle row injects a fully open "
                     "ground; the poor-connection fault gives ground_in=low)."],
            cases=["omni-07"]),
        # 3C oracle fix — double-deviation rows. A fault on the gun/tip
        # degrades TWO of the arc's inputs at once (the fault row sets both
        # material_out and energy_out), so the arc sees a combination no
        # single-deviation row covers and falls to a generated totality row
        # (effect=unknown). These rows own the combinations the oracle rows
        # actually produce. They are exclusive with the single-deviation
        # rows (each constrains the other input to nominal there), so the
        # lint's exclusion check still holds.
        PropagationRow(
            row_id="load.l-material-absent-gas-low.absent",
            inputs={"energy_in": "normal", "material_in": "absent",
                    "gas_in": "low", "ground_in": "normal"},
            fault="none", outputs={"effect_out": "absent",
                                   "weld_quality": "degraded"},
            next_state="absent",
            sources=["3C oracle fix: no wire at the tip -> no arc, whatever "
                     "the gas is doing. Required by case 3(c) (gun connector "
                     "not fully inserted: the tip receives no wire and low gas, "
                     "so the arc's combination is material absent + gas low)."],
            cases=["omni-03"]),
        PropagationRow(
            row_id="load.l-energy-absent-material-absent.absent",
            inputs={"energy_in": "absent", "material_in": "absent",
                    "gas_in": "normal", "ground_in": "normal"},
            fault="none", outputs={"effect_out": "absent",
                                   "weld_quality": "degraded"},
            next_state="absent",
            sources=["3C oracle fix: no welding current AND no wire at the "
                     "tip -> no arc. Required by case 1(c) (gun open), cases "
                     "2(a)/(b)/(c) and 3(b)/(c)/(d) (wire blocked upstream), "
                     "cases 5(a)/(d) (welder power cut) and 7(b) (wrong-size "
                     "tip blocks wire and current)."],
            cases=["omni-01", "omni-02", "omni-03", "omni-05", "omni-07"]),
        PropagationRow(
            row_id="load.l-energy-low-material-absent.unstable",
            inputs={"energy_in": "low", "material_in": "absent",
                    "gas_in": "normal", "ground_in": "normal"},
            fault="none", outputs={"effect_out": "unstable",
                                   "weld_quality": "degraded"},
            next_state="unstable",
            sources=["3C oracle fix: degraded current and no wire -> the arc "
                     "flickers (unstable), it does not vanish outright. "
                     "Required by case 4(e) (damaged gun: energy low, wire "
                     "blocked)."],
            cases=["omni-04"]),
        PropagationRow(
            row_id="load.l-energy-absent-material-low.unstable",
            inputs={"energy_in": "absent", "material_in": "low",
                    "gas_in": "normal", "ground_in": "normal"},
            fault="none", outputs={"effect_out": "unstable",
                                   "weld_quality": "degraded"},
            next_state="unstable",
            sources=["3C oracle fix: no current but the wire arrives poorly "
                     "-> intermittent contact, the arc flickers (unstable). "
                     "Required by case 4(b) (worn tip: material low, energy "
                     "absent). NOTE: the same combination is case 7(c) "
                     "(excessively worn tip), which expects arc=absent; the "
                     "twin picks 'unstable' (the case-4 reading), so case 7(c) "
                     "is a documented O2 mismatch on the arc port."],
            cases=["omni-04", "omni-07"]),
        PropagationRow(
            row_id="load.l-energy-low-material-low.unstable",
            inputs={"energy_in": "low", "material_in": "low",
                    "gas_in": "normal", "ground_in": "normal"},
            fault="none", outputs={"effect_out": "unstable",
                                   "weld_quality": "degraded"},
            next_state="unstable",
            sources=["3C oracle fix: degraded current and a poorly arriving "
                     "wire -> the arc is unstable. Required by case 4(d) "
                     "(loose gun: the gun passes low energy and low wire, so "
                     "the tip receives both degraded)."],
            cases=["omni-04"]),
    ],
    failure_modes=[
        FailureModeDef(name="no arc", fault_state=None,
                       discriminating_test="arc reads absent with wire at the tip",
                       cases=["omni-07"]),
        FailureModeDef(name="weak arc", fault_state=None,
                       discriminating_test="input_voltage_check (p7)",
                       cases=["omni-09"]),
        FailureModeDef(name="unstable arc", fault_state=None,
                       discriminating_test="ground_check / gas_flow_check (p42/p43)",
                       cases=["omni-04"]),
    ],
    evidence_requirements={"min_ports": ["energy_in", "material_in", "effect_out"]},
    provenance={"author": "Phase 3C (hand-written core)",
                "authoring_layer": "layer1-hand-written",
                "review": "3C review — see PHASE3C_BUILD_NOTES.md",
                "sources": ["phase3-design-v3-archetype-oracle.md §2.1"]},
)

# ---------------------------------------------------------------------------
# A8 — conductor (MIG Gun / liner / Contact Tip / Ground Clamp / cable).
# 3C: cases 1–4/7. In 3B the gun's wire path was a reviewed link
# fault-override; in 3C it moves into proper A8 rows and the link becomes
# an identity passthrough (feeder -> liner -> gun -> tip -> arc).
#
# PORTS: a conductor carries material (wire), energy (welding current)
# and gas (shielding gas). ALL A8 instances carry all six ports as fields
# (unmodelled nominal inputs where the part does not really carry that
# flow — e.g. the liner's gas ports, the ground's material ports) so the
# rows fire uniformly. A row that conditioned on a port an instance lacks
# would never fire for it, and the wire path would break. The unread
# passthrough values are harmless (nothing consumes them); the wart is
# recorded in PHASE3C_BUILD_NOTES.md.
#
# OBSERVABLE FIELDS: tip_condition and ground_path are optional A8 output
# ports whose values are the vocabulary's observable values (ok/wrong_size/
# worn; good/poor). The A8 rows set them; instances that do not have the
# port still get the field (unread). The engine creates the field from the
# row effect; the observable mapping reads it on the tip / ground clamp.
#
# ROW SHAPE: nominal rows are a full partition (each constrains every
# input field to its nominal value except the one deviation it owns), so
# overlapping nominal rows can never disagree. Fault rows are disjoint
# from nominal rows by the fault field, so they need not constrain the
# inputs (an open conductor blocks everything regardless of its input).
# ---------------------------------------------------------------------------
_CONDUCTOR = Archetype(
    archetype_id="conductor",
    human_name="conductor",
    aliases=["wire", "cable", "gun liner", "connector", "ground clamp path",
             "mig gun", "contact tip", "liner assembly"],
    ports=[
        ArchetypePort(name="material_in", direction="in", domain="material"),
        ArchetypePort(name="material_out", direction="out", domain="material"),
        ArchetypePort(name="energy_in", direction="in", domain="electrical"),
        ArchetypePort(name="energy_out", direction="out", domain="electrical"),
        ArchetypePort(name="gas_in", direction="in", domain="material"),
        ArchetypePort(name="gas_out", direction="out", domain="material"),
        ArchetypePort(name="tip_condition", direction="out", domain="tip_condition",
                      required=False),
        ArchetypePort(name="ground_path", direction="out", domain="ground_path",
                      required=False),
    ],
    states=["ok", "open", "bent", "loose", "damaged", "clogged", "worn",
            "poor_connection", "short", "high_resistance", "unknown"],
    parameters=[
        ArchetypeParameter(
            name="size_matches", domain="boolean", values=[True, False],
            nominal_default=True, has_nominal_default=True,
            source="p42 case 2 cause 3 (liner too small); p42 case 3 cause 2 "
                   "(incorrect Contact Tip size); p42 case 7 cause 2 (improperly "
                   "sized Contact Tip); p12 / p7 sizes"),
        ArchetypeParameter(
            name="connector_seated", domain="boolean", values=[True, False],
            nominal_default=True, has_nominal_default=True,
            source="p13 steps 13-14 (Gun Cable Connector fully inserted into the "
                   "Wire Feed mechanism socket); p42 case 3 cause 3"),
        ArchetypeParameter(
            name="gas_flow", domain="qualitative", values=["low", "normal", "high"],
            nominal_default="normal", has_nominal_default=True,
            source="p43 case 8 cause 2 (Not enough or too much shielding gas); "
                   "p42 case 4 cause 7 (gas coverage insufficient)"),
    ],
    propagation_table=[
        PropagationRow(
            row_id="conductor.c-nominal.ok",
            inputs={"material_in": "normal", "energy_in": "normal",
                    "gas_in": "normal", "size_matches": True,
                    "connector_seated": True, "gas_flow": "normal"},
            fault="none",
            outputs={"material_out": "normal", "energy_out": "normal",
                     "gas_out": "normal", "tip_condition": "ok",
                     "ground_path": "good"},
            next_state="ok",
            sources=["3C: nominal conductor (passes wire, current and gas)"],
            cases=["omni-01", "omni-02", "omni-03", "omni-04", "omni-07",
                   "omni-08"]),
        PropagationRow(
            row_id="conductor.c-material-low.low",
            inputs={"material_in": "low", "energy_in": "normal",
                    "gas_in": "normal", "size_matches": True,
                    "connector_seated": True, "gas_flow": "normal"},
            fault="none",
            outputs={"material_out": "low", "energy_out": "normal",
                     "gas_out": "normal", "tip_condition": "ok",
                     "ground_path": "good"},
            next_state="ok",
            sources=["3C: a conductor passes on the material it receives. "
                     "Required by the liner->gun->tip chain (a starved liner "
                     "starves the tip)."],
            cases=["omni-02", "omni-03"]),
        PropagationRow(
            row_id="conductor.c-material-absent.absent",
            inputs={"material_in": "absent", "energy_in": "normal",
                    "gas_in": "normal", "size_matches": True,
                    "connector_seated": True, "gas_flow": "normal"},
            fault="none",
            outputs={"material_out": "absent", "energy_out": "normal",
                     "gas_out": "normal", "tip_condition": "ok",
                     "ground_path": "good"},
            next_state="ok",
            sources=["3C: no material in, no material out. Required by the "
                     "liner->gun->tip chain (a blocked liner blocks the tip)."],
            cases=["omni-01", "omni-02", "omni-03"]),
        PropagationRow(
            row_id="conductor.c-material-high.blocked",
            inputs={"material_in": "high", "energy_in": "normal",
                    "gas_in": "normal", "size_matches": True,
                    "connector_seated": True, "gas_flow": "normal"},
            fault="none",
            outputs={"material_out": "absent", "energy_out": "normal",
                     "gas_out": "normal", "tip_condition": "ok",
                     "ground_path": "good"},
            next_state="ok",
            sources=["3C: owner-manual p42 case 3 cause 1 (Excess wire feed "
                     "pressure). The feeder pushes too much wire; it piles up "
                     "(bird's nest) and nothing reaches the tip. The gun is the "
                     "choke point: too much material arriving means the wire "
                     "cannot exit."],
            cases=["omni-03"]),
        PropagationRow(
            row_id="conductor.c-energy-low.low",
            inputs={"material_in": "normal", "energy_in": "low",
                    "gas_in": "normal", "size_matches": True,
                    "connector_seated": True, "gas_flow": "normal"},
            fault="none",
            outputs={"material_out": "normal", "energy_out": "low",
                     "gas_out": "normal", "tip_condition": "ok",
                     "ground_path": "good"},
            next_state="ok",
            sources=["3C: a conductor passes on the energy it receives. "
                     "Required by the trigger->gun->tip->arc energy chain "
                     "(a low supply gives a low arc)."],
            cases=["omni-05", "omni-09"]),
        PropagationRow(
            row_id="conductor.c-energy-absent.absent",
            inputs={"material_in": "normal", "energy_in": "absent",
                    "gas_in": "normal", "size_matches": True,
                    "connector_seated": True, "gas_flow": "normal"},
            fault="none",
            outputs={"material_out": "normal", "energy_out": "absent",
                     "gas_out": "normal", "tip_condition": "ok",
                     "ground_path": "good"},
            next_state="ok",
            sources=["3C: no energy in, no energy out. Required by case 5(c) "
                     "(trigger faulty -> the tip gets no energy -> no arc) and "
                     "case 7 (the tip's energy is what ignites the arc)."],
            cases=["omni-05", "omni-07"]),
        PropagationRow(
            row_id="conductor.c-gas-low.low",
            inputs={"material_in": "normal", "energy_in": "normal",
                    "gas_in": "low", "size_matches": True,
                    "connector_seated": True, "gas_flow": "normal"},
            fault="none",
            outputs={"material_out": "normal", "energy_out": "normal",
                     "gas_out": "low", "tip_condition": "ok",
                     "ground_path": "good"},
            next_state="ok",
            sources=["3C: a conductor passes on the gas it receives. Required "
                     "by the bottle->valve->gun->arc gas chain."],
            cases=["omni-08"]),
        PropagationRow(
            row_id="conductor.c-gas-absent.absent",
            inputs={"material_in": "normal", "energy_in": "normal",
                    "gas_in": "absent", "size_matches": True,
                    "connector_seated": True, "gas_flow": "normal"},
            fault="none",
            outputs={"material_out": "normal", "energy_out": "normal",
                     "gas_out": "absent", "tip_condition": "ok",
                     "ground_path": "good"},
            next_state="ok",
            sources=["3C: no gas in, no gas out. Required by case 8(a) (gas "
                     "bottle empty -> gas_flow@gun absent)."],
            cases=["omni-08"]),
        PropagationRow(
            row_id="conductor.c-gasflow-low.low",
            inputs={"material_in": "normal", "energy_in": "normal",
                    "gas_in": "normal", "size_matches": True,
                    "connector_seated": True, "gas_flow": "low"},
            fault="none",
            outputs={"material_out": "normal", "energy_out": "normal",
                     "gas_out": "low", "tip_condition": "ok",
                     "ground_path": "good"},
            next_state="ok",
            sources=["3C: owner-manual p43 case 8 cause 2 (Not enough shielding "
                     "gas) and p42 case 4 cause 7 (gas coverage insufficient). "
                     "The gun's gas_flow parameter is low -> the gas at the gun "
                     "is low, while the wire and current pass normally."],
            cases=["omni-04", "omni-08"]),
        PropagationRow(
            row_id="conductor.c-size.wrong-size",
            inputs={"material_in": "normal", "energy_in": "normal",
                    "gas_in": "normal", "size_matches": False,
                    "connector_seated": True, "gas_flow": "normal"},
            fault="none",
            outputs={"material_out": "absent", "energy_out": "absent",
                     "gas_out": "normal", "tip_condition": "wrong_size",
                     "ground_path": "good"},
            next_state="ok",
            sources=["3C: owner-manual p42 case 2 cause 3 (liner too small for "
                     "the wire) and case 3 cause 2 (incorrect Contact Tip size). "
                     "A wrong-size conductor blocks the material and does not "
                     "conduct properly (no energy out -> no arc). NOTE: the same "
                     "injection is case 7(b) (improperly sized tip), which expects "
                     "wire_out@tip=normal; the twin picks 'absent' (the blocked "
                     "reading, which matches the majority of the size rows), so "
                     "case 7(b) is a documented O2 mismatch on the tip port."],
            cases=["omni-02", "omni-03", "omni-07"]),
        PropagationRow(
            row_id="conductor.c-connector.not-seated",
            inputs={"material_in": "normal", "energy_in": "normal",
                    "gas_in": "normal", "size_matches": True,
                    "connector_seated": False, "gas_flow": "normal"},
            fault="none",
            outputs={"material_out": "absent", "energy_out": "normal",
                     "gas_out": "low", "tip_condition": "ok",
                     "ground_path": "good"},
            next_state="ok",
            sources=["3C: owner-manual p13 steps 13-14 (Gun Cable Connector not "
                     "fully inserted -> gas leaks, preventing shielding gas from "
                     "reaching the arc) and p42 case 3 cause 3. The wire cannot "
                     "pass and the gas leaks (low), while the current is unaffected."],
            cases=["omni-03"]),
        # 3C oracle fix — double-deviation rows. A fault on an upstream
        # conductor degrades TWO of the next conductor's inputs at once (the
        # fault row sets both material_out and energy_out), so the downstream
        # conductor sees a combination no single-deviation row covers and
        # falls to a generated totality row (outputs=unknown). These rows own
        # the combinations the oracle rows actually produce. They are
        # exclusive with the single-deviation rows (each constrains the other
        # input to nominal there), so the lint's exclusion check holds.
        PropagationRow(
            row_id="conductor.c-material-absent-gas-low.absent",
            inputs={"material_in": "absent", "energy_in": "normal",
                    "gas_in": "low", "size_matches": True,
                    "connector_seated": True, "gas_flow": "normal"},
            fault="none",
            outputs={"material_out": "absent", "energy_out": "normal",
                     "gas_out": "low", "tip_condition": "ok",
                     "ground_path": "good"},
            next_state="ok",
            sources=["3C oracle fix: no material in, low gas in -> no material "
                     "out, low gas out (the gas passes on degraded). Required "
                     "by case 3(c) (gun connector not fully inserted: the gun "
                     "passes no wire and low gas, so the tip receives material "
                     "absent + gas low)."],
            cases=["omni-03"]),
        PropagationRow(
            row_id="conductor.c-energy-low-material-low.low",
            inputs={"material_in": "low", "energy_in": "low",
                    "gas_in": "normal", "size_matches": True,
                    "connector_seated": True, "gas_flow": "normal"},
            fault="none",
            outputs={"material_out": "low", "energy_out": "low",
                     "gas_out": "normal", "tip_condition": "ok",
                     "ground_path": "good"},
            next_state="ok",
            sources=["3C oracle fix: a conductor passes on the degraded "
                     "material and energy it receives. Required by case 4(d) "
                     "(loose gun: the gun passes low wire and low current, so "
                     "the tip receives both degraded and passes both on low)."],
            cases=["omni-04"]),
        PropagationRow(
            row_id="conductor.c-energy-absent-material-absent.absent",
            inputs={"material_in": "absent", "energy_in": "absent",
                    "gas_in": "normal", "size_matches": True,
                    "connector_seated": True, "gas_flow": "normal"},
            fault="none",
            outputs={"material_out": "absent", "energy_out": "absent",
                     "gas_out": "normal", "tip_condition": "ok",
                     "ground_path": "good"},
            next_state="ok",
            sources=["3C oracle fix: no material and no energy in, none out. "
                     "Required by case 1(c) (gun open), cases 2(a)/(b)/(c) and "
                     "3(b)/(c)/(d) (wire and current blocked upstream) and "
                     "7(b) (wrong-size tip blocks both)."],
            cases=["omni-01", "omni-02", "omni-03", "omni-07"]),
        PropagationRow(
            row_id="conductor.c-energy-low-material-absent.absent",
            inputs={"material_in": "absent", "energy_in": "low",
                    "gas_in": "normal", "size_matches": True,
                    "connector_seated": True, "gas_flow": "normal"},
            fault="none",
            outputs={"material_out": "absent", "energy_out": "low",
                     "gas_out": "normal", "tip_condition": "ok",
                     "ground_path": "good"},
            next_state="ok",
            sources=["3C oracle fix: no material in, degraded energy in -> "
                     "no material out, degraded energy out. Required by case "
                     "4(e) (damaged gun: the gun passes no wire and low "
                     "current, so the tip receives both and passes them on)."],
            cases=["omni-04"]),
        PropagationRow(
            row_id="conductor.c-open.open",
            inputs={},
            fault="open", outputs={"material_out": "absent", "energy_out": "absent",
                                   "gas_out": "normal", "tip_condition": "ok",
                                   "ground_path": "good"},
            next_state="open",
            sources=["3C: owner-manual p42 case 1 cause 3 (Damaged MIG Gun, cable, "
                     "or liner assembly). An open conductor passes neither wire "
                     "nor current. In 3B this was the reviewed feeder->gun link "
                     "fault-override; in 3C it is a proper A8 row."],
            cases=["omni-01"]),
        PropagationRow(
            row_id="conductor.c-bent.bent",
            inputs={},
            fault="bent", outputs={"material_out": "absent", "energy_out": "normal",
                                   "gas_out": "normal", "tip_condition": "ok",
                                   "ground_path": "good"},
            next_state="bent",
            sources=["3C: owner-manual p42 case 2 cause 1 (Gun cable is severely "
                     "bent and Wire Feed mechanism cannot feed wire). A bent cable "
                     "blocks the wire but still conducts current."],
            cases=["omni-02"]),
        PropagationRow(
            row_id="conductor.c-loose.loose",
            inputs={},
            fault="loose", outputs={"material_out": "low", "energy_out": "low",
                                    "gas_out": "normal", "tip_condition": "ok",
                                    "ground_path": "good"},
            next_state="loose",
            sources=["3C: owner-manual p42 case 4 cause 4 (Loose MIG Gun cable or "
                     "ground cable). A loose connection passes wire and current "
                     "poorly (low) -> the arc is unstable. NOTE: the vocabulary "
                     "also expects ground_path=poor for this row (the 'or ground "
                     "cable' half of the cause), but the ground clamp is a separate "
                     "component the gun's fault cannot set — a documented O2 "
                     "mismatch on the ground_path port."],
            cases=["omni-04"]),
        PropagationRow(
            row_id="conductor.c-damaged.damaged",
            inputs={},
            fault="damaged", outputs={"material_out": "absent", "energy_out": "low",
                                      "gas_out": "normal", "tip_condition": "ok",
                                      "ground_path": "good"},
            next_state="damaged",
            sources=["3C: owner-manual p42 case 4 cause 5 (Damaged MIG Gun or "
                     "loose connection within Gun) and case 3 cause 4 (Damaged "
                     "liner). A damaged conductor blocks the wire but still "
                     "conducts some current (low) -> the arc is unstable, not "
                     "absent. This is what distinguishes 'damaged' (energy low) "
                     "from 'open' (energy absent)."],
            cases=["omni-03", "omni-04"]),
        PropagationRow(
            row_id="conductor.c-clogged.clogged",
            inputs={},
            fault="clogged", outputs={"material_out": "absent",
                                      "energy_out": "normal", "gas_out": "normal",
                                      "tip_condition": "ok", "ground_path": "good"},
            next_state="clogged",
            sources=["3C: owner-manual p42 case 2 cause 2 (Gun liner is clogged "
                     "or worn). A clogged liner blocks the wire; the current is "
                     "unaffected (the liner carries no current)."],
            cases=["omni-02"]),
        PropagationRow(
            row_id="conductor.c-worn.worn",
            inputs={},
            fault="worn", outputs={"material_out": "low", "energy_out": "absent",
                                   "gas_out": "normal", "tip_condition": "worn",
                                   "ground_path": "good"},
            next_state="worn",
            sources=["3C: owner-manual p42 case 4 cause 2 (Incorrect Contact Tip "
                     "or liner size or excessive wear) and case 7 cause 3 "
                     "(Excessively worn Contact Tip). A worn tip passes wire "
                     "poorly (low) and does not conduct enough to ignite the arc "
                     "(energy absent). NOTE: the same injection is case 4(b) "
                     "(arc unstable) and case 7(c) (arc absent); the twin picks "
                     "energy absent, so case 4(b) is a documented O2 mismatch on "
                     "the arc port and case 7(c) is a documented O2 mismatch on "
                     "the tip port (it expects wire_out@tip=normal)."],
            cases=["omni-04", "omni-07"]),
        PropagationRow(
            row_id="conductor.c-poor-connection.poor",
            inputs={},
            fault="poor_connection",
            outputs={"material_out": "normal", "energy_out": "low",
                     "gas_out": "normal", "tip_condition": "ok",
                     "ground_path": "poor"},
            next_state="poor_connection",
            sources=["3C: owner-manual p42 case 4 cause 8 (Poor connection with "
                     "workpiece) and p43 case 7 cause 1 (Improper ground "
                     "connection). A poor ground is high resistance: it degrades "
                     "the return path (energy low -> the arc is unstable), it "
                     "does not open it. NOTE: the same injection is case 7(a) "
                     "(arc absent); the twin picks 'unstable', so case 7(a) is a "
                     "documented O2 mismatch on the arc port."],
            cases=["omni-04", "omni-07"]),
        PropagationRow(
            row_id="conductor.c-short.short",
            inputs={},
            fault="short", outputs={"material_out": "absent", "energy_out": "absent",
                                    "gas_out": "normal", "tip_condition": "ok",
                                    "ground_path": "good"},
            next_state="short",
            sources=["3C: completeness — a shorted conductor passes nothing. "
                     "No oracle row injects a short."],
            cases=[]),
        PropagationRow(
            row_id="conductor.c-high-resistance.degraded",
            inputs={},
            fault="high_resistance",
            outputs={"material_out": "low", "energy_out": "low",
                     "gas_out": "normal", "tip_condition": "ok",
                     "ground_path": "good"},
            next_state="high_resistance",
            sources=["3C: completeness — a high-resistance conductor passes "
                     "wire and current poorly. No oracle row injects it."],
            cases=[]),
    ],
    failure_modes=[
        FailureModeDef(name="open", fault_state="open",
                       discriminating_test="cable_check (p42)",
                       cases=["omni-01"]),
        FailureModeDef(name="bent", fault_state="bent",
                       discriminating_test="cable_check (p42)",
                       cases=["omni-02"]),
        FailureModeDef(name="loose", fault_state="loose",
                       discriminating_test="cable_check (p42)",
                       cases=["omni-04"]),
        FailureModeDef(name="damaged", fault_state="damaged",
                       discriminating_test="cable_check / liner_check (p42)",
                       cases=["omni-03", "omni-04"]),
        FailureModeDef(name="clogged", fault_state="clogged",
                       discriminating_test="liner_check (p42)",
                       cases=["omni-02"]),
        FailureModeDef(name="worn", fault_state="worn",
                       discriminating_test="tip_condition (p41/p42)",
                       cases=["omni-04", "omni-07"]),
        FailureModeDef(name="poor_connection", fault_state="poor_connection",
                       discriminating_test="ground_check (p43)",
                       cases=["omni-04", "omni-07"]),
        FailureModeDef(name="wrong size", fault_state=None, parameter="size_matches",
                       discriminating_test="tip_size_check / liner_check (p42)",
                       cases=["omni-02", "omni-03", "omni-07"]),
        FailureModeDef(name="connector not seated", fault_state=None,
                       parameter="connector_seated",
                       discriminating_test="connector_seated (p13)",
                       cases=["omni-03"]),
        FailureModeDef(name="low gas flow", fault_state=None, parameter="gas_flow",
                       discriminating_test="gas_flow_check (p43)",
                       cases=["omni-04", "omni-08"]),
    ],
    evidence_requirements={"min_ports": ["material_in", "material_out",
                                         "energy_in", "energy_out"]},
    provenance={"author": "Phase 3C (hand-written core)",
                "authoring_layer": "layer1-hand-written",
                "review": "3C review — see PHASE3C_BUILD_NOTES.md",
                "sources": ["phase3-design-v3-archetype-oracle.md §2.1"]},
)

# ---------------------------------------------------------------------------
# A9 — storage (Wire Spool / Shielding Gas Bottle). 3C: case 2(d) + case 8(a).
# A9 carries the optional supply output (13 Nov 2026 amendment): the spool
# supplies wire (material_out) and the bottle supplies gas (supply_out).
# The level parameter is required_for_signature + distinguishing (it is
# what keeps A9 distinct from A8, which shares the material in/out ports).
# ---------------------------------------------------------------------------
_STORAGE = Archetype(
    archetype_id="storage",
    human_name="storage",
    aliases=["wire spool", "spool", "gas bottle", "shielding gas bottle",
             "coolant tank"],
    ports=[
        ArchetypePort(name="material_in", direction="in", domain="material"),
        ArchetypePort(name="material_out", direction="out", domain="material"),
        ArchetypePort(name="supply_out", direction="out", domain="material",
                      required=False),
    ],
    states=["normal", "low", "empty", "tangled", "unknown"],
    parameters=[
        ArchetypeParameter(
            name="level", domain="level", values=["empty", "low", "normal"],
            nominal_default="normal", has_nominal_default=True,
            required_for_signature=True, distinguishing=True,
            source="A9 carries an optional supply output (13 Nov 2026 amendment): "
                   "one archetype covers the gas bottle's level ('empty bottle' -> "
                   "case 8) and its supply ('no gas flow'). A1 vs A9 remain "
                   "distinguished by the level state + material-in port."),
    ],
    propagation_table=[
        PropagationRow(
            row_id="storage.s-nominal.normal",
            inputs={"material_in": "normal", "level": "normal"},
            fault="none", outputs={"material_out": "normal", "supply_out": "normal"},
            next_state="normal",
            sources=["3C: nominal storage (loaded / full)"],
            cases=["omni-02", "omni-08"]),
        PropagationRow(
            row_id="storage.s-level-low.low",
            inputs={"material_in": "normal", "level": "low"},
            fault="none", outputs={"material_out": "low", "supply_out": "low"},
            next_state="low",
            sources=["3C: a low store supplies poorly. Completeness row (no "
                     "oracle row injects level=low; the empty row is case 8(a))."],
            cases=["omni-08"]),
        PropagationRow(
            row_id="storage.s-level-empty.empty",
            inputs={"material_in": "normal", "level": "empty"},
            fault="none", outputs={"material_out": "absent", "supply_out": "absent"},
            next_state="empty",
            sources=["3C: owner-manual p43 case 8 cause 1 (Shielding gas bottle "
                     "is empty) — no gas flows. The bottle's supply_out is the "
                     "gas chain's source."],
            cases=["omni-08"]),
        PropagationRow(
            row_id="storage.s-tangled.tangled",
            inputs={"material_in": "normal", "level": "normal"},
            fault="tangled", outputs={"material_out": "low", "supply_out": "low"},
            next_state="tangled",
            sources=["3C: owner-manual p42 case 2 cause 4 (Wire is tangled on "
                     "the spool) — the spool still feeds some wire (low), not "
                     "none. The feeder's starved row (T-material-low) turns the "
                     "low input into a low feeder output."],
            cases=["omni-02"]),
    ],
    failure_modes=[
        FailureModeDef(name="empty", fault_state=None, parameter="level",
                       discriminating_test="bottle_level_check (p43)",
                       cases=["omni-08"]),
        FailureModeDef(name="tangled", fault_state="tangled",
                       discriminating_test="spool_check (p42)",
                       cases=["omni-02"]),
    ],
    evidence_requirements={"min_ports": ["material_in", "material_out"]},
    provenance={"author": "Phase 3C (hand-written core)",
                "authoring_layer": "layer1-hand-written",
                "review": "3C review — see PHASE3C_BUILD_NOTES.md",
                "sources": ["phase3-design-v3-archetype-oracle.md §2.1"]},
)

# ---------------------------------------------------------------------------
# A10 — valve (gas valve / regulator). 3C: case 8 (gas flow). The valve is
# the gas chain's control point: bottle (A9 supply_out) -> valve
# (fluid_in -> fluid_out) -> gun (gas_in). Its control_in is the gas
# solenoid signal — an unmodelled nominal input (high: the gas is on
# during welding) in the proof twin, because the controller that drives it
# is A6 (the honest gap).
# ---------------------------------------------------------------------------
_VALVE = Archetype(
    archetype_id="valve",
    human_name="valve",
    aliases=["gas valve", "coolant valve", "regulator"],
    ports=[
        ArchetypePort(name="fluid_in", direction="in", domain="material"),
        ArchetypePort(name="control_in", direction="in", domain="signal"),
        ArchetypePort(name="fluid_out", direction="out", domain="material"),
    ],
    states=["open", "closed", "stuck_open", "stuck_closed", "leaking", "unknown"],
    parameters=[],
    propagation_table=[
        PropagationRow(
            row_id="valve.v-open.open",
            inputs={"fluid_in": "normal", "control_in": "high"},
            fault="none", outputs={"fluid_out": "normal"}, next_state="open",
            sources=["3C: nominal valve (gas present, solenoid energised)"],
            cases=["omni-08"]),
        PropagationRow(
            row_id="valve.v-closed.closed",
            inputs={"fluid_in": "normal", "control_in": "absent"},
            fault="none", outputs={"fluid_out": "absent"}, next_state="closed",
            sources=["3C: no control signal, the valve is closed."],
            cases=["omni-08"]),
        PropagationRow(
            row_id="valve.v-no-fluid.closed",
            inputs={"fluid_in": "absent", "control_in": "high"},
            fault="none", outputs={"fluid_out": "absent"}, next_state="closed",
            sources=["3C: no gas in (bottle empty), no gas out. Required by "
                     "case 8(a) (gas bottle empty -> gas_flow@gun absent)."],
            cases=["omni-08"]),
        PropagationRow(
            row_id="valve.v-stuck-closed.stuck",
            inputs={},
            fault="stuck_closed", outputs={"fluid_out": "absent"},
            next_state="stuck_closed",
            sources=["3C: a stuck-closed valve passes no gas regardless of its "
                     "inputs. Completeness row (no oracle row injects it)."],
            cases=["omni-08"]),
        PropagationRow(
            row_id="valve.v-stuck-open.stuck",
            inputs={},
            fault="stuck_open", outputs={"fluid_out": "normal"},
            next_state="stuck_open",
            sources=["3C: a stuck-open valve passes gas regardless of its "
                     "control input. Completeness row (no oracle row injects it)."],
            cases=["omni-08"]),
    ],
    failure_modes=[
        FailureModeDef(name="stuck_closed", fault_state="stuck_closed",
                       discriminating_test="gas_flow_check (p43)",
                       cases=["omni-08"]),
        FailureModeDef(name="stuck_open", fault_state="stuck_open",
                       discriminating_test="gas_flow_check (p43)",
                       cases=["omni-08"]),
    ],
    evidence_requirements={"min_ports": ["fluid_in", "control_in", "fluid_out"]},
    provenance={"author": "Phase 3C (hand-written core)",
                "authoring_layer": "layer1-hand-written",
                "review": "3C review — see PHASE3C_BUILD_NOTES.md",
                "sources": ["phase3-design-v3-archetype-oracle.md §2.1"]},
)

# ---------------------------------------------------------------------------
# A11 — interface (LCD Display). 3C: cases 5/6. The LCD's power_in comes
# from the Welder's power_out; its warning_in is the protection/thermal
# warning signal from the controller — an unmodelled nominal input
# (absent) in the proof twin, because the controller that raises it is A6
# (the honest gap). So the LCD can show lit/blank, but the warning value
# is unmodelled in 3C: cases 5(a)/(d) (lcd=warning) are documented O2
# mismatches on the lcd port.
# ---------------------------------------------------------------------------
_INTERFACE = Archetype(
    archetype_id="interface",
    human_name="interface",
    aliases=["lcd", "lcd display", "display", "panel", "hmi"],
    ports=[
        ArchetypePort(name="power_in", direction="in", domain="electrical"),
        ArchetypePort(name="warning_in", direction="in", domain="signal"),
        ArchetypePort(name="display_out", direction="out", domain="display"),
    ],
    states=["lit", "blank", "warning", "wrong_reading", "unknown"],
    parameters=[],
    propagation_table=[
        PropagationRow(
            row_id="interface.i-nominal.lit",
            inputs={"power_in": "normal", "warning_in": "absent"},
            fault="none", outputs={"display_out": "lit"}, next_state="lit",
            sources=["3C: nominal LCD (powered, no warning). Required by "
                     "case 5(c) (trigger faulty -> lcd lit)."],
            cases=["omni-05", "omni-06"]),
        PropagationRow(
            row_id="interface.i-power-absent.blank",
            inputs={"power_in": "absent", "warning_in": "absent"},
            fault="none", outputs={"display_out": "blank"}, next_state="blank",
            sources=["3C: no power, the LCD is blank. Required by case 6 "
                     "(LCD does not light) and case 5(b) (insufficient input "
                     "voltage -> the Welder's power_out is low -> see "
                     "i-power-low)."],
            cases=["omni-05", "omni-06"]),
        PropagationRow(
            row_id="interface.i-power-low.blank",
            inputs={"power_in": "low", "warning_in": "absent"},
            fault="none", outputs={"display_out": "blank"}, next_state="blank",
            sources=["3C: a low supply cannot light the LCD. Required by case "
                     "5(b) (input voltage low -> the Welder's power_out=low -> "
                     "lcd blank)."],
            cases=["omni-05"]),
        PropagationRow(
            row_id="interface.i-warning.warning",
            inputs={"power_in": "normal", "warning_in": "high"},
            fault="none", outputs={"display_out": "warning"}, next_state="warning",
            sources=["3C: a warning signal with full power -> the LCD shows a "
                     "warning. The warning_in is unmodelled in 3C (A6 gap), so "
                     "this row never fires on the proof twin; it exists so the "
                     "archetype is total and the row is ready when A6 models "
                     "the warning signal."],
            cases=["omni-05"]),
        PropagationRow(
            row_id="interface.i-warning-low-power.blank",
            inputs={"power_in": "low", "warning_in": "high"},
            fault="none", outputs={"display_out": "blank"}, next_state="blank",
            sources=["3C: a warning with a low supply cannot light the LCD. "
                     "Completeness row."],
            cases=["omni-05"]),
        PropagationRow(
            row_id="interface.i-warning-no-power.blank",
            inputs={"power_in": "absent", "warning_in": "high"},
            fault="none", outputs={"display_out": "blank"}, next_state="blank",
            sources=["3C: a warning with no supply cannot light the LCD. "
                     "Completeness row."],
            cases=["omni-05", "omni-06"]),
    ],
    failure_modes=[
        FailureModeDef(name="blank display", fault_state=None,
                       discriminating_test="outlet_power_check (p43 case 6)",
                       cases=["omni-06"]),
        FailureModeDef(name="warning displayed", fault_state=None,
                       discriminating_test="thermal_warning_check (p43 case 5)",
                       cases=["omni-05"]),
    ],
    evidence_requirements={"min_ports": ["power_in", "display_out"]},
    provenance={"author": "Phase 3C (hand-written core)",
                "authoring_layer": "layer1-hand-written",
                "review": "3C review — see PHASE3C_BUILD_NOTES.md",
                "sources": ["phase3-design-v3-archetype-oracle.md §2.1"]},
)


def _empty_archetype(archetype_id: str, human_name: str, aliases: list[str],
                     ports: list[ArchetypePort], states: list[str],
                     parameters: list[ArchetypeParameter] | None = None,
                     note: str = "") -> Archetype:
    """An archetype that exists as a schema entry only: ports/states
    declared, no rows. Binding can honestly say 'bound, no rows yet'
    (or 'no match') for it; the compiler emits no transitions from it.
    3C populates these only as oracle rows require them."""
    return Archetype(
        archetype_id=archetype_id, human_name=human_name, aliases=aliases,
        ports=ports, states=states, parameters=parameters or [],
        propagation_table=[], failure_modes=[],
        provenance={"author": "Phase 3B (schema entry only — no rows)",
                    "authoring_layer": "layer1-hand-written",
                    "review": "3B review — see PHASE3B_BUILD_NOTES.md",
                    "sources": ["phase3-design-v3-archetype-oracle.md §2.1"],
                    "note": note},
    )


ARCHETYPES: dict[str, Archetype] = {
    "source": _SOURCE,
    "switch": _SWITCH,
    "converter": _CONVERTER,
    "transmission": _TRANSMISSION,
    "sensor": _empty_archetype(
        "sensor", "sensor",
        ["temp sensor", "current sensor", "limit switch", "encoder"],
        [ArchetypePort(name="physical_in", direction="in", domain="mechanical"),
         ArchetypePort(name="signal_out", direction="out", domain="signal")],
        ["normal", "stuck_high", "stuck_low", "no_signal", "unknown"],
        note="Not needed by any OMNIPRO oracle row in 3B/3C scope."),
    "controller": _empty_archetype(
        "controller", "controller",
        ["control board", "plc", "mcu", "nc controller"],
        [ArchetypePort(name="signal_in", direction="in", domain="signal"),
         ArchetypePort(name="control_out", direction="out", domain="signal"),
         ArchetypePort(name="alarm_out", direction="out", domain="signal")],
        ["normal", "wrong_output", "no_output", "unknown"],
        note="A6 is the honest gap (v3 §2.1): a controller's table is a logic "
             "map; where the manual gives no map, outputs stay unknown. 3C "
             "adds alarm lookup rows (fault -> alarm code) only."),
    "load": _LOAD,
    "conductor": _CONDUCTOR,
    "storage": _STORAGE,
    "valve": _VALVE,
    "interface": _INTERFACE,
}

#: Archetype ids in the v3 §2.1 candidate order.
ARCHETYPE_IDS = ["source", "switch", "converter", "transmission", "sensor",
                 "controller", "load", "conductor", "storage", "valve",
                 "interface"]

#: Legacy human-readable type labels (Phase 2 ``library_type``) per
#: archetype, kept for display and case mapping. Binding targets the
#: archetype; the label is not the binding target (v3 §8).
ARCHETYPE_LIBRARY_LABELS: dict[str, str] = {
    "transmission": "wire_feeder",
    "converter": "dc_motor",
    "switch": "relay",
}


def get_archetype(archetype_id: str) -> Archetype | None:
    return ARCHETYPES.get(archetype_id)


def populated_archetypes() -> dict[str, Archetype]:
    """Archetypes with propagation rows (3B: A2/A3/A4; 3C adds A1/A7/A8/A9/
    A10/A11. A5 sensor and A6 controller stay empty schema entries)."""
    return {aid: a for aid, a in ARCHETYPES.items() if a.propagation_table}
