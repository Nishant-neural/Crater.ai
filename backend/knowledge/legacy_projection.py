"""Compatibility projection for the pre-Phase-8 Product Brain tables.

The canonical machine model is built elsewhere. This module is deliberately
small and exists only so older Phase 1-7 consumers can keep receiving
Component / Relationship / Procedure rows during the migration.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from backend.db.models import (
    Component,
    ComponentRelationship,
    Procedure,
    ProcedureType,
    RelationType,
)
from backend.knowledge.evidence import MachineEvidence
from backend.knowledge.machine_model import (
    MachineEntity,
    MachineProcedure,
    MachineRelation,
    UniversalMachineModel,
)
from backend.knowledge.schema import ExtractionResult, MachineKnowledgeExtractionResult


def to_legacy_result(result: MachineKnowledgeExtractionResult) -> ExtractionResult:
    return ExtractionResult(
        components=result.components,
        relationships=result.relationships,
        procedures=result.procedures,
    )


def canonical_from_legacy(result: MachineKnowledgeExtractionResult) -> UniversalMachineModel:
    """Project legacy provider output into the canonical machine model."""
    model = UniversalMachineModel()
    for comp in result.components:
        entity_id = f"entity:{_slug(comp.name)}"
        model.entities.append(MachineEntity(
            id=entity_id,
            name=comp.name,
            entity_type="component",
            properties={
                "function": comp.function,
                "location_description": comp.location_description,
                "part_number": comp.part_number,
            },
            evidence=_evidence_list(
                comp.evidence,
                f"Component {comp.name} extracted from manual text",
            ),
        ))

    for rel in result.relationships:
        model.relations.append(MachineRelation(
            subject_id=f"entity:{_slug(rel.from_component)}",
            subject_name=rel.from_component,
            relation_type=rel.relation_type,
            object_id=f"entity:{_slug(rel.to_component)}",
            object_name=rel.to_component,
            description=rel.description,
            evidence=_evidence_list(
                rel.evidence,
                f"{rel.from_component} {rel.relation_type} {rel.to_component}",
            ),
        ))

    for proc in result.procedures:
        model.procedures.append(MachineProcedure(
            id=f"procedure:{_slug(proc.name)}",
            name=proc.name,
            procedure_type=proc.procedure_type,
            steps=proc.steps,
            evidence=_evidence_list(
                proc.evidence,
                f"Procedure {proc.name} extracted",
            ),
        ))
    return model


def persist_legacy_extraction(
    session: Session,
    revision_id: str,
    source_chunk_id: str,
    result: ExtractionResult,
) -> None:
    """Persist the old Product Brain projection only."""
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
        session.flush()
        name_to_id[comp.name] = row.id

    for rel in result.relationships:
        from_id = name_to_id.get(rel.from_component)
        to_id = name_to_id.get(rel.to_component)
        if not (from_id and to_id):
            continue
        try:
            relation_type = RelationType(rel.relation_type)
        except ValueError:
            continue
        session.add(ComponentRelationship(
            from_component_id=from_id,
            to_component_id=to_id,
            relation_type=relation_type,
            description=rel.description,
            source_chunk_id=source_chunk_id,
        ))

    for proc in result.procedures:
        try:
            procedure_type = ProcedureType(proc.procedure_type)
        except ValueError:
            continue
        session.add(Procedure(
            revision_id=revision_id,
            name=proc.name,
            procedure_type=procedure_type,
            steps=proc.steps,
            source_chunk_id=source_chunk_id,
        ))
    session.flush()


def _slug(value: str) -> str:
    return "-".join(value.lower().split())


def _evidence_list(items: list[dict], fallback_fact: str) -> list[MachineEvidence]:
    if items:
        return [MachineEvidence.model_validate(item) for item in items]
    return [MachineEvidence(
        fact=fallback_fact,
        source_type="text",
        confidence=0.7,
        extraction_method="legacy_projection",
    )]
