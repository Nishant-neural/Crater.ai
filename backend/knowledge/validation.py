"""Validation helpers for Phase 8A universal machine knowledge."""
from __future__ import annotations

from collections import defaultdict

from backend.knowledge.machine_model import UniversalMachineModel


def validate_machine_model(model: UniversalMachineModel) -> list[str]:
    """Return human-readable validation issues for a machine model.

    The validator is intentionally conservative: it reports structurally invalid
    references, duplicate entities, and conflicting state assignments while
    preserving all evidence rather than silently dropping the conflict.
    """
    issues: list[str] = []
    entity_map = model.entity_map()

    seen_names: dict[str, str] = {}
    for entity in model.entities:
        if entity.name in seen_names:
            issues.append(f"duplicate entity name: {entity.name}")
        else:
            seen_names[entity.name] = entity.id

    for relation in model.relations:
        if relation.subject_id not in entity_map:
            issues.append(f"relation references missing subject entity: {relation.subject_id}")
        if relation.object_id not in entity_map:
            issues.append(f"relation references missing object entity: {relation.object_id}")

    state_values: dict[tuple[str, str], set[str]] = defaultdict(set)
    for state in model.states:
        entity_id = state.entity_id
        if entity_id not in entity_map:
            issues.append(f"state references missing entity: {entity_id}")
            continue
        state_values[(entity_id, state.name)].add(str(state.value))
    for (entity_id, state_name), values in state_values.items():
        if len(values) > 1:
            issues.append(f"conflicting state values for {entity_id}/{state_name}: {sorted(values)}")

    for relation in model.relations:
        if not relation.evidence and relation.description:
            issues.append(f"relation without evidence: {relation.subject_name} {relation.relation_type} {relation.object_name}")

    for entity in model.entities:
        if not entity.evidence and entity.properties:
            issues.append(f"entity with properties but no evidence: {entity.name}")

    return issues


validate_model = validate_machine_model
