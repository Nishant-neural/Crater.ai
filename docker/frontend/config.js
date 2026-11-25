// Runtime configuration for the Crater.ai frontend in the container
// stack (frontend audit Step 5). Compose mounts this file over
// public/config.js in the nginx image, so the API base URL is injected
// at runtime — no rebuild per environment. "/v1" is same-origin via
// Traefik: the browser talks ONLY to the gateway, never to a backend
// service directly (contracts README §6).
window.__CRATER_CONFIG__ = { apiBase: "/v1" };
