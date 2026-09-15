"""
Procedure visualization + repair "animation" (plan.md Phase 4: "Procedure
visualization", "Repair animations").

Honest scope (see docs/Phase4.md): there's no CAD/3D pipeline yet (that's
Phase 10), so a "repair animation" here is an ordered sequence of
annotated-diagram frames — one per procedure step that mentions a
component we can locate on a schematic — that a frontend steps/plays
through, not rendered video. That's still the useful thing: "step 3
mentions the K17 relay; here's exactly where it is."

Component matching is deliberately simple (case-insensitive substring of
the component's name in the step text), same trade-off Phase 3 made for
label matching in schematic/graph.py: a fuzzy/LLM-assisted matcher is a
reasonable upgrade once real manuals show how often names are phrased
differently in procedure text vs. the component table.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from backend.db.models import Component, Procedure, SchematicNode
from backend.visualization.schema import (
    HighlightFrame,
    ProcedureStepVisualization,
    ProcedureVisualization,
)


def _find_referenced_components(components: list[Component], step_text: str) -> list[Component]:
    text_lower = step_text.lower()
    return [c for c in components if c.name.strip().lower() in text_lower]


def _nodes_by_chunk(nodes: list[SchematicNode]) -> dict[str, list[SchematicNode]]:
    grouped: dict[str, list[SchematicNode]] = {}
    for n in nodes:
        grouped.setdefault(n.chunk_id, []).append(n)
    return grouped


def build_procedure_visualization(db: Session, procedure_id: str) -> ProcedureVisualization | None:
    procedure = db.get(Procedure, procedure_id)
    if not procedure:
        return None

    components = db.query(Component).filter(Component.revision_id == procedure.revision_id).all()
    component_ids = {c.id for c in components}
    all_located_nodes = (
        db.query(SchematicNode).filter(SchematicNode.component_id.in_(component_ids)).all()
        if component_ids
        else []
    )
    nodes_by_component: dict[str, list[SchematicNode]] = {}
    for n in all_located_nodes:
        nodes_by_component.setdefault(n.component_id, []).append(n)

    steps: list[ProcedureStepVisualization] = []
    animation_frames: list[HighlightFrame] = []
    frame_index = 0

    for step_index, instruction in enumerate(procedure.steps):
        referenced = _find_referenced_components(components, instruction)
        step_nodes = [n for c in referenced for n in nodes_by_component.get(c.id, [])]
        grouped = _nodes_by_chunk(step_nodes)

        step_frames: list[HighlightFrame] = []
        for chunk_id, chunk_nodes in grouped.items():
            frame = HighlightFrame(
                frame_index=frame_index,
                step_index=step_index,
                chunk_id=chunk_id,
                document_id=chunk_nodes[0].document_id,
                labels=[n.label for n in chunk_nodes],
                frame_url=f"/visualization/procedures/{procedure_id}/frames/{frame_index}",
            )
            step_frames.append(frame)
            animation_frames.append(frame)
            frame_index += 1

        steps.append(
            ProcedureStepVisualization(
                step_index=step_index,
                instruction=instruction,
                referenced_components=[c.name for c in referenced],
                frames=step_frames,
            )
        )

    return ProcedureVisualization(
        procedure_id=procedure.id,
        name=procedure.name,
        procedure_type=procedure.procedure_type.value,
        revision_id=procedure.revision_id,
        steps=steps,
        animation_frames=animation_frames,
    )


def get_frame(db: Session, procedure_id: str, frame_index: int) -> HighlightFrame | None:
    """Re-derive the visualization and pick one frame out of it.

    Recomputing on every frame request (rather than persisting frames) is
    deliberate — procedure text and the schematic graph are the source of
    truth, so there's nothing to keep in sync, and this workload (a handful
    of procedure steps) is cheap enough that caching would be premature.
    """
    viz = build_procedure_visualization(db, procedure_id)
    if not viz or frame_index < 0 or frame_index >= len(viz.animation_frames):
        return None
    return viz.animation_frames[frame_index]
