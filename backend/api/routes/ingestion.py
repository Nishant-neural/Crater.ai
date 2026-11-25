"""Durable, resumable document ingestion endpoints.

Phase 3 ingestion extraction (contracts README §3.1, §8.1): the
handlers below only create the job row and enqueue it — the work
runs on a Celery worker (``backend/ingestion/tasks.py``), progress
lives in Postgres (``ingestion_job_progress``), and a broker outage
is a loud 503 naming Redis/Celery, never a silent in-process run.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timedelta

from pydantic import BaseModel
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from backend.db.models import Chunk, Document, DocType, FailureMode, IngestionJob, IngestionStatus, Revision
from backend.db.session import get_session
from backend.ingestion.checkpoints import load_checkpoint, save_checkpoint
from backend.ingestion.ocr import optional_ocr_warning
from backend.ingestion.pipeline import _phase1_registry_for_product, lazy_ingest_summary
from backend.ingestion.tasks import run_ingestion_job

router = APIRouter(prefix="/ingest", tags=["ingestion"])
logger = logging.getLogger(__name__)
_UPLOAD_DIR = Path("data") / "uploads"


def _progress_object(job: IngestionJob, session: Session | None) -> dict:
    """The contracts §3.2 progress object, built from recorded state.

    Counts come from the Postgres progress row (pages/chunks done,
    knowledge failures, tier2 deferral, integration flag) and — when
    the caller passes a session — from the stored rows (document page
    count, chunk count, FailureMode rows). ``table_rows_parsed`` is
    the FailureMode-row count for the revision: the deterministic
    table parse persists one FailureMode row per parsed failure mode
    (``component_extraction.persist_machine_knowledge``), so it is
    the checkable row count until the dedicated ``table_rows`` table
    (contracts §3.5) lands. Fields nothing tracks yet are 0, not
    invented.
    """
    cp = load_checkpoint(job.id)
    pages_done = len(cp.get("completed_pages") or [])
    chunks_done = len(cp.get("vector_chunks_done") or [])
    pages_total = 0
    chunks_total = 0
    table_rows_parsed = 0
    if session is not None:
        document = session.get(Document, job.document_id) if job.document_id else None
        if document is not None:
            pages_total = document.page_count or 0
            chunks_total = session.query(Chunk).filter(Chunk.document_id == document.id).count()
        table_rows_parsed = session.query(FailureMode).filter(
            FailureMode.revision_id == job.revision_id).count()
    return {
        "pages_total": pages_total,
        "pages_done": pages_done,
        "chunks_total": chunks_total,
        "chunks_done": chunks_done,
        "table_rows_total": 0,
        "table_rows_parsed": table_rows_parsed,
        "tier2_deferred_chunks": len(cp.get("tier2_deferred") or []),
        "knowledge_failures": sorted((cp.get("knowledge_failures") or {}).keys()),
        "integration_done": bool(cp.get("integration_done")),
    }


def _job_response(job: IngestionJob, reused: bool = False, session: Session | None = None) -> dict:
    ocr_warning = optional_ocr_warning()
    parts = (job.stage or "queued|0|Queued").split("|", 2)
    try:
        percent = int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        percent = 0
    label = parts[2] if len(parts) > 2 else (parts[0] if parts else "Queued")
    return {"job_id": job.id, "document_id": job.document_id, "revision_id": job.revision_id,
            "status": job.status.value, "stage": job.stage, "percent": percent, "stage_label": label,
            "attempt_count": job.attempt_count, "error_message": job.error_message,
            "progress": _progress_object(job, session),
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

def _queue(job: IngestionJob) -> dict:
    """Enqueue the job on Celery (contracts §8.1).

    The job row is already committed, so the worker can pick it up.
    A broker outage is a loud 503 naming Redis/Celery — the upload
    is accepted into Postgres but the caller is told the queue is
    down instead of the work silently never running.
    """
    try:
        run_ingestion_job.delay(job.id)
    except Exception as exc:  # noqa: BLE001 - broker outage is a 503, not a 500
        logger.exception("Ingestion queue unavailable for job %s", job.id)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"Ingestion queue unavailable (Redis/Celery): {exc}",
        )
    return _job_response(job)

@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def ingest_document(revision_id: str = Form(...), doc_type: DocType = Form(...), title: str = Form(...), idempotency_key: str = Form(..., min_length=1, max_length=255), file: UploadFile = File(...), ingest_tier2: bool | None = Form(default=None), session: Session = Depends(get_session)):
    # Phase 4, Option C: the Tier-2 flag. An explicit value wins; otherwise
    # the registry's per-product default; otherwise True (Phase 1 behaviour).
    revision = session.get(Revision, revision_id)
    if not revision: raise HTTPException(404, "Revision not found")
    source_path, sha = _store_upload(file)
    existing = session.query(IngestionJob).filter(IngestionJob.revision_id==revision_id, IngestionJob.idempotency_key==idempotency_key).first()
    if existing:
        if existing.content_sha256 != sha: raise HTTPException(409, "Idempotency key was already used for different content")
        return _job_response(existing, reused=True, session=session)
    duplicate = session.query(IngestionJob).filter(IngestionJob.revision_id==revision_id, IngestionJob.content_sha256==sha).first()
    if duplicate: return _job_response(duplicate, reused=True, session=session)
    job = IngestionJob(revision_id=revision_id, idempotency_key=idempotency_key, content_sha256=sha, doc_type=doc_type, title=title, source_path=str(source_path), stage="queued|0|Upload complete; queued for processing")
    session.add(job); session.commit(); session.refresh(job)
    _registry = _phase1_registry_for_product(session, revision.product_id)
    _resolved_tier2 = ingest_tier2 if ingest_tier2 is not None else (_registry.tier2_default if _registry is not None else True)
    save_checkpoint(job.id, ingest_tier2=bool(_resolved_tier2))
    return _queue(job)


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
    # NOTE (gateway audit §4; contracts README §3.1): the 5-minute stale
    # heuristic that marked processing jobs failed lived here and is
    # DELETED. Revision readiness comes from recorded progress, not a
    # clock guess; a stuck job is retried via POST /jobs/{id}/retry.

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
        "jobs": [_job_response(j, session=session) for j in jobs],
    }

@router.get("/jobs/{job_id}")
def get_ingestion_job(job_id: str, session: Session = Depends(get_session)):
    job = session.get(IngestionJob, job_id)
    if not job: raise HTTPException(404, "Ingestion job not found")
    return _job_response(job, session=session)

@router.post("/jobs/{job_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry_ingestion_job(job_id: str, session: Session = Depends(get_session)):
    job = session.get(IngestionJob, job_id)
    if not job: raise HTTPException(404, "Ingestion job not found")
    if job.status == IngestionStatus.completed: return _job_response(job, reused=True, session=session)
    if job.status == IngestionStatus.processing:
        if not job.updated_at or job.updated_at >= datetime.utcnow() - timedelta(minutes=5):
            raise HTTPException(409, "Ingestion job is already running")
        job.status = IngestionStatus.failed
        job.error_message = "Ingestion worker stopped; retrying from the saved checkpoint."
    if not Path(job.source_path).is_file(): raise HTTPException(409, "Original upload is unavailable; upload the document again")
    job.attempt_count += 1; job.status = IngestionStatus.queued; job.error_message = None; session.commit(); session.refresh(job)
    return _queue(job)


class LazyIngestRequest(BaseModel):
    pages: list[int]


@router.post("/jobs/{job_id}/lazy-ingest", status_code=status.HTTP_202_ACCEPTED)
def lazy_ingest_job(job_id: str, payload: LazyIngestRequest, session: Session = Depends(get_session)):
    """Phase 4 lazy ingest: release one section (pages) of deferred Tier-2 chunks.

    Only chunks covering the requested pages are released from
    ``tier2_deferred``; the rest stay deferred, and the bounded job that
    follows extracts only the released chunks through the normal v12 LLM
    path. The response carries a condensed summary (pages, counts, source
    types) — never raw chunk content."""
    job = session.get(IngestionJob, job_id)
    if not job: raise HTTPException(404, "Ingestion job not found")
    if job.status == IngestionStatus.processing:
        raise HTTPException(409, "Ingestion job is already running")
    if not payload.pages: raise HTTPException(422, "pages must list at least one page")
    summary = lazy_ingest_summary(session, job_id, payload.pages)
    if not summary["chunks_released"]:
        return {"job_id": job.id, "status": job.status.value, "released": [], **summary}
    save_checkpoint(job_id, tier2_deferred=summary["remaining_deferred"])
    job.attempt_count += 1; job.status = IngestionStatus.queued; job.error_message = None; session.commit(); session.refresh(job)
    response = _queue(job)
    return {**response, **summary}
