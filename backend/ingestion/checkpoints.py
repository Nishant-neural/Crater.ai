from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

CHECKPOINT_DIR = Path("data") / "ingestion_checkpoints"


def _path(job_id: str) -> Path:
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    return CHECKPOINT_DIR / f"{job_id}.json"


def load_checkpoint(job_id: str) -> dict[str, Any]:
    path = _path(job_id)
    if not path.exists():
        return {"stage": "start", "completed_pages": [], "vectors_done": False, "vector_chunks_done": [], "knowledge_done": [], "knowledge_failures": {}, "schematics_done": [], "integration_done": False, "verification_warnings": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("stage", "start")
        data.setdefault("completed_pages", [])
        data.setdefault("vectors_done", False)
        data.setdefault("vector_chunks_done", [])
        data.setdefault("knowledge_done", [])
        data.setdefault("knowledge_failures", {})
        data.setdefault("schematics_done", [])
        data.setdefault("integration_done", False)
        data.setdefault("verification_warnings", [])
        return data
    except Exception:
        return {"stage": "start", "completed_pages": [], "vectors_done": False, "vector_chunks_done": [], "knowledge_done": [], "knowledge_failures": {}, "schematics_done": [], "integration_done": False, "verification_warnings": []}


def save_checkpoint(job_id: str, **updates: Any) -> dict[str, Any]:
    data = load_checkpoint(job_id)
    data.update(updates)
    path = _path(job_id)
    fd, tmp = tempfile.mkstemp(prefix=f"{job_id}-", suffix=".json", dir=str(CHECKPOINT_DIR))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return data


def clear_checkpoint(job_id: str) -> None:
    try:
        _path(job_id).unlink()
    except FileNotFoundError:
        pass
