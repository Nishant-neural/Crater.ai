"""Phase 6 — Mode B on-demand path (plan.md §4 Phase 6).

For the individual user with a big manual for an unknown machine:

- Tier 1 first: the job runs with the Phase 4 Tier-2 flag OFF, so
  Tier 2 chunks are deferred and the pipeline's integration stage
  builds the partial model from Tier 1 knowledge alone. Diagnosis
  starts on that partial model while the rest ingests in the
  background; lazy-ingest is the norm.
- Progress shown per section: Tier 1 (top repair cases), Tier 2
  (on-demand detail), Tier 3 (skipped in v1) — computed from the
  job's Chunk rows and its checkpoint (knowledge_done / tier2_deferred
  / integration_done), never from the job percent alone.
- If a symptom needs a section not yet ingested, the answer says so
  plainly with the wait — naming the section and the pages — instead
  of stalling silently, and names the pages a lazy-ingest would release.
- Graduation: when the same family is contracted, the product flips
  to pre-built (Mode A) and the job's deferred Tier 2 chunks are
  released back to pending, so the next job run (Retry) ingests them
  in full. The flip and the release are recorded; the re-ingest
  itself runs through the normal job path, not a side channel.

No new runtime LLM calls. No engine change. The partial model is the
pipeline's own integration snapshot (built from Tier 1 knowledge);
this module only reads it, never fabricates one.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.db.models import (
    Chunk, IngestionJob, MachineKnowledgeModelSnapshot, ProductMode,
)
from backend.diagnostics.backtracking import _texts_match
from backend.ingestion.pipeline import _chunk_pages
from backend.knowledge.repair_cases import RepairCaseRegistry

SECTION_NAMES = {
    1: "Tier 1 — top repair cases",
    2: "Tier 2 — on-demand detail",
    3: "Tier 3 — skipped in v1",
}


def sections_for_registry(registry: RepairCaseRegistry) -> list[dict[str, Any]]:
    """The manual's sections from the registry: Tier 1 pages, Tier 3
    pages, and everything else (Tier 2) in between."""
    tier1 = sorted(set(registry.tier1_pages or []))
    tier3 = sorted(set(registry.tier3_pages or []))
    page_count = int(getattr(registry, "page_count", 0) or 0)
    tier2 = sorted(set(range(1, page_count + 1)) - set(tier1) - set(tier3))
    return [{"tier": 1, "name": SECTION_NAMES[1], "pages": tier1},
            {"tier": 2, "name": SECTION_NAMES[2], "pages": tier2},
            {"tier": 3, "name": SECTION_NAMES[3], "pages": tier3}]


def _extracted_pages(db: Any, job: IngestionJob, checkpoint: dict[str, Any]
                     ) -> tuple[set[int], set[int]]:
    """(extracted pages, deferred pages) for a job, from its Chunk rows
    and its checkpoint. A page is extracted when at least one chunk
    covering it is in knowledge_done; deferred when its chunks are in
    tier2_deferred. Chunks for a document the job never produced yield
    empty sets — the sections then honestly report 0 pages done."""
    knowledge_done = set(checkpoint.get("knowledge_done") or [])
    deferred = set(checkpoint.get("tier2_deferred") or [])
    extracted: set[int] = set()
    deferred_pages: set[int] = set()
    if job.document_id:
        for chunk in db.query(Chunk).filter(Chunk.document_id == job.document_id).all():
            pages = _chunk_pages(chunk)
            if chunk.id in knowledge_done:
                extracted.update(pages)
            elif chunk.id in deferred:
                deferred_pages.update(pages)
    return extracted, deferred_pages


def section_progress(db: Any, job: IngestionJob, checkpoint: dict[str, Any],
                     registry: RepairCaseRegistry) -> list[dict[str, Any]]:
    """Progress per section: pages total / extracted / deferred, status
    and percent. Tier 3 is always "skipped" — v1 never ingests it.
    Tier 1 is "done" when every Tier 1 page has an extracted chunk;
    Tier 2 is "done" only when nothing is deferred and every Tier 2
    page is extracted."""
    extracted, deferred_pages = _extracted_pages(db, job, checkpoint)
    out: list[dict[str, Any]] = []
    for section in sections_for_registry(registry):
        pages = section["pages"]
        done = sorted(set(pages) & extracted)
        deferred = sorted(set(pages) & deferred_pages)
        if section["tier"] == 3:
            status = "skipped"
        elif len(done) == len(pages) and pages:
            status = "done"
        elif done or deferred:
            status = "in_progress"
        else:
            status = "pending"
        out.append({"tier": section["tier"], "name": section["name"],
                    "pages_total": len(pages), "pages_done": len(done),
                    "pages_deferred": len(deferred),
                    "pages": pages, "pages_extracted": done,
                    "pages_still_deferred": deferred,
                    "status": status,
                    "percent": round(100 * len(done) / len(pages)) if pages else 100})
    return out


def partial_model_available(db: Any, revision_id: str | None,
                            checkpoint: dict[str, Any]) -> dict[str, Any]:
    """Is there a partial model to diagnose on? The pipeline's
    integration stage snapshots the canonical model from whatever
    knowledge exists — for a Tier-1-first job that is the Tier 1
    partial model. Available when the checkpoint says integration ran
    AND a snapshot row exists for the revision."""
    integrated = bool(checkpoint.get("integration_done"))
    snapshot = None
    if revision_id:
        snapshot = (db.query(MachineKnowledgeModelSnapshot)
                    .filter_by(revision_id=revision_id)
                    .order_by(MachineKnowledgeModelSnapshot.version.desc()).first())
    available = integrated and snapshot is not None
    return {"available": available,
            "integration_done": integrated,
            "snapshot_exists": snapshot is not None,
            "snapshot_version": snapshot.version if snapshot is not None else None,
            "note": ("Partial model ready: built from Tier 1 knowledge; "
                     "Tier 2 ingests in the background."
                     if available else
                     "No partial model yet: integration has not run on "
                     "Tier 1 knowledge for this revision.")}


def mode_b_status(db: Any, job: IngestionJob, checkpoint: dict[str, Any],
                  registry: RepairCaseRegistry) -> dict[str, Any]:
    """The Mode B progress view for one job: job state, per-section
    progress, partial-model availability, and how much Tier 2 is still
    deferred in the background."""
    sections = section_progress(db, job, checkpoint, registry)
    partial = partial_model_available(db, job.revision_id, checkpoint)
    parts = (job.stage or "").split("|", 2)
    return {"job_id": job.id, "revision_id": job.revision_id,
            "mode": "on_demand",
            "status": job.status.value if hasattr(job.status, "value") else str(job.status),
            "stage": job.stage, "percent": int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0,
            "stage_label": parts[2] if len(parts) > 2 else (parts[0] if parts else ""),
            "sections": sections,
            "partial_model": partial,
            "tier2_deferred_chunks": len(checkpoint.get("tier2_deferred") or []),
            "ingest_tier2": bool(checkpoint.get("ingest_tier2", True)),
            "note": ("Tier 1 first: diagnosis can start on the partial "
                     "model while Tier 2 ingests in the background; "
                     "lazy-ingest is the norm.")}


def check_symptom_readiness(db: Any, job: IngestionJob, checkpoint: dict[str, Any],
                            registry: RepairCaseRegistry, symptom: str) -> dict[str, Any]:
    """Can a diagnosis start on the partial model for this symptom?

    - The partial model must exist (integration ran on Tier 1).
    - The symptom's registry case (when one matches) must have its
      pages extracted: its Tier 1 pages plus referenced pages. A case
      whose referenced page is Tier 2 (e.g. omni-05, p19) is not
      ready until that page is lazy-ingested.
    - A symptom with no matching top case can still start: diagnosis
      proceeds by composition on the partial model (Phase 4), said
      plainly in the note.
    When not ready, the answer says so plainly — the section, the
    missing pages, and the wait — instead of stalling silently.
    """
    partial = partial_model_available(db, job.revision_id, checkpoint)
    if not partial["available"]:
        return {"available": False, "reason": "partial_model_not_ready",
                "wait_message": ("The manual is still ingesting: the Tier 1 "
                                 "partial model is not ready yet. Please wait "
                                 "for background ingestion to finish Tier 1 "
                                 "before starting a diagnosis."),
                "missing_pages": [], "pages_to_lazy_ingest": [],
                "partial_model": partial}
    extracted, _deferred = _extracted_pages(db, job, checkpoint)
    case = None
    for candidate in registry.cases:
        if _texts_match(symptom, candidate.symptom):
            case = candidate
            break
    if case is None:
        return {"available": True, "reason": "no_top_case_match",
                "case_id": None,
                "note": ("No top repair case matches this symptom, so the "
                         "diagnosis will proceed by composition on the "
                         "partial model (Phase 4 upstream walk), not by "
                         "table lookup."),
                "missing_pages": [], "pages_to_lazy_ingest": [],
                "partial_model": partial}
    required = sorted(set(case.tier1_pages or []) | set(case.referenced_pages or []))
    missing = [p for p in required if p not in extracted]
    if not missing:
        return {"available": True, "reason": "case_pages_extracted",
                "case_id": case.id, "note": "",
                "missing_pages": [], "pages_to_lazy_ingest": [],
                "partial_model": partial}
    tier2_missing = [p for p in missing if registry.tier_for_page(p) == 2]
    section = "Tier 2 — on-demand detail" if tier2_missing else "Tier 1 — top repair cases"
    return {"available": False, "reason": "section_not_yet_ingested",
            "case_id": case.id,
            "wait_message": (
                f"The section this symptom needs ({section}, page(s) "
                f"{missing}) is still ingesting in the background. The "
                f"diagnosis cannot use it yet — please wait for background "
                f"ingestion, or lazy-ingest page(s) {missing} to proceed sooner."),
            "missing_pages": missing,
            "pages_to_lazy_ingest": missing,
            "partial_model": partial}


def register_mode_b(db: Any, product_id: str, job_id: str | None = None) -> ProductMode:
    """Mark a product as Mode B (on-demand). Idempotent: an existing
    row keeps its mode unless it was already prebuilt (graduation is
    one-way through graduate_to_prebuilt, never a silent flip back)."""
    row = db.get(ProductMode, product_id)
    if row is None:
        row = ProductMode(product_id=product_id, mode="on_demand",
                          background_job_id=job_id)
        db.add(row)
    elif row.mode != "prebuilt" and job_id:
        row.background_job_id = job_id
    db.commit()
    return row


def get_product_mode(db: Any, product_id: str) -> ProductMode | None:
    return db.get(ProductMode, product_id)


def graduate_to_prebuilt(db: Any, product_id: str, job_id: str,
                         actor: str, save_checkpoint_fn: Any = None) -> dict[str, Any]:
    """Graduation path: the same family is contracted, so the on-demand
    model becomes a pre-built model (Mode A).

    The flip is recorded (mode, actor, timestamp) and the job's
    deferred Tier 2 chunks are released back to pending: the checkpoint
    flag goes on and the deferred list is cleared (the released ids are
    kept under ``graduation_released`` for the audit trail), so the
    next job run (Retry) ingests them in full through the normal job
    path. The re-ingest itself is not run here — graduation queues it,
    it does not pretend it already happened.
    """
    if not actor or not actor.strip():
        raise ValueError("graduation requires a named actor")
    job = db.get(IngestionJob, job_id)
    if job is None:
        raise ValueError(f"no ingestion job with id {job_id!r}")
    from backend.ingestion.checkpoints import load_checkpoint, save_checkpoint
    save = save_checkpoint_fn or save_checkpoint
    checkpoint = load_checkpoint(job_id)
    deferred = list(checkpoint.get("tier2_deferred") or [])
    updated = save(job_id, ingest_tier2=True, tier2_deferred=[],
                   graduation_released=deferred,
                   graduated_to_prebuilt=True)
    row = db.get(ProductMode, product_id)
    if row is None:
        row = ProductMode(product_id=product_id, background_job_id=job_id)
        db.add(row)
    row.mode = "prebuilt"
    row.background_job_id = job_id
    row.graduated_at = datetime.utcnow()
    row.graduated_by = actor.strip()
    db.commit()
    return {"product_id": product_id, "mode": "prebuilt",
            "job_id": job_id, "graduated_by": actor.strip(),
            "graduated_at": row.graduated_at.isoformat() if row.graduated_at else None,
            "tier2_chunks_released": len(deferred),
            "ingest_tier2": bool(updated.get("ingest_tier2", True)),
            "note": (f"Graduated to pre-built (Mode A): {len(deferred)} deferred "
                     f"Tier 2 chunk(s) released back to pending; the next job "
                     f"run (Retry) ingests them in full.")}
