"""Phase 5 service layer: interview, extraction, review, and diagnostic graph generation."""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import Session

from backend.config import settings
from backend.db.models import (
    DiagnosticGraph,
    DiagnosticGraphEdge,
    DiagnosticGraphNode,
    Expert,
    ExpertInterview,
    ExpertInterviewTurn,
    ExpertKnowledge,
    GraphEdgeType,
    GraphNodeType,
    InterviewStatus,
    KnowledgeStatus,
    KnowledgeType,
    KnowledgeVersion,
)
from backend.knowledge.expert_schema import ExtractedKnowledgeItem, KnowledgeExtractionResult
from backend.knowledge.expert_prompts import EXPERT_EXTRACTION_PROMPT, EXPERT_INTERVIEW_PROMPT


def _llm_text(prompt: str, max_tokens: int = 1200) -> str | None:
    if not settings.anthropic_api_key:
        return None
    from anthropic import Anthropic
    client = Anthropic(api_key=settings.anthropic_api_key)
    response = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    if not response.content:
        return None
    return getattr(response.content[0], "text", "").strip() or None


def _transcript(db: Session, interview_id: str) -> list[ExpertInterviewTurn]:
    return (
        db.query(ExpertInterviewTurn)
        .filter(ExpertInterviewTurn.interview_id == interview_id)
        .order_by(ExpertInterviewTurn.sequence)
        .all()
    )


def transcript_text(db: Session, interview_id: str) -> str:
    return "\n".join(f"[{t.id}] {t.speaker}: {t.content}" for t in _transcript(db, interview_id))


def start_interview(db: Session, expert_id: str, product_id: str, revision_id: str | None, topic: str) -> ExpertInterview:
    if not db.get(Expert, expert_id):
        raise ValueError("Expert not found")
    interview = ExpertInterview(
        expert_id=expert_id, product_id=product_id, revision_id=revision_id,
        topic=topic, status=InterviewStatus.active,
    )
    db.add(interview)
    db.commit()
    db.refresh(interview)
    return interview


def _next_question(db: Session, interview: ExpertInterview) -> str:
    turns = _transcript(db, interview.id)
    context = "revision=" + (interview.revision_id or "unspecified")
    transcript = "\n".join(f"{t.speaker}: {t.content}" for t in turns[-12:]) or "(no answer yet)"
    prompt = EXPERT_INTERVIEW_PROMPT.format(
        topic=interview.topic, context=context, transcript=transcript
    )
    generated = _llm_text(prompt, 500)
    if generated:
        return generated.strip().strip('"')
    # Deterministic fallback keeps the workflow usable in local/dev mode.
    fallbacks = [
        "When this symptom occurs, what do you check first, and what observation makes you choose that check?",
        "What measurement or observation distinguishes the most common cause from a similar-looking fault?",
        "Are there model or revision differences that change this troubleshooting path?",
        "What is a common misdiagnosis here, and what evidence rules it out?",
        "What is the safest next test before replacing a component, and what result confirms the fault?",
    ]
    return fallbacks[min(len(turns), len(fallbacks) - 1)]


def add_turn(db: Session, interview: ExpertInterview, content: str, speaker: str = "expert") -> tuple[ExpertInterviewTurn, str]:
    if interview.status != InterviewStatus.active:
        raise ValueError("Interview is not active")
    if not content.strip():
        raise ValueError("Interview turn cannot be empty")
    max_seq = (
        db.query(ExpertInterviewTurn.sequence)
        .filter(ExpertInterviewTurn.interview_id == interview.id)
        .order_by(ExpertInterviewTurn.sequence.desc())
        .first()
    )
    sequence = (max_seq[0] + 1) if max_seq else 0
    turn = ExpertInterviewTurn(
        interview_id=interview.id, sequence=sequence, speaker=speaker, content=content.strip()
    )
    db.add(turn)
    db.commit()
    db.refresh(turn)
    return turn, _next_question(db, interview)


def complete_interview(db: Session, interview: ExpertInterview) -> None:
    interview.status = InterviewStatus.completed
    interview.completed_at = datetime.utcnow()
    db.add(interview)
    db.commit()


def _parse_json(raw: str) -> dict | None:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:].lstrip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start:end + 1])
            except json.JSONDecodeError:
                return None
    return None


def extract_knowledge(db: Session, interview: ExpertInterview) -> KnowledgeVersion:
    turns = _transcript(db, interview.id)
    if not turns:
        raise ValueError("Cannot extract knowledge from an empty interview")

    # Version is monotonic and immutable; a failed extraction never creates a version.
    latest = (
        db.query(KnowledgeVersion)
        .filter(KnowledgeVersion.interview_id == interview.id)
        .order_by(KnowledgeVersion.version.desc())
        .first()
    )
    version_number = (latest.version + 1) if latest else 1
    prompt = EXPERT_EXTRACTION_PROMPT.format(
        transcript="\n".join(f"[{t.id}] {t.speaker}: {t.content}" for t in turns)
    )
    raw = _llm_text(prompt, 3500)
    result = KnowledgeExtractionResult()
    if raw:
        parsed = _parse_json(raw)
        if parsed:
            try:
                result = KnowledgeExtractionResult.model_validate(parsed)
            except ValueError:
                result = KnowledgeExtractionResult()

    version = KnowledgeVersion(
        interview_id=interview.id,
        revision_id=interview.revision_id,
        version=version_number,
        status=KnowledgeStatus.draft,
    )
    db.add(version)
    db.flush()

    for item in result.items:
        try:
            knowledge_type = KnowledgeType(item.knowledge_type)
        except ValueError:
            continue
        db.add(ExpertKnowledge(
            knowledge_version_id=version.id,
            knowledge_type=knowledge_type,
            title=item.title,
            symptom=item.symptom,
            trigger=item.trigger,
            condition=item.condition,
            action=item.action,
            expected_observation=item.expected_observation,
            failure_mode=item.failure_mode,
            safety_notes=item.safety_notes,
            applicable_models=item.applicable_models,
            applicable_revisions=item.applicable_revisions,
            evidence_turn_ids=item.evidence_turn_ids,
            confidence=item.confidence,
            source_quote=item.source_quote,
        ))
    db.commit()
    db.refresh(version)
    return version


def review_version(db: Session, version: KnowledgeVersion, decision: str, reviewer: str, notes: str | None) -> KnowledgeVersion:
    if version.status not in (KnowledgeStatus.draft, KnowledgeStatus.in_review):
        raise ValueError(f"Knowledge version is already {version.status.value}")
    if decision not in ("approved", "rejected"):
        raise ValueError("Decision must be approved or rejected")
    version.status = KnowledgeStatus.approved if decision == "approved" else KnowledgeStatus.rejected
    version.reviewer = reviewer
    version.review_notes = notes
    version.reviewed_at = datetime.utcnow()

    if decision == "approved":
        previous = (
            db.query(KnowledgeVersion)
            .filter(
                KnowledgeVersion.interview_id == version.interview_id,
                KnowledgeVersion.id != version.id,
                KnowledgeVersion.status == KnowledgeStatus.approved,
            )
            .all()
        )
        for old in previous:
            old.status = KnowledgeStatus.superseded

    db.add(version)
    db.commit()
    db.refresh(version)
    return version


def _add_node(db: Session, graph_id: str, node_type: GraphNodeType, label: str, content: str | None, knowledge_id: str | None):
    node = DiagnosticGraphNode(
        graph_id=graph_id, node_type=node_type, label=label, content=content, knowledge_id=knowledge_id
    )
    db.add(node)
    db.flush()
    return node


def _add_edge(db: Session, graph_id: str, a, b, edge_type: GraphEdgeType, condition: str | None, knowledge_id: str):
    db.add(DiagnosticGraphEdge(
        graph_id=graph_id, from_node_id=a.id, to_node_id=b.id,
        edge_type=edge_type, condition=condition, knowledge_id=knowledge_id
    ))


def generate_diagnostic_graph(db: Session, version: KnowledgeVersion) -> DiagnosticGraph:
    if version.status != KnowledgeStatus.approved:
        raise ValueError("Only approved knowledge can generate a diagnostic graph")

    previous = (
        db.query(DiagnosticGraph)
        .filter(
            DiagnosticGraph.knowledge_version_id == version.id,
        )
        .order_by(DiagnosticGraph.version.desc())
        .first()
    )
    graph_version = (previous.version + 1) if previous else 1
    graph = DiagnosticGraph(
        revision_id=version.revision_id,
        knowledge_version_id=version.id,
        version=graph_version,
        status=KnowledgeStatus.approved,
        name=f"Expert diagnostic graph v{graph_version}",
    )
    db.add(graph)
    db.flush()

    items = db.query(ExpertKnowledge).filter(ExpertKnowledge.knowledge_version_id == version.id).all()
    for item in items:
        symptom = _add_node(db, graph.id, GraphNodeType.symptom, item.symptom or item.title, item.symptom, item.id)
        if item.action or item.condition:
            action = _add_node(db, graph.id, GraphNodeType.action, "Diagnostic action", item.action or item.condition, item.id)
            _add_edge(db, graph.id, symptom, action, GraphEdgeType.leads_to, item.condition, item.id)
        else:
            action = None
        if item.failure_mode:
            hypothesis = _add_node(db, graph.id, GraphNodeType.hypothesis, item.failure_mode, item.failure_mode, item.id)
            _add_edge(db, graph.id, symptom, hypothesis, GraphEdgeType.supports, None, item.id)
            if action:
                _add_edge(db, graph.id, hypothesis, action, GraphEdgeType.leads_to, item.condition, item.id)
        if item.expected_observation:
            outcome = _add_node(db, graph.id, GraphNodeType.outcome, "Expected observation", item.expected_observation, item.id)
            if action:
                _add_edge(db, graph.id, action, outcome, GraphEdgeType.leads_to, None, item.id)
    db.commit()
    db.refresh(graph)
    return graph
