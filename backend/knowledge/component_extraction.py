"""Canonical machine-knowledge extraction and persistence.

The LLM response is converted once into ``UniversalMachineModel``. Legacy
Product Brain tables are populated by a separate compatibility projection;
they are not a second machine-knowledge representation.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from backend.db.models import (
    MachineKnowledgeBehavior,
    MachineKnowledgeEntity,
    MachineKnowledgeEvidence,
    MachineKnowledgeFact,
    MachineKnowledgeRelation,
)
from backend.knowledge.evidence import MachineEvidence
from backend.knowledge.legacy_projection import (
    canonical_from_legacy,
    persist_legacy_extraction,
    to_legacy_result,
)
from backend.knowledge.machine_model import UniversalMachineModel
from backend.knowledge.schema import MachineKnowledgeExtractionResult
from backend.llm import get_llm_provider


_EXTRACTION_PROMPT = """You extract only explicit technical knowledge from one chunk of an industrial manual.
Do not infer or invent facts. Preserve uncertainty instead of guessing.

Return ONLY JSON with these fields:
{
  "components": [{"name":"","function":"","location_description":"","part_number":"","evidence":[]}],
  "relationships": [{"from_component":"","to_component":"","relation_type":"","description":"","evidence":[]}],
  "procedures": [{"name":"","procedure_type":"","steps":[],"evidence":[]}],
  "entities": [{"id":"","name":"","entity_type":"","properties":{},"evidence":[]}],
  "relations": [{"subject_id":"","subject_name":"","relation_type":"","object_id":"","object_name":"","description":"","evidence":[]}],
  "ports": [{"id":"","entity_id":"","name":"","direction":"","properties":{},"evidence":[]}],
  "quantities": [{"id":"","entity_id":"","name":"","value":"","unit":"","properties":{},"evidence":[]}],
  "states": [{"entity_id":"","name":"","value":"","properties":{},"evidence":[]}],
  "events": [{"id":"","entity_id":"","name":"","description":"","evidence":[]}],
  "behaviors": [{"id":"","subject_id":"","subject_name":"","description":"","evidence":[]}],
  "constraints": [{"id":"","entity_id":"","name":"","description":"","evidence":[]}],
  "universal_procedures": [{"id":"","name":"","procedure_type":"","steps":[],"evidence":[]}],
  "failure_modes": [{"id":"","name":"","symptom":"","possible_causes":[],"diagnostic_test":"","expected_observation":"","repair_procedure_id":"","properties":{},"evidence":[]}],
  "evidence": []
}
Every fact should carry evidence with fact, source_type, confidence and extraction_method.
Use explicit UNKNOWN/UNCERTAIN values when the source itself uses them.

Text:
---
{content}
---"""


def _extract_result(content: str) -> MachineKnowledgeExtractionResult:
    raw = get_llm_provider().complete(
        messages=[{"role": "user", "content": _EXTRACTION_PROMPT.replace("{content}", content)}],
        max_tokens=5000,
    )
    if not raw:
        return MachineKnowledgeExtractionResult()
    try:
        return MachineKnowledgeExtractionResult.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValueError, TypeError):
        return MachineKnowledgeExtractionResult()


def _canonical_model(result: MachineKnowledgeExtractionResult) -> UniversalMachineModel:
    if result.has_universal_facts():
        model = UniversalMachineModel(
            entities=result.entities,
            relations=result.relations,
            ports=result.ports,
            quantities=result.quantities,
            states=result.states,
            events=result.events,
            behaviors=result.behaviors,
            constraints=result.constraints,
            procedures=result.universal_procedures,
            failure_modes=result.failure_modes,
            evidence=[MachineEvidence.model_validate(e) for e in result.evidence],
        )
        # Legacy procedures are still accepted from older prompts, but are
        # immediately converted into the canonical procedure primitive.
        existing = {item.name for item in model.procedures}
        for proc in result.procedures:
            if proc.name not in existing:
                model.procedures.append(canonical_from_legacy(
                    MachineKnowledgeExtractionResult(procedures=[proc])
                ).procedures[0])
        return model
    return canonical_from_legacy(result)


def extract_chunk_knowledge(content: str):
    """Return the legacy projection and the canonical machine model."""
    result = _extract_result(content)
    return to_legacy_result(result), _canonical_model(result)


def extract_from_chunk_text(content: str):
    return extract_chunk_knowledge(content)[0]


def extract_machine_knowledge_from_chunk(content: str) -> UniversalMachineModel:
    return extract_chunk_knowledge(content)[1]


def persist_extraction(
    session: Session,
    revision_id: str,
    source_chunk_id: str,
    result,
) -> None:
    """Compatibility entry point for legacy Product Brain persistence."""
    persist_legacy_extraction(session, revision_id, source_chunk_id, result)


def persist_machine_knowledge(
    session: Session,
    revision_id: str | None,
    source_chunk_id: str | None,
    model: UniversalMachineModel,
) -> None:
    """Persist valid facts from the canonical model without all-or-nothing rejection."""
    from backend.knowledge.validation import invalid_fact_keys

    invalid = invalid_fact_keys(model)
    entity_ids: dict[str, str] = {}

    persisted_entity_ids: set[str] = set()
    for entity in model.entities:
        if entity.id in persisted_entity_ids or ("entity", entity.id) in invalid:
            continue
        persisted_entity_ids.add(entity.id)
        row = session.query(MachineKnowledgeEntity).filter(
            MachineKnowledgeEntity.revision_id == revision_id,
            MachineKnowledgeEntity.canonical_id == entity.id,
        ).first()
        if row is None:
            row = MachineKnowledgeEntity(
                revision_id=revision_id,
                canonical_id=entity.id,
                name=entity.name,
                entity_type=entity.entity_type,
                properties=entity.properties,
                source_chunk_id=source_chunk_id,
            )
            session.add(row)
            session.flush()
        else:
            row.properties = {**(row.properties or {}), **entity.properties}
        entity_ids[entity.id] = row.id
        _persist_evidence(session, "entity", row.id, entity.evidence, revision_id)

    for relation in model.relations:
        key = ("relation", f"{relation.subject_id}->{relation.object_id}:{relation.relation_type}")
        if key in invalid:
            continue
        if relation.subject_id not in entity_ids and not _entity_exists(session, revision_id, relation.subject_id):
            continue
        if relation.object_id not in entity_ids and not _entity_exists(session, revision_id, relation.object_id):
            continue
        row = session.query(MachineKnowledgeRelation).filter(
            MachineKnowledgeRelation.revision_id == revision_id,
            MachineKnowledgeRelation.subject_id == relation.subject_id,
            MachineKnowledgeRelation.relation_type == relation.relation_type,
            MachineKnowledgeRelation.object_id == relation.object_id,
        ).first()
        if row is None:
            row = MachineKnowledgeRelation(
                revision_id=revision_id,
                subject_id=relation.subject_id,
                subject_name=relation.subject_name,
                relation_type=relation.relation_type,
                object_id=relation.object_id,
                object_name=relation.object_name,
                description=relation.description,
                source_chunk_id=source_chunk_id,
            )
            session.add(row)
            session.flush()
        _persist_evidence(session, "relation", row.id, relation.evidence, revision_id)

    for behavior in model.behaviors:
        if ("behavior", behavior.id) in invalid:
            continue
        row = session.query(MachineKnowledgeBehavior).filter(
            MachineKnowledgeBehavior.revision_id == revision_id,
            MachineKnowledgeBehavior.subject_id == behavior.subject_id,
            MachineKnowledgeBehavior.description == behavior.description,
        ).first()
        if row is None:
            row = MachineKnowledgeBehavior(
                revision_id=revision_id,
                subject_id=behavior.subject_id,
                subject_name=behavior.subject_name,
                description=behavior.description,
                source_chunk_id=source_chunk_id,
            )
            session.add(row)
            session.flush()
        _persist_evidence(session, "behavior", row.id, behavior.evidence, revision_id)

    for fact_type, fact_key, payload in model.all_facts():
        if (fact_type, fact_key) in invalid:
            continue
        row = session.query(MachineKnowledgeFact).filter(
            MachineKnowledgeFact.revision_id == revision_id,
            MachineKnowledgeFact.fact_type == fact_type,
            MachineKnowledgeFact.fact_key == fact_key,
        ).first()
        if row is None:
            session.add(MachineKnowledgeFact(
                revision_id=revision_id,
                fact_type=fact_type,
                fact_key=fact_key,
                payload=payload,
                source_chunk_id=source_chunk_id,
            ))
        else:
            row.payload = {**(row.payload or {}), **payload}
        _persist_evidence_dicts(
            session, fact_type, fact_key, payload.get("evidence", []), revision_id
        )


    _persist_evidence(session, "model", source_chunk_id, model.evidence, revision_id)
    session.flush()


def _entity_exists(session: Session, revision_id: str | None, canonical_id: str) -> bool:
    return session.query(MachineKnowledgeEntity.id).filter(
        MachineKnowledgeEntity.revision_id == revision_id,
        MachineKnowledgeEntity.canonical_id == canonical_id,
    ).first() is not None


def _persist_evidence(
    session: Session,
    item_type: str,
    item_id: str | None,
    evidence: list[MachineEvidence],
    revision_id: str | None,
) -> None:
    for ev in evidence:
        ev.revision_id = ev.revision_id or revision_id
        session.add(MachineKnowledgeEvidence(
            revision_id=ev.revision_id,
            item_type=item_type,
            item_id=item_id,
            fact=ev.fact,
            source_document=ev.source_document,
            page=ev.page,
            chunk=ev.chunk,
            source_type=ev.source_type,
            location=ev.location,
            region=ev.region,
            confidence=ev.confidence,
            extraction_method=ev.extraction_method,
            evidence_metadata=ev.metadata,
        ))


def _persist_evidence_dicts(
    session: Session,
    item_type: str,
    item_id: str,
    evidence: list[dict[str, Any]],
    revision_id: str | None,
) -> None:
    _persist_evidence(
        session,
        item_type,
        item_id,
        [MachineEvidence.model_validate(item) for item in evidence],
        revision_id,
    )
