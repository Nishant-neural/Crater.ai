"""
Reranking step. Phase 1 Step 2 (diagnosis audit Step 1, amendment
resolution 1): a local CrossEncoder replaces the LLM reranker.

    Model: cross-encoder/ms-marco-MiniLM-L-6-v2 (settings.reranker_model)
    Whole text: the candidate text is passed to the model in full --
    the old text[:1000] cut is deleted. (The model's own 512-token
    window still applies inside CrossEncoder.predict; that is a property
    of the chosen model, not a cut in this code.)
    Same dependency family as the embedding provider
    (sentence-transformers), no provider key, no LLM call.

Contract (unchanged): rerank(query, candidates, top_k) -> chunk_ids,
best-first, truncated to top_k. rerank_with_scores returns the
(chunk_id, score) pairs the audit's "return scores" requires.

Failure semantics (amendment resolution 1 + infra P-06):
- If the model fails to load, rerank() raises RerankerUnavailableError.
  It NEVER silently falls back to fusion order, and /ready fails
  while the model is not loaded. A deploy that cannot load the model
  is a broken deploy, not a degraded one.
- A load failure is cached per process: a retry needs a restart.
  (Model files do not appear spontaneously; readiness probes stay
  cheap instead of re-attempting a network fetch on every probe.)
- A transient predict-time exception is NOT a load failure: the
  hybrid.py fix6 guard catches it and falls back to fusion order so
  one bad rerank call cannot 500 a session start. The guard
  re-raises RerankerUnavailableError unchanged.
"""
from __future__ import annotations

import logging

from backend.config import settings

logger = logging.getLogger(__name__)


class RerankerUnavailableError(RuntimeError):
    """The CrossEncoder model failed to load (or was never loaded).

    Callers must fail loudly (HTTP 503 + red /ready), never silently
    fall back to unranked / fusion order.
    """


_model = None
_load_error: Exception | None = None
_load_attempted = False


def _load_model():
    """Load (once) and return the CrossEncoder, or raise RerankerUnavailableError."""
    global _model, _load_error, _load_attempted
    if _model is not None:
        return _model
    if _load_attempted and _load_error is not None:
        raise RerankerUnavailableError(
            f"CrossEncoder model {settings.reranker_model!r} failed to load: {_load_error}"
        ) from _load_error
    _load_attempted = True
    try:
        # Sandbox env quirk: this VM's NO_PROXY holds bracketed IPv6
        # literals that huggingface_hub's httpx client cannot parse
        # (InvalidURL on any load, cached or not). Drop them first;
        # see backend/retrieval/_proxy_env.py.
        from backend.retrieval._proxy_env import sanitize_proxy_env_for_httpx

        sanitize_proxy_env_for_httpx()
        from sentence_transformers import CrossEncoder  # lazy import: heavy dependency

        _model = CrossEncoder(settings.reranker_model)
    except Exception as exc:  # noqa: BLE001 - any load failure is a readiness failure
        _load_error = exc
        logger.error("CrossEncoder model %r failed to load: %s", settings.reranker_model, exc)
        raise RerankerUnavailableError(
            f"CrossEncoder model {settings.reranker_model!r} failed to load: {exc}"
        ) from exc
    logger.info("CrossEncoder model %r loaded", settings.reranker_model)
    return _model


def ensure_reranker_loaded():
    """Load the model if needed and return it; raise RerankerUnavailableError on failure."""
    return _load_model()


def is_reranker_ready() -> bool:
    """True only if the model loaded successfully. Does not trigger a load."""
    return _model is not None and _load_error is None


def reranker_status() -> dict:
    """Readiness payload for /ready: loaded flag, model name, load error if any."""
    return {
        "loaded": is_reranker_ready(),
        "model": settings.reranker_model,
        "error": str(_load_error) if _load_error is not None else None,
    }


def _reset_reranker_state() -> None:
    """Test helper: forget the cached model / load error so the next call reloads."""
    global _model, _load_error, _load_attempted
    _model = None
    _load_error = None
    _load_attempted = False


def rerank_with_scores(
    query: str, candidates: list[tuple[str, str]], top_k: int
) -> list[tuple[str, float]]:
    """
    candidates: [(chunk_id, chunk_text), ...]
    Returns [(chunk_id, score), ...] best-first, truncated to top_k.
    The whole candidate text is scored -- no truncation in this code.
    Raises RerankerUnavailableError if the model failed to load.
    """
    if not candidates:
        return []
    model = _load_model()
    pairs = [(query, text) for _, text in candidates]
    scores = model.predict(pairs, show_progress_bar=False)
    scored = sorted(
        zip(candidates, scores), key=lambda pair: float(pair[1]), reverse=True
    )
    return [(cid, float(score)) for (cid, _), score in scored[:top_k]]


def rerank(query: str, candidates: list[tuple[str, str]], top_k: int) -> list[str]:
    """
    candidates: [(chunk_id, chunk_text), ...]
    Returns chunk_ids, best-first, truncated to top_k.
    Raises RerankerUnavailableError if the model failed to load --
    never silently returns the fusion order.
    """
    return [cid for cid, _ in rerank_with_scores(query, candidates, top_k)]
