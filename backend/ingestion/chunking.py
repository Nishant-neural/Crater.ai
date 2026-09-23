"""Structure-aware technical chunking.

Chunks follow headings/paragraph boundaries, keep section context in metadata,
and preserve tables/diagrams as atomic evidence. This avoids splitting a
procedure, warning, specification, or component description arbitrarily.
"""
from __future__ import annotations
from dataclasses import dataclass
import re
from backend.config import settings
from backend.db.models import ChunkType
from backend.ingestion.ocr import caption_image, ocr_page_if_needed
from backend.ingestion.pdf_loader import RawPage

@dataclass
class PendingChunk:
    chunk_type: ChunkType
    page_number: int | None
    content: str
    extra: dict | None = None

_HEADING = re.compile(r"^(?:\d+(?:\.\d+)*[.)]?\s+|[A-Z][A-Z0-9 /&:_-]{5,}$).{0,140}$")
_PROCEDURE = re.compile(r"^(?:step\s*)?\d+[.)]\s+|^(?:warning|caution|danger|note)\s*[:\-]", re.I)

def _heading(line: str) -> bool:
    x=line.strip()
    return bool(x) and (x.endswith(":") or _HEADING.match(x) is not None)

def _units(text: str) -> list[tuple[str,str]]:
    lines=[x.strip() for x in text.splitlines() if x.strip()]
    units=[]; section=""
    buf=[]
    def flush():
        nonlocal buf
        if buf:
            units.append((section," ".join(buf))); buf=[]
    for line in lines:
        if _heading(line):
            flush(); section=line
        elif _PROCEDURE.match(line) and buf:
            flush(); buf=[line]
        else:
            buf.append(line)
    flush(); return units

def _semantic_chunks(text: str) -> list[tuple[str,str]]:
    units=_units(text); out=[]; section=""; buf=""
    target=settings.chunk_size_chars; max_chars=settings.chunk_max_chars
    for sec, unit in units:
        if sec != section and buf:
            out.append((section,buf.strip())); buf=""
        section=sec or section
        candidate=(buf+" "+unit).strip()
        if buf and len(candidate)>max_chars:
            out.append((section,buf.strip()))
            # small overlap at semantic boundary, not arbitrary character offset
            tail=buf[-settings.chunk_overlap_chars:]
            buf=(tail+" "+unit).strip()
        else:
            buf=candidate
        if len(buf)>=target and unit.endswith((".",":",";")):
            out.append((section,buf.strip())); buf=""
    if buf: out.append((section,buf.strip()))
    return out

def _table_to_markdown(table: list[list[str | None]]) -> str:
    rows=[[cell or "" for cell in row] for row in table]
    if not rows: return ""
    header,*body=rows
    md=["| "+" | ".join(header)+" |","| "+" | ".join(["---"]*len(header))+" |"]
    md += ["| "+" | ".join(row)+" |" for row in body]
    return "\n".join(md)

def page_to_chunks(pdf_path: str, page: RawPage) -> list[PendingChunk]:
    chunks=[]
    text=ocr_page_if_needed(pdf_path,page.page_number,page.text)
    for section,piece in _semantic_chunks(text):
        chunks.append(PendingChunk(ChunkType.text,page.page_number,piece,{"section":section or None,"chunking":"structure_aware"}))
    for i,table in enumerate(page.tables):
        md=_table_to_markdown(table)
        if md: chunks.append(PendingChunk(ChunkType.table,page.page_number,md,{"rows":len(table),"cols":len(table[0]) if table else 0,"table_index":i,"chunking":"atomic"}))
    for i,image_path in enumerate(page.image_paths):
        caption=caption_image(image_path)
        content=caption or f"[Diagram on page {page.page_number}, no OCR text detected]"
        chunks.append(PendingChunk(ChunkType.diagram,page.page_number,content,{"image_path":image_path,"image_index":i,"chunking":"atomic"}))
    return chunks

# Backward-compatible helper retained for older callers/tests. New ingestion
# uses _semantic_chunks so boundaries are structural rather than character-only.
def _split_text(text: str, size: int, overlap: int) -> list[str]:
    text=text.strip()
    if not text: return []
    if len(text)<=size: return [text]
    out=[]; start=0
    while start<len(text):
        end=min(len(text),start+size); out.append(text[start:end])
        if end>=len(text): break
        start=max(end-overlap,start+1)
    return out
