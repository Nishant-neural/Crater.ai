"""Fact-level validation for the canonical Phase 8A machine model."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from backend.knowledge.machine_model import UniversalMachineModel


@dataclass(frozen=True)
class ValidationIssue:
    fact_type: str
    fact_key: str
    message: str
    severity: Literal["error", "warning"] = "warning"

    @property
    def blocking(self) -> bool:
        return self.severity == "error"


def validate_facts(model: UniversalMachineModel) -> list[ValidationIssue]:
    """Validate individual facts.

    Errors identify facts that cannot safely be persisted. Warnings preserve
    the fact while recording uncertainty/conflict for downstream consumers.
    """
    issues: list[ValidationIssue] = []
    entity_map = model.entity_map()
    seen_ids: set[str] = set()
    seen_names: dict[str, str] = {}

    for entity in model.entities:
        if entity.id in seen_ids:
            issues.append(ValidationIssue(
                "entity", entity.id, f"duplicate entity id: {entity.id}", "error"
            ))
        else:
            seen_ids.add(entity.id)
        if entity.name in seen_names and seen_names[entity.name] != entity.id:
            issues.append(ValidationIssue(
                "entity", entity.id, f"duplicate entity name: {entity.name}", "warning"
            ))
        else:
            seen_names[entity.name] = entity.id
        if entity.properties and not entity.evidence:
            issues.append(ValidationIssue(
                "entity", entity.id, f"entity with properties but no evidence: {entity.name}", "warning"
            ))
        issues.extend(_evidence_issues("entity", entity.id, entity.evidence))

    for relation in model.relations:
        key = f"{relation.subject_id}->{relation.object_id}:{relation.relation_type}"
        if relation.subject_id not in entity_map:
            issues.append(ValidationIssue(
                "relation", key,
                f"relation references missing subject entity: {relation.subject_id}", "error"
            ))
        if relation.object_id not in entity_map:
            issues.append(ValidationIssue(
                "relation", key,
                f"relation references missing object entity: {relation.object_id}", "error"
            ))
        if relation.description and not relation.evidence:
            issues.append(ValidationIssue(
                "relation", key,
                f"relation without evidence: {relation.subject_name} {relation.relation_type} {relation.object_name}",
                "warning",
            ))
        issues.extend(_evidence_issues("relation", key, relation.evidence))

    state_values: dict[tuple[str, str], set[str]] = defaultdict(set)
    for state in model.states:
        key = f"{state.entity_id}:{state.name}"
        if state.entity_id not in entity_map:
            issues.append(ValidationIssue(
                "state", key, f"state references missing entity: {state.entity_id}", "error"
            ))
            continue
        state_values[(state.entity_id, state.name)].add(str(state.value))
        issues.extend(_evidence_issues("state", key, state.evidence))

    for (entity_id, state_name), values in state_values.items():
        if len(values) > 1:
            issues.append(ValidationIssue(
                "state", f"{entity_id}:{state_name}",
                f"conflicting state values for {entity_id}/{state_name}: {sorted(values)}",
                "warning",
            ))

    for fact_type, items in _fact_collections(model):
        for item in items:
            key = _fact_key(fact_type, item)
            issues.extend(_evidence_issues(fact_type, key, item.evidence))
            status = str(getattr(item, "properties", {}).get("status", "")).upper()
            if status in {"UNKNOWN", "UNCERTAIN"}:
                issues.append(ValidationIssue(
                    fact_type, key, f"unsupported or uncertain {fact_type}", "warning"
                ))

    for evidence in model.evidence:
        issues.extend(_evidence_issues("model", "model", [evidence]))
    return issues


def invalid_fact_keys(model: UniversalMachineModel) -> set[tuple[str, str]]:
    """Return only facts that are structurally unsafe to persist."""
    return {
        (issue.fact_type, issue.fact_key)
        for issue in validate_facts(model)
        if issue.blocking and not (
            issue.fact_type == "entity" and issue.message.startswith("duplicate entity id:")
        )
    }


def validate_machine_model(model: UniversalMachineModel) -> list[str]:
    """Backward-compatible human-readable view of fact-level validation."""
    return [issue.message for issue in validate_facts(model)]


def _fact_collections(model: UniversalMachineModel):
    return (
        ("port", model.ports),
        ("quantity", model.quantities),
        ("event", model.events),
        ("behavior", model.behaviors),
        ("constraint", model.constraints),
        ("procedure", model.procedures),
        ("failure_mode", model.failure_modes),
    )


def _fact_key(fact_type: str, item) -> str:
    if fact_type == "state":
        return f"{item.entity_id}:{item.name}"
    return getattr(item, "id", fact_type)


def _evidence_issues(
    fact_type: str,
    fact_key: str,
    evidence,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not evidence:
        return issues

    for ev in evidence:
        if not ev.fact:
            issues.append(ValidationIssue(
                fact_type, fact_key, f"{fact_type} evidence missing fact", "warning"
            ))
        if not ev.source_type and not ev.source_document:
            issues.append(ValidationIssue(
                fact_type, fact_key, f"{fact_type} evidence missing source type", "warning"
            ))
        if not ev.extraction_method and not ev.source_document:
            issues.append(ValidationIssue(
                fact_type, fact_key, f"{fact_type} evidence missing extraction method", "warning"
            ))
        if ev.confidence < 0.5:
            issues.append(ValidationIssue(
                fact_type, fact_key,
                f"low-confidence {fact_type}: {ev.confidence}", "warning"
            ))
        if ev.source_document is None and ev.source_type not in {"expert", "schematic"}:
            issues.append(ValidationIssue(
                fact_type, fact_key,
                f"{fact_type} evidence missing source document", "warning"
            ))


    return issues


validate_model = validate_machine_model
