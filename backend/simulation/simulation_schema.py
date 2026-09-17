"""Typed requests/results for Phase 7 simulation experiments."""
from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field


class ExperimentCommand(BaseModel):
    command: Literal[
        "set_signal", "set_component_state", "inject_fault",
        "clear_fault", "reset", "step"
    ]
    target: str | None = None
    field: str | None = None
    value: Any = None
    reason: str | None = None


class StateAssertion(BaseModel):
    path: str
    expected: Any

    def matches(self, actual: Any) -> bool:
        return actual == self.expected


class ExperimentRequest(BaseModel):
    name: str
    hypothesis: str
    fault_commands: list[ExperimentCommand] = Field(default_factory=list)
    intervention_commands: list[ExperimentCommand] = Field(default_factory=list)
    assertions: list[StateAssertion] = Field(default_factory=list)


class ExperimentResult(BaseModel):
    name: str
    hypothesis: str
    verdict: Literal["validated", "rejected"]
    validated: bool
    baseline_state: dict[str, Any]
    fault_state: dict[str, Any]
    final_state: dict[str, Any]
    fault_trace: list[str] = Field(default_factory=list)
    intervention_trace: list[str] = Field(default_factory=list)
    state_changes: dict[str, Any] = Field(default_factory=dict)
    checks: list[dict[str, Any]] = Field(default_factory=list)
    explanation: str
    warnings: list[str] = Field(default_factory=list)


class HypothesisExperiment(BaseModel):
    name: str
    hypothesis: str
    fault_commands: list[ExperimentCommand] = Field(default_factory=list)
    intervention_commands: list[ExperimentCommand] = Field(default_factory=list)
    assertions: list[StateAssertion] = Field(default_factory=list)


class HypothesisResult(BaseModel):
    name: str
    hypothesis: str
    verdict: Literal["validated", "rejected"]
    validated: bool
    explanation: str
    checks: list[dict[str, Any]] = Field(default_factory=list)
    state_changes: dict[str, Any] = Field(default_factory=dict)


class HypothesisBatchRequest(BaseModel):
    experiments: list[HypothesisExperiment] = Field(min_length=1)


class HypothesisBatchResult(BaseModel):
    results: list[HypothesisResult]
    warnings: list[str] = Field(default_factory=lambda: [
        "Results verify only behavior encoded by this digital twin; they do not prove physical safety."
    ])
