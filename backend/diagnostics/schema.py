"""
Structured state for one diagnostic session, per plan.md §6:

    DiagnosticState
    ├── Product
    ├── Model
    ├── Revision
    ├── Symptoms
    ├── Observations
    ├── Measurements
    ├── Hypotheses
    ├── Eliminated hypotheses
    ├── Evidence
    ├── Current diagnostic step
    ├── Safety constraints
    └── Confidence

Product/Model/Revision live outside `state` (as DiagnosticSession.product_id
/ revision_id columns) since they're set once at session start and used for
retrieval filtering; everything else is genuinely turn-by-turn and lives in
this JSON blob.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Hypothesis(BaseModel):
    cause: str
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)
    supporting_evidence_chunk_ids: list[str] = Field(default_factory=list)


class EvidenceRef(BaseModel):
    chunk_id: str
    content: str
    source_document: str | None = None
    page_number: int | None = None


class NextStepType(str, Enum):
    question = "question"          # ask the technician a diagnostic question
    action = "action"               # ask the technician to perform a measurement/test
    conclusion = "conclusion"       # grounded recommendation, evidence-backed
    escalate = "escalate"           # confidence too low / safety constraint — hand off to a human expert


class NextStep(BaseModel):
    type: NextStepType
    content: str                                  # the question / action / recommendation / escalation message
    rationale: str | None = None                   # why this is the most informative next step
    safety_notes: list[str] = Field(default_factory=list)
    evidence_chunk_ids: list[str] = Field(default_factory=list)
    procedure_steps: list[str] = Field(default_factory=list)


class DiagnosticState(BaseModel):
    symptoms: list[str] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)
    measurements: list[str] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    eliminated_hypotheses: list[Hypothesis] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    current_step: NextStep | None = None
    safety_constraints: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)   # overall confidence in the leading hypothesis
    turns_taken: int = 0
    timeline: list[dict] = Field(default_factory=list)


class DiagnosticSessionView(BaseModel):
    """What the API returns — session identity + current state, flattened."""

    session_id: str
    product_id: str
    revision_id: str | None
    status: str
    state: DiagnosticState
