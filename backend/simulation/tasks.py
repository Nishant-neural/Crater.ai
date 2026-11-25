"""Celery tasks for twin-verifier jobs (contracts README §5.2).

This is the execution side of ``POST /v1/verify``: the route handler
in ``backend/api/routes/verify.py`` only creates the Postgres
``VerifyJob`` row and enqueues (``.delay``) and returns 202; the work
runs here, on a worker, on an isolated copy of the twin (the existing
tournament + verification logic in ``backend/simulation/
verify_service.py`` — nothing about the verdict logic changed).

Timeouts (contracts README §5.2 / openapi-twin-verifier.yaml): 2 s
per hypothesis, 10 s per job. The tournament runs in a worker thread
with its own DB session, bounded by the job cap
``min(10 s, timeout_ms * n_hypotheses)``; on timeout the job still
SUCCEEDS, with every result NOT_VERIFIED and reason
``"twin verification timed out"`` — a timed-out job is not ``failed``,
and diagnosis proceeds. ``failed`` is reserved for the job itself
erroring (bad twin id, worker crash), which retries 3x with
exponential backoff like every other Celery task in this repo.

``execute_job(session, job_id)`` is the session-taking core the route
tests drive inline (the Celery task wraps it with ``SessionLocal``);
``run_verify_with_timeout`` is the bounded-execution seam the timeout
test drives directly.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from backend.db.models import VerifyJob
from backend.db.session import SessionLocal
from backend.simulation.celery_app import celery_app
from backend.simulation.verify_service import execute_verify_job

logger = logging.getLogger(__name__)

# Contracts §5.2: 2 s per hypothesis, 10 s per job.
PER_HYPOTHESIS_TIMEOUT_MS = 2000
JOB_TIMEOUT_CAP_MS = 10000
TIMEOUT_REASON = "twin verification timed out"


def job_cap_seconds(payload: dict) -> float:
    """Job timeout cap in seconds: per-hypothesis timeout times the
    hypothesis count, capped at 10 s (contracts §5.2)."""
    timeout_ms = payload.get("timeout_ms") or PER_HYPOTHESIS_TIMEOUT_MS
    n_hypotheses = max(1, len(payload.get("hypotheses") or []))
    return min(JOB_TIMEOUT_CAP_MS, int(timeout_ms) * n_hypotheses) / 1000.0


def timeout_result(payload: dict) -> dict:
    """A succeeded job document for a timed-out run: every hypothesis
    NOT_VERIFIED with the contract timeout reason, never a rejection
    and never a block on ranking."""
    from datetime import datetime, timezone

    from backend.simulation.library_versions import CURRENT_LIBRARY_VERSION

    results = [{
        "cause": hyp.get("cause"),
        "status": "unavailable",
        "fault_component_id": hyp.get("fault_component_id"),
        "symptom_path": None,
        "rules_used": [],
        "trace": [],
        "cannot_express": False,
        "twin_library_version": CURRENT_LIBRARY_VERSION,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "verdict": "NOT_VERIFIED",
        "reason": TIMEOUT_REASON,
    } for hyp in payload.get("hypotheses") or []]
    return {
        "job_id": payload.get("idempotency_key") or "",
        "status": "succeeded",
        "twin_id": payload.get("twin_id"),
        "results": results,
        "counts": {"validated": 0, "rejected": 0, "cannot_express": 0,
                   "not_verified": len(results)},
    }


def run_verify_with_timeout(
    payload: dict,
    cap_seconds: float,
    session=None,
    session_factory: Callable[[], Any] | None = None,
) -> dict:
    """Run ``execute_verify_job`` bounded by the job cap.

    The tournament runs in a worker thread with its own session (the
    DB session is never used by two threads at once: the caller blocks
    on the future). Session ownership: a ``session`` passed in is used
    as-is and never closed here; otherwise a session is created from
    ``session_factory`` (default ``SessionLocal``) and closed here.
    On timeout the thread is left to finish on its own — the
    deterministic engine is fast, so this is a guard, not a routine
    path — and the caller gets the timeout result. Raises
    ``TimeoutError`` never: the timeout IS a result.
    """
    import concurrent.futures

    def _run() -> dict:
        owned = False
        run_session = session
        if run_session is None:
            run_session = (session_factory or SessionLocal)()
            owned = True
        try:
            return execute_verify_job(run_session, payload)
        finally:
            if owned:
                run_session.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_run)
        try:
            return future.result(timeout=cap_seconds)
        except concurrent.futures.TimeoutError:
            logger.warning(
                "twin-verifier: verify job timed out after %.2fs "
                "(%d hypotheses)", cap_seconds,
                len(payload.get("hypotheses") or []))
            return timeout_result(payload)


def execute_job(session, job_id: str,
                session_factory: Callable[[], Any] | None = None) -> dict | None:
    """Execute one queued verify job against ``session``'s database.

    Reads the ``VerifyJob`` row, marks it running, runs the tournament
    bounded by the job cap, and writes the job document back with
    status ``succeeded``. Returns the job document, or None when the
    row does not exist (an unknown job id is ignored, like the
    ingestion task). A timeout is a succeeded NOT_VERIFIED job, not
    an exception — only a real execution error propagates, so the
    Celery task can mark the job ``failed`` and retry it.

    ``session_factory`` (tests) supplies a fresh session on the same
    engine for the worker thread, so a timed-out run never touches
    the caller's session after the caller moved on; without it the
    worker thread runs on ``session`` itself (the Celery task's own
    session, which it closes).
    """
    job = session.get(VerifyJob, job_id)
    if job is None:
        return None
    payload = dict(job.request or {})
    job.status = "running"
    session.commit()
    if session_factory is not None:
        result = run_verify_with_timeout(
            payload, job_cap_seconds(payload), session_factory=session_factory)
    else:
        result = run_verify_with_timeout(
            payload, job_cap_seconds(payload), session=session)
    # The timeout result carries the idempotency key as job_id; the
    # row's own id is authoritative.
    result = dict(result)
    result["job_id"] = job.id
    job.result = result
    job.counts = result.get("counts")
    job.twin_id = result.get("twin_id") or job.twin_id
    job.status = result.get("status") or "succeeded"
    job.error_message = None
    session.commit()
    return result


@celery_app.task(
    bind=True,
    name="twin_verifier.run_job",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def run_verify_job(self, job_id: str) -> None:
    session = SessionLocal()
    try:
        job = session.get(VerifyJob, job_id)
        if job is None:
            return
        try:
            execute_job(session, job_id)
        except Exception as exc:
            # Logging only: the traceback goes to the server log, since
            # the API only ever sees the stored error_message.
            logger.exception("Verify job %s failed", job_id)
            session.rollback()
            job = session.get(VerifyJob, job_id)
            if job is not None:
                job.status = "failed"
                job.error_message = str(exc)[:2000]
                session.commit()
            # Re-raise so Celery retries 3x with exponential backoff.
            raise
    finally:
        session.close()
