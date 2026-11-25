"""POST /v1/verify — verification as a job, not a call (twin audit p.11).

The diagnosis loop never waits on a twin call: it POSTs a verify job,
gets 202 with a ``job_id``, and polls ``GET /v1/verify/jobs/{job_id}``
for the verdicts. Phase 3 twin-verifier extraction (contracts README
§5.2): the job store is the Postgres ``verify_jobs`` table — the
monolith's in-memory ``_JOBS`` dict is gone, so a job survives a
restart — and the work runs on a Celery worker (``backend/simulation/
tasks.py``), which runs the existing tournament and verification
logic on an isolated copy of the twin. The route only creates the row
and enqueues; it never runs the tournament itself.

Four verdicts per result (audit p.12): VALIDATED / REJECTED /
CANNOT_EXPRESS / NOT_VERIFIED. No result without ``rules_used`` may
be VALIDATED — enforced in ``verify_service.map_verdict_row``.

Idempotency (contracts §1.2): the same key + the same payload reuses
the original job (no second dispatch); the same key + a different
payload is 409. Timeouts (contracts §5.2): 2 s per hypothesis, 10 s
per job — a timed-out job SUCCEEDS with every result NOT_VERIFIED and
reason ``"twin verification timed out"`` (see ``tasks.py``).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from backend.db.models import VerifyJob
from backend.db.session import get_session
from backend.simulation.tasks import (
    job_cap_seconds, run_verify_job,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/verify", tags=["verify"])

_EMPTY_COUNTS = {"validated": 0, "rejected": 0, "cannot_express": 0,
                 "not_verified": 0}


class VerifyHypothesis(BaseModel):
    cause: str
    repair_name: str | None = None
    fault_component_id: str | None = None
    failure_mode_name: str | None = None


class VerifyRequest(BaseModel):
    idempotency_key: str | None = None
    twin_id: str | None = None
    revision_id: str | None = None
    session_id: str | None = None
    hypotheses: list[VerifyHypothesis] = []
    timeout_ms: int | None = 2000


def _job_document(job: VerifyJob) -> dict:
    """The GET job shape (contracts §5.2): ``{job_id, status,
    results, counts}``. A queued/running job reports empty results
    and zero counts; a succeeded job reports the stored document."""
    stored = dict(job.result or {})
    return {
        "job_id": job.id,
        "status": job.status,
        "twin_id": stored.get("twin_id") or job.twin_id,
        "results": stored.get("results") or [],
        "counts": stored.get("counts") or dict(_EMPTY_COUNTS),
    }


def _accepted(job: VerifyJob, payload: VerifyRequest) -> dict:
    return {
        "job_id": job.id,
        "status": job.status,
        "twin_id": job.twin_id,
        "estimated_ms": int(job_cap_seconds(payload.model_dump(mode="json")) * 1000),
    }


def _queue(job: VerifyJob) -> dict:
    """Enqueue the job on Celery (contracts §8.1).

    The job row is already committed, so the worker can pick it up.
    A broker outage is a loud 503 naming Redis/Celery — the job is
    accepted into Postgres but the caller is told the queue is down
    instead of the work silently never running.
    """
    try:
        run_verify_job.delay(job.id)
    except Exception as exc:  # noqa: BLE001 - broker outage is a 503, not a 500
        logger.exception("Verify queue unavailable for job %s", job.id)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"Verify queue unavailable (Redis/Celery): {exc}",
        )
    return {"job_id": job.id, "status": job.status, "twin_id": job.twin_id}


@router.post("", status_code=202)
def create_verify_job(payload: VerifyRequest, db: DBSession = Depends(get_session)):
    """Create a verify job. Returns 202 with the job id; the job runs
    the existing tournament/verification logic on an isolated copy of
    the twin and its result is available at the job-status endpoint."""
    if not payload.hypotheses:
        raise HTTPException(422, "at least one hypothesis is required")
    request = payload.model_dump(mode="json")
    if payload.idempotency_key:
        existing = (db.query(VerifyJob)
                    .filter_by(idempotency_key=payload.idempotency_key)
                    .order_by(VerifyJob.created_at.desc()).first())
        if existing is not None:
            if (existing.request or {}) != request:
                raise HTTPException(
                    409, "Idempotency key was already used for a different verify request")
            return _accepted(existing, payload)
    job = VerifyJob(
        twin_id=payload.twin_id,
        revision_id=payload.revision_id,
        session_id=payload.session_id,
        idempotency_key=payload.idempotency_key,
        status="queued",
        request=request,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    _queue(job)
    return _accepted(job, payload)


@router.get("/jobs/{job_id}")
def get_verify_job(job_id: str, db: DBSession = Depends(get_session)):
    """Job status: ``{job_id, status, results, counts}``. Status is
    ``queued`` / ``running`` / ``succeeded`` / ``failed``; a job whose
    twin was unavailable still succeeds, with every result
    NOT_VERIFIED and a reason."""
    job = db.get(VerifyJob, job_id)
    if job is None:
        raise HTTPException(404, "Verify job not found")
    return _job_document(job)
