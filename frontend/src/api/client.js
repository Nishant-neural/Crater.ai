// Shared FastAPI client for the Crater.ai frontend.
export const API_BASE = (
  import.meta.env.VITE_API_BASE || "http://localhost:8000"
).replace(/\/$/, "");

async function errorText(res, fallback) {
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") return `${fallback}: ${body.detail}`;
    if (Array.isArray(body?.detail)) {
      return `${fallback}: ${body.detail.map((x) => x.msg || JSON.stringify(x)).join(", ")}`;
    }
    return `${fallback}: ${res.status}`;
  } catch {
    return `${fallback}: ${res.status}`;
  }
}

export async function getJson(path) {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    if (res.status === 404) return null;
    throw new Error(await errorText(res, path));
  }
  return res.json();
}

export async function postJson(path, payload) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await errorText(res, path));
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
  return postJson("/expert/experts", payload);
}

export async function listExperts() {
  return getJson("/expert/experts");
}

export async function createExpertInterview(payload) {
  return postJson("/expert/interviews", payload);
}

export async function addExpertTurn(interviewId, payload) {
  return postJson(`/expert/interviews/${interviewId}/turns`, payload);
}

export async function completeExpertInterview(interviewId) {
  const res = await fetch(`${API_BASE}/expert/interviews/${interviewId}/complete`, { method: "POST" });
  if (!res.ok) throw new Error(await errorText(res, "complete interview"));
  return res.json();
}

export async function extractExpertKnowledge(interviewId) {
  const res = await fetch(`${API_BASE}/expert/interviews/${interviewId}/extract`, { method: "POST" });
  if (!res.ok) throw new Error(await errorText(res, "extract knowledge"));
  return res.json();
}

export async function reviewExpertKnowledge(versionId, payload) {
  return postJson(`/expert/knowledge/${versionId}/review`, payload);
}

export async function generateExpertGraph(versionId) {
  const res = await fetch(`${API_BASE}/expert/knowledge/${versionId}/graph`, { method: "POST" });
  if (!res.ok) throw new Error(await errorText(res, "generate graph"));
  return res.json();
}

// Phase 6 — Functional Digital Twin
export async function createDigitalTwin(payload) {
  return postJson("/digital-twins", payload);
}

export async function createDemoDigitalTwin(productId, revisionId) {
  const res = await fetch(`${API_BASE}/digital-twins/demo/${productId}/${revisionId}`, { method: "POST" });
  if (!res.ok) throw new Error(await errorText(res, "create demo twin"));
  return res.json();
}

export async function listDigitalTwins() {
  return getJson("/digital-twins");
}

export async function getDigitalTwin(twinId) {
  return getJson(`/digital-twins/${twinId}`);
}

export async function runDigitalTwinCommand(twinId, payload) {
  return postJson(`/digital-twins/${twinId}/commands`, payload);
}

export async function getDigitalTwinEvents(twinId) {
  return getJson(`/digital-twins/${twinId}/events`);
}

// Phase 7 — Simulation Agent
export async function runSimulationExperiment(twinId, payload) {
  return postJson(`/simulation/twins/${twinId}/experiment`, payload);
}

export async function runSimulationHypotheses(twinId, payload) {
  return postJson(`/simulation/twins/${twinId}/hypotheses`, payload);
}

// Phase 9 — Diagnostic workstation
export async function startDiagnostic(payload) {
  return postJson("/diagnose/start", payload);
}

export async function getDiagnostic(sessionId) {
  return getJson(`/diagnose/${sessionId}`);
}

export async function getDiagnosticContext(sessionId) {
  return getJson(`/diagnose/${sessionId}/context`);
}

export async function respondDiagnostic(sessionId, payload) {
  return postJson(`/diagnose/${sessionId}/respond`, payload);
}

// Phase 9 — Manual ingestion
export async function uploadManual({ revisionId, file, title, idempotencyKey, docType = "manual" }) {
  const form = new FormData();
  form.append("revision_id", revisionId);
  form.append("doc_type", docType);
  form.append("title", title || file.name);
  form.append("idempotency_key", idempotencyKey);
  form.append("file", file);

  const res = await fetch(`${API_BASE}/ingest`, {
    method: "POST",
    body: form,
  });

  if (!res.ok) throw new Error(await errorText(res, "manual ingestion"));
  return res.json();
}

export async function getIngestionJob(jobId) {
  return getJson(`/ingest/jobs/${jobId}`);
}

export async function retryIngestion(jobId) {
  const res = await fetch(`${API_BASE}/ingest/jobs/${jobId}/retry`, { method: "POST" });
  if (!res.ok) throw new Error(await errorText(res, "retry ingestion"));
  return res.json();
}

export async function integrateKnowledge(revisionId) {
  const res = await fetch(`${API_BASE}/knowledge/revisions/${revisionId}/integrate`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(await errorText(res, "knowledge integration"));
  return res.json();
}

export async function getKnowledgeModel(revisionId) {
  return getJson(`/knowledge/revisions/${revisionId}/model`);
}
