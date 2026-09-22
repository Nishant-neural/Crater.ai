"""Evidence and provenance primitives for the Phase 8A universal machine model."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class MachineEvidence(BaseModel):
    """A reviewable claim tied to source evidence and a machine revision."""

    fact: str
    source_document: str | None = None
    page: int | None = None
    chunk: str | None = None
    source_type: str | None = None
    location: str | None = None
    region: str | None = None
    confidence: float = 0.0
    extraction_method: str | None = None
    revision_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def as_summary(self) -> str:
        doc = self.source_document or "unknown document"
        page = f" p.{self.page}" if self.page is not None else ""
        return f"{doc}{page}: {self.fact}"


class EvidenceBundle(BaseModel):
    items: list[MachineEvidence] = Field(default_factory=list)


Evidence = MachineEvidence
