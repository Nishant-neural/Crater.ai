"""
Phase 4 — Technical Visualization endpoints (plan.md §33/§9).

Re-projects Phase 1 (Component) and Phase 3 (SchematicNode/Edge) data
into shapes built for rendering: an interactive diagram manifest, a
component explorer, and a steppable/"animatable" procedure visualization.
No new source-of-truth tables — everything here is computed on read.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from backend.db.models import Chunk, Revision, SchematicNode
from backend.db.session import get_session
from backend.schematic.highlight import highlight_nodes
from backend.visualization.component_explorer import build_component_explorer
from backend.visualization.interactive_diagram import build_interactive_diagram
from backend.visualization.procedure_viz import build_procedure_visualization, get_frame

router = APIRouter(prefix="/visualization", tags=["visualization"])

_FRAME_DIR = Path(tempfile.gettempdir()) / "crater_visualization_frames"


@router.get("/revisions/{revision_id}/components")
def get_component_explorer(revision_id: str, session: Session = Depends(get_session)):
    revision = session.get(Revision, revision_id)
    if not revision:
        raise HTTPException(404, "Revision not found")
    return build_component_explorer(session, revision_id)


@router.get("/diagrams/{chunk_id}")
def get_interactive_diagram(chunk_id: str, session: Session = Depends(get_session)):
    manifest = build_interactive_diagram(session, chunk_id)
    if not manifest:
        raise HTTPException(404, "No interactive diagram available for this chunk")
    return manifest


@router.get("/diagrams/{chunk_id}/image")
def get_diagram_image(chunk_id: str, session: Session = Depends(get_session)):
    chunk = session.get(Chunk, chunk_id)
    if not chunk or not chunk.extra or not chunk.extra.get("image_path"):
        raise HTTPException(404, "No source image found for this chunk")
    image_path = Path(chunk.extra["image_path"])
    if not image_path.exists():
        raise HTTPException(404, "Source image file is missing on disk")
    return FileResponse(image_path)


@router.get("/procedures/{procedure_id}")
def get_procedure_visualization(procedure_id: str, session: Session = Depends(get_session)):
    viz = build_procedure_visualization(session, procedure_id)
    if not viz:
        raise HTTPException(404, "Procedure not found")
    return viz


@router.get("/procedures/{procedure_id}/frames/{frame_index}")
def get_procedure_frame(procedure_id: str, frame_index: int, session: Session = Depends(get_session)):
    frame = get_frame(session, procedure_id, frame_index)
    if not frame:
        raise HTTPException(404, "No such animation frame for this procedure")

    chunk = session.get(Chunk, frame.chunk_id)
    if not chunk or not chunk.extra or not chunk.extra.get("image_path"):
        raise HTTPException(404, "Source diagram image not found for this frame")

    nodes = (
        session.query(SchematicNode)
        .filter(SchematicNode.chunk_id == frame.chunk_id, SchematicNode.label.in_(frame.labels))
        .all()
    )

    out_path = _FRAME_DIR / f"{procedure_id}_{frame_index}.png"
    highlight_nodes(chunk.extra["image_path"], nodes, out_path)
    return FileResponse(out_path, media_type="image/png")
