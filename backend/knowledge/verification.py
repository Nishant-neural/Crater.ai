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



# Deliberately broad vocabulary: the universal model remains extensible, but
# unfamiliar types/relations should be visible during review instead of being
# silently accepted as if they were established ontology terms.
KNOWN_ENTITY_TYPES = {
    "component", "machine", "assembly", "subsystem", "module", "pump", "motor",
    "valve", "sensor", "controller", "plc", "hmi", "actuator", "cylinder",
    "relay", "switch", "transformer", "inverter", "drive", "power_supply",
    "terminal", "connector", "cable", "wire", "fuse", "breaker", "bearing",
    "shaft", "gearbox", "fan", "filter", "tank", "reservoir", "pipe", "hose",
    "fitting", "failure_mode", "procedure", "instrument", "network", "port",
}
KNOWN_RELATION_TYPES = {
    "connected_to", "adjacent_to", "contains", "part_of", "electrical",
    "mechanical", "fluid", "signal", "drives", "driven_by", "controls",
    "controlled_by", "powers", "powered_by", "measures", "measured_by",
    "feeds", "fed_by", "supplied_by", "mounted_on", "has_port", "monitors",
    "actuates", "switches", "grounded_to", "communicates_with", "routes_to",
}

def verify_machine_model(model: UniversalMachineModel) -> list[VerificationIssue]:
    issues: list[VerificationIssue] = []
    entities = model.entity_map()
    names: dict[str, str] = {}
    for e in model.entities:
        entity_type = e.entity_type.strip().lower()
        if entity_type and entity_type not in KNOWN_ENTITY_TYPES:
            issues.append(VerificationIssue(
                "ontology", "warning",
                f"unknown entity type '{e.entity_type}' for {e.name}; accepted as extensible vocabulary",
                [e.id],
            ))
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
        relation_type = r.relation_type.strip().lower()
        if relation_type and relation_type not in KNOWN_RELATION_TYPES:
            issues.append(VerificationIssue(
                "ontology", "warning",
                f"unknown relation type '{r.relation_type}' between {r.subject_name} and {r.object_name}; accepted as extensible vocabulary",
                [x for x in (r.subject_id, r.object_id) if x],
            ))
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
