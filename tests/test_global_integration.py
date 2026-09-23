import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db.models import Base, MachineKnowledgeEntity, MachineKnowledgeFact, Product, Revision
from backend.knowledge.global_integration import build_revision_knowledge_context, integrate_revision_knowledge


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_global_context_contains_all_persisted_fact_types():
    db = make_db()
    product = Product(manufacturer="A", family="F", model="M")
    db.add(product); db.flush()
    revision = Revision(product_id=product.id, label="A")
    db.add(revision); db.flush()
    db.add(MachineKnowledgeEntity(revision_id=revision.id, name="Pump P1", entity_type="pump"))
    db.add(MachineKnowledgeFact(revision_id=revision.id, fact_type="quantity", fact_key="q1", payload={"name": "pressure"}))
    db.commit()
    context, counts = build_revision_knowledge_context(db, revision.id)
    assert len(context["universal_entities"]) == 1
    assert len(context["universal_facts"]) == 1
    assert counts["universal_entities"] == 1


def test_global_integration_resolves_duplicate_entities(monkeypatch):
    db = make_db()
    product = Product(manufacturer="A", family="F", model="M")
    db.add(product); db.flush()
    revision = Revision(product_id=product.id, label="A")
    db.add(revision); db.flush()
    a = MachineKnowledgeEntity(revision_id=revision.id, name="Pump P1", entity_type="pump")
    b = MachineKnowledgeEntity(revision_id=revision.id, name="Hydraulic Pump P1", entity_type="pump")
    db.add_all([a, b]); db.flush(); db.commit()

    payload = {"entities": [{"id": "entity:p1", "name": "Pump P1", "entity_type": "pump", "source_ids": [a.id, b.id], "evidence": []}], "relations": [], "ports": [], "quantities": [], "states": [], "events": [], "behaviors": [], "constraints": [], "procedures": [], "failure_modes": []}
    class Provider:
        def complete(self, **_kwargs): return json.dumps(payload)
    monkeypatch.setattr("backend.knowledge.global_integration.get_llm_provider", lambda: Provider())
    result = integrate_revision_knowledge(db, revision.id)
    assert result.model.entities[0].id == "entity:p1"
    assert result.model.entities[0].name == "Pump P1"
