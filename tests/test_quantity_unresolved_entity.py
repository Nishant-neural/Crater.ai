"""Unresolved-entity quantities: a real measurement must survive even
when it has no entity to attach to.

Crater-4.db showed the loss precisely: the p7 spec table extracted 29
quantities (Mig Power Input 120/240 VAC, OCV 86, Wire Speed 50-500,
...) and the p29 duty-cycle table extracted 8, but the snapshot had
0 quantities. They were not lost in extraction -- they were
quarantined by validation (entity_id "unresolved:entity" not in the
chunk's kept entities) and, had they survived that, dropped again by
code-first integration (fact_entity_not_uniquely_resolvable).

A spec quantity is usually about a welding mode or the whole machine,
not about a named component in the same chunk, so its entity is
genuinely unresolvable at chunk level. The fix keeps such a quantity
when it is a real measurement (real name + non-empty value) and keeps
its entity_id as the explicit placeholder, never guessing an entity.
A quantity with an empty value or a placeholder name is not a
measurement (the p6 unit glossary: Vac, a, OcV, ...) and stays
quarantined. Ports/states/events keep the old behaviour.
"""
from backend.knowledge.code_first_integration import (
    build_code_first_payload,
    code_first_completeness,
)
from backend.knowledge.machine_model import (
    MachineEntity,
    MachineEvent,
    MachinePort,
    MachineQuantity,
    MachineState,
    UniversalMachineModel,
)
from backend.knowledge.validation import quarantine_machine_model


def _model_with_quantities():
    return UniversalMachineModel(
        entities=[
            MachineEntity(id="entity:spool-gun", name="Spool Gun", entity_type="component")
        ],
        quantities=[
            # p7: real spec, no entity in this chunk.
            MachineQuantity(id="quantity:mig_power_input_1", entity_id="unresolved:entity",
                            name="Mig Power Input", value="120 VAC / 60 Hz", unit="VAC / Hz"),
            # p29: real duty cycle, no entity in this chunk.
            MachineQuantity(id="quantity:tig_240vac_duty_1", entity_id="unresolved:entity",
                            name="TIG Rated Duty Cycle 240 VAC Use",
                            value="30% Use at 175 A for 10 continuous Minutes", unit="% / A / min"),
            # p6: glossary entry, empty value -- not a measurement.
            MachineQuantity(id="quantity:vac", entity_id="unresolved:entity",
                            name="Vac", value="", unit="Volts Alternating Current"),
            # Placeholder name -- not a usable fact even with a value.
            MachineQuantity(id="quantity:unnamed", entity_id="unresolved:entity",
                            name="Unresolved item", value="120", unit="VAC"),
            # Specific entity that is not in this chunk's kept set.
            MachineQuantity(id="quantity:feed_speed", entity_id="entity:wire-feed-mechanism",
                            name="Wire Feed Speed", value="50 - 500", unit="IPM"),
            # Linked quantity: entity is in the kept set, must survive as before.
            MachineQuantity(id="quantity:spool_capacity", entity_id="entity:spool-gun",
                            name="Spool Capacity", value="12", unit="lb"),
        ],
        ports=[MachinePort(id="port:x", entity_id="unresolved:entity", name="Terminal X")],
        states=[MachineState(entity_id="unresolved:entity", name="Power", value="on")],
        events=[MachineEvent(id="event:x", entity_id="unresolved:entity", name="Overheat")],
    )


def test_unlinked_real_quantities_are_kept_and_glossary_is_not():
    kept, quarantined = quarantine_machine_model(_model_with_quantities())
    names = {q.name for q in kept.quantities}
    assert names == {
        "Mig Power Input",
        "TIG Rated Duty Cycle 240 VAC Use",
        "Wire Feed Speed",
        "Spool Capacity",
    }, names
    # The kept unlinked quantities keep the placeholder, untouched.
    mig = next(q for q in kept.quantities if q.name == "Mig Power Input")
    assert mig.entity_id == "unresolved:entity"
    assert mig.value == "120 VAC / 60 Hz"
    # The glossary and placeholder-name quantities are quarantined.
    q_reasons = {r["source_id"]: r["reason"] for r in quarantined if r["fact_type"] == "quantity"}
    assert set(q_reasons) == {"quantity:vac", "quantity:unnamed"}, q_reasons
    assert all("unresolved_entity_reference" in r for r in q_reasons.values())
    # The specific-but-not-in-chunk quantity is kept, with a ledger note.
    notes = [r for r in quarantined if r["fact_type"] == "missing_link_note"]
    assert len(notes) == 1
    assert notes[0]["source_id"] == "quantity:feed_speed"
    assert "entity:wire-feed-mechanism" in notes[0]["reason"]
    # Ports/states/events with an unresolved entity are still quarantined.
    assert {r["fact_type"] for r in quarantined} >= {"quantity", "port", "state", "event"}
    assert kept.ports == [] and kept.states == [] and kept.events == []


def _quantity_fact(source_id, name, value, entity_id="unresolved:entity"):
    return {
        "source_id": source_id,
        "fact_type": "quantity",
        "fact_key": source_id,
        "payload": {
            "id": source_id, "entity_id": entity_id, "name": name,
            "value": value, "unit": "VAC",
            "subject_name": name, "evidence": [],
        },
    }


def _empty_context():
    return {
        "universal_entities": [], "universal_relations": [], "universal_facts": [],
        "behaviors": [], "legacy_components": [], "legacy_relationships": [],
        "procedures": [], "failure_modes": [], "schematic_nodes": [],
        "schematic_edges": [], "expert_knowledge": [],
    }


def test_code_first_keeps_unlinked_quantities_in_snapshot_payload():
    context = _empty_context()
    context["universal_facts"] = [
        _quantity_fact("quantity:mig_power_input_1", "Mig Power Input", "120 VAC / 60 Hz"),
        _quantity_fact("quantity:tig_240vac_duty_1", "TIG Rated Duty Cycle 240 VAC Use",
                       "30% Use at 175 A for 10 continuous Minutes"),
        _quantity_fact("quantity:vac", "Vac", ""),
        {
            "source_id": "port:x", "fact_type": "port", "fact_key": "port:x",
            "payload": {"id": "port:x", "entity_id": "unresolved:entity",
                        "name": "Terminal X", "evidence": []},
        },
    ]
    state = build_code_first_payload(context)
    quantities = state["payload"]["quantities"]
    assert {q["name"] for q in quantities} == {
        "Mig Power Input", "TIG Rated Duty Cycle 240 VAC Use",
    }, quantities
    mig = next(q for q in quantities if q["name"] == "Mig Power Input")
    assert mig["entity_id"] == "unresolved:entity"
    assert mig["subject_name"] == "Mig Power Input"
    assert mig["source_ids"] == ["quantity:mig_power_input_1"]
    # The glossary quantity and the unlinked port stay unresolved, not kept.
    unresolved_ids = {u["source_id"] for u in state["payload"]["unresolved_facts"]}
    assert "quantity:vac" in unresolved_ids
    assert "port:x" in unresolved_ids
    assert "quantity:mig_power_input_1" not in unresolved_ids
    # Completeness: the kept quantities count as accounted.
    audit = code_first_completeness(context, state["payload"])
    assert "quantity:mig_power_input_1" not in audit["missing_source_ids"]
    assert "quantity:tig_240vac_duty_1" not in audit["missing_source_ids"]
    assert "quantity:vac" in audit["missing_source_ids"]


def test_code_first_still_prefers_a_real_canonical_entity():
    context = _empty_context()
    context["universal_entities"] = [
        {"source_id": "e1", "name": "Spool Gun", "entity_type": "component",
         "properties": {}, "ports": [], "states": [], "evidence": []},
    ]
    context["universal_facts"] = [
        _quantity_fact("quantity:spool_capacity", "Spool Capacity", "12",
                       entity_id="entity:spool-gun"),
    ]
    state = build_code_first_payload(context)
    assert len(state["payload"]["quantities"]) == 1
    assert state["payload"]["quantities"][0]["entity_id"] == "entity:spool-gun"
