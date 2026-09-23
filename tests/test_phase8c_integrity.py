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
    monkeypatch.setattr("backend.knowledge.global_integration.get_llm_provider",lambda:P())
    result=integrate_revision_knowledge(s,r.id)
    assert result.payload["completeness"]["complete"] is False
    assert result.payload["completeness"]["missing_count"] == 1
    assert result.model.unresolved_facts[0].source_id == fact.id

def test_revision_inheritance_is_effective(monkeypatch):
    s=db(); p=Product(manufacturer="A",family="F",model="M"); s.add(p); s.flush(); parent=Revision(product_id=p.id,label="B"); s.add(parent); s.flush(); child=Revision(product_id=p.id,label="C",parent_revision_id=parent.id); s.add(child); s.flush()
    e=MachineKnowledgeEntity(revision_id=parent.id,name="Pump P1",entity_type="pump"); s.add(e); s.commit()
    payload={"entities":[{"id":"entity:p1","name":"Pump P1","entity_type":"pump","source_ids":[e.id],"evidence":[]}],"relations":[],"ports":[],"quantities":[],"states":[],"events":[],"behaviors":[],"constraints":[],"procedures":[],"failure_modes":[],"conflicts":[],"unresolved_facts":[]}
    class P:
        def complete(self, **kwargs): return json.dumps(payload)
    monkeypatch.setattr("backend.knowledge.global_integration.get_llm_provider",lambda:P())
    result=integrate_revision_knowledge(s,child.id)
    assert result.payload["revision_lineage"] == [parent.id, child.id]
    assert result.model.entities[0].source_ids == [e.id]

def test_graph_expansion_retrieves_related_failure_and_procedure():
    s=db(); p=Product(manufacturer="A",family="F",model="M"); s.add(p); s.flush(); r=Revision(product_id=p.id,label="A"); s.add(r); s.flush()
    model={"entities":[{"id":"entity:p101","name":"P-101","entity_type":"pump"},{"id":"entity:m1","name":"Motor M1","entity_type":"motor"}],"relations":[{"id":"rel:1","subject_id":"entity:p101","subject_name":"P-101","relation_type":"driven_by","object_id":"entity:m1","object_name":"Motor M1"}],"failure_modes":[{"id":"failure:overheat","name":"Pump overheating","entity_ids":["entity:p101"]}],"procedures":[{"id":"procedure:cool","name":"Inspect cooling","entity_ids":["entity:p101"]}]}
    s.add(MachineKnowledgeModelSnapshot(revision_id=r.id,version=1,model=model,source_counts={})); s.commit()
    hits=retrieve_machine_knowledge(s,"P-101",product_id=p.id,revision_id=r.id,top_k=10,graph_hops=1)
    kinds={h.kind for h in hits}
    assert "failure_modes" in kinds and "procedures" in kinds and "relations" in kinds
