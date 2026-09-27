"""
Top-level ingestion pipeline: PDF -> pages -> chunks -> (SQL + Qdrant) ->
optional knowledge extraction and schematic extraction.

The progress callback is intentionally lightweight: the API owns persistence
of job state while this module reports meaningful pipeline milestones.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

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

ProgressCallback = Callable[[int, str], None]


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
    progress_callback: ProgressCallback | None = None,
) -> Document:
    pdf_path = Path(pdf_path)

    def progress(percent: int, label: str) -> None:
        if progress_callback:
            progress_callback(percent, label)

    progress(5, "Extracting PDF text, tables and images")
    pages = load_pdf(pdf_path, image_out_dir)
    progress(18, f"PDF extracted — {len(pages)} pages")

    if existing_document_id:
        document = session.get(Document, existing_document_id)
        if document is not None:
            progress(100, "Existing document already ingested")
            return document

    document = Document(
        revision_id=revision_id,
        doc_type=doc_type,
        title=title,
        source_path=str(pdf_path),
        page_count=len(pages),
    )
    session.add(document)
    session.flush()

    all_chunks: list[Chunk] = []
    total_pages = max(1, len(pages))
    for page_index, page in enumerate(pages, start=1):
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
        page_percent = 18 + int((page_index / total_pages) * 12)
        progress(page_percent, f"Creating chunks — page {page_index}/{len(pages)}")

    session.flush()
    session.commit()
    progress(32, f"Created {len(all_chunks)} chunks")

    if all_chunks:
        progress(34, f"Indexing {len(all_chunks)} chunks in vector search")
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
    progress(40, "Vector index ready")

    if run_knowledge_extraction:
        text_chunks = [c for c in all_chunks if c.content.strip()]
        total = len(text_chunks)
        progress(42, f"Extracting machine knowledge — 0/{total} chunks")
        for index, chunk in enumerate(text_chunks, start=1):
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

            percent = 42 + int((index / max(1, total)) * 33)
            progress(percent, f"Extracting machine knowledge — {index}/{total} chunks")
    else:
        progress(75, "Machine knowledge extraction skipped")

    progress(76, "Machine knowledge extraction complete")

    diagram_chunks = [
        c for c in all_chunks
        if c.chunk_type.value == "diagram" and c.extra and c.extra.get("image_path")
    ]
    if run_schematic_extraction and diagram_chunks:
        total = len(diagram_chunks)
        progress(78, f"Analyzing schematics — 0/{total} diagrams")
        for index, chunk in enumerate(diagram_chunks, start=1):
            schematic_result = extract_schematic(chunk.extra["image_path"])
            if schematic_result.nodes:
                persist_schematic(session, document.id, chunk.id, revision_id, schematic_result)
                schematic_model = schematic_to_machine_model(
                    schematic_result, document.title, chunk.page_number, chunk.id
                )
                if not validate_machine_model(schematic_model):
                    persist_machine_knowledge(session, revision_id, chunk.id, schematic_model)
            percent = 78 + int((index / max(1, total)) * 12)
            progress(percent, f"Analyzing schematics — {index}/{total} diagrams")
    else:
        progress(90, "No schematic extraction required")

    progress(92, "Saving machine knowledge")
    session.commit()
    progress(96, "Finalizing ingestion")
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
