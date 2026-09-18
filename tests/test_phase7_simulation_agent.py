"""Phase 7 tests — isolated simulation experiments and hypothesis comparison."""
from backend.simulation.agent import run_experiment, run_hypotheses
from backend.simulation.engine import DigitalTwinEngine, demo_definition
from backend.simulation.simulation_schema import (
    ExperimentRequest, ExperimentCommand, StateAssertion, HypothesisExperiment,
)


def test_intervention_validates_without_mutating_original_engine():
    definition = demo_definition()
    original = DigitalTwinEngine(definition)
    original.step()
    before = original.state.copy()

    request = ExperimentRequest(
        name="X12 recovery",
        hypothesis="Open X12 is the cause of the motor stop.",
        fault_commands=[
            ExperimentCommand(command="set_signal", target="start_command", value=True),
            ExperimentCommand(command="set_component_state", target="x12", field="connected", value=False),
        ],
        intervention_commands=[
            ExperimentCommand(command="set_component_state", target="x12", field="connected", value=True),
        ],
        assertions=[StateAssertion(path="components.motor.running", expected=True)],
    )
    result = run_experiment(definition, original.state, request)

    assert result.validated is True
    assert result.verdict == "validated"
    assert result.fault_state["components"]["motor"]["running"] is False
    assert result.final_state["components"]["motor"]["running"] is True
    assert original.state == before


def test_rejected_intervention_is_reported():
    definition = demo_definition()
    engine = DigitalTwinEngine(definition)
    engine.step()
    request = ExperimentRequest(
        name="Wrong repair",
        hypothesis="Disconnecting X12 fixes the motor.",
        fault_commands=[ExperimentCommand(command="set_signal", target="start_command", value=True)],
        intervention_commands=[ExperimentCommand(command="set_component_state", target="x12", field="connected", value=False)],
        assertions=[StateAssertion(path="components.motor.running", expected=True)],
    )
    result = run_experiment(definition, engine.state, request)
    assert result.validated is False
    assert result.verdict == "rejected"


def test_competing_hypotheses_are_comparable():
    definition = demo_definition()
    engine = DigitalTwinEngine(definition)
    engine.step()
    items = [
        HypothesisExperiment(
            name="X12",
            hypothesis="X12 is open.",
            fault_commands=[ExperimentCommand(command="set_signal", target="start_command", value=True),
                            ExperimentCommand(command="set_component_state", target="x12", field="connected", value=False)],
            intervention_commands=[ExperimentCommand(command="set_component_state", target="x12", field="connected", value=True)],
            assertions=[StateAssertion(path="components.motor.running", expected=True)],
        ),
        HypothesisExperiment(
            name="Door",
            hypothesis="Door is open.",
            fault_commands=[ExperimentCommand(command="set_signal", target="start_command", value=True),
                            ExperimentCommand(command="set_component_state", target="door_interlock", field="closed", value=False)],
            intervention_commands=[ExperimentCommand(command="set_component_state", target="door_interlock", field="closed", value=True)],
            assertions=[StateAssertion(path="components.motor.running", expected=True)],
        ),
    ]
    results = run_hypotheses(definition, engine.state, items)
    assert len(results) == 2
    assert all(r.validated for r in results)
