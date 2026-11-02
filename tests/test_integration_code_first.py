"""Code-first global integration: must-merge / must-not-merge, relations,
fingerprint pre-check, and adjudication vetoes.

These tests use crater-2.db pairs as the gold set: Fan 44 vs Fan 53,
IGBT Radiator 1 vs 2, 120 vs 240 VAC Power Cord must NOT merge;
exact duplicates and subset failure modes MUST merge.
"""
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db.models import (
    Base, Component, MachineKnowledgeEntity, MachineKnowledgeFact,
    MachineKnowledgeModelSnapshot, MachineKnowledgeRelation, Product, Revision,
)
from backend.knowledge.global_integration import (
    integrate_revision_knowledge, persist_integration_result,
)


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def make_revision(db):
    product = Product(manufacturer="Harbor Freight", family="OMNIPRO", model="220")
    db.add(product); db.flush()
    revision = Revision(product_id=product.id, label="Rev A")
    db.add(revision); db.flush()
    return revision


def test_exact_duplicates_merge_and_conflicting_parts_stay_separate(monkeypatch):
    db = make_db(); revision = make_revision(db)
    # Exact duplicates: same name, same type, subset properties.
    db.add(MachineKnowledgeEntity(revision_id=revision.id, name="Mig/Tig/Stick Welder", entity_type="welding_equipment", properties={"function": "weld"}))
    db.add(MachineKnowledgeEntity(revision_id=revision.id, name="mig/tig/stick welder", entity_type="welding_equipment", properties={}))
    # Must NOT merge: Fan 44 vs Fan 53 (different item numbers).
    db.add(Component(revision_id=revision.id, name="Fan", function="", part_number="44"))
    db.add(Component(revision_id=revision.id, name="Fan", function="", part_number="53"))
    # Must NOT merge: IGBT Radiator 1 vs 2, and 120 vs 240 VAC cords.
    db.add(Component(revision_id=revision.id, name="IGBT Radiator 1", part_number="50"))
    db.add(Component(revision_id=revision.id, name="IGBT Radiator 2", part_number="53"))
    db.add(Component(revision_id=revision.id, name="120 VAC Power Cord", part_number="55"))
    db.add(Component(revision_id=revision.id, name="240 VAC Power Cord", part_number="61"))
    db.commit()
    # Adjudication is only called for ambiguous clusters; mock it to
    # return no decisions so the test does not depend on a real model.
    import backend.knowledge.code_first_integration as cf
    monkeypatch.setattr(cf, "adjudicate_clusters", lambda clusters: [])
    result = integrate_revision_knowledge(db, revision.id)
    names = [e.name for e in result.model.entities]
    welder = [e for e in result.model.entities if "Welder" in e.name or "welder" in e.name.lower()]
    assert len(welder) == 1, names
    assert len(welder[0].source_ids) == 2
    fans = [e for e in result.model.entities if e.name == "Fan"]
    assert len(fans) == 2, names
    assert {e.id for e in fans} == {"entity:fan-44", "entity:fan-53"}
    assert len([e for e in result.model.entities if e.name.startswith("IGBT Radiator")]) == 2
    assert len([e for e in result.model.entities if "Power Cord" in e.name]) == 2
    # Every source row is either canonical or unresolved, never lost.
    assert result.payload["completeness"]["source_total"] == 8
    assert result.payload["completeness"]["accounted_total"] == 8


def test_prose_fan_does_not_merge_into_first_parts_fan():
    from backend.knowledge.code_first_integration import build_code_first_payload
    context = {
        "universal_entities": [], "universal_relations": [], "universal_facts": [],
        "behaviors": [], "legacy_components": [
            {"source_id": "prose-fan", "name": "Fan", "function": "", "location_description": "", "part_number": "", "source_chunk_id": "chunk-prose"},
            {"source_id": "fan-44", "name": "Fan", "function": "", "location_description": "", "part_number": "44", "source_chunk_id": "chunk-parts"},
            {"source_id": "fan-53", "name": "Fan", "function": "", "location_description": "", "part_number": "53", "source_chunk_id": "chunk-parts"},
        ],
        "legacy_relationships": [], "procedures": [], "failure_modes": [],
        "schematic_nodes": [], "schematic_edges": [], "expert_knowledge": [],
    }
    state = build_code_first_payload(context)
    fans = [e for e in state["payload"]["entities"] if e["name"] == "Fan"]
    # Prose Fan is its own subgroup, not merged into Fan 44.
    assert len(fans) == 3


def test_unique_relation_rewrites_and_ambiguous_relation_stays_open():
    from backend.knowledge.code_first_integration import build_code_first_payload
    context = {
        "universal_entities": [
            {"source_id": "e1", "name": "Wire Feed Motor", "entity_type": "component", "properties": {}, "ports": [], "states": [], "evidence": []},
            {"source_id": "e2", "name": "Fan", "entity_type": "component", "properties": {"part_number": "44"}, "ports": [], "states": [], "evidence": []},
            {"source_id": "e3", "name": "Fan", "entity_type": "component", "properties": {"part_number": "53"}, "ports": [], "states": [], "evidence": []},
        ],
        "universal_relations": [
            {"source_id": "r1", "subject_id": "entity:wire-feed-motor", "subject_name": "Wire Feed Motor", "relation_type": "mechanical", "object_id": "entity:fan", "object_name": "Fan", "description": "Fan cools motor", "evidence": []},
        ],
        "universal_facts": [], "behaviors": [], "legacy_components": [],
        "legacy_relationships": [], "procedures": [], "failure_modes": [],
        "schematic_nodes": [], "schematic_edges": [], "expert_knowledge": [],
    }
    state = build_code_first_payload(context)
    # Fan is ambiguous (44 vs 53), so the relation must stay unresolved, not guess.
    assert state["payload"]["relations"] == []
    assert any(u["source_id"] == "r1" for u in state["payload"]["unresolved_facts"])


def test_failure_modes_dedupe_by_name_with_subset_causes():
    from backend.knowledge.code_first_integration import build_code_first_payload
    context = {
        "universal_entities": [], "universal_relations": [], "universal_facts": [],
        "behaviors": [], "legacy_components": [], "legacy_relationships": [],
        "procedures": [],
        "failure_modes": [
            {"source_id": "f1", "symptom": "Weak Arc Strength: Weak arc strength", "possible_causes": ["Incorrect line voltage."], "diagnostic_test": "", "expected_observation": "", "repair_procedure_id": None, "source_chunk_id": "c1"},
            {"source_id": "f2", "symptom": "Weak Arc Strength", "possible_causes": ["Incorrect line voltage.", "Improper gauge or length of cord."], "diagnostic_test": "Check line voltage.", "expected_observation": "", "repair_procedure_id": None, "source_chunk_id": "c2"},
        ],
        "schematic_nodes": [], "schematic_edges": [], "expert_knowledge": [],
    }
    state = build_code_first_payload(context)
    assert len(state["payload"]["failure_modes"]) == 1
    fm = state["payload"]["failure_modes"][0]
    assert set(fm["source_ids"]) == {"f1", "f2"}
    assert "Improper gauge or length of cord." in fm["possible_causes"]
    assert fm["diagnostic_test"] == "Check line voltage."


def test_adjudication_veto_rejects_merging_distinct_parts(monkeypatch):
    import backend.knowledge.code_first_integration as cf
    from backend.knowledge.code_first_integration import apply_adjudication, build_code_first_payload
    context = {
        "universal_entities": [], "universal_relations": [], "universal_facts": [],
        "behaviors": [], "legacy_components": [
            {"source_id": "fan-44", "name": "Fan", "function": "", "location_description": "", "part_number": "44", "source_chunk_id": "chunk-parts"},
            {"source_id": "fan-53", "name": "Fan", "function": "", "location_description": "", "part_number": "53", "source_chunk_id": "chunk-parts"},
        ],
        "legacy_relationships": [], "procedures": [], "failure_modes": [],
        "schematic_nodes": [], "schematic_edges": [], "expert_knowledge": [],
    }
    state = build_code_first_payload(context)
    clusters = state["clusters"]
    conflict = next(c for c in clusters if c["kind"] == "conflicting_same_name")
    # Even if the LLM says merge, code must veto it.
    apply_adjudication(state, [{"cluster_id": conflict["cluster_id"], "action": "merge", "kept_id": "entity:fan-44"}])
    fans = [e for e in state["payload"]["entities"] if e["name"] == "Fan"]
    assert len(fans) == 2


def test_fingerprint_precheck_returns_existing_snapshot_without_llm(monkeypatch):
    db = make_db(); revision = make_revision(db)
    db.add(MachineKnowledgeEntity(revision_id=revision.id, name="Pump P1", entity_type="pump"))
    db.commit()
    import backend.knowledge.code_first_integration as cf
    monkeypatch.setattr(cf, "adjudicate_clusters", lambda clusters: [])
    result = integrate_revision_knowledge(db, revision.id)
    stored = persist_integration_result(db, revision.id, result)
    db.commit()
    assert stored["version"] == 1
    # Second run: source fingerprint matches, so no adjudication and no new snapshot.
    calls = []
    monkeypatch.setattr(cf, "adjudicate_clusters", lambda clusters: calls.append(clusters) or [])
    result2 = integrate_revision_knowledge(db, revision.id)
    assert calls == []
    stored2 = persist_integration_result(db, revision.id, result2)
    assert stored2["unchanged"] is True
    assert stored2["version"] == 1


def test_legacy_components_survive_when_failure_modes_present():
    from backend.knowledge.schema import MachineKnowledgeExtractionResult
    from backend.knowledge.component_extraction import _universal_model_from_result
    result = MachineKnowledgeExtractionResult.model_validate({
        "components": [{"name": "MIG Gun", "function": "Delivers wire", "evidence": {"fact": "MIG Gun", "source_type": "text", "confidence": 0.9, "extraction_method": "llm"}}],
        "relationships": [], "procedures": [],
        "entities": [], "relations": [], "ports": [], "quantities": [], "states": [],
        "events": [], "behaviors": [], "constraints": [],
        "failure_modes": [{"id": "failure:x", "name": "Weak Arc", "symptoms": ["weak"], "possible_causes": ["voltage"], "evidence": []}],
    })
    model = _universal_model_from_result(result)
    assert any(e.name == "MIG Gun" for e in model.entities)
    assert any(fm.name == "Weak Arc" for fm in model.failure_modes)
