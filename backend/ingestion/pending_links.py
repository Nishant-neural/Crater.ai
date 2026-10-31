"""Pending-link resolution: facts independent of chunks.

Cross-chunk relationships and cross-page schematic edges are queued as
open UnresolvedFact rows during extraction (see component_extraction and
schematic/graph), instead of being silently dropped for not being in the
same chunk/image. This module resolves them AFTER all chunks/diagrams for
a document are extracted, against the full revision / document index.

No deterministic entity register is used, and no match is guessed:
- A pending link resolves only when BOTH endpoints match exactly one
  component / node in the index (soft name key: lowercase, punctuation
  collapsed). Ambiguous or still-missing endpoints stay open.
- Anything left open stays in the unresolved ledger with its original
  payload, so a human or a later pass can resurrect it. It is never
  silently deleted, and never forced into a wrong identity.
"""
from __future__ import annotations

import re
from sqlalchemy.orm import Session

from backend.db.models import (
    Component,
    ComponentRelationship,
    RelationType,
    SchematicEdge,
    SchematicNode,
    UnresolvedFact,
    WireType,
)


def norm_name(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


def resolve_pending_relationships(session: Session, revision_id: str) -> tuple[int, int]:
    components = session.query(Component).filter(Component.revision_id == revision_id).all()
    by_name: dict[str, list] = {}
    for comp in components:
        for key in {norm_name(comp.name), norm_name(getattr(comp, "part_number", None))}:
            if key:
                by_name.setdefault(key, []).append(comp)
    pending = session.query(UnresolvedFact).filter(
        UnresolvedFact.revision_id == revision_id,
        UnresolvedFact.fact_type == "pending_relationship",
        UnresolvedFact.status == "open",
    ).all()
    resolved = 0
    for row in pending:
        payload = row.payload or {}
        from_cands = by_name.get(norm_name(payload.get("from_component")), [])
        to_cands = by_name.get(norm_name(payload.get("to_component")), [])
        if len(from_cands) != 1 or len(to_cands) != 1:
            continue
        try:
            rel_type = RelationType(payload.get("relation_type"))
        except Exception:
            continue
        from_id, to_id = from_cands[0].id, to_cands[0].id
        exists = session.query(ComponentRelationship).filter(
            ComponentRelationship.from_component_id == from_id,
            ComponentRelationship.to_component_id == to_id,
            ComponentRelationship.relation_type == rel_type,
        ).first()
        if not exists:
            session.add(ComponentRelationship(
                from_component_id=from_id, to_component_id=to_id,
                relation_type=rel_type, description=payload.get("description"),
                source_chunk_id=payload.get("source_chunk_id"),
            ))
        row.status = "resolved"
        resolved += 1
    session.commit()
    still_open = session.query(UnresolvedFact).filter(
        UnresolvedFact.revision_id == revision_id,
        UnresolvedFact.fact_type == "pending_relationship",
        UnresolvedFact.status == "open",
    ).count()
    return resolved, still_open


def resolve_pending_schematic_edges(session: Session, document_id: str, revision_id: str) -> tuple[int, int]:
    nodes = session.query(SchematicNode).filter(SchematicNode.document_id == document_id).all()
    by_label: dict[str, list] = {}
    for node in nodes:
        by_label.setdefault(norm_name(node.label), []).append(node)
    pending = session.query(UnresolvedFact).filter(
        UnresolvedFact.revision_id == revision_id,
        UnresolvedFact.fact_type == "pending_schematic_edge",
        UnresolvedFact.status == "open",
    ).all()
    resolved = 0
    still_open = 0
    for row in pending:
        payload = row.payload or {}
        if payload.get("document_id") != document_id:
            continue
        from_cands = by_label.get(norm_name(payload.get("from_label")), [])
        to_cands = by_label.get(norm_name(payload.get("to_label")), [])
        if len(from_cands) != 1 or len(to_cands) != 1:
            still_open += 1
            continue
        from_id, to_id = from_cands[0].id, to_cands[0].id
        try:
            wire_type = WireType(payload.get("wire_type"))
        except Exception:
            wire_type = WireType.unknown
        exists = session.query(SchematicEdge).filter(
            SchematicEdge.document_id == document_id,
            SchematicEdge.from_node_id == from_id,
            SchematicEdge.to_node_id == to_id,
        ).first()
        if not exists:
            session.add(SchematicEdge(
                document_id=document_id, from_node_id=from_id, to_node_id=to_id,
                wire_type=wire_type, label=payload.get("label"),
            ))
        row.status = "resolved"
        resolved += 1
    session.commit()
    return resolved, still_open
