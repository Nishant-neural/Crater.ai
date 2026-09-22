# Phase 8A — Universal Machine Knowledge Foundation

## Goal

Build the foundation of Machine Knowledge RAG 2.0 by upgrading the existing extraction pipeline into a **Universal Machine Knowledge System**.

The objective is **not** to create a fixed schema for every type of machine.

Instead, the system should provide a small set of universal primitives that can represent different physical products while allowing domain-specific properties to be added when the documentation provides them.

---

# 1. Core Architecture

Current system:

```text
Documents
    ↓
Chunks
    ↓
Embedding + BM25
    ↓
Reranking
    ↓
LLM
    ↓
Phase 7 Experiments
```

Phase 8A foundation:

```text
Documents
    ↓
Multimodal Evidence
    ↓
Machine Knowledge Extraction
    ↓
Universal Machine Model
    ↓
Validation + Provenance
    ↓
Structured Machine Knowledge
```

Later Phase 8 stages will consume this model:

```text
Structured Machine Knowledge
        ↓
Knowledge Graph
        ↓
RAG 2.0
        ↓
Evidence Pack
        ↓
Machine Compiler
        ↓
Digital Twin / Simulation
```

---

# 2. Universal Machine Representation

Create:

```text
backend/knowledge/machine_model.py
```

Define the canonical representation using universal primitives:

```text
Entity
Relation
Port
Quantity
State
Event
Behavior
Constraint
Evidence
```

These are the foundation of the system.

## Example

A pump may become:

```text
Entity:
    Pump P1

Ports:
    inlet
    outlet

Quantities:
    pressure
    flow_rate

States:
    running
    stopped

Behavior:
    increases fluid pressure

Relations:
    connected_to Valve V3

Failure:
    bearing failure
```

A CNC machine, robot, turbine, electrical system, or other complex product should be represented using the same basic primitives.

---

# 3. Domain Extensions

The universal model must remain small.

Do not create hundreds of machine-specific fields in the core schema.

Instead support domain-specific extensions.

Examples:

```text
Electrical
    voltage
    current
    resistance
    frequency
    power

Mechanical
    force
    torque
    velocity
    acceleration
    mass

Fluid
    pressure
    flow
    density
    valve
    pump

Thermal
    temperature
    heat
    thermal conductivity

Control
    sensor
    actuator
    setpoint
    feedback
    controller
```

The machine model should contain only the domains actually supported by the documentation.

---

# 4. Evidence and Provenance

Create:

```text
backend/knowledge/evidence.py
```

Every extracted knowledge item must retain its evidence.

Minimum information:

```text
fact
source document
page
chunk
source type
location/region if applicable
confidence
extraction method
```

Example:

```text
Fact:
    Pump P1 connected_to Valve V3

Evidence:
    manual.pdf
    page: 42
    source: schematic
    region: diagram_3
    confidence: 0.94
```

This ensures the system does not treat unsupported LLM-generated information as machine knowledge.

---

# 5. Upgrade Machine Knowledge Extraction

Upgrade:

```text
backend/knowledge/component_extraction.py
```

The current component extractor should evolve into the main machine-knowledge extraction layer.

It should extract, when supported:

```text
Entities
Relations
Ports
Quantities
Signals
States
Events
Behaviors
Constraints
Procedures
Failure Modes
```

Important rule:

> Extract only information supported by the source material.

The extractor should be capable of returning:

```text
KNOWN
UNKNOWN
UNCERTAIN
```

rather than inventing missing information.

---

# 6. Integrate Multimodal Evidence

Upgrade the existing ingestion flow:

```text
backend/ingestion/pipeline.py
```

The pipeline should combine evidence from:

```text
Text
OCR
Tables
Schematics
Diagrams
Images
```

into the same Universal Machine Model.

For example:

```text
Manual text
      ↓
"Pump P1 supplies coolant"

Schematic
      ↓
P1 ─── V3 ─── Reservoir

Machine Model
      ↓
P1
 ├── supplies → coolant
 ├── connected_to → V3
 └── connected_to → Reservoir
```

---

# 7. Integrate Schematic Knowledge

Upgrade:

```text
backend/schematic/graph.py
```

The existing schematic graph should no longer remain isolated.

Convert schematic information into Universal Machine Model entities and relations.

Example:

```text
Schematic node
       ↓
Entity

Schematic connection
       ↓
Relation

Connector/terminal
       ↓
Port
```

This allows textual and visual evidence to describe the same machine.

---

# 8. Persist the Knowledge

Upgrade:

```text
backend/db/models.py
```

and:

```text
backend/knowledge/schema.py
```

The database should support storing the structured machine knowledge and its provenance.

Avoid creating a separate database representation for every possible machine type.

Use the Universal Machine Model with extensible properties.

---

# 9. Knowledge Validation

Create:

```text
backend/knowledge/validation.py
```

Validation should detect:

```text
Contradictory facts
Invalid relationships
Missing references
Duplicate entities
Unsupported claims
Low-confidence extraction
Inconsistent states
```

Example:

```text
Document A:
Valve V3 normally open

Document B:
Valve V3 normally closed
```

The system should preserve both pieces of evidence and mark the conflict rather than silently choosing one.

---

# 10. Expert Knowledge Integration

Upgrade:

```text
backend/knowledge/expert.py
```

Expert knowledge should eventually use the same Universal Machine Model.

For example:

```text
Manual evidence
       +
Expert evidence
       ↓
Universal Machine Knowledge
```

Expert knowledge should also retain provenance and confidence.

---

# 11. Target Foundation Architecture

After Phase 8A, the system should look like:

```text
                    DOCUMENTS
                       │
        ┌──────────────┼──────────────┐
        ↓              ↓              ↓
       Text           OCR         Schematics
        │              │              │
        └──────────────┼──────────────┘
                       ↓
               Evidence Extraction
                       ↓
             Machine Knowledge
                  Extraction
                       ↓
          ┌────────────────────────┐
          │ Universal Machine Model│
          │                        │
          │ Entity                 │
          │ Relation               │
          │ Port                   │
          │ Quantity               │
          │ State                  │
          │ Event                  │
          │ Behavior               │
          │ Constraint             │
          │ Evidence               │
          └────────────────────────┘
                       ↓
                Validation
                       ↓
             Structured Machine
                  Knowledge
```

---

# 12. Files to Modify

### Existing files

```text
backend/knowledge/schema.py
backend/knowledge/component_extraction.py
backend/knowledge/expert.py

backend/ingestion/pipeline.py

backend/schematic/graph.py

backend/db/models.py
```

### New files

```text
backend/knowledge/machine_model.py
backend/knowledge/evidence.py
backend/knowledge/validation.py
```

---

# 13. Implementation Order

Implement in this exact order:

### Step 1

Create the Universal Machine Model.

```text
machine_model.py
```

### Step 2

Create the Evidence/Provenance model.

```text
evidence.py
```

### Step 3

Upgrade the existing extraction system.

```text
component_extraction.py
```

### Step 4

Connect text/OCR/schematic extraction.

```text
ingestion/pipeline.py
schematic/graph.py
```

### Step 5

Update persistence.

```text
schema.py
db/models.py
```

### Step 6

Add validation.

```text
validation.py
```

### Step 7

Connect expert knowledge to the same representation.

```text
expert.py
```

### Step 8

Run end-to-end tests using existing Phase 7 machine/document examples.

---

# 14. Definition of Done

Phase 8A is complete when:

```text
PDF/manual
   ↓
Text + OCR + schematic evidence
   ↓
Machine Knowledge Extraction
   ↓
Universal Machine Model
   ↓
Evidence attached to every important fact
   ↓
Validation
   ↓
Persistent structured machine knowledge
```

And the system can represent different machine types without changing the core model.

The output should be usable by the next Phase 8 layers:

```text
Universal Machine Knowledge
        ↓
Knowledge Graph
        ↓
RAG 2.0
        ↓
Machine Compiler
        ↓
Simulation
```

## Core Principle

**Do not build a universal database of every possible machine concept.**

Build a **universal representation language for machines** that can be extended with domain-specific knowledge.

This foundation is what allows the rest of Machine Knowledge RAG 2.0 to scale to increasingly complex physical systems.
