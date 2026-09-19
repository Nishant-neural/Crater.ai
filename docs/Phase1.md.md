# Crater.ai — Phase 1: Product Knowledge

This is the scaffold for **Phase 1** of the Crater.ai roadmap (per `PLAN.md` §33):
turning raw manuals/schematics into a queryable, revision-aware **Product Brain**,
with hybrid retrieval on top. It's deliberately generic — no single product family
is hardcoded — so the same pipeline can onboard multiple manufacturers/products
later just by creating new `Product`/`Revision` rows and ingesting their docs.

Not in scope yet (later phases): diagnostic agent/hypothesis tracking (Phase 2),
schematic component-graph tracing (Phase 3 proper — Phase 1 only OCRs diagram
images), visualization UI (Phase 4), expert interviews (Phase 5), digital twin /
simulation (Phase 6-7), camera/voice (Phase 9).

## What's implemented

| plan.md Phase 1 requirement | Where |
|---|---|
| PDF ingestion | `ingestion/pdf_loader.py` |
| OCR (scanned pages + diagram captions) | `ingestion/ocr.py` |
| Table extraction | `ingestion/pdf_loader.py` + `ingestion/chunking.py` |
| Components / relationships / procedures | `knowledge/component_extraction.py` (provider-based structured extraction) |
| Revision modeling | `db/models.py` — every Document/Component/Procedure/FailureMode hangs off `Revision`, not just `Product` |
| Hybrid retrieval (semantic + BM25 + metadata/revision filters + rerank) | `retrieval/hybrid.py`, `retrieval/vector_store.py`, `retrieval/bm25.py`, `retrieval/reranker.py` |

Diagram-aware retrieval today means: embedded images are extracted, OCR'd for
label/terminal text, and stored as searchable "diagram" chunks. True schematic
graph parsing (tracing wires/symbols into a machine graph) is Phase 3.

## Architecture

```
PDF ─▶ pdf_loader (text/tables/images) ─▶ chunking ─▶ SQL (Product Brain) + Qdrant
                                                              │
                                                    knowledge/component_extraction
                                                    (Claude: components, relations,
                                                     procedures, linked to source chunk)

Question ─▶ hybrid_retrieve
              ├─ semantic_search (Qdrant, filtered by product/revision/doc_type)
              ├─ bm25_search     (SQL corpus, same filters)
              ├─ reciprocal rank fusion
              └─ rerank (Claude cross-encoder-style scoring)
            ─▶ evidence-grounded chunks (source doc, page, chunk type)
```

## Setup

```bash
pip install -e ".[dev]"
cp .env.example .env   # fill in ANTHROPIC_API_KEY at minimum
docker run -p 6333:6333 qdrant/qdrant   # or point QDRANT_URL at a hosted instance
```

System dependency: `tesseract-ocr` must be installed and on PATH (or set
`TESSERACT_CMD` in `.env`) for the OCR fallback to work.

## Running

```bash
# API
uvicorn backend.api.main:app --reload

# CLI ingestion (no server needed)
python scripts/ingest_docs.py path/to/manual.pdf \
    --manufacturer "Acme" --family "CNC" --model "CNC-500X" \
    --revision "Rev C" --doc-type manual --title "CNC-500X Service Manual"
```

Then:
```bash
curl -X POST localhost:8000/query -H "Content-Type: application/json" \
  -d '{"question": "How do I check the drive enable signal?", "revision_id": "..."}'
```

The API requires a revision for every technician-facing query and returns that
revision with each evidence item. PDF uploads require an `idempotency_key` form
field; use `GET /ingest/jobs/{job_id}` to inspect the status and
`POST /ingest/jobs/{job_id}/retry` to resume a failed ingestion.

## Tests

```bash
pytest
```

Current tests cover pure chunking logic (no external services needed). The
manual smoke-test sequence below exercises the rest without needing Qdrant or
an Anthropic key:

```python
from backend.db.session import SessionLocal, init_db
from backend.db.models import Product, Revision
init_db()
# ... create Product/Revision, then crater.ingestion.pipeline.ingest_pdf(...)
```

## Known gaps / next things to tighten

- BM25 index is rebuilt from SQL on every query — fine for one pilot customer's
  corpus, not for scale. Swap for a persisted/incremental index later.
- Knowledge extraction runs per-chunk, so relationships referencing a component
  named in a *different* chunk get silently dropped (see docstring in
  `knowledge/component_extraction.py`). A cross-chunk name-resolution pass would
  fix this — worth doing once real manuals show how often it matters.
- `sentence-transformers` model download requires internet access; if the
  deployment target doesn't have it, swap `retrieval/embeddings.py` for a
  hosted embedding API behind the same `EmbeddingProvider` interface.
- Ingestion runs synchronously in the API route — fine for now, move to a
  background task queue before real files/pilot load.
- No auth on any endpoint yet.
