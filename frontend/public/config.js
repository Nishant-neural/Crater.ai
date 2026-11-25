// Runtime configuration for the Crater.ai frontend (frontend audit
// Step 5). This file is served with the static files, so the API base
// URL is injected at runtime — it is NOT baked into the bundle at
// build time. An empty apiBase means "unset": client.js falls back to
// VITE_API_BASE, then to http://localhost:8000/v1.
//
// In the container stack, compose mounts docker/frontend/config.js
// over this file with apiBase "/v1" (same origin via Traefik/the
// gateway). No rebuild is needed per environment, and the browser
// never talks to a backend service directly — only to the gateway.
window.__CRATER_CONFIG__ = { apiBase: "" };
