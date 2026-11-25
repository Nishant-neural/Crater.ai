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

from backend.config import settings
from backend.retrieval import ingestion_client as ingestion_client_mod
from backend.retrieval.bm25 import bm25_search
from backend.retrieval.reranker import RerankerUnavailableError, rerank
from backend.retrieval.vector_store import semantic_search
from backend.retrieval.knowledge import retrieve_machine_knowledge, RetrievedKnowledge

logger = logging.getLogger(__name__)


class RetrievalUnavailableError(RuntimeError):
    """Semantic retrieval is down (Qdrant / embedding provider).

    Phase 1 Step 2 (infra P-06): a Qdrant failure is a hard failure,
    not a silent BM25-only degradation. The API maps this to HTTP 503
    with a reason, and /ready fails while it persists. BM25 stays a
    first-class leg of the normal fusion path (RRF, kept per both
    audits); it is no longer a fallback that masks a dead Qdrant.
    The full bm25.py removal (Qdrant native sparse) is a separate
    infra migration -- it needs a re-index, not a fallback delete.
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class RetrievedChunk:
    chunk_id: str
    content: str
    page_number: int | None
    document_id: str
    chunk_type: str
    # Source document title, carried on the chunk row ingestion's
    # API returns — the graph builds evidence refs from it instead
    # of reading the Document row (Phase 3 extraction).
    source_document: str | None = None
    # Document type of the source document (same row).
    doc_type: str | None = None


def _reciprocal_rank_fusion(ranked_lists: list[list[str]], k: int = 60) -> list[str]:
    """Standard RRF: score(doc) = sum(1 / (k + rank)) across all lists it appears in."""
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank_index, chunk_id in enumerate(ranked):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank_index + 1)
    return [cid for cid, _ in sorted(scores.items(), key=lambda pair: pair[1], reverse=True)]


def hybrid_retrieve(
    query: str,
    product_id: str | None = None,
    revision_id: str | None = None,
    doc_type: str | None = None,
    client=None,
) -> list[RetrievedChunk]:
    top_k = settings.hybrid_top_k

    try:
        semantic_hits = semantic_search(query, top_k, product_id, revision_id, doc_type)
    except Exception as exc:
        # Phase 1 Step 2 (infra P-06): the old silent fallback to
        # BM25-only is deleted. A dead Qdrant used to look like a
        # valid keyword-only run; now it fails loudly (HTTP 503 +
        # red /ready) so nobody diagnoses on half the evidence.
        raise RetrievalUnavailableError(
            f"semantic retrieval unavailable: {exc}"
        ) from exc
    keyword_hits = bm25_search(query, top_k, product_id, revision_id, doc_type, client=client)

    fused_ids = _reciprocal_rank_fusion(
        [[cid for cid, _ in semantic_hits], [cid for cid, _ in keyword_hits]]
    )[:top_k]

    if not fused_ids:
        return []

    # Whole chunk contents for the fused set, via ingestion's HTTP API
    # (Qdrant payloads carry no content; diagnosis never reads the
    # Chunk table). Preserve fusion order; skip ids ingestion no
    # longer returns (shouldn't happen, but be defensive).
    client = client or ingestion_client_mod.get_ingestion_client()
    fetched = client.fetch_chunks_by_ids(revision_id, fused_ids) if revision_id else []
    if fetched is None:
        fetched = []
    rows = {row["id"]: row for row in fetched}
    ordered_rows = [rows[cid] for cid in fused_ids if cid in rows]

    candidates = [(row["id"], row["content"]) for row in ordered_rows]
    try:
        reranked_ids = rerank(query, candidates, settings.final_top_k)
    except RerankerUnavailableError:
        # The CrossEncoder model failed to load: never silently fall
        # back to fusion order (amendment resolution 1). Propagate
        # so the API returns 503 and /ready stays red.
        raise
    except Exception as exc:
        # fix6 (23 Nov 2026), ported from v12: rerank can fail
        # transiently at predict time. That used to propagate
        # uncaught through retrieve_revision_context -> run_turn
        # -> POST /diagnose/start as a 500, killing session start.
        # Fall back to the pre-rerank candidate order (fusion
        # order, truncated to final_top_k) so the session starts
        # and diagnosis proceeds without reranking.
        logger.warning("Rerank failed; using pre-rerank candidate order: %s", exc)
        reranked_ids = [cid for cid, _ in candidates[: settings.final_top_k]]

    rows_by_id = {row["id"]: row for row in ordered_rows}
    return [
        RetrievedChunk(
            chunk_id=cid,
            content=rows_by_id[cid]["content"],
            page_number=rows_by_id[cid]["page_number"],
            document_id=rows_by_id[cid]["document_id"],
            chunk_type=rows_by_id[cid]["chunk_type"],
            source_document=rows_by_id[cid].get("document_title"),
            doc_type=rows_by_id[cid].get("doc_type"),
        )
        for cid in reranked_ids
    ]


def retrieve_revision_context(query: str, product_id: str, revision_id: str, doc_type: str | None = None, top_k: int | None = None, client=None) -> tuple[list[RetrievedChunk], list[RetrievedKnowledge]]:
    """Return both source evidence and canonical machine knowledge for one exact revision."""
    chunks = hybrid_retrieve(query, product_id=product_id, revision_id=revision_id, doc_type=doc_type, client=client)
    knowledge = retrieve_machine_knowledge(query, product_id=product_id, revision_id=revision_id, top_k=top_k or settings.final_top_k, client=client)
    return chunks, knowledge
