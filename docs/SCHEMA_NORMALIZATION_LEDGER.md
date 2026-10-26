# Schema normalization and unresolved-fact ledger

All LLM extraction boundaries should normalize aliases before Pydantic validation. Normalization is loss-aware and does not establish factual truth.

The `unresolved_facts` table stores records that fail extraction, semantic validation, or global integration. The helper `persist_unresolved_fact` is idempotent for `(revision_id, source_id, fact_type)` and reopens a previously recorded issue when encountered again.

Current integration points:
- single and batched text extraction in `backend/knowledge/component_extraction.py`
- schematic vision extraction in `backend/schematic/vision_extraction.py`
- ingestion failure and semantic-validation handling in `backend/ingestion/pipeline.py`
- global integration output normalization and unresolved record persistence in `backend/knowledge/global_integration.py`

The ledger is a review/recovery mechanism, not proof that any fact is true. Global integration completeness and simulation eligibility must continue to be checked independently.

Verification performed: Python compileall passed; two focused ledger/normalization tests passed. Full CNC-manual ingestion and full-suite tests have not been run in this environment.
