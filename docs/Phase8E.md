# Phase 8E — Structural Retrieval + Diagnostics + Model Routing

## Pipeline

```text
PDF
 ↓
Structure-aware chunking
 ↓
Atomic persisted facts/evidence
 ↓
Hybrid retrieval ───────────────┐
 ↓                              │
Canonical knowledge + graph ────┤
 ↓                              │
Unified diagnostic context ◄────┘
 ↓
Task-aware Model Gateway
 ├─ extraction → cheaper model
 ├─ reranking → cheaper model
 ├─ integration → stronger model
 └─ diagnosis → strongest appropriate model
```

## 1. Better chunking

`backend/ingestion/chunking.py` now groups text around headings and semantic paragraph/procedure boundaries instead of slicing every page into fixed character windows. Section names are retained in `Chunk.extra`. Tables and diagrams remain atomic evidence units.

The old `_split_text()` helper remains for compatibility; the ingestion pipeline no longer uses it.

## 2. Diagnostics + upgraded retrieval

`backend/diagnostics/agent.py` now uses `retrieve_revision_context()` so every diagnostic turn can consume both:
- hybrid document evidence (semantic + BM25 + reranking), and
- revision-scoped canonical machine knowledge with graph expansion.

Legacy structured component/failure/expert knowledge remains as a compatibility layer, so Phase 5 knowledge is not lost.

## 3. Model Gateway

New `backend/llm/gateway.py` provides a task-based model boundary. Business logic requests a task (`extraction`, `integration`, `diagnosis`, `rerank`) rather than directly selecting a provider/model.

## 4. Gemini task routing

`backend/config.py` exposes task-specific provider/model settings. If a task-specific setting is blank, the gateway falls back to the global provider/model.

Example:

```env
LLM_PROVIDER=gemini
EXTRACTION_PROVIDER=gemini
EXTRACTION_MODEL=<cheap-gemini-model>
INTEGRATION_PROVIDER=gemini
INTEGRATION_MODEL=<strong-gemini-model>
DIAGNOSIS_PROVIDER=gemini
DIAGNOSIS_MODEL=<strong-gemini-model>
RERANK_PROVIDER=gemini
RERANK_MODEL=<cheap-gemini-model>
```

No provider-specific model name is hardcoded beyond the existing global defaults; model names can be changed without source edits.

## 5–7. Task allocation

- chunk knowledge extraction → `extraction`
- retrieval reranking → `rerank`
- global canonical integration → `integration`
- diagnostic reasoning → `diagnosis`
- schematic vision extraction → `vision`
- expert interviewer → `expert`

This is deterministic task routing, not an adaptive complexity classifier. Adaptive routing should be added only after latency/cost/quality telemetry shows it is useful.

## Files added/modified

### Added
- `backend/llm/gateway.py`
- `tests/test_phase8e_routing.py`
- `docs/Phase8E.md`

### Modified
- `backend/ingestion/chunking.py`
- `backend/diagnostics/agent.py`
- `backend/knowledge/component_extraction.py`
- `backend/knowledge/global_integration.py`
- `backend/retrieval/reranker.py`
- `backend/llm/provider.py`
- `backend/llm/__init__.py`
- `backend/config.py`
- `.env.example`
- affected tests updated for the gateway boundary
