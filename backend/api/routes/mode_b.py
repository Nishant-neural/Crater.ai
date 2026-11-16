"""Phase 6 — Mode B on-demand endpoints.

The individual user with a big manual for an unknown machine. The
upload itself goes through the existing POST /ingest with
ingest_tier2=false (Tier 1 first); these endpoints are the Mode B
session around that job: register the product as on-demand, watch
per-section progress, start a diagnosis on the partial model, and
graduate to a pre-built model when the family is contracted.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from backend.db.models import IngestionJob, Product, Revision
from backend.db.session import get_session
from backend.diagnostics.agent import start_session
from backend.ingestion.checkpoints import load_checkpoint
from backend.ingestion.mode_b import (
    check_symptom_readiness, get_product_mode, graduate_to_prebuilt,
    mode_b_status, register_mode_b,
)
from backend.ingestion.pipeline import _phase1_registry_for_product

router = APIRouter(prefix="/mode-b", tags=["mode-b"])


class StartModeBRequest(BaseModel):
    revision_id: str
    job_id: str


class DiagnoseRequest(BaseModel):
    symptom: str


class GraduateRequest(BaseModel):
    actor: str
    job_id: str


def _job_and_registry(db: DBSession, job_id: str):
    job = db.get(IngestionJob, job_id)
    if job is None:
        raise HTTPException(404, "Ingestion job not found")
    revision = db.get(Revision, job.revision_id)
    if revision is None:
        raise HTTPException(404, "Revision not found")
    registry = _phase1_registry_for_product(db, revision.product_id)
    if registry is None:
        raise HTTPException(409, "No repair-case registry for this product; "
                                 "Mode B needs the Tier 1 page list")
    return job, revision, registry


@router.post("/start")
def start_mode_b(payload: StartModeBRequest, db: DBSession = Depends(get_session)):
    """Register a product as Mode B (on-demand) for one ingest job.
    The upload itself is POST /ingest with ingest_tier2=false (Tier 1
    first); this call marks the product on-demand and returns the
    initial per-section progress."""
    job, _revision, registry = _job_and_registry(db, payload.job_id)
    if job.revision_id != payload.revision_id:
        raise HTTPException(400, "job_id does not belong to revision_id")
    revision = db.get(Revision, payload.revision_id)
    register_mode_b(db, revision.product_id, payload.job_id)
    return mode_b_status(db, job, load_checkpoint(job.id), registry)


@router.get("/jobs/{job_id}/progress")
def job_progress(job_id: str, db: DBSession = Depends(get_session)):
    """Per-section progress for a Mode B job, with partial-model availability."""
    job, _revision, registry = _job_and_registry(db, job_id)
    return mode_b_status(db, job, load_checkpoint(job.id), registry)


@router.post("/jobs/{job_id}/diagnose")
def diagnose_on_partial(job_id: str, payload: DiagnoseRequest,
                        db: DBSession = Depends(get_session)):
    """Start a diagnosis on the partial model. If the symptom needs a
    section not yet ingested, the answer says so plainly with the wait
    — it never stalls silently and never starts a diagnosis on pages
    that do not exist yet."""
    job, revision, registry = _job_and_registry(db, job_id)
    if not payload.symptom or not payload.symptom.strip():
        raise HTTPException(422, "symptom is required")
    readiness = check_symptom_readiness(db, job, load_checkpoint(job.id),
                                        registry, payload.symptom.strip())
    if not readiness["available"]:
        return {"available": False, "job_id": job_id,
                "reason": readiness["reason"],
                "wait_message": readiness["wait_message"],
                "missing_pages": readiness["missing_pages"],
                "pages_to_lazy_ingest": readiness["pages_to_lazy_ingest"],
                "case_id": readiness.get("case_id"),
                "partial_model": readiness["partial_model"]}
    session = start_session(db, revision.product_id, revision.id,
                            payload.symptom.strip())
    return {"available": True, "job_id": job_id, "session_id": session.id,
            "case_id": readiness.get("case_id"),
            "note": readiness.get("note") or "Diagnosis started on the partial model.",
            "partial_model": readiness["partial_model"]}


@router.get("/products/{product_id}")
def product_mode(product_id: str, db: DBSession = Depends(get_session)):
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(404, "Product not found")
    row = get_product_mode(db, product_id)
    return {"product_id": product_id,
            "mode": row.mode if row is not None else "unknown",
            "background_job_id": row.background_job_id if row is not None else None,
            "graduated_at": row.graduated_at.isoformat() if row is not None and row.graduated_at else None,
            "graduated_by": row.graduated_by if row is not None else None}


@router.post("/products/{product_id}/graduate")
def graduate_product(product_id: str, payload: GraduateRequest,
                     db: DBSession = Depends(get_session)):
    """Graduation path: the same family is contracted, so the on-demand
    model becomes a pre-built model (Mode A)."""
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(404, "Product not found")
    try:
        result = graduate_to_prebuilt(db, product_id, payload.job_id, payload.actor)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return result
