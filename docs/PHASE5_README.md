# Phase 5 — Capture Rajesh

## Overview

Phase 5 adds the **Capture Rajesh** knowledge-capture pipeline to Crater.ai.

The purpose of this phase is to turn the tacit troubleshooting knowledge of a senior service engineer into **structured, reviewable, revision-scoped diagnostic knowledge** that can later be consumed by the diagnostic agent.

The Phase 5 pipeline is:

```text
Senior Engineer
      ↓
AI Expert Interviewer
      ↓
Immutable Interview Transcript
      ↓
LLM Knowledge Extraction
      ↓
Evidence / Provenance
      ↓
Versioned Knowledge Draft
      ↓
Human Expert Review
      ↓
Approved Expert Knowledge
      ↓
Diagnostic Graph
      ↓
Revision-scoped Product Brain
```

A central safety rule is enforced throughout the implementation:

> **LLM-generated expert knowledge is never made available to diagnosis until it is explicitly approved by a human reviewer.**

---

## Phase 5 Goals

Phase 5 implements five capabilities:

1. **AI expert interviewing** — ask targeted technical questions instead of storing a passive transcript.
2. **Knowledge extraction** — convert interview answers into atomic diagnostic rules and other technical knowledge.
3. **Evidence provenance** — keep the exact interview turn IDs and source quote supporting each extracted claim.
4. **Expert review and versioning** — extracted knowledge remains a draft until a reviewer approves or rejects it; later approved versions supersede earlier ones.
5. **Diagnostic graph generation** — convert approved knowledge into a structured troubleshooting graph that can represent symptom → hypothesis/action → expected observation paths.

---

# 1. Expert Interviewer

## Expert registry

Experts are represented as first-class database entities.

Stored fields include:

- Name
- Role
- Organization
- Notes
- Creation timestamp

Experts can be created and listed through the Phase 5 API.

## Interview sessions

An interview is associated with:

- Expert
- Product
- Optional product revision
- Interview topic
- Interview status
- Creation/completion timestamps

This makes captured knowledge traceable to the machine/product context in which it was learned.

## Targeted questioning

After an expert answer is recorded, the backend generates the next question from the current transcript.

The interviewer prompt focuses on questions such as:

- What observation distinguishes similar faults?
- What measurement confirms or rules out a cause?
- What is commonly misdiagnosed?
- Which model/revision differences matter?
- What is the safest next test before replacing a component?
- Under what conditions does the rule not apply?

When no LLM provider is configured, the workflow falls back to deterministic technical questions so the interview workflow remains usable in local/development environments.

---

# 2. Immutable Interview Transcript

Every interview answer is stored as an individual turn.

Each turn contains:

```text
turn id
interview id
sequence number
speaker
content
created_at
```

The `(interview_id, sequence)` pair is unique, which preserves deterministic ordering.

The transcript is intentionally retained rather than only storing the final extracted knowledge. This allows the system to:

- re-run extraction later,
- inspect the original evidence,
- audit why a rule was created,
- correct extraction without losing the source interview.

---

# 3. Structured Knowledge Extraction

Once an interview is complete, the Phase 5 extraction service sends the transcript to the LLM and requests **atomic, reviewable technical claims**.

The supported knowledge types are:

```text
rule
failure_mode
diagnostic_test
repair
exception
heuristic
```

Each extracted item can contain:

```text
Title
Symptom
Trigger
Condition
Action
Expected observation
Failure mode
Safety notes
Applicable models
Applicable revisions
Evidence turn IDs
Confidence
Source quote
```

The extraction prompt explicitly instructs the LLM to:

- extract only knowledge supported by the expert's statements,
- avoid converting speculation into fact,
- prefer actionable diagnostic rules,
- cite supporting interview turn IDs,
- preserve a short exact source quote.

Malformed or unsupported model output is not allowed to silently become executable knowledge. Invalid knowledge types are skipped, and the resulting version remains a draft for review.

---

# 4. Evidence and Provenance

Every extracted knowledge item can retain:

```text
evidence_turn_ids = [turn-id-1, turn-id-3, ...]
source_quote = "..."
```

This provides a direct chain from:

```text
Diagnostic rule
      ↓
Expert knowledge item
      ↓
Interview turn(s)
      ↓
Original expert statement
```

This is important because Phase 5 is not intended to create an opaque “AI memory.” The technical claim remains auditable against the expert's original words.

---

# 5. Knowledge Versioning

Every extraction creates a `KnowledgeVersion`.

Versions are intentionally separate from the interview itself so the same transcript can be re-extracted into a new version without overwriting history.

### Knowledge lifecycle

```text
draft
  ↓
in_review (supported by the data model)
  ↓
approved
```

Alternative terminal states are:

```text
rejected
superseded
```

The implemented review API accepts:

```json
{
  "decision": "approved",
  "reviewer": "chief-engineer",
  "notes": "Checked against service procedure."
}
```

When a newly extracted version is approved, any earlier approved version from the same interview is automatically marked `superseded`.

This prevents multiple competing approved versions from remaining active for the same knowledge lineage.

---

# 6. Human Review Gate

The Phase 5 architecture deliberately separates **extraction** from **approval**.

The sequence is:

```text
Interview
   ↓
LLM extraction
   ↓
DRAFT
   ↓
Human reviewer
   ├── APPROVE → usable by Product Brain / diagnostics
   └── REJECT  → not usable by diagnostics
```

The diagnostic graph generator also enforces this gate directly:

```text
if knowledge_version.status != approved:
    generation is rejected
```

Therefore a draft cannot accidentally become a diagnostic decision path.

---

# 7. Diagnostic Graph Generation

Approved expert knowledge can be compiled into a `DiagnosticGraph`.

The graph contains typed nodes and edges.

## Node types available to Phase 5

```text
symptom
question
observation
hypothesis
action
outcome
safety
```

## Edge types available to Phase 5

```text
asks
if_true
if_false
supports
rules_out
leads_to
requires
```

The current graph compiler uses the extracted expert knowledge to create paths such as:

```text
Symptom
   ↓
Hypothesis
   ↓
Diagnostic Action
   ↓
Expected Observation
```

For a knowledge item with a failure mode and action, Phase 5 can produce:

```text
symptom → hypothesis → action → outcome
```

For a simpler rule it can produce:

```text
symptom → action → outcome
```

Every graph node/edge retains the originating `expert_knowledge` ID where applicable, preserving provenance into the generated diagnostic structure.

---

# 8. Integration With the Diagnostic Agent

Phase 5 also modifies the diagnostic agent so that **approved senior-engineer knowledge becomes part of the revision-scoped diagnostic context**.

The diagnostic agent now loads:

```text
existing structured product knowledge
+
approved expert knowledge
```

Only `KnowledgeVersion` rows with:

```text
status = approved
revision_id = current revision
```

are loaded into the diagnostic knowledge block.

Draft, rejected, and superseded knowledge is intentionally invisible to the diagnostic agent.

The approved expert knowledge supplied to the agent includes fields such as:

```text
Title
Knowledge type
Confidence
Revision scope
Symptom
Condition
Action
Expected observation
Safety notes
```

This creates the Phase 5 bridge from tacit human expertise into the existing diagnostic reasoning loop.

---

# 9. Backend Files Added

The following files are **new Phase 5 implementation files**.

## `backend/knowledge/expert.py`

Main Phase 5 service layer.

Implements:

- expert interview creation,
- transcript handling,
- AI/fallback next-question generation,
- interview completion,
- LLM knowledge extraction,
- JSON parsing/validation,
- knowledge version creation,
- expert review,
- superseding previous approved versions,
- diagnostic graph generation.

## `backend/knowledge/expert_schema.py`

Pydantic request/response contracts for Phase 5.

Includes schemas for:

- interview creation,
- interview turns,
- extracted knowledge items,
- extraction results,
- review requests,
- knowledge version responses,
- graph nodes/edges,
- diagnostic graph responses.

## `backend/knowledge/expert_prompts.py`

LLM prompts for:

- AI expert interviewing,
- structured technical knowledge extraction.

The extraction prompt is specifically designed around evidence-backed, reviewable knowledge rather than generic summarization.

## `backend/api/routes/expert.py`

FastAPI router for the complete Capture Rajesh workflow.

Exposes expert, interview, knowledge review, and graph endpoints.

## `frontend/src/components/ExpertKnowledge.jsx`

Phase 5 frontend workflow for:

- selecting/using an expert,
- starting an interview,
- recording answers,
- viewing the transcript,
- completing the interview,
- extracting knowledge,
- approving/rejecting knowledge,
- generating the diagnostic graph.

## `tests/test_phase5_expert.py`

Focused Phase 5 tests covering:

- interview turn sequencing,
- deterministic fallback questions,
- extraction provenance,
- version creation,
- approval/supersession,
- approval-gated graph generation.

## `docs/Phase5.md`

Phase 5 implementation documentation.

---

# 10. Existing Files Modified for Phase 5

## `backend/db/models.py`

Adds the Phase 5 persistence model and enums.

### New enums

```text
InterviewStatus
KnowledgeStatus
KnowledgeType
GraphNodeType
GraphEdgeType
```

### New database models

```text
Expert
ExpertInterview
ExpertInterviewTurn
KnowledgeVersion
ExpertKnowledge
DiagnosticGraph
DiagnosticGraphNode
DiagnosticGraphEdge
```

Important constraints include:

- unique interview turn sequence per interview,
- unique knowledge version number per interview,
- foreign-key lineage between interviews, versions, knowledge items, and graphs.

## `backend/api/main.py`

Registers the new Phase 5 expert router:

```python
app.include_router(expert.router)
```

## `backend/diagnostics/agent.py`

Adds approved expert knowledge to the diagnostic context for the active revision.

The key safety property is that only approved `KnowledgeVersion` records are queried.

## `frontend/src/App.jsx`

Adds the **Capture Rajesh** application tab and renders the Phase 5 `ExpertKnowledge` component.

## `frontend/src/api/client.js`

Adds frontend API helpers for:

- expert creation/listing,
- interview creation,
- interview turns,
- interview completion,
- knowledge extraction,
- knowledge review,
- diagnostic graph generation.

## `plan.md`

Marks Phase 5 as implemented and records the Capture Rajesh pipeline as part of the project plan.

---

# 11. Phase 5 API

All endpoints are under:

```text
/expert
```

## Experts

### Create expert

```http
POST /expert/experts
```

Example body:

```json
{
  "name": "Rajesh",
  "role": "Senior Service Engineer",
  "organization": "Acme Industrial",
  "notes": "25 years field experience"
}
```

### List experts

```http
GET /expert/experts
```

---

## Interviews

### Start interview

```http
POST /expert/interviews
```

Example:

```json
{
  "expert_id": "expert-id",
  "product_id": "product-id",
  "revision_id": "revision-id",
  "topic": "spindle will not start"
}
```

### Get interview

```http
GET /expert/interviews/{interview_id}
```

### Add expert turn

```http
POST /expert/interviews/{interview_id}/turns
```

Example:

```json
{
  "speaker": "expert",
  "content": "I first check whether the safety relay is energized."
}
```

The response includes the next interviewer question.

### Complete interview

```http
POST /expert/interviews/{interview_id}/complete
```

---

## Knowledge extraction and review

### Extract knowledge

```http
POST /expert/interviews/{interview_id}/extract
```

Creates the next knowledge version in `draft` state.

### Get knowledge version

```http
GET /expert/knowledge/{version_id}
```

### Review knowledge

```http
POST /expert/knowledge/{version_id}/review
```

Example approval:

```json
{
  "decision": "approved",
  "reviewer": "chief-engineer",
  "notes": "Verified against field-service procedure."
}
```

Possible decisions:

```text
approved
rejected
```

---

## Diagnostic graphs

### Generate graph

```http
POST /expert/knowledge/{version_id}/graph
```

Only an approved knowledge version is allowed.

### Get graph

```http
GET /expert/graphs/{graph_id}
```

The response exposes the graph's nodes and edges together with source knowledge IDs.

---

# 12. Frontend Workflow

The Phase 5 UI is exposed as the **Capture Rajesh** tab.

The intended workflow is:

```text
1. Select/create an expert
2. Select product
3. Optionally select revision
4. Enter troubleshooting topic
5. Start interview
6. Answer AI-generated questions
7. Finish interview
8. Extract structured knowledge
9. Review extracted claims
10. Approve or reject
11. Generate diagnostic graph
```

The UI displays the extracted knowledge items with:

- title,
- symptom/action or failure mode,
- expected observation,
- source quote,
- current review status.

After approval, the UI exposes graph generation and reports the resulting node/edge count.

---

# 13. Safety and Trust Properties

Phase 5 deliberately uses several deterministic controls around the LLM.

### Source-grounded extraction

The extraction prompt requires each item to cite supporting transcript turns and a source quote.

### Human approval gate

A draft knowledge version cannot generate a diagnostic graph and is not loaded into the diagnostic agent.

### Revision scoping

Expert knowledge can be attached to a product revision, preventing rules from automatically leaking across incompatible hardware revisions.

### Version history

Re-extraction creates a new version instead of overwriting the previous one.

### Supersession

When a new version is approved, previous approved versions in the same interview lineage are marked `superseded`.

### Safe failure without an LLM

Without `ANTHROPIC_API_KEY`, the interviewer uses deterministic questions and extraction still creates a valid draft version. This keeps local testing possible without pretending an LLM result exists.

---

# 14. Testing

Phase 5 adds a dedicated test module:

```text
tests/test_phase5_expert.py
```

The tests use an in-memory SQLite database and monkeypatch the LLM call where deterministic model output is needed.

The Phase 5 test coverage validates:

```text
✓ interview sequencing
✓ fallback interviewer questions
✓ extraction creates a draft knowledge version
✓ evidence turn IDs are retained
✓ source confidence is retained
✓ approval supersedes previous approved version
✓ draft knowledge cannot generate a graph
✓ approved knowledge can generate graph nodes and edges
```

The Phase 5 test module contains **4 tests**, all passing in the implementation environment.

---

# 15. Example End-to-End Knowledge Capture

Suppose Rajesh says:

```text
"An open door keeps the safety relay off."
```

The extraction stage can produce a rule like:

```json
{
  "knowledge_type": "rule",
  "title": "Open door disables safety",
  "symptom": "spindle will not start",
  "condition": "door is open",
  "action": "inspect door interlock",
  "expected_observation": "safety relay remains off",
  "failure_mode": "door interlock state",
  "safety_notes": [
    "de-energize before inspection"
  ],
  "evidence_turn_ids": [
    "turn-123"
  ],
  "confidence": 0.9,
  "source_quote": "An open door keeps the safety relay off."
}
```

That item is initially:

```text
draft
```

After human review:

```text
approved
```

Only then can it be compiled into a diagnostic path such as:

```text
Spindle will not start
        ↓
Door interlock / safety relay hypothesis
        ↓
Inspect door interlock
        ↓
Safety relay remains off / returns to energized state
```

The expert knowledge item remains traceable back to `turn-123`.

---

# 16. Phase 5 Data Model

The relationships are:

```text
Expert
  │
  └── ExpertInterview
          │
          ├── ExpertInterviewTurn × N
          │
          └── KnowledgeVersion × N
                  │
                  └── ExpertKnowledge × N
                          │
                          └── DiagnosticGraph
                                  ├── DiagnosticGraphNode × N
                                  └── DiagnosticGraphEdge × N
```

The graph and knowledge records retain IDs linking the generated troubleshooting structure back to the extracted expert claim.

---

# 17. Environment

Phase 5 uses the project's existing Anthropic configuration:

```env
ANTHROPIC_API_KEY=...
ANTHROPIC_MODEL=claude-sonnet-4-6
```

The service reads these values through `backend.config.settings`.

No additional Phase 5-specific external service is required.

SQLite is sufficient for the Phase 5 workflow and test suite.

---

# 18. Phase 5 Implementation Summary

### New files

```text
backend/knowledge/expert.py
backend/knowledge/expert_prompts.py
backend/knowledge/expert_schema.py
backend/api/routes/expert.py
frontend/src/components/ExpertKnowledge.jsx
tests/test_phase5_expert.py
docs/Phase5.md
```

### Modified files

```text
backend/db/models.py
backend/api/main.py
backend/diagnostics/agent.py
frontend/src/App.jsx
frontend/src/api/client.js
plan.md
```

### Core new capability

Phase 5 changes Crater.ai from a system that primarily **reads machine documentation** into a system that can also **capture senior-engineer tacit knowledge, turn it into structured diagnostic knowledge, require human approval, version it, and feed approved knowledge into machine diagnosis**.

The resulting architecture is explicitly designed so that expert knowledge is:

```text
Captured
  → Structured
  → Evidence-backed
  → Reviewed
  → Versioned
  → Graph-compiled
  → Diagnosis-ready
```

