"""Resumable PDF ingestion pipeline with durable filesystem checkpoints."""
from __future__ import annotations
from pathlib import Path
from typing import Callable
from sqlalchemy.orm import Session
from backend.schematic.vision_extraction import extract_schematic
from backend.db.models import Chunk, Document, DocType, IngestionJob
from backend.ingestion.chunking import page_to_chunks
from backend.ingestion.pdf_loader import load_pdf
from backend.knowledge.component_extraction import extract_chunk_knowledge, persist_extraction, persist_machine_knowledge
from backend.knowledge.validation import validate_machine_model
from backend.retrieval.vector_store import upsert_chunks
from backend.schematic.graph import persist_schematic, schematic_to_machine_model
from backend.knowledge.evidence import MachineEvidence
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
    pdf_path=Path(pdf_path); image_out_dir=Path(image_out_dir); cp=load_checkpoint(job_id) if job_id else {"stage":"start","completed_pages":[],"vectors_done":False,"knowledge_done":[],"schematics_done":[]}; completed_pages=set(cp.get("completed_pages",[])); knowledge_done=set(cp.get("knowledge_done",[])); schematics_done=set(cp.get("schematics_done",[]))
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
    # Chunk stage: only checkpoint pages after their DB transaction is committed.
    for idx,page in enumerate(pages,start=1):
        if idx in completed_pages: continue
        # A page not checkpointed may contain partial rows after an interruption; remove them before retrying.
        session.query(Chunk).filter(Chunk.document_id==document.id, Chunk.page_number==getattr(page,"page_number",idx)).delete(synchronize_session=False)
        pending_rows=[]
        for pending in page_to_chunks(str(pdf_path),page):
            row=Chunk(document_id=document.id,chunk_type=pending.chunk_type,page_number=pending.page_number,content=pending.content,extra=pending.extra); session.add(row); pending_rows.append(row)
        session.commit(); completed_pages.add(idx)
        if job_id: save_checkpoint(job_id,stage="chunks",completed_pages=sorted(completed_pages))
        progress(15+int(idx/max(1,len(pages))*17),f"Creating chunks — page {idx}/{len(pages)}")
    all_chunks=session.query(Chunk).filter(Chunk.document_id==document.id).all(); progress(32,f"Created {len(all_chunks)} chunks")
    if all_chunks and not cp.get("vectors_done",False):
        progress(34,f"Indexing {len(all_chunks)} chunks in vector search")
        upsert_chunks(chunk_ids=[c.id for c in all_chunks],texts=[c.content for c in all_chunks],payloads=[{"product_id":product_id,"revision_id":revision_id,"doc_type":doc_type.value,"chunk_type":c.chunk_type.value,"document_id":document.id} for c in all_chunks])
        if job_id: save_checkpoint(job_id,vectors_done=True,stage="vectors")
    else: progress(40,"Vector index already complete")
    text_chunks=[c for c in all_chunks if c.content.strip()]
    total=len(text_chunks)
    if run_knowledge_extraction:
        for index,chunk in enumerate(text_chunks,start=1):
            if chunk.id in knowledge_done: continue
            result,machine_model=extract_chunk_knowledge(chunk.content)
            if result and (result.components or result.relationships or result.procedures): persist_extraction(session,revision_id,chunk.id,result)
            _stamp_provenance(machine_model,document.title,chunk.page_number,chunk.id,chunk.chunk_type.value)
            if (machine_model.entities or machine_model.relations or machine_model.ports or machine_model.quantities or machine_model.states or machine_model.events or machine_model.behaviors or machine_model.constraints):
                if not validate_machine_model(machine_model): persist_machine_knowledge(session,revision_id,chunk.id,machine_model)
            session.commit(); knowledge_done.add(chunk.id)
            if job_id: save_checkpoint(job_id,stage="knowledge",knowledge_done=sorted(knowledge_done))
            progress(42+int(index/max(1,total)*33),f"Extracting machine knowledge — {index}/{total} chunks")
    else: progress(75,"Machine knowledge extraction skipped")
    progress(76,"Machine knowledge extraction complete")
    diagrams=[c for c in all_chunks if c.chunk_type.value=="diagram" and c.extra and c.extra.get("image_path")]
    if run_schematic_extraction and diagrams:
        total_d=len(diagrams)
        for index,chunk in enumerate(diagrams,start=1):
            if chunk.id in schematics_done: continue
            result=extract_schematic(chunk.extra["image_path"])
            if result.nodes:
                persist_schematic(session,document.id,chunk.id,revision_id,result); model=schematic_to_machine_model(result,document.title,chunk.page_number,chunk.id)
                if not validate_machine_model(model): persist_machine_knowledge(session,revision_id,chunk.id,model)
            session.commit(); schematics_done.add(chunk.id)
            if job_id: save_checkpoint(job_id,stage="schematics",schematics_done=sorted(schematics_done))
            progress(78+int(index/max(1,total_d)*12),f"Analyzing schematics — {index}/{total_d} diagrams")
    else: progress(90,"No schematic extraction required")
    progress(96,"Finalizing ingestion")
    if job_id: save_checkpoint(job_id,stage="completed",percent=100)
    return document

def _stamp_provenance(model,source_document:str,page:int|None,chunk_id:str,source_type:str)->None:
    def evidence(fact:str)->MachineEvidence: return MachineEvidence(fact=fact,source_document=source_document,page=page,chunk=chunk_id,source_type=source_type,confidence=0.7,extraction_method="llm_extraction")
    for entity in model.entities:
        if not entity.evidence: entity.evidence.append(evidence(f"Entity {entity.name}"))
    for relation in model.relations:
        if not relation.evidence: relation.evidence.append(evidence(f"{relation.subject_name} {relation.relation_type} {relation.object_name}"))
    for collection,label in ((model.ports,"Port"),(model.quantities,"Quantity"),(model.states,"State"),(model.events,"Event"),(model.behaviors,"Behavior"),(model.constraints,"Constraint")):
        for item in collection:
            if not item.evidence: item.evidence.append(evidence(f"{label} {getattr(item,'name',getattr(item,'id','unknown'))}"))
    if not model.evidence: model.evidence.append(evidence("Universal machine extraction"))
