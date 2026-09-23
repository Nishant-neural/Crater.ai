"""
Hybrid retrieval: semantic (Qdrant) + BM25, fused with Reciprocal Rank
Fusion, then reranked. This is the plan.md §7 stack:

    Semantic Search + BM25 + Metadata Filters + Revision Filters
    + Component Filters + (Knowledge Graph Traversal — deferred) + Reranking

Knowledge-model retrieval is now revision-scoped and graph-aware. Canonical
entity/relation/state/failure/procedure knowledge is retrieved alongside
source chunks, with graph expansion from matched entities.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging

from sqlalchemy.orm import Session

from backend.config import settings
from backend.db.models import Chunk
from backend.retrieval.bm25 import bm25_search
from backend.retrieval.reranker import rerank
from backend.retrieval.vector_store import semantic_search
from backend.retrieval.knowledge import retrieve_machine_knowledge, RetrievedKnowledge

logger = logging.getLogger(__name__)


@dataclass
class RetrievedChunk:
    chunk_id: str
    content: str
    page_number: int | None
    document_id: str
    chunk_type: str


def _reciprocal_rank_fusion(ranked_lists: list[list[str]], k: int = 60) -> list[str]:
    """Standard RRF: score(doc) = sum(1 / (k + rank)) across all lists it appears in."""
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank_index, chunk_id in enumerate(ranked):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank_index + 1)
    return [cid for cid, _ in sorted(scores.items(), key=lambda pair: pair[1], reverse=True)]


def hybrid_retrieve(
    session: Session,
    query: str,
    product_id: str | None = None,
    revision_id: str | None = None,
    doc_type: str | None = None,
) -> list[RetrievedChunk]:
    top_k = settings.hybrid_top_k

    try:
        semantic_hits = semantic_search(query, top_k, product_id, revision_id, doc_type)
    except Exception as exc:
        # Keyword retrieval remains useful for exact part numbers, terminals,
        # and error codes when Qdrant or the embedding provider is unavailable.
        logger.warning("Semantic retrieval unavailable; using BM25 only: %s", exc)
        semantic_hits = []
    keyword_hits = bm25_search(session, query, top_k, product_id, revision_id, doc_type)

    fused_ids = _reciprocal_rank_fusion(
        [[cid for cid, _ in semantic_hits], [cid for cid, _ in keyword_hits]]
    )[:top_k]

    if not fused_ids:
        return []

    rows = {c.id: c for c in session.query(Chunk).filter(Chunk.id.in_(fused_ids)).all()}
    # Preserve fusion order; skip ids that vanished from SQL (shouldn't happen, but be defensive).
    ordered_rows = [rows[cid] for cid in fused_ids if cid in rows]

    candidates = [(row.id, row.content) for row in ordered_rows]
    reranked_ids = rerank(query, candidates, settings.final_top_k)

    rows_by_id = {row.id: row for row in ordered_rows}
    return [
        RetrievedChunk(
            chunk_id=cid,
            content=rows_by_id[cid].content,
            page_number=rows_by_id[cid].page_number,
            document_id=rows_by_id[cid].document_id,
            chunk_type=rows_by_id[cid].chunk_type.value,
        )
        for cid in reranked_ids
    ]


def retrieve_revision_context(session: Session, query: str, product_id: str, revision_id: str, doc_type: str | None = None, top_k: int | None = None) -> tuple[list[RetrievedChunk], list[RetrievedKnowledge]]:
    """Return both source evidence and canonical machine knowledge for one exact revision."""
    chunks = hybrid_retrieve(session, query, product_id=product_id, revision_id=revision_id, doc_type=doc_type)
    knowledge = retrieve_machine_knowledge(session, query, product_id=product_id, revision_id=revision_id, top_k=top_k or settings.final_top_k)
    return chunks, knowledge
