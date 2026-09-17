"""Phase 7 — simulation agent for hypothesis and intervention experiments.

The agent runs *isolated* experiments against a deterministic Phase 6 twin.
It never mutates the persisted twin. This makes simulation safe to use as a
reasoning/verification tool: a proposed fault or repair can be reproduced,
tested, compared with a baseline, and rejected/accepted before a technician
touches the real machine.

This is deliberately not a physics solver. It verifies supported logical
machine behavior encoded by the twin's transitions.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .engine import DigitalTwinEngine
from .schema import DigitalTwinDefinition, TwinCommand
from .simulation_schema import (
    ExperimentCommand,
    ExperimentRequest,
    ExperimentResult,
    HypothesisExperiment,
    HypothesisResult,
)


def _get_path(state: dict[str, Any], path: str) -> Any:
    if path.startswith("components."):
        node: Any = state.get("components", {})
        parts = path.split(".")[1:]
    elif path.startswith("signals."):
        node = state.get("signals", {})
        parts = path.split(".")[1:]
    else:
        node = state.get("signals", {})
        parts = [path]
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _apply(engine: DigitalTwinEngine, commands: list[ExperimentCommand]) -> list[str]:
    trace: list[str] = []
    for command in commands:
        trace.extend(
            engine.command(
                command.command,
                command.target,
                command.field,
                command.value,
                command.reason,
            )
        )
    return trace


def _diff(before: dict[str, Any], after: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Small recursive state diff suitable for UI and machine-readable logs."""
    out: dict[str, Any] = {}
    keys = set(before) | set(after)
    for key in sorted(keys):
        path = f"{prefix}.{key}" if prefix else key
        a, b = before.get(key), after.get(key)
        if isinstance(a, dict) and isinstance(b, dict):
            out.update(_diff(a, b, path))
        elif a != b:
            out[path] = {"before": deepcopy(a), "after": deepcopy(b)}
    return out


def run_experiment(definition: DigitalTwinDefinition, initial_state: dict[str, Any],
                   request: ExperimentRequest) -> ExperimentResult:
    """Run baseline → fault → intervention in an isolated clone."""
    baseline_engine = DigitalTwinEngine(definition, initial_state)
    baseline_engine.step()
    baseline = deepcopy(baseline_engine.state)

    fault_engine = DigitalTwinEngine(definition, baseline)
    fault_trace = _apply(fault_engine, request.fault_commands)
    fault_state = deepcopy(fault_engine.state)

    intervention_engine = DigitalTwinEngine(definition, fault_state)
    intervention_trace = _apply(intervention_engine, request.intervention_commands)
    final_state = deepcopy(intervention_engine.state)

    observed: dict[str, Any] = {}
    checks: list[dict[str, Any]] = []
    for assertion in request.assertions:
        actual = _get_path(final_state, assertion.path)
        passed = assertion.matches(actual)
        checks.append({"path": assertion.path, "expected": assertion.expected,
                       "actual": actual, "passed": passed})
        observed[assertion.path] = actual

    passed = all(c["passed"] for c in checks) if checks else final_state != fault_state
    verdict = "validated" if passed else "rejected"

    return ExperimentResult(
        name=request.name,
        hypothesis=request.hypothesis,
        verdict=verdict,
        validated=passed,
        baseline_state=baseline,
        fault_state=fault_state,
        final_state=final_state,
        fault_trace=fault_trace,
        intervention_trace=intervention_trace,
        state_changes=_diff(baseline, final_state),
        checks=checks,
        explanation=(
            "The intervention restored all requested machine-state assertions."
            if passed else
            "The intervention did not satisfy all requested machine-state assertions."
        ),
        warnings=list(
            dict.fromkeys(
                baseline_engine.warnings
                + ["Experiment ran in an isolated copy; no persisted twin state was changed."]
            )
        ),
    )


def run_hypotheses(definition: DigitalTwinDefinition, initial_state: dict[str, Any],
                   experiments: list[HypothesisExperiment]) -> list[HypothesisResult]:
    """Run competing candidate experiments and return comparable results."""
    results: list[HypothesisResult] = []
    for item in experiments:
        result = run_experiment(
            definition,
            initial_state,
            ExperimentRequest(
                name=item.name,
                hypothesis=item.hypothesis,
                fault_commands=item.fault_commands,
                intervention_commands=item.intervention_commands,
                assertions=item.assertions,
            ),
        )
        results.append(HypothesisResult(
            name=item.name,
            hypothesis=item.hypothesis,
            verdict=result.verdict,
            validated=result.validated,
            explanation=result.explanation,
            checks=result.checks,
            state_changes=result.state_changes,
        ))
    return results
