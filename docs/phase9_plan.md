# Phase 9 --- End-to-End Diagnostic Experience

## 1. Objective

Phase 9 turns the existing Crater.ai backend intelligence into a
complete technician-facing diagnostic workflow.

The goal is to connect the Phase 8E backend to a functional frontend so
a technician can move through:

``` text
Machine Manual
    ↓
Machine Knowledge
    ↓
Technician: "Motor M1 is overheating"
    ↓
AI Investigation
    ↓
Hypotheses
    ↓
Exact Evidence + Machine Relationships
    ↓
Targeted Diagnostic Question
    ↓
Technician Answer
    ↓
Updated Hypotheses
    ↓
Recommended Repair Procedure
    ↓
Verification / Simulation
```

The focus is product integration and proof of the existing intelligence,
not another retrieval architecture.

------------------------------------------------------------------------

## 2. Phase Principle

The backend already provides most of the machine intelligence:

-   persisted facts and evidence
-   global integration
-   completeness checking
-   revision awareness
-   conflict handling
-   topology verification
-   graph-aware retrieval
-   knowledge-aware retrieval
-   diagnostic reasoning
-   task/model routing
-   provenance

Phase 9 should therefore primarily build:

``` text
Existing Backend
      ↓
Stable API Contract
      ↓
Frontend Diagnostic State
      ↓
Technician UI
      ↓
Interactive Diagnostic Workflow
      ↓
Visual Machine/Evidence Explanation
```

Do not create another retrieval system unless an actual integration gap
is discovered.

------------------------------------------------------------------------

## 3. Target User Experience

A technician should be able to:

1.  Select or upload a machine/manual.
2.  See the machine revision and knowledge status.
3.  Enter a symptom such as: `Motor M1 is overheating after 20 minutes.`
4.  Start an AI diagnostic session.
5.  See the AI investigate.
6.  See multiple hypotheses rather than one unsupported answer.
7.  See exact evidence supporting those hypotheses.
8.  See relevant components and machine relationships.
9.  Receive a targeted diagnostic question.
10. Answer the question.
11. See the hypotheses update.
12. Receive a repair procedure grounded in machine documentation.
13. Run verification/simulation when available.
14. See whether the proposed intervention succeeds.

The product should feel like a machine diagnostic workstation, not a
generic chatbot.

------------------------------------------------------------------------

# 4. End-to-End Architecture

``` text
                    CRATER.AI

        ┌───────────────────────────────┐
        │         Technician UI         │
        │ Symptom → Investigation →     │
        │ Question → Repair → Verify    │
        └───────────────┬───────────────┘
                        │
                        ▼
                 Frontend API Client
                        │
                        ▼
                 FastAPI Backend APIs
                        │
                        ▼
                  Diagnostic Session
                        │
                        ▼
                   Diagnostic Agent
                        │
        ┌───────────────┼────────────────┐
        ▼               ▼                ▼
 Knowledge-aware   Graph / topology   Evidence /
 Retrieval         relationships      provenance
        │               │                │
        └───────────────┼────────────────┘
                        ▼
                  Reasoning / LLM
                        │
                        ▼
              Hypotheses + Question
                        │
                  Technician Answer
                        │
                        ▼
              Updated Diagnostic State
                        │
                        ▼
              Repair Recommendation
                        │
                        ▼
              Verification / Simulation
```

------------------------------------------------------------------------

# 5. Workstream A --- Backend/API Integration Audit

Before modifying the frontend, inspect the existing Phase 8E APIs and
map them into a frontend contract.

### Tasks

-   Identify machine/product endpoints.
-   Identify revision endpoints.
-   Identify knowledge/model endpoints.
-   Identify diagnostic session endpoints.
-   Identify diagnostic question/answer endpoints.
-   Identify evidence/provenance endpoints.
-   Identify schematic/graph endpoints.
-   Identify existing simulation/verification endpoints.
-   Identify only the genuinely missing endpoints.
-   Reuse existing backend functionality wherever possible.

### Deliverable

Create an API map:

``` text
Frontend Action
      ↓
API Endpoint
      ↓
Backend Service
      ↓
Response Schema
```

------------------------------------------------------------------------

# 6. Workstream B --- Frontend API Client

Create a typed frontend API layer.

Suggested structure:

``` text
frontend/src/api/

├── client.ts
├── machines.ts
├── revisions.ts
├── knowledge.ts
├── diagnostics.ts
├── evidence.ts
├── schematic.ts
└── simulation.ts
```

The frontend should not scatter raw `fetch()` calls across components.

Use:

``` text
React Component
      ↓
Hook / Store
      ↓
API Client
      ↓
FastAPI
```

Handle:

-   requests
-   response parsing
-   loading
-   errors
-   typed request/response objects
-   retries where appropriate
-   authentication if applicable

------------------------------------------------------------------------

# 7. Workstream C --- Diagnostic Session State

Create one central diagnostic state model.

``` ts
DiagnosticSession {
    sessionId
    productId
    revisionId
    symptom
    status
    hypotheses[]
    evidence[]
    relationships[]
    currentQuestion
    answers[]
    recommendedActions[]
    verificationResult
}
```

Suggested statuses:

``` text
idle
initializing
investigating
question_pending
updating
recommendation_ready
verification_running
completed
failed
```

The session must remain available across all diagnostic UI components.

------------------------------------------------------------------------

# 8. Workstream D --- Machine Setup

Build the technician entry point.

``` text
Select Machine
      ↓
Select Revision
      ↓
Upload / Select Documentation
      ↓
Knowledge Status
      ↓
Machine Ready
```

Display:

-   machine name
-   product identifier
-   revision
-   documentation status
-   knowledge model status
-   integration status
-   verification status
-   important unresolved conflicts

Example:

``` text
CNC-01

Revision: R4

Knowledge Model
✓ Integrated

Evidence
✓ 143 sources

Topology
✓ Verified

Conflicts
2 unresolved

[Start Diagnosis]
```

------------------------------------------------------------------------

# 9. Workstream E --- Symptom Input

Create a focused diagnostic entry interface.

``` text
What is happening with the machine?

┌──────────────────────────────────────────────┐
│ Motor M1 is overheating after 20 minutes.   │
└──────────────────────────────────────────────┘

[ Start Investigation ]
```

Support initially:

-   free-text symptom
-   optional component selection
-   optional machine state
-   optional image/schematic input if already supported

Prioritize text symptom input for the hackathon MVP.

------------------------------------------------------------------------

# 10. Workstream F --- Diagnostic Investigation UI

The diagnostic screen is the central Phase 9 interface.

Suggested layout:

``` text
┌─────────────────────────────────────────────────────────┐
│ Machine: CNC-01          Revision: R4                  │
├───────────────────┬─────────────────────────────────────┤
│                   │ AI Investigation                    │
│ Machine Graph     │                                     │
│                   │ Symptom                             │
│ M1 ─ K3 ─ VFD     │ Motor M1 overheating                │
│ │                 │                                     │
│ Fan               │ Hypotheses                          │
│ │                 │ 1. Cooling failure                 │
│ Sensor            │ 2. Relay fault                     │
│                   │ 3. VFD overload                    │
│                   │                                     │
│                   │ Evidence                            │
│                   │ Manual p.143                        │
│                   │ Wiring p.87                         │
│                   │ Failure Mode F-17                   │
└───────────────────┴─────────────────────────────────────┘
```

Show:

-   symptom
-   active hypotheses
-   confidence/likelihood where appropriate
-   supporting evidence
-   relevant components
-   relevant relationships
-   current diagnostic question
-   recommended actions

Do not expose private chain-of-thought. Show conclusions, evidence,
relationships, and concise explanations instead.

------------------------------------------------------------------------

# 11. Workstream G --- Hypothesis Panel

Create a dedicated hypothesis component.

Example:

``` text
Cooling System Failure
────────────────────────
Status: Investigating

Supporting evidence:
✓ M1 temperature increases under load
✓ F1 is connected to M1 cooling path
✓ Manual identifies F1 failure as overheating cause

Related components:
M1 → F1 → K3

[View Evidence]
[View Machine Relationship]
```

Each hypothesis should support:

-   title
-   status
-   confidence/score where appropriate
-   supporting evidence
-   contradicting evidence
-   related entities
-   source references

Possible states:

``` text
candidate
supported
weakened
eliminated
confirmed
```

------------------------------------------------------------------------

# 12. Workstream H --- Evidence Panel

Every important diagnostic conclusion must be traceable.

Example:

``` text
Evidence

Service Manual
Page 143

Motor cooling failure can result in
temperature rise under continuous load.

[Open Source]
```

For schematic evidence:

``` text
Wiring Diagram
Page 87

M1 → K3 → F1

[View Schematic]
```

Use the backend provenance information. The frontend must not invent
source locations or citations.

------------------------------------------------------------------------

# 13. Workstream I --- Machine Relationship / Schematic Visualization

Use the existing machine topology and schematic data to visually explain
why a component matters.

Example:

``` text
        VFD-01
           │
           ▼
          K3
           │
           ▼
          M1
           │
           ▼
          F1
```

When F1 is relevant:

``` text
M1 ─────── F1
          ▲
          │
       Suspected
```

Required interactions:

-   highlight component
-   highlight connected components
-   show relationship type
-   show relevant signal/path
-   open component details
-   connect graph selection to evidence
-   connect graph selection to diagnostic hypothesis

The graph should be diagnostic-context aware, not merely decorative.

------------------------------------------------------------------------

# 14. Workstream J --- Targeted Diagnostic Question

This is a core feature.

Instead of asking for arbitrary information, the system should ask the
next useful question generated by the diagnostic backend.

Example:

``` text
AI Diagnostic Question

Is cooling fan F1 running while
motor M1 is operating?

[ YES ]    [ NO ]    [ NOT SURE ]
```

Flow:

``` text
Technician Answer
      ↓
Backend
      ↓
Update Diagnostic State
      ↓
Re-evaluate Hypotheses
      ↓
Generate Next Question / Recommendation
```

The interaction should be extremely fast.

------------------------------------------------------------------------

# 15. Workstream K --- Hypothesis Update

After every answer, show what changed.

Example:

``` text
Diagnostic Update

Cooling failure
████████████████░░  84%
↑ increased

Relay K3 failure
██████████░░░░░░░░  51%
↓ decreased

VFD overload
████░░░░░░░░░░░░░░  21%
↓ decreased
```

Also show:

``` text
New evidence added:
✓ Technician confirmed F1 is not running
```

Do not imply mathematical certainty if the backend only provides
heuristic confidence.

------------------------------------------------------------------------

# 16. Workstream L --- Repair Recommendation

Present the final recommendation as a structured procedure.

``` text
Recommended Repair

Replace cooling fan F1

Why
────────────────────────
F1 is not operating and is part of
M1's documented cooling path.

Procedure
────────────────────────
1. Power down machine.
2. Isolate F1 circuit.
3. Verify supply voltage.
4. Replace F1.
5. Restore power.
6. Run M1 under normal load.
7. Verify temperature.

Evidence
────────────────────────
Service Manual p.143
Wiring Diagram p.87
Procedure P-22
```

Clearly separate:

``` text
Diagnosis
```

from:

``` text
Recommended Procedure
```

A generated recommendation must not be presented as verified merely
because the LLM produced it.

------------------------------------------------------------------------

# 17. Workstream M --- Verification / Simulation Integration

Connect the existing simulation system when it is ready.

Target flow:

``` text
Repair Recommendation
        ↓
Create Verification Scenario
        ↓
Apply Intervention
        ↓
Run Simulation
        ↓
Observe Machine State
        ↓
PASS / FAIL
```

Example:

``` text
Virtual Verification

Intervention:
Replace F1

Simulation:
✓ M1 operating
✓ Cooling restored
✓ Temperature decreasing
✓ No secondary fault detected

Result: VERIFIED
```

If the simulation backend is not ready, build the frontend contract and
clearly label the feature as unavailable rather than simulating a fake
verification result.

------------------------------------------------------------------------

# 18. Workstream N --- Diagnostic Timeline

Add a compact investigation timeline.

``` text
10:21  Symptom reported
       ↓
10:21  M1 identified
       ↓
10:22  4 hypotheses generated
       ↓
10:22  Evidence retrieved
       ↓
10:23  Technician question asked
       ↓
10:24  F1 confirmed inactive
       ↓
10:24  Cooling hypothesis strengthened
       ↓
10:25  Repair procedure generated
```

This provides transparency without exposing chain-of-thought.

------------------------------------------------------------------------

# 19. Workstream O --- Error and Uncertainty States

Handle:

``` text
Knowledge unavailable
Revision unavailable
No diagnostic evidence
Conflicting knowledge
No matching hypothesis
Diagnostic agent failure
Schematic unavailable
Simulation unavailable
Network failure
Session expired
```

Example:

``` text
Unable to verify this diagnosis.

Reason:
The machine knowledge model contains conflicting
information about relay K3.

Review conflicts before proceeding.
```

Never silently convert backend uncertainty into a confident UI.

------------------------------------------------------------------------

# 20. Suggested Frontend Structure

Adapt this to the existing frontend rather than replacing it blindly.

``` text
frontend/src/

├── api/
│   ├── client.ts
│   ├── machines.ts
│   ├── revisions.ts
│   ├── knowledge.ts
│   ├── diagnostics.ts
│   ├── evidence.ts
│   ├── schematic.ts
│   └── simulation.ts
│
├── components/
│   ├── MachineHeader/
│   ├── MachineGraph/
│   ├── DiagnosticInput/
│   ├── DiagnosticInvestigation/
│   ├── HypothesisPanel/
│   ├── EvidencePanel/
│   ├── DiagnosticQuestion/
│   ├── DiagnosticTimeline/
│   ├── RepairProcedure/
│   ├── VerificationPanel/
│   └── SourceViewer/
│
├── pages/
│   ├── Dashboard.tsx
│   ├── MachineSetup.tsx
│   └── Diagnostic.tsx
│
├── hooks/
│   └── useDiagnosticSession.ts
│
├── stores/
│   └── diagnosticStore.ts
│
├── types/
│   ├── machine.ts
│   ├── knowledge.ts
│   └── diagnostic.ts
│
└── App.tsx
```

------------------------------------------------------------------------

# 21. State Architecture

Recommended flow:

``` text
Page
 ↓
Diagnostic Hook
 ↓
Diagnostic Store
 ↓
API Client
 ↓
FastAPI
```

Components should primarily render state. Keep diagnostic business logic
out of individual UI components.

------------------------------------------------------------------------

# 22. Backend Changes

Backend changes should be minimal and integration-driven.

Potential APIs, only if equivalent endpoints do not already exist:

``` text
POST /diagnostics/sessions
GET  /diagnostics/sessions/{id}
POST /diagnostics/sessions/{id}/answer
GET  /diagnostics/sessions/{id}/evidence
GET  /diagnostics/sessions/{id}/graph
POST /diagnostics/sessions/{id}/verify
```

Backend responsibilities:

-   retrieval
-   reasoning
-   hypothesis generation
-   evidence selection
-   question generation
-   diagnostic state updates
-   repair recommendation
-   verification

Frontend responsibilities:

-   presentation
-   interaction
-   state display
-   visualization
-   technician input

------------------------------------------------------------------------

# 23. API Contract Requirements

Responses should be structured rather than UI-specific prose.

Example diagnostic response:

``` json
{
  "session_id": "diag_001",
  "status": "question_pending",
  "symptom": "Motor M1 is overheating",
  "hypotheses": [],
  "evidence": [],
  "relationships": [],
  "question": {
    "id": "q_001",
    "text": "Is cooling fan F1 running while M1 is operating?",
    "options": ["yes", "no", "not_sure"]
  }
}
```

Hypothesis:

``` json
{
  "id": "h_001",
  "title": "Cooling failure",
  "status": "candidate",
  "confidence": 0.72,
  "supporting_evidence": [],
  "related_entities": ["M1", "F1"]
}
```

Evidence:

``` json
{
  "id": "e_001",
  "source_id": "doc_001",
  "page": 143,
  "text": "...",
  "evidence_type": "manual"
}
```

Follow the existing backend schemas where possible.

------------------------------------------------------------------------

# 24. UX Principles

## 24.1 Not a generic chatbot

Primary interface:

``` text
Machine
+
Graph
+
Hypotheses
+
Evidence
+
Questions
+
Repair
```

A chat stream may exist as a secondary explanation surface.

## 24.2 Evidence first

Important claims should have visible provenance:

``` text
Claim
 ↓
Evidence
 ↓
Source
 ↓
Machine Relationship
```

## 24.3 Show uncertainty

Prefer:

> Cooling failure is currently the leading hypothesis.

over:

> The problem is definitely F1.

## 24.4 Keep technician interaction fast

Prefer:

``` text
Click
Select
Confirm
Answer
```

over requiring long text input for every step.

------------------------------------------------------------------------

# 25. Primary Hackathon Demo Scenario

Use one carefully prepared machine scenario.

### Initial symptom

``` text
Motor M1 is overheating after 20 minutes.
```

### Investigation

``` text
M1
↓
Cooling system
↓
F1
↓
K3
↓
Relevant failure modes
↓
Manual procedures
```

### Initial hypotheses

``` text
1. Cooling fan failure
2. Relay K3 failure
3. VFD overload
4. Mechanical load issue
```

### Question

``` text
Is cooling fan F1 running?
```

### Technician

``` text
No.
```

### Update

``` text
Cooling failure ↑
Relay failure →
VFD overload ↓
```

### Repair

``` text
Inspect F1 supply.
Replace F1 if supply is present.
```

### Verification

``` text
Apply F1 replacement in virtual machine.

Temperature:
High → Normal

Result:
PASS
```

This should be the primary demo path.

------------------------------------------------------------------------

# 26. Testing Strategy

## Backend/API Tests

Test:

-   diagnostic session creation
-   symptom submission
-   hypothesis retrieval
-   question generation
-   technician answer submission
-   hypothesis update
-   evidence retrieval
-   repair recommendation
-   verification integration

## Frontend Tests

Test:

-   loading states
-   error states
-   diagnostic session state
-   hypothesis rendering
-   evidence rendering
-   question interaction
-   answer submission
-   hypothesis update
-   repair rendering
-   verification rendering

## End-to-End Test

The most important test:

``` text
Create/select machine
      ↓
Load knowledge
      ↓
Start diagnosis
      ↓
Enter symptom
      ↓
Receive hypotheses
      ↓
Receive evidence
      ↓
Answer question
      ↓
Receive updated hypotheses
      ↓
Receive repair procedure
      ↓
Run verification
```

The complete path should work through the UI without manually editing
database records.

------------------------------------------------------------------------

# 27. Acceptance Criteria

Phase 9 is complete when a technician can:

-   [ ] Select a machine.
-   [ ] Select a revision.
-   [ ] Confirm machine knowledge is available.
-   [ ] Enter a machine symptom.
-   [ ] Start a diagnostic session.
-   [ ] See diagnostic hypotheses.
-   [ ] See supporting evidence.
-   [ ] See relevant machine relationships.
-   [ ] View schematic/graph context.
-   [ ] Receive a targeted diagnostic question.
-   [ ] Answer the question.
-   [ ] See hypotheses update.
-   [ ] See newly relevant evidence.
-   [ ] Receive a structured repair recommendation.
-   [ ] View supporting procedures/evidence.
-   [ ] Start verification when available.
-   [ ] See a real PASS/FAIL verification result when simulation is
    actually available.
-   [ ] Handle uncertainty, conflicts, and errors without misleading
    output.

------------------------------------------------------------------------

# 28. Hackathon Demo Flow

Target a 2--3 minute demonstration:

``` text
1. Select machine
        ↓
2. Show machine knowledge
        ↓
3. "Motor M1 is overheating"
        ↓
4. AI investigates
        ↓
5. Show machine graph
        ↓
6. Show hypotheses
        ↓
7. Show exact manual + schematic evidence
        ↓
8. AI asks targeted question
        ↓
9. Technician answers
        ↓
10. Hypotheses update
        ↓
11. Repair procedure
        ↓
12. Virtual verification
        ↓
13. PASS
```

The product should demonstrate:

> Crater.ai does not merely retrieve information from a manual. It
> builds a machine-aware representation, reasons over machine components
> and relationships, interacts with the technician to reduce
> uncertainty, and produces an evidence-grounded repair that can
> ultimately be verified.

------------------------------------------------------------------------

# 29. What NOT to Build in Phase 9

Do not spend this phase on:

-   another vector database
-   another retrieval algorithm
-   training a new LLM
-   complex autonomous-agent frameworks
-   large-scale model training
-   advanced physics simulation
-   CAD authoring
-   generalized digital-twin infrastructure
-   additional latent-memory experiments
-   unnecessary model-routing complexity
-   large frontend features unrelated to diagnosis

The goal is to **expose and prove the intelligence already built**.

------------------------------------------------------------------------

# 30. Implementation Order

``` text
Step 1
Audit existing APIs
        ↓
Step 2
Create frontend API client
        ↓
Step 3
Create diagnostic session state
        ↓
Step 4
Machine/revision setup UI
        ↓
Step 5
Symptom input
        ↓
Step 6
Diagnostic session UI
        ↓
Step 7
Hypothesis panel
        ↓
Step 8
Evidence panel
        ↓
Step 9
Machine graph/schematic integration
        ↓
Step 10
Targeted question interaction
        ↓
Step 11
Hypothesis update
        ↓
Step 12
Repair procedure
        ↓
Step 13
Verification/simulation integration
        ↓
Step 14
Error/uncertainty states
        ↓
Step 15
End-to-end testing
        ↓
Step 16
Hackathon demo polish
```

------------------------------------------------------------------------

# 31. Final Phase Architecture

``` text
                         CRATER.AI
                  MACHINE INTELLIGENCE
                           │
                           ▼
                    Machine Documents
                           │
                           ▼
                Persisted Machine Facts
                           │
                           ▼
                 Universal Machine Model
                           │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
      Evidence          Graph           Retrieval
          │                │                │
          └────────────────┼────────────────┘
                           ▼
                   Diagnostic Agent
                           │
                    ┌──────┴──────┐
                    ▼             ▼
               Hypotheses      Questions
                    │             │
                    └──────┬──────┘
                           ▼
                    Technician
                           │
                           ▼
                    Updated State
                           │
                           ▼
                  Repair Procedure
                           │
                           ▼
                    Verification
                           │
                           ▼
                 Verified Intervention
```

------------------------------------------------------------------------

# 32. Phase 9 Deliverables

### Frontend

-   [ ] Machine dashboard
-   [ ] Machine/revision selection
-   [ ] Diagnostic interface
-   [ ] Diagnostic session state
-   [ ] Hypothesis visualization
-   [ ] Evidence viewer
-   [ ] Machine graph/schematic viewer
-   [ ] Diagnostic question UI
-   [ ] Hypothesis update UI
-   [ ] Repair procedure UI
-   [ ] Verification UI
-   [ ] Error/uncertainty states

### Integration

-   [ ] Typed API client
-   [ ] Backend/frontend API contracts
-   [ ] Diagnostic session lifecycle
-   [ ] Evidence/provenance integration
-   [ ] Knowledge model integration
-   [ ] Graph integration
-   [ ] Simulation/verification integration

### Quality

-   [ ] End-to-end diagnostic test
-   [ ] Frontend error handling
-   [ ] Loading states
-   [ ] Demo machine scenario
-   [ ] 2--3 minute hackathon demo flow
-   [ ] No unsupported claims presented as verified facts

------------------------------------------------------------------------

# 33. Definition of Done

Phase 9 is complete when:

``` text
A technician reports a machine symptom
                ↓
Crater understands the machine context
                ↓
Crater investigates multiple hypotheses
                ↓
Crater shows why those hypotheses matter
                ↓
Crater grounds them in machine evidence
                ↓
Crater asks the next useful question
                ↓
Technician answers
                ↓
Crater updates its diagnosis
                ↓
Crater produces an evidence-grounded repair
                ↓
Crater verifies the intervention when simulation is available
```

This is the Phase 9 product milestone.
