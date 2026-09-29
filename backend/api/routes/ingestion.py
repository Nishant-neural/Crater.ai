"""Durable, resumable document ingestion endpoints."""
from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from backend.db.models import Document, DocType, IngestionJob, IngestionStatus, Revision
from backend.db.session import SessionLocal, get_session
from backend.ingestion.ocr import optional_ocr_warning
from backend.ingestion.pipeline import ingest_pdf

router = APIRouter(prefix="/ingest", tags=["ingestion"])
_UPLOAD_DIR = Path("data") / "uploads"
_IMAGE_DIR = Path("data") / "images"

def _job_response(job: IngestionJob, reused: bool = False) -> dict:
    ocr_warning = optional_ocr_warning()
    parts = (job.stage or "queued|0|Queued").split("|", 2)
    try: percent = int(parts[1]) if len(parts) > 1 else 0
    except ValueError: percent = 0
    label = parts[2] if len(parts) > 2 else (parts[0] if parts else "Queued")
    return {"job_id": job.id, "document_id": job.document_id, "revision_id": job.revision_id,
            "status": job.status.value, "stage": job.stage, "percent": percent, "stage_label": label,
            "attempt_count": job.attempt_count, "error_message": job.error_message,
            "ocr_available": ocr_warning is None, "ocr_warning": ocr_warning, "reused": reused}

def _store_upload(file: UploadFile) -> tuple[Path, str]:
    if not file.filename or Path(file.filename).suffix.lower() != ".pdf":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Only PDF uploads are supported")
    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    temporary = _UPLOAD_DIR / f"{uuid4()}.uploading"
    digest = hashlib.sha256()
    with temporary.open("wb") as output:
        while block := file.file.read(1024 * 1024):
            digest.update(block); output.write(block)
    sha = digest.hexdigest(); content_path = _UPLOAD_DIR / f"{sha}.pdf"
    if content_path.exists(): temporary.unlink()
    else: temporary.replace(content_path)
    return content_path, sha

def _run_job_background(job_id: str) -> None:
    session = SessionLocal()
    try:
        job = session.get(IngestionJob, job_id)
        if not job: return
        revision = session.get(Revision, job.revision_id)
        if not revision:
            job.status = IngestionStatus.failed; job.stage = "failed|0|Revision not found"; job.error_message = "Revision not found"; session.commit(); return
        try:
            job.status = IngestionStatus.processing; session.commit()
            def progress(percent: int, label: str):
                current = session.get(IngestionJob, job_id)
                if current:
                    current.status = IngestionStatus.processing
                    current.stage = f"processing|{max(0,min(100,int(percent)))}|{label}"
                    session.commit()
            document = ingest_pdf(session=session, pdf_path=job.source_path, revision_id=revision.id, product_id=revision.product_id,
                doc_type=job.doc_type, title=job.title, image_out_dir=_IMAGE_DIR / revision.id,
                existing_document_id=job.document_id, progress_callback=progress, job_id=job.id)
            job = session.get(IngestionJob, job_id); job.document_id = document.id
            job.status = IngestionStatus.completed; job.stage = "completed|100|Ingestion complete"; job.error_message = None; session.commit()
        except Exception as exc:
            session.rollback(); job = session.get(IngestionJob, job_id)
            if job:
                job.status = IngestionStatus.failed
                # Preserve the last persisted stage so retry knows where to resume.
                if not job.stage or not job.stage.startswith("processing|"):
                    job.stage = "failed|0|Ingestion failed"
                job.error_message = str(exc)[:2000]
                session.commit()
    finally:
        session.close()

def _queue(job: IngestionJob, tasks: BackgroundTasks):
    tasks.add_task(_run_job_background, job.id); return _job_response(job)

@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def ingest_document(background_tasks: BackgroundTasks, revision_id: str = Form(...), doc_type: DocType = Form(...), title: str = Form(...), idempotency_key: str = Form(..., min_length=1, max_length=255), file: UploadFile = File(...), session: Session = Depends(get_session)):
    revision = session.get(Revision, revision_id)
    if not revision: raise HTTPException(404, "Revision not found")
    source_path, sha = _store_upload(file)
    existing = session.query(IngestionJob).filter(IngestionJob.revision_id==revision_id, IngestionJob.idempotency_key==idempotency_key).first()
    if existing:
        if existing.content_sha256 != sha: raise HTTPException(409, "Idempotency key was already used for different content")
        return _job_response(existing, reused=True)
    duplicate = session.query(IngestionJob).filter(IngestionJob.revision_id==revision_id, IngestionJob.content_sha256==sha).first()
    if duplicate: return _job_response(duplicate, reused=True)
    job = IngestionJob(revision_id=revision_id, idempotency_key=idempotency_key, content_sha256=sha, doc_type=doc_type, title=title, source_path=str(source_path), stage="queued|0|Upload complete; queued for processing")
    session.add(job); session.commit(); session.refresh(job); return _queue(job, background_tasks)


@router.get("/revisions/{revision_id}/status")
def get_revision_ingestion_status(revision_id: str, session: Session = Depends(get_session)):
    """Return durable ingestion state for a revision so the UI can recover after reload."""
    revision = session.get(Revision, revision_id)
    if not revision:
        raise HTTPException(404, "Revision not found")

    jobs = (
        session.query(IngestionJob)
        .filter(IngestionJob.revision_id == revision_id)
        .order_by(IngestionJob.created_at.desc())
        .all()
    )
    documents = (
        session.query(Document)
        .filter(Document.revision_id == revision_id)
        .order_by(Document.ingested_at.desc())
        .all()
    )
    # A browser reload is harmless, but a backend restart can leave a job marked
    # processing forever. Treat sufficiently stale jobs as failed/retryable; the
    # filesystem checkpoint still contains the exact completed chunk IDs.
    stale_cutoff = datetime.utcnow() - timedelta(minutes=5)
    for j in jobs:
        if j.status == IngestionStatus.processing and j.updated_at and j.updated_at < stale_cutoff:
            j.status = IngestionStatus.failed
            j.error_message = "Ingestion worker stopped; saved checkpoint is available for retry."
            session.commit()

    completed = [j for j in jobs if j.status == IngestionStatus.completed]
    active = [j for j in jobs if j.status in (IngestionStatus.queued, IngestionStatus.processing)]
    failed = [j for j in jobs if j.status == IngestionStatus.failed]
    return {
        "revision_id": revision_id,
        "ready": bool(completed),
        "status": "processing" if active else ("ready" if completed else ("failed" if failed else "empty")),
        "document_count": len(documents),
        "completed_count": len(completed),
        "active_count": len(active),
        "failed_count": len(failed),
        "documents": [{"id": d.id, "title": d.title, "doc_type": d.doc_type.value, "page_count": d.page_count, "ingested_at": d.ingested_at.isoformat()} for d in documents],
        "jobs": [_job_response(j) for j in jobs],
    }

@router.get("/jobs/{job_id}")
def get_ingestion_job(job_id: str, session: Session = Depends(get_session)):
    job = session.get(IngestionJob, job_id)
    if not job: raise HTTPException(404, "Ingestion job not found")
    return _job_response(job)

@router.post("/jobs/{job_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry_ingestion_job(job_id: str, background_tasks: BackgroundTasks, session: Session = Depends(get_session)):
    job = session.get(IngestionJob, job_id)
    if not job: raise HTTPException(404, "Ingestion job not found")
    if job.status == IngestionStatus.completed: return _job_response(job, reused=True)
    if job.status == IngestionStatus.processing:
        if not job.updated_at or job.updated_at >= datetime.utcnow() - timedelta(minutes=5):
            raise HTTPException(409, "Ingestion job is already running")
        job.status = IngestionStatus.failed
        job.error_message = "Ingestion worker stopped; retrying from the saved checkpoint."
    if not Path(job.source_path).is_file(): raise HTTPException(409, "Original upload is unavailable; upload the document again")
    job.attempt_count += 1; job.status = IngestionStatus.queued; job.error_message = None; session.commit(); session.refresh(job)
    return _queue(job, background_tasks)
