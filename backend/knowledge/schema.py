"""Pydantic shapes for LLM-structured extraction output (knowledge/component_extraction.py).

This module now supports both the legacy Phase 1 extraction format and the
Phase 8A universal machine model vocabulary described in docs/phase 8A.md.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ExtractedComponent(BaseModel):
    name: str
    function: str | None = None
    location_description: str | None = None
    part_number: str | None = None


class ExtractedRelationship(BaseModel):
    from_component: str
    to_component: str
    relation_type: str  # electrical | mechanical | fluid | signal | contains
    description: str | None = None


class ExtractedProcedure(BaseModel):
    name: str
    procedure_type: str  # installation | removal | calibration | maintenance | troubleshooting | replacement | verification
    steps: list[str]


class ExtractionResult(BaseModel):
    components: list[ExtractedComponent] = []
    relationships: list[ExtractedRelationship] = []
    procedures: list[ExtractedProcedure] = []


class UniversalEntity(BaseModel):
    id: str | None = None
    name: str
    entity_type: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)
    ports: list[str] = Field(default_factory=list)
    states: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class UniversalRelation(BaseModel):
    subject_id: str | None = None
    subject_name: str
    relation_type: str
    object_id: str | None = None
    object_name: str
    description: str | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class UniversalEvidence(BaseModel):
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


class UniversalFailureMode(BaseModel):
    id: str | None = None
    name: str
    symptoms: list[str] = Field(default_factory=list)
    possible_causes: list[str] = Field(default_factory=list)
    diagnostic_test: str | None = None
    expected_observation: str | None = None
    repair_procedure_id: str | None = None
    repair_procedure_name: str | None = None
    entity_ids: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class MachineKnowledgeExtractionResult(BaseModel):
    # Legacy fields remain in the same provider response during migration.
    components: list[ExtractedComponent] = Field(default_factory=list)
    relationships: list[ExtractedRelationship] = Field(default_factory=list)
    procedures: list[ExtractedProcedure] = Field(default_factory=list)
    entities: list[UniversalEntity] = Field(default_factory=list)
    relations: list[UniversalRelation] = Field(default_factory=list)
    evidence: list[UniversalEvidence] = Field(default_factory=list)
    ports: list[dict[str, Any]] = Field(default_factory=list)
    states: list[dict[str, Any]] = Field(default_factory=list)
    quantities: list[dict[str, Any]] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)
    behaviors: list[dict[str, Any]] = Field(default_factory=list)
    constraints: list[dict[str, Any]] = Field(default_factory=list)
    failure_modes: list[UniversalFailureMode] = Field(default_factory=list)
