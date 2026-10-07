// Thin fetch wrapper around the FastAPI backend.
export const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

export async function getJson(path) {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    if (res.status === 404) return null;
    throw new Error(`${path} -> ${res.status}`);
  }
  return res.json();
}

export function getComponentExplorer(revisionId) { return getJson(`/visualization/revisions/${revisionId}/components`); }
export function getInteractiveDiagram(chunkId) { return getJson(`/visualization/diagrams/${chunkId}`); }
export function diagramImageUrl(chunkId) { return `${API_BASE}/visualization/diagrams/${chunkId}/image`; }
export function getProcedureVisualization(procedureId) { return getJson(`/visualization/procedures/${procedureId}`); }
export function procedureFrameUrl(procedureId, frameIndex) { return `${API_BASE}/visualization/procedures/${procedureId}/frames/${frameIndex}`; }

export async function createExpert(payload) { return postJson("/expert/experts", payload, "create expert"); }
export async function listExperts() { return getJson("/expert/experts"); }
export async function createExpertInterview(payload) { return postJson("/expert/interviews", payload, "create interview"); }
export async function addExpertTurn(interviewId, payload) { return postJson(`/expert/interviews/${interviewId}/turns`, payload, "add turn"); }
export async function completeExpertInterview(interviewId) { return postRequest(`/expert/interviews/${interviewId}/complete`, "complete interview"); }
export async function extractExpertKnowledge(interviewId) { return postRequest(`/expert/interviews/${interviewId}/extract`, "extract knowledge"); }
export async function reviewExpertKnowledge(versionId, payload) { return postJson(`/expert/knowledge/${versionId}/review`, payload, "review knowledge"); }
export async function generateExpertGraph(versionId) { return postRequest(`/expert/knowledge/${versionId}/graph`, "generate graph"); }

export async function createDigitalTwin(payload) { return postJson("/digital-twins", payload, "create digital twin"); }
export async function createDemoDigitalTwin(productId, revisionId) { return postRequest(`/digital-twins/demo/${productId}/${revisionId}`, "create demo twin"); }
export async function listDigitalTwins() { return getJson("/digital-twins"); }
export async function getDigitalTwin(twinId) { return getJson(`/digital-twins/${twinId}`); }
export async function runDigitalTwinCommand(twinId, payload) { return postJson(`/digital-twins/${twinId}/commands`, payload, "digital twin command"); }
export async function getDigitalTwinEvents(twinId) { return getJson(`/digital-twins/${twinId}/events`); }

export async function runSimulationExperiment(twinId, payload) { return postJson(`/simulation/twins/${twinId}/experiment`, payload, "simulation experiment"); }
export async function runSimulationHypotheses(twinId, payload) { return postJson(`/simulation/twins/${twinId}/hypotheses`, payload, "simulation hypotheses"); }
export async function compileSimulationRevision(revisionId) { return postRequest(`/simulation/revisions/${revisionId}/compile`, "compile simulation"); }
export async function getRevisionSimulation(revisionId) { return getJson(`/simulation/revisions/${revisionId}/twin`); }
export async function getSimulationContext(twinId) { return getJson(`/simulation/twins/${twinId}/context`); }

export async function startDiagnostic(payload) { return postJson("/diagnose/start", payload, "start diagnostic"); }
export async function getDiagnostic(sessionId) { return getJson(`/diagnose/${sessionId}`); }
export async function getDiagnosticContext(sessionId) { return getJson(`/diagnose/${sessionId}/context`); }
export async function respondDiagnostic(sessionId, payload) { return postJson(`/diagnose/${sessionId}/respond`, payload, "diagnostic response"); }

export async function postJson(path, payload, label = path) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await errorText(res, label));
  return res.json();
}

async function postRequest(path, label = path) {
  const res = await fetch(`${API_BASE}${path}`, { method: "POST" });
  if (!res.ok) throw new Error(await errorText(res, label));
  return res.json();
}

/**
 * Upload a PDF while reporting real browser-to-server byte progress.
 * The server responds quickly with an ingestion job id; processing is then
 * observed through /ingest/jobs/{job_id}.
 */
export function uploadManual({ revisionId, file, title, idempotencyKey, onUploadProgress }) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const form = new FormData();
    form.append("revision_id", revisionId);
    form.append("doc_type", "manual");
    form.append("title", title || file.name);
    form.append("idempotency_key", idempotencyKey);
    form.append("file", file);

    xhr.open("POST", `${API_BASE}/ingest`);
    xhr.responseType = "json";

    xhr.upload.onprogress = (event) => {
      if (!event.lengthComputable) return;
      onUploadProgress?.({
        loaded: event.loaded,
        total: event.total,
        percent: Math.round((event.loaded / event.total) * 100),
      });
    };

    xhr.onload = () => {
      const body = xhr.response;
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(body);
        return;
      }
      const detail = body?.detail || body?.message || `HTTP ${xhr.status}`;
      reject(new Error(`manual upload: ${detail}`));
    };

    xhr.onerror = () => reject(new Error("manual upload: network error"));
    xhr.onabort = () => reject(new Error("manual upload: cancelled"));
    xhr.send(form);
  });
}

export async function getIngestionJob(jobId) {
  return getJson(`/ingest/jobs/${jobId}`);
}

export async function getRevisionIngestionStatus(revisionId) {
  return getJson(`/ingest/revisions/${encodeURIComponent(revisionId)}/status`);
}

export async function retryIngestionJob(jobId) {
  const res = await fetch(`${API_BASE}/ingest/jobs/${jobId}/retry`, { method: "POST" });
  if (!res.ok) throw new Error(await errorText(res, "retry ingestion"));
  return res.json();
}

export async function integrateKnowledge(revisionId) {
  const res = await fetch(`${API_BASE}/knowledge/revisions/${revisionId}/integrate`, { method: "POST" });
  if (!res.ok) throw new Error(await errorText(res, "knowledge integration"));
  return res.json();
}

export async function getKnowledgeModel(revisionId) { return getJson(`/knowledge/revisions/${revisionId}/model`); }

async function errorText(res, fallback) {
  try {
    const body = await res.json();
    return `${fallback}: ${body.detail || res.status}`;
  } catch {
    return `${fallback}: ${res.status}`;
  }
}
