"""Evidence and provenance primitives for the Phase 8A universal machine model."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class MachineEvidence(BaseModel):
    """A single, reviewable claim tied to source evidence.

    The validator is a boundary adapter for LLM and legacy evidence references.
    Missing claim text is explicitly marked as missing, never fabricated.
    """

    @model_validator(mode="before")
    @classmethod
    def normalize_evidence_payload(cls, value):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if not data.get("fact"):
            for alias in ("claim", "text", "description", "behavior", "behaviour"):
                if data.get(alias):
                    data["fact"] = str(data[alias])
                    break
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        source_id = data.pop("source_id", None)
        if source_id:
            metadata.setdefault("source_id", str(source_id))
        if not data.get("fact"):
            data["fact"] = f"Evidence reference to source record {source_id}" if source_id else "Evidence claim text missing from input"
            metadata.setdefault("normalization_warning", "missing_fact_text")
        data["metadata"] = metadata
        # Invalid claim statuses are not safely normalizable: let the Literal
        # contract reject them instead of silently changing their meaning.
        return data

    fact: str
    source_document: str | None = None
    page: int | None = None
    chunk: str | None = None
    source_type: str | None = None
    location: str | None = None
    region: str | None = None
    confidence: float = 0.0
    claim_status: Literal["observed", "inferred", "uncertain"] = "observed"
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
