"""
Raw extraction from a single PDF: per-page text, tables, and embedded
images. Downstream code (chunking.py) turns this into Chunk rows.

Fixes in this revision:
- Reading order is column-aware instead of raw PyMuPDF text order, so
  two-column manuals no longer interleave columns into scrambled prose.
- Embedded images are no longer deduplicated across the whole document.
  The same schematic on two pages now produces a diagram chunk on both
  pages (reusing the same saved file), so provenance is per-page instead
  of pointing only at the first page.
- Every page gets a lightweight classification (cover / toc / parts_list /
  specifications / troubleshooting / maintenance / safety / procedure /
  diagram / other). Classification is annotation only: it never silently
  drops a page's facts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re

import pymupdf as fitz
import pdfplumber

MIN_IMAGE_SIDE_PX = 200
MIN_IMAGE_PIXELS = 60_000


@dataclass
class RawPage:
    page_number: int
    text: str
    tables: list[list[list[str | None]]] = field(default_factory=list)
    image_paths: list[str] = field(default_factory=list)
    page_kind: str = "other"
    page_width: float = 0.0
    page_height: float = 0.0


def _blocks_to_text(blocks: list[dict]) -> str:
    out: list[str] = []
    for block in blocks:
        if block.get("type") != 0:
            continue
        lines: list[str] = []
        for line in block.get("lines", []):
            spans = sorted(line.get("spans", []), key=lambda s: s.get("bbox", (0, 0, 0, 0))[0])
            text = "".join(s.get("text", "") for s in spans).strip()
            if text:
                lines.append(text)
        if lines:
            out.append("\n".join(lines))
    return "\n".join(out)


def _column_aware_page_text(page) -> str:
    try:
        data = page.get_text("dict")
        width = float(page.rect.width) if page.rect else 0.0
    except Exception:
        try:
            return page.get_text("text", sort=True)
        except Exception:
            return page.get_text("text")
    blocks = [b for b in data.get("blocks", []) if b.get("type") == 0]
    if len(blocks) < 3 or not width:
        try:
            return page.get_text("text", sort=True)
        except Exception:
            return page.get_text("text")

    def _x0(b):
        return float(b.get("bbox", (0, 0, 0, 0))[0])

    def _y0(b):
        return float(b.get("bbox", (0, 0, 0, 0))[1])

    def _w(b):
        x0, _, x1, _ = b.get("bbox", (0, 0, 0, 0))
        return float(x1) - float(x0)

    full_width = [b for b in blocks if _w(b) > 0.75 * width]
    column_blocks = [b for b in blocks if _w(b) <= 0.75 * width]
    if len(column_blocks) < 2:
        return _blocks_to_text(sorted(blocks, key=_y0))
    ordered = sorted(column_blocks, key=_x0)
    clusters: list[list[dict]] = []
    for b in ordered:
        if clusters and abs(_x0(b) - sum(_x0(x) for x in clusters[-1]) / len(clusters[-1])) <= 80:
            clusters[-1].append(b)
        else:
            clusters.append([b])
    if len(clusters) != 2:
        return _blocks_to_text(sorted(blocks, key=_y0))

    def _y_range(cs):
        ys = [_y0(b) for b in cs]
        return min(ys), max(ys)

    (a0, a1), (b0, b1) = _y_range(clusters[0]), _y_range(clusters[1])
    overlap = min(a1, b1) - max(a0, b0)
    span = max(a1, b1) - min(a0, b0)
    if span <= 0 or overlap / span < 0.3:
        return _blocks_to_text(sorted(blocks, key=_y0))
    left = sorted(clusters[0], key=_y0)
    right = sorted(clusters[1], key=_y0)
    full_sorted = sorted(full_width, key=_y0)
    emitted: list[dict] = []
    li = ri = 0
    for fw in full_sorted:
        y = _y0(fw)
        while li < len(left) and _y0(left[li]) < y:
            emitted.append(left[li]); li += 1
        while ri < len(right) and _y0(right[ri]) < y:
            emitted.append(right[ri]); ri += 1
        emitted.append(fw)
    emitted.extend(left[li:])
    emitted.extend(right[ri:])
    seen = {id(b) for b in emitted}
    emitted.extend(b for b in full_sorted if id(b) not in seen)
    return _blocks_to_text(emitted)


def extract_text_per_page(pdf_path: str | Path) -> dict[int, str]:
    text_by_page: dict[int, str] = {}
    with fitz.open(pdf_path) as doc:
        for i, page in enumerate(doc, start=1):
            text_by_page[i] = _column_aware_page_text(page)
    return text_by_page


def extract_tables_per_page(pdf_path: str | Path) -> dict[int, list[list[list[str | None]]]]:
    tables_by_page: dict[int, list] = {}
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
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    images_by_page: dict[int, list[str]] = {}
    xref_to_path: dict[int, str] = {}
    with fitz.open(pdf_path) as doc:
        for page_index in range(len(doc)):
            page_number = page_index + 1
            page = doc[page_index]
            paths: list[str] = []
            seen_on_page: set[int] = set()
            img_index = -1
            for img in page.get_images(full=True):
                xref, width, height = img[0], img[2], img[3]
                if width < MIN_IMAGE_SIDE_PX or height < MIN_IMAGE_SIDE_PX or width * height < MIN_IMAGE_PIXELS:
                    continue
                if xref in seen_on_page:
                    continue
                seen_on_page.add(xref)
                if xref in xref_to_path:
                    paths.append(xref_to_path[xref])
                    continue
                img_index += 1
                pix = fitz.Pixmap(doc, xref)
                if pix.n - pix.alpha >= 4:
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                out_path = out_dir / f"p{page_number:04d}_img{img_index}.png"
                pix.save(out_path)
                xref_to_path[xref] = str(out_path)
                paths.append(str(out_path))
            if paths:
                images_by_page[page_number] = paths
    return images_by_page


_STEP_RE = re.compile(r"^\s*(?:step\s*)?\d+[.)]\s+", re.I)


def classify_page(page_number: int, text: str, tables, image_paths) -> str:
    lower = text.lower()
    stripped = text.strip()
    if not stripped and image_paths:
        return "diagram"
    first_400 = lower[:400]
    dotted_leaders = lower.count("....") + lower.count(".....")
    if page_number == 1 and ("owner" in first_400 or "manual" in first_400 or "instructions" in first_400):
        if len(stripped) < 3000:
            return "cover"
    if "table of contents" in lower or ("contents" in first_400 and dotted_leaders >= 3):
        return "toc"
    if "parts list" in lower or "parts diagram" in lower:
        return "parts_list"
    if re.search(r"(?m)^\s*specifications\s*$", lower) or "technical data" in lower:
        return "specifications"
    if "troubleshoot" in lower:
        return "troubleshooting"
    if re.search(r"(?m)^\s*maintenance\s*$", lower):
        return "maintenance"
    if "safety" in first_400 and "warning" in lower:
        return "safety"
    step_lines = sum(1 for ln in text.splitlines() if _STEP_RE.match(ln))
    if step_lines >= 3:
        return "procedure"
    if image_paths and len(stripped) < 300:
        return "diagram"
    return "other"


def load_pdf(pdf_path: str | Path, image_out_dir: str | Path) -> list[RawPage]:
    text_by_page = extract_text_per_page(pdf_path)
    tables_by_page = extract_tables_per_page(pdf_path)
    images_by_page = extract_images_per_page(pdf_path, image_out_dir)
    sizes: dict[int, tuple[float, float]] = {}
    try:
        with fitz.open(pdf_path) as doc:
            for i, page in enumerate(doc, start=1):
                sizes[i] = (float(page.rect.width), float(page.rect.height))
    except Exception:
        sizes = {}
    pages: list[RawPage] = []
    for page_number, text in sorted(text_by_page.items()):
        tables = tables_by_page.get(page_number, [])
        images = images_by_page.get(page_number, [])
        w, h = sizes.get(page_number, (0.0, 0.0))
        pages.append(RawPage(page_number=page_number, text=text, tables=tables, image_paths=images, page_kind=classify_page(page_number, text, tables, images), page_width=w, page_height=h))
    return pages
