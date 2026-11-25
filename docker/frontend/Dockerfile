# Frontend container (contracts README §0/§10 step 5; frontend audit Step 5).
# Multi-stage build: node:22-alpine builds the bundle, nginx:alpine
# serves it. The container points at the gateway only — no backend
# service name appears anywhere in the frontend config: the API base
# URL is injected at RUNTIME via /config.js (see public/config.js),
# never baked into the bundle at build time. Compose mounts
# docker/frontend/config.js over the built config.js with apiBase "/v1"
# (same origin via Traefik/the gateway), so no rebuild per environment.
FROM node:22-alpine AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend ./
RUN npm run build

FROM nginx:alpine
COPY docker/frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
# / is liveness (contracts §1.4: the frontend's readiness is process-up
# only; it does not gate on the gateway — the gateway's 503 envelope
# is the signal when a dependency is down).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1
CMD ["nginx", "-g", "daemon off;"]
