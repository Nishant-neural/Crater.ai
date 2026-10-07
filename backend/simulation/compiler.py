"""Compile the canonical UniversalMachineModel into a deterministic simulation contract.

The compiler is deliberately conservative: it carries documented topology, states,
procedures, failure modes and evidence into the twin, but it never invents physical
behaviour. Dynamic transitions are compiled only from explicit simulation rules when
present in the canonical model payload.
"""
from __future__ import annotations

from typing import Any

from backend.knowledge.machine_model import UniversalMachineModel
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


def _safe_initial_state(entity_id: str, model: UniversalMachineModel) -> dict[str, Any]:
    state: dict[str, Any] = {}
    entity = next((e for e in model.entities if e.id == entity_id), None)
    if entity and isinstance(entity.properties.get("initial_state"), dict):
        state.update(entity.properties["initial_state"])
    for item in model.states:
        if item.entity_id != entity_id or item.value is None:
            continue
        # State names are evidence-derived labels; use them as stable state fields.
        key = item.name.strip().lower().replace(" ", "_")
        if key and key not in state:
            state[key] = item.value
    return state


def compile_machine_model(model: UniversalMachineModel, *, name: str, description: str = "") -> tuple[DigitalTwinDefinition, list[str]]:
    warnings: list[str] = []
    components = [
        TwinComponent(
            id=e.id,
            name=e.name,
            component_type=_component_type(e.entity_type),
            initial_state=_safe_initial_state(e.id, model),
            source_ids=list(e.source_ids),
        )
        for e in model.entities
    ]

    transitions: list[TwinTransition] = []
    raw_rules = []
    # Expert/global integration may carry explicit simulation_rules as an extension
    # without changing the canonical core schema. Only accept structurally valid rules.
    raw_rules.extend(getattr(model, "simulation_rules", []) or [])
    if not raw_rules:
        warnings.append("No explicit simulation rules are present in the canonical machine model; the twin is topology/state aware but has no inferred physics or control logic.")

    for index, rule in enumerate(raw_rules):
        if not isinstance(rule, dict):
            warnings.append(f"Ignored malformed simulation rule #{index + 1}.")
            continue
        try:
            transitions.append(TwinTransition.model_validate({
                "id": rule.get("id") or f"sim-rule:{index + 1}",
                "name": rule.get("name") or f"Simulation rule {index + 1}",
                "conditions": rule.get("conditions") or {},
                "effects": rule.get("effects") or {},
                "description": rule.get("description") or "Explicit rule from canonical machine model.",
                "safety_notes": rule.get("safety_notes") or [],
            }))
        except Exception:
            warnings.append(f"Ignored invalid simulation rule #{index + 1}; canonical knowledge remains unchanged.")

    metadata = {
        "compiler": "machine-model-to-functional-twin-v1",
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

    definition = DigitalTwinDefinition(
        name=name,
        description=description or "Functional simulation compiled from the canonical machine model. Simulation is not a physical safety proof.",
        components=components,
        initial_signals={},
        transitions=transitions,
        metadata=metadata,
    )
    return definition, warnings
