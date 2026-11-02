"""Regression tests for the gap 6/7/8 + DB fixes (OMNIPRO crater.db findings).

No LLM or Qdrant is used: these test the code paths that previously lost,
duplicated, or silently hid facts.
"""
from backend.knowledge.global_integration import _merge_rows_by_identity, _snapshot_fingerprint
from backend.knowledge.machine_model import MachineEntity, MachineRelation, UniversalMachineModel
from backend.knowledge.validation import quarantine_machine_model
from backend.schematic.graph import schematic_to_machine_model
from backend.schematic.schema import ExtractedEdge, ExtractedNode, SchematicExtractionResult


def _entity(id_, name, props=None):
    return MachineEntity(id=id_, name=name, entity_type="component", properties=props or {})


def test_merge_same_name_no_conflict_merges_welder_duplicates():
    rows = [
        {"id": "entity:mig_welder", "name": "Mig Welder", "entity_type": "Welding Equipment", "properties": {}, "source_ids": ["src-a"], "evidence": []},
        {"id": "entity:mig-welder", "name": "Mig Welder", "entity_type": "welding_equipment", "properties": {}, "source_ids": ["src-b"], "evidence": []},
    ]
    merged = _merge_rows_by_identity(rows, kind="entities")
    assert len(merged) == 1
    assert set(merged[0]["source_ids"]) == {"src-a", "src-b"}


def test_merge_same_name_with_conflict_keeps_two_fans():
    rows = [
        {"id": "entity:fan-1", "name": "Fan", "entity_type": "component", "properties": {"part_number": "1"}, "source_ids": ["src-1"], "evidence": []},
        {"id": "entity:fan-2", "name": "Fan", "entity_type": "component", "properties": {"part_number": "2"}, "source_ids": ["src-2"], "evidence": []},
    ]
    merged = _merge_rows_by_identity(rows, kind="entities")
    assert len(merged) == 2


def test_merge_failure_modes_same_name_merges():
    rows = [
        {"id": "failure:a", "name": "Wire Stops", "possible_causes": ["clogged liner"], "source_ids": ["f1"], "evidence": []},
        {"id": "failure:b", "name": "Wire Stops", "possible_causes": ["clogged liner"], "source_ids": ["f2"], "evidence": []},
    ]
    merged = _merge_rows_by_identity(rows, kind="failure_modes")
    assert len(merged) == 1
    assert set(merged[0]["source_ids"]) == {"f1", "f2"}


def test_integration_quarantine_keeps_good_entities():
    model = UniversalMachineModel(
        entities=[_entity("entity:fan", "Fan"), _entity("entity:motor", "Motor")],
        relations=[
            MachineRelation(subject_id="entity:fan", subject_name="Fan", relation_type="drives", object_id="entity:motor", object_name="Motor"),
            MachineRelation(subject_id="entity:fan", subject_name="Fan", relation_type="drives", object_id="entity:missing", object_name="Missing"),
        ],
    )
    kept, quarantined = quarantine_machine_model(model)
    assert [e.id for e in kept.entities] == ["entity:fan", "entity:motor"]
    assert len(kept.relations) == 1
    assert any(q["reason"].startswith("unresolved_entity_reference") for q in quarantined)


def test_schematic_model_uses_real_ports_not_synthetic_terminal():
    result = SchematicExtractionResult(nodes=[
        ExtractedNode(label="K17", symbol_type="relay", ports=[]),
        ExtractedNode(label="M1", symbol_type="motor", ports=[{"name": "A1"}, {"name": "A2"}]),
    ], edges=[ExtractedEdge(from_label="K17", to_label="M1", wire_type="power")])
    model = schematic_to_machine_model(result, "manual", 1, "chunk-1")
    # K17 has no ports at all; M1 has exactly its two real pins, no "terminal".
    assert [p.name for p in model.ports] == ["A1", "A2"]
    assert len(model.relations) == 1
    assert model.entities[0].properties["link_normalized_label"] == "k17"


def test_snapshot_fingerprint_ignores_completeness_metadata():
    a = {"entities": [{"id": "e1", "name": "Fan"}], "relations": [], "failure_modes": [], "procedures": [], "quantities": [], "completeness": {"missing_count": 0}}
    b = {"entities": [{"id": "e1", "name": "Fan"}], "relations": [], "failure_modes": [], "procedures": [], "quantities": [], "completeness": {"missing_count": 3}}
    assert _snapshot_fingerprint(a) == _snapshot_fingerprint(b)


def test_pending_link_unique_prefix_resolves_wire_feed_mechanism():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from backend.db.models import Base, Component, UnresolvedFact
    from backend.ingestion.pending_links import resolve_pending_relationships

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    rev = "rev-1"
    # Minimal revision/product rows are not enforced by SQLite FK in this
    # in-memory setup for Component/UnresolvedFact in the project's models
    # when FK constraints are off; use the model's own defaults where possible.
    motor = Component(revision_id=rev, name="Wire Feed Motor")
    s.add(motor)
    s.flush()
    s.add(UnresolvedFact(revision_id=rev, source_id="pending-rel:x", fact_type="pending_relationship",
                         payload={"from_component": "MIG Gun Cable Connector", "to_component": "Wire Feed mechanism",
                                  "relation_type": "mechanical", "description": "inserted into", "source_chunk_id": "c1"},
                         reason="cross_chunk_component_not_in_chunk", status="open"))
    # Connector must exist as a component for the from side to resolve uniquely.
    conn = Component(revision_id=rev, name="MIG Gun Cable Connector")
    s.add(conn)
    s.commit()
    resolved, still_open = resolve_pending_relationships(s, rev)
    assert resolved == 1
    assert still_open == 0


def test_resolve_unresolved_fact_clears_only_matching_source():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from backend.db.models import Base, UnresolvedFact
    from backend.knowledge.normalization import persist_unresolved_fact, resolve_unresolved_fact

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    persist_unresolved_fact(s, revision_id="r1", source_id="chunk-a", fact_type="chunk_extraction_failure", payload={}, reason="x")
    persist_unresolved_fact(s, revision_id="r1", source_id="chunk-b", fact_type="chunk_extraction_failure", payload={}, reason="x")
    s.commit()
    assert resolve_unresolved_fact(s, revision_id="r1", source_id="chunk-a", fact_type="chunk_extraction_failure") == 1
    s.commit()
    rows = {r.source_id: r.status for r in s.query(UnresolvedFact).all()}
    assert rows == {"chunk-a": "resolved", "chunk-b": "open"}


def test_vision_unparseable_output_carries_parse_error(tmp_path, monkeypatch):
    from backend.schematic import vision_extraction as ve
    img = tmp_path / "diag.png"
    # Small valid PNG so tiling is skipped (max side <= 1600).
    from PIL import Image
    Image.new("RGB", (100, 100), "white").save(img)
    monkeypatch.setattr(ve.gateway, "complete", lambda *a, **k: "not json at all {{{")
    result = ve.extract_schematic(img)
    assert result.nodes == []
    assert result.parse_error == "vision_output_unparseable"


def test_lenient_json_trailing_comma_and_single_quotes_and_unquoted_keys():
    from backend.knowledge.component_extraction import _parse_lenient_json
    cases = [
        '{"components": [{"name": "Fan",}], "entities": []}',
        "{'components': [{'name': 'Fan'}], 'entities': []}",
        '{components: [{"name": "Fan"}], entities: []}',
        '{"components": [{"name": "Fan"}], // a comment\n "entities": []}',
        '```json\n{"components": [{"name": "Fan"}], "entities": []}\n```',
        'Here is the extraction:\n{"components": [{"name": "Fan"}], "entities": []}\nDone.',
    ]
    for raw in cases:
        parsed = _parse_lenient_json(raw, allow_repair=False)
        assert parsed["components"][0]["name"] == "Fan", raw


def test_lenient_json_repair_retry_recovers_broken_string(monkeypatch):
    from backend.knowledge import component_extraction as ce
    calls = {"n": 0}

    def fake_complete(task, messages, max_tokens=None):
        calls["n"] += 1
        return '{"components": [{"name": "Fan"}], "entities": []}'

    monkeypatch.setattr(ce.gateway, "complete", fake_complete)
    # Unrecoverable deterministically: unescaped quote inside a string.
    parsed = ce._parse_lenient_json('{"components": [{"name": "Fan "x" }], "entities": []}', allow_repair=True)
    assert parsed["components"][0]["name"] == "Fan"
    assert calls["n"] == 1
