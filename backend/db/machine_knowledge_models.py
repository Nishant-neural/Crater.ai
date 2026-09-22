"""Phase 8A persistence models for canonical machine knowledge."""
from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.models import Base, _uuid


class MachineKnowledgeEntity(Base):
    __tablename__ = "machine_knowledge_entities"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    revision_id: Mapped[str | None] = mapped_column(ForeignKey("revisions.id"), nullable=True, index=True)
    canonical_id: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String, index=True)
    entity_type: Mapped[str] = mapped_column(String, default="component")
    properties: Mapped[dict | None] = mapped_column(JSON, default=dict)
    source_chunk_id: Mapped[str | None] = mapped_column(ForeignKey("chunks.id"), nullable=True)


class MachineKnowledgeRelation(Base):
    __tablename__ = "machine_knowledge_relations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    revision_id: Mapped[str | None] = mapped_column(ForeignKey("revisions.id"), nullable=True, index=True)
    subject_id: Mapped[str] = mapped_column(String, index=True)
    subject_name: Mapped[str] = mapped_column(String)
    relation_type: Mapped[str] = mapped_column(String, index=True)
    object_id: Mapped[str] = mapped_column(String, index=True)
    object_name: Mapped[str] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_chunk_id: Mapped[str | None] = mapped_column(ForeignKey("chunks.id"), nullable=True)


class MachineKnowledgeEvidence(Base):
    __tablename__ = "machine_knowledge_evidence"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    revision_id: Mapped[str | None] = mapped_column(ForeignKey("revisions.id"), nullable=True, index=True)
    item_type: Mapped[str] = mapped_column(String, index=True)
    item_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    fact: Mapped[str] = mapped_column(Text)
    source_document: Mapped[str | None] = mapped_column(String, nullable=True)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk: Mapped[str | None] = mapped_column(String, nullable=True)
    source_type: Mapped[str | None] = mapped_column(String, nullable=True)
    location: Mapped[str | None] = mapped_column(String, nullable=True)
    region: Mapped[str | None] = mapped_column(String, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    extraction_method: Mapped[str | None] = mapped_column(String, nullable=True)
    evidence_metadata: Mapped[dict | None] = mapped_column(JSON, default=dict)


class MachineKnowledgeBehavior(Base):
    __tablename__ = "machine_knowledge_behaviors"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    revision_id: Mapped[str | None] = mapped_column(ForeignKey("revisions.id"), nullable=True, index=True)
    subject_id: Mapped[str | None] = mapped_column(String, nullable=True)
    subject_name: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(Text)
    source_chunk_id: Mapped[str | None] = mapped_column(ForeignKey("chunks.id"), nullable=True)


class MachineKnowledgeFact(Base):
    __tablename__ = "machine_knowledge_facts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    revision_id: Mapped[str | None] = mapped_column(ForeignKey("revisions.id"), nullable=True, index=True)
    fact_type: Mapped[str] = mapped_column(String, index=True)
    fact_key: Mapped[str] = mapped_column(String, index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    source_chunk_id: Mapped[str | None] = mapped_column(ForeignKey("chunks.id"), nullable=True)
