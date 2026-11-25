"""Celery tasks for ingestion jobs (contracts README §8.1).

This is the BackgroundTasks replacement: the route handlers in
``backend/api/routes/ingestion.py`` only enqueue (``.delay``) and
return 202; the work runs here, on a worker, resumable from the
Postgres progress row. The body of ``run_ingestion_job`` is the
former ``_run_job_background`` from the routes module, moved
verbatim — same session handling, same stage strings, same
mark-failed behaviour — plus a re-raise after the failure is
recorded so Celery retries 3x with exponential backoff (a retry
resumes from progress, never a whole-manual restart).
"""
from __future__ import annotations

import logging
from pathlib import Path

from backend.db.models import IngestionJob, IngestionStatus, Revision
from backend.db.session import SessionLocal
from backend.ingestion.celery_app import celery_app
from backend.ingestion.checkpoints import load_checkpoint
from backend.ingestion.pipeline import ingest_pdf

logger = logging.getLogger(__name__)
_IMAGE_DIR = Path("data") / "images"


@celery_app.task(
    bind=True,
    name="ingestion.run_job",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def run_ingestion_job(self, job_id: str) -> None:
    session = SessionLocal()
    try:
        job = session.get(IngestionJob, job_id)
        if not job:
            return
        revision = session.get(Revision, job.revision_id)
        if not revision:
            job.status = IngestionStatus.failed
            job.stage = "failed|0|Revision not found"
            job.error_message = "Revision not found"
            session.commit()
            return
        try:
            job.status = IngestionStatus.processing
            session.commit()

            def progress(percent: int, label: str):
                current = session.get(IngestionJob, job_id)
                if current:
                    current.status = IngestionStatus.processing
                    current.stage = f"processing|{max(0, min(100, int(percent)))}|{label}"
                    session.commit()

            _tier2_flag = load_checkpoint(job.id).get("ingest_tier2", True)
            document = ingest_pdf(
                session=session, pdf_path=job.source_path, revision_id=revision.id,
                product_id=revision.product_id, doc_type=job.doc_type,
                title=job.title, image_out_dir=_IMAGE_DIR / revision.id,
                existing_document_id=job.document_id, progress_callback=progress,
                job_id=job.id, ingest_tier2=_tier2_flag)
            job = session.get(IngestionJob, job_id)
            job.document_id = document.id
            job.status = IngestionStatus.completed
            job.stage = "completed|100|Ingestion and machine-knowledge verification complete"
            job.error_message = None
            session.commit()
        except Exception as exc:
            # Logging only: the traceback goes to the server log (uvicorn
            # console), since the UI only ever sees the stored error_message.
            # No session/job state, control flow, or error_message change here.
            logger.exception("Background ingestion job %s failed", job_id)
            session.rollback()
            job = session.get(IngestionJob, job_id)
            if job:
                job.status = IngestionStatus.failed
                # Preserve the last persisted stage so retry knows where to resume.
                if not job.stage or not job.stage.startswith("processing|"):
                    job.stage = "failed|0|Ingestion failed"
                job.error_message = str(exc)[:2000]
                session.commit()
            # Re-raise so Celery retries 3x with exponential backoff;
            # the retry resumes from the Postgres progress row.
            raise
    finally:
        session.close()
