"""
Schematic parsing (plan.md §5): treat a diagram image as a structured
system, not just an OCR'd caption. Sends the actual image to Claude's
vision input and asks for symbols/labels/terminals/wires as JSON.

This is the thing Phase 1 explicitly deferred — Phase 1's "diagram"
chunks only carry whatever text pytesseract could OCR off the image
(ingestion/ocr.py::caption_image); this module is what turns that same
image into an actual component graph.

Bounding boxes are requested normalized to [0,1] on each axis so
highlight.py can map them onto the image at whatever resolution it's
opened at, without needing the original pixel dimensions round-tripped
through the LLM.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

from anthropic import Anthropic

from backend.config import settings
from backend.schematic.schema import SchematicExtractionResult

_EXTENSION_TO_MEDIA_TYPE = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

_SCHEMATIC_PROMPT = """This image is a page or region from an industrial equipment schematic/wiring \
diagram. Identify every distinct symbol/component you can clearly see, and every wire/connection \
between them. Only extract what is visually evident — do not guess at connections you cannot see.

For each component, give: a label as printed on the diagram (e.g. "K17", "X12", "M1") — if truly \
unlabeled, invent a short stable placeholder like "unlabeled-relay-1"; the symbol type (one of: relay, \
contactor, motor, sensor, switch, fuse, terminal, connector, plc, transformer, power_source, ground, \
other); a one-sentence description of its apparent function if inferable from context; and its \
approximate bounding box as fractions of image width/height (x, y = top-left corner, w, h = size), \
each between 0.0 and 1.0.

For each wire, give the labels of the two components/terminals it connects, its type if determinable \
(power, control, signal, ground, unknown), and its wire label/number if visible.

If this image does not look like a schematic/wiring diagram at all (e.g. it's a photo or unrelated \
figure), return empty lists rather than inventing content.

Respond with ONLY JSON in this exact shape:
{
  "nodes": [{"label": "", "symbol_type": "", "description": "", "bbox": {"x":0.0,"y":0.0,"w":0.0,"h":0.0}}],
  "edges": [{"from_label": "", "to_label": "", "wire_type": "power|control|signal|ground|unknown", "label": ""}]
}"""


def extract_schematic(image_path: str | Path) -> SchematicExtractionResult:
    if not settings.anthropic_api_key:
        return SchematicExtractionResult()

    image_path = Path(image_path)
    media_type = _EXTENSION_TO_MEDIA_TYPE.get(image_path.suffix.lower())
    if media_type is None:
        # Unsupported format for vision input — not a schematic-parsing failure,
        # just nothing we can send to the model.
        return SchematicExtractionResult()

    image_b64 = base64.standard_b64encode(image_path.read_bytes()).decode("utf-8")

    client = Anthropic(api_key=settings.anthropic_api_key)
    response = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=2000,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": image_b64},
                    },
                    {"type": "text", "text": _SCHEMATIC_PROMPT},
                ],
            }
        ],
    )
    raw = response.content[0].text.strip()
    try:
        return SchematicExtractionResult.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValueError):
        return SchematicExtractionResult()
