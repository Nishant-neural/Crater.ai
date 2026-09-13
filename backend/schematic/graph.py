"""
Connection graph persistence + signal tracing (plan.md §5).

`trace_path` is deliberately a pure function over plain (from, to) label
pairs — no DB session, no SQLAlchemy objects — so it's trivial to unit
test and so `get_graph_for_document` is the only place that has to know
how the graph is actually stored.
"""
from __future__ import annotations

from collections import deque

from sqlalchemy.orm import Session

from backend.db.models import Component, SchematicEdge, SchematicNode, SymbolType, WireType
from backend.schematic.schema import SchematicExtractionResult


def persist_schematic(
    db: Session,
    document_id: str,
    chunk_id: str,
    revision_id: str | None,
    result: SchematicExtractionResult,
) -> list[SchematicNode]:
    """
    Write extracted nodes/edges for one diagram, linking each node to an
    existing Phase-1 Component row when the label matches exactly (case-
    insensitive). Exact-match only, deliberately: a fuzzy match risks
    silently linking the wrong physical part, which is worse than no link.
    """
    label_to_node_id: dict[str, str] = {}
    nodes: list[SchematicNode] = []

    known_components: dict[str, str] = {}
    if revision_id:
        for c in db.query(Component).filter(Component.revision_id == revision_id).all():
            known_components[c.name.strip().lower()] = c.id

    for extracted in result.nodes:
        try:
            symbol_type = SymbolType(extracted.symbol_type)
        except ValueError:
            symbol_type = SymbolType.other

        node = SchematicNode(
            document_id=document_id,
            chunk_id=chunk_id,
            component_id=known_components.get(extracted.label.strip().lower()),
            label=extracted.label,
            symbol_type=symbol_type,
            description=extracted.description,
            bbox=extracted.bbox,
        )
        db.add(node)
        db.flush()
        label_to_node_id[extracted.label] = node.id
        nodes.append(node)

    for extracted_edge in result.edges:
        from_id = label_to_node_id.get(extracted_edge.from_label)
        to_id = label_to_node_id.get(extracted_edge.to_label)
        if not (from_id and to_id):
            # Edge references a label not extracted as a node in this same
            # image (e.g. a component visible on an adjacent, un-ingested
            # page). Skip rather than fabricate a dangling node.
            continue
        try:
            wire_type = WireType(extracted_edge.wire_type)
        except ValueError:
            wire_type = WireType.unknown

        db.add(
            SchematicEdge(
                document_id=document_id,
                from_node_id=from_id,
                to_node_id=to_id,
                wire_type=wire_type,
                label=extracted_edge.label,
            )
        )

    db.commit()
    return nodes


def trace_path(edges: list[tuple[str, str]], start: str, end: str) -> list[str] | None:
    """
    Shortest path (by hop count) between two node labels, treating edges
    as undirected (signal/power paths are traceable in either direction
    for diagnostic purposes even though a wire has a "from"/"to" in the
    data model). Returns the label sequence, or None if unreachable.
    """
    if start == end:
        return [start]

    adjacency: dict[str, set[str]] = {}
    for a, b in edges:
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set()).add(a)

    if start not in adjacency:
        return None

    visited = {start}
    queue: deque[list[str]] = deque([[start]])
    while queue:
        path = queue.popleft()
        node = path[-1]
        for neighbor in adjacency.get(node, ()):
            if neighbor in visited:
                continue
            new_path = path + [neighbor]
            if neighbor == end:
                return new_path
            visited.add(neighbor)
            queue.append(new_path)

    return None


def get_graph_for_document(db: Session, document_id: str) -> tuple[list[SchematicNode], list[SchematicEdge]]:
    nodes = db.query(SchematicNode).filter(SchematicNode.document_id == document_id).all()
    edges = db.query(SchematicEdge).filter(SchematicEdge.document_id == document_id).all()
    return nodes, edges


def trace_path_in_document(db: Session, document_id: str, start_label: str, end_label: str) -> list[str] | None:
    _, edges = get_graph_for_document(db, document_id)
    node_id_to_label = {
        n.id: n.label for n in db.query(SchematicNode).filter(SchematicNode.document_id == document_id).all()
    }
    label_edges = [(node_id_to_label[e.from_node_id], node_id_to_label[e.to_node_id]) for e in edges]
    return trace_path(label_edges, start_label, end_label)
