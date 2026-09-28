from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any


def tesseract_available() -> bool:
    """
    Return True only when both pytesseract and the Tesseract executable
    are usable.
    """
    try:
        import pytesseract

        try:
            pytesseract.get_tesseract_version()
            return True
        except Exception:
            return bool(shutil.which("tesseract"))

    except ImportError:
        return False


def optional_ocr_warning() -> str | None:
    """
    Return a human-readable warning when OCR is unavailable.
    OCR is optional; normal PDF text extraction should continue.
    """
    if tesseract_available():
        return None

    return (
        "Tesseract OCR is unavailable. Native PDF text will still be "
        "ingested; OCR-only scanned pages may be skipped."
    )


def ocr_page_if_needed(
    page: Any,
    image_path: str | Path | None = None,
) -> str:
    """
    OCR a page only when OCR is available.

    This function intentionally fails soft: missing Tesseract does not
    make the complete ingestion job fail.
    """

    if not tesseract_available():
        return ""

    try:
        import pytesseract

        if image_path is not None:
            from PIL import Image

            image = Image.open(image_path)
            return pytesseract.image_to_string(image)

        # Support page/image objects used by the existing chunking pipeline.
        if hasattr(page, "image"):
            image = page.image
            return pytesseract.image_to_string(image)

        if hasattr(page, "to_image"):
            image = page.to_image()
            return pytesseract.image_to_string(image)

        return ""

    except Exception:
        # OCR is optional. Never kill the complete ingestion pipeline
        # because one OCR operation failed.
        return ""


def caption_image(
    image_path: str | Path,
    *,
    fallback: str = "",
) -> str:
    """
    Generate a lightweight textual caption/description for an image.

    The existing chunking pipeline imports this function. Keep it
    dependency-light and fail-soft when no image-captioning model is
    configured.

    The image itself remains available through the diagram chunk's
    image_path for the schematic extraction stage.
    """

    path = Path(image_path)

    if not path.exists():
        return fallback

    # Do not require a vision model just to ingest a PDF.
    # The existing schematic extraction pipeline handles actual
    # diagram understanding later.
    return fallback