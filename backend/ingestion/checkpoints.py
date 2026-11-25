"""Postgres-backed ingestion progress (contracts README §3.2, §8.1).

Replaces the filesystem checkpoint files this module used to write
(``data/ingestion_checkpoints/<job_id>.json``). Progress is data in
the ``ingestion_job_progress`` table, so a job survives a container
restart and a second worker sees the same state — filesystem
checkpoints did neither. The load/save/clear signatures are unchanged
from the file version, so the pipeline, mode_b and the routes call it
exactly as before; only the storage moved.

The progress dict keeps the same keys the pipeline's resume logic
reads: stage, completed_pages, vectors_done, vector_chunks_done,
knowledge_done, knowledge_failures, schematics_done,
integration_done, verification_warnings, tier2_deferred, ingest_tier2.
"""
from __future__ import annotations

from typing import Any

from backend.db.models import Base, IngestionJobProgress
from backend.db.session import SessionLocal, engine

_DEFAULT: dict[str, Any] = {
    "stage": "start",
    "completed_pages": [],
    "vectors_done": False,
    "vector_chunks_done": [],
    "knowledge_done": [],
    "knowledge_failures": {},
    "schematics_done": [],
    "integration_done": False,
    "verification_warnings": [],
    "tier2_deferred": [],
    "ingest_tier2": True,
}

_table_ready = False


def _ensure_table() -> None:
    """Create the progress table if it does not exist yet.

    ``init_db`` (``create_all``) creates it on app startup; this is
    the backstop for workers and scripts that touch progress before
    any app startup ran. Only this table is created here, never the
    whole schema.
    """
    global _table_ready
    if _table_ready:
        return
    Base.metadata.create_all(bind=engine, tables=[IngestionJobProgress.__table__])
    _table_ready = True


def load_checkpoint(job_id: str) -> dict[str, Any]:
    _ensure_table()
    session = SessionLocal()
    try:
        row = session.get(IngestionJobProgress, job_id)
        if row is None or not isinstance(row.progress, dict):
            return dict(_DEFAULT)
        data = dict(_DEFAULT)
        data.update(row.progress)
        return data
    finally:
        session.close()


def save_checkpoint(job_id: str, **updates: Any) -> dict[str, Any]:
    if not job_id:
        raise ValueError("save_checkpoint requires a job_id")
    data = load_checkpoint(job_id)
    data.update(updates)
    session = SessionLocal()
    try:
        row = session.get(IngestionJobProgress, job_id)
        if row is None:
            row = IngestionJobProgress(job_id=job_id, progress=dict(data))
            session.add(row)
        else:
            # Assign a fresh dict: plain JSON columns track assignment,
            # not in-place mutation.
            row.progress = dict(data)
        session.commit()
        return dict(data)
    finally:
        session.close()


def clear_checkpoint(job_id: str) -> None:
    _ensure_table()
    session = SessionLocal()
    try:
        row = session.get(IngestionJobProgress, job_id)
        if row is not None:
            session.delete(row)
            session.commit()
    finally:
        session.close()
