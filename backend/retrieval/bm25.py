"""
BM25 (keyword) retrieval over the same chunk corpus as the vector store.

MVP approach: rebuild a rank_bm25 index from the chunk rows matching the
requested product/revision/doc_type filters, on every query. That's
fine for a single-pilot-customer corpus (thousands of chunks); the
obvious next step once corpora grow is a persisted/incremental BM25
index (e.g. via a proper search engine) — noted here rather than
solved, per the "prove the loop first" MVP principle in plan.md §26.

Phase 3 diagnosis-agent extraction: the corpus is fetched from
ingestion's HTTP API (GET /v1/knowledge/revisions/{id}/chunks,
paged), never read from ingestion's Chunk/Document/Revision tables.
The ranking math is unchanged. A lookup failure raises
IngestionUnavailableError (mapped to 503 with ingestion named).
"""
from __future__ import annotations

import re

from rank_bm25 import BM25Okapi

from backend.retrieval import ingestion_client as ingestion_client_mod

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _candidate_chunks(
    product_id: str | None,
    revision_id: str | None,
    doc_type: str | None,
    client=None,
) -> list[dict]:
    """Chunk rows (dicts) for the scope, via ingestion's HTTP API.

    Revision-scoped when a revision is given; a product-only scope
    pages every revision of the product. No scope means no corpus:
    retrieval is hard revision-scoped (an unscoped call used to
    return every chunk in the database). None when the revision /
    product is unknown.
    """
    client = client or ingestion_client_mod.get_ingestion_client()
    if revision_id:
        return client.fetch_all_chunks(revision_id, doc_type=doc_type)
    if product_id:
        revisions = client.list_revisions(product_id)
        if revisions is None:
            return None
        rows: list[dict] = []
        for revision in revisions:
            rows.extend(client.fetch_all_chunks(revision.get("id"), doc_type=doc_type) or [])
        return rows
    return []


def bm25_search(
    query: str,
    top_k: int,
    product_id: str | None = None,
    revision_id: str | None = None,
    doc_type: str | None = None,
    client=None,
) -> list[tuple[str, float]]:
    """Returns [(chunk_id, score), ...] ordered best-first."""
    chunks = _candidate_chunks(product_id, revision_id, doc_type, client=client)
    if not chunks:
        return []

    corpus = [_tokenize(c["content"]) for c in chunks]
    bm25 = BM25Okapi(corpus)
    scores = bm25.get_scores(_tokenize(query))

    # Don't filter on score > 0: with small/homogeneous corpora (e.g. a single
    # ingested document) BM25's IDF term can go negative for every candidate,
    # which would zero out results entirely. Relative rank is what RRF fusion
    # actually consumes downstream, not the absolute score.
    ranked = sorted(zip(chunks, scores), key=lambda pair: pair[1], reverse=True)
    return [(c["id"], float(score)) for c, score in ranked[:top_k]]
