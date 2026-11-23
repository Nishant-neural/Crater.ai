"""Image-extraction hardening in backend/ingestion/pdf_loader.py.

Regression tests for the Lincoln POWER MIG 210 MP ingestion crash
(job 33088f7c): ``pymupdf.mupdf.FzErrorArgument: pixmap must be grayscale
or rgb to write as png`` raised by ``pix.save()`` in
``extract_images_per_page``.

Root cause (grounded in the real PDF, not a guess): the first image that
crashed was page 51, xref 915, colorspace Separation(DeviceCMYK,Black),
n=1, alpha=0, 1946x1250. The old guard only converted when
``pix.n - pix.alpha >= 4``, so a 1-channel Separation image slipped
through to ``pix.save()`` even though its colorspace is neither
grayscale nor RGB. A second identical image sits on page 80, xref 624.

No LLM is used here. The Lincoln/Miller/OMNIPRO tests skip when the
benchmark PDFs are absent.
"""
import logging
from pathlib import Path

import fitz
import pytest

from backend.ingestion.pdf_loader import (
    _to_saveable_pixmap,
    extract_images_per_page,
    load_pdf,
)

LINCOLN_PDF = Path("/home/hatch/workspace/crater-benchmarks/welding/lincoln-power-mig-210/operator-manual.pdf")
MILLER_PDF = Path("/home/hatch/workspace/crater-benchmarks/welding/miller-millermatic-252/owners-manual-om-230-693c.pdf")
OMNIPRO_PDF = Path("/home/hatch/workspace/project-review-v3/docs/owner-manual.pdf")


def _make_pdf_with_image(path: Path, pixmap, text="test page") -> Path:
    doc = fitz.open()
    page = doc.new_page(width=600, height=600)
    page.insert_text((72, 50), text)
    page.insert_image(fitz.Rect(50, 80, 550, 580), pixmap=pixmap)
    doc.save(path)
    return path


def test_cmyk_pixmap_saves_as_png(tmp_path):
    """(i) A CMYK image converts to RGB and saves as PNG."""
    pdf = _make_pdf_with_image(
        tmp_path / "cmyk.pdf",
        fitz.Pixmap(fitz.csCMYK, fitz.IRect(0, 0, 500, 500)),
        text="cmyk test",
    )
    result = extract_images_per_page(pdf, tmp_path / "imgs")
    assert sum(len(v) for v in result.values()) == 1
    saved = next(iter(result.values()))[0]
    pix = fitz.Pixmap(saved)
    assert pix.colorspace.name == "DeviceRGB"
    assert pix.n == 3
    assert pix.alpha == 0


def test_separation_colorspace_pixmap_converts_and_saves(tmp_path):
    """(ii) A non-gray/non-RGB colorspace converts and saves.

    Uses the actual crashing Lincoln image: page 51, xref 915,
    Separation(DeviceCMYK,Black), n=1, 1946x1250.
    """
    if not LINCOLN_PDF.exists():
        pytest.skip("Lincoln manual not present")
    with fitz.open(LINCOLN_PDF) as doc:
        pix = fitz.Pixmap(doc, 915)
        assert pix.colorspace is not None
        assert pix.colorspace.name == "Separation(DeviceCMYK,Black)"
        assert pix.n == 1
        converted = _to_saveable_pixmap(pix)
        assert converted.colorspace.name == "DeviceRGB"
        assert converted.n == 3
        assert converted.alpha == 0
        out = tmp_path / "sep.png"
        converted.save(out)
    saved = fitz.Pixmap(out)
    assert (saved.w, saved.h) == (1946, 1250)


def test_rgba_and_gray_alpha_pixmaps_save(tmp_path):
    """(iii) RGBA and gray+alpha pixmaps drop alpha and save as PNG."""
    rgba = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 500, 500), True)
    assert rgba.n == 4 and rgba.alpha == 1
    converted = _to_saveable_pixmap(rgba)
    assert converted.colorspace.name == "DeviceRGB"
    assert converted.n == 3
    assert converted.alpha == 0
    converted.save(tmp_path / "rgba.png")

    gray_alpha = fitz.Pixmap(fitz.csGRAY, fitz.IRect(0, 0, 500, 500), True)
    assert gray_alpha.n == 2 and gray_alpha.alpha == 1
    converted = _to_saveable_pixmap(gray_alpha)
    assert converted.colorspace.name == "DeviceGray"
    assert converted.n == 1
    assert converted.alpha == 0
    converted.save(tmp_path / "gray_alpha.png")

    # Full path: an RGBA image embedded in a PDF still extracts.
    pdf = _make_pdf_with_image(
        tmp_path / "rgba.pdf",
        fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 500, 500), True),
        text="rgba test",
    )
    result = extract_images_per_page(pdf, tmp_path / "imgs")
    assert sum(len(v) for v in result.values()) == 1
    saved = fitz.Pixmap(next(iter(result.values()))[0])
    assert saved.n == 3
    assert saved.alpha == 0


def test_unconvertible_image_skipped_with_warning(tmp_path, monkeypatch, caplog):
    """(iv) One unconvertible image is skipped, warned about, and the
    job continues: load_pdf still returns the pages."""
    pdf = _make_pdf_with_image(
        tmp_path / "bad.pdf",
        fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 500, 500)),
        text="unconvertible test",
    )
    orig_pixmap = fitz.Pixmap

    def _boom_pixmap(*args, **kwargs):
        if args and isinstance(args[0], fitz.Document):
            raise ValueError("simulated unconvertible image")
        return orig_pixmap(*args, **kwargs)

    # NOTE: pdf_loader does `import pymupdf as fitz`, so patch the Pixmap
    # attribute on the module object pdf_loader actually uses (pymupdf),
    # not the deprecated `fitz` shim module.
    monkeypatch.setattr("backend.ingestion.pdf_loader.fitz.Pixmap", _boom_pixmap)
    with caplog.at_level(logging.WARNING, logger="backend.ingestion.pdf_loader"):
        pages = load_pdf(pdf, tmp_path / "imgs")
    assert len(pages) == 1
    assert "unconvertible test" in pages[0].text
    assert pages[0].image_paths == []
    warnings = [r for r in caplog.records if "Skipping unconvertible image" in r.message]
    assert len(warnings) == 1
    assert "page 1" in warnings[0].message and "xref" in warnings[0].message


@pytest.mark.skipif(not LINCOLN_PDF.exists(), reason="Lincoln manual not present")
def test_lincoln_extraction_no_crash(tmp_path):
    """End-to-end regression: the Lincoln manual extracts without the
    FzErrorArgument crash, including the page-51 Separation image."""
    result = extract_images_per_page(LINCOLN_PDF, tmp_path / "imgs")
    assert sum(len(v) for v in result.values()) == 68
    assert 51 in result and 80 in result
    pix = fitz.Pixmap(result[51][0])
    assert (pix.w, pix.h) == (1946, 1250)
    assert pix.colorspace.name == "DeviceRGB"
    assert pix.n == 3 and pix.alpha == 0
    for paths in result.values():
        for path in paths:
            saved = fitz.Pixmap(path)
            assert saved.alpha == 0


@pytest.mark.skipif(not MILLER_PDF.exists(), reason="Miller manual not present")
def test_miller_stencil_masks_still_extract(tmp_path):
    """Miller's 8 stencil-mask images (colorspace None) must keep
    extracting exactly as before the fix: 9 listings, 6 unique files."""
    result = extract_images_per_page(MILLER_PDF, tmp_path / "imgs")
    assert sum(len(v) for v in result.values()) == 9
    assert len({p for v in result.values() for p in v}) == 6
    for paths in result.values():
        for path in paths:
            assert Path(path).exists()


@pytest.mark.skipif(not OMNIPRO_PDF.exists(), reason="OMNIPRO manual not present")
def test_omnipro_extraction_unchanged(tmp_path):
    """OMNIPRO regression: same extraction counts as before the fix."""
    result = extract_images_per_page(OMNIPRO_PDF, tmp_path / "imgs")
    assert sum(len(v) for v in result.values()) == 34
    assert len(result) == 10
