"""Pydantic contracts for Phase 5 — Capture Rajesh."""

from __future__ import annotations

from pydantic import BaseModel, Field


class InterviewStart(BaseModel):
    expert_id: str
    product_id: str
    revision_id: str | None = None
    topic: str


class InterviewTurnRequest(BaseModel):
    content: str
    speaker: str = "expert"


class InterviewTurnView(BaseModel):
    id: str
    sequence: int
    speaker: str
    content: str


class InterviewView(BaseModel):
    interview_id: str
    expert_id: str
    product_id: str
    revision_id: str | None
    topic: str
    status: str
    next_question: str | None = None
    turns: list[InterviewTurnView] = Field(default_factory=list)


class ExtractedKnowledgeItem(BaseModel):
    knowledge_type: str
    title: str
    symptom: str | None = None
    trigger: str | None = None
    condition: str | None = None
    action: str | None = None
    expected_observation: str | None = None
    failure_mode: str | None = None
    safety_notes: list[str] = Field(default_factory=list)
    applicable_models: list[str] = Field(default_factory=list)
    applicable_revisions: list[str] = Field(default_factory=list)
    evidence_turn_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source_quote: str | None = None


class KnowledgeExtractionResult(BaseModel):
    items: list[ExtractedKnowledgeItem] = Field(default_factory=list)


class ReviewRequest(BaseModel):
    decision: str  # approved | rejected
    reviewer: str
    notes: str | None = None


class KnowledgeItemView(ExtractedKnowledgeItem):
    id: str
    knowledge_version_id: str


class KnowledgeVersionView(BaseModel):
    id: str
    interview_id: str
    revision_id: str | None
    version: int
    status: str
    reviewer: str | None = None
    review_notes: str | None = None
    items: list[KnowledgeItemView] = Field(default_factory=list)


class GraphNodeView(BaseModel):
    id: str
    node_type: str
    label: str
    content: str | None = None
    knowledge_id: str | None = None


class GraphEdgeView(BaseModel):
    id: str
    from_node_id: str
    to_node_id: str
    edge_type: str
    condition: str | None = None
    knowledge_id: str | None = None


class DiagnosticGraphView(BaseModel):
    id: str
    revision_id: str | None
    knowledge_version_id: str
    version: int
    status: str
    name: str
    nodes: list[GraphNodeView] = Field(default_factory=list)
    edges: list[GraphEdgeView] = Field(default_factory=list)
