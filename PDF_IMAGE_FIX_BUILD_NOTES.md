# PDF Image Fix — Build Notes (fix4)

## Traceback (verbatim from Nishant's run, job 33088f7c, Lincoln POWER MIG 210 MP manual)

```
pymupdf.mupdf.FzErrorArgument: code=4: pixmap must be grayscale or rgb to write as png
Call chain: _run_job_background (backend/api/routes/ingestion.py:67)
  → ingest_pdf (backend/ingestion/pipeline.py:173, _load_pages_optional_ocr)
  → load_pdf (backend/ingestion/pdf_loader.py:229)
  → extract_images_per_page (pdf_loader.py:185, pix.save(out_path))
```

## Reproduction (before patching)

Running `extract_images_per_page` on the real Lincoln PDF
(`~/workspace/crater-benchmarks/welding/lincoln-power-mig-210/operator-manual.pdf`,
104 pages) raised the exact same `FzErrorArgument` at `pdf_loader.py:185`
(`pix.save(out_path)`).

## Colorspace cause (grounded in the actual image, not a guess)

The first image that crashes is **page 51, xref 915**:
colorspace **Separation(DeviceCMYK,Black)**, n=1, alpha=0, 1946x1250.
A second identical image is on **page 80, xref 624**
(Separation(DeviceCMYK,Black), n=1, alpha=0, 1946x1250).

So the cause is **not** Lab/ICC and **not** CMYK and **not** alpha. It is a
**Separation (spot-colour "Black") colorspace**: `fz_save_pixmap_as_png`
requires a grayscale or RGB colorspace, and the old guard only converted
when `pix.n - pix.alpha >= 4`. A 1-channel Separation pixmap has
n−alpha = 1, so it slipped through unconverted and `pix.save()` raised.

Full colorspace census of the Lincoln images passing the size filter
(68 images): DeviceCMYK n=4 ×22, DeviceRGB n=3 ×23, DeviceGray n=1 ×21,
Separation(DeviceCMYK,Black) n=1 ×2. The 22 CMYK images were already
handled by the old guard (verified: page 12, xref 27 converts to
DeviceRGB n=3); the 2 Separation images were the only crashers.

## The fix — `backend/ingestion/pdf_loader.py`

- Added `import logging` + module `logger` (lines ~21–28).
- Added `_is_gray_or_rgb_colorspace(colorspace)`: True for `DeviceGray` /
  `DeviceRGB` and for ICC-based profiles whose base space is gray or RGB
  (`ICCBased(Gray…`, `ICCBased(RGB…`); False for CMYK, ICCBased(CMYK),
  Separation, Lab, and `None`. (Note: `Colorspace ==` does not work in
  this PyMuPDF version — comparison is by `.name`, verified empirically.)
- Added `_to_saveable_pixmap(pix)`, using only PyMuPDF's own handling:
  - colorspace is not gray/RGB → `fitz.Pixmap(fitz.csRGB, pix)`;
  - alpha channel present → drop it via `fitz.Pixmap(pix, 0)`.
  - colorspace `None` (stencil mask) is returned untouched — see below.
- `extract_images_per_page`: the per-image pixmap build / convert / save
  is wrapped in `try/except Exception`; on failure it logs
  `logger.warning("Skipping unconvertible image on page %s xref %s: %s", …)`
  and continues with the next image. `img_index` now increments only on a
  successful save. The size filter, `seen_on_page` / `xref_to_path`
  dedupe, and page-classification use of the images are unchanged.

### Deliberate deviation from the brief, grounded in the Miller manual

The brief suggested a stencil mask (no colorspace) is an example of an
unconvertible image to skip. Measured evidence says otherwise: the Miller
manual has **8 stencil-mask images (colorspace `None`, n=1, alpha=1)**
out of its 9 images, and they **save as valid grayscale PNGs directly**
(verified: pre-fix extraction produced 6 valid PNG files, all reopening
as DeviceGray/ICCBased-RGB pixmaps). Converting one raises
`ValueError: source colorspace must not be None`, so converting masks
would have dropped 8 of Miller's 9 images — a regression. The fix
therefore leaves colorspace-`None` pixmaps unconverted and saves them
as-is; the warning+skip path still catches any image whose save genuinely
fails. (A synthetic stencil built by flipping an image XObject to
`ImageMask=true` confirmed both facts: direct save OK, `csRGB`
conversion raises.)

## Tests

New file `tests/test_pdf_loader_images.py` — 7 tests, all passing:

1. `test_cmyk_pixmap_saves_as_png` — synthetic CMYK PDF extracts to a
   DeviceRGB n=3 alpha=0 PNG.
2. `test_separation_colorspace_pixmap_converts_and_saves` — the real
   Lincoln page-51 xref-915 Separation pixmap converts to DeviceRGB n=3
   and saves as a 1946x1250 PNG.
3. `test_rgba_and_gray_alpha_pixmaps_save` — in-memory RGBA (n=4→3) and
   gray+alpha (n=2→1) pixmaps drop alpha and save; a synthetic RGBA PDF
   extracts to an alpha-free PNG.
4. `test_unconvertible_image_skipped_with_warning` — a monkeypatched
   `Pixmap` raising on image load is skipped with a warning naming page 1
   + xref, and `load_pdf` still returns the page with its text.
5. `test_lincoln_extraction_no_crash` — full Lincoln extraction: 68
   listings, pages 51 and 80 present, page-51 file is 1946x1250
   DeviceRGB, every saved PNG alpha-free.
6. `test_miller_stencil_masks_still_extract` — Miller: 9 listings,
   6 unique files, unchanged from pre-fix.
7. `test_omnipro_extraction_unchanged` — OMNIPRO: 34 listings over
   10 pages, unchanged from pre-fix.

Full suite: `~/workspace/crater-phase1/.venv/bin/python -m pytest tests/ -q`
→ **469 passed, 0 failed** (395 warnings, all pre-existing deprecation
warnings; 38m52s wall under three concurrent suite runs on this VM).
Note on the baseline: the task brief said "433 passed", but this tree
collects 462 tests before this change (verified by a pre-patch
`pytest --collect-only`: 462 collected), so 469 = 462 pre-existing + 7
new, all passing, no regressions.

## Real-PDF extraction results (post-fix)

| Manual | Pre-fix | Post-fix |
|---|---|---|
| Lincoln POWER MIG 210 MP (`operator-manual.pdf`, 104 pp) | **CRASH** `FzErrorArgument` at page 51, xref 915 (Separation) | **No crash**: 41 pages with images, 68 listings, 56 unique PNGs, all valid, all alpha-free; page-51 image 1946x1250 DeviceRGB |
| Miller Millermatic 252 (`owners-manual-om-230-693c.pdf`, 52 pp) | No crash: 7 pages, 9 listings, 6 unique files | **No change**: 7 pages, 9 listings, 6 unique files, all valid |
| OMNIPRO (`owner-manual.pdf`, 48 pp) | No crash: 10 pages, 34 listings | **No change (regression check)**: 10 pages, 34 listings, 33 unique files, all valid |

## Packaging

- Zip: `~/workspace/crater-phase1/project-review-phase6-readiness-ingestion-gemini-fix4.zip`
  (new file; no existing zip overwritten)
- Contents: the fix2 tree (242 files) + the 3 changed/added files of this
  fix only. The fix3 diagnosis-loop and O1/O2/O3 runner files being built
  in parallel were deliberately NOT included.
- No LLM calls anywhere in this build.
