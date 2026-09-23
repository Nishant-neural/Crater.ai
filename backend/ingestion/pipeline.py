"""
Top-level ingestion pipeline: PDF -> pages -> chunks -> (SQL + Qdrant) ->
optional knowledge extraction.

This is the single entry point both the CLI (scripts/ingest_docs.py) and
the API (/ingest route) call, so there's exactly one place that defines
"what ingesting a document means."
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from backend.schematic.vision_extraction import extract_schematic
from backend.db.models import Chunk, Document, DocType
from backend.ingestion.chunking import page_to_chunks
from backend.ingestion.pdf_loader import load_pdf
from backend.knowledge.component_extraction import (
    extract_chunk_knowledge,
    persist_extraction,
    persist_machine_knowledge,
)
from backend.knowledge.validation import validate_machine_model
from backend.retrieval.vector_store import upsert_chunks
from backend.schematic.graph import persist_schematic, schematic_to_machine_model
from backend.knowledge.evidence import MachineEvidence


def ingest_pdf(
    session: Session,
    pdf_path: str | Path,
    revision_id: str,
    product_id: str,
    doc_type: DocType,
    title: str,
    image_out_dir: str | Path,
    run_knowledge_extraction: bool = True,
    run_schematic_extraction: bool = True,
    existing_document_id: str | None = None,
) -> Document:
    pdf_path = Path(pdf_path)
    pages = load_pdf(pdf_path, image_out_dir)

    if existing_document_id:
        document = session.get(Document, existing_document_id)
        if document is not None:
            return document

    document = Document(
        revision_id=revision_id,
        doc_type=doc_type,
        title=title,
        source_path=str(pdf_path),
        page_count=len(pages),
    )
    session.add(document)
    session.flush()  # get document.id

    all_chunks: list[Chunk] = []
    for page in pages:
        for pending in page_to_chunks(str(pdf_path), page):
            row = Chunk(
                document_id=document.id,
                chunk_type=pending.chunk_type,
                page_number=pending.page_number,
                content=pending.content,
                extra=pending.extra,
            )
            session.add(row)
            all_chunks.append(row)

    session.flush()  # get chunk ids
    session.commit()

    # Index into the vector store (BM25 reads straight from SQL, no separate step needed).
    if all_chunks:
        upsert_chunks(
            chunk_ids=[c.id for c in all_chunks],
            texts=[c.content for c in all_chunks],
            payloads=[
                {
                    "product_id": product_id,
                    "revision_id": revision_id,
                    "doc_type": doc_type.value,
                    "chunk_type": c.chunk_type.value,
                    "document_id": document.id,
                }
                for c in all_chunks
            ],
        )

    # Knowledge extraction (components/relationships/procedures), text chunks only —
    # tables/diagrams are noisier extraction targets and are deferred (see
    # component_extraction.py docstring).
    if run_knowledge_extraction:
        for chunk in all_chunks:
            if not chunk.content.strip():
                continue
            result, machine_model = extract_chunk_knowledge(chunk.content)
            if result and (result.components or result.relationships or result.procedures):
                persist_extraction(session, revision_id, chunk.id, result)

            _stamp_provenance(machine_model, document.title, chunk.page_number, chunk.id, chunk.chunk_type.value)
            if (machine_model.entities or machine_model.relations or machine_model.ports or
                    machine_model.quantities or machine_model.states or machine_model.events or
                    machine_model.behaviors or machine_model.constraints):
                issues = validate_machine_model(machine_model)
                if not issues:
                    persist_machine_knowledge(session, revision_id, chunk.id, machine_model)

    # Schematic parsing (plan.md §5), diagram chunks only, run AFTER text
    # knowledge extraction above so component-name matching in
    # persist_schematic has something to match against.
    if run_schematic_extraction:
        for chunk in all_chunks:
            if chunk.chunk_type.value != "diagram" or not chunk.extra or not chunk.extra.get("image_path"):
                continue
            schematic_result = extract_schematic(chunk.extra["image_path"])
            if schematic_result.nodes:
                persist_schematic(session, document.id, chunk.id, revision_id, schematic_result)
                schematic_model = schematic_to_machine_model(
                    schematic_result, document.title, chunk.page_number, chunk.id
                )
                if not validate_machine_model(schematic_model):
                    persist_machine_knowledge(session, revision_id, chunk.id, schematic_model)

    session.commit()

    return document


def _stamp_provenance(model, source_document: str, page: int | None, chunk_id: str, source_type: str) -> None:
    """Guarantee source metadata even when a provider omits optional evidence."""
    def evidence(fact: str) -> MachineEvidence:
        return MachineEvidence(
            fact=fact, source_document=source_document, page=page, chunk=chunk_id,
            source_type=source_type, confidence=0.7, extraction_method="llm_extraction",
        )

    for entity in model.entities:
        if not entity.evidence:
            entity.evidence.append(evidence(f"Entity {entity.name}"))
    for relation in model.relations:
        if not relation.evidence:
            relation.evidence.append(evidence(f"{relation.subject_name} {relation.relation_type} {relation.object_name}"))
    for collection, label in (
        (model.ports, "Port"), (model.quantities, "Quantity"), (model.states, "State"),
        (model.events, "Event"), (model.behaviors, "Behavior"), (model.constraints, "Constraint"),
    ):
        for item in collection:
            if not item.evidence:
                item.evidence.append(evidence(f"{label} {getattr(item, 'name', getattr(item, 'id', 'unknown'))}"))
    if not model.evidence:
        model.evidence.append(evidence("Universal machine extraction"))
