"""Provider-facing extraction contracts.

The provider contract may retain legacy component/procedure fields for backward
compatibility, but universal entities and relations are the canonical domain
types from ``machine_model``. No second UniversalEntity/UniversalRelation
schema is maintained here.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from backend.knowledge.machine_model import (
    MachineBehavior,
    MachineConstraint,
    MachineEntity,
    MachineEvent,
    MachineFailureMode,
    MachinePort,
    MachineProcedure,
    MachineQuantity,
    MachineRelation,
    MachineState,
)


class ExtractedComponent(BaseModel):
    name: str
    function: str | None = None
    location_description: str | None = None
    part_number: str | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class ExtractedRelationship(BaseModel):
    from_component: str
    to_component: str
    relation_type: str
    description: str | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class ExtractedProcedure(BaseModel):
    name: str
    procedure_type: str
    steps: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    """Legacy Product Brain projection used during the migration period."""

    components: list[ExtractedComponent] = Field(default_factory=list)
    relationships: list[ExtractedRelationship] = Field(default_factory=list)
    procedures: list[ExtractedProcedure] = Field(default_factory=list)


class MachineKnowledgeExtractionResult(BaseModel):
    """Raw provider result before conversion to the canonical machine model."""

    # Legacy fields remain only as an adapter for existing Product Brain tables.
    components: list[ExtractedComponent] = Field(default_factory=list)
    relationships: list[ExtractedRelationship] = Field(default_factory=list)
    procedures: list[ExtractedProcedure] = Field(default_factory=list)

    # Canonical universal primitives.
    entities: list[MachineEntity] = Field(default_factory=list)
    relations: list[MachineRelation] = Field(default_factory=list)
    ports: list[MachinePort] = Field(default_factory=list)
    quantities: list[MachineQuantity] = Field(default_factory=list)
    states: list[MachineState] = Field(default_factory=list)
    events: list[MachineEvent] = Field(default_factory=list)
    behaviors: list[MachineBehavior] = Field(default_factory=list)
    constraints: list[MachineConstraint] = Field(default_factory=list)
    universal_procedures: list[MachineProcedure] = Field(default_factory=list)
    failure_modes: list[MachineFailureMode] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)

    def has_universal_facts(self) -> bool:
        return any((
            self.entities, self.relations, self.ports, self.quantities,
            self.states, self.events, self.behaviors, self.constraints,
            self.universal_procedures, self.failure_modes, self.evidence,
        ))
