"""
Product Brain schema (relational form for the Phase 1 MVP).

Mirrors plan.md §3 (Product Brain) and §33 Phase 1 scope:
    Product / Revision / Component / Connection / Procedure / Failure knowledge

Kept relational (not graph-native) deliberately for the MVP — every
"relationship" table below is a graph edge in disguise, and can be
migrated into a real graph store later (plan.md explicitly defers that:
"later investigate ... knowledge graph").

Revision-awareness is load-bearing: almost every table hangs off
Revision rather than Product, because the plan is explicit that the
agent "must never silently apply information from the wrong revision."
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Float,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class DocType(str, enum.Enum):
    manual = "manual"
    schematic = "schematic"
    parts_catalog = "parts_catalog"
    service_ticket = "service_ticket"
    maintenance_schedule = "maintenance_schedule"
    troubleshooting_guide = "troubleshooting_guide"
    other = "other"


class IngestionStatus(str, enum.Enum):
    queued = "queued"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class ChunkType(str, enum.Enum):
    text = "text"
    table = "table"
    diagram = "diagram"          # image/schematic region, with OCR'd/captioned text
    procedure_step = "procedure_step"


class RelationType(str, enum.Enum):
    electrical = "electrical"
    mechanical = "mechanical"
    fluid = "fluid"
    signal = "signal"
    contains = "contains"        # part-of / assembly hierarchy


class ProcedureType(str, enum.Enum):
    installation = "installation"
    removal = "removal"
    calibration = "calibration"
    maintenance = "maintenance"
    troubleshooting = "troubleshooting"
    replacement = "replacement"
    verification = "verification"


class Product(Base):
    """A product family, e.g. 'Vulcan OmniPro 220 welder', 'Acme CNC-500'."""

    __tablename__ = "products"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    manufacturer: Mapped[str] = mapped_column(String, index=True)
    family: Mapped[str] = mapped_column(String, index=True)
    model: Mapped[str] = mapped_column(String, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    revisions: Mapped[list["Revision"]] = relationship(back_populates="product", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("manufacturer", "family", "model", name="uq_product_identity"),)


class Revision(Base):
    """
    A specific hardware/firmware revision of a Product.

    Every Document, Component, Procedure and FailureMode is scoped to a
    Revision (not just a Product) so the agent can filter/ground answers
    to the correct configuration instead of blending revisions.
    """

    __tablename__ = "revisions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    label: Mapped[str] = mapped_column(String)          # e.g. "2023", "Rev C", "fw 4.2"
    effective_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    product: Mapped["Product"] = relationship(back_populates="revisions")
    documents: Mapped[list["Document"]] = relationship(back_populates="revision", cascade="all, delete-orphan")
    components: Mapped[list["Component"]] = relationship(back_populates="revision", cascade="all, delete-orphan")
    procedures: Mapped[list["Procedure"]] = relationship(back_populates="revision", cascade="all, delete-orphan")
    failure_modes: Mapped[list["FailureMode"]] = relationship(back_populates="revision", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("product_id", "label", name="uq_revision_label"),)


class Document(Base):
    """A single ingested source file (manual PDF, schematic sheet, etc.)."""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    revision_id: Mapped[str] = mapped_column(ForeignKey("revisions.id"), index=True)
    doc_type: Mapped[DocType] = mapped_column(Enum(DocType))
    title: Mapped[str] = mapped_column(String)
    source_path: Mapped[str] = mapped_column(String)   # original filename / storage path
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    revision: Mapped["Revision"] = relationship(back_populates="documents")
    chunks: Mapped[list["Chunk"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class IngestionJob(Base):
    """Durable upload state used by the idempotent ingestion API."""

    __tablename__ = "ingestion_jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    revision_id: Mapped[str] = mapped_column(ForeignKey("revisions.id"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String)
    content_sha256: Mapped[str] = mapped_column(String, index=True)
    doc_type: Mapped[DocType] = mapped_column(Enum(DocType))
    title: Mapped[str] = mapped_column(String)
    source_path: Mapped[str] = mapped_column(String)
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id"), nullable=True)
    status: Mapped[IngestionStatus] = mapped_column(Enum(IngestionStatus), default=IngestionStatus.queued)
    stage: Mapped[str] = mapped_column(String, default="queued")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint("revision_id", "idempotency_key", name="uq_ingestion_job_key"),)


class Chunk(Base):
    """
    An atomic retrievable unit produced by ingestion: a text passage, a
    serialized table, or a diagram region + its OCR/caption text.

    This is what actually gets embedded and pushed into Qdrant / BM25.
    The Qdrant point id == this row's id, so hybrid retrieval can fetch
    full row data (page, doc, revision) after a vector/BM25 hit.
    """

    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    chunk_type: Mapped[ChunkType] = mapped_column(Enum(ChunkType))
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content: Mapped[str] = mapped_column(Text)          # plain text / markdown table / OCR text
    extra: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # bbox, image path, table shape, etc.

    document: Mapped["Document"] = relationship(back_populates="chunks")


class Component(Base):
    """A named physical/logical part of the product (relay, sensor, valve...)."""

    __tablename__ = "components"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    revision_id: Mapped[str] = mapped_column(ForeignKey("revisions.id"), index=True)
    name: Mapped[str] = mapped_column(String, index=True)          # e.g. "Relay K17"
    function: Mapped[str | None] = mapped_column(Text, nullable=True)
    location_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    part_number: Mapped[str | None] = mapped_column(String, nullable=True)
    source_chunk_id: Mapped[str | None] = mapped_column(ForeignKey("chunks.id"), nullable=True)

    revision: Mapped["Revision"] = relationship(back_populates="components")


class ComponentRelationship(Base):
    """A directed edge between two components (the 'Connections' in plan.md §3)."""

    __tablename__ = "component_relationships"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    from_component_id: Mapped[str] = mapped_column(ForeignKey("components.id"), index=True)
    to_component_id: Mapped[str] = mapped_column(ForeignKey("components.id"), index=True)
    relation_type: Mapped[RelationType] = mapped_column(Enum(RelationType))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_chunk_id: Mapped[str | None] = mapped_column(ForeignKey("chunks.id"), nullable=True)


class Procedure(Base):
    """An installation/calibration/troubleshooting/... procedure."""

    __tablename__ = "procedures"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    revision_id: Mapped[str] = mapped_column(ForeignKey("revisions.id"), index=True)
    name: Mapped[str] = mapped_column(String)
    procedure_type: Mapped[ProcedureType] = mapped_column(Enum(ProcedureType))
    steps: Mapped[list] = mapped_column(JSON)            # ordered list[str]
    source_chunk_id: Mapped[str | None] = mapped_column(ForeignKey("chunks.id"), nullable=True)

    revision: Mapped["Revision"] = relationship(back_populates="procedures")


class DiagnosticStatus(str, enum.Enum):
    active = "active"          # still asking questions / gathering evidence
    concluded = "concluded"    # agent reached a grounded recommendation
    escalated = "escalated"    # confidence too low / safety constraint hit


class DiagnosticSession(Base):
    """
    One troubleshooting conversation (plan.md §6 DiagnosticState), persisted
    as JSON so the full history (symptoms, observations, hypotheses,
    eliminated hypotheses, evidence, confidence) survives across turns
    without needing a dozen join tables for an MVP.

    `state` shape is defined by diagnostics.schema.DiagnosticState — kept as
    JSON here rather than normalized columns because the state's hypothesis
    list grows/shrinks every turn; normalizing it now would mean migrating
    the schema before the diagnostic loop itself is even validated (see
    plan.md §26 "prove the loop first").
    """

    __tablename__ = "diagnostic_sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    revision_id: Mapped[str | None] = mapped_column(ForeignKey("revisions.id"), nullable=True, index=True)
    status: Mapped[DiagnosticStatus] = mapped_column(Enum(DiagnosticStatus), default=DiagnosticStatus.active)
    state: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class InterviewStatus(str, enum.Enum):
    active = "active"
    completed = "completed"
    cancelled = "cancelled"


class KnowledgeStatus(str, enum.Enum):
    draft = "draft"
    in_review = "in_review"
    approved = "approved"
    rejected = "rejected"
    superseded = "superseded"


class KnowledgeType(str, enum.Enum):
    rule = "rule"
    failure_mode = "failure_mode"
    diagnostic_test = "diagnostic_test"
    repair = "repair"
    exception = "exception"
    heuristic = "heuristic"


class GraphNodeType(str, enum.Enum):
    symptom = "symptom"
    question = "question"
    observation = "observation"
    hypothesis = "hypothesis"
    action = "action"
    outcome = "outcome"
    safety = "safety"


class GraphEdgeType(str, enum.Enum):
    asks = "asks"
    if_true = "if_true"
    if_false = "if_false"
    supports = "supports"
    rules_out = "rules_out"
    leads_to = "leads_to"
    requires = "requires"


class Expert(Base):
    """Senior engineer/domain expert whose tacit knowledge is being captured."""

    __tablename__ = "experts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String)
    role: Mapped[str | None] = mapped_column(String, nullable=True)
    organization: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ExpertInterview(Base):
    """A versioned interview session used to turn tacit expertise into reviewable knowledge."""

    __tablename__ = "expert_interviews"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    expert_id: Mapped[str] = mapped_column(ForeignKey("experts.id"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    revision_id: Mapped[str | None] = mapped_column(ForeignKey("revisions.id"), nullable=True, index=True)
    topic: Mapped[str] = mapped_column(String)
    status: Mapped[InterviewStatus] = mapped_column(Enum(InterviewStatus), default=InterviewStatus.active)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ExpertInterviewTurn(Base):
    """Immutable transcript turn; keeping speaker/content separate makes re-extraction possible."""

    __tablename__ = "expert_interview_turns"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    interview_id: Mapped[str] = mapped_column(ForeignKey("expert_interviews.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    speaker: Mapped[str] = mapped_column(String)  # interviewer | expert
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("interview_id", "sequence", name="uq_interview_turn_sequence"),)


class KnowledgeVersion(Base):
    """A reviewable, immutable snapshot of knowledge extracted from an interview."""

    __tablename__ = "knowledge_versions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    interview_id: Mapped[str] = mapped_column(ForeignKey("expert_interviews.id"), index=True)
    revision_id: Mapped[str | None] = mapped_column(ForeignKey("revisions.id"), nullable=True, index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[KnowledgeStatus] = mapped_column(Enum(KnowledgeStatus), default=KnowledgeStatus.draft)
    extracted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reviewer: Mapped[str | None] = mapped_column(String, nullable=True)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (UniqueConstraint("interview_id", "version", name="uq_knowledge_version"),)


class ExpertKnowledge(Base):
    """One atomic knowledge claim, always tied to a specific version and evidence turn."""

    __tablename__ = "expert_knowledge"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    knowledge_version_id: Mapped[str] = mapped_column(ForeignKey("knowledge_versions.id"), index=True)
    knowledge_type: Mapped[KnowledgeType] = mapped_column(Enum(KnowledgeType))
    title: Mapped[str] = mapped_column(String)
    symptom: Mapped[str | None] = mapped_column(Text, nullable=True)
    trigger: Mapped[str | None] = mapped_column(Text, nullable=True)
    condition: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_observation: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_mode: Mapped[str | None] = mapped_column(Text, nullable=True)
    safety_notes: Mapped[list] = mapped_column(JSON, default=list)
    applicable_models: Mapped[list] = mapped_column(JSON, default=list)
    applicable_revisions: Mapped[list] = mapped_column(JSON, default=list)
    evidence_turn_ids: Mapped[list] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)  # stored as 0-100 for portable SQLite
    source_quote: Mapped[str | None] = mapped_column(Text, nullable=True)


class DiagnosticGraph(Base):
    """Executable troubleshooting graph generated from approved expert knowledge."""

    __tablename__ = "diagnostic_graphs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    revision_id: Mapped[str | None] = mapped_column(ForeignKey("revisions.id"), nullable=True, index=True)
    knowledge_version_id: Mapped[str] = mapped_column(ForeignKey("knowledge_versions.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[KnowledgeStatus] = mapped_column(Enum(KnowledgeStatus), default=KnowledgeStatus.draft)
    name: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DiagnosticGraphNode(Base):
    __tablename__ = "diagnostic_graph_nodes"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    graph_id: Mapped[str] = mapped_column(ForeignKey("diagnostic_graphs.id"), index=True)
    node_type: Mapped[GraphNodeType] = mapped_column(Enum(GraphNodeType))
    label: Mapped[str] = mapped_column(String)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    knowledge_id: Mapped[str | None] = mapped_column(ForeignKey("expert_knowledge.id"), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class DiagnosticGraphEdge(Base):
    __tablename__ = "diagnostic_graph_edges"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    graph_id: Mapped[str] = mapped_column(ForeignKey("diagnostic_graphs.id"), index=True)
    from_node_id: Mapped[str] = mapped_column(ForeignKey("diagnostic_graph_nodes.id"), index=True)
    to_node_id: Mapped[str] = mapped_column(ForeignKey("diagnostic_graph_nodes.id"), index=True)
    edge_type: Mapped[GraphEdgeType] = mapped_column(Enum(GraphEdgeType))
    condition: Mapped[str | None] = mapped_column(Text, nullable=True)
    knowledge_id: Mapped[str | None] = mapped_column(ForeignKey("expert_knowledge.id"), nullable=True)


class SymbolType(str, enum.Enum):
    """Recognized schematic symbol categories (plan.md §5)."""

    relay = "relay"
    contactor = "contactor"
    motor = "motor"
    sensor = "sensor"
    switch = "switch"
    fuse = "fuse"
    terminal = "terminal"
    connector = "connector"
    plc = "plc"
    transformer = "transformer"
    power_source = "power_source"
    ground = "ground"
    other = "other"


class WireType(str, enum.Enum):
    power = "power"
    control = "control"
    signal = "signal"
    ground = "ground"
    unknown = "unknown"


class SchematicNode(Base):
    """
    One symbol/component identified inside a schematic image (plan.md §5:
    "Extract: Components and symbols, Labels, Terminals ...").

    Scoped to the diagram Chunk it was extracted from (not directly to
    Revision) because a node's bbox is only meaningful against that one
    image; `component_id` is the optional link back to the Phase 1
    text-extracted Component with the same name, resolved by exact label
    match at persistence time (see schematic/graph.py) — a fuzzier
    match is a reasonable upgrade once real manuals show how often
    labels disagree slightly (e.g. "K17" vs "Relay K17").
    """

    __tablename__ = "schematic_nodes"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    chunk_id: Mapped[str] = mapped_column(ForeignKey("chunks.id"), index=True)
    component_id: Mapped[str | None] = mapped_column(ForeignKey("components.id"), nullable=True)
    label: Mapped[str] = mapped_column(String, index=True)          # e.g. "K17", "X12", "M1"
    symbol_type: Mapped[SymbolType] = mapped_column(Enum(SymbolType))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    bbox: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {"x":0-1,"y":0-1,"w":0-1,"h":0-1} normalized


class SchematicEdge(Base):
    """A wire/connection between two schematic nodes within the same diagram."""

    __tablename__ = "schematic_edges"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    from_node_id: Mapped[str] = mapped_column(ForeignKey("schematic_nodes.id"), index=True)
    to_node_id: Mapped[str] = mapped_column(ForeignKey("schematic_nodes.id"), index=True)
    wire_type: Mapped[WireType] = mapped_column(Enum(WireType), default=WireType.unknown)
    label: Mapped[str | None] = mapped_column(String, nullable=True)  # wire number/label if visible


class FailureMode(Base):
    """
    Symptom -> possible causes -> diagnostic test -> expected observation
    -> repair procedure, per plan.md §3 'Failure knowledge'.

    Phase 1 only needs to *store* this (extracted from docs / seeded
    manually); Phase 2 (Diagnostic Agent) is what walks the graph.
    """

    __tablename__ = "failure_modes"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    revision_id: Mapped[str] = mapped_column(ForeignKey("revisions.id"), index=True)
    symptom: Mapped[str] = mapped_column(Text)
    possible_causes: Mapped[list] = mapped_column(JSON)   # list[str]
    diagnostic_test: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_observation: Mapped[str | None] = mapped_column(Text, nullable=True)
    repair_procedure_id: Mapped[str | None] = mapped_column(ForeignKey("procedures.id"), nullable=True)
    source_chunk_id: Mapped[str | None] = mapped_column(ForeignKey("chunks.id"), nullable=True)

    revision: Mapped["Revision"] = relationship(back_populates="failure_modes")


class MachineKnowledgeEntity(Base):
    """Universal machine model entity persisted in the database."""

    __tablename__ = "machine_knowledge_entities"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    revision_id: Mapped[str | None] = mapped_column(ForeignKey("revisions.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String, index=True)
    entity_type: Mapped[str] = mapped_column(String, default="component")
    properties: Mapped[dict | None] = mapped_column(JSON, default=dict)
    ports: Mapped[list] = mapped_column(JSON, default=list)
    states: Mapped[list] = mapped_column(JSON, default=list)
    source_chunk_id: Mapped[str | None] = mapped_column(ForeignKey("chunks.id"), nullable=True)
    canonical_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)


class MachineKnowledgeRelation(Base):
    """Universal machine model relation persisted in the database."""

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
    canonical_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)


class MachineKnowledgeEvidence(Base):
    """Traceable evidence attached to any universal-machine fact."""

    __tablename__ = "machine_knowledge_evidence"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    item_type: Mapped[str] = mapped_column(String, index=True)  # entity/relation/behavior/state
    item_id: Mapped[str] = mapped_column(String, index=True)
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
    """Behavior-level knowledge persisted for machine simulations."""

    __tablename__ = "machine_knowledge_behaviors"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    revision_id: Mapped[str | None] = mapped_column(ForeignKey("revisions.id"), nullable=True, index=True)
    subject_id: Mapped[str | None] = mapped_column(String, nullable=True)
    subject_name: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(Text)
    source_chunk_id: Mapped[str | None] = mapped_column(ForeignKey("chunks.id"), nullable=True)


class MachineKnowledgeFact(Base):
    """Persisted universal primitive not requiring a machine-specific table."""

    __tablename__ = "machine_knowledge_facts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    revision_id: Mapped[str | None] = mapped_column(ForeignKey("revisions.id"), nullable=True, index=True)
    fact_type: Mapped[str] = mapped_column(String, index=True)
    fact_key: Mapped[str] = mapped_column(String, index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    source_chunk_id: Mapped[str | None] = mapped_column(ForeignKey("chunks.id"), nullable=True)
    canonical_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)


class MachineKnowledgeModelSnapshot(Base):
    """Revision-wide canonical UniversalMachineModel produced by global integration."""
    __tablename__ = "machine_knowledge_model_snapshots"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    revision_id: Mapped[str] = mapped_column(ForeignKey("revisions.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    model: Mapped[dict] = mapped_column(JSON)
    source_counts: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Phase 6 — Functional Digital Twin
# ---------------------------------------------------------------------------

class TwinStatus(str, enum.Enum):
    active = "active"
    archived = "archived"


class TwinEventType(str, enum.Enum):
    command = "command"
    transition = "transition"
    reset = "reset"


class DigitalTwin(Base):
    """A revision-scoped deterministic functional model of a physical machine."""
    __tablename__ = "digital_twins"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    revision_id: Mapped[str] = mapped_column(ForeignKey("revisions.id"), index=True)
    name: Mapped[str] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    definition: Mapped[dict] = mapped_column(JSON)
    state: Mapped[dict] = mapped_column(JSON)
    model_version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[TwinStatus] = mapped_column(Enum(TwinStatus), default=TwinStatus.active)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DigitalTwinEvent(Base):
    """Append-only simulation audit trail: command, before/after state and trace."""
    __tablename__ = "digital_twin_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    twin_id: Mapped[str] = mapped_column(ForeignKey("digital_twins.id"), index=True)
    event_type: Mapped[TwinEventType] = mapped_column(Enum(TwinEventType))
    command: Mapped[dict] = mapped_column(JSON)
    state_before: Mapped[dict] = mapped_column(JSON)
    state_after: Mapped[dict] = mapped_column(JSON)
    trace: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
