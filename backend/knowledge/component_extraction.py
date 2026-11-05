"""LLM-based structured extraction for large semantic manual chunks.

Chunks are deliberately larger and may span pages so the model can see the
context needed to extract useful components, specifications, relationships,
and procedures. Each result is still persisted against one source chunk, so
provenance remains exact at the chunk/page-range level.
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from backend.db.models import (
    Component,
    ComponentRelationship,
    FailureMode,
    MachineKnowledgeBehavior,
    MachineKnowledgeEntity,
    MachineKnowledgeEvidence,
    MachineKnowledgeFact,
    MachineKnowledgeRelation,
    Procedure,
    RelationType,
    ProcedureType,
)
from backend.knowledge.evidence import MachineEvidence
from backend.knowledge.normalization import normalize_payload, persist_unresolved_fact
from backend.knowledge.machine_model import (
    MachineBehavior,
    MachineConstraint,
    MachineEntity,
    MachineEvent,
    MachineFailureMode,
    MachineRelation,
    MachinePort,
    MachineQuantity,
    MachineState,
    UniversalMachineModel,
)
from backend.knowledge.schema import ExtractionResult, MachineKnowledgeExtractionResult, UniversalEntity, UniversalRelation
from backend.llm import gateway
from backend.config import settings


class ExtractionError(RuntimeError):
    """Extraction failure with retry guidance for the ingestion scheduler."""

    def __init__(self, message: str, *, retryable: bool = False, rate_limited: bool = False):
        super().__init__(message)
        self.retryable = retryable
        self.rate_limited = rate_limited


# --- Lenient JSON parsing for LLM extraction output ---------------------
# "Expecting property name enclosed in double quotes" is the JSON parser
# complaining about a property that is not a double-quoted string. In LLM
# output that almost always means one of:
#   * a trailing comma:  {"a": 1,}
#   * single-quoted keys/strings:  {'a': 1}
#   * an unquoted key:  {a: 1}
#   * a // or /* */ comment inside the JSON
# The old code only stripped code fences and brace-hunted, then gave up and
# failed the whole ingestion job for that chunk. The parser below tries, in
# order: strict JSON, trailing-comma repair, Python-literal parsing
# (single quotes + trailing commas), regex repair for unquoted keys, and
# finally ONE LLM repair call that returns only corrected JSON. Only if all
# of those fail does it raise, and the pipeline then records that one chunk
# as failed instead of silently losing it.
import ast as _ast
import re as _json_re


def _strip_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = _json_re.sub(r"^```(?:json)?\s*", "", text)
        text = _json_re.sub(r"\s*```$", "", text)
    return text.strip()


def _brace_hunt(text: str) -> str:
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        return text[start:end + 1]
    return text


def _strict_json(text: str):
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _outside_strings_replace(text: str, mapping: dict[str, str]) -> str:
    """Replace whole words only when they are outside quoted strings."""
    out: list[str] = []
    i = 0
    quote = None
    while i < len(text):
        ch = text[i]
        if quote is not None:
            out.append(ch)
            if ch == "\\" and i + 1 < len(text):
                out.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("\"", "'"):
            quote = ch
            out.append(ch)
            i += 1
            continue
        matched = False
        for src, dst in mapping.items():
            if text.startswith(src, i):
                before = text[i - 1] if i else " "
                after = text[i + len(src)] if i + len(src) < len(text) else " "
                if not (before.isalnum() or before == "_") and not (after.isalnum() or after == "_"):
                    out.append(dst)
                    i += len(src)
                    matched = True
                    break
        if not matched:
            out.append(ch)
            i += 1
    return "".join(out)


def _literal_json(text: str):
    """Parse single-quoted / trailing-comma output via ast.literal_eval."""
    converted = _outside_strings_replace(text, {"true": "True", "false": "False", "null": "None"})
    try:
        parsed = _ast.literal_eval(converted)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _regex_repair_json(text: str):
    """Quote unquoted keys and drop comments, then strict-parse."""
    # Drop // comments outside strings (best-effort; strict parse validates).
    lines = []
    for line in text.splitlines():
        if "//" in line and not line.strip().startswith("http"):
            # Only strip a comment that is not inside a quoted string.
            idx = line.find("//")
            before = line[:idx]
            if before.count('"') % 2 == 0 and before.count("'") % 2 == 0:
                line = before
        lines.append(line)
    repaired = "\n".join(lines)
    repaired = _json_re.sub(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:", r'\1"\2":', repaired)
    repaired = _json_re.sub(r",\s*([}\]])", r"\1", repaired)
    return _strict_json(repaired)


def _parse_lenient_json(raw: str | None, *, allow_repair: bool = True) -> dict:
    if not raw:
        raise ExtractionError("LLM returned no extraction output")
    text = _strip_fence(raw)
    candidates = [text, _brace_hunt(text)]
    for cand in candidates:
        for attempt in (_strict_json, lambda c: _strict_json(_json_re.sub(r",\s*([}\]])", r"\1", c)), _literal_json, _regex_repair_json):
            parsed = attempt(cand)
            if parsed is not None:
                return parsed
    if allow_repair:
        # One repair call: ask the model to fix its own JSON, not to
        # re-extract. This recovers broken strings / unescaped quotes that
        # deterministic repair cannot safely fix.
        try:
            repaired_raw = gateway.complete(
                "extraction",
                messages=[{"role": "user", "content": (
                    "The following is a structured extraction that is not valid JSON. "
                    "Return ONLY the corrected valid JSON in exactly the same shape, "
                    "without adding, removing, or inventing any facts. If a string is "
                    "broken, close it with the content that was present.\n\n" + raw
                )}],
                max_tokens=8000,
            )
        except Exception:
            repaired_raw = None
        if repaired_raw:
            try:
                return _parse_lenient_json(repaired_raw, allow_repair=False)
            except ExtractionError:
                pass
    raise ExtractionError("LLM returned invalid JSON: Expecting property name enclosed in double quotes (unrecoverable after lenient parsing and one repair retry)")


_EXTRACTION_PROMPT = """You are extracting structured technical knowledge from a technical excerpt of an \
industrial equipment manual. The excerpt may span multiple pages. Only extract what is explicitly stated — do not infer or \
invent components, connections, or procedures that aren't clearly described in the text.

If the text contains no extractable technical knowledge (e.g. it's a cover page, \
table of contents, or legal boilerplate), return empty lists.

Text:
---
{content}
---

Respond with ONLY JSON matching this shape (omit fields that don't apply, use empty lists \
where nothing was found). Every extracted item must include an evidence object with fact, \
source_type, confidence from 0.0 to 1.0, claim_status (observed|inferred|uncertain), and extraction_method. Use status KNOWN, UNKNOWN, \
or UNCERTAIN when a value is not explicit:

{{
    "components": [{{"name": "", "function": "", "location_description": "", "part_number": "", "evidence": {{"fact": "", "source_type": "text", "confidence": 0.0, "extraction_method": "llm"}}}}],
    "relationships": [{{"from_component": "", "to_component": "", "relation_type": "electrical|mechanical|fluid|signal|contains", "description": "", "evidence": {{"fact": "", "source_type": "text", "confidence": 0.0, "extraction_method": "llm"}}}}],
    "procedures": [{{"name": "", "procedure_type": "installation|removal|calibration|maintenance|troubleshooting|replacement|verification", "steps": [""]}}],
    "entities": [{{"id": "entity:p1", "name": "", "entity_type": "", "properties": {{}}, "ports": [], "states": [], "evidence": []}}],
    "relations": [{{"subject_id": "", "subject_name": "", "relation_type": "", "object_id": "", "object_name": "", "description": "", "evidence": []}}],
    "ports": [{{"id": "port:p1", "entity_id": "", "name": "", "direction": "", "properties": {{}}, "evidence": []}}],
    "quantities": [{{"id": "quantity:q1", "entity_id": "", "name": "", "value": "", "unit": "", "properties": {{}}, "evidence": []}}],
    "states": [{{"entity_id": "", "name": "", "value": "", "properties": {{}}, "evidence": []}}],
    "events": [{{"id": "event:e1", "entity_id": "", "name": "", "description": "", "evidence": []}}],
    "behaviors": [{{"id": "behavior:b1", "subject_id": "", "subject_name": "", "description": "", "evidence": []}}],
    "constraints": [{{"id": "constraint:c1", "entity_id": "", "name": "", "description": "", "evidence": []}}],
    "failure_modes": [{{"id": "failure:overheating", "name": "", "symptoms": [""], "possible_causes": [""], "diagnostic_test": "", "expected_observation": "", "repair_procedure_name": "", "entity_ids": [], "evidence": []}}]
}}

Also extract failure modes explicitly whenever the text describes a fault, symptom,
troubleshooting entry, or "if X happens, check Y" pattern. A failure mode must
carry its symptoms, possible causes, diagnostic test and expected observation
whenever the text states them; do not invent a cause or test that is not stated.

Evidence shape differs by collection: components and relationships use a single
evidence object, as shown above; entities, relations, ports, quantities, states,
events, behaviors, constraints and failure_modes use a list of evidence objects.
In particular, a failure mode's evidence must be a list, never a single object."""


def _normalize_extraction_item(key: str, item):
    """Normalize one extracted item before schema validation.

    Only conversions that preserve exactly what the model gave are done here:
    - A bare string in a name-only collection (components, entities,
      failure_modes) is a name with no other fields, not a different fact.
      Batch runs returned components as plain strings ("Chipping Hammer");
      converting them to {"name": ...} keeps the component instead of
      failing the whole chunk. Other collections are left untouched: a
      string cannot safely supply a relation's endpoints or a procedure's
      type, so those still fail visibly and split to singles.
    - A quantity's "parameter" field is its name under another label
      (batch runs used {"parameter": "Power Input (Mig)", "value": ...}).
      Copying it to "name" is an alias, not an invented value. The
      original "parameter" field is kept, so nothing is lost.
    Deliberately NOT done here: inventing a procedure_type, an entity_id,
    or a relation endpoint. A missing required field still fails validation
    visibly and triggers the pipeline's split-to-single recovery, rather
    than being silently filled with a guessed value.
    """
    if isinstance(item, str) and item.strip() and key in ("components", "entities", "failure_modes"):
        return {"name": item.strip()}
    if not isinstance(item, dict):
        return item
    if key == "quantities" and not item.get("name") and isinstance(item.get("parameter"), str) and item["parameter"].strip():
        item = {**item, "name": item["parameter"]}
    return normalize_payload(item, kind=key[:-1] if key.endswith("s") else key)


def _extract_result(content: str) -> MachineKnowledgeExtractionResult:
    try:
        raw = gateway.complete("extraction",
            messages=[{"role": "user", "content": _EXTRACTION_PROMPT.format(content=content)}],
            max_tokens=8000,
        )
    except Exception as exc:
        message = str(exc)
        lower = message.lower()
        rate_limited = any(token in lower for token in ("429", "resource_exhausted", "rate limit", "quota exceeded", "too many requests"))
        raise ExtractionError(
            f"LLM extraction request failed: {message}",
            retryable=True,
            rate_limited=rate_limited,
        ) from exc
    payload = _parse_lenient_json(raw)
    try:
        if not isinstance(payload, dict):
            raise ValueError("extraction root must be an object")
        # Apply one shared alias contract to every typed fact collection before validation.
        for key in ("components", "relationships", "procedures", "entities", "relations", "ports", "quantities", "states", "events", "behaviors", "constraints", "failure_modes"):
            if isinstance(payload.get(key), list):
                payload[key] = [_normalize_extraction_item(key, item) for item in payload[key]]
        return MachineKnowledgeExtractionResult.model_validate(payload)
    except ValueError as exc:
        raise ExtractionError(f"LLM extraction schema validation failed: {exc}") from exc


_BATCH_EXTRACTION_PROMPT = """You are extracting structured technical knowledge from MULTIPLE independent excerpts of an industrial equipment manual.

Process every excerpt independently. Do not merge facts between excerpts and do not move a fact from one source_chunk_id to another.
Only extract what is explicitly stated. Do not infer or invent components, connections, or procedures.
If an excerpt contains no extractable technical knowledge (e.g. it is a cover page, table of contents, or legal boilerplate), return empty lists for that excerpt. A specification table, a troubleshooting table, and a procedure are extractable knowledge: do not return empty lists for them merely because they are tables or because a procedure starts mid-way through its steps.

Return ONLY JSON in this shape. Every supplied source_chunk_id must appear exactly once, with its own knowledge object. The knowledge object uses exactly the field shapes below, which are the same shapes as the single-excerpt extraction contract. Do not invent other field names, and do not shorten an item to a bare string:

{{
  "chunks": [
    {
      "source_chunk_id": "the exact supplied id",
      "knowledge": {{
        "components": [{{"name": "", "function": "", "location_description": "", "part_number": "", "evidence": {{"fact": "", "source_type": "text", "confidence": 0.0, "extraction_method": "llm"}}}}],
        "relationships": [{{"from_component": "", "to_component": "", "relation_type": "electrical|mechanical|fluid|signal|contains", "description": "", "evidence": {{"fact": "", "source_type": "text", "confidence": 0.0, "extraction_method": "llm"}}}}],
        "procedures": [{{"name": "", "procedure_type": "installation|removal|calibration|maintenance|troubleshooting|replacement|verification", "steps": [""]}}],
        "entities": [{{"id": "entity:p1", "name": "", "entity_type": "", "properties": {{}}, "ports": [], "states": [], "evidence": []}}],
        "relations": [{{"subject_id": "", "subject_name": "", "relation_type": "", "object_id": "", "object_name": "", "description": "", "evidence": []}}],
        "evidence": [],
        "ports": [{{"id": "port:p1", "entity_id": "", "name": "", "direction": "", "properties": {{}}, "evidence": []}}],
        "states": [{{"entity_id": "", "name": "", "value": "", "properties": {{}}, "evidence": []}}],
        "quantities": [{{"id": "quantity:q1", "entity_id": "", "name": "", "value": "", "unit": "", "properties": {{}}, "evidence": []}}],
        "events": [{{"id": "event:e1", "entity_id": "", "name": "", "description": "", "evidence": []}}],
        "behaviors": [{{"id": "behavior:b1", "subject_id": "", "subject_name": "", "description": "", "evidence": []}}],
        "constraints": [{{"id": "constraint:c1", "entity_id": "", "name": "", "description": "", "evidence": []}}],
        "failure_modes": [{{"id": "failure:overheating", "name": "", "symptoms": [""], "possible_causes": [""], "diagnostic_test": "", "expected_observation": "", "repair_procedure_name": "", "entity_ids": [], "evidence": []}}]
      }}
    }
  ]
}}

Rules that the shape alone does not convey, and that previous batch runs got wrong:
- A component is an object with a "name" field, never a bare string such as "Chipping Hammer". A bare string cannot be validated and loses the whole chunk.
- A procedure always has both "name" and "procedure_type" (from the enum above). Do not use a "procedure_name" field and do not omit "procedure_type".
- A quantity always has "name", "value" and "unit", plus "entity_id" (which may be "" when no entity is named). Do not use a "parameter" field in place of "name".
- Evidence shape differs by collection: components and relationships use a single evidence object, as shown above; entities, relations, ports, quantities, states, events, behaviors, constraints and failure_modes use a list of evidence objects. In particular, a failure mode's evidence must be a list, never a single object, even though a component's evidence is a single object.
- Also extract failure modes explicitly whenever an excerpt describes a fault, symptom, troubleshooting entry, or "if X happens, check Y" pattern. A failure mode must carry its symptoms, possible causes, diagnostic test and expected observation whenever the text states them; do not invent a cause or test that is not stated.
- For a specification table, emit one quantity per numeric specification row (power input, current, duty cycle, OCV, wire speed, capacity, and so on), with the mode or component in the quantity name when the table is split by mode. Do not bury specification values only inside an entity's properties.
- Use empty lists only where nothing was found for that collection in that excerpt, not as the default for a whole excerpt.

EXCERPTS:
__EXCERPTS__
"""


def _parse_json_response(raw: str) -> dict:
    return _parse_lenient_json(raw)


def extract_chunk_knowledge_batch(chunks: list[tuple[str, str]]) -> dict[str, tuple[ExtractionResult, UniversalMachineModel]]:
    """Extract several independent chunks in one provider request.

    The result is keyed by source chunk ID. The batch contract is deliberately
    strict: missing or duplicate IDs cause an ExtractionError so the caller can
    retry with a smaller batch instead of silently losing provenance.
    """
    if not chunks:
        return {}
    if len(chunks) == 1:
        result, model = extract_chunk_knowledge(chunks[0][1])
        return {chunks[0][0]: (result, model)}

    excerpts = "\n\n".join(
        f"===== SOURCE_CHUNK_ID: {chunk_id} =====\n{content}\n===== END SOURCE_CHUNK_ID: {chunk_id} ====="
        for chunk_id, content in chunks
    )
    if len(excerpts) > settings.extraction_batch_max_chars:
        raise ExtractionError("Extraction batch exceeds configured character budget")

    try:
        raw = gateway.complete(
            "extraction",
            messages=[{"role": "user", "content": _BATCH_EXTRACTION_PROMPT.replace("__EXCERPTS__", excerpts)}],
            max_tokens=max(8000, min(16000, len(chunks) * 1000)),
        )
    except Exception as exc:
        message = str(exc)
        lower = message.lower()
        rate_limited = any(token in lower for token in ("429", "resource_exhausted", "rate limit", "quota exceeded", "too many requests"))
        raise ExtractionError(
            f"LLM batch extraction request failed: {message}",
            retryable=True,
            rate_limited=rate_limited,
        ) from exc

    try:
        payload = _parse_json_response(raw)
    except ExtractionError as exc:
        # A malformed/truncated batch response is usually caused by too much
        # output for the selected batch. The caller should split it immediately,
        # not spend two more RPM slots asking for the same malformed response.
        raise ExtractionError(str(exc), retryable=False) from exc
    rows = payload.get("chunks") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ExtractionError("LLM batch extraction response missing chunks list")

    expected = [chunk_id for chunk_id, _ in chunks]
    seen: set[str] = set()
    out: dict[str, tuple[ExtractionResult, UniversalMachineModel]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ExtractionError("LLM batch extraction contained a non-object chunk result")
        chunk_id = str(row.get("source_chunk_id") or "")
        if chunk_id not in expected or chunk_id in seen:
            raise ExtractionError("LLM batch extraction returned an unknown or duplicate source_chunk_id")
        knowledge = row.get("knowledge")
        if not isinstance(knowledge, dict):
            raise ExtractionError(f"LLM batch extraction missing knowledge object for {chunk_id}")
        try:
            for key in ("components", "relationships", "procedures", "entities", "relations", "ports", "quantities", "states", "events", "behaviors", "constraints", "failure_modes"):
                if isinstance(knowledge.get(key), list):
                    knowledge[key] = [_normalize_extraction_item(key, item) for item in knowledge[key]]
            result = MachineKnowledgeExtractionResult.model_validate(knowledge)
        except ValueError as exc:
            raise ExtractionError(f"LLM batch extraction schema validation failed for {chunk_id}: {exc}") from exc
        legacy = _legacy_result(result)
        try:
            # Dict collections (ports, quantities, states, events, behaviors,
            # constraints) are list[dict] at extraction stage, so they are
            # not validated until _universal_model_from_result, where a
            # malformed quantity used to raise an uncaught ValidationError
            # that bypassed the pipeline's split recovery entirely. Wrap it
            # as an ExtractionError so a batch splits to singles instead of
            # failing the job, and a single chunk fails visibly as an
            # ExtractionError rather than an unhandled validation error.
            model = _universal_model_from_result(result) if _has_universal_facts(result) else _legacy_model(result)
        except ValueError as exc:
            raise ExtractionError(f"LLM batch extraction produced an unusable universal fact for {chunk_id}: {exc}") from exc
        out[chunk_id] = (legacy, model)
        seen.add(chunk_id)

    missing = [chunk_id for chunk_id in expected if chunk_id not in seen]
    if missing:
        raise ExtractionError(f"LLM batch extraction omitted {len(missing)} source chunks")
    return out


def _legacy_result(result: MachineKnowledgeExtractionResult) -> ExtractionResult:
    return ExtractionResult(
        components=result.components,
        relationships=result.relationships,
        procedures=result.procedures,
    )


def extract_chunk_knowledge(content: str) -> tuple[ExtractionResult, UniversalMachineModel]:
    """Extract legacy Product Brain rows and universal facts in one provider call."""
    result = _extract_result(content)
    legacy = _legacy_result(result)
    if _has_universal_facts(result):
        try:
            return legacy, _universal_model_from_result(result)
        except ValueError as exc:
            # Same dict-collection validation gap as the batch path: turn
            # an uncaught validation error into a handled ExtractionError
            # so the pipeline records it for this chunk instead of failing
            # with an unhandled exception.
            raise ExtractionError(f"LLM extraction produced an unusable universal fact: {exc}") from exc
    return legacy, _legacy_model(result)


def _has_universal_facts(result: MachineKnowledgeExtractionResult) -> bool:
    return any((
        result.entities, result.relations, result.ports, result.quantities,
        result.states, result.events, result.behaviors, result.constraints,
        result.failure_modes,
    ))


def extract_from_chunk_text(content: str) -> ExtractionResult:
    return extract_chunk_knowledge(content)[0]


def persist_extraction(
    session: Session,
    revision_id: str,
    source_chunk_id: str,
    result: ExtractionResult,
) -> None:
    """Write extracted components/relationships/procedures, linked back to their source chunk."""
    name_to_id: dict[str, str] = {}

    for comp in result.components:
        row = Component(
            revision_id=revision_id,
            name=comp.name,
            function=comp.function,
            location_description=comp.location_description,
            part_number=comp.part_number,
            source_chunk_id=source_chunk_id,
        )
        session.add(row)
        session.flush()  # get row.id without committing
        name_to_id[comp.name] = row.id

    for rel in result.relationships:
        from_id = name_to_id.get(rel.from_component)
        to_id = name_to_id.get(rel.to_component)
        if not (from_id and to_id):
            # FIX: do not silently drop cross-chunk relationships. Queue them
            # as pending links; the pipeline resolves them after ALL chunks
            # are extracted, against the revision-wide component index, and
            # leaves genuinely unresolvable ones open (never guessed).
            persist_unresolved_fact(
                session, revision_id=revision_id,
                source_id=f"pending-rel:{source_chunk_id}:{rel.from_component}->{rel.to_component}:{rel.relation_type}",
                fact_type="pending_relationship",
                payload={
                    "from_component": rel.from_component,
                    "to_component": rel.to_component,
                    "relation_type": rel.relation_type,
                    "description": rel.description,
                    "source_chunk_id": source_chunk_id,
                },
                reason="cross_chunk_component_not_in_chunk",
            )
            continue
        try:
            relation_type = RelationType(rel.relation_type)
        except ValueError:
            persist_unresolved_fact(
                session, revision_id=revision_id,
                source_id=f"pending-rel:{source_chunk_id}:{rel.from_component}->{rel.to_component}:{rel.relation_type}",
                fact_type="pending_relationship",
                payload={
                    "from_component": rel.from_component,
                    "to_component": rel.to_component,
                    "relation_type": rel.relation_type,
                    "description": rel.description,
                    "source_chunk_id": source_chunk_id,
                },
                reason="invalid_relation_type",
            )
            continue
        session.add(
            ComponentRelationship(
                from_component_id=from_id,
                to_component_id=to_id,
                relation_type=relation_type,
                description=rel.description,
                source_chunk_id=source_chunk_id,
            )
        )

    for proc in result.procedures:
        try:
            procedure_type = ProcedureType(proc.procedure_type)
        except ValueError:
            continue
        session.add(
            Procedure(
                revision_id=revision_id,
                name=proc.name,
                procedure_type=procedure_type,
                steps=proc.steps,
                source_chunk_id=source_chunk_id,
            )
        )

    # The pipeline commits the complete extraction in one transaction. This
    # prevents retries from accumulating a partially extracted Product Brain.
    session.flush()


def extract_machine_knowledge_from_chunk(content: str) -> UniversalMachineModel:
    """Build a Phase 8A universal machine model from text evidence.

    This compatibility layer intentionally keeps the legacy extractor intact
    while exposing the universal representation expected by the Phase 8A docs.
    """
    return extract_chunk_knowledge(content)[1]


def _legacy_model(result: MachineKnowledgeExtractionResult) -> UniversalMachineModel:
    legacy = _legacy_result(result)
    model = UniversalMachineModel()
    for comp in legacy.components:
        evidence = [MachineEvidence(
            fact=f"Component {comp.name} extracted from manual text",
            source_type="text",
            confidence=0.7,
            extraction_method="legacy_component_extractor",
        )]
        model.entities.append(MachineEntity(
            id=f"entity:{comp.name.lower().replace(' ', '-')}",
            name=comp.name,
            entity_type="component",
            properties={
                "function": comp.function,
                "location_description": comp.location_description,
                "part_number": comp.part_number,
            },
            evidence=evidence,
        ))
    for rel in legacy.relationships:
        model.relations.append(MachineRelation(
            subject_id=f"entity:{rel.from_component.lower().replace(' ', '-')}",
            subject_name=rel.from_component,
            relation_type=rel.relation_type,
            object_id=f"entity:{rel.to_component.lower().replace(' ', '-')}",
            object_name=rel.to_component,
            description=rel.description,
            evidence=[MachineEvidence(
                fact=f"{rel.from_component} {rel.relation_type} {rel.to_component}",
                source_type="text",
                confidence=0.7,
                extraction_method="legacy_relationship_extractor",
            )],
        ))
    for proc in legacy.procedures:
        model.behaviors.append(MachineBehavior(
            id=f"behavior:{proc.name.lower().replace(' ', '-')}",
            subject_id="",
            subject_name=proc.name,
            description="Procedure: " + "; ".join(proc.steps),
            evidence=[MachineEvidence(
                fact=f"Procedure {proc.name} extracted",
                source_type="text",
                confidence=0.6,
                extraction_method="legacy_procedure_extractor",
            )],
        ))
    return model


def _universal_model_from_result(result: MachineKnowledgeExtractionResult) -> UniversalMachineModel:
    """Convert the provider-neutral extraction contract into typed primitives.

    Legacy components/relationships/procedures are merged in when they
    are not already present as universal items. Previously, a chunk
    with any universal fact (including a failure mode) took this path
    and its legacy components were silently dropped from the universal
    model -- in crater-2.db, pages 39/42/43 produced 7/6/64 legacy
    components but 0 universal entities, while page 46 (no failure
    modes) took the legacy path and its 12 components became entities.
    """
    model = UniversalMachineModel()
    for item in result.entities:
        model.entities.append(MachineEntity.model_validate(item.model_dump()))
    for item in result.relations:
        model.relations.append(MachineRelation.model_validate(item.model_dump()))
    for item in result.ports:
        model.ports.append(MachinePort.model_validate(item))
    for item in result.quantities:
        model.quantities.append(MachineQuantity.model_validate(item))
    for item in result.states:
        model.states.append(MachineState.model_validate(item))
    for item in result.events:
        model.events.append(MachineEvent.model_validate(item))
    for item in result.behaviors:
        model.behaviors.append(MachineBehavior.model_validate(item))
    for item in result.constraints:
        model.constraints.append(MachineConstraint.model_validate(item))
    for item in result.failure_modes:
        model.failure_modes.append(MachineFailureMode.model_validate(item.model_dump()))
    # Merge legacy items that universal extraction did not already cover.
    legacy_model = _legacy_model(result)
    existing_entity_names = {e.name.strip().lower() for e in model.entities}
    for entity in legacy_model.entities:
        if entity.name.strip().lower() not in existing_entity_names:
            model.entities.append(entity)
            existing_entity_names.add(entity.name.strip().lower())
    existing_relations = {(r.subject_name.strip().lower(), r.relation_type, r.object_name.strip().lower()) for r in model.relations}
    for relation in legacy_model.relations:
        key = (relation.subject_name.strip().lower(), relation.relation_type, relation.object_name.strip().lower())
        if key not in existing_relations:
            model.relations.append(relation)
            existing_relations.add(key)
    existing_behaviors = {(b.subject_name.strip().lower(), b.description.strip().lower()) for b in model.behaviors}
    for behavior in legacy_model.behaviors:
        key = (behavior.subject_name.strip().lower(), behavior.description.strip().lower())
        if key not in existing_behaviors:
            model.behaviors.append(behavior)
            existing_behaviors.add(key)
    # Legacy procedures as typed procedures (in addition to the behavior
    # form _legacy_model produces), so they survive in universal facts.
    existing_procedures = {p.name.strip().lower() for p in model.procedures}
    for proc in result.procedures:
        if proc.name.strip().lower() not in existing_procedures:
            from backend.knowledge.machine_model import MachineProcedure
            model.procedures.append(MachineProcedure(
                id=f"procedure:{proc.name.lower().replace(' ', '-')}",
                name=proc.name, procedure_type=proc.procedure_type or "maintenance",
                steps=list(proc.steps or []), entity_ids=[],
            ))
            existing_procedures.add(proc.name.strip().lower())
    model.evidence.extend(result.evidence)
    return model


def persist_machine_knowledge(session: Session, revision_id: str | None, source_chunk_id: str | None, model: UniversalMachineModel) -> None:
    """Persist the universal machine model into project tables for later graph/RAG work."""
    for entity in model.entities:
        db_entity = MachineKnowledgeEntity(
            revision_id=revision_id,
            name=entity.name,
            entity_type=entity.entity_type,
            properties=entity.properties,
            ports=entity.ports,
            states=entity.states,
            source_chunk_id=source_chunk_id,
        )
        session.add(db_entity)
        session.flush()
        _persist_evidence(session, "entity", db_entity.id, entity.evidence)

    for rel in model.relations:
        db_rel = MachineKnowledgeRelation(
            revision_id=revision_id,
            subject_id=rel.subject_id,
            subject_name=rel.subject_name,
            relation_type=rel.relation_type,
            object_id=rel.object_id,
            object_name=rel.object_name,
            description=rel.description,
            source_chunk_id=source_chunk_id,
        )
        session.add(db_rel)
        session.flush()
        _persist_evidence(session, "relation", db_rel.id, rel.evidence)

    for behavior in model.behaviors:
        db_behavior = MachineKnowledgeBehavior(
            revision_id=revision_id,
            subject_id=behavior.subject_id,
            subject_name=behavior.subject_name,
            description=behavior.description,
            source_chunk_id=source_chunk_id,
        )
        session.add(db_behavior)
        session.flush()
        _persist_evidence(session, "behavior", db_behavior.id, behavior.evidence)

    # Legacy FailureMode rows, for the diagnostic agent which queries that
    # table (not just universal facts). Symptom text keeps the failure-mode
    # name plus its symptoms, so neither is lost in the single text column.
    for fm in model.failure_modes:
        symptom_text = fm.name
        if fm.symptoms:
            symptom_text = fm.name + ": " + "; ".join(fm.symptoms)
        session.add(FailureMode(
            revision_id=revision_id,
            symptom=symptom_text,
            possible_causes=list(fm.possible_causes or []),
            diagnostic_test=fm.diagnostic_test,
            expected_observation=fm.expected_observation,
            repair_procedure_id=None,
            source_chunk_id=source_chunk_id,
        ))
        session.flush()

    for fact_type, fact_key, payload in model.all_facts():
        if fact_type == "behavior":
            continue  # Behaviors already have the dedicated legacy table above.
        session.add(MachineKnowledgeFact(
            revision_id=revision_id,
            fact_type=fact_type,
            fact_key=fact_key,
            payload=payload,
            source_chunk_id=source_chunk_id,
        ))
        _persist_evidence_dicts(session, fact_type, fact_key, payload.get("evidence", []))

    _persist_evidence(session, "model", source_chunk_id, model.evidence)

    session.flush()


def _persist_evidence(session: Session, item_type: str, item_id: str | None, evidence: list[MachineEvidence]) -> None:
    for ev in evidence:
        session.add(MachineKnowledgeEvidence(
            item_type=item_type,
            item_id=item_id,
            fact=ev.fact,
            source_document=ev.source_document,
            page=ev.page,
            chunk=ev.chunk,
            source_type=ev.source_type,
            location=ev.location,
            region=ev.region,
            confidence=ev.confidence,
            extraction_method=ev.extraction_method,
            claim_status=ev.claim_status,
            evidence_metadata=ev.metadata,
        ))


def _persist_evidence_dicts(session: Session, item_type: str, item_id: str, evidence: list[dict]) -> None:
    for item in evidence:
        session.add(MachineKnowledgeEvidence(
            item_type=item_type,
            item_id=item_id,
            fact=item.get("fact", f"{item_type}:{item_id}"),
            source_document=item.get("source_document"),
            page=item.get("page"),
            chunk=item.get("chunk"),
            source_type=item.get("source_type"),
            location=item.get("location"),
            region=item.get("region"),
            confidence=item.get("confidence", 0.0),
            extraction_method=item.get("extraction_method"),
            claim_status=item.get("claim_status", "observed"),
            evidence_metadata=item.get("metadata", {}),
        ))


def extract_machine_knowledge(content: str) -> MachineKnowledgeExtractionResult:
    """Structured result shape that matches the Phase 8A schema contract."""
    result = _extract_result(content)
    if _has_universal_facts(result):
        return result

    legacy = _legacy_result(result)
    for comp in legacy.components:
        result.entities.append(UniversalEntity(
            id=f"entity:{comp.name.lower().replace(' ', '-')}",
            name=comp.name,
            entity_type="component",
            properties={
                "function": comp.function,
                "location_description": comp.location_description,
                "part_number": comp.part_number,
            },
            ports=[],
            states=[],
        ))
    for rel in legacy.relationships:
        result.relations.append(UniversalRelation(
            subject_id=f"entity:{rel.from_component.lower().replace(' ', '-')}",
            subject_name=rel.from_component,
            relation_type=rel.relation_type,
            object_id=f"entity:{rel.to_component.lower().replace(' ', '-')}",
            object_name=rel.to_component,
            description=rel.description,
        ))
    return result
