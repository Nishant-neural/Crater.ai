"""
The Phase-1 payoff endpoint: ask a question, get back evidence-grounded
chunks (not yet a diagnostic answer — that's Phase 2's Diagnostic Agent).

Response shape deliberately mirrors plan.md §8 (Evidence-Grounded
Answers): every result carries its source document, page, and
product/revision scope, so nothing here can be mistaken for an
unsourced claim.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.db.models import DocType
from backend.retrieval import ingestion_client as ingestion_client_mod
from backend.retrieval.hybrid import retrieve_revision_context

router = APIRouter(prefix="/query", tags=["query"])


class QueryRequest(BaseModel):
    question: str
    revision_id: str
    doc_type: DocType | None = None


@router.post("")
def query(payload: QueryRequest):
    # Phase 3 diagnosis-agent extraction: the revision check and the
    # evidence both come from ingestion's HTTP API — no Revision /
    # Document DB reads here. An ingestion failure raises
    # IngestionUnavailableError (503, ingestion named).
    client = ingestion_client_mod.get_ingestion_client()
    summary = client.get_revision_summary(payload.revision_id)
    if summary is None:
        raise HTTPException(404, "Revision not found")

    results, knowledge = retrieve_revision_context(
        query=payload.question, product_id=summary.get("product_id"),
        revision_id=payload.revision_id, doc_type=payload.doc_type.value if payload.doc_type else None,
        client=client,
    )

    evidence = []
    for r in results:
        # Source document title / doc_type ride on the chunk row
        # ingestion's API returned — no Document DB read.
        evidence.append(
            {
                "chunk_id": r.chunk_id,
                "chunk_type": r.chunk_type,
                "content": r.content,
                "page_number": r.page_number,
                "source_document": r.source_document,
                "doc_type": r.doc_type,
                "revision_id": payload.revision_id,
                "revision_label": summary.get("revision_label"),
            }
        )

    return {
        "question": payload.question,
        "product_id": summary.get("product_id"),
        "revision_id": payload.revision_id,
        "revision_label": summary.get("revision_label"),
        "evidence": evidence,
        "machine_knowledge": [
            {"kind": k.kind, "item_id": k.item_id, "score": k.score, "payload": k.payload,
             "product_id": k.product_id, "revision_id": k.revision_id, "revision_label": k.revision_label, "retrieval_path": k.retrieval_path}
            for k in knowledge
        ],
    }
