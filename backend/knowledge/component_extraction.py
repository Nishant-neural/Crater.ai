"""
LLM-based structured extraction: turns raw chunk text into Component /
ComponentRelationship / Procedure rows (plan.md §3-4).

This is intentionally a thin, single-purpose call — extract from ONE
chunk at a time, with the chunk id carried through as source_chunk_id
so every extracted fact stays traceable to its evidence (plan.md §8:
answers must expose "source document / page / section"). Batching
chunks together for efficiency is a reasonable later optimization, but
it makes provenance fuzzier, so it isn't the MVP default.

Extraction quality on tables/diagram-caption chunks will be weaker than
on prose text chunks — that's expected at this stage; treat low-yield
extractions as a retrieval-quality signal, not a bug to chase yet.
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from backend.db.models import (
    Component,
    ComponentRelationship,
    MachineKnowledgeBehavior,
    MachineKnowledgeEntity,
    MachineKnowledgeEvidence,
    MachineKnowledgeFact,
    MachineKnowledgeRelation,
    Procedure,
    RelationType,
    ProcedureType,
)
from backend.knowledge.evidence import MachineEvidence
from backend.knowledge.machine_model import (
    MachineBehavior,
    MachineConstraint,
    MachineEntity,
    MachineEvent,
    MachineRelation,
    MachinePort,
    MachineQuantity,
    MachineState,
    UniversalMachineModel,
)
from backend.knowledge.schema import ExtractionResult, MachineKnowledgeExtractionResult
from backend.llm import get_llm_provider

_EXTRACTION_PROMPT = """You are extracting structured technical knowledge from one page of an \
industrial equipment manual. Only extract what is explicitly stated — do not infer or \
invent components, connections, or procedures that aren't clearly described in the text.

If the text contains no extractable technical knowledge (e.g. it's a cover page, \
table of contents, or legal boilerplate), return empty lists.

Text:
---
{content}
---

Respond with ONLY JSON matching this shape (omit fields that don't apply, use empty lists \
where nothing was found). Every extracted item must include an evidence object with fact, \
source_type, confidence from 0.0 to 1.0, and extraction_method. Use status KNOWN, UNKNOWN, \
or UNCERTAIN when a value is not explicit:

{{
    "components": [{{"name": "", "function": "", "location_description": "", "part_number": "", "evidence": {{"fact": "", "source_type": "text", "confidence": 0.0, "extraction_method": "llm"}}}}],
    "relationships": [{{"from_component": "", "to_component": "", "relation_type": "electrical|mechanical|fluid|signal|contains", "description": "", "evidence": {{"fact": "", "source_type": "text", "confidence": 0.0, "extraction_method": "llm"}}}}],
    "procedures": [{{"name": "", "procedure_type": "installation|removal|calibration|maintenance|troubleshooting|replacement|verification", "steps": [""]}}],
    "entities": [{{"id": "entity:p1", "name": "", "entity_type": "", "properties": {{}}, "ports": [], "states": [], "evidence": []}}],
    "relations": [{{"subject_id": "", "subject_name": "", "relation_type": "", "object_id": "", "object_name": "", "description": "", "evidence": []}}],
    "ports": [{{"id": "port:p1", "entity_id": "", "name": "", "direction": "", "properties": {{}}, "evidence": []}}],
    "quantities": [{{"id": "quantity:q1", "entity_id": "", "name": "", "value": "", "unit": "", "properties": {{}}, "evidence": []}}],
    "states": [{{"entity_id": "", "name": "", "value": "", "properties": {{}}, "evidence": []}}],
    "events": [{{"id": "event:e1", "entity_id": "", "name": "", "description": "", "evidence": []}}],
    "behaviors": [{{"id": "behavior:b1", "subject_id": "", "subject_name": "", "description": "", "evidence": []}}],
    "constraints": [{{"id": "constraint:c1", "entity_id": "", "name": "", "description": "", "evidence": []}}]
}}"""


def _extract_result(content: str) -> MachineKnowledgeExtractionResult:
    raw = get_llm_provider().complete(
        messages=[{"role": "user", "content": _EXTRACTION_PROMPT.format(content=content)}],
        max_tokens=4000,
    )
    if not raw:
        return MachineKnowledgeExtractionResult()
    try:
        return MachineKnowledgeExtractionResult.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValueError):
        return MachineKnowledgeExtractionResult()


def _legacy_result(result: MachineKnowledgeExtractionResult) -> ExtractionResult:
    return ExtractionResult(
        components=result.components,
        relationships=result.relationships,
        procedures=result.procedures,
    )


def extract_chunk_knowledge(content: str) -> tuple[ExtractionResult, UniversalMachineModel]:
    """Extract legacy Product Brain rows and universal facts in one provider call."""
    result = _extract_result(content)
    legacy = _legacy_result(result)
    if _has_universal_facts(result):
        return legacy, _universal_model_from_result(result)
    return legacy, _legacy_model(result)


def _has_universal_facts(result: MachineKnowledgeExtractionResult) -> bool:
    return any((
        result.entities, result.relations, result.ports, result.quantities,
        result.states, result.events, result.behaviors, result.constraints,
    ))


def extract_from_chunk_text(content: str) -> ExtractionResult:
    return extract_chunk_knowledge(content)[0]


def persist_extraction(
    session: Session,
    revision_id: str,
    source_chunk_id: str,
    result: ExtractionResult,
) -> None:
    """Write extracted components/relationships/procedures, linked back to their source chunk."""
    name_to_id: dict[str, str] = {}

    for comp in result.components:
        row = Component(
            revision_id=revision_id,
            name=comp.name,
            function=comp.function,
            location_description=comp.location_description,
            part_number=comp.part_number,
            source_chunk_id=source_chunk_id,
        )
        session.add(row)
        session.flush()  # get row.id without committing
        name_to_id[comp.name] = row.id

    for rel in result.relationships:
        from_id = name_to_id.get(rel.from_component)
        to_id = name_to_id.get(rel.to_component)
        if not (from_id and to_id):
            # Referenced a component not extracted in this same chunk (common —
            # relationships often span text extracted from other pages). Skip
            # rather than guess; a later cross-chunk linking pass can resolve
            # these by name within the same revision.
            continue
        try:
            relation_type = RelationType(rel.relation_type)
        except ValueError:
            continue
        session.add(
            ComponentRelationship(
                from_component_id=from_id,
                to_component_id=to_id,
                relation_type=relation_type,
                description=rel.description,
                source_chunk_id=source_chunk_id,
            )
        )

    for proc in result.procedures:
        try:
            procedure_type = ProcedureType(proc.procedure_type)
        except ValueError:
            continue
        session.add(
            Procedure(
                revision_id=revision_id,
                name=proc.name,
                procedure_type=procedure_type,
                steps=proc.steps,
                source_chunk_id=source_chunk_id,
            )
        )

    # The pipeline commits the complete extraction in one transaction. This
    # prevents retries from accumulating a partially extracted Product Brain.
    session.flush()


def extract_machine_knowledge_from_chunk(content: str) -> UniversalMachineModel:
    """Build a Phase 8A universal machine model from text evidence.

    This compatibility layer intentionally keeps the legacy extractor intact
    while exposing the universal representation expected by the Phase 8A docs.
    """
    return extract_chunk_knowledge(content)[1]


def _legacy_model(result: MachineKnowledgeExtractionResult) -> UniversalMachineModel:
    legacy = _legacy_result(result)
    model = UniversalMachineModel()
    for comp in legacy.components:
        evidence = [MachineEvidence(
            fact=f"Component {comp.name} extracted from manual text",
            source_type="text",
            confidence=0.7,
            extraction_method="legacy_component_extractor",
        )]
        model.entities.append(MachineEntity(
            id=f"entity:{comp.name.lower().replace(' ', '-')}",
            name=comp.name,
            entity_type="component",
            properties={
                "function": comp.function,
                "location_description": comp.location_description,
                "part_number": comp.part_number,
            },
            evidence=evidence,
        ))
    for rel in legacy.relationships:
        model.relations.append(MachineRelation(
            subject_id=f"entity:{rel.from_component.lower().replace(' ', '-')}",
            subject_name=rel.from_component,
            relation_type=rel.relation_type,
            object_id=f"entity:{rel.to_component.lower().replace(' ', '-')}",
            object_name=rel.to_component,
            description=rel.description,
            evidence=[MachineEvidence(
                fact=f"{rel.from_component} {rel.relation_type} {rel.to_component}",
                source_type="text",
                confidence=0.7,
                extraction_method="legacy_relationship_extractor",
            )],
        ))
    for proc in legacy.procedures:
        model.behaviors.append(MachineBehavior(
            id=f"behavior:{proc.name.lower().replace(' ', '-')}",
            subject_id="",
            subject_name=proc.name,
            description="Procedure: " + "; ".join(proc.steps),
            evidence=[MachineEvidence(
                fact=f"Procedure {proc.name} extracted",
                source_type="text",
                confidence=0.6,
                extraction_method="legacy_procedure_extractor",
            )],
        ))
    return model


def _universal_model_from_result(result: MachineKnowledgeExtractionResult) -> UniversalMachineModel:
    """Convert the provider-neutral extraction contract into typed primitives."""
    model = UniversalMachineModel()
    for item in result.entities:
        model.entities.append(MachineEntity.model_validate(item.model_dump()))
    for item in result.relations:
        model.relations.append(MachineRelation.model_validate(item.model_dump()))
    for item in result.ports:
        model.ports.append(MachinePort.model_validate(item))
    for item in result.quantities:
        model.quantities.append(MachineQuantity.model_validate(item))
    for item in result.states:
        model.states.append(MachineState.model_validate(item))
    for item in result.events:
        model.events.append(MachineEvent.model_validate(item))
    for item in result.behaviors:
        model.behaviors.append(MachineBehavior.model_validate(item))
    for item in result.constraints:
        model.constraints.append(MachineConstraint.model_validate(item))
    model.evidence.extend(result.evidence)
    return model


def persist_machine_knowledge(session: Session, revision_id: str | None, source_chunk_id: str | None, model: UniversalMachineModel) -> None:
    """Persist the universal machine model into project tables for later graph/RAG work."""
    for entity in model.entities:
        db_entity = MachineKnowledgeEntity(
            revision_id=revision_id,
            name=entity.name,
            entity_type=entity.entity_type,
            properties=entity.properties,
            ports=entity.ports,
            states=entity.states,
            source_chunk_id=source_chunk_id,
        )
        session.add(db_entity)
        session.flush()
        _persist_evidence(session, "entity", db_entity.id, entity.evidence)

    for rel in model.relations:
        db_rel = MachineKnowledgeRelation(
            revision_id=revision_id,
            subject_id=rel.subject_id,
            subject_name=rel.subject_name,
            relation_type=rel.relation_type,
            object_id=rel.object_id,
            object_name=rel.object_name,
            description=rel.description,
            source_chunk_id=source_chunk_id,
        )
        session.add(db_rel)
        session.flush()
        _persist_evidence(session, "relation", db_rel.id, rel.evidence)

    for behavior in model.behaviors:
        db_behavior = MachineKnowledgeBehavior(
            revision_id=revision_id,
            subject_id=behavior.subject_id,
            subject_name=behavior.subject_name,
            description=behavior.description,
            source_chunk_id=source_chunk_id,
        )
        session.add(db_behavior)
        session.flush()
        _persist_evidence(session, "behavior", db_behavior.id, behavior.evidence)

    for fact_type, fact_key, payload in model.all_facts():
        if fact_type == "behavior":
            continue  # Behaviors already have the dedicated legacy table above.
        session.add(MachineKnowledgeFact(
            revision_id=revision_id,
            fact_type=fact_type,
            fact_key=fact_key,
            payload=payload,
            source_chunk_id=source_chunk_id,
        ))
        _persist_evidence_dicts(session, fact_type, fact_key, payload.get("evidence", []))

    _persist_evidence(session, "model", source_chunk_id, model.evidence)

    session.flush()


def _persist_evidence(session: Session, item_type: str, item_id: str | None, evidence: list[MachineEvidence]) -> None:
    for ev in evidence:
        session.add(MachineKnowledgeEvidence(
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


def _persist_evidence_dicts(session: Session, item_type: str, item_id: str, evidence: list[dict]) -> None:
    for item in evidence:
        session.add(MachineKnowledgeEvidence(
            item_type=item_type,
            item_id=item_id,
            fact=item.get("fact", f"{item_type}:{item_id}"),
            source_document=item.get("source_document"),
            page=item.get("page"),
            chunk=item.get("chunk"),
            source_type=item.get("source_type"),
            location=item.get("location"),
            region=item.get("region"),
            confidence=item.get("confidence", 0.0),
            extraction_method=item.get("extraction_method"),
            evidence_metadata=item.get("metadata", {}),
        ))

