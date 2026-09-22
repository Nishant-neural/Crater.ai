"""Universal machine knowledge model for Phase 8A.

This is intentionally small and extensible: it captures a machine using a
shared set of primitives rather than a product-specific schema. Domain details
are carried as extra properties on the entities and as supplemental metadata.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from backend.knowledge.evidence import MachineEvidence


class MachineEntity(BaseModel):
    id: str
    name: str
    entity_type: str
    properties: dict[str, Any] = Field(default_factory=dict)
    ports: list[str] = Field(default_factory=list)
    states: list[str] = Field(default_factory=list)
    evidence: list[MachineEvidence] = Field(default_factory=list)


class MachineEntityRelation(BaseModel):
    id: str | None = None
    subject_id: str | None = None
    subject_name: str | None = None
    relation_type: str | None = None
    object_id: str | None = None
    object_name: str | None = None
    description: str | None = None
    evidence: list[MachineEvidence] = Field(default_factory=list)


class MachineRelation(BaseModel):
    subject_id: str
    subject_name: str
    relation_type: str
    object_id: str
    object_name: str
    description: str | None = None
    evidence: list[MachineEvidence] = Field(default_factory=list)


class MachinePort(BaseModel):
    id: str
    entity_id: str
    name: str
    direction: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)
    evidence: list[MachineEvidence] = Field(default_factory=list)


class MachineQuantity(BaseModel):
    id: str
    entity_id: str
    name: str
    value: str | None = None
    unit: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)
    evidence: list[MachineEvidence] = Field(default_factory=list)


class MachineState(BaseModel):
    entity_id: str
    name: str
    value: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)
    evidence: list[MachineEvidence] = Field(default_factory=list)


class MachineEvent(BaseModel):
    id: str
    entity_id: str
    name: str
    description: str | None = None
    evidence: list[MachineEvidence] = Field(default_factory=list)


class MachineBehavior(BaseModel):
    id: str
    subject_id: str
    subject_name: str
    description: str
    evidence: list[MachineEvidence] = Field(default_factory=list)


class MachineConstraint(BaseModel):
    id: str
    entity_id: str | None = None
    name: str
    description: str
    evidence: list[MachineEvidence] = Field(default_factory=list)


class UniversalMachineModel(BaseModel):
    """A compact, extensible representation for machine knowledge."""

    entities: list[MachineEntity] = Field(default_factory=list)
    relations: list[MachineRelation] = Field(default_factory=list)
    ports: list[MachinePort] = Field(default_factory=list)
    quantities: list[MachineQuantity] = Field(default_factory=list)
    states: list[MachineState] = Field(default_factory=list)
    events: list[MachineEvent] = Field(default_factory=list)
    behaviors: list[MachineBehavior] = Field(default_factory=list)
    constraints: list[MachineConstraint] = Field(default_factory=list)
    evidence: list[MachineEvidence] = Field(default_factory=list)

    def all_facts(self) -> list[tuple[str, str, Any]]:
        """Return every typed primitive as a serializable fact tuple."""
        return [
            ("port", item.id, item.model_dump(mode="json")) for item in self.ports
        ] + [
            ("quantity", item.id, item.model_dump(mode="json")) for item in self.quantities
        ] + [
            ("state", f"{item.entity_id}:{item.name}", item.model_dump(mode="json")) for item in self.states
        ] + [
            ("event", item.id, item.model_dump(mode="json")) for item in self.events
        ] + [
            ("behavior", item.id, item.model_dump(mode="json")) for item in self.behaviors
        ] + [
            ("constraint", item.id, item.model_dump(mode="json")) for item in self.constraints
        ]

    def entity_map(self) -> dict[str, MachineEntity]:
        return {entity.id: entity for entity in self.entities}

    def relation_map(self) -> dict[str, MachineRelation]:
        return {f"{r.subject_id}->{r.object_id}:{r.relation_type}": r for r in self.relations}

    def add_entity(self, entity: MachineEntity) -> None:
        if entity not in self.entities:
            self.entities.append(entity)

    def add_relation(self, relation: MachineRelation) -> None:
        if relation not in self.relations:
            self.relations.append(relation)


# Canonical universal-machine vocabulary expected by the Phase 8A design.
Entity = MachineEntity
Relation = MachineRelation
Port = MachinePort
Quantity = MachineQuantity
State = MachineState
Event = MachineEvent
Behavior = MachineBehavior
Constraint = MachineConstraint
MachineModel = UniversalMachineModel
