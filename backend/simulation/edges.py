"""Human-confirmed topology edges (design v3, Amendments Fix 2) and the
link transitions the compiler emits from them.

An edge is a reviewed record (``omnipro_edge_list.json``): source
component + port, target component + port, a value mapping, and its
manual sources. The compiler emits ONE transition per (source value)
for each edge — a link copies a value along a declared edge; it never
invents an edge, a port, or a value. A fault override on an edge (3B:
the feeder->gun link, where a faulted gun passes no wire) is part of
the reviewed edge record, and is retired when the target's archetype
rows take over the path (3C: A8 conductor rows for the gun).

Value mappings are data, reviewed with the edge: ``identity`` copies the
value unchanged; ``material_to_tip`` / ``material_to_arc`` are threshold
mappings (only a normal feeder output means wire at the tip / an arc —
p42 case 1, every cause reads wire_out@tip=absent); ``switch_to_enable``
maps electrical normal to signal high (v3 §3 signal domain).

Link transitions are total over the mapping's source values, and the
fault-override row is mutually exclusive with the nominal rows (it
conditions on the target fault, the nominal rows on fault == "none").
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.simulation.archetypes import QUALITATIVE_DOMAINS
from backend.simulation.schema import TwinTransition

_DEFAULT_PATH = Path(__file__).with_name("omnipro_edge_list.json")


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def load_edge_list(path: str | Path | None = None) -> dict[str, Any]:
    """Load the reviewed edge list JSON. A missing or malformed file
    raises, loudly — an edge list is a reviewed artifact, never a
    default."""
    with open(path or _DEFAULT_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def _resolve_component(name: str, components: list[Any]) -> tuple[Any | None, str]:
    candidates = [c for c in components if _norm(c.name) == _norm(name)]
    if len(candidates) == 1:
        return candidates[0], ""
    if not candidates:
        return None, f"no component named {name!r} in this twin"
    return None, f"{len(candidates)} components named {name!r} in this twin; ambiguous"


def _port_domain(component: Any, port_name: str, bindings: dict[str, Any]) -> str | None:
    """The domain of one port on a component: typed ports first, then the
    bound archetype's port declaration. None when the port is not declared
    anywhere — the link is then quarantined, never guessed."""
    for port in (getattr(component, "typed_ports", None) or []):
        name = port.name if hasattr(port, "name") else port.get("name")
        if _norm(name) == _norm(port_name):
            domain = port.domain if hasattr(port, "domain") else port.get("domain")
            return domain
    record = bindings.get(component.id) or {}
    archetype = record.get("archetype")
    if archetype is not None:
        for port in archetype.ports:
            if _norm(port.name) == _norm(port_name):
                return port.domain
    return None


def expand_link_transitions(edge_list: dict[str, Any], components: list[Any],
                            bindings: dict[str, Any]) -> tuple[list[TwinTransition], list[dict[str, Any]]]:
    """Emit link transitions for the reviewed edges that resolve in this twin.

    Returns (transitions, records). A record per edge says whether the
    link was emitted, quarantined (and why), or skipped (component
    missing/ambiguous). Nothing is invented: an unresolvable edge
    produces no transition and a record, not a guess.
    """
    value_maps = edge_list.get("value_maps") or {}
    transitions: list[TwinTransition] = []
    records: list[dict[str, Any]] = []
    for edge in edge_list.get("edges") or []:
        edge_id = str(edge.get("id") or "edge:unknown")
        record: dict[str, Any] = {"edge_id": edge_id,
                                  "source": edge.get("source_component"),
                                  "target": edge.get("target_component"),
                                  "emitted": [], "status": "emitted"}
        source, source_reason = _resolve_component(str(edge.get("source_component") or ""), components)
        target, target_reason = _resolve_component(str(edge.get("target_component") or ""), components)
        if source is None or target is None:
            record["status"] = "skipped"
            record["reason"] = source_reason or target_reason
            records.append(record)
            continue
        source_port = str(edge.get("source_port") or "")
        target_port = str(edge.get("target_port") or "")
        source_domain = _port_domain(source, source_port, bindings)
        target_domain = _port_domain(target, target_port, bindings)
        if source_domain is None:
            record["status"] = "quarantined"
            record["reason"] = (f"source port {source_port!r} is not declared on "
                                f"{source.name!r}")
            records.append(record)
            continue
        if target_domain is None:
            record["status"] = "quarantined"
            record["reason"] = (f"target port {target_port!r} is not declared on "
                                f"{target.name!r}")
            records.append(record)
            continue
        mapping = value_maps.get(edge.get("value_map") or "identity") or {}
        if not isinstance(mapping, dict) or not mapping:
            record["status"] = "quarantined"
            record["reason"] = f"value map {edge.get('value_map')!r} is not defined"
            records.append(record)
            continue
        source_values = QUALITATIVE_DOMAINS.get(source_domain, []) + ["unknown"]
        bad_source = [v for v in mapping if v not in source_values]
        target_values = QUALITATIVE_DOMAINS.get(target_domain, []) + ["unknown"]
        bad_target = [v for v in mapping.values() if v not in target_values]
        if bad_source or bad_target:
            record["status"] = "quarantined"
            record["reason"] = (f"value map {edge.get('value_map')!r} uses values outside "
                                f"the port domains (source: {bad_source}, target: {bad_target})")
            records.append(record)
            continue
        fault_override = edge.get("fault_override") or None
        fault_field = (fault_override or {}).get("target_fault_field") or "fault"
        # Nominal rows: one per source value, conditioned on the target
        # not being faulted (when the edge carries a fault override).
        # They also set the target state to "ok" when an override exists,
        # so the faulted component's state change is visible in the trace
        # (O5: the case-1(c) gun fault needs its third state change).
        for source_value in sorted(mapping, key=str):
            conditions: dict[str, Any] = {f"{source.id}.{source_port}": source_value}
            if fault_override is not None:
                conditions[f"{target.id}.{fault_field}"] = "none"
            effects: dict[str, Any] = {f"{target.id}.{target_port}": mapping[source_value]}
            if fault_override is not None:
                effects[f"{target.id}.state"] = "ok"
            transition_id = f"link:{edge_id}:{source_value}"
            transitions.append(TwinTransition(
                id=transition_id,
                name=f"Link {source.name}.{source_port} -> {target.name}.{target_port} "
                     f"({source_value} -> {mapping[source_value]})",
                conditions=conditions, effects=effects,
                description=("Reviewed topology link (omnipro-edge-list-v1): value flows "
                             "along a declared edge; the mapping is reviewed data."),
                source_ids=["omnipro-edge-list-v1"],
                evidence=[{"edge_id": edge_id, "sources": list(edge.get("sources") or [])}],
            ))
            record["emitted"].append(transition_id)
        if fault_override is not None:
            fault_value = fault_override.get("fault_value")
            effects = {f"{target.id}.{field}": value
                       for field, value in (fault_override.get("effects") or {}).items()}
            transition_id = f"link:{edge_id}:fault-{fault_value}"
            transitions.append(TwinTransition(
                id=transition_id,
                name=f"Link {source.name}.{source_port} -> {target.name}.{target_port} "
                     f"(fault {fault_value} blocks the path)",
                conditions={f"{target.id}.{fault_field}": fault_value},
                effects=effects,
                description=("Reviewed fault override on a topology link: a faulted "
                             "component passes no value along this edge."),
                source_ids=["omnipro-edge-list-v1"],
                evidence=[{"edge_id": edge_id, "sources": list(edge.get("sources") or [])}],
            ))
            record["emitted"].append(transition_id)
        records.append(record)
    return transitions, records
