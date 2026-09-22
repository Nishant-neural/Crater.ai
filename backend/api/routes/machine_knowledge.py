"""Read APIs for the Phase 8A universal machine knowledge foundation."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.db.models import (
    MachineKnowledgeBehavior, MachineKnowledgeEntity, MachineKnowledgeEvidence, MachineKnowledgeFact,
    MachineKnowledgeRelation, Revision,
)
from backend.db.session import get_session

router = APIRouter(prefix="/knowledge", tags=["machine knowledge"])


def _revision_or_404(db: Session, revision_id: str) -> Revision:
    revision = db.get(Revision, revision_id)
    if not revision:
        raise HTTPException(404, "Revision not found")
    return revision


@router.get("/revisions/{revision_id}")
def get_machine_knowledge(
    revision_id: str,
    fact_type: str | None = None,
    name: str | None = None,
    db: Session = Depends(get_session),
):
    _revision_or_404(db, revision_id)
    entities = db.query(MachineKnowledgeEntity).filter(MachineKnowledgeEntity.revision_id == revision_id)
    relations = db.query(MachineKnowledgeRelation).filter(MachineKnowledgeRelation.revision_id == revision_id)
    facts = db.query(MachineKnowledgeFact).filter(MachineKnowledgeFact.revision_id == revision_id)
    behaviors = db.query(MachineKnowledgeBehavior).filter(MachineKnowledgeBehavior.revision_id == revision_id)
    if name:
        entities = entities.filter(MachineKnowledgeEntity.name.ilike(f"%{name}%"))
        relations = relations.filter(
            (MachineKnowledgeRelation.subject_name.ilike(f"%{name}%")) |
            (MachineKnowledgeRelation.object_name.ilike(f"%{name}%"))
        )
        behaviors = behaviors.filter(MachineKnowledgeBehavior.subject_name.ilike(f"%{name}%"))
    if fact_type:
        facts = facts.filter(MachineKnowledgeFact.fact_type == fact_type)
    entity_rows = entities.all()
    relation_rows = relations.all()
    fact_rows = facts.all()
    behavior_rows = behaviors.all()
    item_ids = [row.id for row in entity_rows + relation_rows] + [row.fact_key for row in fact_rows]
    evidence = db.query(MachineKnowledgeEvidence).filter(
        MachineKnowledgeEvidence.item_id.in_(item_ids or [""])
    ).all()
    return {
        "revision_id": revision_id,
        "entities": [
            {"id": row.id, "canonical_id": row.canonical_id, "name": row.name,
             "entity_type": row.entity_type, "properties": row.properties or {}}
            for row in entity_rows
        ],
        "relations": [
            {"id": row.id, "subject_id": row.subject_id, "subject_name": row.subject_name,
             "relation_type": row.relation_type, "object_id": row.object_id,
             "object_name": row.object_name, "description": row.description}
            for row in relation_rows
        ],
        "facts": [{"id": row.id, "fact_type": row.fact_type, "fact_key": row.fact_key, "payload": row.payload} for row in fact_rows],
        "behaviors": [
            {"id": row.id, "subject_id": row.subject_id, "subject_name": row.subject_name,
             "description": row.description}
            for row in behavior_rows
        ],
        "evidence": [
            {"id": row.id, "item_type": row.item_type, "item_id": row.item_id,
             "fact": row.fact, "source_document": row.source_document, "page": row.page,
             "chunk": row.chunk, "source_type": row.source_type, "location": row.location,
             "region": row.region, "confidence": row.confidence,
             "extraction_method": row.extraction_method}
            for row in evidence
        ],
    }


@router.get("/revisions/{revision_id}/entities")
def list_machine_entities(revision_id: str, name: str | None = None, db: Session = Depends(get_session)):
    _revision_or_404(db, revision_id)
    query = db.query(MachineKnowledgeEntity).filter(MachineKnowledgeEntity.revision_id == revision_id)
    if name:
        query = query.filter(MachineKnowledgeEntity.name.ilike(f"%{name}%"))
    return [
        {"id": row.id, "canonical_id": row.canonical_id, "name": row.name,
         "entity_type": row.entity_type, "properties": row.properties or {}}
        for row in query.all()
    ]


@router.get("/revisions/{revision_id}/facts")
def list_machine_facts(revision_id: str, fact_type: str | None = None, db: Session = Depends(get_session)):
    _revision_or_404(db, revision_id)
    query = db.query(MachineKnowledgeFact).filter(MachineKnowledgeFact.revision_id == revision_id)
    if fact_type:
        query = query.filter(MachineKnowledgeFact.fact_type == fact_type)
    return [{"id": row.id, "fact_type": row.fact_type, "fact_key": row.fact_key, "payload": row.payload} for row in query.all()]