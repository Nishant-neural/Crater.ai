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


def quarantine_machine_model(model: UniversalMachineModel) -> tuple[UniversalMachineModel, list[dict]]:
    """Per-fact quarantine: keep every usable fact, quarantine only the unusable.

    This replaces the old all-or-nothing pattern in the pipeline, where one
    bad relation or duplicate name in a chunk caused the *whole* chunk model
    to be stored as a single unresolved blob and none of its good facts to
    be persisted.

    Quarantined (removed from the kept model, preserved as a ledger record):
    - duplicate entity ids (second occurrence)
    - relations whose subject or object entity is not in the kept set
    - ports / quantities / states / events whose entity is not in the kept set
    - a second, conflicting value for the same (entity, state name)
    - facts with no id / name at all

    Deliberately NOT quarantined, because they are still facts:
    - duplicate entity *names* with different ids (two fans, two knobs)
    - low confidence (confidence is a ranking signal, not validity)
    - behaviors / procedures / failure modes whose subject entity is not
      yet known -- the description itself is a fact, independent of the
      chunk it came from. Their missing link is recorded in the ledger
      for the pending-link pass, but the fact is kept.
    - entities with an invalid bbox -- the bbox is stripped, the entity kept.

    No deterministic entity register is used here, and no fact is guessed
    into a canonical identity. Anything quarantined stays in the unresolved
    ledger with its original payload, so a later pass (or a human) can
    resurrect it instead of it vanishing.
    """
    from backend.knowledge.machine_model import UniversalMachineModel as _UMM

    kept_entities = []
    seen_ids: set[str] = set()
    quarantined: list[dict] = []

    def _quarantine(kind: str, item, reason: str):
        payload = item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item)
        quarantined.append(unresolved_record(
            payload,
            source_id=payload.get("id") or (payload.get("source_ids") or [None])[0],
            fact_type=kind, reason=reason,
        ))

    for entity in model.entities:
        if not entity.id:
            _quarantine("entity", entity, "missing_entity_id")
            continue
        if entity.id in seen_ids:
            _quarantine("entity", entity, "duplicate_entity_id")
            continue
        seen_ids.add(entity.id)
        # Invalid bbox: strip it, keep the entity. A bad coordinate must
        # not kill a real component.
        props = dict(entity.properties or {})
        bbox = props.get("bbox")
        if isinstance(bbox, dict):
            try:
                x, y, w, h = (float(bbox[k]) for k in ("x", "y", "w", "h"))
                if not (w > 0 and h > 0 and x >= 0 and y >= 0 and x + w <= 1 and y + h <= 1):
                    raise ValueError("out of range")
            except Exception:
                props.pop("bbox", None)
                entity.properties = props
                quarantined.append(unresolved_record(
                    {"entity_id": entity.id, "bbox": bbox},
                    source_id=entity.id, fact_type="entity_bbox",
                    reason="invalid_bbox_stripped",
                ))
        kept_entities.append(entity)

    kept_ids = {e.id for e in kept_entities}

    kept_relations = []
    for rel in model.relations:
        if rel.subject_id not in kept_ids or rel.object_id not in kept_ids:
            missing = []
            if rel.subject_id not in kept_ids:
                missing.append(f"subject:{rel.subject_id}")
            if rel.object_id not in kept_ids:
                missing.append(f"object:{rel.object_id}")
            _quarantine("relation", rel, "unresolved_entity_reference:" + ",".join(missing))
        else:
            kept_relations.append(rel)

    def _keep_linked(collection, kind):
        kept = []
        for item in collection:
            eid = getattr(item, "entity_id", None)
            if eid and eid not in kept_ids:
                _quarantine(kind, item, f"unresolved_entity_reference:{eid}")
            else:
                kept.append(item)
        return kept

    kept_ports = _keep_linked(model.ports, "port")
    kept_quantities = _keep_linked(model.quantities, "quantity")
    kept_events = _keep_linked(model.events, "event")

    kept_states = []
    seen_states: dict[tuple[str, str], str] = {}
    for state in model.states:
        if state.entity_id and state.entity_id not in kept_ids:
            _quarantine("state", state, f"unresolved_entity_reference:{state.entity_id}")
            continue
        key = (state.entity_id, state.name)
        if key in seen_states and seen_states[key] != str(state.value):
            _quarantine("state", state, f"conflicting_state_value:{seen_states[key]} vs {state.value}")
            continue
        seen_states[key] = str(state.value)
        kept_states.append(state)

    # Behaviors, constraints, procedures and failure modes are kept even
    # when their subject is not yet in the kept set: the fact itself is
    # independent of the chunk that produced it. A missing link is noted
    # in the ledger without removing the fact.
    for item in list(model.behaviors) + list(model.constraints) + list(model.procedures) + list(model.failure_modes):
        eid = getattr(item, "entity_id", None) or getattr(item, "subject_id", None)
        if eid and eid not in kept_ids and not str(eid).startswith("unresolved:"):
            quarantined.append(unresolved_record(
                item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item),
                source_id=item.model_dump(mode="json").get("id") if hasattr(item, "model_dump") else None,
                fact_type="missing_link_note",
                reason=f"subject_not_in_kept_entities:{eid} (fact kept)",
            ))

    kept = _UMM(
        entities=kept_entities,
        relations=kept_relations,
        ports=kept_ports,
        quantities=kept_quantities,
        states=kept_states,
        events=kept_events,
        behaviors=list(model.behaviors),
        constraints=list(model.constraints),
        procedures=list(model.procedures),
        failure_modes=list(model.failure_modes),
        conflicts=list(model.conflicts),
        unresolved_facts=list(model.unresolved_facts),
        simulation_rules=list(model.simulation_rules),
        evidence=list(model.evidence),
    )
    # De-duplicate quarantine records deterministically.
    unique: dict[tuple, dict] = {}
    for item in quarantined:
        key = (item.get("fact_type"), item.get("source_id"), item.get("reason"))
        unique[key] = item
    return kept, list(unique.values())


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
