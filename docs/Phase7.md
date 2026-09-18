# Phase 7 — Simulation Agent

## Goal

Phase 7 turns the Phase 6 deterministic digital twin into a **reasoning and
verification tool**. The system can now run a proposed machine-failure
hypothesis in an isolated copy of the twin, apply a simulated intervention,
compare the resulting state with the desired outcome, and return a
machine-readable verdict.

This phase intentionally does **not** claim physical or physics-level
validation. It verifies only behavior encoded in the twin's transition model.

## What Phase 7 adds

### 1. Isolated experiment runner

The Simulation Agent executes a fixed experiment pipeline:

```text
Current Twin State
       │
       ├──────────────► Baseline
       │
       ▼
 Inject / reproduce fault
       │
       ▼
   Fault state
       │
       ▼
 Proposed intervention
       │
       ▼
    Final state
       │
       ▼
 Assertions + state diff
       │
       ▼
 Validated / Rejected
```

The persisted Phase 6 digital twin is never mutated by an experiment.

### 2. Hypothesis testing

Multiple competing hypotheses can be submitted as experiments. Each one has:

- hypothesis text
- fault/reproduction commands
- proposed intervention commands
- expected state assertions

The agent returns comparable results for every hypothesis.

### 3. Explainable traces

Each experiment exposes:

- baseline state
- fault state
- final state
- fault execution trace
- intervention execution trace
- recursive state changes
- assertion results
- verdict
- warnings

This makes the simulation useful as an evidence-producing step rather than
a black-box "AI says the repair works" feature.

### 4. Frontend Simulation Agent

A new UI tab provides two ready-to-run demo experiments:

- X12 connector recovery
- Door interlock recovery

It also provides a **Compare hypotheses** action that runs both against the
same twin and displays their individual validation results.

## New files

| File | Purpose |
|---|---|
| `backend/simulation/agent.py` | Core Phase 7 isolated experiment and hypothesis runner |
| `backend/simulation/simulation_schema.py` | Typed experiment requests, assertions, results, and hypothesis batches |
| `backend/api/routes/simulation.py` | Phase 7 REST API |
| `frontend/src/components/SimulationAgent.jsx` | Simulation Agent UI |
| `tests/test_phase7_simulation_agent.py` | Phase 7 unit tests |

## Modified files

| File | Change |
|---|---|
| `backend/api/main.py` | Registers the Simulation Agent router and updates API phase description |
| `frontend/src/api/client.js` | Adds experiment and hypothesis-comparison API calls |
| `frontend/src/App.jsx` | Adds the Simulation Agent tab and view |
| `frontend/src/App.css` | Adds Phase 7 result-grid and verdict styling |

## API

### Run one experiment

```http
POST /simulation/twins/{twin_id}/experiment
```

Example:

```json
{
  "name": "X12 connector recovery",
  "hypothesis": "An open X12 connector causes the motor to stop.",
  "fault_commands": [
    {
      "command": "set_signal",
      "target": "start_command",
      "value": true
    },
    {
      "command": "set_component_state",
      "target": "x12",
      "field": "connected",
      "value": false
    }
  ],
  "intervention_commands": [
    {
      "command": "set_component_state",
      "target": "x12",
      "field": "connected",
      "value": true
    }
  ],
  "assertions": [
    {
      "path": "components.motor.running",
      "expected": true
    }
  ]
}
```

A successful response contains `"verdict": "validated"` and the complete
experiment trace.

### Compare hypotheses

```http
POST /simulation/twins/{twin_id}/hypotheses
```

The body contains an `experiments` array. Each candidate is executed from the
same starting twin state, preventing one experiment from contaminating
another.

## Safety boundary

Phase 7 is deliberately scoped as **functional simulation**.

It can answer:

> "Does this intervention restore the state that our digital-twin model says
> should be restored?"

It cannot answer:

> "Is this physically safe on the real machine?"

The API therefore returns an explicit warning that simulation is virtual and
model-bounded. Real-world execution remains outside the simulation system.

## Relationship to previous phases

```text
Phase 1  Product Brain
   ↓
Phase 2  Diagnostic Agent
   ↓
Phase 3  Schematic Intelligence
   ↓
Phase 4  Technical Visualization
   ↓
Phase 5  Expert Knowledge
   ↓
Phase 6  Functional Digital Twin
   ↓
Phase 7  Simulation Agent
```

Phase 6 provides the executable machine model. Phase 7 adds the experiment
loop around that model.

## Demo flow

1. Create a Phase 6 demo digital twin.
2. Copy its twin ID.
3. Open **Simulation Agent**.
4. Enter the twin ID.
5. Run **Test X12 repair** or **Test door repair**.
6. Inspect the verdict, assertions, state changes, and traces.
7. Use **Compare hypotheses** to execute multiple candidates independently.

## Tests

Run:

```bash
pytest tests/test_phase7_simulation_agent.py
```

The tests verify:

- validated interventions
- rejected interventions
- no mutation of the original twin during experiments
- comparison of competing hypotheses

## Scope deliberately deferred

Phase 7 does not add:

- physics simulation
- CAD/mesh simulation
- thermal/fluid/mechanical solvers
- real-machine control
- autonomous physical actuation
- automatic claims of physical safety
- training a new ML model

Those belong to later phases/research once the functional diagnostic loop is
validated.

## Phase 7 result

The project now has the core executable loop:

```text
Diagnose
   ↓
Form hypothesis
   ↓
Run virtual fault
   ↓
Apply proposed intervention
   ↓
Check expected machine state
   ↓
Validate / reject
   ↓
Show evidence + trace
```

This is the bridge between the **Diagnostic Agent** and the long-term
`Diagnose → Simulate → Validate → Repair` workflow.
