# Phase 6 — Functional Digital Twin

Phase 6 adds the first executable machine model to Crater.ai.

The goal is deliberately narrower than full physics simulation: represent a
specific product revision as a deterministic functional state machine with
components, signals and transitions, then allow a technician or later
simulation agent to inject a fault, change an input, step the model and inspect
the predicted machine state.

> **Safety boundary:** this phase is a virtual functional model. A successful
> simulation is not evidence that a real machine is safe to operate or that a
> physical repair will work.

## 1. What Phase 6 Adds

### Functional machine model

A digital twin contains:

- revision-scoped machine identity
- typed components
  - sensors
  - relays
  - controllers
  - actuators
  - connectors
  - power sources
  - signals
  - other
- initial component state
- input signals
- deterministic state transitions
- model version
- current machine state

### Deterministic execution

The engine evaluates transition conditions and applies effects until the
machine reaches a stable state. This makes behavior:

- repeatable
- inspectable
- testable
- explainable
- independent of an LLM

The engine records a transition trace such as:

```text
Safety relay logic: safety_relay.energized False → True
Controller enable logic: controller.enabled False → True
Motor start logic: motor.running False → True
```

### Fault injection

The model supports controlled virtual interventions:

- set a signal
- set a component field
- inject a fault
- clear a fault
- reset the machine
- explicitly step the state machine

This allows Phase 7 to use the twin as an execution environment for diagnostic
hypotheses.

### Audit trail

Every executed command stores:

- command
- state before
- state after
- transition trace
- timestamp
- event type

This creates a reproducible simulation history rather than an opaque result.

## 2. Demo Digital Twin

Phase 6 includes a small deterministic motor-drive model:

```text
Power Source
     ↓
Safety Relay
     ↓
Drive Controller
     ↓
Motor
```

with:

```text
Door Interlock ──┐
                 ├──> Safety Relay
Connector X12 ───┘
```

The machine requires:

```text
power_on = true
door_interlock.closed = true
x12.connected = true
```

before the safety relay can energize.

The controller enables after the safety relay energizes.

The motor runs when:

```text
controller.enabled = true
start_command = true
```

Opening the door or disconnecting X12 forces the safety chain to drop and the
motor to stop.

This is intentionally small. It provides a complete state-transition loop
that can be validated before attempting arbitrary industrial simulation.

## 3. Architecture

```text
                 Product / Revision
                        │
                        ▼
                DigitalTwin record
                        │
              ┌─────────┴─────────┐
              │                   │
        Definition JSON       Current State
              │                   │
              └─────────┬─────────┘
                        ▼
              DigitalTwinEngine
                        │
            ┌───────────┼───────────┐
            ▼           ▼           ▼
        Conditions   Transitions   Effects
            │           │           │
            └───────────┼───────────┘
                        ▼
                 New machine state
                        │
                        ▼
                  Execution trace
                        │
                        ▼
                 DigitalTwinEvent
```

The LLM is **not** inside the state-transition loop.

That separation is intentional:

```text
LLM
 │
 ├── proposes hypothesis
 │
 ▼
Digital Twin
 │
 ├── applies explicit virtual change
 ├── evaluates deterministic rules
 └── returns predicted outcome
```

This provides a future verification boundary for Phase 7.

## 4. Backend Implementation

### `backend/simulation/schema.py` — NEW

Defines the typed Phase 6 contract:

- `TwinComponent`
- `TwinTransition`
- `DigitalTwinDefinition`
- `TwinCommand`
- `TwinSnapshot`
- `TwinCreate`
- `TwinView`

The schemas keep the digital-twin definition independent from the database
implementation.

### `backend/simulation/engine.py` — NEW

Contains the deterministic execution engine.

Important behavior:

1. Build initial state from the model definition.
2. Evaluate transition conditions.
3. Apply effects.
4. Repeat until stable.
5. Record human-readable transition trace.
6. Expose derived machine state.
7. Support explicit commands and fault injection.

`demo_definition()` provides the complete Phase 6 demonstration machine.

### `backend/simulation/service.py` — NEW

Persistence boundary between the engine and SQLAlchemy.

Responsibilities:

- validate product/revision relationship
- create a digital twin
- create the demo twin
- execute commands
- persist state
- persist simulation events
- reconstruct snapshots

The service ensures that the executable model remains revision-scoped.

### `backend/api/routes/digital_twin.py` — NEW

Adds the Phase 6 REST API.

Endpoints:

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/digital-twins` | Create a custom twin |
| POST | `/digital-twins/demo/{product_id}/{revision_id}` | Create demo motor twin |
| GET | `/digital-twins` | List twins |
| GET | `/digital-twins/{twin_id}` | Inspect a twin |
| POST | `/digital-twins/{twin_id}/commands` | Execute a virtual command |
| GET | `/digital-twins/{twin_id}/events` | Read simulation audit history |

### `backend/db/models.py` — MODIFIED

Adds:

#### `TwinStatus`

```text
active
archived
```

#### `TwinEventType`

```text
command
transition
reset
```

#### `DigitalTwin`

Stores:

- product ID
- revision ID
- name
- description
- machine definition
- current state
- model version
- lifecycle status

#### `DigitalTwinEvent`

Stores the append-only simulation audit record.

## 5. API Command Model

Commands use the following shape:

```json
{
  "command": "set_component_state",
  "target": "x12",
  "field": "connected",
  "value": false,
  "reason": "Injected open connector fault"
}
```

Supported command types:

```text
set_signal
set_component_state
inject_fault
clear_fault
reset
step
```

Example:

```json
{
  "command": "set_signal",
  "target": "start_command",
  "value": true
}
```

The API returns the updated twin snapshot, including:

```text
state
derived
trace
warnings
```

## 6. Frontend Implementation

### `frontend/src/components/DigitalTwin.jsx` — NEW

Adds the Phase 6 interactive digital-twin console.

It supports:

- product/revision selection
- demo twin creation
- existing twin selection
- machine reset
- start command
- door open/close
- X12 disconnect/reconnect
- signal inspection
- component state inspection
- derived state inspection
- execution trace inspection
- simulation warning display

The UI makes the state-machine behavior directly observable.

### `frontend/src/App.jsx` — MODIFIED

Adds:

```text
Digital Twin
```

as a new application tab and mounts `DigitalTwin`.

### `frontend/src/api/client.js` — MODIFIED

Adds frontend API functions for:

- creating a twin
- creating the demo twin
- listing twins
- loading a twin
- executing commands
- retrieving event history

### `frontend/src/App.css` — MODIFIED

Adds Phase 6 styling for:

- setup controls
- simulation controls
- state panels
- execution trace
- responsive layout
- error display

## 7. API Registration

### `backend/api/main.py` — MODIFIED

Registers the new:

```python
digital_twin.router
```

and updates the application description to include Phase 6.

## 8. Example Diagnostic Scenario

Initial state:

```text
power_on = true
door closed = true
X12 connected = true
start command = false
```

The engine settles at:

```text
safety relay = energized
controller = enabled
motor = stopped
```

Technician applies:

```text
start command = true
```

The twin predicts:

```text
motor = running
```

Now inject:

```text
X12 connected = false
```

The twin predicts:

```text
safety relay = de-energized
controller = disabled
motor = stopped
```

Restore:

```text
X12 connected = true
```

The safety chain recovers.

This creates a minimal executable loop:

```text
Healthy machine
      ↓
Apply input
      ↓
Machine transition
      ↓
Observe output
      ↓
Inject fault
      ↓
Observe failure
      ↓
Repair virtual fault
      ↓
Observe recovery
```

## 9. Why This Is a Digital Twin MVP

This phase does **not** attempt to reproduce every physical property of a
machine.

It models the functional relationships needed to answer questions such as:

```text
"If X12 is disconnected, does the machine exhibit the observed failure?"
```

and:

```text
"If X12 is reconnected, does the functional model recover?"
```

That is the useful bridge between the diagnostic knowledge graph from Phase 5
and the simulation-agent work planned for Phase 7.

## 10. Safety Design

Phase 6 enforces a clear distinction:

```text
Simulation prediction ≠ physical validation
```

Every snapshot exposes the warning:

> Simulation is deterministic and virtual; it is not proof of physical safety
> or real-machine behavior.

The system therefore does not claim that:

- a simulated repair is physically safe
- a simulated repair will definitely work
- the model captures all physical failure modes
- the machine is safe to operate
- a simulation replaces manufacturer procedures

The model is an engineering reasoning tool.

## 11. Tests

### `tests/test_phase6_digital_twin.py` — NEW

Three tests cover:

1. **Initial machine behavior**
   - safety relay settles correctly
   - controller enables
   - motor remains stopped without start command

2. **Fault reproduction and recovery**
   - start command runs the motor
   - X12 disconnection stops the motor
   - X12 reconnection restores the safety chain

3. **Persistence and auditability**
   - twin remains scoped to the selected revision
   - state changes persist
   - before/after state is recorded
   - simulation event is persisted

Phase 6 tests:

```text
3 passed
```

Python compilation of the backend also succeeds.

The full repository test suite was not runnable in the clean environment because
pre-existing project dependencies (`anthropic` and `rank_bm25`) are not installed
there. This does not affect the Phase 6 test result.

## 12. Files Added

```text
backend/simulation/__init__.py
backend/simulation/schema.py
backend/simulation/engine.py
backend/simulation/service.py

backend/api/routes/digital_twin.py

frontend/src/components/DigitalTwin.jsx

tests/test_phase6_digital_twin.py

docs/Phase6.md
```

## 13. Files Modified

```text
backend/db/models.py
backend/api/main.py

frontend/src/App.jsx
frontend/src/api/client.js
frontend/src/App.css
```

No Phase 5 implementation was removed. Phase 6 is additive and consumes the
existing Product/Revision architecture.

## 14. Phase 5 → Phase 6 → Phase 7

The architecture now progresses as:

```text
PHASE 5
Capture Rajesh
      │
      ▼
Expert knowledge
      │
      ▼
Diagnostic graph
      │
      │
      └──────────────┐
                     ▼
PHASE 6       Functional Digital Twin
                     │
              Machine state
              Components
              Signals
              Transitions
                     │
                     ▼
PHASE 7          Simulation Agent
                     │
              ┌──────┼──────┐
              ▼      ▼      ▼
           Hypothesis Fault Intervention
              │      │      │
              └──────┼──────┘
                     ▼
              Predicted outcome
                     │
                     ▼
              Diagnostic Agent
```

Phase 6 therefore establishes the **execution substrate** required before an
AI agent can meaningfully test diagnostic hypotheses.

## 15. Deliberate Scope Boundary

Not included in Phase 6:

- full physics simulation
- FEM
- CFD
- thermal simulation
- electromagnetic simulation
- arbitrary CAD-to-twin conversion
- automatic machine reconstruction
- real sensor integration
- autonomous physical control
- simulation-based safety certification

Those require substantially different models and validation requirements.

The Phase 6 objective is:

> **Make one machine functionally executable in software before trying to
> simulate arbitrary physical reality.**
