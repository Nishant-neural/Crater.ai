"""Evidence and provenance primitives for the Phase 8A universal machine model."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class MachineEvidence(BaseModel):
    """A single, reviewable claim tied to source evidence."""

    fact: str
    source_document: str | None = None
    page: int | None = None
    chunk: str | None = None
    source_type: str | None = None
    location: str | None = None
    region: str | None = None
    confidence: float = 0.0
    extraction_method: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def as_summary(self) -> str:
        doc = self.source_document or "unknown document"
        if self.page is not None:
            return f"{doc} p.{self.page}: {self.fact}"
        return f"{doc}: {self.fact}"


class EvidenceBundle(BaseModel):
    """A convenience container for grouping many evidence items."""

    items: list[MachineEvidence] = Field(default_factory=list)


# Phase 8A compatibility aliases for the canonical universal-model vocabulary.
Evidence = MachineEvidence
