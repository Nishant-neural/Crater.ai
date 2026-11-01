"""Pydantic shapes for LLM-vision structured schematic extraction (vision_extraction.py)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ExtractedPort(BaseModel):
    """A real terminal/pin on a schematic node, as printed on the diagram.

    This replaces the old synthetic "terminal" port: if the vision model
    cannot see a pin, it returns no port for it, rather than inventing one.
    """
    name: str
    bbox: dict[str, float] | None = None


class ExtractedNode(BaseModel):
    label: str                      # e.g. "K17", "X12"
    symbol_type: str                # relay | contactor | motor | sensor | switch | fuse |
                                     # terminal | connector | plc | transformer | power_source | ground | other
    description: str | None = None
    bbox: dict[str, float] | None = None   # {"x":0-1,"y":0-1,"w":0-1,"h":0-1}, normalized to image size
    part_number: str | None = None  # only when printed on the diagram itself
    ports: list[ExtractedPort] = Field(default_factory=list)


class ExtractedEdge(BaseModel):
    from_label: str
    to_label: str
    wire_type: str = "unknown"      # power | control | signal | ground | unknown
    label: str | None = None
    from_port: str | None = None   # pin/terminal label on the from node, when visible
    to_port: str | None = None


class SchematicExtractionResult(BaseModel):
    nodes: list[ExtractedNode] = Field(default_factory=list)
    edges: list[ExtractedEdge] = Field(default_factory=list)
    # Set when vision output could not be parsed at all, so the pipeline can
    # record an unresolved fact instead of silently treating it as "no nodes".
    parse_error: str | None = None
