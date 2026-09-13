"""
Schematic graph endpoints (plan.md §5): inspect the extracted component
graph for a document, trace a signal/power path between two labeled
nodes, and get a highlighted image for a given node.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from crater.db.models import Chunk, SchematicNode
from crater.db.session import get_session
from crater.schematic.graph import get_graph_for_document, trace_path_in_document
from crater.schematic.highlight import highlight_nodes

router = APIRouter(prefix="/schematics", tags=["schematics"])

_HIGHLIGHT_DIR = Path(tempfile.gettempdir()) / "crater_highlights"


@router.get("/{document_id}/graph")
def get_graph(document_id: str, session: Session = Depends(get_session)):
    nodes, edges = get_graph_for_document(session, document_id)
    if not nodes and not edges:
        raise HTTPException(404, "No schematic graph found for this document")

    node_by_id = {n.id: n for n in nodes}
    return {
        "nodes": [
            {
                "id": n.id,
                "label": n.label,
                "symbol_type": n.symbol_type.value,
                "description": n.description,
                "bbox": n.bbox,
                "component_id": n.component_id,
            }
            for n in nodes
        ],
        "edges": [
            {
                "from_label": node_by_id[e.from_node_id].label,
                "to_label": node_by_id[e.to_node_id].label,
                "wire_type": e.wire_type.value,
                "label": e.label,
            }
            for e in edges
            if e.from_node_id in node_by_id and e.to_node_id in node_by_id
        ],
    }


@router.get("/{document_id}/trace")
def trace(document_id: str, from_label: str, to_label: str, session: Session = Depends(get_session)):
    path = trace_path_in_document(session, document_id, from_label, to_label)
    if path is None:
        return {"path": None, "message": f"No connection found between {from_label!r} and {to_label!r}"}
    return {"path": path}


@router.get("/{document_id}/highlight")
def highlight(document_id: str, label: str, session: Session = Depends(get_session)):
    node = (
        session.query(SchematicNode)
        .filter(SchematicNode.document_id == document_id, SchematicNode.label == label)
        .first()
    )
    if not node or not node.bbox:
        raise HTTPException(404, f"No located node {label!r} found for this document")

    chunk = session.get(Chunk, node.chunk_id)
    if not chunk or not chunk.extra or not chunk.extra.get("image_path"):
        raise HTTPException(404, "Source diagram image not found for this node")

    out_path = _HIGHLIGHT_DIR / f"{document_id}_{label}.png"
    highlight_nodes(chunk.extra["image_path"], [node], out_path)
    return FileResponse(out_path, media_type="image/png")
