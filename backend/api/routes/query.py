"""
The Phase-1 payoff endpoint: ask a question, get back evidence-grounded
chunks (not yet a diagnostic answer — that's Phase 2's Diagnostic Agent).

Response shape deliberately mirrors plan.md §8 (Evidence-Grounded
Answers): every result carries its source document, page, and
product/revision scope, so nothing here can be mistaken for an
unsourced claim.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.db.models import Document, DocType, Revision
from backend.db.session import get_session
from backend.retrieval.hybrid import retrieve_revision_context

router = APIRouter(prefix="/query", tags=["query"])


class QueryRequest(BaseModel):
    question: str
    revision_id: str
    doc_type: DocType | None = None


@router.post("")
def query(payload: QueryRequest, session: Session = Depends(get_session)):
    revision = session.get(Revision, payload.revision_id)
    if not revision:
        raise HTTPException(404, "Revision not found")

    results, knowledge = retrieve_revision_context(
        session=session, query=payload.question, product_id=revision.product_id,
        revision_id=payload.revision_id, doc_type=payload.doc_type.value if payload.doc_type else None,
    )

    evidence = []
    for r in results:
        doc = session.get(Document, r.document_id)
        evidence.append(
            {
                "chunk_id": r.chunk_id,
                "chunk_type": r.chunk_type,
                "content": r.content,
                "page_number": r.page_number,
                "source_document": doc.title if doc else None,
                "doc_type": doc.doc_type.value if doc else None,
                "revision_id": revision.id,
                "revision_label": revision.label,
            }
        )

    return {
        "question": payload.question,
        "product_id": revision.product_id,
        "revision_id": revision.id,
        "revision_label": revision.label,
        "evidence": evidence,
        "machine_knowledge": [
            {"kind": k.kind, "item_id": k.item_id, "score": k.score, "payload": k.payload,
             "product_id": k.product_id, "revision_id": k.revision_id, "revision_label": k.revision_label, "retrieval_path": k.retrieval_path}
            for k in knowledge
        ],
    }
