"""
LLM-based structured extraction: turns raw chunk text into Component /
ComponentRelationship / Procedure rows (plan.md §3-4).

This is intentionally a thin, single-purpose call — extract from ONE
chunk at a time, with the chunk id carried through as source_chunk_id
so every extracted fact stays traceable to its evidence (plan.md §8:
answers must expose "source document / page / section"). Batching
chunks together for efficiency is a reasonable later optimization, but
it makes provenance fuzzier, so it isn't the MVP default.

Extraction quality on tables/diagram-caption chunks will be weaker than
on prose text chunks — that's expected at this stage; treat low-yield
extractions as a retrieval-quality signal, not a bug to chase yet.
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from backend.db.models import Component, ComponentRelationship, Procedure, RelationType, ProcedureType
from backend.llm import get_llm_provider
from backend.knowledge.schema import ExtractionResult

_EXTRACTION_PROMPT = """You are extracting structured technical knowledge from one page of an \
industrial equipment manual. Only extract what is explicitly stated — do not infer or \
invent components, connections, or procedures that aren't clearly described in the text.

If the text contains no extractable technical knowledge (e.g. it's a cover page, \
table of contents, or legal boilerplate), return empty lists.

Text:
---
{content}
---

Respond with ONLY JSON matching this shape (omit fields that don't apply, use empty lists \
where nothing was found):

{{
  "components": [{{"name": "", "function": "", "location_description": "", "part_number": ""}}],
  "relationships": [{{"from_component": "", "to_component": "", "relation_type": "electrical|mechanical|fluid|signal|contains", "description": ""}}],
  "procedures": [{{"name": "", "procedure_type": "installation|removal|calibration|maintenance|troubleshooting|replacement|verification", "steps": [""]}}]
}}"""


def extract_from_chunk_text(content: str) -> ExtractionResult:
    raw = get_llm_provider().complete(
        messages=[{"role": "user", "content": _EXTRACTION_PROMPT.format(content=content)}],
        max_tokens=2000,
    )
    if not raw:
        return ExtractionResult()
    try:
        return ExtractionResult.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValueError):
        return ExtractionResult()


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
            # Referenced a component not extracted in this same chunk (common —
            # relationships often span text extracted from other pages). Skip
            # rather than guess; a later cross-chunk linking pass can resolve
            # these by name within the same revision.
            continue
        try:
            relation_type = RelationType(rel.relation_type)
        except ValueError:
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
