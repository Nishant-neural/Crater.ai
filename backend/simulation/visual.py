"""Visual twin: a deterministic, render-ready layout of a compiled twin.

The layout is computed on the backend so every client renders the same
picture: nodes are the twin's components, edges are the canonical
relations the compiler already carries in ``metadata["topology"]``, and
each node carries its current state. Nothing is inferred -- a relation
says "connected", never "fault propagates", and a component with no
relations is shown unconnected in the last layer, not hidden.

Trace playback is a frontend concern: an experiment/verification result
already carries ``baseline_state`` / ``fault_state`` / ``final_state``
plus both traces, so the UI steps through those three states against
this layout. The endpoint only serves the layout itself.
"""
from __future__ import annotations

from typing import Any

from backend.simulation.schema import DigitalTwinDefinition

_NODE_W, _NODE_H = 240, 84
_LAYER_X, _ROW_Y = 280, 120
_PAD = 24


def layout_twin(definition: DigitalTwinDefinition, state: dict[str, Any] | None) -> dict[str, Any]:
    """Return nodes/edges/transitions for rendering, with deterministic positions."""
    state = state or {}
    live = state.get("components") or {}

    components = list(definition.components)
    by_id = {c.id: c for c in components}

    # Edges from the documented topology only; drop any relation whose
    # endpoint is not a twin component rather than inventing a node for it.
    topology = (definition.metadata or {}).get("topology") or []
    edges: list[dict[str, Any]] = []
    incoming: dict[str, int] = {c.id: 0 for c in components}
    outgoing: dict[str, list[str]] = {c.id: [] for c in components}
    for rel in topology:
        if not isinstance(rel, dict):
            continue
        src, dst = rel.get("subject_id"), rel.get("object_id")
        if src not in by_id or dst not in by_id or src == dst:
            continue
        edges.append({
            "from": src, "to": dst,
            "relation_type": rel.get("relation_type") or "connected_to",
            "label": rel.get("relation_type") or "connected_to",
        })
        incoming[dst] += 1
        outgoing[src].append(dst)

    # Layered layout: BFS from roots (no incoming relation, with outgoing
    # edges), alphabetical within a layer. Nodes unreachable from any root
    # -- cycles, and components with no relations at all -- are appended
    # in name order, so nothing is ever dropped from the picture and the
    # connected machinery reads left to right.
    layers: list[list[str]] = []
    assigned: dict[str, int] = {}
    frontier = sorted(
        [c.id for c in components if incoming[c.id] == 0 and outgoing[c.id]],
        key=lambda i: by_id[i].name)
    while frontier:
        layers.append(frontier)
        for node_id in frontier:
            assigned[node_id] = len(layers) - 1
        nxt: set[str] = set()
        for node_id in frontier:
            for dst in outgoing[node_id]:
                if dst not in assigned and dst not in frontier:
                    nxt.add(dst)
        frontier = sorted(nxt, key=lambda i: by_id[i].name)
    unassigned = sorted([c.id for c in components if c.id not in assigned], key=lambda i: by_id[i].name)
    if unassigned:
        layers.append(unassigned)
        for node_id in unassigned:
            assigned[node_id] = len(layers) - 1

    nodes: list[dict[str, Any]] = []
    for layer_idx, layer in enumerate(layers):
        for row_idx, node_id in enumerate(layer):
            comp = by_id[node_id]
            current = live.get(node_id) or {}
            nodes.append({
                "id": comp.id, "name": comp.name, "type": comp.component_type,
                "state": dict(current) if isinstance(current, dict) else {},
                "initial_state": dict(comp.initial_state),
                "x": _PAD + layer_idx * _LAYER_X,
                "y": _PAD + row_idx * _ROW_Y,
                "width": _NODE_W, "height": _NODE_H,
                "layer": layer_idx,
            })

    width = _PAD * 2 + (len(layers) - 1) * _LAYER_X + _NODE_W if layers else _PAD * 2 + _NODE_W
    height = _PAD * 2 + (max((len(layer) for layer in layers), default=1) - 1) * _ROW_Y + _NODE_H

    return {
        "nodes": nodes,
        "edges": edges,
        "transitions": [
            {"id": t.id, "name": t.name, "conditions": t.conditions,
             "effects": t.effects, "description": t.description,
             "source_ids": t.source_ids, "evidence": t.evidence}
            for t in definition.transitions
        ],
        "signals": dict(definition.initial_signals),
        "warnings": list((definition.metadata or {}).get("compiler_warnings") or []),
        "expert_seed": (definition.metadata or {}).get("expert_seed"),
        "canvas": {"width": width, "height": height},
    }
