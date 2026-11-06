"""Same-chunk relation disambiguation in code-first integration.

A relation and its endpoints were extracted from one chunk, in one
extraction output, so the names in that output co-refer to the
entities in that same output. When a name has several canonical
candidates, the relation's own source chunk may narrow it to exactly
one. That is the only new resolution rule here: it never merges
entities, and it never changes a relation that already resolves
uniquely. A universal row and its legacy copy are the same logical
relation and must appear once, with both source ids.
"""
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db.models import (
    Base, Chunk, ChunkType, Component, ComponentRelationship, DocType, Document,
    MachineKnowledgeEntity, MachineKnowledgeRelation, Product, RelationType, Revision,
)
from backend.knowledge.code_first_integration import (
    apply_adjudication, build_code_first_payload,
)
from backend.knowledge.global_integration import integrate_revision_knowledge


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def make_revision_with_chunks(db):
    product = Product(manufacturer="Harbor Freight", family="OMNIPRO", model="220")
    db.add(product)
    db.flush()
    revision = Revision(product_id=product.id, label="Rev A")
    db.add(revision)
    db.flush()
    doc = Document(revision_id=revision.id, doc_type=DocType.manual,
                   title="owner-manual.pdf", source_path="owner-manual.pdf")
    db.add(doc)
    db.flush()
    chunks = {}
    for label, page in (("p5", 5), ("p10", 10), ("p15", 15)):
        chunk = Chunk(id=f"chunk-{label}", document_id=doc.id,
                      chunk_type=ChunkType.text, page_number=page,
                      content=f"chunk {label}")
        db.add(chunk)
        chunks[label] = chunk
    db.commit()
    return revision, chunks


def _stub_adjudication(monkeypatch, seen):
    import backend.knowledge.code_first_integration as cf

    def _capture(clusters):
        seen.extend(clusters)
        return []

    monkeypatch.setattr(cf, "adjudicate_clusters", _capture)


def _add_entities(db, revision, rows):
    """rows: (name, entity_type, properties, chunk_label)"""
    made = []
    for name, entity_type, props, chunk_label in rows:
        row = MachineKnowledgeEntity(
            revision_id=revision.id, name=name, entity_type=entity_type,
            properties=props, source_chunk_id=f"chunk-{chunk_label}" if chunk_label else None)
        db.add(row)
        made.append(row)
    db.flush()
    return made


def _add_relation(db, revision, subject_id, subject_name, object_id, object_name,
                  chunk_label="p10", relation_type="mechanical"):
    row = MachineKnowledgeRelation(
        revision_id=revision.id, subject_id=subject_id, subject_name=subject_name,
        relation_type=relation_type, object_id=object_id, object_name=object_name,
        description=f"{subject_name} relates to {object_name}",
        source_chunk_id=f"chunk-{chunk_label}" if chunk_label else None)
    db.add(row)
    db.flush()
    return row


def test_both_sides_ambiguous_resolves_by_same_chunk(monkeypatch):
    db = make_db()
    revision, _ = make_revision_with_chunks(db)
    # Wingnut and Spool Spindle each appear twice: a p5 mention and a
    # p10 mention with a different location, so code keeps them apart.
    _add_entities(db, revision, [
        ("Wingnut", "component", {"function": "Secure wire spool", "location_description": "Interior"}, "p5"),
        ("Wingnut", "component", {"function": "Secures the spool in place over the spool spindle", "location_description": "Over the Spool Spindle"}, "p10"),
        ("Spool Spindle", "component", {"function": "Support wire spool", "location_description": "Interior"}, "p5"),
        ("Spool Spindle", "component", {"function": "Supports the wire spool", "location_description": "Welder wall"}, "p10"),
    ])
    _add_relation(db, revision, "entity:wingnut", "Wingnut",
                  "entity:spool-spindle", "Spool Spindle", chunk_label="p10")
    db.commit()
    seen = []
    _stub_adjudication(monkeypatch, seen)
    result = integrate_revision_knowledge(db, revision.id)
    assert len(result.model.relations) == 1, [r.subject_id for r in result.model.relations]
    rel = result.model.relations[0]
    subject = next(e for e in result.model.entities if e.id == rel.subject_id)
    obj = next(e for e in result.model.entities if e.id == rel.object_id)
    assert subject.properties["location_description"] == "Over the Spool Spindle"
    assert obj.properties["location_description"] == "Welder wall"
    assert not [u for u in result.payload["unresolved_facts"] if u.get("fact_type") == "relation"]
    # The relation was resolved in code, so no relation cluster was sent.
    assert not [c for c in seen if c.get("kind") == "relation_endpoint"]


def test_one_sided_ambiguous_resolves_by_same_chunk(monkeypatch):
    db = make_db()
    revision, _ = make_revision_with_chunks(db)
    _add_entities(db, revision, [
        ("Wire Spool", "component", {"function": "Holds wire"}, "p10"),
        ("Spool Spindle", "component", {"function": "Support wire spool", "location_description": "Interior"}, "p5"),
        ("Spool Spindle", "component", {"function": "Supports the wire spool", "location_description": "Welder wall"}, "p10"),
    ])
    _add_relation(db, revision, "entity:wire-spool", "Wire Spool",
                  "entity:spool-spindle", "Spool Spindle", chunk_label="p10")
    db.commit()
    seen = []
    _stub_adjudication(monkeypatch, seen)
    result = integrate_revision_knowledge(db, revision.id)
    assert len(result.model.relations) == 1
    rel = result.model.relations[0]
    obj = next(e for e in result.model.entities if e.id == rel.object_id)
    assert obj.properties["location_description"] == "Welder wall"


def test_same_chunk_with_two_candidates_stays_open(monkeypatch):
    db = make_db()
    revision, _ = make_revision_with_chunks(db)
    # Both Wingnut candidates come from the relation's own chunk: the
    # chunk itself cannot disambiguate, so the link must stay open.
    _add_entities(db, revision, [
        ("Wingnut", "component", {"function": "Secure wire spool", "location_description": "Over the Spool Spindle"}, "p10"),
        ("Wingnut", "component", {"function": "Secure wire spool", "location_description": "Inside cover"}, "p10"),
        ("Spool Spindle", "component", {"function": "Supports the wire spool", "location_description": "Welder wall"}, "p10"),
    ])
    _add_relation(db, revision, "entity:wingnut", "Wingnut",
                  "entity:spool-spindle", "Spool Spindle", chunk_label="p10")
    db.commit()
    seen = []
    _stub_adjudication(monkeypatch, seen)
    result = integrate_revision_knowledge(db, revision.id)
    assert len(result.model.relations) == 0
    assert any(u.get("fact_type") == "relation"
               and u.get("reason") == "relation_endpoint_not_uniquely_resolvable"
               for u in result.payload["unresolved_facts"])
    assert any(c.get("kind") == "relation_endpoint" for c in seen)


def test_no_chunk_info_stays_open(monkeypatch):
    db = make_db()
    revision, _ = make_revision_with_chunks(db)
    _add_entities(db, revision, [
        ("Wingnut", "component", {"function": "Secure wire spool", "location_description": "Interior"}, "p5"),
        ("Wingnut", "component", {"function": "Secures the spool in place over the spool spindle", "location_description": "Over the Spool Spindle"}, "p10"),
        ("Spool Spindle", "component", {"function": "Supports the wire spool", "location_description": "Welder wall"}, "p10"),
    ])
    _add_relation(db, revision, "entity:wingnut", "Wingnut",
                  "entity:spool-spindle", "Spool Spindle", chunk_label=None)
    db.commit()
    _stub_adjudication(monkeypatch, [])
    result = integrate_revision_knowledge(db, revision.id)
    assert len(result.model.relations) == 0
    assert any(u.get("fact_type") == "relation" for u in result.payload["unresolved_facts"])


def test_unique_resolution_unchanged_without_chunk_info(monkeypatch):
    db = make_db()
    revision, _ = make_revision_with_chunks(db)
    _add_entities(db, revision, [
        ("Wingnut", "component", {"function": "Secure wire spool"}, "p5"),
        ("Spool Spindle", "component", {"function": "Supports the wire spool"}, "p10"),
    ])
    _add_relation(db, revision, "entity:wingnut", "Wingnut",
                  "entity:spool-spindle", "Spool Spindle", chunk_label=None)
    db.commit()
    _stub_adjudication(monkeypatch, [])
    result = integrate_revision_knowledge(db, revision.id)
    assert len(result.model.relations) == 1


def test_universal_and_legacy_duplicate_dedupes_to_one(monkeypatch):
    db = make_db()
    revision, _ = make_revision_with_chunks(db)
    _add_entities(db, revision, [
        ("Wingnut", "component", {"function": "Secure wire spool", "location_description": "Interior"}, "p5"),
        ("Wingnut", "component", {"function": "Secures the spool in place over the spool spindle", "location_description": "Over the Spool Spindle"}, "p10"),
        ("Spool Spindle", "component", {"function": "Support wire spool", "location_description": "Interior"}, "p5"),
        ("Spool Spindle", "component", {"function": "Supports the wire spool", "location_description": "Welder wall"}, "p10"),
    ])
    universal = _add_relation(db, revision, "entity:wingnut", "Wingnut",
                              "entity:spool-spindle", "Spool Spindle", chunk_label="p10")
    # Legacy copy of the same relation, from the same chunk.
    wingnut = Component(revision_id=revision.id, name="Wingnut",
                        function="Secures the spool in place over the spool spindle",
                        location_description="Over the Spool Spindle",
                        source_chunk_id="chunk-p10")
    spindle = Component(revision_id=revision.id, name="Spool Spindle",
                        function="Supports the wire spool",
                        location_description="Welder wall",
                        source_chunk_id="chunk-p10")
    db.add(wingnut)
    db.add(spindle)
    db.flush()
    legacy = ComponentRelationship(
        from_component_id=wingnut.id, to_component_id=spindle.id,
        relation_type=RelationType.mechanical,
        description="Wingnut screws onto the Spool Spindle.",
        source_chunk_id="chunk-p10")
    db.add(legacy)
    db.commit()
    _stub_adjudication(monkeypatch, [])
    result = integrate_revision_knowledge(db, revision.id)
    assert len(result.model.relations) == 1, [r.source_ids for r in result.model.relations]
    rel = result.model.relations[0]
    assert set(rel.source_ids) == {universal.id, legacy.id}
    assert not [u for u in result.payload["unresolved_facts"] if u.get("fact_type") == "relation"]


def test_adjudication_link_does_not_duplicate_resolved_relation():
    # Pure code-first level: the relation is unresolved (both Wingnut
    # candidates come from its own chunk), a link decision arrives,
    # but an identical relation is already in the payload. The
    # decision must fold its source id in, not create a second copy.
    context = {
        "universal_entities": [
            {"source_id": "e-wingnut-a", "name": "Wingnut", "entity_type": "component",
             "properties": {"function": "Secure wire spool", "location_description": "Over the Spool Spindle"},
             "evidence": [], "source_chunk_id": "chunk-p10"},
            {"source_id": "e-wingnut-b", "name": "Wingnut", "entity_type": "component",
             "properties": {"function": "Secure wire spool", "location_description": "Inside cover"},
             "evidence": [], "source_chunk_id": "chunk-p10"},
            {"source_id": "e-spindle", "name": "Spool Spindle", "entity_type": "component",
             "properties": {"function": "Supports the wire spool"},
             "evidence": [], "source_chunk_id": "chunk-p10"},
        ],
        "universal_relations": [
            {"source_id": "r-1", "subject_id": "entity:wingnut", "subject_name": "Wingnut",
             "relation_type": "mechanical", "object_id": "entity:spool-spindle",
             "object_name": "Spool Spindle", "description": "Wingnut screws onto the Spool Spindle.",
             "evidence": [], "source_chunk_id": "chunk-p10"},
        ],
    }
    state = build_code_first_payload(context)
    assert len(state["payload"]["relations"]) == 0
    cluster = next(c for c in state["clusters"] if c.get("kind") == "relation_endpoint")
    subject_candidates = cluster["subject_candidates"]
    chosen = subject_candidates[0]["id"]
    # A code-resolved copy of the same relation already exists.
    state["payload"]["relations"].append({
        "subject_id": chosen, "subject_name": "Wingnut",
        "relation_type": "mechanical",
        "object_id": "entity:spool-spindle", "object_name": "Spool Spindle",
        "description": "Wingnut screws onto the Spool Spindle.",
        "source_ids": ["r-0"], "evidence": [],
    })
    apply_adjudication(state, [{"cluster_id": cluster["cluster_id"], "action": "link", "linked_id": chosen}])
    assert len(state["payload"]["relations"]) == 1
    assert set(state["payload"]["relations"][0]["source_ids"]) == {"r-0", "r-1"}
    assert not [u for u in state["payload"]["unresolved_facts"] if u.get("source_id") == "r-1"]
