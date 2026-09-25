# Crater.ai — Machine Intelligence for Complex Physical Products

Crater.ai is an AI technical-intelligence system for diagnosing and troubleshooting complex physical machines from their manuals, schematics, procedures, expert knowledge, and machine models.

The project has evolved from a document RAG system into a **machine-aware diagnostic platform**:

```text
Machine Documentation
        ↓
Multimodal Ingestion
        ↓
Persisted Machine Facts
        ↓
Global Machine Knowledge Model
        ↓
Hybrid + Graph-Aware Retrieval
        ↓
Diagnostic Agent
        ↓
Hypotheses + Evidence
        ↓
Technician Question
        ↓
Technician Answer
        ↓
Updated Diagnosis
        ↓
Repair Procedure
        ↓
Digital-Twin Verification
```

The latest completed milestone is **Phase 9: End-to-End Diagnostic Experience**.

---

# Current Project Status

| Phase | Capability | Status |
|---|---|---|
| Phase 1 | Product Knowledge / document ingestion | ✅ Implemented |
| Phase 2 | Diagnostic Agent | ✅ Implemented |
| Phase 3 | Schematic Intelligence | ✅ Implemented |
| Phase 4 | Technical Visualization | ✅ Implemented |
| Phase 5 | Expert Knowledge / "Capture Rajesh" | ✅ Implemented |
| Phase 6 | Functional Digital Twin | ✅ Implemented |
| Phase 7 | Simulation Agent | ✅ Implemented |
| Phase 8A | Machine Knowledge foundation | ✅ Implemented |
| Phase 8B | Global Machine Knowledge Integration | ✅ Implemented |
| Phase 8C | Knowledge integrity + verification hardening | ✅ Implemented |
| Phase 8D | Graph retrieval + revision inheritance | ✅ Implemented |
| Phase 8E | Structural retrieval + diagnostics + model routing | ✅ Implemented |
| Phase 9 | End-to-End Diagnostic Workstation | ✅ Implemented |

Phase 9 connects the existing machine-intelligence backend to a technician-facing workflow:

```text
Select Machine
      ↓
Select Revision
      ↓
Enter Symptom
      ↓
AI Investigation
      ↓
Hypotheses
      ↓
Evidence + Machine Relationships
      ↓
Targeted Diagnostic Question
      ↓
Technician Answer
      ↓
Updated Diagnostic State
      ↓
Evidence-Grounded Repair
      ↓
Simulation Verification
```

The Phase 9 UI does not expose private chain-of-thought and does not fabricate citations or simulation results.

---


## Backend

- Python 3.11+
- uv
- FastAPI
- SQLAlchemy
- SQLite for local development
- Qdrant
- PyMuPDF
- pdfplumber
- Tesseract OCR
- Pillow
- BM25
- sentence-transformers
- Anthropic API
- Google Gemini API
- Pydantic
- Alembic
- Pytest

## Frontend

- React 19
- Vite
- JavaScript
- CSS

## AI Architecture

- Hybrid retrieval
- Semantic retrieval
- BM25 retrieval
- Reranking
- Machine knowledge integration
- Graph-aware retrieval
- Vision-based schematic extraction
- Structured diagnostic reasoning
- Task-specific model routing
- Functional digital twins
- Isolated simulation experiments

---

# Repository Structure

```text
crater-ai/
│
├── backend/
│   ├── api/
│   │   └── routes/
│   ├── db/
│   ├── diagnostics/
│   ├── digital_twin/
│   ├── expert/
│   ├── ingestion/
│   ├── knowledge/
│   ├── llm/
│   ├── retrieval/
│   ├── schematic/
│   ├── simulation/
│   └── visualization/
│
├── frontend/
│   └── src/
│       ├── api/
│       ├── components/
│       ├── technical-viewer/
│       ├── App.jsx
│       └── App.css
│
├── docs/
│   ├── Phase1.md
│   ├── Phase4.md
│   ├── Phase5.md
│   ├── Phase6.md
│   ├── Phase7.md
│   ├── Phase8.md
│   ├── Phase8B.md
│   ├── Phase8D.md
│   ├── Phase8E.md
│   └── ...
│
├── tests/
├── scripts/
├── data/
│   └── uploads/
│
├── plan.md
├── PHASE9.md
├── pyproject.toml
├── .env.example
└── README.md
```

---

# Installation

## Prerequisites

Install:

- Git
- Python 3.11+
- Node.js 18+
- npm
- Docker
- Tesseract OCR

You also need an API key for at least one configured LLM provider.

Supported providers currently include:

- Anthropic
- Google Gemini

Qdrant is required for the vector retrieval layer.

---

# 1. Clone the GitHub Repository

```bash
git clone https://github.com/Nishant-neural/crater-ai.git
cd crater-ai
```

If the repository has been renamed or moved, use the current repository URL from GitHub.

---

# 2. Create a Python Environment

From the repository root:

```bash
uv venv
```

### Windows

```powershell
.venv\Scripts\activate
```

### Linux / macOS

```bash
source .venv/bin/activate
```

---

# 3. Install Backend Dependencies

From the repository root:

```bash
uv sync --extra dev
```

This installs the backend and development/test dependencies defined in `pyproject.toml`.

---

# 4. Install Tesseract OCR

Tesseract is used as the OCR fallback for scanned technical documents.

### Windows

Install Tesseract and make sure it is available on `PATH`.

If it is not on PATH, set:

```env
TESSERACT_CMD=C:\path\to\tesseract.exe
```

### Ubuntu / Debian

```bash
sudo apt update
sudo apt install tesseract-ocr
```

### macOS

```bash
brew install tesseract
```

Verify:

```bash
tesseract --version
```

---

# 5. Start Qdrant

The easiest local setup is Docker:

```bash
docker run -p 6333:6333 qdrant/qdrant
```

Qdrant should now be available at:

```text
http://localhost:6333
```

The default configuration is:

```env
QDRANT_URL=http://localhost:6333
QDRANT_COLLECTION=crater_chunks
```

---

# 6. Configure Environment Variables

Copy the example environment file:

```bash
cp .env.example .env
```

On Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

At minimum, configure an LLM provider.

## Anthropic

```env
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=your_api_key
ANTHROPIC_MODEL=claude-sonnet-4-6
```

## Gemini

```env
LLM_PROVIDER=gemini
GEMINI_API_KEY=your_api_key
GEMINI_MODEL=gemini-2.5-flash
```

Do not commit `.env` or API keys to GitHub.

---

# 7. Optional Task-Specific Model Routing

Phase 8E supports task-specific routing.

For example:

```env
LLM_PROVIDER=gemini

EXTRACTION_PROVIDER=gemini
EXTRACTION_MODEL=

INTEGRATION_PROVIDER=gemini
INTEGRATION_MODEL=

DIAGNOSIS_PROVIDER=gemini
DIAGNOSIS_MODEL=

RERANK_PROVIDER=gemini
RERANK_MODEL=

VISION_PROVIDER=gemini
VISION_MODEL=

EXPERT_PROVIDER=gemini
EXPERT_MODEL=
```

If a task-specific provider/model is blank, the gateway falls back to the global provider/model.

The task categories are:

```text
extraction
integration
diagnosis
rerank
vision
expert
```

---

# 8. Start the Backend

From the repository root:

```bash
uvicorn backend.api.main:app --reload
```

The API will be available at:

```text
http://localhost:8000
```

Check the health endpoint:

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{
  "status": "ok"
}
```

FastAPI documentation is available at:

```text
http://localhost:8000/docs
```

---

# 9. Install Frontend Dependencies

Open another terminal:

```bash
cd frontend
npm install
```

Create the frontend environment file if needed:

```bash
cp .env.example .env
```

Then start Vite:

```bash
npm run dev
```

The frontend will normally be available at:

```text
http://localhost:5173
```

The frontend communicates with the FastAPI backend.

---

# Running the Full Project

You should have three processes running:

### Terminal 1 — Qdrant

```bash
docker run -p 6333:6333 qdrant/qdrant
```

### Terminal 2 — Backend

```bash
uvicorn backend.api.main:app --reload
```

### Terminal 3 — Frontend

```bash
cd frontend
npm run dev
```

Then open the Vite URL shown in the terminal.

---

# Ingesting a Machine Manual

The project includes document ingestion through the backend ingestion APIs and CLI tooling.

A typical ingestion flow is:

```text
PDF
 ↓
Text / Table / Image extraction
 ↓
OCR where required
 ↓
Structure-aware chunking
 ↓
Component / procedure / failure extraction
 ↓
Schematic extraction
 ↓
Embeddings + BM25 indexing
 ↓
Persisted machine knowledge
```

The CLI ingestion command is:

```bash
python scripts/ingest_docs.py path/to/manual.pdf \
    --manufacturer "Acme" \
    --family "CNC" \
    --model "CNC-500X" \
    --revision "Rev C" \
    --doc-type manual \
    --title "CNC-500X Service Manual"
```

After ingestion, use the machine/product and revision APIs to inspect the created records.

---

# Starting a Diagnostic Session Through the API

The main Phase 9 experience is available through the frontend, but the diagnostic APIs can also be tested directly.

Start a session:

```bash
curl -X POST http://localhost:8000/diagnose/start \
  -H "Content-Type: application/json" \
  -d '{
    "product_id": "...",
    "revision_id": "...",
    "symptom": "Motor M1 is overheating after 20 minutes."
  }'
```

Retrieve a session:

```bash
curl http://localhost:8000/diagnose/<session_id>
```

Submit a technician observation:

```bash
curl -X POST http://localhost:8000/diagnose/<session_id>/respond \
  -H "Content-Type: application/json" \
  -d '{
    "input": "The cooling fan is not running.",
    "input_kind": "observation"
  }'
```

Phase 9 also exposes diagnostic context for the frontend:

```text
GET /diagnose/{session_id}/context
```

---

# Machine Knowledge APIs

Global machine knowledge can be integrated for a revision:

```text
POST /knowledge/revisions/{revision_id}/integrate
```

Retrieve the latest canonical machine model:

```text
GET /knowledge/revisions/{revision_id}/model
```

Phase 8D also provides completeness/integrity and graph-expanded retrieval capabilities.

---

# Schematic APIs

Inspect a schematic graph:

```bash
curl http://localhost:8000/schematics/<document_id>/graph
```

Trace a path:

```bash
curl "http://localhost:8000/schematics/<document_id>/trace?from_label=Power&to_label=Motor"
```

Generate a highlighted schematic:

```bash
curl "http://localhost:8000/schematics/<document_id>/highlight?label=K17" \
  --output k17_highlighted.png
```

---

# Digital Twin / Simulation APIs

Phase 6 provides the deterministic digital twin.

Phase 7 provides isolated simulation experiments for:

- fault reproduction
- interventions
- expected-state assertions
- state diffs
- transition traces
- hypothesis comparison

The simulation system does not mutate the persisted twin during an experiment.

Use the frontend Simulation Agent interface or inspect the simulation routes in:

```text
backend/api/routes/simulation.py
```

---

# Testing

Run the complete test suite:

```bash
pytest
```

For faster development, run targeted tests:

```bash
pytest tests/test_diagnostics.py
pytest tests/test_schematic_graph.py
pytest tests/test_phase7_simulation_agent.py
pytest tests/test_phase8e_routing.py
pytest tests/test_phase8a.py
```

The repository contains tests covering:

- ingestion/chunking
- diagnostics
- schematic graph extraction
- visualization
- expert knowledge
- digital twins
- simulation
- machine knowledge integration
- retrieval
- knowledge integrity
- model routing

---

# Frontend Production Build

From `frontend/`:

```bash
npm run build
```

Preview the production build:

```bash
npm run preview
```

Lint:

```bash
npm run lint
```

---

# Important Configuration

Default local configuration:

```env
DATABASE_URL=sqlite:///./crater.db

QDRANT_URL=http://localhost:6333
QDRANT_COLLECTION=crater_chunks

EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2

TESSERACT_CMD=tesseract
```

SQLite is intended for local development. A production deployment should use a production database configuration.

---

# API Surface

Major backend areas include:

```text
/products
/ingestion
/query
/diagnose
/schematics
/visualization
/expert
/digital-twin
/simulation
/knowledge
```

The exact endpoint contracts are defined by the FastAPI routes and Pydantic schemas in the repository.

---

# Current Limitations

Crater is a research/hackathon-stage machine-intelligence system rather than a production industrial safety system.

Important limitations include:

### Retrieval

- BM25 is currently rebuilt from SQL on query.
- Local sentence-transformer models require model availability/download.
- Retrieval quality depends on the quality of the source documentation.

### Knowledge integration

- Knowledge extraction is still largely chunk-local before global integration.
- Some cross-document/entity naming mismatches can remain.
- Canonical integration preserves unresolved/conflicting knowledge, but the system still needs evaluation on large real-world manuals.

### Schematics

- Schematic extraction currently relies on vision-LLM interpretation.
- Bounding boxes are model estimates, not CAD-grade geometry.
- Cross-page schematic tracing is not yet a complete electrical/CAD graph system.
- Label matching can still be stricter than real-world naming variations.

### Diagnostics

- Diagnostic confidence is evidence-support confidence, not a calibrated probability.
- Diagnostic sessions are not yet a fully multi-user concurrent production system.
- Safety-sensitive decisions should remain under qualified human supervision.

### Simulation

- Phase 6/7 simulation is a deterministic functional model.
- It is not a full physics simulator.
- A simulation PASS only means the intervention satisfied the assertions encoded in the digital twin.
- It does not prove that a real physical repair is safe or correct.

### Infrastructure

- Authentication/authorization is not yet implemented as a complete production security layer.
- Background job processing for large document ingestion is still a future scaling requirement.
- Production deployment requires additional observability, security, persistence, and concurrency hardening.

---

# What Phase 9 Demonstrates

The important product transition is:

```text
BEFORE

PDF
 ↓
RAG
 ↓
Answer
```

to:

```text
NOW

Machine Documentation
 ↓
Machine Knowledge
 ↓
Machine Relationships
 ↓
Evidence Retrieval
 ↓
Diagnostic Reasoning
 ↓
Multiple Hypotheses
 ↓
Technician Interaction
 ↓
Hypothesis Update
 ↓
Repair Procedure
 ↓
Digital-Twin Verification
```

Crater is therefore intended to behave more like a **machine diagnostic workstation** than a generic chatbot.

---

# Recommended Hackathon Demo

Use one prepared machine scenario.

### 1. Select the machine

Show:

```text
Machine: CNC-01
Revision: R4
Knowledge: Integrated
Evidence: Available
Topology: Available
```

### 2. Enter the symptom

```text
Motor M1 is overheating after 20 minutes.
```

### 3. Show investigation

Display:

```text
Cooling failure
Relay K3 failure
VFD overload
Mechanical load issue
```

### 4. Show evidence

Open the relevant:

- manual page
- schematic
- component relationship
- failure mode/procedure

### 5. Ask the technician

```text
Is cooling fan F1 running while M1 is operating?
```

### 6. Answer

```text
No.
```

### 7. Show updated diagnosis

The cooling hypothesis becomes more supported while competing hypotheses may be weakened.

### 8. Show repair

Display the evidence-grounded repair procedure.

### 9. Verify

Run the applicable digital-twin experiment.

Only display `PASS` if the simulation actually returns a successful result.

---

# Development Philosophy

Crater intentionally separates responsibilities:

```text
Backend
├── Retrieval
├── Knowledge
├── Graph
├── Diagnostics
├── Simulation
└── Verification

Frontend
├── Technician interaction
├── Evidence visualization
├── Machine visualization
├── Diagnostic state
└── Verification display
```

The frontend should not invent machine facts.

The diagnostic agent should not invent evidence.

The simulation layer should not fabricate verification.

The machine knowledge layer should preserve provenance and unresolved conflicts.

---

# Roadmap

The next improvements should focus on making the existing machine-intelligence loop more reliable rather than adding unrelated AI features.

Potential next areas include:

1. Better evaluation datasets for diagnosis.
2. Calibrated diagnostic confidence.
3. Cross-page schematic graph merging.
4. Better entity resolution between text and schematics.
5. Larger and more realistic digital-twin models.
6. Production background ingestion.
7. Authentication and authorization.
8. Observability and latency/cost telemetry.
9. Multi-user diagnostic sessions.
10. Camera/voice technician interaction.
11. Stronger simulation and verification models.
12. Production deployment.

---

# Documentation

Detailed implementation notes are available in:

```text
docs/Phase1.md
docs/Phase4.md
docs/Phase5.md
docs/Phase6.md
docs/Phase7.md
docs/Phase8.md
docs/Phase8B.md
docs/Phase8D.md
docs/Phase8E.md
PHASE9.md
plan.md
```

Start with:

```text
PHASE9.md
```

for the latest product-facing implementation.

---

# Project Status

**Current milestone: Phase 9 complete.**

Crater.ai now has an end-to-end path from machine documentation to an interactive diagnostic workflow:

```text
Documentation
     ↓
Machine Knowledge
     ↓
Retrieval
     ↓
Diagnosis
     ↓
Technician Interaction
     ↓
Repair
     ↓
Verification
```

The next milestone should build on this complete loop rather than creating another independent retrieval subsystem.

---

## License

Add the project's chosen license here before public distribution.
