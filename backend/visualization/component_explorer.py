"""
Component explorer (plan.md Phase 4: "Component explorer").

Joins Phase 1's structured Component/ComponentRelationship knowledge with
Phase 3's SchematicNode locations, so a technician can browse "what is
this part, where does it physically/visually show up, and what's it
connected to" without hand-correlating three tables themselves.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from backend.db.models import (
    Component,
    ComponentRelationship,
    Document,
    SchematicNode,
)
from backend.visualization.schema import (
    ComponentAppearance,
    ComponentExplorer,
    ComponentExplorerEntry,
    PixelBBox,
    RelatedComponentRef,
)


def _bbox(node: SchematicNode) -> PixelBBox | None:
    # Explorer bboxes stay normalized->None here; the frontend fetches pixel
    # geometry from the diagram manifest (interactive_diagram.py) when the
    # technician actually opens an appearance — the explorer list itself
    # only needs to say "this component is on these diagrams".
    return None


def build_component_explorer(db: Session, revision_id: str) -> ComponentExplorer:
    components = db.query(Component).filter(Component.revision_id == revision_id).all()
    if not components:
        return ComponentExplorer(revision_id=revision_id, components=[])

    component_ids = {c.id for c in components}
    component_by_id = {c.id: c for c in components}

    nodes = db.query(SchematicNode).filter(SchematicNode.component_id.in_(component_ids)).all()
    doc_ids = {n.document_id for n in nodes}
    documents = {d.id: d for d in db.query(Document).filter(Document.id.in_(doc_ids)).all()} if doc_ids else {}

    appearances_by_component: dict[str, list[ComponentAppearance]] = {}
    for n in nodes:
        doc = documents.get(n.document_id)
        if not doc:
            continue
        appearances_by_component.setdefault(n.component_id, []).append(
            ComponentAppearance(
                chunk_id=n.chunk_id,
                document_id=n.document_id,
                document_title=doc.title,
                label=n.label,
                diagram_url=f"/visualization/diagrams/{n.chunk_id}",
                bbox=_bbox(n),
            )
        )

    relationships = (
        db.query(ComponentRelationship)
        .filter(
            (ComponentRelationship.from_component_id.in_(component_ids))
            | (ComponentRelationship.to_component_id.in_(component_ids))
        )
        .all()
    )
    related_by_component: dict[str, list[RelatedComponentRef]] = {}
    for r in relationships:
        if r.from_component_id in component_ids and r.to_component_id in component_by_id:
            related_by_component.setdefault(r.from_component_id, []).append(
                RelatedComponentRef(
                    component_id=r.to_component_id,
                    name=component_by_id[r.to_component_id].name,
                    relation_type=r.relation_type.value,
                    direction="outgoing",
                    description=r.description,
                )
            )
        if r.to_component_id in component_ids and r.from_component_id in component_by_id:
            related_by_component.setdefault(r.to_component_id, []).append(
                RelatedComponentRef(
                    component_id=r.from_component_id,
                    name=component_by_id[r.from_component_id].name,
                    relation_type=r.relation_type.value,
                    direction="incoming",
                    description=r.description,
                )
            )

    entries = [
        ComponentExplorerEntry(
            id=c.id,
            name=c.name,
            function=c.function,
            location_description=c.location_description,
            part_number=c.part_number,
            appearances=appearances_by_component.get(c.id, []),
            related_components=related_by_component.get(c.id, []),
        )
        for c in components
    ]
    return ComponentExplorer(revision_id=revision_id, components=entries)
