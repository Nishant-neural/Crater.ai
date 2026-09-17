"""Deterministic, explainable digital-twin execution engine.

This is intentionally a functional state-machine model, not a physics solver.
It is designed to answer: "given this machine state and this fault, what
does the model predict?" Every mutation is explicit and produces a trace.
"""
from __future__ import annotations
from copy import deepcopy
from typing import Any
from .schema import DigitalTwinDefinition, TwinSnapshot


def _matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        if "equals" in expected:
            return actual == expected["equals"]
        if "not_equals" in expected:
            return actual != expected["not_equals"]
        if "in" in expected:
            return actual in expected["in"]
    return actual == expected


def _get(state: dict[str, Any], key: str) -> Any:
    if key in state:
        return state[key]
    return None


class DigitalTwinEngine:
    def __init__(self, definition: DigitalTwinDefinition, state: dict[str, Any] | None = None):
        self.definition = definition
        self.state = deepcopy(state) if state is not None else self._initial_state()
        self.trace: list[str] = []
        self.warnings = [
            "Simulation is deterministic and virtual; it is not proof of physical safety or real-machine behavior."
        ]

    def _initial_state(self) -> dict[str, Any]:
        state = {"signals": deepcopy(self.definition.initial_signals), "components": {}}
        for c in self.definition.components:
            state["components"][c.id] = deepcopy(c.initial_state)
        return state

    def snapshot(self, twin_id: str, name: str, status: str = "active") -> TwinSnapshot:
        return TwinSnapshot(
            twin_id=twin_id, name=name, status=status, state=deepcopy(self.state),
            derived=self._derived(), trace=list(self.trace), warnings=list(self.warnings)
        )

    def _derived(self) -> dict[str, Any]:
        return {
            "safety_relay_energized": self._component_field("safety_relay", "energized"),
            "controller_enabled": self._component_field("controller", "enabled"),
            "motor_running": self._component_field("motor", "running"),
        }

    def _component_field(self, component_id: str, field: str) -> Any:
        return self.state.get("components", {}).get(component_id, {}).get(field)

    def _set_path(self, path: str, value: Any) -> None:
        # Supported paths: signal key, component_id.field
        if "." in path:
            cid, field = path.split(".", 1)
            self.state.setdefault("components", {}).setdefault(cid, {})[field] = value
        else:
            self.state.setdefault("signals", {})[path] = value

    def _get_path(self, path: str) -> Any:
        if "." in path:
            cid, field = path.split(".", 1)
            return self.state.get("components", {}).get(cid, {}).get(field)
        return self.state.get("signals", {}).get(path)

    def step(self) -> list[str]:
        changes: list[str] = []
        # Fixed-point evaluation makes dependent transitions settle in one step.
        for _ in range(max(1, len(self.definition.transitions) + 1)):
            changed = False
            for t in self.definition.transitions:
                if all(_matches(self._get_path(k), v) for k, v in t.conditions.items()):
                    for path, value in t.effects.items():
                        old = self._get_path(path)
                        if old != value:
                            self._set_path(path, value)
                            msg = f"{t.name}: {path} {old!r} → {value!r}"
                            changes.append(msg)
                            changed = True
            if not changed:
                break
        self.trace.extend(changes or ["No transition changed the machine state."])
        return changes

    def command(self, command: str, target: str | None = None, field: str | None = None,
                value: Any = None, reason: str | None = None) -> list[str]:
        if command == "reset":
            self.state = self._initial_state()
            self.trace.append("Machine state reset to the digital-twin initial condition.")
            return self.step()
        if command == "step":
            return self.step()
        if command == "set_signal":
            if not target:
                raise ValueError("target is required for set_signal")
            self._set_path(target, value)
            self.trace.append(f"Set signal {target} = {value!r}.")
        elif command == "set_component_state":
            if not target or not field:
                raise ValueError("target and field are required for set_component_state")
            self._set_path(f"{target}.{field}", value)
            self.trace.append(f"Set {target}.{field} = {value!r}.")
        elif command == "inject_fault":
            if not target:
                raise ValueError("target is required for inject_fault")
            fault_field = field or "fault"
            self._set_path(f"{target}.{fault_field}", value if value is not None else True)
            self.trace.append(f"Injected fault at {target}.{fault_field} = {value if value is not None else True!r}.")
            if reason:
                self.trace.append(f"Fault reason: {reason}")
        elif command == "clear_fault":
            if not target:
                raise ValueError("target is required for clear_fault")
            fault_field = field or "fault"
            self._set_path(f"{target}.{fault_field}", False)
            self.trace.append(f"Cleared fault at {target}.{fault_field}.")
        else:
            raise ValueError(f"Unsupported command: {command}")
        return self.step()


def demo_definition() -> DigitalTwinDefinition:
    """Small complete machine used by the Phase 6 UI/tests."""
    return DigitalTwinDefinition(
        name="Demo Motor Drive",
        description="A deterministic safety-controlled motor chain for validating diagnostic hypotheses.",
        components=[
            {"id": "power", "name": "Power Source", "component_type": "power_source", "initial_state": {}},
            {"id": "safety_relay", "name": "Safety Relay", "component_type": "relay", "initial_state": {"energized": False}},
            {"id": "controller", "name": "Drive Controller", "component_type": "controller", "initial_state": {"enabled": False}},
            {"id": "motor", "name": "Motor", "component_type": "actuator", "initial_state": {"running": False}},
            {"id": "door_interlock", "name": "Door Interlock", "component_type": "sensor", "initial_state": {"closed": True}},
            {"id": "x12", "name": "Connector X12", "component_type": "connector", "initial_state": {"connected": True}},
        ],
        initial_signals={"power_on": True, "start_command": False},
        transitions=[
            {"id": "t1", "name": "Safety relay logic",
             "conditions": {"power_on": True, "door_interlock.closed": True, "x12.connected": True},
             "effects": {"safety_relay.energized": True},
             "description": "Power and both interlocks must be healthy before the safety relay energizes."},
            {"id": "t2", "name": "Controller enable logic",
             "conditions": {"safety_relay.energized": True},
             "effects": {"controller.enabled": True},
             "description": "The controller enables only after the safety relay is energized."},
            {"id": "t3", "name": "Motor start logic",
             "conditions": {"controller.enabled": True, "start_command": True},
             "effects": {"motor.running": True},
             "description": "The motor runs when the enabled controller receives a start command."},
            {"id": "t4", "name": "Safety relay drops on interlock loss",
             "conditions": {"door_interlock.closed": False},
             "effects": {"safety_relay.energized": False, "controller.enabled": False, "motor.running": False}},
            {"id": "t5", "name": "Safety relay drops on X12 loss",
             "conditions": {"x12.connected": False},
             "effects": {"safety_relay.energized": False, "controller.enabled": False, "motor.running": False}},
            {"id": "t6", "name": "Safety relay drops on power loss",
             "conditions": {"power_on": False},
             "effects": {"safety_relay.energized": False, "controller.enabled": False, "motor.running": False}},
            {"id": "t7", "name": "Motor stops without start command",
             "conditions": {"start_command": False},
             "effects": {"motor.running": False}},
        ],
    )
