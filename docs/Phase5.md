# Phase 5 — Capture Rajesh

Phase 5 turns senior-engineer tacit knowledge into **reviewable, revision-scoped,
executable diagnostic knowledge**.

## Flow

```text
Senior Engineer
      ↓
Expert Interview
      ↓
Immutable Transcript
      ↓
LLM Atomic Knowledge Extraction
      ↓
Draft Knowledge Version
      ↓
Human Technical Review
   ┌──┴─────┐
 reject   approve
             ↓
      Diagnostic Graph
             ↓
      Product Brain / Agent
```

## What is implemented

### 1. Expert registry
`Expert` stores the engineer identity and role without mixing it into the
technical knowledge itself.

### 2. AI interviewer
`POST /expert/interviews/{id}/turns` records an expert answer and returns one
targeted follow-up question. The prompt focuses on failure signatures,
disambiguating observations, revision differences, misdiagnoses, safe tests,
and repair decisions.

When no Anthropic key is configured, a deterministic question bank keeps local
development usable without pretending that an LLM generated the question.

### 3. Atomic knowledge extraction
`POST /expert/interviews/{id}/extract` converts the transcript into atomic
claims:

- rule
- failure mode
- diagnostic test
- repair
- exception
- heuristic

Every claim retains transcript turn IDs and a source quote. This makes the
knowledge auditable instead of treating the transcript as an opaque RAG blob.

### 4. Human review + versioning
Knowledge is immutable by version:

`draft → approved/rejected`

Approving a newer version marks the previous approved version for that
interview as `superseded`.

Only approved knowledge is exposed to the diagnostic agent.

### 5. Diagnostic graph generation
`POST /expert/knowledge/{version_id}/graph` transforms approved claims into
graph nodes and edges representing symptoms, hypotheses, diagnostic actions,
and expected outcomes.

Graphs are generated from reviewed knowledge; the LLM does not directly get to
invent executable diagnostic branches.

### 6. Diagnostic-agent integration
The Phase 2 diagnostic agent now includes approved expert knowledge in its
revision-scoped Product Brain context. Draft/rejected knowledge cannot affect
diagnostic recommendations.

## API

- `POST /expert/experts`
- `GET /expert/experts`
- `POST /expert/interviews`
- `GET /expert/interviews/{id}`
- `POST /expert/interviews/{id}/turns`
- `POST /expert/interviews/{id}/complete`
- `POST /expert/interviews/{id}/extract`
- `GET /expert/knowledge/{version_id}`
- `POST /expert/knowledge/{version_id}/review`
- `POST /expert/knowledge/{version_id}/graph`
- `GET /expert/graphs/{graph_id}`

## Safety / provenance rules

1. Expert claims are drafts until a reviewer approves them.
2. Every extracted claim stores its supporting interview turn IDs.
3. Every extracted claim stores an optional exact source quote.
4. Knowledge is scoped to the interview's product revision.
5. Only approved knowledge enters diagnostic-agent context.
6. Diagnostic graphs can only be generated from approved versions.
7. The system never treats an interview transcript as authoritative merely
   because an LLM extracted it.

## Demo

1. Create a product + revision.
2. Create an expert.
3. Start an interview on a recurring fault.
4. Answer 3–5 targeted questions.
5. Complete the interview.
6. Extract knowledge.
7. Review and approve the draft.
8. Generate the diagnostic graph.
9. Run a diagnostic case against the same revision and observe the approved
   expert knowledge in the Product Brain.

This gives Phase 5 a complete loop from **human expertise → structured
knowledge → review → executable troubleshooting graph → diagnostic agent**.
