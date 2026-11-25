"""HTTP client for the ingestion service's read API.

Phase 3 diagnosis-agent extraction (contracts README §1.1, §3.3):
diagnosis-agent never imports ingestion code and never reads
ingestion's Postgres tables. Every cross-service read is an HTTP call
to ingestion's API, on the internal network, with the contracts §1.2
timeout (10 s). A lookup failure raises
``IngestionUnavailableError`` — diagnosis does not proceed on partial
knowledge in silence; the service apps map it to HTTP 503 with
ingestion named as upstream.

Consumers call ``get_ingestion_client()`` at request time (via this
module's attribute, so tests can monkeypatch the factory once and
every consumer sees the stub). ``httpx.MockTransport`` is the
test seam: pass ``transport=`` to ``IngestionClient``.
"""
from __future__ import annotations

import logging

import httpx

from backend.config import settings

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 10.0


class IngestionUnavailableError(RuntimeError):
    """The ingestion API could not answer (down, timeout, 5xx).

    Carries ``reason``; service apps map it to 503
    ``ingestion_unavailable`` with upstream ``ingestion``.
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class IngestionClient:
    """Synchronous client for ingestion's read API (diagnosis callers
    are sync; the verify client is the async one)."""

    def __init__(self, base_url: str | None = None,
                 transport=None, timeout: float = _DEFAULT_TIMEOUT):
        self._base_url = (base_url or settings.ingestion_base_url).rstrip("/")
        self._transport = transport
        self._timeout = timeout

    def _get(self, path: str, params: dict | None = None):
        """GET returning parsed JSON, None on 404, raising
        IngestionUnavailableError on transport failure or 5xx/4xx
        other than 404."""
        try:
            with httpx.Client(base_url=self._base_url,
                              transport=self._transport,
                              timeout=self._timeout) as client:
                resp = client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise IngestionUnavailableError(
                f"ingestion API unreachable: {exc}") from exc
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise IngestionUnavailableError(
                f"ingestion API error: HTTP {resp.status_code} on {path}")
        return resp.json()

    # --- products / revisions (ingestion-owned registry) ---

    def list_products(self) -> list[dict]:
        data = self._get("/v1/products")
        return list(data or [])

    def list_revisions(self, product_id: str) -> list[dict] | None:
        """Revisions of one product; None when the product is unknown."""
        return self._get(f"/v1/products/{product_id}/revisions")

    def get_revision_summary(self, revision_id: str) -> dict | None:
        """{revision_id, product_id, revision_label} for a revision, or
        None when the revision is unknown. Probed via the chunks
        endpoint with limit=0 (no content fetched)."""
        data = self._get(f"/v1/knowledge/revisions/{revision_id}/chunks",
                         params={"limit": 0})
        if data is None:
            return None
        return {"revision_id": data.get("revision_id") or revision_id,
                "product_id": data.get("product_id"),
                "revision_label": data.get("revision_label")}

    # --- knowledge reads (contracts §3.3) ---

    def get_model(self, revision_id: str) -> dict | None:
        """Canonical machine model snapshot payload, or None when the
        revision has no integrated model (or is unknown)."""
        return self._get(f"/v1/knowledge/revisions/{revision_id}/model")

    def get_failure_modes(self, revision_id: str,
                          symptom: str | None = None) -> list[dict] | None:
        """FailureMode rows for a revision (symptom-filtered when
        given); None when the revision is unknown."""
        params = {"symptom": symptom} if symptom else None
        data = self._get(
            f"/v1/knowledge/revisions/{revision_id}/failure-modes",
            params=params)
        if data is None:
            return None
        return list(data.get("failure_modes") or [])

    # --- chunk contents (retrieval read path) ---

    def fetch_chunks(self, revision_id: str, doc_type: str | None = None,
                     limit: int = 500, offset: int = 0) -> dict | None:
        """One page of chunk rows for a revision (whole contents, no
        truncation), or None when the revision is unknown. Each row:
        id, content, page_number, document_id, document_title,
        doc_type, document_page_count, chunk_type."""
        params: dict = {"limit": limit, "offset": offset}
        if doc_type:
            params["doc_type"] = doc_type
        return self._get(f"/v1/knowledge/revisions/{revision_id}/chunks",
                         params=params)

    def fetch_all_chunks(self, revision_id: str,
                         doc_type: str | None = None) -> list[dict] | None:
        """Every chunk row for a revision (BM25 corpus), or None when
        the revision is unknown. Pages through the chunks endpoint."""
        rows: list[dict] = []
        offset = 0
        while True:
            page = self.fetch_chunks(revision_id, doc_type=doc_type,
                                     limit=500, offset=offset)
            if page is None:
                return None
            rows.extend(page.get("chunks") or [])
            total = page.get("total") or 0
            offset += len(page.get("chunks") or [])
            if offset >= total or not page.get("chunks"):
                return rows

    def fetch_chunks_by_ids(self, revision_id: str,
                            ids: list[str]) -> list[dict] | None:
        """Chunk rows for the given ids (the fused set hybrid retrieval
        needs content for), or None when the revision is unknown."""
        if not ids:
            return []
        page = self._get(
            f"/v1/knowledge/revisions/{revision_id}/chunks",
            params={"ids": ",".join(ids), "limit": len(ids)})
        if page is None:
            return None
        return list(page.get("chunks") or [])


def get_ingestion_client(transport=None) -> IngestionClient:
    """Fresh client from settings (no shared pool; pilot scale)."""
    return IngestionClient(transport=transport)
