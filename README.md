# Crater.ai — Phases 1, 2 & 3

This is the scaffold for **Phase 1 (Product Knowledge)**, **Phase 2
(Diagnostic Agent)**, and **Phase 3 (Schematic Intelligence)** of the
Crater.ai roadmap (`PLAN.md` §33): turning raw manuals/schematics into a
queryable, revision-aware **Product Brain**, with hybrid retrieval, a
structured troubleshooting loop, and a real component/connection graph
extracted from diagram images, all on top of each other. It's deliberately
generic — no single product family is hardcoded — so the same pipeline can
onboard multiple manufacturers/products later just by creating new
`Product`/`Revision` rows and ingesting their docs.

Not in scope yet (later phases): visualization UI beyond a single
highlighted image (Phase 4), expert interviews (Phase 5), digital twin /
simulation (Phase 6-7), camera/voice (Phase 9).

## What's implemented

### Phase 1 — Product Knowledge

| plan.md requirement | Where |
|---|---|
| PDF ingestion | `ingestion/pdf_loader.py` |
| OCR (scanned pages + diagram captions) | `ingestion/ocr.py` |
| Table extraction | `ingestion/pdf_loader.py` + `ingestion/chunking.py` |
| Components / relationships / procedures | `knowledge/component_extraction.py` (Claude-based structured extraction) |
| Revision modeling | `db/models.py` — every Document/Component/Procedure/FailureMode hangs off `Revision`, not just `Product` |
| Hybrid retrieval (semantic + BM25 + metadata/revision filters + rerank) | `retrieval/hybrid.py`, `retrieval/vector_store.py`, `retrieval/bm25.py`, `retrieval/reranker.py` |

Diagram-aware retrieval in Phase 1 means: embedded images are extracted,
OCR'd for label/terminal text, and stored as searchable "diagram" chunks.
Turning those same images into an actual component/wire graph is Phase 3,
below.

### Phase 2 — Diagnostic Agent

| plan.md §6 / §33 requirement | Where |
|---|---|
| Diagnostic state (symptoms, observations, measurements, hypotheses, eliminated hypotheses, evidence, confidence) | `diagnostics/schema.py` |
| Hypothesis generation + question selection (one LLM call, since they're entangled) | `diagnostics/agent.py::_run_llm_turn`, `diagnostics/prompts.py` |
| Evidence retrieval | reuses Phase 1's `retrieval/hybrid.py::hybrid_retrieve`, scoped to the session's product/revision |
| Structured troubleshooting loop, persisted across turns | `db/models.py::DiagnosticSession` (JSON state blob) + `diagnostics/agent.py::run_turn` / `start_session` |
| Safety architecture (§25): never invent specs, escalate on low confidence or turn limits, flag safety notes | `diagnostics/prompts.py` (LLM-side rules) + `diagnostics/agent.py::_apply_forced_safeguards` (deterministic guardrail — enforced even if the LLM disagrees) |
| API | `api/routes/diagnostics.py`: `POST /diagnose/start`, `POST /diagnose/{id}/respond`, `GET /diagnose/{id}` |

**Deliberately not yet built** (later phases per plan.md §33): schematic
component-graph tracing feeding hypothesis generation (Phase 3), visual
highlighting of components (Phase 4), the "Capture Rajesh" expert-interview
pipeline that would populate richer FailureMode data (Phase 5), and any
actual digital-twin/simulation testing of hypotheses (Phase 6-7) — right now
`overall_confidence` comes only from the LLM's read of retrieved evidence
and structured Component/FailureMode rows, not from simulated verification.

### Phase 3 — Schematic Intelligence

| plan.md §5 / §33 requirement | Where |
|---|---|
| Schematic parsing (treat the image as a structured system, not just OCR text) | `schematic/vision_extraction.py` — sends the diagram image itself to Claude's vision input |
| Component extraction (symbols, labels, terminals) | same call, structured via `schematic/schema.py::SchematicExtractionResult` |
| Connection graph (wires between components) | `db/models.py::SchematicNode` / `SchematicEdge`, persisted by `schematic/graph.py::persist_schematic` |
| Signal tracing | `schematic/graph.py::trace_path` (pure BFS over labels) / `trace_path_in_document` (DB-backed wrapper) |
| Visual highlighting | `schematic/highlight.py::highlight_nodes` — draws labeled boxes on the source image for a given component |
| API | `api/routes/schematics.py`: `GET /schematics/{document_id}/graph`, `GET /schematics/{document_id}/trace`, `GET /schematics/{document_id}/highlight` |

Wired into ingestion: `ingestion/pipeline.py::ingest_pdf` now runs schematic
extraction on every `diagram`-type chunk after Phase 1's text-based component
extraction has run, so a `SchematicNode` can link to its matching Phase-1
`Component` row by exact label match (case-insensitive) when one exists.

**Honest limits**: node/edge extraction is a single vision-LLM call per
diagram image, not true CV-based symbol/wire detection — quality depends
entirely on how legible the source scan is and how well Claude's vision
input reads it. Bounding boxes are the model's own estimate, not
pixel-precise. `trace_path` only knows about wires the model actually
extracted from that one image; a signal that continues onto a different
page/diagram won't be traced across documents yet — that's a natural
Phase 4 (or a schematic-graph-merging pass) extension.

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

Diagram chunk ─▶ schematic/vision_extraction (Claude vision: nodes + edges)
                    ─▶ schematic/graph.persist_schematic (SchematicNode/Edge rows,
                                                           linked to Phase 1 Component by label)
                    ─▶ schematic/graph.trace_path (signal tracing)
                    ─▶ schematic/highlight.highlight_nodes (annotated image)
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
uvicorn crater.api.main:app --reload

# CLI ingestion (no server needed)
python scripts/ingest_docs.py path/to/manual.pdf \
    --manufacturer "Acme" --family "CNC" --model "CNC-500X" \
    --revision "Rev C" --doc-type manual --title "CNC-500X Service Manual"
```

Then:
```bash
curl -X POST localhost:8000/query -H "Content-Type: application/json" \
  -d '{"question": "How do I check the drive enable signal?", "product_id": "..."}'

# Phase 2: start a troubleshooting session, then keep feeding it observations
curl -X POST localhost:8000/diagnose/start -H "Content-Type: application/json" \
  -d '{"product_id": "...", "revision_id": "...", "symptom": "Motor will not start"}'

curl -X POST localhost:8000/diagnose/<session_id>/respond -H "Content-Type: application/json" \
  -d '{"input": "Error code E207 is displayed", "input_kind": "observation"}'

# Phase 3: inspect a schematic's extracted graph, trace a path, get a highlighted image
curl localhost:8000/schematics/<document_id>/graph
curl "localhost:8000/schematics/<document_id>/trace?from_label=Power&to_label=Motor"
curl "localhost:8000/schematics/<document_id>/highlight?label=K17" --output k17_highlighted.png
```

Each response's `state.current_step` tells you what to do next: ask the
technician a `question`, request an `action` (a measurement/test), give a
`conclusion` (evidence-cited recommendation), or `escalate` to a human
expert. The session stops accepting `/respond` calls once it's concluded or
escalated — start a new session to keep troubleshooting a different symptom.

## Tests

```bash
pytest
```

Current tests cover pure chunking logic (no external services needed). The
manual smoke-test sequence below exercises the rest without needing Qdrant or
an Anthropic key:

```python
from crater.db.session import SessionLocal, init_db
from crater.db.models import Product, Revision
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
- Diagnostic sessions are single-user/single-turn-at-a-time — no concurrency
  control on `DiagnosticSession.state` if two requests race on the same
  session id.
- `overall_confidence` is entirely LLM self-assessed against retrieved text
  evidence; there's no simulation-based verification yet (that's Phase 6-7),
  so treat it as "how well the evidence supports this," not a calibrated
  probability.
- The forced-escalation thresholds (`_MAX_TURNS_BEFORE_FORCED_ESCALATION`,
  `_LOW_CONFIDENCE_ESCALATION_THRESHOLD` in `diagnostics/agent.py`) are
  reasonable starting guesses, not tuned against real cases — plan.md §29's
  evaluation benchmark is exactly the tool to tune them once real
  troubleshooting transcripts exist.
- Schematic label matching (`schematic/graph.py::persist_schematic`) is exact,
  case-insensitive string match only — "K17" won't link to a Phase-1
  Component named "Relay K17". A fuzzy/LLM-assisted matching pass is a
  reasonable upgrade once real manuals show how often labels disagree.
- Schematic extraction is not yet wired into the diagnostic agent's
  reasoning (Phase 2's `diagnostics/agent.py` still only sees Component/
  FailureMode text knowledge, not the schematic graph) — that integration
  (e.g. "trace the path implicated by the leading hypothesis and surface
  it") is a natural next step, not yet built.
