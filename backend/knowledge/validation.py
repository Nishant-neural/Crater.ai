"""Validation helpers for Phase 8A universal machine knowledge."""
from __future__ import annotations

from collections import defaultdict

from backend.knowledge.machine_model import UniversalMachineModel
from backend.knowledge.normalization import unresolved_record


def validate_machine_model(model: UniversalMachineModel) -> list[str]:
    """Return human-readable validation issues for a machine model.

    The validator is intentionally conservative: it reports structurally invalid
    references, duplicate entities, and conflicting state assignments while
    preserving all evidence rather than silently dropping the conflict.
    """
    issues: list[str] = []
    entity_map = model.entity_map()

    seen_names: dict[str, str] = {}
    seen_ids: set[str] = set()
    for entity in model.entities:
        if entity.id in seen_ids:
            issues.append(f"duplicate entity id: {entity.id}")
        seen_ids.add(entity.id)
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
        for evidence in entity.evidence:
            issues.extend(_evidence_issues(f"entity {entity.name}", evidence))

    for relation in model.relations:
        for evidence in relation.evidence:
            issues.extend(_evidence_issues(f"relation {relation.subject_name}->{relation.object_name}", evidence))

    for fact_type, _, payload in model.all_facts():
        evidence = payload.get("evidence", [])
        for item in evidence:
            confidence = item.get("confidence", 0.0)
            if confidence < 0.5:
                issues.append(f"low-confidence {fact_type}: {confidence}")
        status = str(payload.get("properties", {}).get("status", "")).upper()
        if status in {"UNKNOWN", "UNCERTAIN"}:
            issues.append(f"unsupported or uncertain {fact_type}")

    return issues


def _evidence_issues(label: str, evidence) -> list[str]:
    issues: list[str] = []
    if not evidence.fact:
        issues.append(f"{label} evidence missing fact")
    if not evidence.source_type and not evidence.source_document:
        issues.append(f"{label} evidence missing source type")
    if not evidence.extraction_method and not evidence.source_document:
        issues.append(f"{label} evidence missing extraction method")
    if evidence.confidence < 0.5:
        issues.append(f"low-confidence {label}: {evidence.confidence}")
    if evidence.source_document is None and evidence.source_type not in {"expert", "schematic"}:
        issues.append(f"{label} evidence missing source document")
    return issues


validate_model = validate_machine_model


def validate_machine_model_with_ledger(model: UniversalMachineModel) -> tuple[list[str], list[dict]]:
    """Validate semantics and return an explicit ledger for facts that cannot be trusted.

    The model is not mutated and no fact is silently discarded. Callers may persist
    returned ledger records alongside the model for review/reprocessing.
    """
    issues = validate_machine_model(model)
    ledger: list[dict] = []

    def record(kind: str, item, reason: str) -> None:
        payload = item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item)
        ledger.append(unresolved_record(
            payload, source_id=payload.get("id") or (payload.get("source_ids") or [None])[0],
            fact_type=kind, reason=reason,
        ))

    entity_ids = {entity.id for entity in model.entities}
    for relation in model.relations:
        if relation.subject_id not in entity_ids or relation.object_id not in entity_ids:
            missing = []
            if relation.subject_id not in entity_ids:
                missing.append(f"subject:{relation.subject_id}")
            if relation.object_id not in entity_ids:
                missing.append(f"object:{relation.object_id}")
            record("relation", relation, "unresolved_entity_reference:" + ",".join(missing))
    for kind, collection in (
        ("port", model.ports), ("quantity", model.quantities), ("state", model.states),
        ("event", model.events), ("behavior", model.behaviors), ("constraint", model.constraints),
    ):
        for item in collection:
            entity_id = getattr(item, "entity_id", None) or getattr(item, "subject_id", None)
            if entity_id and entity_id not in entity_ids and not str(entity_id).startswith("unresolved:"):
                record(kind, item, f"unresolved_entity_reference:{entity_id}")
            elif entity_id and str(entity_id).startswith("unresolved:"):
                record(kind, item, "placeholder_entity_reference")
    for kind, collection in (("procedure", model.procedures), ("failure_mode", model.failure_modes)):
        for item in collection:
            if not item.evidence:
                record(kind, item, "missing_evidence")
    for item in model.unresolved_facts:
        ledger.append(item.model_dump(mode="json"))
    # De-duplicate repeated issue records deterministically.
    unique = {}
    for item in ledger:
        key = (item.get("fact_type"), item.get("source_id"), item.get("reason"))
        unique[key] = item
    return issues, list(unique.values())
