"""Phase 2 canonical model: typed ports, first-class parameters,
library_type binding, source + precedence, model coverage.

Groups 1-4 of design §5. Hermetic: no LLM, no Qdrant, no real DB.
"""
from backend.knowledge.evidence import MachineEvidence
from backend.knowledge.global_integration import _model_from_payload
from backend.knowledge.identity import entity_types_compatible, hard_identity_conflict
from backend.knowledge.library_types import assign_library_types, bind_library_type
from backend.knowledge.machine_model import (
    MachineConflict,
    MachineEntity,
    MachinePort,
    MachineQuantity,
    UniversalMachineModel,
)
from backend.knowledge.provenance import (
    PRECEDENCE_RANK,
    parse_source_doc,
    precedence_for_source,
)
from backend.knowledge.repair_cases import model_coverage
from backend.simulation.compiler import compile_machine_model


# ---------------------------------------------------------------------------
# 1. Typed ports
# ---------------------------------------------------------------------------

def _ports_payload():
    return {
        "entities": [
            {"id": "entity:feeder", "name": "Wire Feeder", "entity_type": "component",
             "properties": {}, "source_ids": ["c1"], "evidence": []},
        ],
        "ports": [
            {"id": "port:drive", "entity_id": "entity:feeder", "name": "drive_in",
             "direction": "in", "domain": "electrical", "unit": "V",
             "properties": {}, "source_ids": ["c1"], "evidence": []},
            {"id": "port:wire", "entity_id": "entity:feeder", "name": "wire_out",
             "direction": "out", "domain": "mechanical",
             "properties": {}, "source_ids": ["c1"], "evidence": []},
        ],
        "quantities": [], "relations": [], "states": [], "events": [],
        "behaviors": [], "constraints": [], "procedures": [],
        "failure_modes": [], "simulation_rules": [], "conflicts": [],
        "unresolved_facts": [],
    }


def test_typed_ports_round_trip_and_deprecated_view():
    model = _model_from_payload(_ports_payload())
    assert len(model.entities) == 1
    entity = model.entities[0]
    assert [p.name for p in entity.typed_ports] == ["drive_in", "wire_out"]
    assert entity.typed_ports[0].direction == "in"
    assert entity.typed_ports[0].domain == "electrical"
    assert entity.typed_ports[0].unit == "V"
    assert entity.typed_ports[1].domain == "mechanical"
    # The deprecated list[str] view is derived, not a second truth.
    assert entity.ports == ["drive_in", "wire_out"]
    # Round-trip through JSON: typed ports survive persist -> load.
    restored = UniversalMachineModel.model_validate(model.model_dump(mode="json"))
    assert [p.name for p in restored.entities[0].typed_ports] == ["drive_in", "wire_out"]
    assert restored.entities[0].ports == ["drive_in", "wire_out"]


def test_v12_snapshot_without_typed_ports_derives_them_and_compiles():
    # A v12 snapshot carries ports only as a model-level list. The
    # typed ports are derived from that list, and the model still
    # compiles with the 0-transition warning preserved.
    payload = _ports_payload()
    for entity in payload["entities"]:
        entity.pop("typed_ports", None)
    model = _model_from_payload(payload)
    assert [p.name for p in model.entities[0].typed_ports] == ["drive_in", "wire_out"]
    definition, warnings = compile_machine_model(model, name="v12 compat")
    assert definition.transitions == []
    assert any("No explicit simulation rules" in w for w in warnings)


def test_typed_port_validates_from_llm_shaped_json():
    # v10.1 lesson: schema changes are tested with the real LLM-shaped
    # JSON through model_validate, not hand-built fixtures.
    port = MachinePort.model_validate({
        "id": "port:drive", "entity_id": "entity:feeder", "name": "drive_in",
        "direction": "in", "domain": "electrical", "unit": "V",
        "properties": {}, "source_ids": ["c1"], "evidence": [],
    })
    assert port.domain == "electrical" and port.unit == "V"


# ---------------------------------------------------------------------------
# 2. Parameters as first-class quantities
# ---------------------------------------------------------------------------

def test_quantity_bound_and_unbound_and_source_kind():
    bound = MachineQuantity.model_validate({
        "id": "q:wire-size", "entity_id": "entity:feeder", "name": "Wire size",
        "value": "0.030", "unit": "in", "source_kind": "spec_page",
        "properties": {}, "source_ids": ["c7"], "evidence": [],
    })
    assert bound.is_bound is True
    unbound = MachineQuantity.model_validate({
        "id": "q:motor-rating", "entity_id": "entity:motor", "name": "Motor DC rating",
        "value": None, "unit": "V", "source_kind": None,
        "properties": {}, "source_ids": [], "evidence": [],
    })
    assert unbound.is_bound is False
    # An unbound parameter is a gap in the model, not a borrowed value.
    assert unbound.value is None


def test_quantity_source_kind_derived_from_evidence():
    payload = _ports_payload()
    payload["quantities"] = [
        {"id": "q:wire-size", "entity_id": "entity:feeder", "name": "Wire size",
         "value": "0.030", "unit": "in", "properties": {}, "source_ids": ["c7"],
         "evidence": [{"fact": "Wire size 0.030 in", "source_document": "owner-manual.pdf",
                       "source_type": "spec_table", "page": 7}]},
        {"id": "q:param", "entity_id": "entity:feeder", "name": "Parameter #3028",
         "value": "1", "unit": "", "properties": {}, "source_ids": ["c9"],
         "evidence": [{"fact": "Parameter #3028", "source_document": "ib1501809engm Parameter Manual",
                       "source_type": "text", "page": 3}]},
    ]
    model = _model_from_payload(payload)
    by_id = {q.id: q for q in model.quantities}
    assert by_id["q:wire-size"].source_kind == "spec_page"
    assert by_id["q:param"].source_kind == "parameter_manual"


# ---------------------------------------------------------------------------
# 3. library_type binding (unique-match only)
# ---------------------------------------------------------------------------

def test_bind_library_type_aliases_and_refusals():
    assert bind_library_type("Wire Feed Mechanism", "component") == "wire_feeder"
    assert bind_library_type("Wire Feed Motor", "motor") == "dc_motor"
    assert bind_library_type("Power Relay", "relay") == "relay"
    # Feed Tensioner is a part of the feeder, not the feeder itself.
    assert bind_library_type("Feed Tensioner", "tensioner") is None
    # Bare "Motor" says nothing about DC vs AC.
    assert bind_library_type("Motor", "motor") is None
    # A bare name with a relay type does not bind: type-based binding
    # is a Phase 3 decision, not a Phase 2 guess.
    assert bind_library_type("K1", "relay") is None
    assert bind_library_type(None, "relay") is None


def test_assign_library_types_reports_ambiguous_never_guesses():
    # assign_library_types takes a model, sets library_type in place
    # where binding is unique, and returns a report: bound counts per
    # type, plus names that matched two-plus types. A name matching
    # nothing (Feed Tensioner) stays unbound -- it is not guessed.
    model = UniversalMachineModel(entities=[
        MachineEntity(id="e1", name="Wire Feed Mechanism", entity_type="component"),
        MachineEntity(id="e2", name="Wire Feed Motor", entity_type="motor"),
        MachineEntity(id="e3", name="Feed Tensioner", entity_type="tensioner"),
    ])
    report = assign_library_types(model)
    assert report == {"bound": {"wire_feeder": 1, "dc_motor": 1}, "ambiguous": []}
    by_id = {e.id: e for e in model.entities}
    assert by_id["e1"].library_type == "wire_feeder"
    assert by_id["e2"].library_type == "dc_motor"
    assert by_id["e3"].library_type is None


def test_library_type_field_lives_beside_the_coarse_bucket():
    payload = _ports_payload()
    payload["entities"][0]["library_type"] = "wire_feeder"
    model = _model_from_payload(payload)
    entity = model.entities[0]
    assert entity.library_type == "wire_feeder"
    # The coarse bucket is not removed in Phase 2.
    assert entity.entity_type == "component"
    # And when the payload carries no library_type, the name binds it.
    payload["entities"][0].pop("library_type")
    model = _model_from_payload(payload)
    assert model.entities[0].library_type == "wire_feeder"


# ---------------------------------------------------------------------------
# 4. Source + precedence, conflicts recorded not resolved
# ---------------------------------------------------------------------------

def test_precedence_ranking_and_source_doc_parsing():
    assert PRECEDENCE_RANK["mtb"] > PRECEDENCE_RANK["oem"] > PRECEDENCE_RANK["generic"]
    assert precedence_for_source("MTB maintenance manual") == "mtb"
    assert precedence_for_source("ib1501808engc_M800V_M80V_Series_Alarm_Manual.pdf") == "oem"
    assert precedence_for_source("some generic blog post") == "generic"
    # An explicit origin wins over the filename heuristics.
    assert precedence_for_source("manual.pdf", origin="mtb") == "mtb"
    assert parse_source_doc("IB-1501610 Specifications Manual") == {
        "ib_number": "IB-1501610", "source_doc": "IB-1501610 Specifications Manual"}
    assert parse_source_doc("ib1501808engc Alarm Manual")["ib_number"] == "IB-1501808"
    assert parse_source_doc("owner-manual.pdf") == {"ib_number": None, "source_doc": "owner-manual.pdf"}


def test_conflict_records_precedence_instead_of_merging():
    conflict = MachineConflict.model_validate({
        "id": "conflict:1", "subject": "Parameter #3028", "property": "value",
        "values": ["1", "0"], "resolution": None, "status": "unresolved",
        "precedence": {"mtb": "1", "oem": "0"},
        "source_ids": ["mtb-doc", "oem-doc"], "evidence": [],
    })
    assert conflict.precedence == {"mtb": "1", "oem": "0"}
    assert conflict.status == "unresolved"
    assert conflict.resolution is None


def test_stamp_provenance_stamps_precedence_revision_variant():
    from backend.ingestion.pipeline import _stamp_provenance

    model = UniversalMachineModel(entities=[
        MachineEntity(id="entity:a", name="A", entity_type="component",
                      properties={}, source_ids=[], evidence=[]),
        MachineEntity(id="entity:b", name="B", entity_type="component",
                      properties={}, source_ids=[],
                      evidence=[MachineEvidence(
                          fact="B exists", source_document="owner-manual.pdf",
                          source_type="text", precedence="oem")]),
    ])
    _stamp_provenance(model, "MTB maintenance manual", 12, "chunk-1", "text",
                      source_revision="Rev A", model_variant="220")
    ev_a = model.entities[0].evidence[0]
    assert ev_a.precedence == "mtb"
    assert ev_a.source_revision == "Rev A"
    assert ev_a.model_variant == "220"
    # Existing evidence keeps its own precedence; missing fields fill in.
    ev_b = model.entities[1].evidence[0]
    assert ev_b.precedence == "oem"
    assert ev_b.source_revision == "Rev A"
    assert ev_b.model_variant == "220"


def test_hard_identity_rule_only_blocks_on_hard_signals():
    assert hard_identity_conflict({"part_number": "44"}, {"part_number": "53"}) is True
    assert hard_identity_conflict({"voltage": "120"}, {"voltage": "240"}) is True
    assert hard_identity_conflict(
        {"location_description": "Interior Controls"},
        {"location_description": "Front Panel"}) is True
    # Paraphrased function prose never blocks a merge.
    assert hard_identity_conflict(
        {"function": "Applies tension to wire feed"},
        {"function": "Applies pressure to wire feed"}) is False
    # Empty values are not a claim.
    assert hard_identity_conflict({"part_number": "57812"}, {"part_number": ""}) is False
    # A generic type never blocks; two different specific types do.
    assert entity_types_compatible("component", "tensioner") is True
    assert entity_types_compatible("motor", "sensor") is False
    assert entity_types_compatible("Welding Equipment", "welding_equipment") is True


# ---------------------------------------------------------------------------
# 5. Coverage computed from the model
# ---------------------------------------------------------------------------

def test_model_coverage_names_unbound_parameters():
    snapshot = UniversalMachineModel(
        entities=[
            MachineEntity(id="entity:feeder", name="Wire Feeder", entity_type="component",
                          typed_ports=[MachinePort(id="p1", entity_id="entity:feeder", name="drive_in")],
                          library_type="wire_feeder", properties={}, source_ids=[]),
            MachineEntity(id="entity:motor", name="Wire Feed Motor", entity_type="motor",
                          properties={}, source_ids=[]),
        ],
        quantities=[
            MachineQuantity(id="q1", entity_id="entity:feeder", name="Wire size",
                            value="0.030", unit="in", source_kind="spec_page"),
            MachineQuantity(id="q2", entity_id="entity:motor", name="Motor DC rating",
                            value=None, unit="V"),
        ],
        failure_modes=[
            {"id": "fm:1", "name": "Wire does not feed", "entity_ids": ["entity:feeder"]},
            {"id": "fm:2", "name": "Motor overheats", "entity_ids": []},
        ],
        conflicts=[
            MachineConflict(id="c1", subject="Parameter #3028", property="value", values=["1", "0"]),
        ],
    )
    coverage = model_coverage(snapshot)
    assert coverage["entities"] == 2
    assert coverage["entities_with_typed_ports"] == 1
    assert coverage["entities_with_library_type"] == 1
    assert coverage["quantities"] == 2
    assert coverage["quantities_bound"] == 1
    assert coverage["quantities_unbound"] == ["Motor DC rating"]
    assert coverage["failure_modes"] == 2
    assert coverage["failure_modes_with_entity_links"] == 1
    assert coverage["conflicts"] == 1
