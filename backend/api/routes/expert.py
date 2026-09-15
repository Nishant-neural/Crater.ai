"""Phase 5 — Capture Rajesh API: interview → extraction → review → diagnostic graph."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.db.models import (
    DiagnosticGraph,
    DiagnosticGraphEdge,
    DiagnosticGraphNode,
    Expert,
    ExpertInterview,
    ExpertInterviewTurn,
    ExpertKnowledge,
    KnowledgeVersion,
    Product,
    Revision,
)
from backend.db.session import get_session
from backend.knowledge.expert import (
    add_turn,
    complete_interview,
    extract_knowledge,
    generate_diagnostic_graph,
    review_version,
    start_interview,
)
from backend.knowledge.expert_schema import (
    InterviewStart,
    InterviewTurnRequest,
    InterviewTurnView,
    InterviewView,
    KnowledgeItemView,
    KnowledgeVersionView,
    ReviewRequest,
    DiagnosticGraphView,
    GraphNodeView,
    GraphEdgeView,
)
from pydantic import BaseModel

router = APIRouter(prefix="/expert", tags=["expert knowledge"])


class ExpertCreate(BaseModel):
    name: str
    role: str | None = None
    organization: str | None = None
    notes: str | None = None


@router.post("/experts")
def create_expert(payload: ExpertCreate, db: Session = Depends(get_session)):
    expert = Expert(**payload.model_dump())
    db.add(expert)
    db.commit()
    db.refresh(expert)
    return {"id": expert.id, "name": expert.name, "role": expert.role}


@router.get("/experts")
def list_experts(db: Session = Depends(get_session)):
    return [
        {"id": e.id, "name": e.name, "role": e.role, "organization": e.organization}
        for e in db.query(Expert).order_by(Expert.name).all()
    ]


def _interview_view(db: Session, interview: ExpertInterview, next_question: str | None = None) -> InterviewView:
    turns = db.query(ExpertInterviewTurn).filter(
        ExpertInterviewTurn.interview_id == interview.id
    ).order_by(ExpertInterviewTurn.sequence).all()
    return InterviewView(
        interview_id=interview.id, expert_id=interview.expert_id,
        product_id=interview.product_id, revision_id=interview.revision_id,
        topic=interview.topic, status=interview.status.value,
        next_question=next_question,
        turns=[InterviewTurnView(id=t.id, sequence=t.sequence, speaker=t.speaker, content=t.content) for t in turns],
    )


@router.post("/interviews", response_model=InterviewView)
def create_interview(payload: InterviewStart, db: Session = Depends(get_session)):
    if not db.get(Product, payload.product_id):
        raise HTTPException(404, "Product not found")
    if payload.revision_id:
        revision = db.get(Revision, payload.revision_id)
        if not revision or revision.product_id != payload.product_id:
            raise HTTPException(400, "Revision does not belong to the selected product")
    try:
        interview = start_interview(db, **payload.model_dump())
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return _interview_view(db, interview, "Describe a recurring failure or difficult service case for this product.")


@router.get("/interviews/{interview_id}", response_model=InterviewView)
def get_interview(interview_id: str, db: Session = Depends(get_session)):
    interview = db.get(ExpertInterview, interview_id)
    if not interview:
        raise HTTPException(404, "Interview not found")
    next_question = None if interview.status.value != "active" else (
        "Continue with a concrete example: what did you observe, test, or measure next?"
    )
    return _interview_view(db, interview, next_question)


@router.post("/interviews/{interview_id}/turns", response_model=InterviewView)
def interview_turn(interview_id: str, payload: InterviewTurnRequest, db: Session = Depends(get_session)):
    interview = db.get(ExpertInterview, interview_id)
    if not interview:
        raise HTTPException(404, "Interview not found")
    try:
        _, question = add_turn(db, interview, payload.content, payload.speaker)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _interview_view(db, interview, question)


@router.post("/interviews/{interview_id}/complete", response_model=InterviewView)
def finish_interview(interview_id: str, db: Session = Depends(get_session)):
    interview = db.get(ExpertInterview, interview_id)
    if not interview:
        raise HTTPException(404, "Interview not found")
    try:
        complete_interview(db, interview)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _interview_view(db, interview)


def _version_view(db: Session, version: KnowledgeVersion) -> KnowledgeVersionView:
    items = db.query(ExpertKnowledge).filter(
        ExpertKnowledge.knowledge_version_id == version.id
    ).all()
    return KnowledgeVersionView(
        id=version.id, interview_id=version.interview_id, revision_id=version.revision_id,
        version=version.version, status=version.status.value, reviewer=version.reviewer,
        review_notes=version.review_notes,
        items=[
            KnowledgeItemView(
                id=i.id, knowledge_version_id=i.knowledge_version_id,
                knowledge_type=i.knowledge_type.value, title=i.title, symptom=i.symptom,
                trigger=i.trigger, condition=i.condition, action=i.action,
                expected_observation=i.expected_observation, failure_mode=i.failure_mode,
                safety_notes=i.safety_notes or [], applicable_models=i.applicable_models or [],
                applicable_revisions=i.applicable_revisions or [], evidence_turn_ids=i.evidence_turn_ids or [],
                confidence=i.confidence, source_quote=i.source_quote,
            ) for i in items
        ],
    )


@router.post("/interviews/{interview_id}/extract", response_model=KnowledgeVersionView)
def extract(interview_id: str, db: Session = Depends(get_session)):
    interview = db.get(ExpertInterview, interview_id)
    if not interview:
        raise HTTPException(404, "Interview not found")
    try:
        version = extract_knowledge(db, interview)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _version_view(db, version)


@router.get("/knowledge/{version_id}", response_model=KnowledgeVersionView)
def get_knowledge(version_id: str, db: Session = Depends(get_session)):
    version = db.get(KnowledgeVersion, version_id)
    if not version:
        raise HTTPException(404, "Knowledge version not found")
    return _version_view(db, version)


@router.post("/knowledge/{version_id}/review", response_model=KnowledgeVersionView)
def review(version_id: str, payload: ReviewRequest, db: Session = Depends(get_session)):
    version = db.get(KnowledgeVersion, version_id)
    if not version:
        raise HTTPException(404, "Knowledge version not found")
    try:
        version = review_version(db, version, payload.decision, payload.reviewer, payload.notes)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _version_view(db, version)


def _graph_view(db: Session, graph: DiagnosticGraph) -> DiagnosticGraphView:
    nodes = db.query(DiagnosticGraphNode).filter(DiagnosticGraphNode.graph_id == graph.id).all()
    edges = db.query(DiagnosticGraphEdge).filter(DiagnosticGraphEdge.graph_id == graph.id).all()
    return DiagnosticGraphView(
        id=graph.id, revision_id=graph.revision_id, knowledge_version_id=graph.knowledge_version_id,
        version=graph.version, status=graph.status.value, name=graph.name,
        nodes=[GraphNodeView(id=n.id, node_type=n.node_type.value, label=n.label, content=n.content, knowledge_id=n.knowledge_id) for n in nodes],
        edges=[GraphEdgeView(id=e.id, from_node_id=e.from_node_id, to_node_id=e.to_node_id, edge_type=e.edge_type.value, condition=e.condition, knowledge_id=e.knowledge_id) for e in edges],
    )


@router.post("/knowledge/{version_id}/graph", response_model=DiagnosticGraphView)
def graph(version_id: str, db: Session = Depends(get_session)):
    version = db.get(KnowledgeVersion, version_id)
    if not version:
        raise HTTPException(404, "Knowledge version not found")
    try:
        result = generate_diagnostic_graph(db, version)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _graph_view(db, result)


@router.get("/graphs/{graph_id}", response_model=DiagnosticGraphView)
def get_graph(graph_id: str, db: Session = Depends(get_session)):
    result = db.get(DiagnosticGraph, graph_id)
    if not result:
        raise HTTPException(404, "Diagnostic graph not found")
    return _graph_view(db, result)
