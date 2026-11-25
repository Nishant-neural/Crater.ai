"""Manual smoke check for Phase 1 Step 2 (CrossEncoder reranker).

Run from the repo root with the phase-1 venv:
    ~/workspace/crater-phase1/.venv/bin/python scripts/smoke_reranker_miller.py

What it proves, against real Miller text from frozen/miller_parser_rows.json:
1. A Miller query returns CrossEncoder-ranked results through the live
   hybrid_retrieve() path -- and the reranker OVERTURNS the fusion
   order (the stubs deliberately rank the distractor first, so a
   first-place §6-5 chunk can only come from the CrossEncoder).
2. Whole-text scoring: the §6-5 table chunk is 2,885 chars and its
   deciding remedy ("Readjust drive roll pressure") sits past char
   2,400 -- invisible to the old text[:1000] reranker (P-05). The
   script records the exact text lengths CrossEncoder.predict received.
3. Qdrant down is a loud failure, not a silent BM25 run: the same
   call with semantic_search raising reports RetrievalUnavailableError,
   and GET /ready is 503 with Qdrant red (this VM has no Qdrant) while
   the reranker check is green.

Qdrant itself is stubbed here (no Qdrant runs in this VM); the
semantic_search stub stands in for its hit list. The reranker is the
real cross-encoder/ms-marco-MiniLM-L-6-v2 model.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Keep the smoke DB out of the working copy.
os.environ["DATABASE_URL"] = "sqlite:////tmp/smoke-reranker-miller.db"

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from backend.db.models import Base, Chunk, ChunkType, Document, DocType, Product, Revision  # noqa: E402
from backend.retrieval import hybrid, reranker as reranker_mod  # noqa: E402
from backend.retrieval.hybrid import RetrievalUnavailableError  # noqa: E402


def miller_chunks() -> tuple[str, str, str]:
    rows = json.loads((REPO / "frozen" / "miller_parser_rows.json").read_text())
    s65 = [r for r in rows if r["source_table"].startswith("6-5")]
    table = "\n".join(f"Trouble: {r['symptom']} Remedy: {r['remedy']}" for r in s65)
    other = [r for r in rows if r["source_table"].startswith("8-17")]
    distractor = "\n".join(
        f"Trouble: {r['symptom']} Remedy: {r['remedy']}" for r in other[:8]
    )
    third = "\n".join(
        f"Trouble: {r['symptom']} Remedy: {r['remedy']}" for r in other[8:16]
    )
    return table, distractor, third


def main() -> int:
    table, distractor, third = miller_chunks()
    query = "Low, high, or erratic wire speed."
    remedy = "Readjust drive roll pressure (see Section 4-14)."
    print(f"query: {query}")
    print(f"§6-5 table chunk: {len(table)} chars; deciding remedy at offset {table.find(remedy)}")
    print(f"distractor chunk: {len(distractor)} chars; third chunk: {len(third)} chars")

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    product = Product(manufacturer="Miller Electric", family="Millermatic", model="252")
    session.add(product)
    session.flush()
    revision = Revision(product_id=product.id, label="OM-230 693C")
    session.add(revision)
    session.flush()
    doc = Document(
        revision_id=revision.id, title="Millermatic 252 Owner's Manual",
        doc_type=DocType.manual, source_path="frozen/miller_parser_rows.json",
    )
    session.add(doc)
    session.flush()
    for cid, content in (("s65", table), ("distractor", distractor), ("third", third)):
        session.add(Chunk(id=cid, document_id=doc.id, chunk_type=ChunkType.table,
                          page_number=31, content=content))
    session.commit()

    # Fusion order deliberately WRONG: distractor first. Only the
    # CrossEncoder can put the §6-5 chunk first.
    hybrid.semantic_search = lambda *a, **k: [("distractor", 1.0), ("s65", 0.9), ("third", 0.8)]
    hybrid.bm25_search = lambda *a, **k: [("distractor", 1.0), ("third", 0.5)]

    # Phase 3 extraction: hybrid_retrieve fetches fused chunk contents
    # from ingestion's HTTP API instead of the Chunk table, so the
    # smoke feeds the seeded chunks through a stub client.
    _chunk_rows = {
        cid: {"id": cid, "content": content, "page_number": 31,
              "document_id": doc.id,
              "document_title": "Millermatic 252 Owner's Manual",
              "doc_type": "manual", "chunk_type": "table"}
        for cid, content in (("s65", table), ("distractor", distractor),
                             ("third", third))
    }

    class _StubIngestionClient:
        def fetch_chunks_by_ids(self, revision_id, ids):
            return [_chunk_rows[cid] for cid in ids if cid in _chunk_rows]

        def fetch_all_chunks(self, revision_id, doc_type=None):
            return list(_chunk_rows.values())

    _stub_client = _StubIngestionClient()

    # Record exactly what text lengths the real model is asked to score.
    model = reranker_mod.ensure_reranker_loaded()
    seen_lengths: list[int] = []
    real_predict = model.predict

    def recording_predict(pairs, **kwargs):
        seen_lengths.extend(len(text) for _, text in pairs)
        return real_predict(pairs, **kwargs)

    model.predict = recording_predict

    scored = reranker_mod.rerank_with_scores(
        query,
        [("distractor", distractor), ("s65", table), ("third", third)],
        top_k=3,
    )
    print("\nrerank_with_scores (real CrossEncoder):")
    for cid, score in scored:
        print(f"  {cid:10s} {score:8.3f}")
    results = hybrid.hybrid_retrieve(query, revision_id=revision.id, client=_stub_client)
    print("\nhybrid_retrieve order (fusion had distractor first):")
    for r in results:
        print(f"  {r.chunk_id:10s} page {r.page_number} [{r.chunk_type}]")
    print(f"\nCrossEncoder.predict received text lengths: {seen_lengths}")
    print(f"longest input: {max(seen_lengths)} chars (old cut was 1000)")

    ok = True
    if not results or results[0].chunk_id != "s65":
        print("SMOKE FAIL: §6-5 table chunk is not first after reranking")
        ok = False
    if max(seen_lengths) <= 1000:
        print("SMOKE FAIL: model never saw whole text")
        ok = False

    # Part 2: Qdrant down must fail loudly.
    def _down(*a, **k):
        raise ConnectionError("Qdrant is unavailable")

    hybrid.semantic_search = _down
    try:
        hybrid.hybrid_retrieve(query, revision_id=revision.id, client=_stub_client)
        print("SMOKE FAIL: Qdrant-down call returned results instead of raising")
        ok = False
    except RetrievalUnavailableError as exc:
        print(f"\nQdrant down -> RetrievalUnavailableError: {exc.reason}")

    # Part 3: /ready is 503 while Qdrant is down, reranker green.
    from fastapi.testclient import TestClient

    from backend.api.main import app

    client = TestClient(app)
    try:
        resp = client.get("/ready")
        print(f"\nGET /ready -> {resp.status_code}: {resp.json()}")
        if resp.status_code != 503:
            print("SMOKE FAIL: /ready should be 503 with Qdrant down")
            ok = False
        elif resp.json()["checks"]["reranker"]["ready"] is not True:
            print("SMOKE FAIL: reranker check should be green")
            ok = False
        elif resp.json()["checks"]["qdrant"]["ready"] is not False:
            print("SMOKE FAIL: qdrant check should be red")
            ok = False
    finally:
        client.close()

    print("\nSMOKE " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
