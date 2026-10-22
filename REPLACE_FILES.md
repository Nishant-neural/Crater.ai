# Resumable ingestion replacement

Replace these files on top of the Phase 9 progress project:

- `backend/api/routes/ingestion.py`
- `backend/ingestion/pipeline.py`
- `backend/ingestion/checkpoints.py` (new)
- `backend/ingestion/ocr.py` (new)
- `frontend/src/components/MachineOnboarding.jsx`
- `frontend/src/api/client.js`
- `frontend/src/App.css`

Features:
1. Retry failed ingestion without re-uploading the PDF.
2. Resume from the last persisted stage/checkpoint.
3. Progress is persisted in the existing `IngestionJob.stage` plus a durable JSON checkpoint under `data/ingestion_checkpoints/`.
4. Completed pages, vector indexing, knowledge chunks, and schematics are skipped on retry.
5. Tesseract is optional; the pipeline attempts a native-text-only retry when the PDF loader reports a Tesseract error and sets `CRATER_DISABLE_OCR=1`.
6. OCR absence is reported as a warning rather than a job failure when the underlying PDF loader supports that flag.
7. UI clearly distinguishes processing, FAILED, and READY and provides a Retry button.

Restart FastAPI after replacing files. Existing databases do not require new columns.

# General schema-normalization hardening (added)

Files changed/added for this upgrade:
- `backend/knowledge/normalization.py` (new shared alias normalizer, original-payload retention, deterministic IDs, unresolved ledger helper)
- `backend/knowledge/machine_model.py` (uses shared normalizer and preserves extra normalization metadata)
- `backend/knowledge/evidence.py` (invalid claim statuses remain strict validation errors)
- `backend/knowledge/validation.py` (semantic validation plus `validate_machine_model_with_ledger`)
- `backend/knowledge/__init__.py` (exports the new API)
- `tests/knowledge/test_schema_normalization.py` (regression tests)
- `docs/SCHEMA_NORMALIZATION.md` (contract and integration guidance)

The unresolved ledger API currently returns records; it does not automatically persist them to the database. The integration layer must persist returned records before claiming the ledger is durable.
