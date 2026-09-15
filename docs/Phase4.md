# Crater.ai — Phase 4: Technical Visualization

Phase 4 (`PLAN.md` §33) turns the structured knowledge Phases 1-3 already
built — Components, ComponentRelationships, and the schematic component/wire
graph — into things a technician can actually look at and click through:
an interactive diagram, a component explorer, and a step-by-step procedure
viewer with a played-through "repair animation."

It adds **no new source-of-truth tables**. Everything in
`backend/visualization/` is a read-time re-projection of Phase 1/3 data into
shapes a frontend can render directly, plus a small React app
(`frontend/`) that renders them.

## What's implemented

| plan.md Phase 4 requirement | Where |
|---|---|
| Interactive diagrams (pan/zoom/click) | `backend/visualization/interactive_diagram.py` (pixel-space manifest) + `frontend/src/technical-viewer/DiagramViewer.jsx` |
| Annotated schematics | Same `DiagramViewer.jsx` — clicking a node shows its Phase-1 component link; a `highlightLabels` prop drives the highlighted-box styling used both for manual node clicks and for procedure-step frames |
| Component explorer | `backend/visualization/component_explorer.py` (joins Component + ComponentRelationship + SchematicNode) + `frontend/src/components/ComponentExplorer.jsx` |
| Procedure visualization | `backend/visualization/procedure_viz.py::build_procedure_visualization` (matches each step's text against the revision's components) + `frontend/src/components/ProcedureViewer.jsx` |
| Repair animations | `procedure_viz.py`'s `animation_frames` — an ordered sequence of annotated-diagram frames, one per (step, diagram) pair with a located component — played back by `ProcedureViewer.jsx`'s "Play repair animation" button |
| API | `backend/api/routes/visualization.py`: `GET /visualization/revisions/{revision_id}/components`, `GET /visualization/diagrams/{chunk_id}` (+ `/image`), `GET /visualization/procedures/{procedure_id}` (+ `/frames/{frame_index}`) |

## Honest limits

- **"Repair animation" is a slideshow, not video or 3D.** There's no CAD or
  digital-twin pipeline yet (that's Phase 6-7/10) — a repair animation is an
  ordered sequence of Phase 3's annotated diagram images, one per procedure
  step that mentions a locatable component, played back on a timer. That's
  still useful ("step 3 mentions the K17 relay — here's exactly where it
  is"), but it's not a rendered repair simulation.
- **Step -> component matching is case-insensitive substring matching**
  (`procedure_viz.py::_find_referenced_components`), the same trade-off
  Phase 3 made for schematic label matching. A step phrased as "the drive
  relay" won't match a component named "Relay K17." A fuzzy/LLM-assisted
  matcher is a reasonable upgrade once real manuals show how often steps and
  component names disagree in phrasing.
- **No cross-diagram animation.** A procedure's frames only include
  diagrams where Phase 3 already located the mentioned component; a step
  whose component was never extracted onto any schematic just has zero
  frames (the frontend shows "No located components to highlight" for that
  step rather than silently guessing).
- **Frames are computed on every request, not cached.** `procedure_viz.get_frame`
  re-derives the whole visualization per call. Fine at pilot scale (a
  handful of steps/components); revisit if procedures or component counts
  grow large enough for this to matter.
- **No product/revision picker in the frontend.** The React app takes a
  revision ID / procedure ID as raw text input — there's no "browse
  everything I've ingested" endpoint yet from Phases 1-3 to build a picker
  against.
- **Interactive diagram manifests are scoped per diagram chunk, not per
  document** (a Document/manual can have several diagram pages). This
  mirrors how Phase 3's highlight endpoint already resolves a node's image
  via its chunk, not its document.

## Running

Backend (same server as Phases 1-3 — no new dependencies):

```bash
uvicorn backend.api.main:app --reload
```

```bash
# Component explorer for a revision
curl localhost:8000/visualization/revisions/<revision_id>/components

# Interactive diagram manifest + raw image for one diagram chunk
curl localhost:8000/visualization/diagrams/<chunk_id>
curl localhost:8000/visualization/diagrams/<chunk_id>/image --output diagram.png

# Procedure visualization + one repair-animation frame
curl localhost:8000/visualization/procedures/<procedure_id>
curl localhost:8000/visualization/procedures/<procedure_id>/frames/0 --output frame0.png
```

Frontend:

```bash
cd frontend
npm install
cp .env.example .env   # point VITE_API_BASE at the backend if not localhost:8000
npm run dev
```

## Tests

```bash
pytest tests/test_visualization.py
```

Covers the pure/DB-backed logic in `backend/visualization/` against a real
in-memory SQLite database (component appearances/relationships, pixel-space
bbox conversion, edge resolution, step-to-component matching, and frame
indexing) — no Qdrant, Anthropic key, or frontend build required.
