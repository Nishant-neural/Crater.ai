// Thin fetch wrapper around the gateway (contracts README §6: the
// frontend talks ONLY to the gateway, all calls under /v1/).
// Base URL is injected at RUNTIME, not baked in at build time
// (frontend audit Step 5): /config.js, served with the static files,
// may set window.__CRATER_CONFIG__.apiBase. Precedence: runtime
// config > VITE_API_BASE (local-dev override) > http://localhost:8000/v1.
// A runtime apiBase of "" means "unset" — it never overrides the env
// fallback. In the container stack, compose mounts
// docker/frontend/config.js over public/config.js with apiBase "/v1"
// (same origin via Traefik/the gateway), so no rebuild per environment.
function resolveApiBase() {
  const runtime = globalThis.window?.__CRATER_CONFIG__?.apiBase;
  if (typeof runtime === "string" && runtime.trim()) return runtime.replace(/\/$/, "");
  const env = import.meta.env.VITE_API_BASE;
  if (typeof env === "string" && env.trim()) return env.replace(/\/$/, "");
  return "http://localhost:8000/v1";
}
export const API_BASE = resolveApiBase();

// Contracts §1.2: explicit timeouts on every call (frontend audit
// Step 2). A call that times out surfaces as a retryable error, not
// a hang. Every request carries an X-Request-ID (generated per call;
// the gateway echoes it in the error envelope).
const REQUEST_TIMEOUT_MS = 10_000;

function newRequestId() {
  if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
  return `fe-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

async function fetchWithTimeout(path, init = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    return await fetch(`${API_BASE}${path}`, {
      ...init,
      signal: controller.signal,
      headers: { "X-Request-ID": newRequestId(), ...(init.headers || {}) },
    });
  } catch (err) {
    if (err?.name === "AbortError") throw new Error(`${path} -> timed out after ${REQUEST_TIMEOUT_MS}ms`);
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

export async function getJson(path) {
  const res = await fetchWithTimeout(path);
  if (!res.ok) {
    if (res.status === 404) return null;
    throw new Error(await errorText(res, path));
  }
  return res.json();
}

export function getComponentExplorer(revisionId) { return getJson(`/visualization/revisions/${revisionId}/components`); }
export function getInteractiveDiagram(chunkId) { return getJson(`/visualization/diagrams/${chunkId}`); }
export function diagramImageUrl(chunkId) { return `${API_BASE}/visualization/diagrams/${chunkId}/image`; }
export function getProcedureVisualization(procedureId) { return getJson(`/visualization/procedures/${procedureId}`); }
export function procedureFrameUrl(procedureId, frameIndex) { return `${API_BASE}/visualization/procedures/${procedureId}/frames/${frameIndex}`; }


export async function runSimulationExperiment(twinId, payload) { return postJson(`/simulation/twins/${twinId}/experiment`, payload, "simulation experiment"); }
export async function runSimulationHypotheses(twinId, payload) { return postJson(`/simulation/twins/${twinId}/hypotheses`, payload, "simulation hypotheses"); }
export async function compileSimulationRevision(revisionId) { return postRequest(`/simulation/revisions/${revisionId}/compile`, "compile simulation"); }
export async function approveTwin(twinId) { return postRequest(`/simulation/twins/${twinId}/approve`, "approve twin"); }
export async function getRevisionSimulation(revisionId) { return getJson(`/simulation/revisions/${revisionId}/twin`); }
export async function getSimulationContext(twinId) { return getJson(`/simulation/twins/${twinId}/context`); }
export async function getTwinVisual(twinId) { return getJson(`/simulation/twins/${twinId}/visual`); }

// NOTE (Phase 3 frontend step): the legacy /digital-twins client calls
// (create/list/get/command) are deleted with the CUT demo panel
// (frontend audit, Category 2). VisualTwin + TwinWorkspace remain
// the twin surfaces and reach the twin only through the /simulation/*
// calls above, via the gateway.

export async function startDiagnostic(payload) { return postJson("/diagnose/start", payload, "start diagnostic"); }
export async function getDiagnostic(sessionId) { return getJson(`/diagnose/${sessionId}`); }
export async function listDiagnostics(productId, revisionId) {
  // Phase 2: past sessions for a machine, most recent first (backend
  // orders by created_at desc). Returns { sessions: [...] }.
  const params = new URLSearchParams();
  if (productId) params.set("product_id", productId);
  if (revisionId) params.set("revision_id", revisionId);
  const query = params.toString();
  return getJson(`/diagnose/sessions${query ? `?${query}` : ""}`);
}
export async function getDiagnosticContext(sessionId) { return getJson(`/diagnose/${sessionId}/context`); }
export async function respondDiagnostic(sessionId, payload) { return postJson(`/diagnose/${sessionId}/respond`, payload, "diagnostic response"); }
export async function recordDiagnosticOutcome(sessionId, payload) { return postJson(`/diagnose/${sessionId}/outcome`, payload, "record repair outcome"); }

// NOTE (Phase 3 frontend step): the section-2.5 client functions are
// deleted with the backend endpoints (contracts section 2.5;
// diagnosis notes section 3). The tournament and the upstream walk
// run inside POST /diagnose/{id}/respond and their results arrive on
// the session timeline (early_tournament entries); the discriminating
// test arrives the same way. DiagnosticWorkspace reads them from the
// session state — no separate call exists, and none is invented here.

export async function postJson(path, payload, label = path) {
  const res = await fetchWithTimeout(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await errorText(res, label));
  return res.json();
}

async function postRequest(path, label = path) {
  const res = await fetchWithTimeout(path, { method: "POST" });
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
    xhr.setRequestHeader("X-Request-ID", newRequestId());
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
  const res = await fetchWithTimeout(`/ingest/jobs/${jobId}/retry`, { method: "POST" });
  if (!res.ok) throw new Error(await errorText(res, "retry ingestion"));
  return res.json();
}

export async function integrateKnowledge(revisionId) {
  const res = await fetchWithTimeout(`/knowledge/revisions/${revisionId}/integrate`, { method: "POST" });
  if (!res.ok) throw new Error(await errorText(res, "knowledge integration"));
  return res.json();
}

export async function getKnowledgeModel(revisionId) { return getJson(`/knowledge/revisions/${revisionId}/model`); }

async function errorText(res, fallback) {
  try {
    const body = await res.json();
    // The §1.3 error envelope carries the plain-sentence message;
    // older handlers carry `detail`. Show the message, never a bare
    // status code.
    const message = body?.error?.message || body?.detail || res.status;
    return `${fallback}: ${message}`;
  } catch {
    return `${fallback}: ${res.status}`;
  }
}
