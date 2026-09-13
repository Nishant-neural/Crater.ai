"""
Visual highlighting (plan.md §5: "Highlight relevant schematic regions";
§9 Technical Visualization draws on this same primitive later).

Takes the original diagram image + one or more SchematicNode bboxes and
draws labeled rectangles over them, so a diagnostic answer can point at
"this is X12" instead of only naming it in text.

Kept as a standalone image-drawing function rather than an SVG/vector
overlay, because the source is a rasterized diagram image to begin with
(see ingestion/pdf_loader.py::extract_images_per_page) — there's no
underlying vector geometry to annotate.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from backend.db.models import SchematicNode

_HIGHLIGHT_COLOR = (255, 0, 0)
_BOX_WIDTH = 4


def highlight_nodes(image_path: str | Path, nodes: list[SchematicNode], out_path: str | Path) -> str:
    """Draw a labeled box for each node with a bbox onto a copy of the image; returns out_path."""
    image_path = Path(image_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(image_path) as img:
        img = img.convert("RGB")
        draw = ImageDraw.Draw(img)
        width, height = img.size

        try:
            font = ImageFont.load_default()
        except Exception:
            font = None

        for node in nodes:
            if not node.bbox:
                continue
            x = node.bbox.get("x", 0.0) * width
            y = node.bbox.get("y", 0.0) * height
            w = node.bbox.get("w", 0.0) * width
            h = node.bbox.get("h", 0.0) * height

            draw.rectangle([x, y, x + w, y + h], outline=_HIGHLIGHT_COLOR, width=_BOX_WIDTH)
            draw.text((x, max(0, y - 14)), node.label, fill=_HIGHLIGHT_COLOR, font=font)

        img.save(out_path)

    return str(out_path)
