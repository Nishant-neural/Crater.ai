from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db.models import Base, MachineKnowledgeModelSnapshot, Product, Revision
from backend.knowledge.machine_model import MachineEntity, MachineRelation, MachinePort, UniversalMachineModel
from backend.knowledge.verification import verify_machine_model
from backend.retrieval.knowledge import retrieve_machine_knowledge


def db():
    e=create_engine("sqlite:///:memory:"); Base.metadata.create_all(e); return sessionmaker(bind=e)()

def test_topology_verification_catches_missing_reference():
    m=UniversalMachineModel(entities=[MachineEntity(id="a", name="A", entity_type="pump")], relations=[MachineRelation(subject_id="a",subject_name="A",relation_type="connected_to",object_id="missing",object_name="X")])
    issues=verify_machine_model(m)
    assert any(i.category=="topology" and i.severity=="error" for i in issues)

def test_spatial_verification_catches_bad_bbox():
    m=UniversalMachineModel(entities=[MachineEntity(id="a",name="A",entity_type="x",properties={"bbox":{"x":0.9,"y":0,"w":0.3,"h":0.2}})])
    issues=verify_machine_model(m)
    assert any(i.category=="spatial" and i.severity=="error" for i in issues)

def test_retrieval_is_hard_revision_scoped():
    s=db(); p=Product(manufacturer="A",family="F",model="M"); s.add(p); s.flush(); r=Revision(product_id=p.id,label="Rev A"); s.add(r); s.flush()
    s.add(MachineKnowledgeModelSnapshot(revision_id=r.id,version=1,model={"entities":[{"id":"entity:p1","name":"Pump P1","entity_type":"pump"}]},source_counts={})); s.commit()
    hits=retrieve_machine_knowledge(s,"pump",product_id=p.id,revision_id=r.id)
    assert hits and hits[0].revision_id==r.id
    assert retrieve_machine_knowledge(s,"pump",product_id="wrong",revision_id=r.id)==[]
