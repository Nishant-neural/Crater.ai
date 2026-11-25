"""Gate 6: index the gate DB's chunks into the local Qdrant.

Replicates the ingestion pipeline's payload exactly
(backend/ingestion/pipeline.py): product_id, revision_id, doc_type,
chunk_type, document_id per chunk. All 98 chunks (text + table +
diagram, stored content as-is) are indexed so semantic_search sees
the same corpus the original ingestion produced.

Run with the crater-phase1 venv python from ~/workspace/crater-refactor:
  NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1 \
  ~/workspace/crater-phase1/.venv/bin/python gate6-run/index_chunks.py
"""
import sqlite3
import sys
from pathlib import Path

REFACTOR = Path("/home/hatch/workspace/crater-refactor")
sys.path.insert(0, str(REFACTOR))

DB = REFACTOR / "gate6-run" / "crater-gate6.db"

from backend.retrieval.vector_store import (  # noqa: E402
    ensure_collection,
    get_client,
    upsert_chunks,
)


def main() -> int:
    conn = sqlite3.connect(DB)
    docs = {
        row[0]: {"revision_id": row[1], "doc_type": row[2]}
        for row in conn.execute(
            "SELECT id, revision_id, doc_type FROM documents"
        )
    }
    rev_product = {
        row[0]: row[1]
        for row in conn.execute("SELECT id, product_id FROM revisions")
    }
    rows = conn.execute(
        "SELECT id, document_id, chunk_type, content FROM chunks"
    ).fetchall()
    print(f"chunks to index: {len(rows)}", flush=True)
    chunk_ids, texts, payloads = [], [], []
    for cid, doc_id, chunk_type, content in rows:
        doc = docs[doc_id]
        chunk_ids.append(cid)
        texts.append(content)
        payloads.append(
            {
                "product_id": rev_product[doc["revision_id"]],
                "revision_id": doc["revision_id"],
                "doc_type": doc["doc_type"],
                "chunk_type": chunk_type,
                "document_id": doc_id,
            }
        )
    client = get_client()
    ensure_collection(client)
    upsert_chunks(chunk_ids, texts, payloads, client=client)
    info = client.get_collection("crater_chunks")
    print(f"collection points: {info.points_count}", flush=True)
    for product_id in sorted({p["product_id"] for p in payloads}):
        count = client.count(
            collection_name="crater_chunks",
            count_filter={
                "must": [
                    {"key": "product_id", "match": {"value": product_id}}
                ]
            },
        ).count
        print(f"product {product_id}: {count} points", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
