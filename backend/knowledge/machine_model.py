"""Canonical universal machine knowledge model for Phase 8A.

The classes in this module are the domain model used by extraction, validation,
persistence, retrieval and future graph compilation. Provider schemas and SQL
rows are adapters around this model; they are not competing representations.
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


class MachineProcedure(BaseModel):
    id: str
    name: str
    procedure_type: str
    steps: list[str] = Field(default_factory=list)
    evidence: list[MachineEvidence] = Field(default_factory=list)


class MachineFailureMode(BaseModel):
    id: str
    name: str
    symptom: str | None = None
    possible_causes: list[str] = Field(default_factory=list)
    diagnostic_test: str | None = None
    expected_observation: str | None = None
    repair_procedure_id: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)
    evidence: list[MachineEvidence] = Field(default_factory=list)


class UniversalMachineModel(BaseModel):
    """Single canonical representation of machine knowledge."""

    entities: list[MachineEntity] = Field(default_factory=list)
    relations: list[MachineRelation] = Field(default_factory=list)
    ports: list[MachinePort] = Field(default_factory=list)
    quantities: list[MachineQuantity] = Field(default_factory=list)
    states: list[MachineState] = Field(default_factory=list)
    events: list[MachineEvent] = Field(default_factory=list)
    behaviors: list[MachineBehavior] = Field(default_factory=list)
    constraints: list[MachineConstraint] = Field(default_factory=list)
    procedures: list[MachineProcedure] = Field(default_factory=list)
    failure_modes: list[MachineFailureMode] = Field(default_factory=list)
    evidence: list[MachineEvidence] = Field(default_factory=list)

    def all_facts(self) -> list[tuple[str, str, Any]]:
        """Return non-entity primitives as persistence-ready fact tuples."""
        collections = (
            ("port", self.ports),
            ("quantity", self.quantities),
            ("state", self.states),
            ("event", self.events),
            ("constraint", self.constraints),
            ("procedure", self.procedures),
            ("failure_mode", self.failure_modes),
        )
        facts: list[tuple[str, str, Any]] = []
        for fact_type, items in collections:
            for item in items:
                if isinstance(item, MachineState):
                    key = f"{item.entity_id}:{item.name}"
                else:
                    key = item.id
                facts.append((fact_type, key, item.model_dump(mode="json")))
        return facts

    def entity_map(self) -> dict[str, MachineEntity]:
        return {entity.id: entity for entity in self.entities}

    def relation_map(self) -> dict[str, MachineRelation]:
        return {
            f"{r.subject_id}->{r.object_id}:{r.relation_type}": r
            for r in self.relations
        }

    def add_entity(self, entity: MachineEntity) -> None:
        if entity.id not in self.entity_map():
            self.entities.append(entity)

    def add_relation(self, relation: MachineRelation) -> None:
        if relation not in self.relations:
            self.relations.append(relation)


# Stable short aliases for callers that prefer the universal vocabulary.
Entity = MachineEntity
Relation = MachineRelation
Port = MachinePort
Quantity = MachineQuantity
State = MachineState
Event = MachineEvent
Behavior = MachineBehavior
Constraint = MachineConstraint
Procedure = MachineProcedure
FailureMode = MachineFailureMode
MachineModel = UniversalMachineModel
MachineEntityRelation = MachineRelation  # compatibility alias from the pre-canonical model
