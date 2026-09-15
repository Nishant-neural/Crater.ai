# PLAN.md --- AI Technical Intelligence for Complex Physical Products

## Vision

Build an AI technical expert for complex physical products such as
industrial machinery, CNC equipment, packaging machines, HVAC systems,
EV chargers, welding equipment, pumps, compressors, and automation
equipment.

The system should not be a chatbot that merely talks to manuals. It
should build a **living, multimodal, executable technical model of a
product** and use it to understand documentation, capture tribal
knowledge, diagnose problems, visualize repairs, simulate supported
machine behavior, validate proposed interventions virtually, and learn
from real outcomes.

### Core loop

``` text
Technician reports symptom
        ↓
Identify product / model / revision
        ↓
Retrieve technical evidence
        ↓
Generate competing hypotheses
        ↓
Ask highest-value diagnostic question
        ↓
Receive observation / measurement / image
        ↓
Update hypotheses
        ↓
Recommend next diagnostic action
        ↓
Visualize relevant machine/component
        ↓
Simulate proposed intervention when possible
        ↓
Validate / reject / modify solution
        ↓
Show grounded repair procedure + animation
        ↓
Technician performs repair
        ↓
Capture real-world outcome
        ↓
Update Product Brain
```

Long-term interaction: **See → Understand → Diagnose → Simulate →
Demonstrate → Repair → Verify → Learn**.

------------------------------------------------------------------------

## 1. Initial Wedge

### Wedge A --- AI Technical Expert for Industrial Equipment Manufacturers

Sell to equipment manufacturers/OEMs rather than starting with a generic
industrial chatbot.

Potential customers:

-   CNC and machine tools
-   Packaging machinery
-   Textile machinery
-   Pumps and compressors
-   Industrial automation equipment
-   HVAC systems
-   Welding equipment
-   Electrical equipment
-   Injection molding machines
-   Agricultural machinery

The pain: support depends on a small number of experienced engineers,
while technical knowledge is fragmented across manuals, wiring diagrams,
service documentation, tickets, messages, service reports, CAD drawings,
photos/videos, and senior engineers' memory.

The product turns that fragmented information into a **Product Brain**.

### Wedge B --- Capture Rajesh

Build an AI interviewer for highly experienced engineers. The objective
is not transcript storage; it is extraction of the expert's diagnostic
mental model.

``` text
Symptom
  ↓
Possible causes
  ↓
Diagnostic test
  ↓
Observation
  ↓
Decision branch
  ↓
Next test
  ↓
Repair
  ↓
Verification
```

Expert knowledge becomes structured, searchable, versioned knowledge
that the diagnostic agent can execute.

------------------------------------------------------------------------

## 2. Product Architecture

``` text
                         PRODUCT DATA
 ┌──────────┬─────────────┬────────────┬──────────────┐
 │ Manuals  │ Schematics  │ CAD/Images │ Service Data │
 └────┬─────┴──────┬──────┴─────┬──────┴──────┬───────┘
      └─────────────┴────────────┴─────────────┘
                            ↓
                  MULTIMODAL INGESTION
                            ↓
                ┌────────────────────────┐
                │      PRODUCT BRAIN     │
                │ Components / Relations │
                │ Procedures / Failures  │
                │ Revisions / Parts      │
                │ Expert knowledge       │
                └───────────┬────────────┘
                            ↓
                    HYBRID RETRIEVAL
                            ↓
                   DIAGNOSTIC AGENT
                   /      |       \
                Chat    Camera     Voice
                   \      |       /
                    \     |      /
                     TECHNICAL UI
                          ↓
             ┌────────────┴────────────┐
             ↓                         ↓
      Visualization               Simulation
             ↓                         ↓
      Diagrams / CAD          Virtual Machine
      Animations               Functional Model
             \                         /
              └──────→ Validation ←──┘
                           ↓
                    REAL-WORLD REPAIR
                           ↓
                      OUTCOME DATA
                           ↓
                    PRODUCT KNOWLEDGE
```

------------------------------------------------------------------------

## 3. Product Brain

Represent:

### Product

-   Manufacturer
-   Product family
-   Model
-   Variants
-   Revision
-   Firmware/software version where relevant

### Components

-   Identity
-   Function
-   Location
-   Part number
-   Compatible replacements
-   Failure modes
-   Related procedures

### Connections

-   Electrical connections
-   Mechanical relationships
-   Fluid connections
-   Signals
-   Harnesses
-   Terminals
-   Connectors

### Procedures

-   Installation
-   Removal
-   Calibration
-   Maintenance
-   Troubleshooting
-   Replacement
-   Verification

### Failure knowledge

``` text
Symptom
→ Failure mode
→ Possible causes
→ Diagnostic test
→ Expected observation
→ Branch
→ Repair
→ Verification
```

### Revision awareness

``` text
Product A
├── Revision 2021
├── Revision 2022
└── Revision 2024
```

The agent must never silently apply information from the wrong revision.

------------------------------------------------------------------------

## 4. Multimodal Ingestion

Process:

-   PDFs and text
-   Tables
-   Wiring diagrams
-   Electrical schematics
-   Mechanical diagrams
-   Installation diagrams
-   CAD-derived views
-   Images
-   Parts catalogs
-   Service manuals
-   Maintenance schedules
-   Troubleshooting guides
-   Historical service tickets
-   Technician reports
-   Photos/videos

Preserve relationships between textual and visual evidence.

``` text
Manual page 47
       ↓
Electrical schematic
       ↓
Relay K3
       ↓
Terminal 14
       ↓
Motor controller X12
       ↓
Procedure: Check spindle enable
```

------------------------------------------------------------------------

## 5. Schematic Intelligence

Treat technical schematics as structured systems rather than ordinary
images.

Extract:

-   Components and symbols
-   Labels
-   Terminals
-   Wires
-   Signals
-   Connections
-   Power paths
-   Control paths
-   References to physical components

Construct a machine graph.

``` text
Power
  ↓
Contactor K3
  ↓
Drive
  ↓
Motor
```

Capabilities:

-   Trace signal paths.
-   Explain component relationships.
-   Highlight relevant schematic regions.
-   Map schematic components to physical components.
-   Identify diagnostic points.
-   Show expected vs observed states.

------------------------------------------------------------------------

## 6. Diagnostic Agent

Maintain structured state:

``` text
DiagnosticState
├── Product
├── Model
├── Revision
├── Symptoms
├── Observations
├── Measurements
├── Hypotheses
├── Eliminated hypotheses
├── Evidence
├── Current diagnostic step
├── Safety constraints
└── Confidence
```

Workflow:

1.  Identify product.
2.  Verify model/revision.
3.  Understand symptom.
4.  Retrieve evidence.
5.  Generate competing hypotheses.
6.  Identify missing information.
7.  Ask the most informative question.
8.  Update hypotheses.
9.  Recommend next diagnostic action.
10. Escalate when confidence is insufficient.

------------------------------------------------------------------------

## 7. Hybrid Retrieval

Use:

``` text
Semantic Search
      +
BM25 / Keyword Search
      +
Metadata Filters
      +
Model / Revision Filters
      +
Component Filters
      +
Knowledge Graph Traversal
      +
Reranking
```

Prioritize correct product, revision, component, failure mode,
procedure, and authoritative source.

------------------------------------------------------------------------

## 8. Evidence-Grounded Answers

High-impact recommendations should expose:

-   Recommended action
-   Why
-   Evidence
-   Source document
-   Page/section where available
-   Applicable model/revision
-   Confidence
-   Safety constraints
-   Escalation path

The system should prefer **"I don't have enough evidence"** over an
invented technical answer.

------------------------------------------------------------------------

## 9. Technical Visualization Engine

Communicate technical instructions visually using:

-   Annotated schematics
-   Component callouts
-   Wiring diagrams
-   Signal-flow diagrams
-   Exploded views
-   Installation diagrams
-   Component relationship graphs
-   Before/after states
-   Diagnostic flowcharts

``` text
AI diagnosis
    ↓
Component X12
    ↓
Find physical location
    ↓
Highlight X12
    ↓
Show related schematic path
    ↓
Show repair procedure
```

------------------------------------------------------------------------

## 10. Repair Animation System

Convert verified procedures into visual step sequences.

``` text
Machine
  ↓
Highlight access panel
  ↓
Animate panel removal
  ↓
Highlight connector X12
  ↓
Show release direction
  ↓
Show connector removal
  ↓
Show component removal
  ↓
Show replacement
  ↓
Show reconnection
  ↓
Show verification
```

Features:

-   Component highlighting
-   Directional arrows
-   Labels
-   Sequential steps
-   Safety warnings
-   Before/after states
-   Optional narration
-   Pause/play
-   Step navigation

Animations must come from verified technical structure, documentation,
CAD, or explicitly labeled approximations. Never animate an invented
repair.

------------------------------------------------------------------------

## 11. Virtual Machine / Digital Twin

Construct a simplified virtual representation of the machine.

Do **not** initially attempt full physics simulation. Start with a
**functional digital twin**.

``` text
Virtual Machine
├── Power System
├── Controller
├── Sensors
├── Relays
├── Motors
├── Actuators
├── Safety System
├── Communication
└── Mechanical Components
```

Each component has a state, for example:

``` text
Door Sensor = OPEN
Safety Relay = OFF
Drive Enable = OFF
Motor = STOPPED
```

------------------------------------------------------------------------

## 12. Functional Simulation

Model deterministic machine behavior.

``` text
Door Sensor = OPEN
        ↓
Safety PLC detects unsafe state
        ↓
Safety Relay = OFF
        ↓
Drive Enable = OFF
        ↓
Motor cannot start
```

The simulator can test documented state changes such as sensor states,
relay states, connector continuity, controller inputs, and machine
sequences.

The first simulator should be a transparent state-machine model, not an
opaque AI prediction.

------------------------------------------------------------------------

## 13. Simulation Agent

Allow the diagnostic agent to test hypotheses in the virtual
environment.

``` text
Hypothesis
    ↓
Modify virtual machine state
    ↓
Run simulation
    ↓
Observe result
    ↓
Compare result with reported symptom
    ↓
Update hypothesis confidence
```

Example:

``` text
Symptom: Spindle does not start.

Hypothesis A: Safety interlock failure.
Simulation: Door OPEN → Safety relay OFF → Spindle OFF.
Result: Matches symptom.

Hypothesis B: Contactor failure.
Simulation: Contactor OPEN → Drive loses power → Spindle OFF.
Result: Also matches symptom.

Agent: Need additional observation.
```

Simulation should reduce the hypothesis space rather than falsely claim
certainty.

------------------------------------------------------------------------

## 14. Virtual Repair Validation

When the model is sufficiently accurate, test proposed interventions.

``` text
Current state
      ↓
Proposed repair
      ↓
Apply repair virtually
      ↓
Run machine sequence
      ↓
Observe virtual result
      ↓
Does symptom disappear?
     / \
   YES  NO
   ↓     ↓
Recommend  Modify hypothesis
           ↓
       Try alternative
```

Example:

``` text
Repair candidate 1: Replace sensor
→ Simulation still fails

Repair candidate 2: Reseat X12
→ Simulation succeeds

Repair candidate 3: Replace controller
→ Simulation succeeds but is unnecessary

Agent selects Reseat X12 because it resolves the modeled failure
with the least invasive intervention.
```

Simulation results must include model-fidelity information and must
never be presented as proof that a physical repair is safe.

------------------------------------------------------------------------

## 15. Three Levels of Simulation

### Level 1 --- Procedural / Visual Simulation

-   2D/3D machine representation
-   Component states
-   Repair animations
-   Exploded views
-   Interactive diagrams

**MVP-friendly.**

### Level 2 --- Functional Simulation

Model sensors, relays, controllers, motors, safety systems, signals, and
state transitions.

**Primary simulation target for the initial product.**

### Level 3 --- Physics Simulation

Eventually model mechanical forces, electrical behavior, thermal
behavior, fluid dynamics, loads, materials, and realistic actuator
behavior.

This remains a long-term research direction.

------------------------------------------------------------------------

## 16. Camera Mode

A technician can photograph or record the machine.

The multimodal agent should:

-   Identify the machine
-   Read labels
-   Identify components and connectors
-   Locate buttons
-   Detect visible damage
-   Match physical components to documentation
-   Cross-reference the schematic
-   Highlight the relevant component

``` text
Technician photo
      ↓
Vision model
      ↓
Identify connector X12
      ↓
Product Brain
      ↓
Schematic X12
      ↓
Diagnostic state
      ↓
Next action
```

When confidence is low, request another image or escalate.

------------------------------------------------------------------------

## 17. Voice Mode

Use the same diagnostic state through voice.

``` text
Speech
  ↓
Speech Recognition
  ↓
Diagnostic Agent
  ↓
Technical Response
  ↓
Speech Synthesis
```

Support hands-free troubleshooting, reading steps, asking questions,
reporting observations, navigating procedures, and expert escalation.

------------------------------------------------------------------------

## 18. Component Explorer

Build an interactive machine interface. Selecting a component should
show:

-   Function
-   Part number
-   Connections
-   Related components
-   Failure modes
-   Maintenance
-   Repair procedures
-   Applicable revisions
-   Schematic references

The UI becomes an interactive technical map rather than a generic
chatbot.

------------------------------------------------------------------------

## 19. Maintenance Mode

Answer product-specific maintenance questions using verified
product/revision documentation.

Examples:

-   When should I replace the liner?
-   What lubrication schedule applies to Revision 2024?
-   Which filter is compatible with this model?

------------------------------------------------------------------------

## 20. Comparison Mode

Allow comparison between product versions, components, compatible parts,
procedures, and technologies. Comparisons must be grounded in relevant
documentation.

------------------------------------------------------------------------

## 21. Capture Rajesh Knowledge Pipeline

``` text
Senior Engineer
      ↓
AI Interviewer
      ↓
Targeted Questions
      ↓
Technical Knowledge Extraction
      ↓
Diagnostic Graph Generation
      ↓
Expert Review
      ↓
Product Brain
```

Extract rules of thumb, exceptions, common misdiagnoses, revision
differences, diagnostic shortcuts, measurements, failure signatures, and
repair outcomes.

All extracted knowledge should be reviewable and versioned.

------------------------------------------------------------------------

## 22. Learning From Real Service Outcomes

Every completed case can generate:

``` text
Product
+
Revision
+
Symptoms
+
Observations
+
Diagnostic path
+
Hypothesis
+
Action
+
Outcome
```

Example:

``` text
Prediction: Loose connector X12
Action: Reseat connector
Outcome: Machine recovered
Validation: Confirmed
```

This becomes proprietary technical outcome data.

------------------------------------------------------------------------

## 23. Knowledge Feedback Loop

``` text
More Cases
    ↓
More Outcomes
    ↓
More Validated Diagnoses
    ↓
Better Diagnostic Graphs
    ↓
Better Product Brain
    ↓
Better Recommendations
    ↓
More Technician Trust
    ↓
More Cases
```

The moat is accumulated technical knowledge plus validated service
outcomes, not simply access to an LLM.

------------------------------------------------------------------------

## 24. Hidden-State / Latent Memory Research Track

Keep hidden-state memory as a separate R&D track.

Research hypothesis:

> Can transformer hidden representations provide compact technical
> memory that preserves useful product knowledge better than raw text
> retrieval alone?

Compare vanilla LLM, text RAG, hybrid RAG, graph RAG,
latent/hidden-state retrieval, hybrid text + latent memory, and
persistent-memory diagnostic agents.

Measure answer accuracy, evidence accuracy, diagnostic accuracy,
hallucination rate, token usage, latency, memory size, retrieval
quality, robustness, and revision consistency.

Do not make hidden-state memory a required MVP dependency.

------------------------------------------------------------------------

## 25. Safety Architecture

Because the system can influence physical-world actions, safety is
first-class.

The system must:

-   Verify product/model/revision.
-   Ground high-impact instructions in authoritative sources.
-   Display confidence.
-   Detect insufficient evidence.
-   Respect approved procedures.
-   Surface safety warnings.
-   Require human confirmation for consequential actions.
-   Escalate uncertain cases.
-   Log recommendations and outcomes.
-   Version technical knowledge.
-   Avoid hallucinating specifications.
-   Never treat simulation as proof of physical safety.

For camera diagnosis, never invent component identity or location when
confidence is insufficient.

------------------------------------------------------------------------

## 26. MVP

Focus on one product family.

### Required MVP

1.  Product ingestion --- manuals, schematics, tables, parts, service
    documentation.
2.  Product Brain --- components, relationships, procedures, failure
    modes, revisions.
3.  Hybrid retrieval --- semantic, BM25, metadata, graph relationships,
    reranking.
4.  Diagnostic agent --- hypotheses, diagnostic questions, evidence
    retrieval, structured state, escalation.
5.  Schematic intelligence --- component extraction, connection tracing,
    visual highlighting.
6.  Technical visualization --- annotated diagrams, component
    highlighting, diagnostic flow, repair sequence.
7.  Functional virtual machine --- a small manually modeled machine with
    deterministic state transitions.
8.  Basic simulation agent --- test a limited set of hypotheses against
    the virtual model.
9.  Outcome capture --- record whether recommendations worked.

### MVP demo principle

A small, accurate virtual machine with a complete diagnostic loop is
better than an impressive but unreliable general-purpose simulator.

------------------------------------------------------------------------

## 27. Features to Delay

Delay:

-   Full industrial physics simulation
-   Automatic CAD reconstruction of arbitrary machines
-   Autonomous physical repair
-   Custom foundation model
-   Industrial sensors/hardware
-   Massive universal machine knowledge base
-   Large-scale enterprise integrations
-   Fully autonomous diagnosis
-   Perfect 3D reconstruction from a single photograph

Prove the technical intelligence loop first.

------------------------------------------------------------------------

## 28. First Demo

Use one machine with a deliberately injected fault.

``` text
Power
 ↓
Safety Relay
 ↓
Controller
 ↓
Motor
```

Inject:

``` text
Connector X12 = disconnected
```

Technician asks:

> The motor won't start. What should I check?

The system should:

1.  Identify machine.
2.  Identify revision.
3.  Retrieve schematic.
4.  Generate hypotheses.
5.  Ask a diagnostic question.
6.  Show the relevant schematic path.
7.  Highlight X12.
8.  Show the physical component.
9.  Run a virtual diagnostic.
10. Detect that disconnected X12 produces the observed symptom.
11. Test "reseat X12" in the virtual environment.
12. Simulate successful recovery.
13. Show an animated repair procedure.
14. Give an evidence-backed recommendation.
15. Record the outcome.

This single demo communicates the product thesis.

------------------------------------------------------------------------

## 29. Evaluation Benchmark

Each test case contains:

``` text
Question
Product
Model
Revision
Symptoms
Ground-truth evidence
Expected diagnostic path
Expected action
Safety constraints
Expected outcome
```

Compare vanilla LLM, basic vector RAG, hybrid RAG, graph RAG, diagnostic
agent, multimodal diagnostic agent, and simulation-augmented diagnostic
agent.

### Retrieval

-   Recall@K
-   MRR
-   Evidence precision

### Reasoning

-   Diagnostic accuracy
-   First-correct-diagnosis rate
-   Number of questions
-   Hypothesis ranking accuracy
-   Hallucination rate
-   Unsafe recommendation rate

### Simulation

-   State prediction accuracy
-   Failure reproduction accuracy
-   Successful virtual repair rate
-   False-positive repair rate
-   Simulation-to-real agreement

### System

-   Latency
-   Token usage
-   Cost
-   Memory footprint

### Business

-   Mean time to resolution
-   First-time-fix rate
-   Escalation rate
-   Service visits avoided
-   Expert interruptions
-   Technician onboarding time
-   Support cost saved

------------------------------------------------------------------------

## 30. Customer Validation

Interview industrial equipment manufacturers and service teams.

1.  Which support issues require the most experienced engineers?
2.  How often do cases escalate?
3.  How long do difficult cases take?
4.  What happens when a senior engineer is unavailable?
5.  Where does technical knowledge live?
6.  How often are manuals insufficient?
7.  How much does a field-service visit cost?
8.  How long does technician training take?
9.  Which recurring problems create the most cost?
10. What would a 20--30% reduction in support/escalations be worth?
11. Do technicians use photos/videos during support?
12. Which failures could be represented with deterministic machine
    logic?
13. Do they have CAD models or digital machine representations?
14. How valuable would virtual verification be?
15. Would they trust AI-generated repair instructions without expert
    approval?

Validate pain before building a large platform.

------------------------------------------------------------------------

## 31. Initial Customer Profile

Ideal early customer:

-   Industrial equipment manufacturer
-   Complex physical product
-   Existing installed base
-   10--100+ support/service employees
-   Recurring technical support cases
-   Small group of highly experienced engineers
-   Geographically distributed technicians/customers
-   Significant cost from escalations or service visits
-   Existing manuals and service documentation
-   Willingness to run a focused pilot

Start with:

``` text
1 company
→ 1 product family
→ 1 support workflow
→ 5–20 users
```

------------------------------------------------------------------------

## 32. Business Model

### Stage 1 --- Paid Pilot

One manufacturer, one product family, one workflow.

Potential value:

-   Faster troubleshooting
-   Fewer escalations
-   Reduced service visits
-   Faster technician onboarding

### Stage 2 --- Enterprise Subscription

Potential pricing dimensions include product families, users, case
volume, knowledge modules, integrations, deployment requirements, and
support requirements.

### Stage 3 --- Outcome-Based Pricing

Potential metrics include resolution time, first-time-fix, escalation
reduction, service visits avoided, downtime reduction, and warranty cost
reduction.

Pricing must be validated through customer discovery.

------------------------------------------------------------------------

## 33. Development Roadmap

### Phase 0 --- Validation

-   Select one product family.
-   Interview manufacturers/service teams.
-   Collect representative documentation.
-   Identify the most expensive support workflow.
-   Define success metrics.

### Phase 1 --- Product Knowledge

-   PDF ingestion
-   OCR
-   Tables
-   Diagrams
-   Components
-   Relationships
-   Revision modeling
-   Hybrid retrieval

### Phase 2 --- Diagnostic Agent

-   Diagnostic state
-   Hypothesis generation
-   Question selection
-   Evidence retrieval
-   Structured troubleshooting

### Phase 3 --- Schematic Intelligence

-   Schematic parsing
-   Component extraction
-   Connection graph
-   Signal tracing
-   Visual highlighting

### Phase 4 --- Technical Visualization

-   Interactive diagrams
-   Component explorer
-   Annotated schematics
-   Repair animations
-   Procedure visualization

### Phase 5 --- Capture Rajesh **(implemented)**

-   AI expert interviewer
-   Knowledge extraction
-   Diagnostic graph generation
-   Expert review
-   Knowledge versioning

### Phase 6 --- Functional Digital Twin

-   Model machine state
-   Sensors
-   Relays
-   Controllers
-   Actuators
-   Signals
-   State transitions

### Phase 7 --- Simulation Agent

-   Run hypotheses
-   Modify machine state
-   Simulate failure
-   Simulate intervention
-   Compare outcomes
-   Iterate solutions

### Phase 8 --- Real-World Feedback

-   Technician outcome capture
-   Validation/rejection
-   Case memory
-   Diagnostic performance tracking

### Phase 9 --- Multimodal Interface

-   Camera mode
-   Voice mode
-   Video input
-   Visual grounding

### Phase 10 --- Advanced Technical Intelligence

-   CAD-aware reasoning
-   Rich 3D machine models
-   More advanced simulation
-   Cross-revision reasoning
-   Predictive-maintenance integrations

### Phase 11 --- Physics Simulation Research

Research appropriate simulation engines for mechanical, electrical,
thermal, and fluid systems only where the business case justifies the
complexity.

------------------------------------------------------------------------

## 34. Proposed Repository Structure

``` text
project/
├── README.md
├── PLAN.md
├── docs/
│   ├── architecture.md
│   ├── product-spec.md
│   ├── simulation.md
│   ├── schematic-intelligence.md
│   ├── visualization.md
│   ├── evaluation.md
│   └── business.md
│
├── backend/
│   ├── api/
│   ├── agents/
│   │   ├── diagnostic/
│   │   ├── expert-interviewer/
│   │   └── simulation/
│   ├── retrieval/
│   ├── knowledge/
│   ├── diagnostics/
│   ├── simulation/
│   ├── multimodal/
│   └── memory/
│
├── frontend/
│   ├── components/
│   ├── pages/
│   ├── technical-viewer/
│   ├── schematic-viewer/
│   ├── machine-viewer/
│   ├── simulation/
│   ├── camera/
│   └── voice/
│
├── ingestion/
│   ├── pdf/
│   ├── tables/
│   ├── diagrams/
│   ├── schematics/
│   ├── images/
│   └── cad/
│
├── digital-twin/
│   ├── models/
│   ├── components/
│   ├── state-machine/
│   └── scenarios/
│
├── evaluation/
│   ├── datasets/
│   ├── baselines/
│   ├── metrics/
│   └── experiments/
│
└── research/
    └── latent-memory/
```

------------------------------------------------------------------------

## 35. Technology Direction

### Backend

-   Python
-   FastAPI
-   PyTorch
-   Transformers

### Retrieval

-   Qdrant
-   BM25
-   Hybrid retrieval
-   Reranking
-   Knowledge graph

### Frontend

-   React
-   Vite
-   Framer Motion
-   WebGL/Three.js where appropriate

### Agent

-   Claude Agent SDK
-   Tool-based diagnostic workflow
-   Structured state
-   Expert escalation

### Multimodal

-   Vision-language model
-   Speech recognition
-   Speech synthesis
-   Image/video processing

### Simulation

Start with a custom deterministic state-machine simulator plus 2D/3D
browser visualization. Later investigate CAD integration,
physics/simulation engines, and domain-specific digital-twin platforms.

Do not over-engineer simulation before the diagnostic workflow is
validated.

------------------------------------------------------------------------

## 36. Core Differentiation

The product should not compete by saying:

> "We have a better chatbot."

The differentiation is:

1.  **Product-aware** --- understands the actual machine.
2.  **Revision-aware** --- knows which technical configuration applies.
3.  **Multimodal** --- understands text, schematics, images, CAD, video,
    and voice.
4.  **Diagnostic** --- performs structured troubleshooting.
5.  **Visual** --- shows technicians what to inspect and how to repair
    it.
6.  **Executable** --- represents machine behavior and can test
    hypotheses in a virtual environment.
7.  **Learning** --- learns from experts, technician interactions,
    service cases, and real repair outcomes.

The moat becomes:

``` text
Product Knowledge
        +
Diagnostic Graphs
        +
Multimodal Understanding
        +
Machine Simulation
        +
Expert Knowledge
        +
Validated Service Outcomes
```

------------------------------------------------------------------------

## 37. Long-Term Vision

The ultimate system becomes a **technical intelligence layer for
physical machines**.

``` text
             PHYSICAL MACHINE
                    ↕
              DIGITAL TWIN
                    ↕
              PRODUCT BRAIN
                    ↕
             DIAGNOSTIC AGENT
                    ↕
        ┌───────────┼───────────┐
        ↓           ↓           ↓
      Voice       Camera       UI
        ↓           ↓           ↓
        └───────────┼───────────┘
                    ↓
              Technician
                    ↓
              Real Outcome
                    ↓
              Product Brain
```

Eventually, the AI should be able to understand a machine, reason about
why it is behaving incorrectly, inspect relevant evidence, test possible
explanations in a virtual model, demonstrate the correct repair, guide
the technician through it, verify the result, and learn from what
actually happened.

------------------------------------------------------------------------

## 38. Guiding Principle

> **Technical correctness over conversational fluency.**

A useful technical agent is not the one that gives the most confident
answer.

It is the one that can say:

``` text
I know this.
↓
Here is the evidence.
↓
Here is my hypothesis.
↓
Here is what I still need to know.
↓
Here is the safest next test.
↓
Here is what the machine model predicts.
↓
Here is the repair procedure.
↓
Here is how we verify it.
```

Final goal:

> **An executable technical expert for physical machines.**
