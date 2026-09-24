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
8. Preserve evidence by copying source evidence references into the canonical objects.
9. Every persisted source record must be referenced by source_ids on at least one canonical item. Never silently drop a source record.
10. If a source record cannot be canonically integrated, put it in unresolved_facts with its source_id, fact_type and original payload.
11. The output must contain only the requested JSON.

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
            return json.loads(text[start:end + 1])
        return {}


def _ev(raw: list[dict[str, Any]] | None) -> list[MachineEvidence]:
    return [MachineEvidence.model_validate(x) for x in (raw or [])]


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
    behaviors = [MachineBehavior(
        id=x["id"], source_ids=x.get("source_ids", []), subject_id=x["subject_id"], subject_name=x["subject_name"], description=x["description"],
        evidence=_ev(x.get("evidence")),
    ) for x in payload.get("behaviors", []) if x.get("id") and x.get("subject_id")]
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


def integrate_revision_knowledge(session: Session, revision_id: str) -> IntegrationResult:
    context, counts, lineage = build_effective_revision_knowledge_context(session, revision_id)
    prompt = _GLOBAL_PROMPT.format(knowledge=json.dumps(context, ensure_ascii=False, separators=(",", ":")))
    raw = gateway.complete("integration",messages=[{"role": "user", "content": prompt}], max_tokens=12000)
    payload = _parse_json(raw)
    audit = completeness_check(context, payload)
    payload["completeness"] = audit
    payload["revision_lineage"] = lineage
    model = _model_from_payload(payload)
    model.unresolved_facts = [__import__("backend.knowledge.machine_model", fromlist=["MachineUnresolvedFact"]).MachineUnresolvedFact.model_validate(x) for x in payload.get("unresolved_facts", [])]
    if not model.entities and not model.relations and not model.procedures and not model.failure_modes and not model.unresolved_facts:
        raise ValueError("Global integration returned no canonical machine knowledge")
    counts = dict(counts)
    counts["completeness_missing"] = audit["missing_count"]
    counts["completeness_source_total"] = audit["source_total"]
    return IntegrationResult(model=model, payload=payload, source_counts=counts)
