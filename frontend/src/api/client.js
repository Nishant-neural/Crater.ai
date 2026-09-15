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

export async function createExpert(payload) {
  const res = await fetch(`${API_BASE}/expert/experts`, {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(`create expert -> ${res.status}`);
  return res.json();
}

export async function listExperts() {
  return getJson("/expert/experts");
}

export async function createExpertInterview(payload) {
  const res = await fetch(`${API_BASE}/expert/interviews`, {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(`create interview -> ${res.status}`);
  return res.json();
}

export async function addExpertTurn(interviewId, payload) {
  const res = await fetch(`${API_BASE}/expert/interviews/${interviewId}/turns`, {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(`add turn -> ${res.status}`);
  return res.json();
}

export async function completeExpertInterview(interviewId) {
  const res = await fetch(`${API_BASE}/expert/interviews/${interviewId}/complete`, {method: "POST"});
  if (!res.ok) throw new Error(`complete interview -> ${res.status}`);
  return res.json();
}

export async function extractExpertKnowledge(interviewId) {
  const res = await fetch(`${API_BASE}/expert/interviews/${interviewId}/extract`, {method: "POST"});
  if (!res.ok) throw new Error(`extract knowledge -> ${res.status}`);
  return res.json();
}

export async function reviewExpertKnowledge(versionId, payload) {
  const res = await fetch(`${API_BASE}/expert/knowledge/${versionId}/review`, {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(`review knowledge -> ${res.status}`);
  return res.json();
}

export async function generateExpertGraph(versionId) {
  const res = await fetch(`${API_BASE}/expert/knowledge/${versionId}/graph`, {method: "POST"});
  if (!res.ok) throw new Error(`generate graph -> ${res.status}`);
  return res.json();
}
