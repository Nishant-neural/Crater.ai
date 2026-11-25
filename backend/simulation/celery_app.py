"""Celery app for twin-verifier jobs (contracts README §8.1; amendment
resolution 2: Celery + Redis everywhere, no Redis Streams).

- Broker: Redis db 0. Result backend: Redis db 1. Redis holds no
  durable state by design — queue contents are reconstructible from
  the ``verify_jobs`` rows in ``queued`` / ``running`` status.
- Tasks retry 3x with exponential backoff; a retried job re-runs the
  deterministic tournament from its Postgres row, never a second
  verification of a different twin state.
- ``task_track_started`` so a worker marks the job running as soon
  as it picks it up (visible in Flower / ``celery inspect``).

The API process enqueues with ``run_verify_job.delay(job_id)``
(``backend/simulation/tasks.py``); the worker process runs
``celery -A backend.simulation.celery_app:celery_app worker``.
``include`` registers the tasks module for the worker.
"""
from __future__ import annotations

from celery import Celery

from backend.config import settings

_DEFAULT_BROKER = "redis://localhost:6379/0"


def _result_backend_for(broker_url: str) -> str:
    """Same Redis server as the broker, database 1."""
    url = broker_url.rstrip("/")
    head, _, tail = url.rpartition("/")
    if tail.isdigit():
        return f"{head}/1"
    return f"{url}/1"


broker_url = settings.celery_broker_url or settings.redis_url or _DEFAULT_BROKER
result_backend_url = settings.celery_result_backend or _result_backend_for(broker_url)

celery_app = Celery(
    "crater_twin_verifier",
    broker=broker_url,
    backend=result_backend_url,
    include=["backend.simulation.tasks"],
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_default_queue="twin-verifier",
)
