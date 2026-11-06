"""Relation endpoint recovery: a stated connection must survive even
when the LLM never listed one of its endpoints as an entity.

Crater-6.db showed the loss precisely: extraction produced 11
relations, all of which died at the chunk level -- 11 validation
quarantines (relation_endpoint unresolved_entity_reference) and 11
pending_relationship rows (cross_chunk_component_not_in_chunk) -- and
the snapshot had 0 relations, with 0 source relation rows at all.
Their endpoints (Ground Clamp Cable, Wire Feed Power Cable, TIG Torch
Cable, Electrode Holder Cable, Negative/Positive Sockets, Workpiece,
circuit breaker) were never extracted as entities anywhere in the run,
even though the text names them in 4-5 chunks; only Fan, Welder,
Ground Clamp and Reset Button among the endpoints exist. The v6
same-chunk fix handles endpoints that exist but are ambiguous; it
cannot recover endpoints that were never extracted.

The fix has three gates, mirroring the v5 quantity fix (a fix in only
one gate still loses the fact):
1. Prompt (source): both prompts now require every relation endpoint
   to also appear as a component/entity in the same output.
2. Validation (safety net): a relation whose endpoint is missing from
   the chunk's kept entities is kept, and a minimal stub entity is
   recorded for the missing endpoint -- id and name verbatim from the
   relation, nothing else invented, marked endpoint_derived, with a
   missing_link_note. A relation with no usable endpoint name is
   still quarantined.
3. Legacy persist: persist_extraction records a minimal stub component
   for a missing endpoint instead of queueing the relation as pending
   forever; an invalid relation_type still goes to pending.
4. Code-first: resolve_entity_id falls back to the endpoint name when
   the extractor's id slug does not resolve (e.g. entity:p3).
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db.models import (
    Base, Component, ComponentRelationship, UnresolvedFact,
)
from backend.knowledge import component_extraction as ce
from backend.knowledge.code_first_integration import build_code_first_payload
from backend.knowledge.component_extraction import (
    ExtractionResult, persist_extraction,
)
from backend.knowledge.machine_model import (
    MachineEntity, MachineRelation, UniversalMachineModel,
)
from backend.knowledge.schema import ExtractedComponent, ExtractedRelationship
from backend.knowledge.validation import _stub_id_for, quarantine_machine_model


def _rel(subject_id, subject_name, object_id, object_name, relation_type="electrical"):
    return MachineRelation(
        subject_id=subject_id, subject_name=subject_name,
        relation_type=relation_type, object_id=object_id, object_name=object_name,
        description=f"{subject_name} {relation_type} {object_name}",
    )


def test_stub_id_for():
    assert _stub_id_for("entity:ground-clamp-cable", "Ground Clamp Cable") == "entity:ground-clamp-cable"
    assert _stub_id_for("", "Ground Clamp Cable") == "entity:ground-clamp-cable"
    assert _stub_id_for("unresolved:entity", "Wire Feed Power Cable") == "entity:wire-feed-power-cable"
    assert _stub_id_for("", "") == ""


def test_both_endpoints_missing_relation_kept_with_stubs():
    # crater-6 p15 shape: the chunk lists feed parts, but the relation
    # endpoints (cable, socket) were never listed as entities.
    model = UniversalMachineModel(
        entities=[
            MachineEntity(id="entity:feed-roller", name="Feed Roller", entity_type="component"),
        ],
        relations=[
            _rel("entity:ground-clamp-cable", "Ground Clamp Cable",
                 "entity:negative-socket", "Negative Socket"),
        ],
    )
    kept, quarantined = quarantine_machine_model(model)
    assert len(kept.relations) == 1
    rel = kept.relations[0]
    assert rel.subject_id == "entity:ground-clamp-cable"
    assert rel.object_id == "entity:negative-socket"
    stubs = {e.name: e for e in kept.entities if e.properties.get("endpoint_derived")}
    assert set(stubs) == {"Ground Clamp Cable", "Negative Socket"}
    assert stubs["Ground Clamp Cable"].id == "entity:ground-clamp-cable"
    # A stub invents nothing: no function, no location, no part number.
    assert stubs["Ground Clamp Cable"].properties == {"endpoint_derived": True}
    # The real entity survives untouched alongside the stubs.
    assert any(e.name == "Feed Roller" for e in kept.entities)
    # Ledger shows both stubs were derived, and no relation was quarantined.
    notes = [q for q in quarantined if q.get("fact_type") == "missing_link_note"]
    assert len(notes) == 2
    assert not [q for q in quarantined if q.get("fact_type") == "relation"]


def test_one_endpoint_missing_one_stub_only():
    model = UniversalMachineModel(
        entities=[
            MachineEntity(id="entity:ground-clamp", name="Ground Clamp", entity_type="component"),
        ],
        relations=[
            _rel("entity:ground-clamp", "Ground Clamp",
                 "entity:workpiece", "Workpiece"),
        ],
    )
    kept, quarantined = quarantine_machine_model(model)
    assert len(kept.relations) == 1
    stubs = [e for e in kept.entities if e.properties.get("endpoint_derived")]
    assert [e.name for e in stubs] == ["Workpiece"]
    assert len([q for q in quarantined if q.get("fact_type") == "missing_link_note"]) == 1


def test_shared_endpoint_gets_one_stub_for_two_relations():
    model = UniversalMachineModel(
        entities=[],
        relations=[
            _rel("entity:ground-clamp-cable", "Ground Clamp Cable",
                 "entity:negative-socket", "Negative Socket"),
            _rel("entity:ground-clamp-cable", "Ground Clamp Cable",
                 "entity:positive-socket", "Positive Socket"),
        ],
    )
    kept, _ = quarantine_machine_model(model)
    assert len(kept.relations) == 2
    stubs = [e for e in kept.entities if e.properties.get("endpoint_derived")]
    assert sorted(e.name for e in stubs) == [
        "Ground Clamp Cable", "Negative Socket", "Positive Socket",
    ]


def test_placeholder_endpoint_id_rewritten_to_name_slug():
    model = UniversalMachineModel(
        entities=[],
        relations=[
            _rel("unresolved:subject", "TIG Torch Cable",
                 "unresolved:object", "Negative Socket"),
        ],
    )
    kept, _ = quarantine_machine_model(model)
    assert len(kept.relations) == 1
    rel = kept.relations[0]
    assert rel.subject_id == "entity:tig-torch-cable"
    assert rel.object_id == "entity:negative-socket"
    assert {e.id for e in kept.entities} == {
        "entity:tig-torch-cable", "entity:negative-socket",
    }


def test_relation_with_no_usable_endpoint_name_still_quarantined():
    # Empty names become the normalizer's "Unresolved subject/object"
    # placeholders; a stub for those would invent an entity from nothing.
    model = UniversalMachineModel(
        entities=[],
        relations=[
            MachineRelation(
                subject_id="", subject_name="", relation_type="electrical",
                object_id="", object_name="", description="",
            ),
        ],
    )
    kept, quarantined = quarantine_machine_model(model)
    assert kept.relations == []
    assert kept.entities == []
    assert any(q.get("fact_type") == "relation" for q in quarantined)


def test_relation_with_both_endpoints_present_unchanged():
    model = UniversalMachineModel(
        entities=[
            MachineEntity(id="entity:fan", name="Fan", entity_type="component"),
            MachineEntity(id="entity:welder", name="Welder", entity_type="component"),
        ],
        relations=[
            _rel("entity:fan", "Fan", "entity:welder", "Welder", "contains"),
        ],
    )
    kept, quarantined = quarantine_machine_model(model)
    assert len(kept.relations) == 1
    assert len(kept.entities) == 2
    assert not [e for e in kept.entities if e.properties.get("endpoint_derived")]
    assert quarantined == []


def test_prompts_require_relation_endpoints_as_entities():
    rule = "must also appear as a component/entity"
    assert rule in ce._EXTRACTION_PROMPT
    assert rule in ce._BATCH_EXTRACTION_PROMPT
    assert "Cables, sockets, connectors" in ce._EXTRACTION_PROMPT
    assert "Cables, sockets, connectors" in ce._BATCH_EXTRACTION_PROMPT


def _make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_persist_extraction_stubs_missing_endpoint_instead_of_pending():
    db = _make_db()
    result = ExtractionResult(
        components=[ExtractedComponent(name="Ground Clamp")],
        relationships=[
            ExtractedRelationship(
                from_component="Ground Clamp Cable", to_component="Negative Socket",
                relation_type="electrical",
                description="Plug Ground Clamp Cable into Negative Socket"),
            ExtractedRelationship(
                from_component="Ground Clamp Cable", to_component="Positive Socket",
                relation_type="electrical",
                description="Plug Ground Clamp Cable into Positive Socket"),
        ],
    )
    persist_extraction(db, "rev-1", "chunk-p15", result)
    db.commit()
    names = sorted(c.name for c in db.query(Component).all())
    assert names == ["Ground Clamp", "Ground Clamp Cable", "Negative Socket", "Positive Socket"]
    # The cable stub is reused by the second relationship, not duplicated.
    assert len([c for c in db.query(Component).all() if c.name == "Ground Clamp Cable"]) == 1
    rels = db.query(ComponentRelationship).all()
    assert len(rels) == 2
    pending = db.query(UnresolvedFact).filter_by(fact_type="pending_relationship").all()
    assert pending == []


def test_persist_extraction_invalid_type_still_goes_to_pending():
    db = _make_db()
    result = ExtractionResult(
        components=[ExtractedComponent(name="Ground Clamp")],
        relationships=[
            ExtractedRelationship(
                from_component="Ground Clamp", to_component="Workpiece",
                relation_type="not_a_type", description="Ground clamp connects"),
        ],
    )
    persist_extraction(db, "rev-1", "chunk-p42", result)
    db.commit()
    assert db.query(ComponentRelationship).all() == []
    pending = db.query(UnresolvedFact).filter_by(fact_type="pending_relationship").all()
    assert len(pending) == 1
    assert pending[0].reason == "invalid_relation_type"


def test_code_first_falls_back_to_endpoint_name_when_id_slug_fails():
    # The extractor used an invented id (entity:p3) for a named endpoint.
    context = {
        "universal_entities": [
            {"source_id": "e-cable", "name": "Ground Clamp Cable", "entity_type": "component",
             "properties": {"endpoint_derived": True}, "evidence": [],
             "source_chunk_id": "chunk-p15"},
            {"source_id": "e-socket", "name": "Negative Socket", "entity_type": "component",
             "properties": {"endpoint_derived": True}, "evidence": [],
             "source_chunk_id": "chunk-p15"},
        ],
        "universal_relations": [
            {"source_id": "r-1", "subject_id": "entity:p3", "subject_name": "Ground Clamp Cable",
             "relation_type": "electrical", "object_id": "entity:p7",
             "object_name": "Negative Socket",
             "description": "Plug Ground Clamp Cable into Negative Socket.",
             "evidence": [], "source_chunk_id": "chunk-p15"},
        ],
    }
    state = build_code_first_payload(context)
    assert len(state["payload"]["relations"]) == 1
    rel = state["payload"]["relations"][0]
    assert rel["subject_id"] == "entity:ground-clamp-cable"
    assert rel["object_id"] == "entity:negative-socket"


def test_code_first_dedupes_same_relation_from_two_chunks():
    # crater-6 shape: Ground Clamp Cable -> Negative Socket appears in
    # both the p15 and the p24 chunk (once as "Negative (-) Socket").
    # Both resolve to the same canonical endpoints, so the snapshot
    # must hold one relation with both source ids.
    def entity(sid, name, chunk):
        return {"source_id": sid, "name": name, "entity_type": "component",
                "properties": {"endpoint_derived": True}, "evidence": [],
                "source_chunk_id": chunk}

    context = {
        "universal_entities": [
            entity("e-gcc-15", "Ground Clamp Cable", "chunk-p15"),
            entity("e-neg-15", "Negative (-) Socket", "chunk-p15"),
            entity("e-gcc-24", "Ground Clamp Cable", "chunk-p24"),
            entity("e-neg-24", "Negative Socket", "chunk-p24"),
        ],
        "universal_relations": [
            {"source_id": "r-15", "subject_id": "entity:ground-clamp-cable",
             "subject_name": "Ground Clamp Cable", "relation_type": "electrical",
             "object_id": "entity:negative-(-)-socket", "object_name": "Negative (-) Socket",
             "description": "Plug Ground Clamp Cable into Negative (-) Socket.",
             "evidence": [], "source_chunk_id": "chunk-p15"},
            {"source_id": "r-24", "subject_id": "entity:ground-clamp-cable",
             "subject_name": "Ground Clamp Cable", "relation_type": "electrical",
             "object_id": "entity:negative-socket", "object_name": "Negative Socket",
             "description": "Plug Ground Clamp Cable into Negative Socket (Stick Setup).",
             "evidence": [], "source_chunk_id": "chunk-p24"},
        ],
    }
    state = build_code_first_payload(context)
    assert len(state["payload"]["relations"]) == 1, state["payload"]["relations"]
    rel = state["payload"]["relations"][0]
    assert set(rel["source_ids"]) == {"r-15", "r-24"}
    assert len(state["payload"]["entities"]) == 2  # cable + socket, merged across chunks
