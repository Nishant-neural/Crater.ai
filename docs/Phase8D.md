# Phase 8D — Knowledge Integrity, Graph Retrieval & Revision Inheritance

Built on Phase 8C. This phase closes three remaining knowledge-system gaps.

## 1. Completeness / no silent fact loss

Global integration now creates a source-fact accounting ledger. Every persisted source record must be referenced by `source_ids` on a canonical item. Records that cannot be integrated are retained in `unresolved_facts` instead of disappearing.

The integration response includes:
- `completeness.source_total`
- `completeness.accounted_total`
- `completeness.missing_count`
- `completeness.missing_source_ids`
- `completeness.complete`
- `unresolved_facts`

The LLM is therefore a canonicalization engine, not the authority on whether knowledge survives.

## 2. Knowledge-model-aware graph retrieval

Canonical retrieval now performs:

```text
query
 ├─ lexical/entity matching
 ├─ relation matching
 ├─ state/failure/procedure matching
 └─ unresolved/conflict matching
          ↓
     seed entities
          ↓
     graph expansion
          ↓
 related entities + relations + states + failures + procedures + evidence
```

`RetrievedKnowledge.retrieval_path` identifies direct, seed-entity, or graph-derived context. The existing semantic/BM25 chunk retrieval remains the source-evidence path.

## 3. Revision inheritance

`Revision.parent_revision_id` creates an explicit product revision lineage. Global integration resolves knowledge root → child before canonicalization. Child records shadow equivalent parent records. Revision deprecation records can remove inherited records using `target_fact_key` / `source_id`.

Example:

```text
Rev C = Rev B effective knowledge
       + Rev C additions/overrides
       - Rev C deprecations
```

A parent revision must belong to the same product. Cyclic revision inheritance is rejected.

## Files changed

- `backend/knowledge/global_integration.py`
- `backend/knowledge/machine_model.py`
- `backend/retrieval/knowledge.py`
- `backend/retrieval/hybrid.py`
- `backend/db/models.py`
- `backend/api/routes/products.py`
- `backend/api/routes/machine_knowledge.py`
- `backend/api/routes/query.py`
- `tests/test_phase8c_integrity.py`

## Testing

Targeted Phase 8 integration/retrieval tests pass: 8 passed.
