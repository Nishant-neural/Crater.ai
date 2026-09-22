# Phase 8 — Machine Intelligence + Simulation Compiler

## Objective

Upgrade Crater from a document-grounded diagnostic system into a machine-understanding and simulation-generation framework.

> **Do not make the LLM directly generate a simulation. Make it compile documentation into an intermediate Machine Model, and compile that model into an executable simulation.**

## Target Pipeline

```text
Machine Documents
        ↓
Multimodal Machine Understanding
        ↓
Machine Knowledge Model
        ↓
Machine Knowledge Graph
        ↓
Executable Machine Model
        ↓
Simulation Compiler
        ↓
Executable Digital Twin
        ↓
Visual Simulation
        ↓
Fault / Intervention Experiment
        ↓
Verification
```

## 1. Machine Understanding / RAG 2.0

Replace the current PDF → chunks → embedding/BM25 → reranker → LLM flow with:

```text
Documents
 ↓
Multimodal Ingestion
 ↓
Evidence Extraction
 ↓
Machine Knowledge Model
 ├── Components
 ├── Connections
 ├── Signals
 ├── States
 ├── Procedures
 ├── Failure Modes
 ├── Constraints
 ├── Sensors
 ├── Actuators
 └── Behavioral Rules
 ↓
Knowledge Graph
 ↓
Hybrid Retrieval
 ├── Semantic
 ├── BM25
 ├── Graph
 ├── Component
 ├── Procedure
 ├── Failure
 └── Visual Evidence
 ↓
Evidence Pack
 ↓
Agent
```

Create:

```text
backend/knowledge/
├── machine_model.py
├── machine_extractor.py
├── relationship_extractor.py
├── behavior_extractor.py
├── failure_extractor.py
├── constraint_extractor.py
├── knowledge_graph.py
├── evidence.py
└── validation.py
```

Every extracted fact must preserve `source_document`, `page`, `chunk`, `source_type`, `confidence`, and `revision`.

## 2. Machine Knowledge Graph

Represent machine structure and behavior as a semantic graph.

Example:

```text
Power Supply → Safety Relay → Controller
                              ├→ Sensor
                              └→ Motor
```

Relationships should include meanings such as `connected_to`, `carries`, `located_at`, `failure_mode`, and `tested_by`.

Create:

```text
backend/graph/
├── schema.py
├── builder.py
├── traversal.py
├── queries.py
└── validator.py
```

Capabilities:

- Build graph from extracted knowledge
- Query component relationships
- Traverse dependencies
- Trace signals and failures
- Find affected components
- Validate graph consistency
- Provide graph context to the agent

## 3. Upgrade Retrieval

Create:

```text
backend/retrieval/
├── graph_retriever.py
├── component_retriever.py
├── failure_retriever.py
├── procedure_retriever.py
├── visual_retriever.py
└── evidence_builder.py
```

Pipeline:

```text
Query → Intent → Entity/Component Identification
     → Semantic + BM25 + Graph + Component + Failure + Procedure + Visual Retrieval
     → Fusion/RRF → Reranking → Evidence Pack
```

The system should retrieve machine context rather than only text chunks.

## 4. Canonical Machine Model

Represent:

```text
Machine
 ├── metadata
 ├── components
 ├── connections
 ├── signals
 ├── states
 ├── behaviors
 ├── failure_modes
 ├── procedures
 ├── constraints
 ├── sensors
 ├── actuators
 └── evidence
```

This becomes the intermediate representation shared by the knowledge and simulation systems.

## 5. Machine Compiler

Create:

```text
backend/compiler/
├── schema.py
├── machine_compiler.py
├── behavior_compiler.py
├── simulation_compiler.py
├── validation.py
└── compiler_prompts.py
```

Pipeline:

```text
Knowledge Graph
 ↓
Machine Model
 ↓
Behavior Extraction
 ↓
Executable Rules
 ↓
Simulation Model
```

Example rule:

```json
{
  "when": {
    "door.closed": true,
    "power.on": true
  },
  "then": {
    "safety_relay.energized": true
  }
}
```

The compiler converts documentation-derived rules into executable simulation behavior.

## 6. General Simulation Runtime

Keep the Phase 7 simulation engine as the foundation and extend it with:

```text
backend/simulation/
├── runtime.py
├── physics.py
├── mechanics.py
├── electrical.py
├── sensors.py
├── actuators.py
├── events.py
├── faults.py
├── compiler.py
└── schemas.py
```

Initial behavior domains:

- Electrical
- Mechanical
- Thermal
- Fluid
- Control Logic
- Sensors
- Actuators
- Events
- Faults

Prioritize functional engineering simulation over expensive high-fidelity physics.

## 7. Simulation Runtime

Support:

- Machine initialization
- State transitions
- Component interactions
- Signal propagation
- Event processing
- Sensor updates
- Actuator behavior
- Fault injection
- Intervention execution
- Simulation traces
- Assertions
- Experiment comparison

Runtime flow:

```text
Machine State
 ↓
Apply Event
 ↓
Update Components
 ↓
Propagate Signals
 ↓
Update Sensors
 ↓
Evaluate Constraints
 ↓
Record State
```

## 8. AI → Simulation Generation

Add:

```text
POST /simulation/generate
```

Input:

```text
product_id
revision_id
```

Pipeline:

```text
Product → Knowledge Graph → Machine Model → Behavior Extraction
→ Simulation Compilation → Validation → Executable Digital Twin
```

Return:

```text
MachineModel
SimulationModel
ValidationReport
VisualizationModel
```

## 9. Visual Simulation

Transform the current JSON/state-focused simulation into an interactive visual machine scene showing:

- Component states
- Active connections
- Signal flow
- Fault locations
- Sensor values
- Actuator states
- State transitions
- Intervention effects
- Before/after state

## 10. Frontend Simulation Architecture

Create:

```text
frontend/src/simulation/
├── MachineScene.jsx
├── MachineRenderer.jsx
├── ComponentNode.jsx
├── ConnectionRenderer.jsx
├── SignalFlow.jsx
├── SimulationControls.jsx
├── FaultInjector.jsx
├── StateInspector.jsx
├── SimulationTimeline.jsx
└── BeforeAfter.jsx
```

The UI should support start, pause, reset, step, fault injection, state inspection, timeline playback, and before/after comparison.

## 11. Parameterized 3D Representation

Do not initially generate arbitrary Blender models with the LLM.

Use:

```text
Machine Model
 ↓
Component Geometry Metadata
 ↓
Parameterized Components
 ↓
Scene Graph
 ↓
3D Renderer
```

Example:

```json
{
  "component": "motor",
  "geometry": "motor_basic",
  "position": [2, 0, 1],
  "connections": ["drive"]
}
```

The AI assembles the machine from reusable component primitives.

## 12. Simulation Verification Loop

Connect Phase 8 to the Phase 7 hypothesis engine:

```text
AI Diagnosis
 ↓
Proposed Repair
 ↓
Simulation
 ↓
Expected State?
 ├── YES → Validate
 └── NO  → Reject → Generate Alternative → Simulate Again
```

The agent should form hypotheses, select relevant components, create experiments, apply interventions, run simulations, compare expected vs observed states, explain failures, and try alternatives when appropriate.

## 13. Safety Boundary

Simulation evidence is **not physical safety proof**.

Expose:

- Assumptions
- Source evidence
- Unsupported parameters
- Confidence
- Validation status
- Limitations

Never claim a repair is physically safe solely because the simulation passed.

## 14. Testing

Add:

```text
tests/
├── test_machine_extraction.py
├── test_knowledge_graph.py
├── test_graph_retrieval.py
├── test_machine_compiler.py
├── test_simulation_compiler.py
├── test_simulation_runtime.py
└── test_phase8_integration.py
```

Test extraction, graph construction, provenance, retrieval, machine-model validation, compiler output, simulation execution, fault injection, intervention behavior, state assertions, verification, and the end-to-end document → simulation pipeline.

## 15. Target Architecture

```text
                 TECHNICAL DOCUMENTS
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
        TEXT          SCHEMATIC       IMAGE
          │              │              │
          └──────────────┼──────────────┘
                         ▼
              MACHINE UNDERSTANDING
                         │
                         ▼
                KNOWLEDGE GRAPH
                         │
                         ▼
                  MACHINE MODEL
                         │
             ┌───────────┴───────────┐
             ▼                       ▼
       DIAGNOSTIC AGENT       SIMULATION COMPILER
                                     │
                                     ▼
                              EXECUTABLE TWIN
                                     │
                         ┌───────────┼───────────┐
                         ▼           ▼           ▼
                     Electrical  Mechanical   Control
                         │           │           │
                         └───────────┼───────────┘
                                     ▼
                            VISUAL SIMULATION
                                     │
                                     ▼
                            FAULT / REPAIR TEST
                                     │
                                     ▼
                              VALIDATE / REJECT
```

## 16. Phase 8 Deliverables

By the end of Phase 8, Crater should be able to:

- Ingest machine documentation
- Understand machine components and relationships
- Construct a machine knowledge graph
- Retrieve structured machine evidence
- Construct a canonical machine model
- Compile machine behavior into simulation rules
- Generate an executable digital twin
- Render the machine visually
- Animate machine states and signals
- Inject faults
- Apply AI-generated interventions
- Run experiments
- Compare expected and observed states
- Validate or reject hypotheses
- Preserve evidence and provenance throughout the process

## 17. Product Transformation

### Before Phase 8

```text
AI + RAG
   ↓
Answer
   ↓
Simulation Check
```

### After Phase 8

```text
Machine Documentation
        ↓
Machine Understanding
        ↓
Machine Knowledge Graph
        ↓
Executable Machine Model
        ↓
Generated Digital Twin
        ↓
Visual Simulation
        ↓
AI Diagnosis
        ↓
AI Repair
        ↓
Simulation Verification
```

### Core Product Concept

> **Crater doesn't just retrieve information about a machine. It builds an executable representation of the machine and uses that representation to test AI-generated reasoning.**
