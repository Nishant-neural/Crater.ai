"""Source provenance: document identity and precedence (Phase 2).

Precedence is the ranking the manuals themselves state: a machine tool
builder's (MTB) manual takes precedence over the machine manufacturer's
(OEM) manual, and both take precedence over generic material. This
module derives the precedence label from the document -- it never asks
the LLM, and it never guesses: when the origin cannot be determined
from the document, the label is ``"generic"`` (the lowest rank), not a
pretend OEM.

Rules (in order):
- an explicitly declared origin wins (``origin="mtb"`` / ``"oem"``),
  e.g. from upload metadata;
- a document whose title/filename marks it as an MTB manual wins next;
- a manufacturer manual (the ordinary case for this system: owner
  manuals, Mitsubishi IB manuals, spec/alarm/PLC manuals) is ``"oem"``;
- anything else is ``"generic"``.
"""
from __future__ import annotations

import re
from typing import Any

# MTB > OEM > generic. Higher wins when two facts conflict.
PRECEDENCE_RANK = {"mtb": 3, "oem": 2, "generic": 1}
PRECEDENCE_LABELS = tuple(PRECEDENCE_RANK)

# No trailing word boundary: real Mitsubishi filenames run letters
# straight after the digits (ib1501808engc_...). The (?!\d) guard
# still rejects an 8-digit run, so only exact 7-digit IB numbers
# match.
_IB_RE = re.compile(r"\bIB[-\s]?(\d{7})(?!\d)", re.IGNORECASE)
_MTB_MARKERS = ("mtb", "machine tool builder", "builder manual")


def parse_source_doc(title: str | None) -> dict[str, str | None]:
    """Split a document title into its identity parts.

    Returns ``{"source_doc": <title as given>, "ib_number": <"IB-1501808"
    or None>}``. The IB number is the Mitsubishi document identifier;
    other manufacturers' manuals simply have no IB number, which is
    recorded as None rather than invented.
    """
    title = (title or "").strip()
    match = _IB_RE.search(title)
    return {
        "source_doc": title or None,
        "ib_number": f"IB-{match.group(1)}" if match else None,
    }


def precedence_for_source(
    source_document: str | None,
    *,
    origin: str | None = None,
    doc_type: str | None = None,
) -> str:
    """Precedence label (``"mtb"`` / ``"oem"`` / ``"generic"``) for a source.

    Never guesses: an MTB label needs an explicit marker, an OEM label
    needs a manufacturer manual. Unknown material is ``"generic"``.
    """
    if origin:
        label = str(origin).strip().lower()
        if label in PRECEDENCE_RANK:
            return label
    text = (source_document or "").lower()
    if any(marker in text for marker in _MTB_MARKERS):
        return "mtb"
    if doc_type and str(doc_type).strip().lower() in {
        "manual", "troubleshooting_guide", "maintenance_schedule",
        "parts_catalog", "service_ticket", "schematic",
    }:
        return "oem"
    if "manual" in text or "ib-" in text or "ib " in text:
        return "oem"
    return "generic"


def higher_precedence(a: str | None, b: str | None) -> str:
    """The winning precedence label of two, by rank. Unknown loses."""
    ra = PRECEDENCE_RANK.get((a or "generic").lower(), 1)
    rb = PRECEDENCE_RANK.get((b or "generic").lower(), 1)
    if ra == rb:
        return (a or "generic").lower()
    return (a or "generic").lower() if ra > rb else (b or "generic").lower()


def source_kind_for_evidence(evidence: dict[str, Any] | None) -> str | None:
    """Parameter source kind derived from a quantity's evidence.

    ``"spec_page"`` for a spec-table row, ``"parameter_manual"`` for a
    parameter-manual page, ``"data_book"`` for a drive DATA BOOK page.
    Returns None when the evidence does not say -- the caller records
    the parameter as unbound rather than inventing a source.
    """
    if not isinstance(evidence, dict):
        return None
    source_type = str(evidence.get("source_type") or "").strip().lower()
    doc = str(evidence.get("source_document") or "").lower()
    if source_type == "spec_table":
        return "spec_page"
    if "parameter" in doc:
        return "parameter_manual"
    if "data book" in doc or "databook" in doc:
        return "data_book"
    if "nameplate" in doc:
        return "nameplate"
    return None
