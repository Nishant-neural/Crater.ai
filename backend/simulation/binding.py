"""Phase 3B evidence-based binding — to archetypes, not to types (v3 §8).

Priority order (unchanged from v2 §6): (1) typed ports / port signature
(strongest); (2) bound parameters / spec values; (3) topology /
neighbour evidence (supporting only, never sufficient alone); (4) name /
alias (hint only, at most a tie-break; ports win over a misleading name).

The v3 rule (no points table until 3D): bind to the archetype whose
signature + parameters match, *if* exactly one archetype matches on
signature and no other archetype matches on signature + parameters;
otherwise unresolved with the competing candidates + their evidence
recorded. The points table may return in 3D, tuned against oracle data
— as a *tuned* rule, not a guessed one.

§8 rule, effective now (third Amendments, item 4): a bound parameter
value counts as evidence for a contract parameter only when it measures
the same quantity on the same component (same domain, same component,
compatible unit family). Machine input voltage (p7 120/240 VAC) is NEVER
motor-rating evidence — a bound parameter counts only when it measures
the same quantity on the same component. The motor rating stays unbound
until a nameplate or spec value for the motor itself exists.

Binding never merges entities, never invents a parameter value, never
uses LLM judgement at runtime, and never fixes bad extraction. Evidence
is recorded per candidate so the table can be tuned later (in 3D).
"""
from __future__ import annotations

from typing import Any

from backend.simulation.archetypes import (
    ARCHETYPES, UNIT_FAMILIES, Archetype,
)


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _unit_family(unit: str | None) -> str | None:
    if not unit:
        return None
    cleaned = _norm(unit).replace(".", "")
    for family, members in UNIT_FAMILIES.items():
        if cleaned in members:
            return family
    return None


def parameter_evidence(entity_id: str, quantity: Any, parameter: Any) -> dict[str, Any] | None:
    """Evidence that one bound quantity supports one archetype parameter.

    Returns the evidence record, or None when the quantity is not
    evidence for this parameter. Hard requirements: the quantity must
    belong to the SAME component (``entity_id``) and must measure the
    same quantity — same unit family, or a name that matches the
    parameter name. A quantity on another component (or on no component,
    e.g. p7's machine input voltage with entity ``unresolved:entity``)
    is never evidence, however suggestive its value looks.
    """
    q_entity = quantity.entity_id if hasattr(quantity, "entity_id") else quantity.get("entity_id")
    q_name = quantity.name if hasattr(quantity, "name") else quantity.get("name")
    q_value = quantity.value if hasattr(quantity, "value") else quantity.get("value")
    q_unit = quantity.unit if hasattr(quantity, "unit") else quantity.get("unit")
    if _norm(q_entity) != _norm(entity_id):
        return None
    if q_value is None or str(q_value).strip() == "":
        return None
    param_name = parameter.name if hasattr(parameter, "name") else parameter.get("name")
    param_family = parameter.unit_family if hasattr(parameter, "unit_family") else parameter.get("unit_family")
    name_matches = _norm(param_name) in _norm(q_name) or _norm(q_name) in _norm(param_name)
    family_matches = (
        param_family is not None and _unit_family(q_unit) == param_family
    )
    if family_matches and not name_matches:
        # A unit family alone does not identify the quantity: "input
        # voltage" and "motor rating" share the voltage family but are
        # different quantities (§8, third Amendments item 4 — machine
        # input voltage is NEVER motor-rating evidence). A family match
        # counts only when the quantity's name does not name a different
        # quantity (input / supply / mains / line voltage).
        if any(marker in _norm(q_name)
               for marker in ("input", "supply", "mains", "line")):
            return None
    if not (name_matches or family_matches):
        return None
    return {
        "parameter": param_name,
        "quantity_name": q_name,
        "quantity_value": q_value,
        "quantity_unit": q_unit,
        "quantity_id": quantity.id if hasattr(quantity, "id") else quantity.get("id"),
        "basis": "same-component quantity, same unit family or name match",
    }


def entity_ports(entity: Any, model: Any = None) -> list[dict[str, Any]]:
    """The entity's typed ports as plain dicts (name/direction/domain/unit).

    Reads ``typed_ports`` on the entity first (Phase 2 truth), falling
    back to the model's port list matched by entity_id (v12 snapshots).
    """
    ports: list[dict[str, Any]] = []
    typed = list(getattr(entity, "typed_ports", None) or [])
    if typed:
        for port in typed:
            ports.append({
                "name": port.name if hasattr(port, "name") else port.get("name"),
                "direction": port.direction if hasattr(port, "direction") else port.get("direction"),
                "domain": port.domain if hasattr(port, "domain") else port.get("domain"),
                "unit": port.unit if hasattr(port, "unit") else port.get("unit"),
            })
        return ports
    for port in (getattr(model, "ports", None) or []):
        if getattr(port, "entity_id", None) == entity.id:
            ports.append({"name": port.name, "direction": port.direction,
                          "domain": port.domain, "unit": port.unit})
    return ports


def _signature_matches(archetype: Archetype, ports: list[dict[str, Any]],
                       bound_parameters: set[str]) -> tuple[bool, list[str]]:
    """True when the entity's ports (+ bound parameters) match the
    archetype's signature. Every required port must be present with the
    same direction and domain; every parameter flagged
    ``required_for_signature`` must be bound on the entity (from model
    data — the archetype's nominal default never counts as binding
    evidence). Returns (matches, missing)."""
    by_name = {_norm(p["name"]): p for p in ports}
    missing: list[str] = []
    for port in archetype.ports:
        if not port.required:
            continue
        candidate = by_name.get(_norm(port.name))
        if candidate is None:
            missing.append(f"port {port.name}")
        elif _norm(candidate.get("direction")) != _norm(port.direction):
            missing.append(f"port {port.name} direction "
                           f"{candidate.get('direction')} != {port.direction}")
        elif candidate.get("domain") and _norm(candidate.get("domain")) != _norm(port.domain):
            missing.append(f"port {port.name} domain "
                           f"{candidate.get('domain')} != {port.domain}")
    for parameter in archetype.parameters:
        if parameter.required_for_signature and parameter.name not in bound_parameters:
            missing.append(f"parameter {parameter.name} (required for signature)")
    return (not missing), missing


def bind_archetype(entity: Any, model: Any = None) -> dict[str, Any]:
    """Bind one canonical entity to one archetype, or leave it unresolved.

    Returns a binding record: {"archetype_id" | None, "evidence": {...},
    "candidates": [...], "unresolved_reason": ...}. Unique-match-on-
    evidence only: exactly one archetype may match on signature, and
    where several match on signature the distinguishing parameters
    decide (a bound distinguishing parameter separates two archetypes
    that share a port signature, v3 §2.4 item 3); otherwise the entity
    stays unresolved with the competing candidates + their evidence
    recorded. Name/alias is a hint only — it never binds on its own and
    never overrides a port-signature mismatch.
    """
    ports = entity_ports(entity, model)
    quantities = [q for q in (getattr(model, "quantities", None) or [])
                  if _norm(getattr(q, "entity_id", None)) == _norm(entity.id)]
    bound_parameters: set[str] = set()
    parameter_evidence_records: list[dict[str, Any]] = []
    for archetype in ARCHETYPES.values():
        for parameter in archetype.parameters:
            for quantity in quantities:
                evidence = parameter_evidence(entity.id, quantity, parameter)
                if evidence is not None:
                    bound_parameters.add(parameter.name)
                    parameter_evidence_records.append({**evidence,
                                               "archetype_id": archetype.archetype_id})
    # Neighbour evidence (supporting only): bound archetypes adjacent in
    # the model's relations. Recorded, never sufficient alone.
    neighbours: list[str] = []
    for relation in (getattr(model, "relations", None) or []):
        other = None
        if relation.subject_id == entity.id:
            other = relation.object_name
        elif relation.object_id == entity.id:
            other = relation.subject_name
        if other:
            neighbours.append(str(other))

    candidates: list[dict[str, Any]] = []
    for archetype in ARCHETYPES.values():
        matches, missing = _signature_matches(archetype, ports, bound_parameters)
        if not matches:
            continue
        distinguishing_bound = [
            p.name for p in archetype.parameters
            if p.distinguishing and p.name in bound_parameters]
        name_hint = _norm(entity.name) in {_norm(a) for a in archetype.aliases}
        candidates.append({
            "archetype_id": archetype.archetype_id,
            "distinguishing_bound": distinguishing_bound,
            "name_hint": name_hint,
            "ports_matched": [p.name for p in archetype.ports if p.required],
            "parameters_bound": sorted(
                p.name for p in archetype.parameters if p.name in bound_parameters),
            "parameters_unbound": sorted(
                p.name for p in archetype.parameters if p.name not in bound_parameters),
            "missing": missing,
        })
    record: dict[str, Any] = {
        "entity_id": entity.id,
        "entity_name": entity.name,
        "archetype_id": None,
        "evidence": {
            "ports": ports,
            "parameters_bound": sorted(bound_parameters),
            "parameter_evidence": parameter_evidence_records,
            "neighbours": sorted(set(neighbours)),
            "name": entity.name,
        },
        "candidates": candidates,
        "unresolved_reason": None,
    }
    if not candidates:
        record["unresolved_reason"] = (
            "no archetype signature matches "
            f"(ports: {[p['name'] for p in ports] or 'none recorded'}; "
            f"bound parameters: {sorted(bound_parameters) or 'none'})")
        return record
    if len(candidates) == 1:
        record["archetype_id"] = candidates[0]["archetype_id"]
        return record
    # Several archetypes match on signature: a bound distinguishing
    # parameter separates them (v3 §2.4 item 3). Exactly one candidate
    # with a bound distinguishing parameter wins; otherwise unresolved.
    with_distinguishing = [c for c in candidates if c["distinguishing_bound"]]
    if len(with_distinguishing) == 1:
        record["archetype_id"] = with_distinguishing[0]["archetype_id"]
        record["evidence"]["distinguished_by"] = with_distinguishing[0]["distinguishing_bound"]
        return record
    # Last resort, hint only: the name alias breaks the tie, and only if
    # exactly one tied candidate carries it. Ports still win over a
    # misleading name — a name hint never creates a match on its own.
    hinted = [c for c in candidates if c["name_hint"]]
    if len(hinted) == 1 and not with_distinguishing:
        record["archetype_id"] = hinted[0]["archetype_id"]
        record["evidence"]["tie_broken_by_name_hint"] = True
        return record
    record["unresolved_reason"] = (
        "ambiguous: archetypes "
        + ", ".join(c["archetype_id"] for c in candidates)
        + " all match on signature; distinguishing parameters: "
        + ", ".join(f"{c['archetype_id']}={c['distinguishing_bound'] or 'none'}"
                    for c in candidates))
    return record


def bind_model(model: Any) -> dict[str, dict[str, Any]]:
    """Bind every entity in a canonical model. Returns
    {entity_id: binding record}."""
    return {entity.id: bind_archetype(entity, model) for entity in model.entities}


def binding_coverage(bindings: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Binding coverage (v3 §11): % bound, % unresolved with reasons."""
    total = len(bindings)
    bound = {eid: rec for eid, rec in bindings.items() if rec["archetype_id"]}
    by_archetype: dict[str, int] = {}
    for rec in bound.values():
        by_archetype[rec["archetype_id"]] = by_archetype.get(rec["archetype_id"], 0) + 1
    unresolved = {eid: rec["unresolved_reason"] for eid, rec in bindings.items()
                  if not rec["archetype_id"]}
    return {"entities": total, "bound": len(bound),
            "bound_rate": (len(bound) / total) if total else None,
            "by_archetype": by_archetype,
            "unresolved": len(unresolved),
            "unresolved_reasons": unresolved}
