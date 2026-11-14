"""Compile the canonical UniversalMachineModel into a deterministic simulation contract.

The compiler is deliberately conservative: it carries documented topology, states,
procedures, failure modes and evidence into the twin, but it never invents physical
behaviour. Dynamic transitions are compiled only from explicit simulation rules when
present in the canonical model payload.

Phase 3B — compiler v3 (design v3 §5.5 as revised by the second Amendments
section): in addition to explicit rules, the compiler

1. binds each canonical entity to an **archetype** by the §8 rule
   (``backend/simulation/binding.py`` — unique-match-on-evidence, name a
   hint only, machine input voltage never motor-rating evidence);
2. instantiates the archetype's propagation rows as **one generic
   conditional transition per row** (condition = the row's inputs,
   including the fault state; effect = the row's outputs + next state).
   The engine propagates at runtime via its existing ``step()`` fixpoint
   — the compiler never compiles per-fault traces (that would bake one
   fault into the twin);
3. generates explicit unknown-setting rows for uncovered input
   combinations (the engine never retracts effects and cannot produce
   ``unknown`` on its own) and lints the table for mutual exclusion +
   totality (``backend/simulation/lint.py``);
4. emits link transitions for the reviewed human-confirmed topology
   edges (``backend/simulation/edges.py``) — value flows along a declared
   edge, never across an undeclared one;
5. translates expert-seed rules into the parameter vocabulary at compile
   time (``tension_ok=true`` -> ``tension=normal``; the too-tight rule ->
   condition ``tension=high``), records the translation, and never runs
   both vocabularies live for the same behaviour;
6. runs the nominal fixpoint once and stores the settled values as
   ``initial_state`` (so the verifier's healthy value is a real value,
   not a fallback guess);
7. pins the archetype-library version and records per-component coverage
   (ports/parameters/rows bound vs missing, binding evidence) on the twin.

Engine untouched. No new LLM calls at runtime. Demo Motor Drive
(7 hand-written transitions) compiles identically — it is hand-built,
not compiled, and the compiler changes nothing about it.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from backend.knowledge.machine_model import UniversalMachineModel
from backend.simulation.archetypes import (
    ARCHETYPE_LIBRARY_VERSION, ARCHETYPES, ARCHETYPE_LIBRARY_LABELS, Archetype,
)
from backend.simulation.binding import bind_model
from backend.simulation.edges import expand_link_transitions, load_edge_list
from backend.simulation.engine import DigitalTwinEngine
from backend.simulation.lint import (
    check_settles, generate_unknown_rows, lint_archetype_table, lint_transitions,
    order_transitions,
)
from .schema import DigitalTwinDefinition, TwinComponent, TwinTransition


def _component_type(entity_type: str) -> str:
    value = (entity_type or "").strip().lower()
    if value in {"sensor", "relay", "controller", "actuator", "power_source", "connector"}:
        return value
    if value in {"motor", "pump", "valve", "cylinder", "fan", "drive", "inverter"}:
        return "actuator"
    if value in {"plc", "hmi", "module", "instrument", "network"}:
        return "controller"
    if value in {"switch", "breaker", "fuse"}:
        return "relay"
    return "other"


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _coerce_state_value(value: Any) -> Any:
    """Coerce a state value to the JSON type a rule condition uses.
    MachineState.value may be a string ("true", "120"), a boolean, or a
    number; rule conditions carry
    JSON values (true, 120). Without coercion the engine's _matches would
    never see them as equal and no transition could fire. Non-numeric,
    non-boolean strings pass through unchanged.
    """
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        try:
            return int(value.strip())
        except (TypeError, ValueError):
            pass
        try:
            return float(value.strip())
        except (TypeError, ValueError):
            pass
    return value


def _safe_initial_state(entity_id: str, model: UniversalMachineModel) -> dict[str, Any]:
    state: dict[str, Any] = {}
    entity = next((e for e in model.entities if e.id == entity_id), None)
    if entity and isinstance(entity.properties.get("initial_state"), dict):
        state.update({k: _coerce_state_value(v) for k, v in entity.properties["initial_state"].items()})
    for item in model.states:
        if item.entity_id != entity_id or item.value is None:
            continue
        # State names are evidence-derived labels; use them as stable state fields.
        key = item.name.strip().lower().replace(" ", "_")
        if key and key not in state:
            state[key] = _coerce_state_value(item.value)
    return state


# ---------------------------------------------------------------------------
# Archetype row -> transition
# ---------------------------------------------------------------------------

def _row_transition(archetype: Archetype, row: Any, component_id: str) -> TwinTransition:
    """One generic conditional transition per propagation row.

    Conditions are the row's inputs (on the component's own fields) plus
    the fault condition — ALWAYS explicit, including ``fault == "none"``
    for nominal rows, so a nominal row cannot fire while the component
    is faulted (the engine never retracts effects; a nominal row that
    ignored the fault would fight the fault row forever). Effects are
    the row's outputs plus the next state. A generated totality row with
    ``fault=None`` (don't care) omits the fault condition.
    """
    conditions: dict[str, Any] = {
        f"{component_id}.{field}": value for field, value in (row.inputs or {}).items()}
    fault = getattr(row, "fault", "none")
    if fault is not None:
        conditions[f"{component_id}.fault"] = fault
    effects: dict[str, Any] = {
        f"{component_id}.{field}": value for field, value in (row.outputs or {}).items()}
    effects[f"{component_id}.state"] = row.next_state
    # 3C fix (13 Nov 2026): the id AND the name carry the component id.
    # The 3B format (``archetype:{row_id}``) assumed one instance per
    # archetype — the 3B proof twin had one A2, one A3, one A4. In 3C
    # there are two A1 instances (cord + welder) and four A8 instances
    # (gun / liner / tip / ground), and the 3B format collapsed them:
    # ``order_transitions`` dedupes by id (``by_id = {t.id: t ...}``),
    # so only one s1.on / c-nominal survived and the other instances
    # never got a transition. The per-component id also makes the lint's
    # conflict quarantine precise (it removes by id) and the
    # failure-to-behaviour template ids unambiguous (which component's
    # row). The row id itself (``transmission.t1.feeding``) is unchanged
    # — it is the archetype row; the transition id names the instance.
    return TwinTransition(
        id=f"archetype:{row.row_id}@{component_id}",
        name=f"{archetype.archetype_id} row {row.row_id} [{component_id}]",
        conditions=conditions,
        effects=effects,
        description=(f"Archetype {archetype.archetype_id} v{archetype.version} row "
                     f"{row.row_id}; sources: {', '.join(row.sources or []) or 'none'}"),
        source_ids=[f"archetype-library:{archetype.version}"],
        evidence=[{"archetype_id": archetype.archetype_id,
                   "archetype_row": row.row_id,
                   "archetype_version": archetype.version,
                   "sources": list(row.sources or []),
                   "cases": list(row.cases or [])}],
    )


def _bound_parameter_value(model: UniversalMachineModel, entity_id: str,
                           parameter_name: str) -> tuple[Any | None, dict[str, Any] | None]:
    """The model's bound value for one archetype parameter on one entity,
    or (None, None) when the parameter is unbound there. A quantity
    counts only when it belongs to the SAME entity (the §8 same-quantity
    rule — the binding module decides the evidence; this is the value
    lookup for the twin's initial state)."""
    for quantity in model.quantities:
        if _norm(quantity.entity_id) != _norm(entity_id):
            continue
        name = _norm(quantity.name)
        if _norm(parameter_name) in name or name in _norm(parameter_name):
            if quantity.is_bound:
                return _coerce_state_value(quantity.value), {
                    "quantity_id": quantity.id, "name": quantity.name,
                    "value": quantity.value, "unit": quantity.unit,
                    "source_kind": quantity.source_kind}
    return None, None


def _archetype_transitions(model: UniversalMachineModel,
                           bindings: dict[str, dict[str, Any]],
                           warnings: list[str]) -> tuple[list[TwinTransition], dict[str, Any]]:
    """Instantiate propagation rows for every bound entity.

    Returns (transitions, report). Rows are validated and linted first:
    an invalid row is quarantined (never emitted); a table that fails
    exclusion/totality after its generated unknown rows is reported and
    its conflicting rows quarantined. An archetype with no rows (the 3B
    empty schema entries) contributes no transitions and is recorded as
    bound-with-no-rows, never as a failure.
    """
    transitions: list[TwinTransition] = []
    report: dict[str, Any] = {"bound_components": {}, "quarantined_rows": []}
    for entity in model.entities:
        record = bindings.get(entity.id) or {}
        archetype_id = record.get("archetype_id")
        if not archetype_id:
            continue
        archetype = ARCHETYPES.get(archetype_id)
        if archetype is None:
            continue
        entry: dict[str, Any] = {"archetype_id": archetype_id,
                                 "archetype_version": archetype.version,
                                 "library_type": ARCHETYPE_LIBRARY_LABELS.get(archetype_id),
                                 "rows_emitted": [], "rows_quarantined": [],
                                 "parameters": {}, "evidence": record.get("evidence", {})}
        report["bound_components"][entity.id] = entry
        if not archetype.propagation_table:
            entry["rows_note"] = ("bound to an empty schema entry (no rows in 3B); "
                                  "no transitions emitted")
            continue
        rows = list(archetype.propagation_table)
        generated = generate_unknown_rows(archetype)
        rows = rows + generated
        lint = lint_archetype_table(archetype, rows)
        for invalid in lint["invalid"]:
            entry["rows_quarantined"].append(invalid)
            report["quarantined_rows"].append(
                {"component_id": entity.id, **invalid})
            warnings.append(
                f"Archetype {archetype_id}: quarantined invalid row "
                f"{invalid['row_id']} for {entity.name}: "
                f"{'; '.join(invalid['problems'])}")
        invalid_ids = {item["row_id"] for item in lint["invalid"]}
        conflict_rows = {row_id for conflict in lint["conflicts"]
                         for row_id in conflict["rows"]}
        # Conflicts between rows with identical outputs are not conflicts
        # (lint_archetype_table only reports differing outputs), so any
        # row named here genuinely disagrees with another row.
        for row_id in sorted(conflict_rows):
            if row_id in invalid_ids:
                continue
            entry["rows_quarantined"].append(
                {"row_id": row_id, "problems": ["conflicting effects with another row"]})
            report["quarantined_rows"].append(
                {"component_id": entity.id, "row_id": row_id,
                 "problems": ["conflicting effects with another row"]})
            warnings.append(
                f"Archetype {archetype_id}: quarantined conflicting row {row_id} "
                f"for {entity.name} (two rows write different values under "
                f"simultaneously satisfiable conditions); fix the rows, do not "
                f"weaken the lint.")
        quarantined = invalid_ids | conflict_rows
        if lint["uncovered"]:
            warnings.append(
                f"Archetype {archetype_id}: {len(lint['uncovered'])} input "
                f"combination(s) still uncovered for {entity.name} after totality "
                f"rows — the twin says 'unknown' there by omission, not by row.")
        for row in rows:
            if row.row_id in quarantined:
                continue
            transitions.append(_row_transition(archetype, row, entity.id))
            entry["rows_emitted"].append(row.row_id)
        for parameter in archetype.parameters:
            value, evidence = _bound_parameter_value(model, entity.id, parameter.name)
            entry["parameters"][parameter.name] = {
                "bound": value is not None,
                "value": value,
                "evidence": evidence,
                "nominal_default": (parameter.nominal_default
                                    if parameter.has_nominal_default else None),
            }
    return transitions, report


# ---------------------------------------------------------------------------
# Seed translation (Amendments item 6): boolean vocabulary -> parameter
# vocabulary, at compile time, recorded, never both live for one behaviour.
# ---------------------------------------------------------------------------

def _translate_seed_rules(raw_rules: list[dict[str, Any]],
                          model: UniversalMachineModel,
                          bindings: dict[str, Any],
                          warnings: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Translate expert-seed rules into the parameter vocabulary.

    The seed speaks a boolean vocabulary (``tension_ok``) while the
    archetype tables speak parameters (``tension: low/normal/high``) —
    and the seed's own ``tension_ok=false`` rule is labelled 'too tight'
    though false is ambiguous (too low *or* too high). The translation
    uses the rule's own label to disambiguate false, maps the condition
    onto the unique A4 instance's ``tension`` parameter, and subsumes the
    rule under the archetype row whose conditions cover it (the row's
    material_out effect is the archetype expression of the seed's
    'feeding' effect). A subsumed rule is NOT emitted — the archetype row
    already says it — and the translation is recorded. A rule no row
    subsumes, or that has no unique A4 instance to map onto, is retained
    as-is as a rule-11 manual override and recorded as untranslated
    (the Wingnut rules: no A9 vocabulary exists in 3B).
    """
    translation: dict[str, Any] = {"translated": [], "subsumed": [], "untranslated": []}
    out_rules: list[dict[str, Any]] = []
    a4_instances = [eid for eid, rec in bindings.items()
                    if rec.get("archetype_id") == "transmission"]
    feeder_id = a4_instances[0] if len(a4_instances) == 1 else None
    feeder = ARCHETYPES.get("transmission")
    for rule in raw_rules:
        source_ids = list(rule.get("source_ids") or [])
        is_seed = any(str(s).startswith("expert-seed:") for s in source_ids)
        if not is_seed:
            out_rules.append(rule)
            continue
        conditions = dict(rule.get("conditions") or {})
        effects = dict(rule.get("effects") or {})
        # The tensioner rules: conditions on <tensioner>.tension_ok.
        tensioner_paths = [p for p in conditions if p.endswith(".tension_ok")]
        if len(tensioner_paths) != 1 or len(conditions) != 1:
            translation["untranslated"].append({
                "rule_id": rule.get("id"),
                "reason": "not a single tension_ok condition; no parameter "
                          "vocabulary mapping exists in 3B"})
            warnings.append(
                f"Seed rule {rule.get('id')}: not translated (not a single "
                f"tension_ok condition); retained as a rule-11 manual override.")
            out_rules.append(rule)
            continue
        # The boolean vocabulary is shared by two different parts: the
        # Feed Tensioner (A4, parameter vocabulary exists in 3B) and the
        # Wingnut (A9 storage, no vocabulary in 3B). Identify the part by
        # the component the condition names — never by the boolean alone.
        tensioner_entity = next(
            (e for e in model.entities
             if e.id == tensioner_paths[0].rsplit(".", 1)[0]), None)
        if tensioner_entity is None or _norm(tensioner_entity.name) != "feed tensioner":
            translation["untranslated"].append({
                "rule_id": rule.get("id"),
                "reason": ("condition is on "
                           f"{tensioner_entity.name if tensioner_entity else tensioner_paths[0]!r}, "
                           "not the Feed Tensioner; the Wingnut/spool vocabulary is "
                           "A9 storage, which arrives in 3C — no parameter mapping "
                           "exists in 3B")})
            warnings.append(
                f"Seed rule {rule.get('id')}: not translated (condition is on "
                f"{tensioner_entity.name if tensioner_entity else tensioner_paths[0]!r}; "
                f"A9 storage vocabulary arrives in 3C); retained as a rule-11 "
                f"manual override.")
            out_rules.append(rule)
            continue
        tension_value = conditions[tensioner_paths[0]]
        rule_text = f"{rule.get('name') or ''} {rule.get('description') or ''}".lower()
        if tension_value is True:
            tension = "normal"
        elif tension_value is False and "too tight" in rule_text:
            tension = "high"
        else:
            translation["untranslated"].append({
                "rule_id": rule.get("id"),
                "reason": ("tension_ok=false is ambiguous (too low or too high) "
                           "and this rule's label does not say 'too tight'")})
            warnings.append(
                f"Seed rule {rule.get('id')}: not translated (tension_ok=false is "
                f"ambiguous and the rule does not say 'too tight'); retained as a "
                f"rule-11 manual override.")
            out_rules.append(rule)
            continue
        if feeder_id is None or feeder is None:
            translation["untranslated"].append({
                "rule_id": rule.get("id"),
                "reason": ("no unique A4 transmission instance in this twin to map "
                           "the tension parameter onto")})
            warnings.append(
                f"Seed rule {rule.get('id')}: not translated (no unique A4 instance "
                f"in this twin); retained as a rule-11 manual override.")
            out_rules.append(rule)
            continue
        # The effect must be the wire's feeding state; the subsuming row
        # supplies the archetype expression (material_out).
        feeding_paths = [p for p in effects if p.endswith(".feeding")]
        if len(feeding_paths) != 1 or len(effects) != 1:
            translation["untranslated"].append({
                "rule_id": rule.get("id"),
                "reason": "effect is not the single wire feeding state"})
            warnings.append(
                f"Seed rule {rule.get('id')}: not translated (effect is not the "
                f"wire feeding state); retained as a rule-11 manual override.")
            out_rules.append(rule)
            continue
        feeding = effects[feeding_paths[0]]
        wanted = "normal" if feeding is True else ("low", "absent")
        subsuming = None
        for row in feeder.propagation_table:
            row_conditions = {
                f"{feeder_id}.{field}": value for field, value in (row.inputs or {}).items()}
            if row.fault is not None:
                row_conditions[f"{feeder_id}.fault"] = row.fault
            translated_conditions = {f"{feeder_id}.tension": tension}
            if not set(translated_conditions.items()) <= set(row_conditions.items()):
                continue
            material_out = row.outputs.get("material_out")
            if feeding is True and material_out == "normal":
                subsuming = row
                break
            if feeding is False and material_out in wanted:
                subsuming = row
                break
        if subsuming is None:
            translation["untranslated"].append({
                "rule_id": rule.get("id"),
                "reason": "no A4 row subsumes this rule (conditions/effect)"})
            warnings.append(
                f"Seed rule {rule.get('id')}: not translated (no A4 row subsumes "
                f"it); retained as a rule-11 manual override.")
            out_rules.append(rule)
            continue
        added_context = {
            path: value
            for path, value in ({
                f"{feeder_id}.{field}": val
                for field, val in (subsuming.inputs or {}).items()
            } | ({f"{feeder_id}.fault": subsuming.fault}
                 if subsuming.fault is not None else {})).items()
            if path != f"{feeder_id}.tension"}
        translation["subsumed"].append({
            "rule_id": rule.get("id"),
            "tension_ok": tension_value,
            "tension": tension,
            "archetype_row": subsuming.row_id,
            "added_context": added_context,
            "effect_mapping": {feeding_paths[0]: "feeding",
                               f"{feeder_id}.material_out": subsuming.outputs.get("material_out")},
        })
        translation["translated"].append(rule.get("id"))
        warnings.append(
            f"Seed rule {rule.get('id')} translated to the parameter vocabulary "
            f"(tension_ok={tension_value!r} -> tension={tension!r}) and subsumed "
            f"by archetype row {subsuming.row_id}; not emitted separately.")
    return out_rules, translation


# ---------------------------------------------------------------------------
# Nominal fixpoint -> initial_state (Amendments item 4)
# ---------------------------------------------------------------------------

def _nominal_initial_state(definition: DigitalTwinDefinition) -> dict[str, dict[str, Any]]:
    """Run the nominal fixpoint once and return the settled values per
    component. The engine's step() fixpoint propagates the archetype rows
    and links from the provisional initial states (sources and nominal
    parameter values); the settled state is what the twin starts in, so
    the verifier's healthy value is a real value, not a fallback guess.
    Only called when archetype or link transitions exist."""
    engine = DigitalTwinEngine(definition)
    engine.step()
    return {cid: dict(fields)
            for cid, fields in engine.state.get("components", {}).items()}


def _settle_injections(model: UniversalMachineModel,
                       bindings: dict[str, Any]) -> list[dict[str, Any]]:
    """Fault injections for the lint's settle check: every fault value and
    every non-nominal parameter value on every bound component. Bounded
    by the archetype tables, not by the twin size."""
    injections: list[dict[str, Any]] = []
    for entity in model.entities:
        record = bindings.get(entity.id) or {}
        archetype = ARCHETYPES.get(record.get("archetype_id") or "")
        if archetype is None or not archetype.propagation_table:
            continue
        for fault in archetype.fault_values:
            if fault != "none":
                injections.append({"component_id": entity.id, "field": "fault",
                                   "value": fault})
        for parameter in archetype.parameters:
            for value in parameter.values:
                if parameter.has_nominal_default and value == parameter.nominal_default:
                    continue
                injections.append({"component_id": entity.id, "field": parameter.name,
                                   "value": value})
    return injections


# ---------------------------------------------------------------------------
# Compile entry point
# ---------------------------------------------------------------------------

def compile_machine_model(model: UniversalMachineModel, *, name: str, description: str = "") -> tuple[DigitalTwinDefinition, list[str]]:
    warnings: list[str] = []
    bindings = bind_model(model)
    bound_ids = {eid for eid, rec in bindings.items() if rec.get("archetype_id")}

    # Provisional initial states: model states first, then archetype
    # nominal defaults for parameters the model leaves unbound, then
    # fault="none" for components whose rows or links condition on it.
    # A model value always wins over a nominal default.
    link_targets_with_fault: set[str] = set()
    try:
        edge_list = load_edge_list()
        for edge in edge_list.get("edges") or []:
            if edge.get("fault_override"):
                for entity in model.entities:
                    if _norm(entity.name) == _norm(edge.get("target_component")):
                        link_targets_with_fault.add(entity.id)
    except (OSError, ValueError):
        edge_list = {"edges": [], "value_maps": {}}

    provisional: dict[str, dict[str, Any]] = {}
    for entity in model.entities:
        state = _safe_initial_state(entity.id, model)
        record = bindings.get(entity.id) or {}
        archetype = ARCHETYPES.get(record.get("archetype_id") or "")
        if archetype is not None:
            used_fields = {f for row in archetype.propagation_table
                           for f in (row.inputs or {})}
            for parameter in archetype.parameters:
                if parameter.name not in used_fields:
                    continue
                if parameter.name in state:
                    continue
                value, _evidence = _bound_parameter_value(model, entity.id, parameter.name)
                if value is not None:
                    state[parameter.name] = value
                elif parameter.has_nominal_default:
                    state[parameter.name] = parameter.nominal_default
            if "fault" not in state:
                state["fault"] = "none"
        if entity.id in link_targets_with_fault and "fault" not in state:
            state["fault"] = "none"
        provisional[entity.id] = state

    components = [
        TwinComponent(
            id=e.id,
            name=e.name,
            component_type=_component_type(e.entity_type),
            initial_state=dict(provisional.get(e.id, {})),
            source_ids=list(e.source_ids),
        )
        for e in model.entities
    ]

    # Explicit rules first: seed rules are translated (or retained) here.
    raw_rules = []
    raw_rules.extend(getattr(model, "simulation_rules", []) or [])
    if not raw_rules:
        warnings.append("No explicit simulation rules are present in the canonical machine model; the twin is topology/state aware but has no inferred physics or control logic.")
    explicit_rules, seed_translation = _translate_seed_rules(
        [r for r in raw_rules if isinstance(r, dict)], model, bindings, warnings)
    if len([r for r in raw_rules if isinstance(r, dict)]) != len(raw_rules):
        warnings.append("Ignored malformed simulation rule(s); canonical knowledge remains unchanged.")
    transitions: list[TwinTransition] = []
    for index, rule in enumerate(explicit_rules):
        try:
            transitions.append(TwinTransition.model_validate({
                "id": rule.get("id") or f"sim-rule:{index + 1}",
                "name": rule.get("name") or f"Simulation rule {index + 1}",
                "conditions": rule.get("conditions") or {},
                "effects": rule.get("effects") or {},
                "description": rule.get("description") or "Explicit rule from canonical machine model.",
                "safety_notes": rule.get("safety_notes") or [],
                "source_ids": rule.get("source_ids") or [],
                "evidence": rule.get("evidence") or [],
            }))
        except Exception:
            warnings.append(f"Ignored invalid simulation rule #{index + 1}; canonical knowledge remains unchanged.")

    # Archetype rows + generated totality rows.
    archetype_transitions, archetype_report = _archetype_transitions(
        model, bindings, warnings)
    transitions.extend(archetype_transitions)

    # Reviewed topology links.
    link_bindings = {eid: {"archetype": ARCHETYPES.get(rec.get("archetype_id") or "")}
                     for eid, rec in bindings.items()}
    link_transitions, link_records = expand_link_transitions(
        edge_list, list(model.entities), link_bindings)
    for record_item in link_records:
        # A skipped edge is normal for any twin whose components do not
        # carry the OMNIPRO edge list's names — it is recorded in the
        # metadata, not warned about. Only a quarantined link (ports or
        # values the twin does not declare) is a warning.
        if record_item.get("status") == "quarantined":
            warnings.append(
                f"Topology link {record_item.get('edge_id')}: quarantined "
                f"({record_item.get('reason')})")
    transitions.extend(link_transitions)

    # Deterministic ordering (lint requirement): producers before
    # consumers (topological, id tie-break) so the engine's fixpoint
    # settles a chain in one step() call — a plain id sort puts every
    # archetype row before every link and the chain never settles.
    transitions, order_cycles = order_transitions(transitions)
    if order_cycles:
        warnings.append(
            "Compiler lint: transition dependency cycle detected at "
            + ", ".join(order_cycles)
            + "; those transitions fall back to id order and the settle "
              "lint decides whether the twin settles.")

    # Whole-twin lint: conflicting effects -> quarantine the row (never
    # silent last-writer-wins); invalid refs -> quarantine.
    definition = DigitalTwinDefinition(
        name=name,
        description=description or "Functional simulation compiled from the canonical machine model. Simulation is not a physical safety proof.",
        components=components,
        initial_signals={},
        transitions=transitions,
        metadata={},
    )
    lint = lint_transitions(definition)
    if not lint["ordered"]:
        warnings.append("Compiler lint: transition list was not in deterministic order; re-ordered.")
        definition.transitions, _cycles = order_transitions(definition.transitions)
        lint = lint_transitions(definition)
    quarantined_ids: set[str] = set()
    for conflict in lint["conflicts"]:
        # Quarantine the later transition of each conflicting pair (the
        # deterministic order makes "later" well-defined); the earlier
        # one stands. Recorded, never silent.
        for transition_id in conflict["transitions"][1:]:
            quarantined_ids.add(transition_id)
            warnings.append(
                f"Compiler lint: quarantined conflicting transition {transition_id} "
                f"(writes {conflict['paths']} differently from "
                f"{conflict['transitions'][0]} under simultaneously satisfiable "
                f"conditions); fix the rows, do not weaken the lint.")
    for invalid in lint["invalid"]:
        quarantined_ids.add(invalid["transition_id"])
        warnings.append(
            f"Compiler lint: quarantined invalid transition {invalid['transition_id']} "
            f"({invalid['problem']}).")
    if quarantined_ids:
        definition.transitions = [t for t in definition.transitions if t.id not in quarantined_ids]
        # Re-lint after quarantine: the metadata lint report describes the
        # twin as shipped, not the twin before quarantine.
        lint = lint_transitions(definition)

    # Nominal fixpoint -> initial_state, only when archetype or link
    # transitions exist (seed-only / rule-only twins keep v12 behaviour
    # exactly, including the Phase 2 seed proof's 4 transitions).
    archetype_or_link = any(
        t.id.startswith("archetype:") or t.id.startswith("link:")
        for t in definition.transitions)
    settled_note = "no archetype or link transitions; initial_state is the v12 provisional state"
    if archetype_or_link:
        settled = _nominal_initial_state(definition)
        for component in definition.components:
            if component.id in settled:
                component.initial_state = settled[component.id]
        settled_note = "nominal fixpoint run once; initial_state holds the settled values"

    # Settle lint (engine contract): nominal + every enumerable fault /
    # parameter injection on every bound component.
    settle = {"settled": True, "runs": []}
    if archetype_or_link:
        settle = check_settles(definition, _settle_injections(model, bindings))
        if not settle["settled"]:
            warnings.append(
                "Compiler lint: propagation did not settle (did-not-settle) for "
                "some nominal/fault run; the twin is reported unavailable with "
                "reason by the oracle/verifier, not silently truncated.")

    from backend.simulation.binding import binding_coverage
    coverage = binding_coverage(bindings)
    metadata = {
        "compiler": "machine-model-to-functional-twin-v3",
        "archetype_library_version": ARCHETYPE_LIBRARY_VERSION,
        "archetype_bindings": {
            eid: {"archetype_id": rec.get("archetype_id"),
                  "archetype_version": (ARCHETYPES[rec["archetype_id"]].version
                                        if rec.get("archetype_id") in ARCHETYPES else None),
                  "library_type": ARCHETYPE_LIBRARY_LABELS.get(rec.get("archetype_id") or ""),
                  "evidence": rec.get("evidence", {}),
                  "unresolved_reason": rec.get("unresolved_reason")}
            for eid, rec in bindings.items()},
        "binding_coverage": coverage,
        "archetype_report": archetype_report,
        "seed_translation": seed_translation,
        "link_records": link_records,
        "lint": lint,
        "settle": settle,
        "nominal_fixpoint": settled_note,
        "simulation_only": True,
        "source_model": {
            "entity_ids": [e.id for e in model.entities],
            "relation_count": len(model.relations),
            "state_count": len(model.states),
            "behavior_count": len(model.behaviors),
            "constraint_count": len(model.constraints),
            "procedure_count": len(model.procedures),
            "failure_mode_count": len(model.failure_modes),
        },
        "topology": [r.model_dump(mode="json") for r in model.relations],
        "ports": [p.model_dump(mode="json") for p in model.ports],
        "quantities": [q.model_dump(mode="json") for q in model.quantities],
        "behaviors": [b.model_dump(mode="json") for b in model.behaviors],
        "constraints": [c.model_dump(mode="json") for c in model.constraints],
        "procedures": [p.model_dump(mode="json") for p in model.procedures],
        "failure_modes": [f.model_dump(mode="json") for f in model.failure_modes],
        "conflicts": [c.model_dump(mode="json") for c in model.conflicts],
        "unresolved_facts": [u.model_dump(mode="json") for u in model.unresolved_facts],
        "compiler_warnings": warnings,
    }

    definition.metadata = metadata
    return definition, warnings
