"""Schematic parsing: treat a diagram image as a structured system, not
just an OCR'd caption. Sends the actual image to vision input and asks
for symbols/labels/terminals/wires as JSON.

Fixes for the gaps found in the OMNIPRO DB (34 diagram chunks, 0 nodes):
- 8000 output tokens instead of 2000, so dense schematics do not truncate.
- Real terminals/pins are extracted per node; no synthetic "terminal" port
  is invented downstream when none are visible.
- Large images are tiled (2x2 with overlap) and tile bboxes are remapped to
  full-image coordinates, instead of shrinking a dense schematic to one call.
- Unparseable vision output is retried once with a JSON-repair prompt, and
  if it still fails the result carries parse_error, so the pipeline records
  an unresolved fact instead of silently treating it as "no nodes".
"""
from __future__ import annotations

import base64
import io
import json
import re
from pathlib import Path

from backend.llm import gateway
from backend.schematic.schema import SchematicExtractionResult
from backend.knowledge.normalization import normalize_payload

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
other); a one-sentence description of its apparent function if inferable from context; its \
approximate bounding box as fractions of image width/height (x, y = top-left corner, w, h = size), \
each between 0.0 and 1.0; a part_number ONLY if one is printed on the diagram for that component, \
otherwise null; and its terminals/pins as printed on the diagram — a list of {"name": "<pin label>", \
"bbox": {...}} — empty if no pins are visible. Do NOT invent pins.

For each wire, give the labels of the two components/terminals it connects, its type if determinable \
(power, control, signal, ground, unknown), its wire label/number if visible, and the pin labels on \
each end (from_port, to_port) when visible.

If this image does not look like a schematic/wiring diagram at all (e.g. it's a photo or unrelated \
figure), return empty lists rather than inventing content.

Respond with ONLY JSON in this exact shape:
{
  "nodes": [{"label": "", "symbol_type": "", "description": "", "bbox": {"x":0.0,"y":0.0,"w":0.0,"h":0.0}, "part_number": null, "ports": [{"name": "", "bbox": {"x":0.0,"y":0.0,"w":0.0,"h":0.0}}]}],
  "edges": [{"from_label": "", "to_label": "", "wire_type": "power|control|signal|ground|unknown", "label": "", "from_port": null, "to_port": null}]
}"""

_TILE_MAX_SIDE = 1600
_TILE_OVERLAP = 0.10


def _extract_json(text: str | None) -> dict | None:
    if not text:
        return None
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(raw[start:end + 1])
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _validate(payload: dict | None) -> SchematicExtractionResult | None:
    if payload is None:
        return None
    try:
        if isinstance(payload.get("nodes"), list):
            payload["nodes"] = [normalize_payload(x, kind="schematic_node") if isinstance(x, dict) else x for x in payload["nodes"]]
        if isinstance(payload.get("edges"), list):
            payload["edges"] = [normalize_payload(x, kind="schematic_edge") if isinstance(x, dict) else x for x in payload["edges"]]
        return SchematicExtractionResult.model_validate(payload)
    except Exception:
        return None


def _call_vision(image_b64: str, media_type: str, prompt: str, max_tokens: int) -> str | None:
    return gateway.complete("vision",
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
            {"type": "text", "text": prompt},
        ]}],
        max_tokens=max_tokens,
    )


def _extract_one(image_b64: str, media_type: str) -> SchematicExtractionResult:
    raw = _call_vision(image_b64, media_type, _SCHEMATIC_PROMPT, 8000)
    result = _validate(_extract_json(raw))
    if result is not None:
        return result
    # One JSON-repair retry: the model often truncates or wraps output, and a
    # repair pass recovers a usable result without a second full vision call.
    if raw:
        repair = ("The following is a schematic extraction that is not valid JSON. "
                  "Return ONLY the corrected JSON in exactly the same shape, without adding, "
                  "removing, or inventing nodes or edges:\n\n" + raw)
        raw2 = _call_vision(image_b64, media_type, repair, 8000)
        result = _validate(_extract_json(raw2))
        if result is not None:
            return result
    return SchematicExtractionResult(parse_error="vision_output_unparseable")


def _remap_bbox(bbox: dict | None, ox: float, oy: float, tw: float, th: float) -> dict | None:
    if not isinstance(bbox, dict):
        return bbox
    try:
        x, y, w, h = (float(bbox[k]) for k in ("x", "y", "w", "h"))
    except Exception:
        return bbox
    return {"x": ox + x * tw, "y": oy + y * th, "w": w * tw, "h": h * th}


def extract_schematic(image_path: str | Path) -> SchematicExtractionResult:
    image_path = Path(image_path)
    media_type = _EXTENSION_TO_MEDIA_TYPE.get(image_path.suffix.lower())
    if media_type is None:
        return SchematicExtractionResult(parse_error="unsupported_image_format")
    data = image_path.read_bytes()
    image_b64 = base64.standard_b64encode(data).decode("utf-8")

    # Tile only large images: a dense schematic shrunk to one vision call is
    # where truncation and missed symbols happen. Small images keep one call.
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
    except Exception:
        width = height = 0
    if max(width, height) <= _TILE_MAX_SIDE or width == 0:
        return _extract_one(image_b64, media_type)

    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as img:
            img = img.convert("RGB")
            tiles = []
            for row in range(2):
                for col in range(2):
                    x0 = int(col * width / 2 - _TILE_OVERLAP * width / 2) if col else 0
                    y0 = int(row * height / 2 - _TILE_OVERLAP * height / 2) if row else 0
                    x1 = int((col + 1) * width / 2 + _TILE_OVERLAP * width / 2) if col == 0 else width
                    y1 = int((row + 1) * height / 2 + _TILE_OVERLAP * height / 2) if row == 0 else height
                    x0, y0 = max(0, x0), max(0, y0)
                    crop = img.crop((x0, y0, x1, y1))
                    buf = io.BytesIO()
                    crop.save(buf, format="PNG")
                    tiles.append((x0 / width, y0 / height, (x1 - x0) / width, (y1 - y0) / height,
                                  base64.standard_b64encode(buf.getvalue()).decode("utf-8")))
    except Exception:
        return _extract_one(image_b64, media_type)

    merged_nodes: dict[str, dict] = {}
    merged_edges: list[dict] = []
    seen_edges: set[tuple] = set()
    for ox, oy, tw, th, tile_b64 in tiles:
        tile_result = _extract_one(tile_b64, "image/png")
        for node in tile_result.nodes:
            key = node.label.strip().lower()
            if key not in merged_nodes:
                d = node.model_dump(mode="json")
                d["bbox"] = _remap_bbox(d.get("bbox"), ox, oy, tw, th)
                d["ports"] = [{**port, "bbox": _remap_bbox(port.get("bbox"), ox, oy, tw, th)}
                              for port in (d.get("ports") or [])]
                merged_nodes[key] = d
        for edge in tile_result.edges:
            key = (edge.from_label.strip().lower(), edge.to_label.strip().lower(), edge.wire_type)
            if key not in seen_edges:
                seen_edges.add(key)
                merged_edges.append(edge.model_dump(mode="json"))
    payload = {"nodes": list(merged_nodes.values()), "edges": merged_edges}
    result = _validate(payload)
    return result if result is not None else SchematicExtractionResult(parse_error="tile_merge_failed")
