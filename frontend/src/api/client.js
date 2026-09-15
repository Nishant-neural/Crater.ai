// Thin fetch wrapper around backend/api/routes/visualization.py.
// Base URL points at the FastAPI app (see backend README "Running").
export const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

async function getJson(path) {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    if (res.status === 404) return null;
    throw new Error(`${path} -> ${res.status}`);
  }
  return res.json();
}

export function getComponentExplorer(revisionId) {
  return getJson(`/visualization/revisions/${revisionId}/components`);
}

export function getInteractiveDiagram(chunkId) {
  return getJson(`/visualization/diagrams/${chunkId}`);
}

export function diagramImageUrl(chunkId) {
  return `${API_BASE}/visualization/diagrams/${chunkId}/image`;
}

export function getProcedureVisualization(procedureId) {
  return getJson(`/visualization/procedures/${procedureId}`);
}

export function procedureFrameUrl(procedureId, frameIndex) {
  return `${API_BASE}/visualization/procedures/${procedureId}/frames/${frameIndex}`;
}
