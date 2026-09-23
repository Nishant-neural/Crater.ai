import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.api.routes.machine_knowledge import get_machine_knowledge
from backend.db.models import (
    Base,
    ChunkType,
    DocType,
    ExpertKnowledge,
    KnowledgeStatus,
    KnowledgeType,
    KnowledgeVersion,
    MachineKnowledgeEntity,
    MachineKnowledgeFact,
    Product,
    Revision,
)
from backend.ingestion import pipeline
from backend.ingestion.chunking import PendingChunk
from backend.ingestion.pdf_loader import RawPage
from backend.knowledge.component_extraction import (
    extract_machine_knowledge_from_chunk,
    persist_machine_knowledge,
)
from backend.knowledge.evidence import MachineEvidence
from backend.knowledge.expert import persist_approved_knowledge
from backend.knowledge.machine_model import (
    MachineBehavior,
    MachineConstraint,
    MachineEntity,
    MachineEvent,
    MachinePort,
    MachineQuantity,
    MachineRelation,
    MachineState,
    UniversalMachineModel,
)
from backend.schematic.graph import schematic_to_machine_model
from backend.schematic.schema import ExtractedEdge, ExtractedNode, SchematicExtractionResult


def make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def complete_model():
    evidence = MachineEvidence(
        fact="Documented fact", source_document="manual.pdf", page=1,
        source_type="text", confidence=0.9, extraction_method="test",
    )
    return UniversalMachineModel(
        entities=[MachineEntity(id="pump", name="Pump P1", entity_type="pump", evidence=[evidence])],
        relations=[],
        ports=[MachinePort(id="pump:inlet", entity_id="pump", name="inlet", evidence=[evidence])],
        quantities=[MachineQuantity(id="pressure", entity_id="pump", name="pressure", unit="bar", evidence=[evidence])],
        states=[MachineState(entity_id="pump", name="running", value="false", evidence=[evidence])],
        events=[MachineEvent(id="start", entity_id="pump", name="start", evidence=[evidence])],
        behaviors=[MachineBehavior(id="run", subject_id="pump", subject_name="Pump P1", description="moves fluid", evidence=[evidence])],
        constraints=[MachineConstraint(id="power", entity_id="pump", name="power_required", description="Power required", evidence=[evidence])],
        evidence=[evidence],
    )


def test_provider_extracts_all_universal_primitives(monkeypatch):
    payload = {
        "entities": [{"id": "pump", "name": "Pump P1", "entity_type": "pump"}],
        "relations": [],
        "ports": [{"id": "inlet", "entity_id": "pump", "name": "inlet"}],
        "quantities": [{"id": "pressure", "entity_id": "pump", "name": "pressure", "unit": "bar"}],
        "states": [{"entity_id": "pump", "name": "running", "value": "false"}],
        "events": [{"id": "start", "entity_id": "pump", "name": "start"}],
        "behaviors": [{"id": "run", "subject_id": "pump", "subject_name": "Pump P1", "description": "moves fluid"}],
        "constraints": [{"id": "power", "entity_id": "pump", "name": "power", "description": "required"}],
    }

    class Provider:
        def complete(self, **_kwargs):
            return json.dumps(payload)

    monkeypatch.setattr("backend.knowledge.component_extraction.get_llm_provider", lambda: Provider())
    model = extract_machine_knowledge_from_chunk("Pump P1 requires power and has an inlet.")

    assert len(model.entities) == 1
    assert len(model.ports) == len(model.quantities) == len(model.states) == 1
    assert len(model.events) == len(model.behaviors) == len(model.constraints) == 1


def test_persistence_and_query_include_every_universal_fact():
    db = make_session()
    product = Product(manufacturer="Acme", family="Pump", model="P1")
    db.add(product)
    db.flush()
    revision = Revision(product_id=product.id, label="Rev A")
    db.add(revision)
    db.flush()
    persist_machine_knowledge(db, revision.id, "chunk-1", complete_model())
    db.commit()

    assert db.query(MachineKnowledgeFact).filter_by(revision_id=revision.id).count() == 5
    response = get_machine_knowledge(revision.id, db=db)
    assert len(response["entities"]) == 1
    assert {fact["fact_type"] for fact in response["facts"]} == {
        "port", "quantity", "state", "event", "constraint"
    }
    assert len(response["behaviors"]) == 1
    assert response["evidence"]


def test_pdf_chunk_modalities_reach_universal_persistence(monkeypatch, tmp_path):
    db = make_session()
    product = Product(manufacturer="Acme", family="CNC", model="C1")
    db.add(product)
    db.flush()
    revision = Revision(product_id=product.id, label="Rev A")
    db.add(revision)
    db.commit()

    chunks = [
        PendingChunk(ChunkType.text, 1, "text evidence"),
        PendingChunk(ChunkType.table, 1, "| name | value |"),
        PendingChunk(ChunkType.diagram, 1, "OCR label X1", {"image_path": "diagram.png"}),
    ]
    monkeypatch.setattr(pipeline, "load_pdf", lambda *_args: [RawPage(1, "text")])
    monkeypatch.setattr(pipeline, "page_to_chunks", lambda *_args: chunks)
    monkeypatch.setattr(pipeline, "upsert_chunks", lambda **_kwargs: None)
    monkeypatch.setattr(pipeline, "extract_chunk_knowledge", lambda content: (None, complete_model()))
    monkeypatch.setattr(pipeline, "validate_machine_model", lambda _model: [])

    pipeline.ingest_pdf(
        db, tmp_path / "manual.pdf", revision.id, product.id, DocType.manual,
        "Manual", tmp_path / "images", run_schematic_extraction=False,
    )

    assert db.query(MachineKnowledgeEntity).filter_by(revision_id=revision.id).count() == 1


def test_schematic_projection_creates_entities_ports_and_relations():
    model = schematic_to_machine_model(SchematicExtractionResult(
        nodes=[
            ExtractedNode(label="P1", symbol_type="pump"),
            ExtractedNode(label="V3", symbol_type="valve"),
        ],
        edges=[ExtractedEdge(from_label="P1", to_label="V3", wire_type="fluid")],
    ), "manual.pdf", 4, "chunk-4")

    assert len(model.entities) == 2
    assert len(model.ports) == 2
    assert model.relations[0].relation_type == "connected_to"
    assert model.relations[0].evidence[0].source_type == "schematic"


def test_approved_expert_claims_are_projected_to_universal_knowledge():
    db = make_session()
    product = Product(manufacturer="Acme", family="CNC", model="C1")
    db.add(product)
    db.flush()
    revision = Revision(product_id=product.id, label="Rev A")
    db.add(revision)
    db.flush()
    version = KnowledgeVersion(
        interview_id="interview-1", revision_id=revision.id, version=1,
        status=KnowledgeStatus.approved,
    )
    db.add(version)
    db.flush()
    db.add(ExpertKnowledge(
        knowledge_version_id=version.id, knowledge_type=KnowledgeType.failure_mode,
        title="Door fault", failure_mode="Door interlock open", symptom="Motor stopped",
        action="Close door", expected_observation="Motor runs", evidence_turn_ids=["turn-1"],
        confidence=0.9, source_quote="Close the door before testing.",
    ))
    db.commit()

    persist_approved_knowledge(db, version)
    db.commit()

    entity = db.query(MachineKnowledgeEntity).filter_by(revision_id=revision.id).one()
    assert entity.entity_type == "failure_mode"
    assert db.query(MachineKnowledgeFact).filter_by(revision_id=revision.id, fact_type="behavior").count() == 0
