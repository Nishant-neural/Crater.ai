"""Structure-aware technical chunking for industrial manuals.

Text is accumulated across page boundaries so the extractor receives enough
technical context to understand components, specifications and procedures.
Tables and diagrams remain atomic evidence units.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from backend.config import settings
from backend.db.models import ChunkType
from backend.ingestion.ocr import ocr_page_if_needed
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
    x = line.strip()
    return bool(x) and (x.endswith(":") or _HEADING.match(x) is not None)


def _units(text: str) -> list[tuple[str, str]]:
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    units: list[tuple[str, str]] = []
    section = ""
    buf: list[str] = []

    def flush() -> None:
        nonlocal buf
        if buf:
            units.append((section, " ".join(buf)))
            buf = []

    for line in lines:
        if _heading(line):
            flush()
            section = line
        elif _PROCEDURE.match(line) and buf:
            flush()
            buf = [line]
        else:
            buf.append(line)
    flush()
    return units


def _semantic_chunks(text: str) -> list[tuple[str, str]]:
    """Chunk a single text block while respecting headings/procedure boundaries."""
    units = _units(text)
    out: list[tuple[str, str]] = []
    section = ""
    buf = ""
    target = settings.chunk_size_chars
    max_chars = settings.chunk_max_chars

    for sec, unit in units:
        if sec != section and buf:
            out.append((section, buf.strip()))
            buf = ""
        section = sec or section
        candidate = (buf + " " + unit).strip()
        if buf and len(candidate) > max_chars:
            out.append((section, buf.strip()))
            tail = buf[-settings.chunk_overlap_chars:]
            buf = (tail + " " + unit).strip()
        else:
            buf = candidate
        if len(buf) >= target and unit.endswith((".", ":", ";")):
            out.append((section, buf.strip()))
            buf = ""

    if buf:
        out.append((section, buf.strip()))
    return out


def _document_text_chunks(pdf_path: str, pages: list[RawPage]) -> list[PendingChunk]:
    """Build larger semantic text chunks across page boundaries.

    A chunk may span multiple pages. `page_number` remains the first page for
    backwards compatibility while `extra.page_start/page_end/pages` preserves
    the complete provenance range.
    """
    chunks: list[PendingChunk] = []
    buffer = ""
    section = ""
    page_numbers: list[int] = []
    target = settings.chunk_size_chars
    max_chars = settings.chunk_max_chars

    def flush() -> None:
        nonlocal buffer, section, page_numbers
        if not buffer.strip():
            return
        pages_seen = sorted(set(page_numbers))
        chunks.append(PendingChunk(
            ChunkType.text,
            pages_seen[0] if pages_seen else None,
            buffer.strip(),
            {
                "section": section or None,
                "chunking": "structure_aware_cross_page",
                "page_start": pages_seen[0] if pages_seen else None,
                "page_end": pages_seen[-1] if pages_seen else None,
                "pages": pages_seen,
            },
        ))
        buffer = ""
        page_numbers = []

    for page in pages:
        text = ocr_page_if_needed(pdf_path, page.page_number, page.text)
        for sec, unit in _units(text):
            if not unit:
                continue
            # A new explicit section starts a new chunk only when the current
            # chunk already has meaningful content. This avoids losing context
            # while still preventing unrelated manual sections from blending.
            if sec and section and sec != section and buffer:
                flush()
            if sec:
                section = sec

            candidate = (buffer + " " + unit).strip()
            if buffer and len(candidate) > max_chars:
                flush()
                # Keep a semantic overlap rather than a hard character split.
                # The previous chunk's section remains in metadata; the new
                # unit starts a fresh evidence block.
                candidate = unit

            buffer = candidate
            page_numbers.append(page.page_number)
            if len(buffer) >= target and unit.endswith((".", ":", ";")):
                flush()

    flush()
    return chunks


def _table_to_markdown(table: list[list[str | None]]) -> str:
    rows = [[cell or "" for cell in row] for row in table]
    if not rows:
        return ""
    header, *body = rows
    md = ["| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * len(header)) + " |"]
    md += ["| " + " | ".join(row) + " |" for row in body]
    return "\n".join(md)


def page_to_chunks(pdf_path: str, page: RawPage) -> list[PendingChunk]:
    """Backward-compatible single-page helper used by older callers/tests."""
    chunks: list[PendingChunk] = []
    text = ocr_page_if_needed(pdf_path, page.page_number, page.text)
    for section, piece in _semantic_chunks(text):
        chunks.append(PendingChunk(
            ChunkType.text,
            page.page_number,
            piece,
            {"section": section or None, "chunking": "structure_aware"},
        ))
    for i, table in enumerate(page.tables):
        md = _table_to_markdown(table)
        if md:
            chunks.append(PendingChunk(
                ChunkType.table, page.page_number, md,
                {"rows": len(table), "cols": len(table[0]) if table else 0, "table_index": i, "chunking": "atomic"},
            ))
    for i, image_path in enumerate(page.image_paths):
        chunks.append(PendingChunk(
            ChunkType.diagram,
            page.page_number,
            f"[Diagram on page {page.page_number}]",
            {"image_path": image_path, "image_index": i, "chunking": "atomic"},
        ))
    return chunks


def pages_to_chunks(pdf_path: str, pages: list[RawPage]) -> list[PendingChunk]:
    """Create document-level text chunks plus atomic tables/diagrams."""
    chunks = _document_text_chunks(pdf_path, pages)
    for page in pages:
        for i, table in enumerate(page.tables):
            md = _table_to_markdown(table)
            if md:
                chunks.append(PendingChunk(
                    ChunkType.table, page.page_number, md,
                    {"rows": len(table), "cols": len(table[0]) if table else 0, "table_index": i, "chunking": "atomic"},
                ))
        for i, image_path in enumerate(page.image_paths):
            chunks.append(PendingChunk(
                ChunkType.diagram,
                page.page_number,
                f"[Diagram on page {page.page_number}]",
                {"image_path": image_path, "image_index": i, "chunking": "atomic"},
            ))
    return chunks


# Backward-compatible helper retained for older callers/tests.
def _split_text(text: str, size: int, overlap: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    out: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        out.append(text[start:end])
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return out
