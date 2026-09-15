"""
Tests for Phase 4 visualization logic. Uses a real in-memory SQLite DB
(fast, no external services) so the SQLAlchemy joins in
component_explorer.py / interactive_diagram.py / procedure_viz.py are
actually exercised, rather than mocked away.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db.models import (
    Base,
    Chunk,
    ChunkType,
    Component,
    ComponentRelationship,
    Document,
    DocType,
    Procedure,
    ProcedureType,
    Product,
    RelationType,
    Revision,
    SchematicEdge,
    SchematicNode,
    SymbolType,
    WireType,
)
from backend.visualization.component_explorer import build_component_explorer
from backend.visualization.interactive_diagram import build_interactive_diagram
from backend.visualization.procedure_viz import build_procedure_visualization, get_frame


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def diagram_image(tmp_path):
    path = tmp_path / "diagram.png"
    Image.new("RGB", (1000, 500), color="white").save(path)
    return str(path)


def _seed_basic_fixture(db, diagram_image):
    """Product -> Revision -> Document(schematic) -> Chunk(diagram) with two
    linked Components (Relay K17, Motor M1), one relationship, one SchematicNode
    per component on the same diagram chunk, and one wire between them."""
    product = Product(manufacturer="Acme", family="CNC", model="CNC-500X")
    db.add(product)
    db.flush()

    revision = Revision(product_id=product.id, label="Rev C")
    db.add(revision)
    db.flush()

    document = Document(revision_id=revision.id, doc_type=DocType.schematic, title="Wiring Diagram", source_path="x.pdf")
    db.add(document)
    db.flush()

    chunk = Chunk(
        document_id=document.id,
        chunk_type=ChunkType.diagram,
        page_number=3,
        content="diagram region",
        extra={"image_path": diagram_image},
    )
    db.add(chunk)
    db.flush()

    relay = Component(revision_id=revision.id, name="Relay K17", function="Switches motor power")
    motor = Component(revision_id=revision.id, name="Motor M1", function="Drives the spindle")
    db.add_all([relay, motor])
    db.flush()

    db.add(
        ComponentRelationship(
            from_component_id=relay.id,
            to_component_id=motor.id,
            relation_type=RelationType.electrical,
            description="K17 switches power to M1",
        )
    )

    relay_node = SchematicNode(
        document_id=document.id,
        chunk_id=chunk.id,
        component_id=relay.id,
        label="K17",
        symbol_type=SymbolType.relay,
        bbox={"x": 0.1, "y": 0.2, "w": 0.05, "h": 0.05},
    )
    motor_node = SchematicNode(
        document_id=document.id,
        chunk_id=chunk.id,
        component_id=motor.id,
        label="M1",
        symbol_type=SymbolType.motor,
        bbox={"x": 0.6, "y": 0.4, "w": 0.1, "h": 0.1},
    )
    db.add_all([relay_node, motor_node])
    db.flush()

    db.add(
        SchematicEdge(
            document_id=document.id,
            from_node_id=relay_node.id,
            to_node_id=motor_node.id,
            wire_type=WireType.power,
            label="W1",
        )
    )
    db.commit()

    return {
        "revision": revision,
        "document": document,
        "chunk": chunk,
        "relay": relay,
        "motor": motor,
    }


def test_component_explorer_includes_appearances_and_relationships(db, diagram_image):
    ctx = _seed_basic_fixture(db, diagram_image)

    explorer = build_component_explorer(db, ctx["revision"].id)

    assert explorer.revision_id == ctx["revision"].id
    by_name = {c.name: c for c in explorer.components}
    assert set(by_name) == {"Relay K17", "Motor M1"}

    relay_entry = by_name["Relay K17"]
    assert len(relay_entry.appearances) == 1
    assert relay_entry.appearances[0].label == "K17"
    assert relay_entry.appearances[0].document_title == "Wiring Diagram"
    assert len(relay_entry.related_components) == 1
    assert relay_entry.related_components[0].name == "Motor M1"
    assert relay_entry.related_components[0].direction == "outgoing"

    motor_entry = by_name["Motor M1"]
    assert motor_entry.related_components[0].direction == "incoming"


def test_component_explorer_empty_revision_returns_no_components(db):
    revision = Revision(product_id="does-not-exist", id="rev-empty", label="Rev Z")
    # bypass FK check semantics by not committing a real product; SQLite w/o FK
    # enforcement lets this insert stand purely to exercise the empty-list path.
    db.add(Product(id="does-not-exist", manufacturer="X", family="Y", model="Z"))
    db.add(revision)
    db.commit()

    explorer = build_component_explorer(db, "rev-empty")
    assert explorer.components == []


def test_interactive_diagram_manifest_has_pixel_bboxes_and_edges(db, diagram_image):
    ctx = _seed_basic_fixture(db, diagram_image)

    manifest = build_interactive_diagram(db, ctx["chunk"].id)

    assert manifest is not None
    assert manifest.image_width == 1000
    assert manifest.image_height == 500
    assert manifest.document_title == "Wiring Diagram"
    assert manifest.page_number == 3

    nodes_by_label = {n.label: n for n in manifest.nodes}
    assert nodes_by_label["K17"].bbox.x == pytest.approx(100.0)   # 0.1 * 1000
    assert nodes_by_label["K17"].bbox.y == pytest.approx(100.0)   # 0.2 * 500
    assert nodes_by_label["K17"].component_name == "Relay K17"

    assert len(manifest.edges) == 1
    assert manifest.edges[0].from_label == "K17"
    assert manifest.edges[0].to_label == "M1"
    assert manifest.edges[0].wire_type == "power"


def test_interactive_diagram_returns_none_for_unknown_chunk(db):
    assert build_interactive_diagram(db, "no-such-chunk") is None


def test_procedure_visualization_links_steps_to_located_components(db, diagram_image):
    ctx = _seed_basic_fixture(db, diagram_image)

    procedure = Procedure(
        revision_id=ctx["revision"].id,
        name="Replace drive relay",
        procedure_type=ProcedureType.replacement,
        steps=[
            "Power down the machine and lock out the main disconnect.",
            "Locate Relay K17 behind the control panel and remove its wiring.",
            "Install the new Relay K17 and verify Motor M1 spins freely.",
        ],
    )
    db.add(procedure)
    db.commit()

    viz = build_procedure_visualization(db, procedure.id)

    assert viz is not None
    assert viz.name == "Replace drive relay"
    assert len(viz.steps) == 3

    # Step 0 mentions no known component -> no frames.
    assert viz.steps[0].referenced_components == []
    assert viz.steps[0].frames == []

    # Step 1 mentions only the relay -> one frame, one label.
    assert viz.steps[1].referenced_components == ["Relay K17"]
    assert len(viz.steps[1].frames) == 1
    assert viz.steps[1].frames[0].labels == ["K17"]

    # Step 2 mentions both components, both on the same diagram chunk -> one
    # frame (grouped by chunk) carrying both labels.
    assert set(viz.steps[2].referenced_components) == {"Relay K17", "Motor M1"}
    assert len(viz.steps[2].frames) == 1
    assert set(viz.steps[2].frames[0].labels) == {"K17", "M1"}

    # animation_frames flattens steps in order with globally-unique indices.
    assert [f.frame_index for f in viz.animation_frames] == [0, 1]
    assert [f.step_index for f in viz.animation_frames] == [1, 2]


def test_get_frame_out_of_range_returns_none(db, diagram_image):
    ctx = _seed_basic_fixture(db, diagram_image)
    procedure = Procedure(
        revision_id=ctx["revision"].id,
        name="No-op procedure",
        procedure_type=ProcedureType.verification,
        steps=["Nothing component-specific here."],
    )
    db.add(procedure)
    db.commit()

    assert get_frame(db, procedure.id, 0) is None
    assert get_frame(db, "no-such-procedure", 0) is None


def test_get_frame_returns_matching_frame(db, diagram_image):
    ctx = _seed_basic_fixture(db, diagram_image)
    procedure = Procedure(
        revision_id=ctx["revision"].id,
        name="Replace drive relay",
        procedure_type=ProcedureType.replacement,
        steps=["Locate Relay K17 and inspect it."],
    )
    db.add(procedure)
    db.commit()

    frame = get_frame(db, procedure.id, 0)
    assert frame is not None
    assert frame.labels == ["K17"]
    assert frame.chunk_id == ctx["chunk"].id
