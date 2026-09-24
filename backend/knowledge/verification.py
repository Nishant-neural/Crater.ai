"""Deterministic topology and spatial-coherence checks for a machine model."""
from __future__ import annotations

from dataclasses import dataclass
import ast
import math
from typing import Any

from backend.knowledge.machine_model import UniversalMachineModel

@dataclass
class VerificationIssue:
    category: str
    severity: str
    message: str
    entity_ids: list[str]


def _bbox(value: Any) -> tuple[float, float, float, float] | None:
    if isinstance(value, dict):
        raw = value
    elif isinstance(value, str):
        try: raw = ast.literal_eval(value)
        except Exception: return None
    else: return None
    try:
        x, y, w, h = (float(raw[k]) for k in ("x", "y", "w", "h"))
    except Exception:
        return None
    return x, y, w, h


def verify_machine_model(model: UniversalMachineModel) -> list[VerificationIssue]:
    issues: list[VerificationIssue] = []
    entities = model.entity_map()
    names: dict[str, str] = {}
    for e in model.entities:
        key = e.name.strip().lower()
        if key in names and names[key] != e.id:
            issues.append(VerificationIssue("topology", "error", f"duplicate entity identity: {e.name}", [names[key], e.id]))
        names[key] = e.id

        bbox = _bbox(e.properties.get("bbox"))
        if bbox is not None:
            x, y, w, h = bbox
            if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > 1 or y + h > 1:
                issues.append(VerificationIssue("spatial", "error", f"invalid/out-of-page bbox for {e.name}", [e.id]))

    adjacency: dict[str, set[str]] = {e.id: set() for e in model.entities}
    for r in model.relations:
        if r.subject_id not in entities or r.object_id not in entities:
            missing = [x for x in (r.subject_id, r.object_id) if x not in entities]
            issues.append(VerificationIssue("topology", "error", f"relation references missing entity: {missing}", missing))
            continue
        if r.subject_id == r.object_id:
            issues.append(VerificationIssue("topology", "warning", f"self-relation: {r.subject_name} --{r.relation_type}--> itself", [r.subject_id]))
        adjacency[r.subject_id].add(r.object_id)
        adjacency[r.object_id].add(r.subject_id)

    for p in model.ports:
        if p.entity_id not in entities:
            issues.append(VerificationIssue("topology", "error", f"port {p.id} references missing entity {p.entity_id}", [p.entity_id]))

    for s in model.states:
        if s.entity_id not in entities:
            issues.append(VerificationIssue("topology", "error", f"state references missing entity {s.entity_id}", [s.entity_id]))

    # A disconnected graph is not automatically invalid: independent subsystems are legitimate.
    # Flag it so an integration review can inspect whether the separation is intentional.
    if len(entities) > 1:
        unseen = set(entities)
        components = 0
        while unseen:
            components += 1
            stack = [unseen.pop()]
            while stack:
                node = stack.pop()
                for nxt in adjacency.get(node, ()):
                    if nxt in unseen:
                        unseen.remove(nxt); stack.append(nxt)
        if components > 1:
            issues.append(VerificationIssue("topology", "warning", f"machine graph has {components} disconnected components", []))

    # Spatial claims must have usable geometry when supplied by schematic extraction.
    spatial_entities = [e for e in model.entities if _bbox(e.properties.get("bbox")) is not None]
    if spatial_entities:
        for r in model.relations:
            a, b = entities.get(r.subject_id), entities.get(r.object_id)
            if not a or not b: continue
            ba, bb = _bbox(a.properties.get("bbox")), _bbox(b.properties.get("bbox"))
            if ba and bb:
                ax, ay, aw, ah = ba; bx, by, bw, bh = bb
                distance = math.hypot((ax + aw/2) - (bx + bw/2), (ay + ah/2) - (by + bh/2))
                if r.relation_type in {"connected_to", "adjacent_to"} and distance > 0.8:
                    issues.append(VerificationIssue("spatial", "warning", f"spatially distant connected entities: {a.name} and {b.name}", [a.id, b.id]))

    return issues
