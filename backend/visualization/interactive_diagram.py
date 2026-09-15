"""
Interactive diagram manifest (plan.md Phase 4: "Interactive diagrams",
"Annotated schematics").

Phase 3 already produces the component/wire graph and normalized (0-1)
bboxes; this module is the thin re-projection into pixel space plus
image metadata a frontend canvas actually needs to draw a pannable,
zoomable, clickable diagram instead of a static highlighted PNG.

Scoped per diagram *chunk* (one image), not per document, because a
manual's "Document" can contain several diagram pages/chunks, each with
its own image and its own local node/edge set (this mirrors how Phase 3
already scopes SchematicNode.chunk_id, and how the Phase 3 highlight
endpoint resolves a node's source image via its chunk).
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image
from sqlalchemy.orm import Session

from backend.db.models import Chunk, Component, Document, SchematicEdge, SchematicNode
from backend.visualization.schema import (
    InteractiveDiagramManifest,
    InteractiveEdge,
    InteractiveNode,
    PixelBBox,
)


def _to_pixel_bbox(bbox: dict | None, width: int, height: int) -> PixelBBox | None:
    if not bbox:
        return None
    return PixelBBox(
        x=bbox.get("x", 0.0) * width,
        y=bbox.get("y", 0.0) * height,
        w=bbox.get("w", 0.0) * width,
        h=bbox.get("h", 0.0) * height,
    )


def build_interactive_diagram(db: Session, chunk_id: str) -> InteractiveDiagramManifest | None:
    """Returns None if the chunk isn't a diagram with an extracted graph and image."""
    chunk = db.get(Chunk, chunk_id)
    if not chunk or not chunk.extra or not chunk.extra.get("image_path"):
        return None

    image_path = Path(chunk.extra["image_path"])
    if not image_path.exists():
        return None

    document = db.get(Document, chunk.document_id)
    if not document:
        return None

    with Image.open(image_path) as img:
        width, height = img.size

    nodes = db.query(SchematicNode).filter(SchematicNode.chunk_id == chunk_id).all()
    node_ids = {n.id for n in nodes}
    edges = (
        db.query(SchematicEdge)
        .filter(
            SchematicEdge.document_id == chunk.document_id,
            SchematicEdge.from_node_id.in_(node_ids),
            SchematicEdge.to_node_id.in_(node_ids),
        )
        .all()
        if node_ids
        else []
    )

    component_ids = {n.component_id for n in nodes if n.component_id}
    component_names: dict[str, str] = {}
    if component_ids:
        for c in db.query(Component).filter(Component.id.in_(component_ids)).all():
            component_names[c.id] = c.name

    node_by_id = {n.id: n for n in nodes}

    return InteractiveDiagramManifest(
        chunk_id=chunk_id,
        document_id=document.id,
        document_title=document.title,
        page_number=chunk.page_number,
        image_width=width,
        image_height=height,
        image_url=f"/visualization/diagrams/{chunk_id}/image",
        nodes=[
            InteractiveNode(
                id=n.id,
                label=n.label,
                symbol_type=n.symbol_type.value,
                description=n.description,
                component_id=n.component_id,
                component_name=component_names.get(n.component_id) if n.component_id else None,
                bbox=_to_pixel_bbox(n.bbox, width, height),
            )
            for n in nodes
        ],
        edges=[
            InteractiveEdge(
                from_id=e.from_node_id,
                to_id=e.to_node_id,
                from_label=node_by_id[e.from_node_id].label,
                to_label=node_by_id[e.to_node_id].label,
                wire_type=e.wire_type.value,
                label=e.label,
            )
            for e in edges
        ],
    )
