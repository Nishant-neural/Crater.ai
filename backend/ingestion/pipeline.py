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

from crater.db.models import Chunk, Document, DocType
from crater.ingestion.chunking import page_to_chunks
from crater.ingestion.pdf_loader import load_pdf
from crater.knowledge.component_extraction import extract_from_chunk_text, persist_extraction
from crater.retrieval.vector_store import upsert_chunks
from crater.schematic.graph import persist_schematic
from crater.schematic.vision_extraction import extract_schematic


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
) -> Document:
    pdf_path = Path(pdf_path)
    pages = load_pdf(pdf_path, image_out_dir)

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
            if chunk.chunk_type.value != "text" or len(chunk.content.strip()) < 100:
                continue
            result = extract_from_chunk_text(chunk.content)
            if result.components or result.relationships or result.procedures:
                persist_extraction(session, revision_id, chunk.id, result)

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

    return document
