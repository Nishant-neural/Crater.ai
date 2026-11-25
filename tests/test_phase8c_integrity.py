import json
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from backend.db.models import Base, Product, Revision, MachineKnowledgeEntity, MachineKnowledgeFact, MachineKnowledgeRelation, MachineKnowledgeModelSnapshot
from backend.knowledge.global_integration import integrate_revision_knowledge
from backend.retrieval.knowledge import retrieve_machine_knowledge

def db():
    e=create_engine("sqlite:///:memory:")
    Base.metadata.create_all(e)
    return sessionmaker(bind=e)()

def test_completeness_retains_unaccounted_fact(monkeypatch):
    s=db(); p=Product(manufacturer="A",family="F",model="M"); s.add(p); s.flush(); r=Revision(product_id=p.id,label="A"); s.add(r); s.flush()
    fact=MachineKnowledgeFact(revision_id=r.id,fact_type="quantity",fact_key="pressure.limit",payload={"value":"10 bar"}); s.add(fact); s.commit()
    payload={"entities":[],"relations":[],"ports":[],"quantities":[],"states":[],"events":[],"behaviors":[],"constraints":[],"procedures":[],"failure_modes":[],"conflicts":[],"unresolved_facts":[]}
    class P:
        def complete(self, **kwargs): return json.dumps(payload)
    monkeypatch.setattr("backend.knowledge.global_integration.gateway.complete",lambda *args, **kwargs:P().complete())
    result=integrate_revision_knowledge(s,r.id)
    assert result.payload["completeness"]["complete"] is False
    assert result.payload["completeness"]["missing_count"] == 1
    assert result.model.unresolved_facts[0].source_id == fact.id

def test_revision_inheritance_is_effective(monkeypatch):
    # Code-first redesign: canonical id is a slug derived from the name,
    # not an LLM-invented entity:p1. Lineage and source_ids are preserved.
    s=db(); p=Product(manufacturer="A",family="F",model="M"); s.add(p); s.flush(); parent=Revision(product_id=p.id,label="B"); s.add(parent); s.flush(); child=Revision(product_id=p.id,label="C",parent_revision_id=parent.id); s.add(child); s.flush()
    e=MachineKnowledgeEntity(revision_id=parent.id,name="Pump P1",entity_type="pump"); s.add(e); s.commit()
    import backend.knowledge.code_first_integration as cf
    monkeypatch.setattr(cf, "adjudicate_clusters", lambda clusters: [])
    result=integrate_revision_knowledge(s,child.id)
    assert result.payload["revision_lineage"] == [parent.id, child.id]
    assert result.model.entities[0].name == "Pump P1"
    assert result.model.entities[0].id == "entity:pump-p1"
    assert result.model.entities[0].source_ids == [e.id]
def test_graph_expansion_retrieves_related_failure_and_procedure():
    """Phase 3 extraction: the canonical model comes from ingestion's
    HTTP API, never the snapshot table. The 2-hop graph expansion
    itself is unchanged."""
    import httpx

    from backend.retrieval.ingestion_client import IngestionClient

    model = {"entities": [{"id": "entity:p101", "name": "P-101", "entity_type": "pump"}, {"id": "entity:m1", "name": "Motor M1", "entity_type": "motor"}], "relations": [{"id": "rel:1", "subject_id": "entity:p101", "subject_name": "P-101", "relation_type": "driven_by", "object_id": "entity:m1", "object_name": "Motor M1"}], "failure_modes": [{"id": "failure:overheat", "name": "Pump overheating", "entity_ids": ["entity:p101"]}], "procedures": [{"id": "procedure:cool", "name": "Inspect cooling", "entity_ids": ["entity:p101"]}]}
    product_id = "prod-1"
    revision_id = "rev-1"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/v1/products/{product_id}/revisions":
            return httpx.Response(200, json=[
                {"id": revision_id, "product_id": product_id, "label": "A"}])
        if request.url.path == f"/v1/knowledge/revisions/{revision_id}/model":
            return httpx.Response(200, json={
                "revision_id": revision_id, "version": 1, "model": model,
                "source_counts": {}})
        return httpx.Response(404, json={"error": {"code": "not_found"}})

    client = IngestionClient(base_url="http://ingestion-test",
                             transport=httpx.MockTransport(handler))
    hits = retrieve_machine_knowledge("P-101", product_id=product_id, revision_id=revision_id, top_k=10, graph_hops=1, client=client)
    kinds = {h.kind for h in hits}
    assert "failure_modes" in kinds and "procedures" in kinds and "relations" in kinds
