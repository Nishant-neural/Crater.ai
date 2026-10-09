"""
Raw extraction from a single PDF: per-page text, tables, and embedded
images. Downstream code (chunking.py) turns this into Chunk rows.

Deliberately split into three passes (text / tables / images) rather
than one "smart" parser, because each needs a different library and a
different failure mode: pdfplumber tables can misfire on complex
layouts, embedded images need OCR, native text extraction is cheap and
should always be tried first.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pymupdf as fitz  # PyMuPDF (module renamed from `fitz`; alias kept for readability)
import pdfplumber


@dataclass
class RawPage:
    page_number: int                     # 1-indexed
    text: str
    tables: list[list[list[str | None]]] = field(default_factory=list)   # list of tables, each a grid of cell strings
    image_paths: list[str] = field(default_factory=list)                 # extracted embedded images, saved to disk


def extract_text_per_page(pdf_path: str | Path) -> dict[int, str]:
    """Native text layer per page (fast, works for born-digital PDFs)."""
    text_by_page: dict[int, str] = {}
    with fitz.open(pdf_path) as doc:
        for i, page in enumerate(doc, start=1):
            text_by_page[i] = page.get_text("text")
    return text_by_page


def extract_tables_per_page(pdf_path: str | Path) -> dict[int, list[list[list[str | None]]]]:
    """Table grids per page via pdfplumber's layout heuristics."""
    tables_by_page: dict[int, list[list[list[str | None]]]] = {}
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            tables = page.extract_tables()
            validated = []
            for table in tables or []:
                width = max((len(row or []) for row in table), default=0)
                if width < 2: continue
                rows = []
                for row in table:
                    cells = [str(cell).strip() if cell is not None else "" for cell in (row or [])]
                    if not any(cells): continue
                    cells = (cells + [""] * width)[:width]
                    if sum(bool(cell) for cell in cells) >= 2 or not rows: rows.append(cells)
                if len(rows) >= 2: validated.append(rows)
            if validated: tables_by_page[i] = validated
    return tables_by_page


def extract_images_per_page(pdf_path: str | Path, out_dir: str | Path) -> dict[int, list[str]]:
    """
    Dump embedded raster images (schematics, photos, diagrams) to
    out_dir, keyed by page. These get OCR'd / captioned in ocr.py and
    become 'diagram' chunks.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    images_by_page: dict[int, list[str]] = {}

    with fitz.open(pdf_path) as doc:
        for page_index in range(len(doc)):
            page_number = page_index + 1
            page = doc[page_index]
            paths: list[str] = []
            for img_index, img in enumerate(page.get_images(full=True)):
                xref = img[0]
                pix = fitz.Pixmap(doc, xref)
                if pix.n - pix.alpha >= 4:  # CMYK -> convert to RGB before saving
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                out_path = out_dir / f"p{page_number:04d}_img{img_index}.png"
                pix.save(out_path)
                paths.append(str(out_path))
            if paths:
                images_by_page[page_number] = paths
    return images_by_page


def load_pdf(pdf_path: str | Path, image_out_dir: str | Path) -> list[RawPage]:
    """Run all three extraction passes and merge into per-page records."""
    text_by_page = extract_text_per_page(pdf_path)
    tables_by_page = extract_tables_per_page(pdf_path)
    images_by_page = extract_images_per_page(pdf_path, image_out_dir)

    pages: list[RawPage] = []
    for page_number, text in sorted(text_by_page.items()):
        pages.append(
            RawPage(
                page_number=page_number,
                text=text,
                tables=tables_by_page.get(page_number, []),
                image_paths=images_by_page.get(page_number, []),
            )
        )
    return pages
