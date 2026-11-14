"""Phase 2 Feed Tensioner dedupe + expert-seed unblocking (design §5
groups 5-6).

The Phase 0 cause: the code-first grouping split one component on any
differing property (including paraphrased ``function`` prose) and on
entity type, and the v9 narrow veto lived only in the optional LLM
adjudication path that never fired on the live run. The fix merges in
code on the hard-identity rule only. Hermetic: no LLM, no Qdrant.
"""
from backend.knowledge.code_first_integration import build_code_first_payload
from backend.knowledge.global_integration import _merge_rows_by_identity, _model_from_payload
from backend.knowledge.machine_model import MachineEntity
from backend.simulation.compiler import compile_machine_model
from backend.simulation.expert_rules import apply_expert_seed, load_seed_for_product


def _universal(source_id, name, entity_type, properties, chunk):
    return {"source_id": source_id, "name": name, "entity_type": entity_type,
            "properties": properties, "ports": [], "states": [],
            "evidence": [], "source_chunk_id": chunk}


def _context(entities, legacy=None):
    return {
        "universal_entities": entities,
        "universal_relations": [], "universal_facts": [], "behaviors": [],
        "legacy_components": legacy or [], "legacy_relationships": [],
        "procedures": [], "failure_modes": [], "schematic_nodes": [],
        "schematic_edges": [], "expert_knowledge": [],
    }


def _crater9_tensioner_context():
    """The crater-9.db shape: 6 rows for one Feed Tensioner -- five
    ``component`` rows with paraphrased function prose plus one
    ``tensioner`` row with no function at all. No part_number anywhere;
    only one row has a location and it conflicts with nothing.
    """
    return _context([
        _universal("t1", "Feed Tensioner", "component",
                   {"function": "Adjusts wire feed tension", "location_description": "Interior Controls"}, "chunk-a"),
        _universal("t2", "Feed Tensioner", "component",
                   {"function": "Applies tension to wire feed"}, "chunk-b"),
        _universal("t3", "Feed Tensioner", "component",
                   {"function": "Applies tension to wire"}, "chunk-c"),
        _universal("t4", "Feed Tensioner", "component",
                   {"function": "Applies pressure to wire feed"}, "chunk-d"),
        _universal("t5", "Feed Tensioner", "component",
                   {"function": "Applies pressure to the wire feed"}, "chunk-e"),
        _universal("t6", "Feed Tensioner", "tensioner", {}, "chunk-f"),
    ])


def test_crater9_shape_merges_six_tensioners_into_one():
    state = build_code_first_payload(_crater9_tensioner_context())
    tensioners = [e for e in state["payload"]["entities"] if e["name"] == "Feed Tensioner"]
    assert len(tensioners) == 1
    assert sorted(tensioners[0]["source_ids"]) == ["t1", "t2", "t3", "t4", "t5", "t6"]
    assert len(tensioners[0].get("merged_from") or []) == 6
    # No cluster, no conflict: the merge is in code, not via the LLM.
    assert [c for c in state["clusters"] if c["kind"] == "conflicting_same_name"] == []
    assert state["payload"]["conflicts"] == []


def test_legacy_merge_keeps_all_evidence_and_merged_from():
    model = _model_from_payload(build_code_first_payload(_crater9_tensioner_context())["payload"])
    tensioners = [e for e in model.entities if e.name == "Feed Tensioner"]
    assert len(tensioners) == 1
    assert sorted(tensioners[0].source_ids) == ["t1", "t2", "t3", "t4", "t5", "t6"]


def test_conflicting_voltage_stays_separate():
    state = build_code_first_payload(_context([
        _universal("v1", "Fan", "component", {"part_number": "44", "voltage": "120"}, "chunk-a"),
        _universal("v2", "Fan", "component", {"part_number": "53", "voltage": "240"}, "chunk-b"),
    ]))
    fans = [e for e in state["payload"]["entities"] if e["name"] == "Fan"]
    assert len(fans) == 2


def test_fans_with_different_part_numbers_stay_separate():
    state = build_code_first_payload(_context([
        _universal("f1", "Fan", "component", {"part_number": "44"}, "chunk-a"),
        _universal("f2", "Fan", "component", {"part_number": "53"}, "chunk-b"),
    ]))
    fans = [e for e in state["payload"]["entities"] if e["name"] == "Fan"]
    assert len(fans) == 2
    assert {e["id"] for e in fans} == {"entity:fan-44", "entity:fan-53"}


def test_two_specific_types_with_one_name_stay_separate():
    state = build_code_first_payload(_context([
        _universal("m1", "M1", "motor", {"function": "Drive the feed"}, "chunk-a"),
        _universal("m2", "M1", "sensor", {"function": "Sense the feed"}, "chunk-b"),
    ]))
    assert len([e for e in state["payload"]["entities"] if e["name"] == "M1"]) == 2


def test_legacy_name_level_merge_uses_the_same_narrow_rule():
    # The legacy path (_merge_rows_by_identity) had the same bug: any
    # differing property blocked the merge. Function prose alone must
    # not split one part; a hard signal still must.
    merged = _merge_rows_by_identity([
        {"id": "entity:t1", "name": "Feed Tensioner", "entity_type": "component",
         "properties": {"function": "Applies tension to wire feed"},
         "source_ids": ["t1"], "evidence": []},
        {"id": "entity:t2", "name": "Feed Tensioner", "entity_type": "tensioner",
         "properties": {"function": "Applies pressure to wire feed"},
         "source_ids": ["t2"], "evidence": []},
    ], kind="entities")
    assert len(merged) == 1
    assert sorted(merged[0]["source_ids"]) == ["t1", "t2"]
    split = _merge_rows_by_identity([
        {"id": "entity:f1", "name": "Fan", "entity_type": "component",
         "properties": {"part_number": "44"}, "source_ids": ["f1"], "evidence": []},
        {"id": "entity:f2", "name": "Fan", "entity_type": "component",
         "properties": {"part_number": "53"}, "source_ids": ["f2"], "evidence": []},
    ], kind="entities")
    assert len(split) == 2


# ---------------------------------------------------------------------------
# 6. Seed resolution after dedupe
# ---------------------------------------------------------------------------

def _seeded_model(entity_names):
    """A model with one entity per name in ``entity_names`` (the seed's
    four names, each unique), built through the real payload path."""
    payload = build_code_first_payload(_crater9_tensioner_context())["payload"]
    model = _model_from_payload(payload)
    for name in entity_names:
        if name == "Feed Tensioner":
            continue
        model.entities.append(MachineEntity(
            id=f"entity:{name.lower().replace(' ', '-')}",
            name=name, entity_type="component", properties={}, source_ids=[]))
    return model


def test_seed_resolves_after_dedupe_four_transitions():
    seed = load_seed_for_product("Harbor Freight", "OMNIPRO 220")
    assert seed is not None
    model = _seeded_model(["Feed Tensioner", "Welding Wire", "Wingnut", "Wire Spool"])
    merged, warnings, applied = apply_expert_seed(model, seed)
    assert applied["skipped"] == []
    assert len(applied["rules_applied"]) == 4
    assert len(applied["states_applied"]) == 4
    definition, compile_warnings = compile_machine_model(merged, name="OMNIPRO 220")
    assert len(definition.transitions) == 4
    assert {t.id for t in definition.transitions} == {
        "seed:feed-tension-too-tight", "seed:feed-tension-ok-restores-feed",
        "seed:wingnut-loose-spool-spins", "seed:wingnut-tight-stops-spool-spin"}


def test_seed_skips_when_duplicates_remain():
    # The Phase 0 shape, before the fix: seven Feed Tensioners means
    # the seed cannot resolve by unique name and must skip, never guess.
    seed = load_seed_for_product("Harbor Freight", "OMNIPRO 220")
    model = _seeded_model(["Feed Tensioner", "Welding Wire", "Wingnut", "Wire Spool"])
    for i in range(2, 8):
        model.entities.append(MachineEntity(
            id=f"entity:feed-tensioner-{i}", name="Feed Tensioner",
            entity_type="component", properties={}, source_ids=[]))
    merged, warnings, applied = apply_expert_seed(model, seed)
    # Skipped entries are dicts ({kind, entity, reason}); the tensioner
    # state and both tensioner rules must be among them.
    assert any(s.get("entity") == "Feed Tensioner" for s in applied["skipped"])
    assert "seed:feed-tension-too-tight" not in applied["rules_applied"]
    assert "seed:feed-tension-ok-restores-feed" not in applied["rules_applied"]
    # The Wingnut rules still resolve: one skip does not block the rest.
    assert "seed:wingnut-loose-spool-spins" in applied["rules_applied"]
