# Crater.ai — Phase 9: End-to-End Diagnostic Experience

Phase 9 connects the existing Phase 8E machine-intelligence backend to a technician-facing diagnostic workstation.

## Implemented

### Backend
- Added `/diagnose/{session_id}/context` to expose product/revision, documentation, component topology, and latest machine-knowledge status.
- Preserved source-document provenance in diagnostic evidence (`source_document` + page).
- Added a persisted, event-level diagnostic timeline (no chain-of-thought).
- Extended diagnostic next-step schema for evidence references and structured procedure steps.
- Added product/revision ownership validation when starting a diagnostic.
- Extended the diagnostic prompt to request `evidence_chunk_ids` and `procedure_steps`.

### Frontend
- Added a Phase 9 **Diagnose** workspace and made it the default entry point.
- Added machine/product and revision selection.
- Added symptom entry and investigation start.
- Added typed diagnostic API functions.
- Added central session-driven UI for:
  - machine knowledge status
  - hypotheses and confidence
  - source evidence
  - machine relationships/topology
  - diagnostic timeline
  - targeted question/action interaction
  - repair/recommendation surface
  - verification state
- Added dedicated API modules:
  - `api/machines.js`
  - `api/revisions.js`
  - `api/knowledge.js`
  - `api/diagnostics.js`
  - `api/evidence.js`
  - `api/schematic.js`
- Added responsive technician-workstation styling.

## Primary demo path

```text
Select Product
  ↓
Select Revision
  ↓
Enter "Motor M1 is overheating after 20 minutes."
  ↓
Start Investigation
  ↓
Hypotheses + Evidence + Machine Relationships
  ↓
Targeted Diagnostic Question
  ↓
Technician Answer
  ↓
Updated Diagnostic State
  ↓
Evidence-grounded Recommendation
  ↓
Real simulation verification when an applicable twin/experiment exists
```

## Important integrity behavior

The UI does not fabricate citations, source locations, or PASS results. Simulation is explicitly separated from diagnosis and is only treated as verification when an actual digital-twin experiment returns a result.

## Files added

```text
frontend/src/api/machines.js
frontend/src/api/revisions.js
frontend/src/api/knowledge.js
frontend/src/api/diagnostics.js
frontend/src/api/evidence.js
frontend/src/api/schematic.js
frontend/src/components/DiagnosticWorkspace.jsx
PHASE9.md
```

## Files modified

```text
backend/api/routes/diagnostics.py
backend/diagnostics/agent.py
backend/diagnostics/prompts.py
backend/diagnostics/schema.py
frontend/src/api/client.js
frontend/src/App.jsx
frontend/src/App.css
```

## Validation

- Python backend: `compileall` passed.
- Frontend build was attempted, but the uploaded repository did not contain an installed Vite binary; dependency installation timed out in the build environment. Run `npm ci && npm run build` locally before deployment.
