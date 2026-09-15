"""
Pydantic shapes for Phase 4 (Technical Visualization, plan.md §33/§9).

These are pure response/DTO models — no persistence of their own. Phase 4
doesn't introduce new source-of-truth tables; it *re-projects* Phase 1
(Component/Procedure), Phase 3 (SchematicNode/SchematicEdge) data into
shapes a frontend can render directly (pixel-space diagrams, browsable
component trees, steppable/animatable procedures) without re-deriving
that logic client-side.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class PixelBBox(BaseModel):
    """A bbox in actual image pixels (normalized 0-1 bbox * image dimensions)."""

    x: float
    y: float
    w: float
    h: float


class InteractiveNode(BaseModel):
    id: str
    label: str
    symbol_type: str
    description: str | None = None
    component_id: str | None = None
    component_name: str | None = None
    bbox: PixelBBox | None = None


class InteractiveEdge(BaseModel):
    from_id: str
    to_id: str
    from_label: str
    to_label: str
    wire_type: str
    label: str | None = None


class InteractiveDiagramManifest(BaseModel):
    """Everything a frontend needs to render one diagram as a pannable/zoomable,
    clickable canvas (plan.md Phase 4 'Interactive diagrams' + 'Annotated schematics')."""

    chunk_id: str
    document_id: str
    document_title: str
    page_number: int | None = None
    image_width: int
    image_height: int
    image_url: str
    nodes: list[InteractiveNode] = Field(default_factory=list)
    edges: list[InteractiveEdge] = Field(default_factory=list)


class ComponentAppearance(BaseModel):
    """One place a component is visually located in a schematic."""

    chunk_id: str
    document_id: str
    document_title: str
    label: str
    diagram_url: str            # the annotated-diagram endpoint for this chunk
    bbox: PixelBBox | None = None


class RelatedComponentRef(BaseModel):
    component_id: str
    name: str
    relation_type: str
    direction: str               # "outgoing" | "incoming"
    description: str | None = None


class ComponentExplorerEntry(BaseModel):
    """One row of the Phase 4 'Component explorer' (plan.md §9)."""

    id: str
    name: str
    function: str | None = None
    location_description: str | None = None
    part_number: str | None = None
    appearances: list[ComponentAppearance] = Field(default_factory=list)
    related_components: list[RelatedComponentRef] = Field(default_factory=list)


class ComponentExplorer(BaseModel):
    revision_id: str
    components: list[ComponentExplorerEntry] = Field(default_factory=list)


class HighlightFrame(BaseModel):
    """One frame of a repair 'animation' — an annotated diagram image with the
    components relevant to one procedure step highlighted. plan.md Phase 4 scopes
    'Repair animations' as a viewable sequence of these, not rendered video/3D —
    see docs/Phase4.md 'Honest limits'."""

    frame_index: int
    step_index: int
    chunk_id: str
    document_id: str
    labels: list[str] = Field(default_factory=list)
    frame_url: str


class ProcedureStepVisualization(BaseModel):
    step_index: int
    instruction: str
    referenced_components: list[str] = Field(default_factory=list)
    frames: list[HighlightFrame] = Field(default_factory=list)


class ProcedureVisualization(BaseModel):
    procedure_id: str
    name: str
    procedure_type: str
    revision_id: str
    steps: list[ProcedureStepVisualization] = Field(default_factory=list)
    animation_frames: list[HighlightFrame] = Field(default_factory=list)
