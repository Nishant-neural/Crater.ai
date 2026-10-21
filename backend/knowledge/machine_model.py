"""Universal machine knowledge model for Phase 8A.

This is intentionally small and extensible: it captures a machine using a
shared set of primitives rather than a product-specific schema. Domain details
are carried as extra properties on the entities and as supplemental metadata.
"""
from __future__ import annotations

from typing import Any
import hashlib
import json
import re

from pydantic import BaseModel, Field, model_validator

from backend.knowledge.evidence import MachineEvidence


class MachineModelBase(BaseModel):
    """Shared boundary normalizer for imperfect LLM/legacy payloads.

    It maps common aliases and supplies explicit unresolved placeholders for
    structurally required fields. It never treats placeholders as verified facts.
    """
    @model_validator(mode="before")
    @classmethod
    def normalize_external_payload(cls, value):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        aliases = {
            "id": ("fact_id", "behavior_id", "event_id", "port_id", "quantity_id", "constraint_id", "procedure_id", "failure_id"),
            "name": ("component", "label", "title", "alarm", "failure", "procedure_name"),
            "description": ("behavior", "behaviour", "effect", "function", "details", "text", "fact"),
            "subject_id": ("entity_id", "component_id", "source_entity_id"),
            "subject_name": ("component", "entity_name", "subject", "name"),
            "entity_id": ("component_id", "subject_id"),
            "entity_type": ("type", "component_type", "kind"),
            "relation_type": ("type", "relationship", "relation"),
            "object_id": ("target_id", "to_id", "object_entity_id"),
            "object_name": ("target", "to_component", "to", "object"),
            "value": ("setting", "parameter_value"),
            "unit": ("units",),
            "steps": ("procedure_steps", "instructions"),
            "possible_causes": ("causes", "cause"),
            "symptoms": ("symptom",),
            "source_ids": ("source_id", "evidence_ids"),
        }
        fields = getattr(cls, "model_fields", {})
        for target, candidates in aliases.items():
            if target not in fields or data.get(target) not in (None, "", []):
                continue
            for alias in candidates:
                candidate = data.get(alias)
                if candidate not in (None, "", []):
                    data[target] = candidate
                    break
        def stable_id(prefix: str) -> str:
            raw = json.dumps(data, sort_keys=True, default=str, ensure_ascii=False)
            digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
            return f"{prefix}:{digest}"
        defaults = {
            "id": lambda: stable_id(cls.__name__.removeprefix("Machine").lower()),
            "name": lambda: "Unresolved item",
            "entity_type": lambda: "unknown",
            "subject_id": lambda: "unresolved:subject",
            "subject_name": lambda: str(data.get("component") or data.get("name") or "Unresolved subject"),
            "description": lambda: "Description missing; original payload retained",
            "relation_type": lambda: "unspecified",
            "object_id": lambda: "unresolved:object",
            "object_name": lambda: "Unresolved object",
            "entity_id": lambda: "unresolved:entity",
            "procedure_type": lambda: "unspecified",
            "steps": list, "properties": dict, "ports": list, "states": list,
            "source_ids": list, "evidence": list, "quantities": list,
            "symptoms": list, "possible_causes": list, "entity_ids": list,
            "values": list, "status": lambda: "unresolved",
        }
        for field_name, field in fields.items():
            if field_name in data and data[field_name] not in (None, ""):
                continue
            if field_name in defaults and field.is_required():
                data[field_name] = defaults[field_name]()
        # Normalize scalar identifiers to strings and source_id to a list.
        if isinstance(data.get("source_ids"), str):
            data["source_ids"] = [data["source_ids"]]
        return data


class MachineEntity(MachineModelBase):
    id: str
    name: str
    entity_type: str
    properties: dict[str, Any] = Field(default_factory=dict)
    ports: list[str] = Field(default_factory=list)
    states: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    evidence: list[MachineEvidence] = Field(default_factory=list)


class MachineEntityRelation(MachineModelBase):
    id: str | None = None
    subject_id: str | None = None
    subject_name: str | None = None
    relation_type: str | None = None
    object_id: str | None = None
    object_name: str | None = None
    description: str | None = None
    evidence: list[MachineEvidence] = Field(default_factory=list)


class MachineRelation(MachineModelBase):
    source_ids: list[str] = Field(default_factory=list)
    subject_id: str
    subject_name: str
    relation_type: str
    object_id: str
    object_name: str
    description: str | None = None
    evidence: list[MachineEvidence] = Field(default_factory=list)


class MachinePort(MachineModelBase):
    id: str
    source_ids: list[str] = Field(default_factory=list)
    entity_id: str
    name: str
    direction: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)
    evidence: list[MachineEvidence] = Field(default_factory=list)


class MachineQuantity(MachineModelBase):
    id: str
    source_ids: list[str] = Field(default_factory=list)
    entity_id: str
    name: str
    value: str | None = None
    unit: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)
    evidence: list[MachineEvidence] = Field(default_factory=list)


class MachineState(MachineModelBase):
    source_ids: list[str] = Field(default_factory=list)
    entity_id: str
    name: str
    value: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)
    evidence: list[MachineEvidence] = Field(default_factory=list)


class MachineEvent(MachineModelBase):
    id: str
    source_ids: list[str] = Field(default_factory=list)
    entity_id: str
    name: str
    description: str | None = None
    evidence: list[MachineEvidence] = Field(default_factory=list)


class MachineBehavior(MachineModelBase):
    id: str
    source_ids: list[str] = Field(default_factory=list)
    subject_id: str
    subject_name: str
    description: str
    evidence: list[MachineEvidence] = Field(default_factory=list)


class MachineConstraint(MachineModelBase):
    id: str
    source_ids: list[str] = Field(default_factory=list)
    entity_id: str | None = None
    name: str
    description: str
    evidence: list[MachineEvidence] = Field(default_factory=list)


class MachineProcedure(MachineModelBase):
    id: str
    source_ids: list[str] = Field(default_factory=list)
    name: str
    procedure_type: str
    steps: list[str] = Field(default_factory=list)
    entity_ids: list[str] = Field(default_factory=list)
    evidence: list[MachineEvidence] = Field(default_factory=list)


class MachineFailureMode(MachineModelBase):
    id: str
    source_ids: list[str] = Field(default_factory=list)
    name: str
    symptoms: list[str] = Field(default_factory=list)
    possible_causes: list[str] = Field(default_factory=list)
    diagnostic_test: str | None = None
    expected_observation: str | None = None
    repair_procedure_id: str | None = None
    entity_ids: list[str] = Field(default_factory=list)
    evidence: list[MachineEvidence] = Field(default_factory=list)


class MachineConflict(MachineModelBase):
    id: str
    source_ids: list[str] = Field(default_factory=list)
    subject: str
    property: str
    values: list[Any] = Field(default_factory=list)
    evidence: list[MachineEvidence] = Field(default_factory=list)
    resolution: str | None = None
    status: str = "unresolved"


class MachineUnresolvedFact(MachineModelBase):
    source_id: str
    fact_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    reason: str = "not_accounted_for_by_global_integration"


class UniversalMachineModel(MachineModelBase):
    """A compact, extensible representation for machine knowledge."""

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
    conflicts: list[MachineConflict] = Field(default_factory=list)
    unresolved_facts: list[MachineUnresolvedFact] = Field(default_factory=list)
    # Optional explicit executable rules. These are accepted only when grounded in source data;
    # the simulator never invents rules from prose.
    simulation_rules: list[dict[str, Any]] = Field(default_factory=list)
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
        ] + [
            ("procedure", item.id, item.model_dump(mode="json")) for item in self.procedures
        ] + [
            ("failure_mode", item.id, item.model_dump(mode="json")) for item in self.failure_modes
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
