"""Pydantic shapes for LLM-vision structured schematic extraction (vision_extraction.py)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ExtractedNode(BaseModel):
    label: str                      # e.g. "K17", "X12"
    symbol_type: str                # relay | contactor | motor | sensor | switch | fuse |
                                     # terminal | connector | plc | transformer | power_source | ground | other
    description: str | None = None
    bbox: dict[str, float] | None = None   # {"x":0-1,"y":0-1,"w":0-1,"h":0-1}, normalized to image size


class ExtractedEdge(BaseModel):
    from_label: str
    to_label: str
    wire_type: str = "unknown"      # power | control | signal | ground | unknown
    label: str | None = None


class SchematicExtractionResult(BaseModel):
    nodes: list[ExtractedNode] = Field(default_factory=list)
    edges: list[ExtractedEdge] = Field(default_factory=list)
