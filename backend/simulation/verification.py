"""Verified-repair loop: prove a repair in the twin before anyone touches the machine.

Diagnosis -> twin experiment -> VALIDATED / REJECTED verdict, where every step
is derived from the twin's own explicit simulation rules -- never from free
text, and never by guessing a link.

How it works
------------
1. The caller names the faulty component (a canonical entity id, usually from
   the matched snapshot failure mode's entity_ids).
2. The builder finds the twin's fault rules: transitions whose conditions
   mention that component. Their conditions ARE the fault state; their
   downstream effects ARE the symptom.
3. The builder finds the recovery rule: a transition whose effects restore
   the symptom to its healthy value. Its conditions ARE the repair.
4. The experiment runs baseline -> fault -> intervention in an isolated copy
   (the existing ``run_experiment``), and the verdict comes from its
   assertions -- always at least one, so the vacuous-validation path in
   ``run_experiment`` (no assertions -> validated whenever the state merely
   changed) is never reachable from here.

When verification is NOT possible it says so, instead of guessing:
- the twin has no transitions at all (no simulation rules were compiled in);
- no fault rule mentions the named component;
- the fault state does not reproduce the symptom in the twin;
- no recovery rule exists, or the intervention fires no transition at all
  (the twin simply does not model that repair).

An unavailable outcome is not a rejection: the model did not disprove the
repair, it cannot evaluate it. A rejection means the model DID propagate
the repair through its transitions and the symptom still did not recover.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .agent import run_experiment
from .schema import DigitalTwinDefinition
from .simulation_schema import ExperimentCommand, ExperimentRequest, ExperimentResult, StateAssertion


@dataclass
class RepairVerification:
    """Outcome of one verified-repair run. ``available`` is False exactly when
    the twin cannot evaluate this repair; ``verdict`` is only set when it can."""

    available: bool
    reason: str | None = None
    verdict: str | None = None  # "validated" | "rejected"
    result: ExperimentResult | None = None
    fault_rules: list[dict[str, Any]] = field(default_factory=list)
    recovery_rules: list[dict[str, Any]] = field(default_factory=list)
    symptom_path: str | None = None
    symptom_expected: Any = None


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (bool, int, float, str))


def _split_path(path: str) -> tuple[str, str] | None:
    if not isinstance(path, str) or "." not in path:
        return None
    component_id, field_name = path.split(".", 1)
    if not component_id or not field_name:
        return None
    return component_id, field_name


def _rule_summary(transition: Any) -> dict[str, Any]:
    return {
        "id": transition.id,
        "name": transition.name,
        "description": transition.description,
        "conditions": dict(transition.conditions),
        "effects": dict(transition.effects),
        "source_ids": list(getattr(transition, "source_ids", []) or []),
        "evidence": list(getattr(transition, "evidence", []) or []),
    }


def build_verification_experiment(
    definition: DigitalTwinDefinition,
    *,
    fault_component_id: str,
    repair_name: str = "",
    hypothesis: str = "",
) -> tuple[ExperimentRequest | None, RepairVerification]:
    """Build the fault -> intervention experiment for one faulty component.

    Returns (request, outcome). When the twin cannot express the fault or the
    repair, request is None and outcome carries available=False + the reason.
    """
    transitions = list(definition.transitions or [])
    if not transitions:
        return None, RepairVerification(
            available=False,
            reason="This twin has no simulation rules, so no repair can be verified in it yet.",
        )

    component_ids = {c.id for c in definition.components}
    if fault_component_id not in component_ids:
        return None, RepairVerification(
            available=False,
            reason=f"Component {fault_component_id} is not in this twin.",
        )

    # Candidate rules: transitions whose conditions mention the faulty component.
    # Their effects are the symptom; the split into fault vs recovery comes
    # from the effect value on the symptom path, not from the rule's name:
    # a rule whose effect on the symptom path differs from the healthy value
    # IS the fault, and one that restores the healthy value IS the repair.
    # (A rule's conditions mentioning the component is not enough on its own
    # -- the recovery rule mentions the same component with healthy values.)
    candidates = [
        t for t in transitions
        if any((_split_path(p) or ("", ""))[0] == fault_component_id for p in t.conditions)
    ]
    if not candidates:
        return None, RepairVerification(
            available=False,
            reason=(
                f"No simulation rule in this twin describes a fault in "
                f"{fault_component_id}; the model cannot express this failure."
            ),
        )

    # The symptom is the candidates' downstream effect: the first effect on
    # a component other than the faulty one, else the fault component's own
    # effect.
    symptom_path: str | None = None
    for rule in candidates:
        for path in rule.effects:
            split = _split_path(path)
            if split and split[0] != fault_component_id:
                symptom_path = path
                break
        if symptom_path:
            break
    if symptom_path is None:
        for rule in candidates:
            for path in rule.effects:
                symptom_path = path
                break
            if symptom_path:
                break
    if symptom_path is None:
        return None, RepairVerification(
            available=False,
            reason=f"Rules mentioning {fault_component_id} have no effects to check as a symptom.",
        )
    symptom_component_id, symptom_field = _split_path(symptom_path)  # type: ignore[misc]

    # Healthy value: the symptom component's initial state -- what the
    # compiled model says the machine starts in.
    healthy_value: Any = None
    healthy_known = False
    for component in definition.components:
        if component.id == symptom_component_id and symptom_field in component.initial_state:
            healthy_value = component.initial_state[symptom_field]
            healthy_known = True
            break

    if healthy_known:
        fault_rules = [t for t in candidates if t.effects.get(symptom_path) != healthy_value]
        recovery_rules = [
            t for t in transitions
            if t.effects.get(symptom_path) == healthy_value and t not in fault_rules
        ]
    else:
        # Fallback, only when the initial state does not say what healthy
        # is: the fault value is the first candidate's effect on the path,
        # and the recovery rule is the first other rule that sets the path
        # to a different value -- which then defines healthy.
        symptom_expected = next(
            (t.effects[symptom_path] for t in candidates if symptom_path in t.effects), None)
        fault_rules = [t for t in candidates if t.effects.get(symptom_path) == symptom_expected]
        recovery_rules = []
        for t in transitions:
            if t in fault_rules:
                continue
            if symptom_path in t.effects and t.effects[symptom_path] != symptom_expected:
                recovery_rules.append(t)
                healthy_value = t.effects[symptom_path]
                healthy_known = True
                break
    if not fault_rules:
        return None, RepairVerification(
            available=False,
            reason=(
                f"Rules mentioning {fault_component_id} never move {symptom_path} "
                f"away from its healthy value; the model describes no fault here."
            ),
        )
    if not recovery_rules or not healthy_known:
        return None, RepairVerification(
            available=False,
            reason=(
                f"No recovery rule in this twin restores {symptom_path}; the model "
                f"can show the fault but not the repair."
            ),
            fault_rules=[_rule_summary(t) for t in fault_rules],
            symptom_path=symptom_path,
            symptom_expected=fault_rules[0].effects.get(symptom_path),
        )
    symptom_expected = fault_rules[0].effects.get(symptom_path)

    for rule in fault_rules + recovery_rules:
        for path, value in list(rule.conditions.items()) + list(rule.effects.items()):
            if not _is_scalar(value):
                return None, RepairVerification(
                    available=False,
                    reason=(
                        f"Rule {rule.id} uses a condition the verifier cannot set "
                        f"({path} = {value!r}); only plain state values are supported."
                    ),
                )

    fault_commands: list[ExperimentCommand] = []
    seen: set[tuple[str, str]] = set()
    for rule in fault_rules:
        for path, value in rule.conditions.items():
            split = _split_path(path)
            if split is None or split in seen:
                continue
            seen.add(split)
            fault_commands.append(ExperimentCommand(
                command="set_component_state", target=split[0], field=split[1],
                value=value, reason=f"Fault state from rule {rule.id}: {rule.name}",
            ))
    intervention_commands: list[ExperimentCommand] = []
    seen = set()
    for rule in recovery_rules:
        for path, value in rule.conditions.items():
            split = _split_path(path)
            if split is None or split in seen:
                continue
            seen.add(split)
            intervention_commands.append(ExperimentCommand(
                command="set_component_state", target=split[0], field=split[1],
                value=value, reason=f"Repair from rule {rule.id}: {rule.name}",
            ))

    request = ExperimentRequest(
        name=repair_name or f"Verify repair for {fault_component_id}",
        hypothesis=hypothesis or f"Repairing {fault_component_id} restores {symptom_path} to {healthy_value!r}.",
        fault_commands=fault_commands,
        intervention_commands=intervention_commands,
        # Always at least one assertion: the vacuous path in run_experiment
        # (no assertions -> validated on any state change) is never reachable.
        assertions=[StateAssertion(path=f"components.{symptom_path}", expected=healthy_value)],
    )
    return request, RepairVerification(
        available=True,
        fault_rules=[_rule_summary(t) for t in fault_rules],
        recovery_rules=[_rule_summary(t) for t in recovery_rules],
        symptom_path=symptom_path,
        symptom_expected=symptom_expected,
    )


def run_verified_repair(
    definition: DigitalTwinDefinition,
    initial_state: dict[str, Any],
    *,
    fault_component_id: str,
    repair_name: str = "",
    hypothesis: str = "",
) -> RepairVerification:
    """Run the verified-repair loop for one faulty component.

    Pre-flight gates, in order, before any verdict is reported:
    1. The twin must be able to express the fault and the repair
       (build_verification_experiment).
    2. The fault state must reproduce the symptom -- else unavailable,
       "fault did not reproduce the symptom".
    3. The intervention must fire at least one real transition -- else
       unavailable, "no recovery transition fired". A repair the twin does
       not model is not a failed repair.
    Only then does the experiment's validated/rejected verdict stand.
    """
    request, outcome = build_verification_experiment(
        definition,
        fault_component_id=fault_component_id,
        repair_name=repair_name,
        hypothesis=hypothesis,
    )
    if request is None:
        return outcome

    result = run_experiment(definition, initial_state, request)
    outcome.result = result

    # Gate 2: the fault must reproduce the symptom in the twin.
    assert outcome.symptom_path is not None
    fault_value = _get_path(result.fault_state, f"components.{outcome.symptom_path}")
    if fault_value != outcome.symptom_expected:
        outcome.available = False
        outcome.verdict = None
        outcome.result = None
        outcome.reason = (
            f"Fault did not reproduce the symptom in the twin: "
            f"{outcome.symptom_path} was {fault_value!r}, "
            f"expected {outcome.symptom_expected!r}."
        )
        return outcome

    # Gate 3: the intervention must fire a real transition. The intervention
    # trace holds only step() change messages ("<rule>: <path> <old> -> <new>");
    # an empty trace means the repair changed nothing the model propagates.
    if not result.intervention_trace:
        outcome.available = False
        outcome.verdict = None
        outcome.result = None
        outcome.reason = (
            "No recovery transition fired for this repair in the twin; "
            "the model does not describe how this repair restores the machine."
        )
        return outcome

    outcome.verdict = result.verdict
    return outcome


def _get_path(state: dict[str, Any], path: str) -> Any:
    node: Any = state
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node
