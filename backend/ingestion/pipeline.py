"""Resumable PDF ingestion pipeline with durable filesystem checkpoints."""
from __future__ import annotations
from pathlib import Path
from typing import Callable
import time
from sqlalchemy.orm import Session
from backend.schematic.vision_extraction import extract_schematic
from backend.db.models import Chunk, Component, ComponentRelationship, Document, DocType, IngestionJob, RelationType, SchematicEdge, SchematicNode, UnresolvedFact
from backend.ingestion.chunking import pages_to_chunks
from backend.ingestion.pdf_loader import load_pdf
from backend.knowledge.component_extraction import ExtractionError, extract_chunk_knowledge, extract_chunk_knowledge_batch, persist_extraction, persist_machine_knowledge
from backend.knowledge.validation import quarantine_machine_model, validate_machine_model, validate_machine_model_with_ledger
from backend.knowledge.global_integration import build_effective_revision_knowledge_context, integrate_revision_knowledge, persist_integration_result
from backend.knowledge.verification import verify_machine_model
from backend.retrieval.vector_store import upsert_chunks
from backend.schematic.graph import persist_schematic, schematic_to_machine_model
from backend.knowledge.evidence import MachineEvidence
from backend.knowledge.normalization import persist_unresolved_fact
from backend.config import settings
from backend.ingestion.pending_links import resolve_pending_relationships, resolve_pending_schematic_edges
from backend.ingestion.checkpoints import load_checkpoint, save_checkpoint

ProgressCallback = Callable[[int, str], None]

def _is_tesseract_error(exc: Exception) -> bool:
    s = str(exc).lower()
    return "tesseract" in s or "pytesseract" in s

def _load_pages_optional_ocr(pdf_path: Path, image_out_dir: Path):
    try:
        return load_pdf(pdf_path, image_out_dir), None
    except Exception as exc:
        if not _is_tesseract_error(exc): raise
        # Retry once with OCR disabled. The bundled PDF loader may honor this flag;
        # native/selectable PDF text can therefore continue without Tesseract.
        import os
        old = os.environ.get("CRATER_DISABLE_OCR")
        os.environ["CRATER_DISABLE_OCR"] = "1"
        try:
            return load_pdf(pdf_path, image_out_dir), "Tesseract OCR unavailable; continued with native PDF extraction. OCR-only pages may be skipped."
        finally:
            if old is None: os.environ.pop("CRATER_DISABLE_OCR", None)
            else: os.environ["CRATER_DISABLE_OCR"] = old

def ingest_pdf(session: Session, pdf_path: str | Path, revision_id: str, product_id: str, doc_type: DocType, title: str, image_out_dir: str | Path, run_knowledge_extraction: bool=True, run_schematic_extraction: bool=True, existing_document_id: str|None=None, progress_callback: ProgressCallback|None=None, job_id: str|None=None) -> Document:
    pdf_path=Path(pdf_path); image_out_dir=Path(image_out_dir); cp=load_checkpoint(job_id) if job_id else {"stage":"start","completed_pages":[],"vectors_done":False,"knowledge_done":[],"knowledge_failures":{},"schematics_done":[],"integration_done":False,"verification_warnings":[]}; completed_pages=set(cp.get("completed_pages",[])); vector_chunks_done=set(cp.get("vector_chunks_done",[])); knowledge_done=set(cp.get("knowledge_done",[])); schematics_done=set(cp.get("schematics_done",[])); knowledge_failures=dict(cp.get("knowledge_failures",{}))
    def progress(p:int,label:str):
        if job_id: save_checkpoint(job_id, stage=label, percent=p)
        if progress_callback: progress_callback(p,label)
    progress(3,"Loading PDF")
    pages, ocr_warning = _load_pages_optional_ocr(pdf_path,image_out_dir)
    if ocr_warning: progress(8,ocr_warning)
    else: progress(10,f"PDF extracted — {len(pages)} pages")
    document = session.get(Document, existing_document_id) if existing_document_id else None
    if document is None:
        document=Document(revision_id=revision_id,doc_type=doc_type,title=title,source_path=str(pdf_path),page_count=len(pages))
        session.add(document)
        session.flush()
        session.commit()
        # Persist the document link before expensive resumable work starts.
        if job_id:
            job = session.get(IngestionJob, job_id)
            if job:
                job.document_id = document.id
                session.commit()
    else:
        document.page_count=len(pages); session.commit()
    progress(15,f"Document ready — {len(pages)} pages")
    # Chunk stage is document-level: text chunks are allowed to cross page
    # boundaries so the extractor sees complete technical context. Rebuild the
    # chunk set atomically if this stage was interrupted before completion.
    existing_chunks = session.query(Chunk).filter(Chunk.document_id == document.id).all()
    chunking_version = cp.get("chunking_version")
    needs_rechunk = len(completed_pages) < len(pages)
    # A legacy checkpoint can say all pages were chunked even though those
    # chunks used the old small page-local strategy. Only migrate automatically
    # when no knowledge has been persisted yet; otherwise preserving source IDs
    # is safer than silently orphaning already-extracted facts.
    if (chunking_version != "large_cross_page_v2" and not knowledge_done
            and existing_chunks and any(
                (c.extra or {}).get("chunking") != "structure_aware_cross_page"
                for c in existing_chunks
            )):
        needs_rechunk = True

    if needs_rechunk:
        session.query(Chunk).filter(Chunk.document_id == document.id).delete(synchronize_session=False)
        pending_rows = []
        for pending in pages_to_chunks(str(pdf_path), pages):
            row = Chunk(
                document_id=document.id,
                chunk_type=pending.chunk_type,
                page_number=pending.page_number,
                content=pending.content,
                extra=pending.extra,
            )
            session.add(row)
            pending_rows.append(row)
        session.commit()
        completed_pages = {p.page_number for p in pages}
        if job_id:
            save_checkpoint(job_id, stage="chunks", completed_pages=sorted(completed_pages), chunking_version="large_cross_page_v2")
        progress(30, f"Creating large semantic chunks — {len(pending_rows)} chunks")
    all_chunks = session.query(Chunk).filter(Chunk.document_id == document.id).all()
    progress(32, f"Created {len(all_chunks)} chunks")
    # FIX: diagram chunks carry only a placeholder ("[Diagram on page 12]").
    # Indexing them wastes vector space and lets a useless placeholder be
    # retrieved as evidence. Diagram content is indexed later, after
    # schematic extraction, as a real node/edge summary (see below).
    vector_chunks = [c for c in all_chunks if c.chunk_type.value != "diagram"]
    if vector_chunks and not cp.get("vectors_done",False):
        total_vectors = len(vector_chunks)
        pending_vectors = [c for c in vector_chunks if c.id not in vector_chunks_done]
        batch_size = max(1, int(settings.vector_index_batch_size))
        processed_vectors = total_vectors - len(pending_vectors)
        progress(34, f"Indexing chunks: {processed_vectors}/{total_vectors}")
        for start in range(0, len(pending_vectors), batch_size):
            batch = pending_vectors[start:start + batch_size]
            upsert_chunks(
                chunk_ids=[c.id for c in batch],
                texts=[c.content for c in batch],
                payloads=[{"product_id": product_id, "revision_id": revision_id, "doc_type": doc_type.value, "chunk_type": c.chunk_type.value, "document_id": document.id} for c in batch],
                batch_size=batch_size,
            )
            vector_chunks_done.update(c.id for c in batch)
            processed_vectors += len(batch)
            percent = 34 + int(processed_vectors / max(1, total_vectors) * 6)
            if job_id:
                save_checkpoint(job_id, vector_chunks_done=sorted(vector_chunks_done), vectors_done=(processed_vectors >= total_vectors), stage="vectors", percent=percent)
            progress(percent, f"Indexing chunks: {processed_vectors}/{total_vectors}")
        if job_id:
            save_checkpoint(job_id, vector_chunks_done=sorted(vector_chunks_done), vectors_done=True, stage="vectors", percent=40)
    else: progress(40,"Vector index already complete")
    # Diagram placeholders are not useful to the text extractor; they have a
    # dedicated schematic/vision pass below. Keeping them out here avoids a
    # wasted Gemini request for every embedded image.
    text_chunks=[c for c in all_chunks if c.content.strip() and c.chunk_type.value in {"text", "table"}]
    total=len(text_chunks)
    if run_knowledge_extraction:
        processed_knowledge = len(knowledge_done)
        pending_knowledge = [c for c in text_chunks if c.id not in knowledge_done]
        batch_size = max(1, int(settings.extraction_batch_size))
        total = len(text_chunks)
        batch_index = 0
        while pending_knowledge:
            # Keep both request count and character size bounded. The latter
            # protects the local process and leaves room for the model output.
            # Include the per-chunk wrapper overhead in the character budget;
            # previously 12 x 6000-character chunks could exceed the configured
            # limit after IDs/separators were added and waste retries before split.
            batch = []
            prompt_chars = 0
            for chunk in pending_knowledge:
                wrapper_chars = len(chunk.id) * 2 + 100
                added_chars = len(chunk.content) + wrapper_chars + (2 if batch else 0)
                if batch and (len(batch) >= batch_size or prompt_chars + added_chars > settings.extraction_batch_max_chars):
                    break
                if not batch and added_chars > settings.extraction_batch_max_chars:
                    # A single unusually large chunk still gets a chance to be
                    # processed; the extractor will return a clear size error.
                    batch.append(chunk)
                    prompt_chars += added_chars
                    break
                batch.append(chunk)
                prompt_chars += added_chars
            if not batch:
                batch = [pending_knowledge[0]]

            def run_batch(current_batch):
                last_error: Exception | None = None
                # Retry only provider/transient failures. Schema/truncation errors
                # should split immediately; repeating the same prompt consumes RPM
                # and RPD without changing the likely outcome.
                for attempt in range(1, 3):
                    try:
                        return extract_chunk_knowledge_batch([(c.id, c.content) for c in current_batch])
                    except ExtractionError as exc:
                        last_error = exc
                        for c in current_batch:
                            knowledge_failures[c.id] = {"attempts": attempt, "error": str(exc)[:1000]}
                        if job_id:
                            save_checkpoint(job_id, stage="knowledge_retry", knowledge_done=sorted(knowledge_done), knowledge_failures=knowledge_failures, percent=42 + int(processed_knowledge / max(1, total) * 33))
                        if exc.rate_limited:
                            # One quota-aware retry only. Do not recursively split a
                            # 429 batch into more requests; that amplifies the quota
                            # problem. The limiter already spaces normal calls.
                            if attempt == 1:
                                time.sleep(max(60.0, settings.extraction_rate_limit_backoff_seconds))
                                continue
                            raise
                        if not exc.retryable:
                            break
                        if attempt == 1:
                            time.sleep(min(8.0, max(1.0, settings.extraction_rate_limit_backoff_seconds)))
                # Split malformed/truncated or persistently failing multi-chunk
                # outputs, but never split a quota-limited request into more calls.
                if last_error and getattr(last_error, "rate_limited", False):
                    raise last_error
                if len(current_batch) > 1:
                    mid = len(current_batch) // 2
                    left = run_batch(current_batch[:mid])
                    right = run_batch(current_batch[mid:])
                    return {**left, **right}
                raise ExtractionError(f"Chunk {current_batch[0].id} extraction failed: {last_error}")

            batch_started = time.monotonic()
            try:
                results = run_batch(batch)
                batch_elapsed = time.monotonic() - batch_started
            except Exception as exc:
                for failed_chunk in batch:
                    persist_unresolved_fact(session, revision_id=revision_id, source_id=failed_chunk.id,
                        fact_type="chunk_extraction_failure",
                        payload={"chunk_id": failed_chunk.id, "content_excerpt": failed_chunk.content[:4000]},
                        reason=f"extraction_failed_after_retries: {str(exc)[:1000]}")
                session.commit()
                raise
            for chunk in batch:
                result, machine_model = results[chunk.id]
                if result and (result.components or result.relationships or result.procedures):
                    persist_extraction(session, revision_id, chunk.id, result)
                _stamp_provenance(machine_model, document.title, chunk.page_number, chunk.id, chunk.chunk_type.value)
                if (machine_model.entities or machine_model.relations or machine_model.ports or machine_model.quantities or machine_model.states or machine_model.events or machine_model.behaviors or machine_model.constraints or machine_model.procedures or machine_model.failure_modes):
                    # Per-fact quarantine: keep every usable fact, quarantine
                    # only the structurally unusable ones. The old code dropped
                    # the whole chunk model when it had ANY issue.
                    kept_model, quarantined = quarantine_machine_model(machine_model)
                    for record in quarantined:
                        persist_unresolved_fact(session, revision_id=revision_id,
                            source_id=record.get("source_id") or chunk.id, fact_type=record.get("fact_type", "quarantine"),
                            payload=record.get("payload", record), reason=record.get("reason", "quarantined"))
                    if (kept_model.entities or kept_model.relations or kept_model.ports or kept_model.quantities or kept_model.states or kept_model.events or kept_model.behaviors or kept_model.constraints or kept_model.procedures or kept_model.failure_modes):
                        persist_machine_knowledge(session, revision_id, chunk.id, kept_model)
                    else:
                        persist_unresolved_fact(session, revision_id=revision_id, source_id=chunk.id,
                            fact_type="machine_model_all_quarantined", payload=machine_model.model_dump(mode="json"),
                            reason="all facts in this chunk model were quarantined")
                knowledge_done.add(chunk.id)
                knowledge_failures.pop(chunk.id, None)
            session.commit()
            processed_knowledge += len(batch)
            pending_ids = {c.id for c in batch}
            pending_knowledge = [c for c in pending_knowledge if c.id not in pending_ids]
            percent = 42 + int(processed_knowledge / max(1, total) * 33)
            batch_index += 1
            if job_id:
                save_checkpoint(job_id, stage="knowledge", knowledge_done=sorted(knowledge_done), knowledge_failures=knowledge_failures, percent=percent)
            progress(percent, f"Extracting machine knowledge — {processed_knowledge}/{total} chunks ({batch_index}-batch; {len(batch)} chunks, {prompt_chars:,} chars, {batch_elapsed:.1f}s)")
    else: progress(75,"Machine knowledge extraction skipped")
    progress(76,"Machine knowledge extraction complete")
    # Fact-centric resolution, independent of chunks: now that every chunk
    # for this document is extracted, pending cross-chunk relationships
    # can be resolved against the full revision component index.
    try:
        _resolved, _open = resolve_pending_relationships(session, revision_id)
        progress(76, f"Cross-chunk relationships resolved: {_resolved} resolved, {_open} still open")
    except Exception as _exc:
        session.rollback()
        progress(76, f"Cross-chunk relationship resolution skipped: {str(_exc)[:200]}")
    diagrams=[c for c in all_chunks if c.chunk_type.value=="diagram" and c.extra and c.extra.get("image_path")]
    if run_schematic_extraction and diagrams:
        total_d=len(diagrams)
        processed_schematics = len(schematics_done)
        # Cache vision results by image_path: the same schematic reused on
        # two pages is analysed once, but persisted per page (see
        # pdf_loader provenance fix), so neither cost nor provenance is lost.
        schematic_result_cache: dict[str, object] = {}
        diagram_summaries: dict[str, str] = {}
        for chunk in diagrams:
            if chunk.id in schematics_done: continue
            try:
                _img = chunk.extra["image_path"]
                if _img in schematic_result_cache:
                    result = schematic_result_cache[_img]
                else:
                    result = extract_schematic(_img)
                    schematic_result_cache[_img] = result
            except Exception as exc:
                persist_unresolved_fact(session, revision_id=revision_id, source_id=chunk.id,
                    fact_type="schematic_extraction_failure", payload={"image_path": chunk.extra.get("image_path"), "chunk_id": chunk.id},
                    reason=f"schematic_extraction_failed: {str(exc)[:1000]}")
                session.commit()
                schematics_done.add(chunk.id)
                continue
            if result.nodes:
                persist_schematic(session,document.id,chunk.id,revision_id,result)
                model=schematic_to_machine_model(result,document.title,chunk.page_number,chunk.id)
                kept_model, quarantined = quarantine_machine_model(model)
                for record in quarantined:
                    persist_unresolved_fact(session, revision_id=revision_id, source_id=record.get("source_id") or chunk.id,
                        fact_type=record.get("fact_type", "schematic_quarantine"), payload=record.get("payload", record),
                        reason=record.get("reason", "schematic_quarantined"))
                if kept_model.entities or kept_model.relations:
                    persist_machine_knowledge(session,revision_id,chunk.id,kept_model)
                # Real diagram content for the vector index, replacing the
                # placeholder that was deliberately NOT indexed above.
                _node_text = ", ".join(f"{n.label} ({n.symbol_type})" for n in result.nodes)
                _edge_text = ", ".join(f"{e.from_label} -{e.wire_type}-> {e.to_label}" for e in result.edges)
                diagram_summaries[chunk.id] = f"Schematic diagram on page {chunk.page_number}: nodes: {_node_text}; connections: {_edge_text}"
            session.commit(); schematics_done.add(chunk.id); processed_schematics += 1
            percent = 78 + int(processed_schematics/max(1,total_d)*12)
            if job_id: save_checkpoint(job_id,stage="schematics",schematics_done=sorted(schematics_done),percent=percent)
            progress(percent,f"Analyzing schematics — {processed_schematics}/{total_d} diagrams")
    else: progress(90,"No schematic extraction required")
    # Resolve cross-page schematic edges now that all diagrams for this
    # document are extracted, and index real diagram summaries.
    try:
        _s_res, _s_open = resolve_pending_schematic_edges(session, document.id, revision_id)
        progress(91, f"Cross-page schematic edges resolved: {_s_res} resolved, {_s_open} still open")
    except Exception as _exc:
        session.rollback()
        progress(91, f"Schematic edge resolution skipped: {str(_exc)[:200]}")
    try:
        _summaries = locals().get("diagram_summaries") or {}
        if _summaries:
            _ids = list(_summaries.keys())
            upsert_chunks(
                chunk_ids=_ids,
                texts=[_summaries[cid] for cid in _ids],
                payloads=[{"product_id": product_id, "revision_id": revision_id, "doc_type": doc_type.value, "chunk_type": "diagram", "document_id": document.id} for _ in _ids],
                batch_size=max(1, int(settings.vector_index_batch_size)),
            )
            progress(92, f"Indexed {len(_ids)} diagram summaries")
    except Exception as _exc:
        progress(92, f"Diagram summary indexing skipped: {str(_exc)[:200]}")
    # Canonical integration is part of machine readiness, not a separate manual step.
    # It is skipped only when this revision has no persisted knowledge at all.
    if not cp.get("integration_done", False):
        context, _, _ = build_effective_revision_knowledge_context(session, revision_id)
        has_knowledge = any(bool(rows) for rows in context.values())
        if has_knowledge:
            progress(93,"Building canonical machine knowledge")
            integration = integrate_revision_knowledge(session, revision_id)
            issues = verify_machine_model(integration.model)
            errors = [i for i in issues if i.severity == "error"]
            warnings = [i.__dict__ for i in issues if i.severity != "error"]
            if errors:
                if job_id:
                    save_checkpoint(job_id, stage="verification_failed", verification_warnings=warnings)
                raise ValueError("Canonical machine model failed verification: " + "; ".join(i.message for i in errors[:5]))
            stored = persist_integration_result(session, revision_id, integration)
            session.commit()
            if job_id:
                save_checkpoint(job_id, stage="integration", integration_done=True, verification_warnings=warnings, integration_version=stored["version"], percent=98)
            progress(98, f"Machine knowledge verified — {len(warnings)} warnings")
        else:
            if job_id: save_checkpoint(job_id, stage="integration_skipped", integration_done=True, verification_warnings=[], percent=98)
            progress(98,"No machine knowledge to integrate")
    else:
        progress(98,"Canonical machine knowledge already integrated")
    progress(99,"Finalizing ingestion")
    if job_id: save_checkpoint(job_id,stage="completed",percent=100)
    return document

def _stamp_provenance(model,source_document:str,page:int|None,chunk_id:str,source_type:str)->None:
    def evidence(fact:str)->MachineEvidence: return MachineEvidence(fact=fact,source_document=source_document,page=page,chunk=chunk_id,source_type=source_type,confidence=0.7,claim_status="observed",extraction_method="llm_extraction")
    for entity in model.entities:
        if not entity.evidence: entity.evidence.append(evidence(f"Entity {entity.name}"))
    for relation in model.relations:
        if not relation.evidence: relation.evidence.append(evidence(f"{relation.subject_name} {relation.relation_type} {relation.object_name}"))
    for collection,label in ((model.ports,"Port"),(model.quantities,"Quantity"),(model.states,"State"),(model.events,"Event"),(model.behaviors,"Behavior"),(model.constraints,"Constraint")):
        for item in collection:
            if not item.evidence: item.evidence.append(evidence(f"{label} {getattr(item,'name',getattr(item,'id','unknown'))}"))
    for proc in model.procedures:
        if not proc.evidence: proc.evidence.append(evidence(f"Procedure {proc.name}"))
    for fm in model.failure_modes:
        if not fm.evidence: fm.evidence.append(evidence(f"Failure mode {fm.name}"))
    if not model.evidence: model.evidence.append(evidence("Universal machine extraction"))
