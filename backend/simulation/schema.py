"""Typed Phase 6 digital-twin definitions and commands."""
from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel, Field


class TwinComponent(BaseModel):
    id: str
    name: str
    component_type: Literal["sensor", "relay", "controller", "actuator", "signal", "power_source", "connector", "other"]
    initial_state: dict[str, Any] = Field(default_factory=dict)


class TwinTransition(BaseModel):
    id: str
    name: str
    conditions: dict[str, Any] = Field(default_factory=dict)
    effects: dict[str, Any] = Field(default_factory=dict)
    description: str = ""
    safety_notes: list[str] = Field(default_factory=list)


class DigitalTwinDefinition(BaseModel):
    name: str
    description: str = ""
    components: list[TwinComponent]
    initial_signals: dict[str, Any] = Field(default_factory=dict)
    transitions: list[TwinTransition] = Field(default_factory=list)


class TwinCommand(BaseModel):
    command: Literal["set_signal", "set_component_state", "inject_fault", "clear_fault", "reset", "step"]
    target: str | None = None
    field: str | None = None
    value: Any = None
    reason: str | None = None


class TwinSnapshot(BaseModel):
    twin_id: str
    name: str
    status: str
    simulated_only: bool = True
    state: dict[str, Any]
    derived: dict[str, Any]
    trace: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class TwinCreate(BaseModel):
    product_id: str
    revision_id: str
    definition: DigitalTwinDefinition


class TwinView(BaseModel):
    id: str
    product_id: str
    revision_id: str
    name: str
    status: str
    model_version: int
    definition: DigitalTwinDefinition
    snapshot: TwinSnapshot
