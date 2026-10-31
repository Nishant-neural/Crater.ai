"""Global machine-knowledge integration.

Phase 8A extraction is intentionally chunk-local. This module is the second
stage: it loads all persisted knowledge for one revision, gives the complete
fact set to the LLM, and asks it to resolve identities and relationships into
one canonical UniversalMachineModel.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from backend.db.models import (
    Component,
    ComponentRelationship,
    ExpertKnowledge,
    FailureMode,
    MachineKnowledgeBehavior,
    MachineKnowledgeEntity,
    MachineKnowledgeEvidence,
    MachineKnowledgeFact,
    MachineKnowledgeRelation,
    Procedure,
    SchematicEdge,
    SchematicNode,
)
from backend.knowledge.evidence import MachineEvidence
from backend.knowledge.machine_model import (
    MachineBehavior,
    MachineConstraint,
    MachineEntity,
    MachineEvent,
    MachineFailureMode,
    MachinePort,
    MachineProcedure,
    MachineQuantity,
    MachineRelation,
    MachineState,
    UniversalMachineModel,
)
from backend.llm import gateway
from backend.knowledge.normalization import normalize_payload, persist_unresolved_fact

_GLOBAL_PROMPT = """You are the global integration engine for an industrial machine knowledge system.
You are given ALL persisted extracted knowledge for ONE machine revision. The records were
extracted independently from text, tables, schematics, procedures and expert knowledge.
Your job is NOT to summarize them. Build one canonical UniversalMachineModel.

Rules:
1. Resolve aliases/repeated mentions of the same physical or logical entity into one canonical entity.
2. Preserve source ids in source_ids on canonical entities/relations/facts so provenance can be mapped back.
3. Connect relations across records when the persisted evidence supports the connection.
4. Do not invent undocumented components, relationships, values, procedures or failure modes.
5. Prefer explicit evidence over guesses. If two facts conflict, keep both as distinct evidence and
   represent the conflict in the affected object's properties using `conflict` rather than silently choosing.
6. Canonical IDs must be stable, readable IDs such as `entity:p-101`, `entity:pressure-sensor-ps1`.
7. Ports/states/quantities/events/behaviors/constraints/procedures/failure modes must reference the
   canonical entity ids where applicable.
8. Preserve evidence by copying source evidence references into the canonical objects. Keep each evidence claim_status (observed|inferred|uncertain); do not upgrade inferred or uncertain claims to observed.
9. Every persisted source record must be referenced by source_ids on at least one canonical item. Never silently drop a source record.
10. If a source record cannot be canonically integrated, put it in unresolved_facts with its source_id, fact_type and original payload.
11. simulation_rules are optional. Include a rule ONLY when the persisted evidence explicitly defines a machine-state transition. Never infer a rule merely from a component type or a vague behavior description. Each rule must use existing canonical entity/state/signal paths.
12. The output must contain only the requested JSON.

PERSISTED KNOWLEDGE:
{knowledge}

Return JSON with this exact top-level shape:
{{
  "entities": [{{"id":"", "name":"", "entity_type":"", "properties":{{}}, "source_ids":[], "evidence":[]}}],
  "relations": [{{"subject_id":"", "subject_name":"", "relation_type":"", "object_id":"", "object_name":"", "description":"", "source_ids":[], "evidence":[]}}],
  "ports": [{{"id":"", "entity_id":"", "name":"", "direction":"", "properties":{{}}, "source_ids":[], "evidence":[]}}],
  "quantities": [{{"id":"", "entity_id":"", "name":"", "value":"", "unit":"", "properties":{{}}, "source_ids":[], "evidence":[]}}],
  "states": [{{"entity_id":"", "name":"", "value":"", "properties":{{}}, "source_ids":[], "evidence":[]}}],
  "events": [{{"id":"", "entity_id":"", "name":"", "description":"", "source_ids":[], "evidence":[]}}],
  "behaviors": [{{"id":"", "subject_id":"", "subject_name":"", "description":"", "source_ids":[], "evidence":[]}}],
  "constraints": [{{"id":"", "entity_id":null, "name":"", "description":"", "source_ids":[], "evidence":[]}}],
  "procedures": [{{"id":"", "name":"", "procedure_type":"", "steps":[], "entity_ids":[], "source_ids":[], "evidence":[]}}],
  "failure_modes": [{{"id":"", "name":"", "symptoms":[], "possible_causes":[], "diagnostic_test":"", "expected_observation":"", "repair_procedure_id":null, "entity_ids":[], "source_ids":[], "evidence":[]}}],
  "simulation_rules": [{{"id":"", "name":"", "conditions":{{}}, "effects":{{}}, "description":"", "safety_notes":[], "source_ids":[], "evidence":[]}}],
  "conflicts": [{{"id":"", "subject":"", "property":"", "values":[], "resolution":null, "status":"unresolved", "source_ids":[], "evidence":[]}}],
  "unresolved_facts": [{{"source_id":"", "fact_type":"", "payload":{{}}, "reason":""}}]
}}"""


@dataclass
class IntegrationResult:
    model: UniversalMachineModel
    payload: dict[str, Any]
    source_counts: dict[str, int]


def _safe_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _safe_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_safe_json(v) for v in value]
    if hasattr(value, "value"):
        return value.value
    return value


def _evidence_for(session: Session, item_type: str, item_id: str) -> list[dict[str, Any]]:
    rows = session.query(MachineKnowledgeEvidence).filter(
        MachineKnowledgeEvidence.item_type == item_type,
        MachineKnowledgeEvidence.item_id == item_id,
    ).all()
    return [{
        "fact": r.fact, "source_document": r.source_document, "page": r.page,
        "chunk": r.chunk, "source_type": r.source_type, "location": r.location,
        "region": r.region, "confidence": r.confidence,
        "claim_status": r.claim_status or "observed",
        "extraction_method": r.extraction_method, "metadata": r.evidence_metadata or {},
    } for r in rows]


def build_revision_knowledge_context(session: Session, revision_id: str) -> tuple[dict[str, Any], dict[str, int]]:
    """Load every persisted knowledge source belonging to the revision."""
    entities = session.query(MachineKnowledgeEntity).filter_by(revision_id=revision_id).all()
    relations = session.query(MachineKnowledgeRelation).filter_by(revision_id=revision_id).all()
    facts = session.query(MachineKnowledgeFact).filter_by(revision_id=revision_id).all()
    behaviors = session.query(MachineKnowledgeBehavior).filter_by(revision_id=revision_id).all()
    components = session.query(Component).filter_by(revision_id=revision_id).all()
    procedures = session.query(Procedure).filter_by(revision_id=revision_id).all()
    failures = session.query(FailureMode).filter_by(revision_id=revision_id).all()

    # Schematic rows are revision-scoped indirectly through their document/chunk.
    from backend.db.models import Chunk, Document, KnowledgeVersion
    docs = session.query(Document).filter(Document.revision_id == revision_id).all()
    document_ids = [d.id for d in docs]
    chunk_rows = session.query(Chunk).filter(Chunk.document_id.in_(document_ids or [""])).all()
    chunk_ids = [c.id for c in chunk_rows]
    schematic_nodes = session.query(SchematicNode).filter(SchematicNode.chunk_id.in_(chunk_ids or [""])).all()
    schematic_edges = session.query(SchematicEdge).filter(SchematicEdge.document_id.in_(document_ids or [""])).all()
    component_relationships = session.query(ComponentRelationship).filter(
        ComponentRelationship.source_chunk_id.in_(chunk_ids or [""])
    ).all()
    expert = session.query(ExpertKnowledge).join(
        KnowledgeVersion, ExpertKnowledge.knowledge_version_id == KnowledgeVersion.id
    ).filter(KnowledgeVersion.revision_id == revision_id).all()

    context = {
        "universal_entities": [
            {"source_id": r.id, "name": r.name, "entity_type": r.entity_type,
             "properties": r.properties or {}, "ports": r.ports or [], "states": r.states or [],
             "evidence": _evidence_for(session, "entity", r.id)} for r in entities
        ],
        "universal_relations": [
            {"source_id": r.id, "subject_id": r.subject_id, "subject_name": r.subject_name,
             "relation_type": r.relation_type, "object_id": r.object_id, "object_name": r.object_name,
             "description": r.description, "evidence": _evidence_for(session, "relation", r.id)} for r in relations
        ],
        "universal_facts": [
            {"source_id": r.id, "fact_type": r.fact_type, "fact_key": r.fact_key,
             "payload": r.payload} for r in facts
        ],
        "behaviors": [
            {"source_id": r.id, "subject_id": r.subject_id, "subject_name": r.subject_name,
             "description": r.description, "evidence": _evidence_for(session, "behavior", r.id)} for r in behaviors
        ],
        "legacy_components": [
            {"source_id": r.id, "name": r.name, "function": r.function,
             "location_description": r.location_description, "part_number": r.part_number,
             "source_chunk_id": r.source_chunk_id} for r in components
        ],
        "legacy_relationships": [
            {"source_id": r.id, "from_component_id": r.from_component_id,
             "to_component_id": r.to_component_id, "relation_type": r.relation_type.value,
             "description": r.description, "source_chunk_id": r.source_chunk_id}
            for r in component_relationships
        ],
        "procedures": [
            {"source_id": r.id, "name": r.name, "procedure_type": r.procedure_type.value,
             "steps": r.steps, "source_chunk_id": r.source_chunk_id} for r in procedures
        ],
        "failure_modes": [
            {"source_id": r.id, "symptom": r.symptom, "possible_causes": r.possible_causes,
             "diagnostic_test": r.diagnostic_test, "expected_observation": r.expected_observation,
             "repair_procedure_id": r.repair_procedure_id, "source_chunk_id": r.source_chunk_id} for r in failures
        ],
        "schematic_nodes": [
            {"source_id": r.id, "label": r.label, "symbol_type": r.symbol_type.value,
             "description": r.description, "bbox": r.bbox, "chunk_id": r.chunk_id} for r in schematic_nodes
        ],
        "schematic_edges": [
            {"source_id": r.id, "from_node_id": r.from_node_id, "to_node_id": r.to_node_id,
             "wire_type": r.wire_type.value, "label": r.label} for r in schematic_edges
        ],
        "expert_knowledge": [
            {"source_id": r.id, "knowledge_type": r.knowledge_type.value,
             "title": r.title, "failure_mode": r.failure_mode, "symptom": r.symptom,
             "action": r.action, "expected_observation": r.expected_observation,
             "confidence": r.confidence, "source_quote": r.source_quote}
            for r in expert
        ],
    }
    counts = {k: len(v) for k, v in context.items()}
    return _safe_json(context), counts


def _parse_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                return {}   # truncated / malformed output: caller splits the batch and retries
        return {}


def _ev(raw: list[dict[str, Any]] | None) -> list[MachineEvidence]:
    """Normalize integration evidence without discarding source provenance.

    The integration LLM sometimes returns an evidence *reference* (source_id and
    claim_status) rather than the full MachineEvidence shape.  MachineEvidence.fact
    is required, so turn that reference into an explicit reference claim and retain
    the identifier in metadata instead of failing the entire revision integration.
    """
    normalized: list[MachineEvidence] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        data = dict(item)
        source_id = data.pop("source_id", None)
        metadata = data.get("metadata")
        metadata = dict(metadata) if isinstance(metadata, dict) else {}
        if source_id:
            metadata.setdefault("source_id", str(source_id))
        data["metadata"] = metadata
        if not isinstance(data.get("fact"), str) or not data["fact"].strip():
            if source_id:
                data["fact"] = f"Evidence reference to source record {source_id}"
            else:
                # A malformed evidence item must not crash integration, but mark it
                # as unresolved rather than pretending a factual claim was supplied.
                data["fact"] = "Evidence claim text missing from integration output"
                metadata["normalization_warning"] = "missing_fact_and_source_id"
        normalized.append(MachineEvidence.model_validate(data))
    return normalized


def _model_from_payload(payload: dict[str, Any]) -> UniversalMachineModel:
    # source_ids are integration metadata and are deliberately not part of the canonical model.
    entities = [MachineEntity(
        id=x["id"], name=x["name"], entity_type=x.get("entity_type", "unknown"),
        properties=x.get("properties", {}), source_ids=x.get("source_ids", []), evidence=_ev(x.get("evidence")),
    ) for x in payload.get("entities", []) if x.get("id") and x.get("name")]
    relations = [MachineRelation(
        source_ids=x.get("source_ids", []), subject_id=x["subject_id"], subject_name=x["subject_name"], relation_type=x["relation_type"],
        object_id=x["object_id"], object_name=x["object_name"], description=x.get("description"),
        evidence=_ev(x.get("evidence")),
    ) for x in payload.get("relations", []) if x.get("subject_id") and x.get("object_id")]
    ports = [MachinePort(
        id=x["id"], source_ids=x.get("source_ids", []), entity_id=x["entity_id"], name=x["name"], direction=x.get("direction"),
        properties=x.get("properties", {}), evidence=_ev(x.get("evidence")),
    ) for x in payload.get("ports", []) if x.get("id") and x.get("entity_id")]
    quantities = [MachineQuantity(
        id=x["id"], source_ids=x.get("source_ids", []), entity_id=x["entity_id"], name=x["name"], value=x.get("value"), unit=x.get("unit"),
        properties=x.get("properties", {}), evidence=_ev(x.get("evidence")),
    ) for x in payload.get("quantities", []) if x.get("id") and x.get("entity_id")]
    states = [MachineState(
        source_ids=x.get("source_ids", []), entity_id=x["entity_id"], name=x["name"], value=x.get("value"), properties=x.get("properties", {}),
        evidence=_ev(x.get("evidence")),
    ) for x in payload.get("states", []) if x.get("entity_id") and x.get("name")]
    events = [MachineEvent(
        id=x["id"], source_ids=x.get("source_ids", []), entity_id=x["entity_id"], name=x["name"], description=x.get("description"),
        evidence=_ev(x.get("evidence")),
    ) for x in payload.get("events", []) if x.get("id") and x.get("entity_id")]
    behaviors = []
    for index, raw_behavior in enumerate(payload.get("behaviors", [])):
        if not isinstance(raw_behavior, dict):
            continue
        x = dict(raw_behavior)
        # LLM output may use extraction-style keys (component/behavior) instead
        # of the canonical MachineBehavior schema (subject_id/description).
        source_ids = x.get("source_ids") or ([str(x["source_id"])] if x.get("source_id") else [])
        component = x.get("component") or x.get("subject_name") or x.get("subject") or "Unknown component"
        subject_name = str(x.get("subject_name") or component)
        description = x.get("description") or x.get("behavior") or x.get("behaviour") or x.get("effect")
        if isinstance(description, dict):
            description = json.dumps(description, ensure_ascii=False, sort_keys=True)
        if not description:
            # Preserve the original record as transparent unresolved detail rather
            # than silently inventing a behavior description.
            description = "Behavior description missing; original payload retained in evidence metadata"
        subject_id = x.get("subject_id") or x.get("entity_id")
        if not subject_id:
            # Mark unlinked subjects explicitly; do not pretend they resolve to a
            # canonical entity just because a component label was supplied.
            subject_id = "unresolved:behavior-subject:" + re.sub(r"[^a-z0-9]+", "-", subject_name.lower()).strip("-")[:80]
        behavior_id = x.get("id") or ("behavior:" + str(source_ids[0])) if source_ids else x.get("id")
        if not behavior_id:
            stable_seed = f"{subject_name}|{description}|{index}"
            behavior_id = "behavior:" + re.sub(r"[^a-z0-9]+", "-", stable_seed.lower()).strip("-")[:100]
        evidence = x.get("evidence") or []
        if not evidence and ("component" in x or "behavior" in x or "behaviour" in x):
            evidence = [{"fact": str(description), "claim_status": "observed",
                         "metadata": {"normalization_warning": "canonical_behavior_fields_missing",
                                       "original_payload": x}}]
        behaviors.append(MachineBehavior(
            id=str(behavior_id), source_ids=[str(v) for v in source_ids],
            subject_id=str(subject_id), subject_name=subject_name,
            description=str(description), evidence=_ev(evidence),
        ))
    constraints = [MachineConstraint(
        id=x["id"], source_ids=x.get("source_ids", []), entity_id=x.get("entity_id"), name=x["name"], description=x["description"],
        evidence=_ev(x.get("evidence")),
    ) for x in payload.get("constraints", []) if x.get("id") and x.get("name")]
    procedures = [MachineProcedure(
        id=x["id"], source_ids=x.get("source_ids", []), name=x["name"], procedure_type=x.get("procedure_type", "maintenance"),
        steps=x.get("steps", []), entity_ids=x.get("entity_ids", []), evidence=_ev(x.get("evidence")),
    ) for x in payload.get("procedures", []) if x.get("id") and x.get("name")]
    failures = [MachineFailureMode(
        id=x["id"], source_ids=x.get("source_ids", []), name=x["name"], symptoms=x.get("symptoms", []), possible_causes=x.get("possible_causes", []),
        diagnostic_test=x.get("diagnostic_test"), expected_observation=x.get("expected_observation"),
        repair_procedure_id=x.get("repair_procedure_id"), entity_ids=x.get("entity_ids", []), evidence=_ev(x.get("evidence")),
    ) for x in payload.get("failure_modes", []) if x.get("id") and x.get("name")]
    from backend.knowledge.machine_model import MachineConflict
    conflicts = [MachineConflict(id=x["id"], source_ids=x.get("source_ids", []), subject=x.get("subject", ""), property=x.get("property", ""), values=x.get("values", []), resolution=x.get("resolution"), status=x.get("status", "unresolved"), evidence=_ev(x.get("evidence"))) for x in payload.get("conflicts", []) if x.get("id")]
    return UniversalMachineModel(
        entities=entities, relations=relations, ports=ports, quantities=quantities,
        states=states, events=events, behaviors=behaviors, constraints=constraints,
        procedures=procedures, failure_modes=failures, conflicts=conflicts,
        simulation_rules=payload.get("simulation_rules", []) if isinstance(payload.get("simulation_rules", []), list) else [],
    )


def _source_records(context: dict[str, Any]) -> dict[str, tuple[str, dict[str, Any]]]:
    """Flatten persisted records into a source-id keyed ledger."""
    ledger: dict[str, tuple[str, dict[str, Any]]] = {}
    for kind, rows in context.items():
        for row in rows:
            sid = row.get("source_id")
            if sid:
                ledger[str(sid)] = (kind, row)
    return ledger


def _collect_accounted_source_ids(payload: dict[str, Any]) -> set[str]:
    accounted: set[str] = set()
    for kind, rows in payload.items():
        if kind == "unresolved_facts":
            continue
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict):
                accounted.update(str(x) for x in row.get("source_ids", []) if x)
    accounted.update(str(x.get("source_id")) for x in payload.get("unresolved_facts", []) if isinstance(x, dict) and x.get("source_id"))
    return accounted


def completeness_check(context: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Prove that every persisted source record is accounted for by the candidate model."""
    ledger = _source_records(context)
    accounted = _collect_accounted_source_ids(payload)
    missing_ids = sorted(set(ledger) - accounted)
    unresolved = [
        {"source_id": sid, "fact_type": kind, "payload": row,
         "reason": "not_accounted_for_by_global_integration"}
        for sid in missing_ids for kind, row in [ledger[sid]]
    ]
    # Existing unresolved entries are retained and de-duplicated by source id.
    existing = {str(x.get("source_id")) for x in payload.get("unresolved_facts", []) if isinstance(x, dict)}
    payload["unresolved_facts"] = [x for x in payload.get("unresolved_facts", []) if isinstance(x, dict)] + [x for x in unresolved if x["source_id"] not in existing]
    return {
        "source_total": len(ledger),
        "accounted_total": len(accounted & set(ledger)),
        "missing_count": len(missing_ids),
        "missing_source_ids": missing_ids,
        "complete": not missing_ids,
        "unresolved_count": len(payload["unresolved_facts"]),
    }


def revision_lineage(session: Session, revision_id: str) -> list[Any]:
    """Return root -> target revision lineage, rejecting cycles."""
    result = []
    seen = set()
    current = session.get(__import__("backend.db.models", fromlist=["Revision"]).Revision, revision_id)
    while current is not None:
        if current.id in seen:
            raise ValueError(f"Revision inheritance cycle detected at {current.id}")
        seen.add(current.id)
        result.append(current)
        current = current.parent_revision
    return list(reversed(result))


def _merge_contexts(contexts: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge root->child knowledge; child records shadow identical logical keys."""
    if not contexts:
        return {}
    merged = {k: [] for k in contexts[0]}
    key_fields = {
        "universal_entities": lambda x: (x.get("name", "").strip().lower(), x.get("entity_type", "")),
        "universal_relations": lambda x: (x.get("subject_name", "").strip().lower(), x.get("relation_type", ""), x.get("object_name", "").strip().lower()),
        "universal_facts": lambda x: (x.get("fact_key") or x.get("source_id"),),
        "behaviors": lambda x: (x.get("subject_name", "").strip().lower(), x.get("description", "").strip().lower()),
        "legacy_components": lambda x: (x.get("name", "").strip().lower(), x.get("part_number")),
        "legacy_relationships": lambda x: (x.get("from_component_id"), x.get("relation_type"), x.get("to_component_id")),
        "procedures": lambda x: (x.get("name", "").strip().lower(), x.get("procedure_type")),
        "failure_modes": lambda x: (x.get("symptom", "").strip().lower(), x.get("diagnostic_test", "").strip().lower()),
        "schematic_nodes": lambda x: (x.get("label", "").strip().lower(), x.get("symbol_type")),
        "schematic_edges": lambda x: (x.get("from_node_id"), x.get("wire_type"), x.get("to_node_id")),
        "expert_knowledge": lambda x: (x.get("title", "").strip().lower(), x.get("knowledge_type")),
    }
    for ctx in contexts:
        for kind, rows in ctx.items():
            if kind not in merged:
                merged[kind] = []
            if kind not in key_fields:
                merged[kind].extend(rows); continue
            index = {key_fields[kind](r): i for i, r in enumerate(merged[kind])}
            for row in rows:
                payload = row.get("payload") or {}
                if row.get("fact_type") in {"deprecation", "revision_deprecation"} or payload.get("deprecated") is True:
                    target = payload.get("target_fact_key") or payload.get("fact_key")
                    merged[kind] = [r for r in merged[kind] if r.get("fact_key") != target and r.get("source_id") != target]
                    continue
                key = key_fields[kind](row)
                if key in index:
                    merged[kind][index[key]] = row
                else:
                    index[key] = len(merged[kind]); merged[kind].append(row)
    return merged


def build_effective_revision_knowledge_context(session: Session, revision_id: str) -> tuple[dict[str, Any], dict[str, int], list[str]]:
    lineage = revision_lineage(session, revision_id)
    contexts = [build_revision_knowledge_context(session, rev.id)[0] for rev in lineage]
    merged = _merge_contexts(contexts)
    counts = {k: len(v) for k, v in merged.items()}
    return merged, counts, [r.id for r in lineage]


def _snapshot_fingerprint(model: dict[str, Any]) -> tuple:
    """Model-only fingerprint, excluding completeness/coverage/version metadata."""
    def _names(rows):
        return tuple(sorted((str(r.get("id")), str(r.get("name"))) for r in rows if isinstance(r, dict)))
    return (
        _names(model.get("entities", [])),
        tuple(sorted((str(r.get("subject_id")), str(r.get("relation_type")), str(r.get("object_id"))) for r in model.get("relations", []) if isinstance(r, dict))),
        _names(model.get("failure_modes", [])),
        _names(model.get("procedures", [])),
        tuple(sorted(str(r.get("id")) for r in model.get("quantities", []) if isinstance(r, dict))),
    )


def persist_integration_result(session: Session, revision_id: str, result: IntegrationResult) -> dict[str, Any]:
    """Persist a canonical integration result and map source rows to canonical ids."""
    from backend.db.models import MachineKnowledgeModelSnapshot
    raw = result.payload
    source_maps = {
        "entities": {sid: item["id"] for item in raw.get("entities", []) for sid in item.get("source_ids", [])},
        "relations": {sid: item["id"] for item in raw.get("relations", []) for sid in item.get("source_ids", [])},
        "behaviors": {sid: item["id"] for item in raw.get("behaviors", []) for sid in item.get("source_ids", [])},
        "facts": {sid: item["id"] for item in raw.get("ports", []) + raw.get("quantities", []) + raw.get("events", []) + raw.get("constraints", []) for sid in item.get("source_ids", [])},
    }
    for row in session.query(MachineKnowledgeEntity).filter_by(revision_id=revision_id).all():
        if row.id in source_maps["entities"]: row.canonical_id = source_maps["entities"][row.id]
    for row in session.query(MachineKnowledgeRelation).filter_by(revision_id=revision_id).all():
        if row.id in source_maps["relations"]: row.canonical_id = source_maps["relations"][row.id]
    for row in session.query(MachineKnowledgeBehavior).filter_by(revision_id=revision_id).all():
        if row.id in source_maps["behaviors"]: row.canonical_id = source_maps["behaviors"][row.id]
    for row in session.query(MachineKnowledgeFact).filter_by(revision_id=revision_id).all():
        if row.id in source_maps["facts"]: row.canonical_id = source_maps["facts"][row.id]

    latest = session.query(MachineKnowledgeModelSnapshot).filter_by(revision_id=revision_id).order_by(MachineKnowledgeModelSnapshot.version.desc()).first()
    if latest is not None and _snapshot_fingerprint(latest.model) == _snapshot_fingerprint(raw):
        # Re-running integration on unchanged source data must not inflate
        # the snapshot version or duplicate its model (OMNIPRO v1->v2 grew
        # 32->36 entities with no new source data).
        return {"revision_id": revision_id, "version": latest.version, "source_counts": result.source_counts, "completeness": raw.get("completeness", {}), "revision_lineage": raw.get("revision_lineage", [revision_id]), "model": raw, "unchanged": True}
    snapshot = MachineKnowledgeModelSnapshot(
        revision_id=revision_id,
        version=(latest.version + 1 if latest else 1),
        model=raw,
        source_counts=result.source_counts,
    )
    session.add(snapshot)
    session.flush()
    return {"revision_id": revision_id, "version": snapshot.version, "source_counts": result.source_counts, "completeness": raw.get("completeness", {}), "revision_lineage": raw.get("revision_lineage", [revision_id]), "model": raw}


_INTEGRATION_ROWS_PER_BATCH = 30      # was 80: 80 re-emitted rows overflowed the output cap
_INTEGRATION_MAX_TOKENS = 16000


def _integrate_part(key: str | None, part: dict[str, Any], label: str) -> list[tuple[str, dict[str, Any]]]:
    """Run one integration call. If the JSON is truncated/invalid, split the rows
    of the batch's category in half and retry each half (down to single rows),
    instead of failing the whole ingestion job."""
    prompt = _GLOBAL_PROMPT.format(knowledge=json.dumps(part, ensure_ascii=False, separators=(",", ":")))
    raw = gateway.complete("integration", messages=[{"role": "user", "content": prompt}], max_tokens=_INTEGRATION_MAX_TOKENS)
    if not raw:
        raise ValueError(f"Integration batch {label} returned no output (check API key / quota)")
    parsed = _parse_json(raw)
    if parsed:
        return [(label, parsed)]
    rows = part.get(key) if key else None
    if not isinstance(rows, list) or not rows:
        if key is None:
            # Single full-context batch that failed to parse: split the
            # largest category and retry, rather than failing the whole job.
            split_key = None
            for k, v in part.items():
                if isinstance(v, list) and len(v) > 1:
                    split_key = k
                    break
            if split_key is None:
                raise ValueError(f"Integration batch {label} returned invalid JSON and cannot be split further")
            rows = part[split_key]
            key = split_key
        else:
            raise ValueError(f"Integration batch {label} returned invalid JSON and cannot be split further")
    if len(rows) == 1:
        row = rows[0]
        sid = str(row.get("id") or row.get("source_id") or "") if isinstance(row, dict) else ""
        return [(label, {"unresolved_facts": [{
            "source_id": sid or f"integration:{label}",
            "fact_type": f"integration_{key}",
            "payload": {"value": row},
            "reason": "integration_output_unparseable_after_split",
        }]})]
    mid = len(rows) // 2
    return (_integrate_part(key, {**part, key: rows[:mid]}, label + "a")
            + _integrate_part(key, {**part, key: rows[mid:]}, label + "b"))


def _norm_text(value: object) -> str:
    import re as _re
    return _re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _props_conflict(a: dict, b: dict) -> bool:
    """True if a and b claim different values for the same property key.

    This is what protects two real parts with the same name (two Fans with
    different part numbers) from being merged, while still merging a welder
    that the LLM emitted twice with identical or subset properties.
    """
    if not isinstance(a, dict) or not isinstance(b, dict):
        return False
    for k, v in a.items():
        if k in b and b[k] not in (None, "", {}) and v not in (None, "", {}) and v != b[k]:
            return True
    return False


def _merge_rows_by_identity(rows: list[dict[str, Any]], kind: str = "") -> list[dict[str, Any]]:
    """Collapse rows that share a canonical id WITHOUT losing provenance.

    The old logic kept the first row and silently discarded later ones, so their
    source_ids/evidence vanished (and showed up as 'missing' in the completeness
    audit). Now source_ids and evidence are unioned and missing properties filled in.

    Second pass, name-level, only when it cannot merge two distinct parts:
    two rows with the same normalized name and type, and no conflicting
    property values, are the same fact emitted twice (the OMNIPRO snapshot
    had Mig/Tig/Stick Welder twice each, and 5 failure modes twice). Two Fans
    with different part numbers have conflicting properties and are NOT merged.
    Relations are merged on identical subject/object/type.
    """
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in rows:
        ident = row.get("id") or row.get("source_id") or json.dumps(row, sort_keys=True, ensure_ascii=False)
        keep = merged.get(ident)
        if keep is None:
            merged[ident] = row
            order.append(ident)
            continue
        for list_key in ("source_ids", "evidence"):
            extra = row.get(list_key)
            current = keep.get(list_key)
            if isinstance(extra, list) and extra and isinstance(current, list):
                current.extend(item for item in extra if item not in current)
        if isinstance(keep.get("properties"), dict) and isinstance(row.get("properties"), dict):
            for k, v in row["properties"].items():
                keep["properties"].setdefault(k, v)
    kept = [merged[i] for i in order]

    def _name_key(row: dict) -> tuple:
        if kind == "relations":
            return (_norm_text(row.get("subject_id")), _norm_text(row.get("relation_type")), _norm_text(row.get("object_id")))
        if kind == "failure_modes":
            return (_norm_text(row.get("name")),)
        return (_norm_text(row.get("name")), _norm_text(row.get("entity_type")))

    out: list[dict[str, Any]] = []
    for row in kept:
        if not row.get("name") and kind != "relations":
            out.append(row)
            continue
        target = None
        for cand in out:
            if _name_key(cand) != _name_key(row):
                continue
            if kind == "relations":
                target = cand
                break
            # Name+type match: merge only if properties do not conflict, and
            # for failure modes, causes do not conflict either.
            if _props_conflict(cand.get("properties") or {}, row.get("properties") or {}):
                continue
            if kind == "failure_modes":
                ca, cb = set(cand.get("possible_causes") or []), set(row.get("possible_causes") or [])
                if ca and cb and ca != cb and not (ca <= cb or cb <= ca):
                    continue
            target = cand
            break
        if target is None:
            out.append(row)
            continue
        for list_key in ("source_ids", "evidence", "entity_ids"):
            extra = row.get(list_key)
            current = target.get(list_key)
            if isinstance(extra, list) and extra:
                if isinstance(current, list):
                    current.extend(item for item in extra if item not in current)
                else:
                    target[list_key] = list(extra)
        if isinstance(target.get("properties"), dict) and isinstance(row.get("properties"), dict):
            for k, v in row["properties"].items():
                target["properties"].setdefault(k, v)
        # Keep the longer description / name casing from the first row; do not
        # overwrite a populated field with an empty one.
        for k, v in row.items():
            if target.get(k) in (None, "", []) and v not in (None, "", []):
                target[k] = v
    return out


def _rewrite_references(payload: dict[str, Any], id_map: dict[str, str]) -> None:
    """Rewrite relations/ports/quantities/etc. that point at a merged-away id.

    Without this, merging two entity rows leaves relations pointing at the
    dropped id, which verification then (correctly) flags as missing.
    """
    if not id_map:
        return
    for rel in payload.get("relations", []):
        if rel.get("subject_id") in id_map:
            rel["subject_id"] = id_map[rel["subject_id"]]
        if rel.get("object_id") in id_map:
            rel["object_id"] = id_map[rel["object_id"]]
    for key in ("ports", "quantities", "states", "events"):
        for item in payload.get(key, []):
            if item.get("entity_id") in id_map:
                item["entity_id"] = id_map[item["entity_id"]]
    for item in payload.get("behaviors", []):
        if item.get("subject_id") in id_map:
            item["subject_id"] = id_map[item["subject_id"]]
    for item in payload.get("procedures", []) + payload.get("failure_modes", []):
        ids = item.get("entity_ids") or []
        item["entity_ids"] = [id_map.get(x, x) for x in ids]


def _coverage_for_revision(session: Session, revision_id: str) -> dict[str, int]:
    """Honest coverage, separate from the source-record completeness audit.

    The OMNIPRO snapshot claimed 76/76 complete while 56 chunks had produced
    no facts and all 34 diagrams had no nodes. Completeness counts DB rows;
    coverage counts chunks/diagrams, so a thin model cannot hide behind it.
    """
    from backend.db.models import Chunk, Document, UnresolvedFact
    docs = session.query(Document).filter(Document.revision_id == revision_id).all()
    doc_ids = [d.id for d in docs]
    chunks = session.query(Chunk).filter(Chunk.document_id.in_(doc_ids or [""])).all()
    chunk_ids = [c.id for c in chunks]
    fact_chunks: set[str] = set()
    for model_cls, field in (
        (MachineKnowledgeEntity, "source_chunk_id"),
        (MachineKnowledgeFact, "source_chunk_id"),
        (MachineKnowledgeBehavior, "source_chunk_id"),
        (Component, "source_chunk_id"),
        (Procedure, "source_chunk_id"),
        (FailureMode, "source_chunk_id"),
    ):
        try:
            rows = session.query(model_cls).filter_by(revision_id=revision_id).all()
        except Exception:
            rows = []
        for r in rows:
            if getattr(r, field, None):
                fact_chunks.add(getattr(r, field))
    diagram_chunks = [c for c in chunks if c.chunk_type.value == "diagram"]
    nodes = session.query(SchematicNode).filter(SchematicNode.chunk_id.in_(chunk_ids or [""])).all()
    node_chunks = {n.chunk_id for n in nodes}
    unresolved_open = session.query(UnresolvedFact).filter(
        UnresolvedFact.revision_id == revision_id, UnresolvedFact.status == "open"
    ).count()
    return {
        "total_chunks": len(chunks),
        "diagram_chunks": len(diagram_chunks),
        "text_table_chunks": len(chunks) - len(diagram_chunks),
        "chunks_with_facts": len(fact_chunks & set(chunk_ids)),
        "chunks_empty": len(chunks) - len(fact_chunks & set(chunk_ids)),
        "diagrams_with_nodes": len(node_chunks & {c.id for c in diagram_chunks}),
        "diagrams_empty": len(diagram_chunks) - len(node_chunks & {c.id for c in diagram_chunks}),
        "unresolved_open": unresolved_open,
    }


def integrate_revision_knowledge(session: Session, revision_id: str) -> IntegrationResult:
    context, counts, lineage = build_effective_revision_knowledge_context(session, revision_id)
    total_rows = sum(len(v) for v in context.values())
    output_keys = ("entities", "relations", "ports", "quantities", "states", "events", "behaviors", "constraints", "procedures", "failure_modes", "simulation_rules", "conflicts", "unresolved_facts")
    payload = {k: [] for k in output_keys}
    # Code-first, LLM-last: for a normal manual (<=250 source rows, OMNIPRO
    # had 76), one call sees entities AND relations AND facts together, so
    # the LLM cannot create the same welder in two batches with two ids.
    # Only larger manuals fall back to category batches, and even then
    # non-entity batches see the FULL entity list, not the first 150.
    if total_rows <= 250:
        batches = [(None, context)]
    else:
        category_keys = list(context)
        batches = []
        for key in category_keys:
            rows = context[key]
            for start in range(0, len(rows), _INTEGRATION_ROWS_PER_BATCH):
                part = {k: [] for k in category_keys}
                part[key] = rows[start:start + _INTEGRATION_ROWS_PER_BATCH]
                part["universal_entities"] = context.get("universal_entities", [])
                batches.append((key, part))
    if not batches:
        batches = [(None, context)]
    parsed_parts: list[tuple[str, dict[str, Any]]] = []
    for index, (batch_key, batch) in enumerate(batches):
        parsed_parts.extend(_integrate_part(batch_key, batch, str(index + 1)))
    for index, parsed in parsed_parts:
        for key in output_keys:
            values = parsed.get(key)
            if not isinstance(values, list):
                if values is not None:
                    persist_unresolved_fact(session, revision_id=revision_id, source_id=f"integration:{index}:{key}", fact_type=f"integration_{key}", payload={"value": values}, reason="integration_category_not_a_list")
                continue
            for row_index, item in enumerate(values):
                if not isinstance(item, dict):
                    persist_unresolved_fact(session, revision_id=revision_id, source_id=f"integration:{index}:{key}:{row_index}", fact_type=f"integration_{key}", payload={"value": item}, reason="integration_item_not_an_object")
                    continue
                payload[key].append(normalize_payload(item, kind=key[:-1] if key.endswith("s") else key))
    # Name-level merge + reference rewriting, in code, before any audit.
    # Build the dropped-id map from the pre-merge entity list.
    pre_entities = list(payload.get("entities", []))
    pre_ids = [r.get("id") for r in pre_entities if r.get("id")]
    for key, rows in list(payload.items()):
        payload[key] = _merge_rows_by_identity(rows, kind=key)
    post_entities = payload.get("entities", [])
    post_ids = {r.get("id") for r in post_entities if r.get("id")}
    # Dropped entity id -> kept entity id, only when the kept row is the
    # unique same-name, non-conflicting row for that dropped entity. This
    # is what lets relations survive a name-level merge instead of pointing
    # at a dropped id and then being quarantined as missing.
    id_map: dict[str, str] = {}
    for dropped in pre_entities:
        did = dropped.get("id")
        if not did or did in post_ids:
            continue
        cands = [r for r in post_entities
                 if _norm_text(r.get("name")) == _norm_text(dropped.get("name"))
                 and _norm_text(r.get("entity_type")) == _norm_text(dropped.get("entity_type"))
                 and not _props_conflict(r.get("properties") or {}, dropped.get("properties") or {})]
        if len(cands) == 1:
            id_map[did] = cands[0]["id"]
    _rewrite_references(payload, id_map)
    audit = completeness_check(context, payload)
    model = _model_from_payload(payload)
    # Gap 6 at integration level: per-fact quarantine, not job abort.
    # Invalid bbox, missing-entity relations, duplicate ids are quarantined
    # here; the kept model is what gets snapshotted.
    from backend.knowledge.validation import quarantine_machine_model
    kept_model, quarantined = quarantine_machine_model(model)
    if quarantined:
        kept_payload = {
            "entities": [e.model_dump(mode="json") for e in kept_model.entities],
            "relations": [r.model_dump(mode="json") for r in kept_model.relations],
            "ports": [x.model_dump(mode="json") for x in kept_model.ports],
            "quantities": [x.model_dump(mode="json") for x in kept_model.quantities],
            "states": [x.model_dump(mode="json") for x in kept_model.states],
            "events": [x.model_dump(mode="json") for x in kept_model.events],
            "behaviors": [x.model_dump(mode="json") for x in kept_model.behaviors],
            "constraints": [x.model_dump(mode="json") for x in kept_model.constraints],
            "procedures": [x.model_dump(mode="json") for x in kept_model.procedures],
            "failure_modes": [x.model_dump(mode="json") for x in kept_model.failure_modes],
            "simulation_rules": kept_model.simulation_rules,
            "conflicts": [x.model_dump(mode="json") for x in kept_model.conflicts],
            "unresolved_facts": list(payload.get("unresolved_facts", [])),
        }
        for rec in quarantined:
            kept_payload["unresolved_facts"].append(rec)
        payload = kept_payload
        model = kept_model
    for unresolved in payload.get("unresolved_facts", []):
        if isinstance(unresolved, dict):
            persist_unresolved_fact(session, revision_id=revision_id,
                source_id=str(unresolved.get("source_id") or unresolved.get("id") or ""),
                fact_type=str(unresolved.get("fact_type") or "global_integration"),
                payload=unresolved.get("payload", unresolved),
                reason=str(unresolved.get("reason") or "unresolved_by_global_integration"))
    audit = completeness_check(context, payload)
    payload["completeness"] = audit
    payload["revision_lineage"] = lineage
    try:
        payload["coverage"] = _coverage_for_revision(session, revision_id)
    except Exception:
        payload["coverage"] = {}
    model.unresolved_facts = [__import__("backend.knowledge.machine_model", fromlist=["MachineUnresolvedFact"]).MachineUnresolvedFact.model_validate(x) for x in payload.get("unresolved_facts", []) if isinstance(x, dict)]
    if not model.entities and not model.relations and not model.procedures and not model.failure_modes and not model.unresolved_facts:
        raise ValueError("Global integration returned no canonical machine knowledge")
    counts = dict(counts)
    counts["completeness_missing"] = audit["missing_count"]
    counts["completeness_source_total"] = audit["source_total"]
    counts.update({f"coverage_{k}": v for k, v in (payload.get("coverage") or {}).items()})
    return IntegrationResult(model=model, payload=payload, source_counts=counts)
