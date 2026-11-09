"""Validation helpers for Phase 8A universal machine knowledge."""
from __future__ import annotations

import re
from collections import defaultdict

from backend.knowledge.machine_model import MachineEntity, UniversalMachineModel
from backend.knowledge.normalization import unresolved_record


def validate_machine_model(model: UniversalMachineModel) -> list[str]:
    """Return human-readable validation issues for a machine model.

    The validator is intentionally conservative: it reports structurally invalid
    references, duplicate entities, and conflicting state assignments while
    preserving all evidence rather than silently dropping the conflict.
    """
    issues: list[str] = []
    entity_map = model.entity_map()

    seen_names: dict[str, str] = {}
    seen_ids: set[str] = set()
    for entity in model.entities:
        if entity.id in seen_ids:
            issues.append(f"duplicate entity id: {entity.id}")
        seen_ids.add(entity.id)
        if entity.name in seen_names:
            issues.append(f"duplicate entity name: {entity.name}")
        else:
            seen_names[entity.name] = entity.id

    for relation in model.relations:
        if relation.subject_id not in entity_map:
            issues.append(f"relation references missing subject entity: {relation.subject_id}")
        if relation.object_id not in entity_map:
            issues.append(f"relation references missing object entity: {relation.object_id}")

    state_values: dict[tuple[str, str], set[str]] = defaultdict(set)
    for state in model.states:
        entity_id = state.entity_id
        if entity_id not in entity_map:
            issues.append(f"state references missing entity: {entity_id}")
            continue
        state_values[(entity_id, state.name)].add(str(state.value))
    for (entity_id, state_name), values in state_values.items():
        if len(values) > 1:
            issues.append(f"conflicting state values for {entity_id}/{state_name}: {sorted(values)}")

    for relation in model.relations:
        if not relation.evidence and relation.description:
            issues.append(f"relation without evidence: {relation.subject_name} {relation.relation_type} {relation.object_name}")

    for entity in model.entities:
        if not entity.evidence and entity.properties:
            issues.append(f"entity with properties but no evidence: {entity.name}")
        for evidence in entity.evidence:
            issues.extend(_evidence_issues(f"entity {entity.name}", evidence))

    for relation in model.relations:
        for evidence in relation.evidence:
            issues.extend(_evidence_issues(f"relation {relation.subject_name}->{relation.object_name}", evidence))

    for fact_type, _, payload in model.all_facts():
        evidence = payload.get("evidence", [])
        for item in evidence:
            confidence = item.get("confidence", 0.0)
            if confidence < 0.5:
                issues.append(f"low-confidence {fact_type}: {confidence}")
        status = str(payload.get("properties", {}).get("status", "")).upper()
        if status in {"UNKNOWN", "UNCERTAIN"}:
            issues.append(f"unsupported or uncertain {fact_type}")

    return issues


def _evidence_issues(label: str, evidence) -> list[str]:
    issues: list[str] = []
    if not evidence.fact:
        issues.append(f"{label} evidence missing fact")
    if not evidence.source_type and not evidence.source_document:
        issues.append(f"{label} evidence missing source type")
    if not evidence.extraction_method and not evidence.source_document:
        issues.append(f"{label} evidence missing extraction method")
    if evidence.confidence < 0.5:
        issues.append(f"low-confidence {label}: {evidence.confidence}")
    if evidence.source_document is None and evidence.source_type not in {"expert", "schematic"}:
        issues.append(f"{label} evidence missing source document")
    return issues


validate_model = validate_machine_model


def _stub_id_for(endpoint_id: str, endpoint_name: str) -> str:
    """Id for an endpoint-derived stub entity.

    The endpoint id the relation already carries, unless it is empty
    or an unresolved placeholder, in which case the slug of the
    endpoint name. An empty name yields no id, so the caller keeps
    the relation quarantined instead of inventing an entity.
    """
    eid = (endpoint_id or "").strip()
    if eid and not eid.startswith("unresolved:"):
        return eid
    slug = re.sub(r"[^a-z0-9]+", "-", (endpoint_name or "").strip().lower()).strip("-")
    return f"entity:{slug}" if slug else ""


def quarantine_machine_model(model: UniversalMachineModel) -> tuple[UniversalMachineModel, list[dict]]:
    """Per-fact quarantine: keep every usable fact, quarantine only the unusable.

    This replaces the old all-or-nothing pattern in the pipeline, where one
    bad relation or duplicate name in a chunk caused the *whole* chunk model
    to be stored as a single unresolved blob and none of its good facts to
    be persisted.

    Quarantined (removed from the kept model, preserved as a ledger record):
    - duplicate entity ids (second occurrence)
    - relations whose subject or object has no usable name at all, so
      no endpoint entity can be recorded for it (see the endpoint
      rule below: a relation with a named but unlisted endpoint is
      kept, with a stub entity, not quarantined)
    - ports / states / events whose entity is not in the kept set
    - quantities whose entity is not in the kept set AND which carry no
      usable measurement (empty value, or a placeholder name). See the
      quantity rule below.
    - a second, conflicting value for the same (entity, state name)
    - simulation rules with no conditions/effects, or whose paths name
      an entity or state field that is not in this chunk (a rule may only
      condition on a state the chunk actually extracted for that entity)
    - facts with no id / name at all

    Deliberately NOT quarantined, because they are still facts:
    - duplicate entity *names* with different ids (two fans, two knobs)
    - low confidence (confidence is a ranking signal, not validity)
    - behaviors / procedures / failure modes whose subject entity is not
      yet known -- the description itself is a fact, independent of the
      chunk it came from. Their missing link is recorded in the ledger
      for the pending-link pass, but the fact is kept.
    - quantities with a real name and a non-empty value whose entity is
      not in the kept set. A specification-table quantity is usually
      about a welding mode or the whole machine ("Mig Power Input",
      120 VAC), not about a named component extracted in the same
      chunk, so its entity_id is the explicit placeholder
      "unresolved:entity". Quarantining it discards a real, evidenced
      measurement for a link that was never going to be in the chunk.
      The quantity is kept with its entity_id left as the placeholder
      (never guessed into an entity) and its subject_name preserved,
      so the link stays visibly open. A quantity with a specific,
      non-placeholder entity id that is not in the kept set is kept
      the same way and gets a missing_link_note, since a later
      revision-wide pass can still resolve it. Quantities with an
      empty value or a placeholder name ("Unresolved item") are still
      quarantined: a unit-abbreviation glossary (Vac, a, OcV, ...) is
      not a measurement.
    - relations whose endpoint entity was never listed in the chunk,
      even though the relation itself was extracted with a name and
      evidence for both endpoints. Dropping the relation loses a real,
      stated connection (crater-6 lost all 11 of its relations this
      way: cables and sockets named only inside a "plug X into Y"
      step, the machine itself, the workpiece). Instead the relation
      is kept and a minimal stub entity is recorded for each missing
      endpoint: its id and name are taken verbatim from the relation,
      no function, location or property is invented, it is marked
      endpoint_derived in properties, and a missing_link_note records
      that it was derived from the relation rather than directly
      extracted. The stub is per chunk, like every other entity here;
      integration groups it with same-name entities from other chunks
      only under its usual no-conflict rules, so a stub for the whole
      machine stays separate from the parts-list Welder when their
      part numbers differ, exactly as prose Fan stays separate from
      Fan 44. A relation is still quarantined when an endpoint has no
      usable name, or its id belongs to an entity already quarantined
      as a duplicate.
    - entities with an invalid bbox -- the bbox is stripped, the entity kept.

    No deterministic entity register is used here, and no fact is guessed
    into a canonical identity. Anything quarantined stays in the unresolved
    ledger with its original payload, so a later pass (or a human) can
    resurrect it instead of it vanishing.
    """
    from backend.knowledge.machine_model import UniversalMachineModel as _UMM

    kept_entities = []
    seen_ids: set[str] = set()
    quarantined: list[dict] = []

    def _quarantine(kind: str, item, reason: str):
        payload = item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item)
        quarantined.append(unresolved_record(
            payload,
            source_id=payload.get("id") or (payload.get("source_ids") or [None])[0],
            fact_type=kind, reason=reason,
        ))

    for entity in model.entities:
        if not entity.id:
            _quarantine("entity", entity, "missing_entity_id")
            continue
        if entity.id in seen_ids:
            _quarantine("entity", entity, "duplicate_entity_id")
            continue
        seen_ids.add(entity.id)
        # Invalid bbox: strip it, keep the entity. A bad coordinate must
        # not kill a real component.
        props = dict(entity.properties or {})
        bbox = props.get("bbox")
        if isinstance(bbox, dict):
            try:
                x, y, w, h = (float(bbox[k]) for k in ("x", "y", "w", "h"))
                if not (w > 0 and h > 0 and x >= 0 and y >= 0 and x + w <= 1 and y + h <= 1):
                    raise ValueError("out of range")
            except Exception:
                props.pop("bbox", None)
                entity.properties = props
                quarantined.append(unresolved_record(
                    {"entity_id": entity.id, "bbox": bbox},
                    source_id=entity.id, fact_type="entity_bbox",
                    reason="invalid_bbox_stripped",
                ))
        kept_entities.append(entity)

    kept_ids = {e.id for e in kept_entities}

    kept_relations = []
    stub_ids: set[str] = set()
    for rel in model.relations:
        missing_sides = []
        if rel.subject_id not in kept_ids:
            missing_sides.append(("subject", rel.subject_id, rel.subject_name))
        if rel.object_id not in kept_ids:
            missing_sides.append(("object", rel.object_id, rel.object_name))
        if not missing_sides:
            kept_relations.append(rel)
            continue
        # Endpoint recovery: keep the relation and record a minimal
        # stub entity for each missing endpoint (see docstring). The
        # stub id is the endpoint id the relation already carries,
        # unless it is empty or a placeholder, in which case it is
        # the slug of the endpoint name; the relation is rewritten
        # to that id only in that case, never otherwise.
        rewritten = {"subject_id": rel.subject_id, "object_id": rel.object_id}
        can_keep = True
        for side, eid, name in missing_sides:
            # A missing or placeholder name is not a thing: the model
            # normalizer fills empty names with "Unresolved subject" /
            # "Unresolved object", and a stub for that would invent an
            # entity out of nothing. Only a real stated name may seed
            # a stub.
            if not (name or "").strip() or name.strip() in {
                "Unresolved subject", "Unresolved object", "Unresolved item",
            }:
                can_keep = False
                break
            new_id = _stub_id_for(eid, name)
            if not new_id:
                can_keep = False
                break
            if new_id in kept_ids or new_id in stub_ids:
                rewritten["subject_id" if side == "subject" else "object_id"] = new_id
                continue
            if new_id in seen_ids:
                # The id belongs to an entity already quarantined as
                # a duplicate: do not resurrect it as a stub.
                can_keep = False
                break
            stub = MachineEntity(
                id=new_id, name=name.strip(), entity_type="component",
                properties={"endpoint_derived": True},
                evidence=list(rel.evidence),
            )
            kept_entities.append(stub)
            kept_ids.add(new_id)
            stub_ids.add(new_id)
            seen_ids.add(new_id)
            quarantined.append(unresolved_record(
                stub.model_dump(mode="json"), source_id=new_id,
                fact_type="missing_link_note",
                reason=f"relation_endpoint_entity_derived_from_relation:{rel.subject_name} {rel.relation_type} {rel.object_name} (fact kept)",
            ))
            rewritten["subject_id" if side == "subject" else "object_id"] = new_id
        if not can_keep:
            missing = []
            if rel.subject_id not in kept_ids:
                missing.append(f"subject:{rel.subject_id}")
            if rel.object_id not in kept_ids:
                missing.append(f"object:{rel.object_id}")
            _quarantine("relation", rel, "unresolved_entity_reference:" + ",".join(missing))
            continue
        if rewritten["subject_id"] != rel.subject_id or rewritten["object_id"] != rel.object_id:
            rel = rel.model_copy(update=rewritten)
        kept_relations.append(rel)

    def _keep_quantities(quantities, kept_ids, quarantined):
        """Keep a quantity that is a real measurement, even unlinked."""
        kept = []
        for item in quantities:
            eid = getattr(item, "entity_id", None)
            if not eid or eid in kept_ids:
                kept.append(item)
                continue
            name = str(getattr(item, "name", "") or "").strip()
            value = getattr(item, "value", None)
            value_text = "" if value is None else str(value).strip()
            if name and name != "Unresolved item" and value_text:
                # A real measurement about a mode / the whole machine:
                # keep it with the placeholder entity_id untouched.
                # Only a specific (non-placeholder) entity id gets a
                # missing-link note, since that one could still resolve.
                if not str(eid).startswith("unresolved:"):
                    quarantined.append(unresolved_record(
                        item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item),
                        source_id=item.model_dump(mode="json").get("id") if hasattr(item, "model_dump") else None,
                        fact_type="missing_link_note",
                        reason=f"quantity_subject_not_in_kept_entities:{eid} (fact kept)",
                    ))
                kept.append(item)
                continue
            _quarantine("quantity", item, f"unresolved_entity_reference:{eid}")
        return kept

    def _keep_linked(collection, kind):
        kept = []
        for item in collection:
            eid = getattr(item, "entity_id", None)
            if eid and eid not in kept_ids:
                _quarantine(kind, item, f"unresolved_entity_reference:{eid}")
            else:
                kept.append(item)
        return kept

    kept_ports = _keep_linked(model.ports, "port")
    kept_quantities = _keep_quantities(model.quantities, kept_ids, quarantined)
    kept_events = _keep_linked(model.events, "event")

    kept_states = []
    seen_states: dict[tuple[str, str], str] = {}
    for state in model.states:
        if state.entity_id and state.entity_id not in kept_ids:
            _quarantine("state", state, f"unresolved_entity_reference:{state.entity_id}")
            continue
        key = (state.entity_id, state.name)
        if key in seen_states and seen_states[key] != str(state.value):
            _quarantine("state", state, f"conflicting_state_value:{seen_states[key]} vs {state.value}")
            continue
        seen_states[key] = str(state.value)
        kept_states.append(state)

    # Simulation rules: kept only when every condition/effect path names
    # an entity and a state field that this chunk actually extracted. A rule
    # is one fact; a bad path quarantines that rule, never the chunk. Paths
    # are "<Entity Name>.<state name>" at extraction time (integration rewrites
    # the name part to a canonical id); values are never second-guessed here.
    kept_rules: list[dict] = []
    _kept_names = {e.name.strip().lower(): e for e in kept_entities if e.name}
    _known_fields: dict[str, set[str]] = {}
    for _st in list(model.states):
        _eid = _st.entity_id if hasattr(_st, "entity_id") else (dict(_st).get("entity_id") if isinstance(_st, dict) else None)
        _nm = _st.name if hasattr(_st, "name") else (dict(_st).get("name") if isinstance(_st, dict) else None)
        if _eid and _nm:
            _known_fields.setdefault(str(_eid), set()).add(str(_nm).strip().lower())
    # States embedded in an entity carry no entity_id of their own; the
    # enclosing entity is their owner. (v10.1: entity states are name/value
    # objects, so they are visible here at all -- as plain strings they
    # contributed nothing, and a rule naming only an embedded state was
    # quarantined even though the chunk extracted that state.)
    for _ent in kept_entities:
        for _st in (_ent.states or []):
            _nm = _st.name if hasattr(_st, "name") else (dict(_st).get("name") if isinstance(_st, dict) else None)
            if _nm:
                _known_fields.setdefault(str(_ent.id), set()).add(str(_nm).strip().lower())
    for _ent in kept_entities:
        _init = (_ent.properties or {}).get("initial_state")
        if isinstance(_init, dict):
            _known_fields.setdefault(_ent.id, set()).update(str(k).strip().lower() for k in _init)
    for _rule in list(model.simulation_rules or []):
        if not isinstance(_rule, dict):
            _quarantine("simulation_rule", {"value": _rule}, "simulation_rule_not_object")
            continue
        _conds, _effs = _rule.get("conditions"), _rule.get("effects")
        if not isinstance(_conds, dict) or not _conds or not isinstance(_effs, dict) or not _effs:
            _quarantine("simulation_rule", _rule, "simulation_rule_missing_conditions_or_effects")
            continue
        _bad: str | None = None
        for _path in list(_conds) + list(_effs):
            if not isinstance(_path, str) or "." not in _path:
                _bad = f"simulation_rule_bad_path:{_path}"
                break
            _ename, _field = _path.split(".", 1)
            # Paths arrive as "<Entity Name>.<state>" from extraction,
            # but as "<canonical id>.<state>" after integration rewrites
            # them -- integrate_revision_knowledge re-runs this veto
            # on the integrated model, so both forms must resolve.
            _ent = _kept_names.get(_ename.strip().lower())
            if _ent is None:
                _ent = next((e for e in kept_entities if e.id == _ename.strip()), None)
            if _ent is None:
                _bad = f"simulation_rule_unknown_entity:{_ename}"
                break
            if _field.strip().lower() not in _known_fields.get(_ent.id, set()):
                _bad = f"simulation_rule_unknown_state:{_ename}.{_field}"
                break
        if _bad:
            _quarantine("simulation_rule", _rule, _bad)
            continue
        kept_rules.append(_rule)

    # Behaviors, constraints, procedures and failure modes are kept even
    # when their subject is not yet in the kept set: the fact itself is
    # independent of the chunk that produced it. A missing link is noted
    # in the ledger without removing the fact.
    for item in list(model.behaviors) + list(model.constraints) + list(model.procedures) + list(model.failure_modes):
        eid = getattr(item, "entity_id", None) or getattr(item, "subject_id", None)
        if eid and eid not in kept_ids and not str(eid).startswith("unresolved:"):
            quarantined.append(unresolved_record(
                item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item),
                source_id=item.model_dump(mode="json").get("id") if hasattr(item, "model_dump") else None,
                fact_type="missing_link_note",
                reason=f"subject_not_in_kept_entities:{eid} (fact kept)",
            ))

    kept = _UMM(
        entities=kept_entities,
        relations=kept_relations,
        ports=kept_ports,
        quantities=kept_quantities,
        states=kept_states,
        events=kept_events,
        behaviors=list(model.behaviors),
        constraints=list(model.constraints),
        procedures=list(model.procedures),
        failure_modes=list(model.failure_modes),
        conflicts=list(model.conflicts),
        unresolved_facts=list(model.unresolved_facts),
        simulation_rules=kept_rules,
        evidence=list(model.evidence),
    )
    # De-duplicate quarantine records deterministically.
    unique: dict[tuple, dict] = {}
    for item in quarantined:
        key = (item.get("fact_type"), item.get("source_id"), item.get("reason"))
        unique[key] = item
    return kept, list(unique.values())


def validate_machine_model_with_ledger(model: UniversalMachineModel) -> tuple[list[str], list[dict]]:
    """Validate semantics and return an explicit ledger for facts that cannot be trusted.

    The model is not mutated and no fact is silently discarded. Callers may persist
    returned ledger records alongside the model for review/reprocessing.
    """
    issues = validate_machine_model(model)
    ledger: list[dict] = []

    def record(kind: str, item, reason: str) -> None:
        payload = item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item)
        ledger.append(unresolved_record(
            payload, source_id=payload.get("id") or (payload.get("source_ids") or [None])[0],
            fact_type=kind, reason=reason,
        ))

    entity_ids = {entity.id for entity in model.entities}
    for relation in model.relations:
        if relation.subject_id not in entity_ids or relation.object_id not in entity_ids:
            missing = []
            if relation.subject_id not in entity_ids:
                missing.append(f"subject:{relation.subject_id}")
            if relation.object_id not in entity_ids:
                missing.append(f"object:{relation.object_id}")
            record("relation", relation, "unresolved_entity_reference:" + ",".join(missing))
    for kind, collection in (
        ("port", model.ports), ("quantity", model.quantities), ("state", model.states),
        ("event", model.events), ("behavior", model.behaviors), ("constraint", model.constraints),
    ):
        for item in collection:
            entity_id = getattr(item, "entity_id", None) or getattr(item, "subject_id", None)
            if entity_id and entity_id not in entity_ids and not str(entity_id).startswith("unresolved:"):
                record(kind, item, f"unresolved_entity_reference:{entity_id}")
            elif entity_id and str(entity_id).startswith("unresolved:"):
                record(kind, item, "placeholder_entity_reference")
    for kind, collection in (("procedure", model.procedures), ("failure_mode", model.failure_modes)):
        for item in collection:
            if not item.evidence:
                record(kind, item, "missing_evidence")
    for item in model.unresolved_facts:
        ledger.append(item.model_dump(mode="json"))
    # De-duplicate repeated issue records deterministically.
    unique = {}
    for item in ledger:
        key = (item.get("fact_type"), item.get("source_id"), item.get("reason"))
        unique[key] = item
    return issues, list(unique.values())
