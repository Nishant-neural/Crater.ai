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
