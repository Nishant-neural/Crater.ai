from __future__ import annotations

import shutil
from pathlib import Path


def tesseract_available() -> bool:
    """Return True when pytesseract and the Tesseract executable are usable."""
    try:
        import pytesseract
    except ImportError:
        return False
    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return bool(shutil.which("tesseract"))


def optional_ocr_warning() -> str | None:
    if tesseract_available():
        return None
    return (
        "Tesseract OCR is unavailable. Native PDF text will still be ingested; "
        "OCR-only scanned pages may be skipped."
    )


def ocr_page_if_needed(pdf_path: str | Path, page_number: int, native_text: str) -> str:
    """Use native PDF text when available; OCR only textless pages."""
    if native_text and native_text.strip():
        return native_text
    if not tesseract_available():
        return ""
    try:
        import io
        import fitz
        import pytesseract
        from PIL import Image

        with fitz.open(str(pdf_path)) as doc:
            index = page_number - 1
            if index < 0 or index >= len(doc):
                return ""
            pix = doc[index].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            image = Image.open(io.BytesIO(pix.tobytes("png")))
        return pytesseract.image_to_string(image) or ""
    except Exception:
        # OCR is optional; never fail the complete ingestion job because of it.
        return ""
