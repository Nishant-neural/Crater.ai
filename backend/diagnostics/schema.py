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
from typing import Any

from pydantic import BaseModel, Field


class Hypothesis(BaseModel):
    cause: str
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)
    supporting_evidence_chunk_ids: list[str] = Field(default_factory=list)
    # Phase 1 Step 4 (audit P-14): structured fields the rank call fills
    # from the retrieved row, not free text -- the component the cause is
    # about, and the fault value/state when the row states one (e.g.
    # "low" / "false" / "open", else ""). The server blanks a component
    # that does not occur in the prompted evidence (see agent.py).
    component: str = ""
    value: str = ""
    # Phase 1 Step 4 (audit P-16 / Gate 5): server-computed, never
    # model-asserted -- "grounded" when at least one cited chunk id
    # survived intersection with the chunks retrieved this turn,
    # "guess" otherwise. A guess is kept and labelled, never dropped.
    citation_status: str = "guess"


class EvidenceRef(BaseModel):
    chunk_id: str
    content: str
    source_document: str | None = None
    page_number: int | None = None


class NextStepType(str, Enum):
    question = "question"          # ask the technician a diagnostic question
    action = "action"               # ask the technician to perform a measurement/test
    conclusion = "conclusion"       # grounded recommendation, evidence-backed
    escalate = "escalate"           # LEGACY only: stored sessions may still carry it (fix-3 removed
                                    # escalation from the loop — it is never emitted, and a model
                                    # returning it is degraded to a question). Kept so stored
                                    # states still validate.


class NextStep(BaseModel):
    type: NextStepType
    content: str                                  # the question / action / recommendation
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
    # Phase 1 Step 4 (audit P-12): the discriminating test for this
    # turn, from the single backtracking picker
    # (select_discriminating_test) or, when no signature test splits
    # the ranking, the deterministic ranking fallback -- always a dict
    # with at least "test" / "rationale" / "separates", or None when no
    # test could be named (degraded turn, or every cause already
    # asked). Set on every successful turn, including turn 1.
    discriminating_test: dict[str, Any] | None = None
    timeline: list[dict] = Field(default_factory=list)
    # Phase 1 Step 5 (audit Step 4 / schema p.5): explicit asked sets,
    # owned by the LangGraph loop — the sets the no-repeat gate and the
    # discriminating-test picker read, instead of regex over prose.
    # ``asked_causes`` holds canonical hypothesis-cause strings;
    # ``asked_tests`` holds discriminating-test texts. Older sessions
    # stored before this step have empty lists; the loop unions these
    # with the timeline-derived sets (see agent._asked_causes_and_tests)
    # so their history is not lost.
    asked_causes: list[str] = Field(default_factory=list)
    asked_tests: list[str] = Field(default_factory=list)


class DiagnosticSessionView(BaseModel):
    """What the API returns — session identity + current state, flattened."""

    session_id: str
    product_id: str
    revision_id: str | None
    status: str
    state: DiagnosticState
