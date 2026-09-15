"""Phase 5 tests use SQLite only; LLM calls are monkeypatched where needed."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db.models import (
    Base, Expert, Product, Revision, KnowledgeStatus, KnowledgeVersion,
    ExpertKnowledge, KnowledgeType, ExpertInterview,
)
from backend.knowledge import expert as expert_service


def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def seed():
    session = db()
    product = Product(manufacturer="Acme", family="CNC", model="CNC-500X")
    session.add(product)
    session.flush()
    revision = Revision(product_id=product.id, label="Rev C")
    session.add(revision)
    expert = Expert(name="Rajesh", role="Senior Service Engineer")
    session.add(expert)
    session.commit()
    return session, product, revision, expert


def test_interview_keeps_immutable_turn_sequence_and_fallback_question():
    session, product, revision, expert = seed()
    interview = expert_service.start_interview(session, expert.id, product.id, revision.id, "spindle won't start")
    turn, question = expert_service.add_turn(session, interview, "I first check whether the safety relay is energized.")
    assert turn.sequence == 0
    assert question
    turn2, _ = expert_service.add_turn(session, interview, "If it is not energized, I inspect the door interlock.")
    assert turn2.sequence == 1
    assert [t.sequence for t in expert_service._transcript(session, interview.id)] == [0, 1]


def test_extraction_creates_new_version_with_turn_provenance(monkeypatch):
    session, product, revision, expert = seed()
    interview = expert_service.start_interview(session, expert.id, product.id, revision.id, "spindle won't start")
    turn, _ = expert_service.add_turn(session, interview, "An open door keeps the safety relay off.")
    monkeypatch.setattr(
        expert_service, "_llm_text",
        lambda *_args, **_kwargs: '{"items":[{"knowledge_type":"rule","title":"Open door disables safety","symptom":"spindle will not start","condition":"door is open","action":"inspect door interlock","expected_observation":"safety relay remains off","failure_mode":"door interlock state","safety_notes":["de-energize before inspection"],"applicable_models":[],"applicable_revisions":["Rev C"],"evidence_turn_ids":["%s"],"confidence":0.9,"source_quote":"An open door keeps the safety relay off."}]}' % turn.id,
    )
    version = expert_service.extract_knowledge(session, interview)
    assert version.version == 1
    assert version.status == KnowledgeStatus.draft
    item = session.query(ExpertKnowledge).filter_by(knowledge_version_id=version.id).one()
    assert item.evidence_turn_ids == [turn.id]
    assert item.confidence == 0.9


def test_approval_supersedes_previous_approved_version():
    session, product, revision, expert = seed()
    interview = expert_service.start_interview(session, expert.id, product.id, revision.id, "bearing noise")
    expert_service.add_turn(session, interview, "A high-pitched noise usually means bearing wear.")
    # Extraction with no API key produces an empty but valid version.
    v1 = expert_service.extract_knowledge(session, interview)
    expert_service.review_version(session, v1, "approved", "chief-engineer", "checked")
    v2 = expert_service.extract_knowledge(session, interview)
    expert_service.review_version(session, v2, "approved", "chief-engineer", "updated")
    session.refresh(v1)
    assert v1.status == KnowledgeStatus.superseded
    assert v2.status == KnowledgeStatus.approved


def test_graph_generation_requires_approval_and_builds_executable_path():
    session, product, revision, expert = seed()
    interview = expert_service.start_interview(session, expert.id, product.id, revision.id, "motor won't start")
    expert_service.add_turn(session, interview, "If X12 is disconnected, the drive stays disabled.")
    version = expert_service.extract_knowledge(session, interview)
    item = ExpertKnowledge(
        knowledge_version_id=version.id,
        knowledge_type=KnowledgeType.rule,
        title="X12 disconnect rule",
        symptom="motor won't start",
        condition="X12 disconnected",
        action="reseat X12",
        expected_observation="drive enable returns",
        failure_mode="open X12 connection",
        safety_notes=["lockout/tagout"],
        evidence_turn_ids=[],
        confidence=0.95,
        source_quote="If X12 is disconnected, the drive stays disabled.",
    )
    session.add(item)
    session.commit()
    try:
        expert_service.generate_diagnostic_graph(session, version)
        assert False, "draft knowledge must not generate a graph"
    except ValueError:
        pass
    expert_service.review_version(session, version, "approved", "chief-engineer", None)
    graph = expert_service.generate_diagnostic_graph(session, version)
    assert graph.status == KnowledgeStatus.approved
    assert session.query(expert_service.DiagnosticGraphNode).filter_by(graph_id=graph.id).count() >= 2
    assert session.query(expert_service.DiagnosticGraphEdge).filter_by(graph_id=graph.id).count() >= 1
