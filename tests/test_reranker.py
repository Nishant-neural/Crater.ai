"""Phase 1 Step 2 gate tests: CrossEncoder reranker + retrieval hardening.

Diagnosis audit Step 1 / amendment resolution 1:
- reranker is cross-encoder/ms-marco-MiniLM-L-6-v2, whole text (the old
  text[:1000] cut is deleted), rerank(query, candidates, top_k)
  signature kept, scores returned.
- Model-load failure is a readiness failure: rerank() raises
  RerankerUnavailableError, never silently falls back to fusion order.
- Qdrant failure is a hard 503 (RetrievalUnavailableError), never a
  silent BM25-only run.
- The v12 fix6 rerank guard is ported: a transient predict-time
  reranker exception falls back to fusion order instead of 500ing
  POST /diagnose/start -- but a load failure still propagates.

Most tests use a stub CrossEncoder (fast, deterministic). Exactly one
test loads the real model (test_real_crossencoder_ranks_miller_table):
it proves end-to-end that the Miller §6-5 table chunk -- whose
wire-speed remedies sit past character 2,200, invisible to the old
[:1000] cut (P-05) -- ranks first for its own symptom.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.api import readiness as readiness_mod
from backend.retrieval import hybrid
from backend.retrieval import reranker as reranker_mod
from backend.retrieval.hybrid import RetrievalUnavailableError, hybrid_retrieve
from backend.retrieval.reranker import RerankerUnavailableError, rerank, rerank_with_scores

_FROZEN = Path(__file__).resolve().parent.parent / "frozen"


@pytest.fixture(autouse=True)
def _fresh_reranker_state():
    reranker_mod._reset_reranker_state()
    yield
    reranker_mod._reset_reranker_state()


# --- Stub CrossEncoder -------------------------------------------------

class _FakeCrossEncoder:
    """Deterministic stand-in: scores 10.0 iff the text carries the marker."""

    instances: list["_FakeCrossEncoder"] = []

    def __init__(self, model_name: str):
        self.model_name = model_name
        self.seen_pairs: list[tuple[str, str]] = []
        _FakeCrossEncoder.instances.append(self)

    def predict(self, pairs, show_progress_bar=False):
        self.seen_pairs = list(pairs)
        return [10.0 if "RELEVANT-MARKER" in text else 0.0 for _, text in pairs]


class _FailingCrossEncoder:
    init_calls = 0

    def __init__(self, model_name: str):
        type(self).init_calls += 1
        raise ConnectionError("huggingface.co unreachable in this test")


@pytest.fixture()
def _fake_model(monkeypatch):
    _FakeCrossEncoder.instances.clear()
    import sentence_transformers

    monkeypatch.setattr(sentence_transformers, "CrossEncoder", _FakeCrossEncoder)
    return _FakeCrossEncoder


@pytest.fixture()
def _failing_model(monkeypatch):
    _FailingCrossEncoder.init_calls = 0
    import sentence_transformers

    monkeypatch.setattr(sentence_transformers, "CrossEncoder", _FailingCrossEncoder)
    return _FailingCrossEncoder


# --- Whole-text scoring ------------------------------------------------

def test_rerank_passes_whole_text_to_model(_fake_model):
    """No truncation: the model receives the full candidate text, and the
    chunk whose relevant tail sits past char 1000 still wins."""
    filler = "Unrelated spot-timer prose. " * 60  # ~1,700 chars
    long_relevant = filler + " RELEVANT-MARKER: readjust drive roll pressure."
    short_irrelevant = "Spot timer setting for thin sheet metal."
    assert len(long_relevant) > 1000

    ranked = rerank(
        "erratic wire speed",
        [("short", short_irrelevant), ("long", long_relevant)],
        top_k=2,
    )
    assert ranked == ["long", "short"]

    model = _FakeCrossEncoder.instances[-1]
    # predict() receives (query, text) pairs -- no chunk ids -- so match
    # by text content.
    seen_texts = [text for _query, text in model.seen_pairs]
    assert set(seen_texts) == {short_irrelevant, long_relevant}
    # The whole text reached the model -- not text[:1000].
    seen_long = next(t for t in seen_texts if t.startswith("Unrelated spot-timer prose."))
    assert seen_long == long_relevant
    assert len(seen_long) > 1000
    assert seen_long.endswith("readjust drive roll pressure.")


def test_rerank_with_scores_returns_scores_best_first(_fake_model):
    scored = rerank_with_scores(
        "q",
        [("a", "nothing here"), ("b", "RELEVANT-MARKER here"), ("c", "nothing either")],
        top_k=2,
    )
    assert scored == [("b", 10.0), ("a", 0.0)]


def test_rerank_empty_candidates_does_not_load_model():
    assert rerank("q", [], 5) == []
    assert rerank_with_scores("q", [], 5) == []
    # No load attempted at all for an empty shortlist.
    assert reranker_mod._model is None
    assert reranker_mod._load_error is None


# --- Load failure is a readiness failure, never a silent fallback -----

def test_model_load_failure_raises_and_does_not_fall_back(_failing_model):
    with pytest.raises(RerankerUnavailableError, match="failed to load"):
        rerank("q", [("a", "text")], 1)
    # Still failing, and the failure is cached: the constructor ran once.
    assert reranker_mod.reranker_status()["loaded"] is False
    assert reranker_mod.reranker_status()["error"] is not None
    with pytest.raises(RerankerUnavailableError):
        rerank("q", [("a", "text")], 1)
    assert _FailingCrossEncoder.init_calls == 1


def test_readiness_fails_when_reranker_not_loaded(_failing_model):
    check = readiness_mod.check_reranker()
    assert check["ready"] is False
    assert "failed to load" in check["detail"]
    assert check["model"] == "cross-encoder/ms-marco-MiniLM-L-6-v2"


def test_readiness_passes_when_reranker_loaded(_fake_model):
    check = readiness_mod.check_reranker()
    assert check["ready"] is True
    assert check["model"] == "cross-encoder/ms-marco-MiniLM-L-6-v2"


def test_ready_endpoint_maps_checks_to_200_and_503(monkeypatch):
    """The /ready endpoint itself: 200 when every check passes, 503 with
    the failed checks named when any fails. (Checks are stubbed here;
    the real checks are covered above and below.)"""
    from fastapi.testclient import TestClient

    from backend.api import main as main_mod

    client = TestClient(main_mod.app)
    try:
        monkeypatch.setattr(
            main_mod,
            "readiness_checks",
            lambda: {
                "database": {"ready": True, "detail": "SELECT 1 ok"},
                "qdrant": {"ready": True, "detail": "collection reachable"},
                "reranker": {"ready": True, "detail": "model loaded"},
            },
        )
        ok = client.get("/ready")
        assert ok.status_code == 200
        assert ok.json()["status"] == "ready"

        monkeypatch.setattr(
            main_mod,
            "readiness_checks",
            lambda: {
                "database": {"ready": True, "detail": "SELECT 1 ok"},
                "qdrant": {"ready": False, "detail": "qdrant unreachable: refused"},
                "reranker": {"ready": False, "detail": "model failed to load"},
            },
        )
        down = client.get("/ready")
        assert down.status_code == 503
        assert down.json()["status"] == "not_ready"
        assert down.json()["checks"]["qdrant"]["ready"] is False
        assert down.json()["checks"]["reranker"]["ready"] is False

        assert client.get("/health").status_code == 200
    finally:
        client.close()


# --- Qdrant failure: hard 503, no BM25 fallback -----------------------

class _Row:
    def __init__(self, cid, content):
        self.id = cid
        self.content = content
        self.page_number = 7
        self.document_id = "document-1"

        class _ChunkType:
            value = "table"

        self.chunk_type = _ChunkType()


class _FakeIngestionClient:
    """Chunk rows for hybrid_retrieve's HTTP fetch, from _Row fakes.

    Phase 3 extraction: hybrid_retrieve fetches fused chunk contents
    from ingestion's HTTP API instead of the Chunk table, so the
    SQL-session fakes are replaced by this client stub.
    """

    def __init__(self, rows):
        self._rows = rows

    def _as_dict(self, row):
        return {"id": row.id, "content": row.content,
                "page_number": row.page_number,
                "document_id": row.document_id,
                "document_title": "document-1",
                "doc_type": "manual",
                "chunk_type": row.chunk_type.value}

    def fetch_chunks_by_ids(self, revision_id, ids):
        by_id = {r.id: r for r in self._rows}
        return [self._as_dict(by_id[cid]) for cid in ids if cid in by_id]

    def fetch_all_chunks(self, revision_id, doc_type=None):
        return [self._as_dict(r) for r in self._rows]


def _hybrid_with_stubs(monkeypatch, rows, semantic, bm25, rerank_fn):
    monkeypatch.setattr(hybrid, "semantic_search", semantic)
    monkeypatch.setattr(hybrid, "bm25_search", bm25)
    monkeypatch.setattr(hybrid, "rerank", rerank_fn)
    import backend.retrieval.ingestion_client as ingestion_client_mod

    monkeypatch.setattr(ingestion_client_mod, "get_ingestion_client",
                        lambda *a, **k: _FakeIngestionClient(rows))
    return hybrid_retrieve("What voltage is expected at X12?", revision_id="rev-1")


def test_hybrid_qdrant_failure_raises_not_bm25(monkeypatch):
    """P-06: semantic failure raises RetrievalUnavailableError; BM25 is
    never consulted as a fallback."""
    def unavailable(*_args, **_kwargs):
        raise ConnectionError("Qdrant is unavailable")

    def bm25_must_not_run(*_args, **_kwargs):
        raise AssertionError("BM25 must not run as a fallback when semantic search fails")

    with pytest.raises(RetrievalUnavailableError) as excinfo:
        _hybrid_with_stubs(
            monkeypatch,
            [_Row("chunk-1", "Terminal X12 should read 24 V.")],
            unavailable,
            bm25_must_not_run,
            lambda _q, c, _k: [cid for cid, _ in c],
        )
    assert "semantic retrieval unavailable" in str(excinfo.value)
    assert "Qdrant is unavailable" in excinfo.value.reason


# --- fix6 guard: transient rerank failure falls back, load failure does not

def test_hybrid_rerank_exception_falls_back_to_fusion_order(monkeypatch):
    """Ported v12 fix6 guard: a transient predict-time rerank exception
    must not 500 POST /diagnose/start -- fusion order is returned."""
    rows = [
        _Row("c1", "Terminal X12 should read 24 V."),
        _Row("c2", "Check the drive roll pressure."),
        _Row("c3", "Replace the contact tip."),
    ]

    def boom(*_args, **_kwargs):
        raise RuntimeError("predict exploded")

    results = _hybrid_with_stubs(
        monkeypatch,
        rows,
        lambda *_a, **_k: [("c1", 1.0), ("c2", 0.9), ("c3", 0.8)],
        lambda *_a, **_k: [("c3", 1.0), ("c1", 0.5)],
        boom,
    )
    # RRF: c1 = 1/61 + 1/62, c3 = 1/63 + 1/61, c2 = 1/62  ->  c1, c3, c2.
    assert [r.chunk_id for r in results] == ["c1", "c3", "c2"]


def test_hybrid_reranker_unavailable_propagates(monkeypatch):
    """The fix6 guard must NOT swallow a load failure: if the model never
    loaded, hybrid_retrieve raises instead of silently returning
    fusion order (amendment resolution 1)."""
    rows = [_Row("c1", "Terminal X12 should read 24 V.")]

    def load_failed(*_args, **_kwargs):
        raise RerankerUnavailableError("model failed to load")

    with pytest.raises(RerankerUnavailableError):
        _hybrid_with_stubs(
            monkeypatch,
            rows,
            lambda *_a, **_k: [("c1", 1.0)],
            lambda *_a, **_k: [("c1", 1.0)],
            load_failed,
        )


# --- Real model: Miller §6-5 table chunk (P-05) ------------------------

def _miller_6_5_chunk() -> tuple[str, str, int]:
    """The §6-5 table as one chunk (Step 1 atomic-table shape), the
    wire-speed symptom, and the char offset of its discriminating
    remedy -- past the old [:1000] cut by design."""
    rows = json.loads((_FROZEN / "miller_parser_rows.json").read_text())
    s65 = [r for r in rows if r["source_table"].startswith("6-5")]
    assert len(s65) == 21  # Step 1 correction: §6-5 proper is 21 rows
    chunk = "\n".join(f"Trouble: {r['symptom']} Remedy: {r['remedy']}" for r in s65)
    assert len(chunk) > 2000
    remedy = "Readjust drive roll pressure (see Section 4-14)."
    offset = chunk.find(remedy)
    assert offset > 1000  # invisible to the old text[:1000] reranker
    return chunk, "Low, high, or erratic wire speed.", offset


def test_real_crossencoder_ranks_miller_table_chunk_first():
    """Real model, real Miller text: the §6-5 table chunk ranks first
    for the wire-speed symptom, beating a distractor chunk about a
    different trouble -- with the deciding remedy past char 1000."""
    chunk, query, offset = _miller_6_5_chunk()
    rows = json.loads((_FROZEN / "miller_parser_rows.json").read_text())
    other = [r for r in rows if r["source_table"].startswith("8-17")]
    distractor = "\n".join(
        f"Trouble: {r['symptom']} Remedy: {r['remedy']}" for r in other[:8]
    )
    assert "wire speed" not in distractor.lower()

    scored = rerank_with_scores(query, [("distractor", distractor), ("s65", chunk)], top_k=2)
    assert scored[0][0] == "s65"
    assert scored[0][1] > scored[1][1]
    assert reranker_mod.reranker_status()["loaded"] is True
