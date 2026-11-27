# Phase 3 — frontend extraction build notes

**Date:** 23 Nov 2026. **Working copy only:** `~/workspace/crater-refactor/`.
**Governing:** `contracts/README.md` (§6 frontend ↔ gateway) +
`contracts/openapi-gateway.yaml`, the frontend audit (incl. its 23 Nov
recheck corrections), the amendment (four resolutions),
`PHASE3_INGESTION_BUILD_NOTES.md`, `PHASE3_DIAGNOSIS_BUILD_NOTES.md`,
`PHASE3_TWIN_BUILD_NOTES.md` (the diagnosis step left the §2.5
frontend callers for this step and listed them exactly; the twin step
left any further visual-rendering move for this step — both are
handled here).
**Scope discipline (Nishant, 23 Nov 2026):** containerize the EXISTING
application per the audits; change code ONLY where the audit says to;
no rewriting modules from scratch, no regenerating files that already
exist. This step is the Phase 3 extraction order's step 5 (last, after
gateway, ingestion, diagnosis-agent, twin-verifier). The oracle gate
after this extraction is READY AND WAITING for Nishant's other LLM
(standing rule 23 Nov 15:49 IST — no Muse-as-backend run here).

## Baseline (Step 0, before any edit)

`npm ci` + `npm run build` pass (vite build, 30 modules, 311.60 kB
JS); `npm run lint` 31 warnings, 0 errors. No frontend tests existed
(audit OQ4). `frontend/src` counts at baseline: `client.js` 144,
`DiagnosticWorkspace.jsx` 420, `App.jsx` 157, `App.css` 527 lines.

## What changed (file paths, edits not rewrites)

### 1. Runtime-injected base URL (`frontend/src/api/client.js`, `frontend/public/config.js` — new, `frontend/index.html`)

- `client.js` now resolves the base URL at runtime, in precedence
  order: `window.__CRATER_CONFIG__.apiBase` (from `/config.js`) >
  `VITE_API_BASE` (local-dev override) > `http://localhost:8000/v1`
  (unchanged default, gateway step). A runtime `apiBase` of `""`
  means "unset" and never overrides the env fallback.
- `frontend/public/config.js` (new, minimal): sets
  `window.__CRATER_CONFIG__ = { apiBase: "" }` with a comment
  explaining the mechanism. `index.html` loads `/config.js` before
  the bundle. In the container stack, compose mounts
  `docker/frontend/config.js` over this file with `apiBase: "/v1"`
  (same origin via Traefik/the gateway) — no rebuild per environment,
  and the browser never talks to a backend service directly.
- Client hardening (audit Step 2, transport only — the file stays
  hand-written; the generated client is post-gate, see §7): every
  request now has a 10 s `AbortController` timeout (contracts §1.2),
  an `X-Request-ID` per call (generated via `crypto.randomUUID()`),
  and error text from the §1.3 envelope (`error.message`, falling
  back to `detail`, never a bare status code). The upload XHR sends
  `X-Request-ID` too. `fetchWithTimeout` is the single fetch path
  for `getJson`/`postJson`/`postRequest`/retry/integrate.
- Deliberately NOT changed (follow-up, §7): `getJson` still returns
  `null` on 404. The audit's Step 2 says remove 404-to-null, but
  ~10 call sites rely on it (`restoreSession`'s explicit null check,
  `begin`/`sendAnswer`'s unguarded context fetch, the shell's
  `.catch(() => {})` loads). Removing it is a caller-wide change,
  not a transport edit, so it stays and is recorded here rather
  than done half-way.

### 2. CUT sweep — remainder (audit Category 2)

The three CUT components were **already absent from this tree**
when this step started (verified, not assumed): no
`ExpertKnowledge.jsx`, `ProcedureViewer.jsx`, or `DigitalTwin.jsx`
under `frontend/src/components/`, no `api/schematic.js` or
`api/evidence.js`, no `.expert*` selectors in `App.css`, no expert
imports anywhere. The gateway step had already established the
expert file absent; this step verified the other two and the dead
shims the same way. What remained of the sweeps, done here:

- `client.js`: deleted the legacy `/digital-twins` client calls
  (`createDigitalTwin`, `listDigitalTwins`, `getDigitalTwin`,
  `runDigitalTwinCommand`) — zero frontend callers (verified by
  grep). A NOTE records the deletion and that VisualTwin +
  TwinWorkspace remain the twin surfaces, reaching the twin only
  through `/simulation/*` via the gateway.
- `App.css`: removed the last expert remnant, the stale comment
  `/* Expert capture as an overlay from Diagnose, not a top-level
  tab. */` (line 499). The `.twin*` selectors are untouched —
  shared with the kept `VisualTwin.jsx` (verified still present).
- `DiagnosticWorkspace.jsx` outcome card: the recorded-outcome copy
  no longer names "Expert Knowledge" ("It stays pending review and
  does not influence diagnosis until reviewed") and the checkbox
  reads "Submit correction as draft knowledge for review". The
  `submit_correction_for_review` backend field itself is untouched
  (record-only, `pending_review` — backend data, not the CUT UI).
- Remaining "expert" strings in `src/` are `expert_seed` — the twin
  definition's backend metadata (the seeded OMNIPRO twin), not the
  cut capture surface. Left alone.
- No learning UI exists anywhere in `frontend/src` (grep: zero
  hits), so there is nothing to gate under contracts §2.4/§6.

### 3. §2.5 frontend callers deleted and rewired to the live /respond flow (`frontend/src/api/client.js`, `frontend/src/api/diagnostics.js`, `frontend/src/components/DiagnosticWorkspace.jsx`)

Deleted from `client.js` + the `diagnostics.js` re-export:
`verifyRepair`, `verifyHypotheses`, `getMostInformativeTest`,
`getGuidedPhysicalChecks`. No calls to the deleted endpoints remain
in `src/` or in the built bundle (grep-clean, see §Test results;
the two NOTE comments that named the endpoints were reworded so a
plain grep is clean).

Rewired in `DiagnosticWorkspace.jsx` (edits, not a rewrite — 420 →
368 lines):

- **Tournament (P-12):** there is no endpoint to call — the
  tournament runs inside `POST /diagnose/{id}/respond`
  (`backend/api/routes/diagnostics.py:817`, plus the `run_turn`
  conclusion gate). The workspace now derives it from the session
  timeline: the latest `early_tournament` entry (results
  `cause/status/verdict/reason/cannot_express`, `counts`,
  `discriminating_test`) or the latest `twin_unavailable` entry.
  The HYPOTHESIS TOURNAMENT card renders those rows, and the
  VERIFICATION card renders the latest twin entry's counts/reason.
  The old gating bug is gone with the buttons: the tournament is no
  longer gated on `isConclusion` — it shows from the first turn
  that ran one. The visual-twin playback button is gone with the
  old card: timeline rows carry no `baseline_state`/`fault_trace`
  playback state to open it with. The "Open simulation workspace"
  button survives via the direct prop (see §5).
- **Most informative next test:** the card now renders the
  `discriminating_test` from the latest `early_tournament` entry
  (test + rationale + `separates`), falling back to a muted line
  when no documented test separated the current hypotheses. No
  button, no endpoint — the test picker already runs inside the
  turn and rewrites `current_step` when it splits the hypotheses.
- **Guided physical checks:** card deleted entirely. No live
  equivalent exists; per contracts §2.5 the checks folded into the
  turn response, and no separate call is invented.
- Deleted state and functions: `nextTest`, `guidedChecks`,
  `verification`, `verifyBusy`, `tournament`, `tournamentBusy`,
  `loadNextTest`, `loadGuidedChecks`, `verifyRepairInTwin`,
  `runTournament`, `openTournamentPlayback`. `restoreSession` /
  `backToSessions` clear only the outcome state now (tournament /
  verification state lives on the timeline, which is persisted).

### 4. Step 3b — real inputs and no slice caps (audit P-03, P-08)

- **Hardcoded symptom removed (P-03):** the setup textarea no
  longer defaults to `"Motor M1 is overheating after 20 minutes."`
  — it starts empty with the existing placeholder.
- **Observations at start — CORRECTION TO THE TASK PREMISE:** the
  Step 3a diagnosis schema change has **not** landed.
  `StartDiagnosticRequest` (`backend/api/routes/diagnostics.py:65-68`)
  still accepts only `product_id`, `revision_id`, `symptom` — no
  `observations` field. The task brief said the change "landed in
  Phase 1/Diagnosis"; it did not, and this step does not invent the
  field on start (contracts §4.1: a start carrying `observations`
  before the change is rejected, not silently dropped). What the
  frontend does instead, on the documented interim path: the setup
  form gains an optional "Observations / measurements" textarea,
  and `begin()` sends it as the first observation turn via the live
  `POST /diagnose/{id}/respond` (`input_kind: "observation"`)
  immediately after a successful start, updating the session from
  the response. Nothing is dropped, nothing is invented into the
  start payload. The real schema change (Step 3a) is still owed by
  the diagnosis-agent side.
- **Slice caps removed (P-08):** `MachineGraph` `components.slice(0,
  18)` → all components; `MiniGraph` `entities.slice(0, 24)` → all
  entities (`MachineModelViewer.jsx`); `SimulationAgent.jsx`
  relations `slice(0, 24)` → all relations; sources
  `docs.slice(0, 5)` → all docs; repair sources
  `evidence.slice(0, 4)` → all evidence; verification findings
  `slice(0, 12)` → all findings. The containers already scroll
  (`.machine-graph`, `.model-mini-graph`,
  `.simulation-relations` all `overflow: auto`); `.context-docs`
  gained `max-height: 220px; overflow: auto` in `App.css` for the
  same reason. No truncation at any boundary (contracts §1.1).

### 5. Event bus removed, lists deduplicated (audit Step 4)

- No `window.dispatchEvent` / `CustomEvent` / `addEventListener`
  remains in `src/` (grep-clean; the audit gate's check passes).
  `App.jsx`'s two listeners are replaced by a direct callback prop:
  `openSimulationFor(detail)` passed as `onOpenSimulation` to
  `DiagnosticWorkspace`, which calls it instead of dispatching.
  `visualContext` state and the `visualContext` prop to
  `TwinWorkspace` are deleted with the bus (see §3: no playback
  data survives to open it with). `simulationContext` stays as
  App state passed to `TwinWorkspace` as `diagnosticContext`.
- `listMachines`/`listRevisions` deduplication, shell-owned: `App.jsx`
  owns both lists and passes `machines`, `revisions`, and a
  `refreshMachines` callback to `DiagnosticWorkspace`, which no
  longer fetches them itself (its onboarding `onReady` now sets the
  ids from the result and calls `onRefreshMachines`). `Machines.jsx`,
  `MachineModelViewer.jsx`, and `MachineOnboarding.jsx` still fetch
  their own lists (their own selection state / standalone use) —
  full dedupe across those three is a follow-up, not smuggled in
  here.

### 6. Frontend container (`docker/frontend/Dockerfile` — new, `docker/frontend/nginx.conf` — new, `docker/frontend/config.js` — new, `docker-compose.yml`, `docker/traefik/dynamic.yml`)

- `docker/frontend/Dockerfile` (new, mirrors the audit Step 5
  shape): multi-stage, `node:22-alpine` builds the bundle (`npm ci`
  + `npm run build`), `nginx:alpine` serves `dist/` on port 80,
  HEALTHCHECK on `/`. No backend service name appears in the image
  config; the API base comes from the runtime `config.js` only.
- `docker/frontend/nginx.conf` (new, minimal): serves `dist/`,
  `try_files $uri $uri/ /index.html` (no router yet, so this is a
  future-proof fallback, not a live requirement), `Cache-Control:
  no-store` on `/config.js` so a stale config never points the UI
  at the wrong gateway.
- `docker/frontend/config.js` (new): `window.__CRATER_CONFIG__ =
  { apiBase: "/v1" }` — same origin via Traefik/the gateway.
- `docker-compose.yml`: new `frontend` service (builds
  `docker/frontend/Dockerfile`, mounts
  `./docker/frontend/config.js:/usr/share/nginx/html/config.js:ro`,
  `depends_on: gateway`, **no published port** — Traefik is the
  only entry point). 11 services now. Header comment updated.
- `docker/traefik/dynamic.yml`: new `frontend` router
  (`PathPrefix(`/`)`, explicit `priority: 1`) + `frontend` service
  (`http://frontend:80`, health check on `/`). The gateway routers
  keep their longer rules, so `/v1/`, `/health`, `/ready`,
  `/metrics`, `/auth/verify` still route to the gateway.
- Docker images were NOT built — no Docker in this VM. The
  Dockerfile and compose entries are written but unbuilt; the first
  `docker compose up --build` on a machine with Docker is still
  unverified.

### 7. What was deliberately NOT done here (deferred, with owner)

- **Post-gate replacements (audit p4, explicitly post-gate):**
  hand-rolled SVG graphs → `@xyflow/react`, ad-hoc fetching →
  `@tanstack/react-query`, view switching → `react-router-dom`,
  hand-rolled client → generated OpenAPI client. None of these is
  done — the audit says each swap lands after the Miller/OMNIPRO
  gate, one PR each, measured on the same rows. Doing any of them
  here would have been a rewrite, which the task forbids. Follow-up
  after the final oracle gate.
- **Visual rendering stays frontend** (twin-notes item): no move
  was needed. `visual.py` already lives in the gateway shim
  (`backend/api/visual.py`, `/simulation/twins/{id}/visual` +
  `/v1/` alias) and the frontend still calls the same path via
  `getTwinVisual`. `VisualTwin.jsx` is KEEP-custom per the audit —
  backend-coordinate SVG is not a node graph and `@xyflow/react`
  is not a substitute for it.
- **Step 3a observations schema change** (diagnosis-agent side):
  still owed — see §4. Until it lands, observations travel via
  `/respond` as implemented here.
- **404-to-null** in `getJson`: kept, see §1 (caller-wide change).
- **`Machines.jsx` / `MachineModelViewer.jsx` /
  `MachineOnboarding.jsx` list fetching**: still per-component,
  see §5.
- No backend code was touched in this step.

## Non-LLM test results

No LLM call was made anywhere in this step; no mock-server run; no
Muse tokens spent.

- **Baseline (pre-edit):** `npm run build` passes; `npm run lint`
  31 warnings, 0 errors.
- **`npm run build`: passes** (vite build, 30 modules; JS 306.60 kB
  vs 311.60 kB baseline — the deleted client functions and cards).
- **`npm run lint`: 31 warnings, 0 errors** — identical count to
  the baseline; no new warnings.
- **New `frontend/tests/frontend-smoke.test.mjs`: 8 passed, 0 failed**
  (`npm test` → `node --test tests/*.test.mjs`; no new deps, no
  runner — plain `node:test`). Tests: no deleted §2.5/expert
  strings in `src/`; none in the built bundle; CUT components gone
  and unreferenced (and `.twin` CSS still present); no CustomEvent
  bus, deep link is a direct prop; runtime-injected base URL
  (config file + `index.html` script tag + container `/v1`
  config); no backend service host in src or container config;
  live `/respond` is the only diagnosis write path, hardcoded
  symptom gone, observations input present; slice caps gone.
  Two test bugs found and fixed in this step (not built over): the
  bundle-path join doubled `assets/`, and two repo-path reads went
  through the frontend-root `read()` helper. One real finding from
  the suite: the NOTE comments in `client.js` named the deleted
  endpoints, so a plain grep tripped — the comments were reworded
  (no endpoint literals remain in `src/`). `node --test tests/`
  (directory form) fails on this Node (24.22.1) with
  `MODULE_NOT_FOUND` — the script uses the glob form, which works.
- **Smoke test (built app, static serving):** `dist/` served with
  a throwaway `python3 -m http.server` on 127.0.0.1:4174 — `/` 200,
  `/config.js` 200 serving the runtime config, `index.html`
  contains `<script src="/config.js"></script>`, the built bundle
  contains `__CRATER_CONFIG__`. Server stopped afterwards (no
  leftover listeners).
- **Gate greps (src + built bundle):** no matches for
  `verify-repair`, `verify-hypotheses`, `early-tournament`,
  `upstream-walk`, `next-test`, `guided-checks`, `fleet-patterns`,
  `/v1/expert`, `verifyRepair`, `verifyHypotheses`,
  `getMostInformativeTest`, `getGuidedPhysicalChecks`,
  `createDigitalTwin`, `listDigitalTwins`, `getDigitalTwin`,
  `runDigitalTwinCommand`, `ExpertKnowledge`, `ProcedureViewer`,
  `DigitalTwin.jsx`, `dispatchEvent`, `addEventListener`. (The
  React bundle itself contains `addEventListener` as framework
  internals — the src grep is the meaningful check and is clean.)
- **YAML:** `docker-compose.yml` and `docker/traefik/dynamic.yml`
  parse (11 services incl. `frontend`).
- **NOT run here:** no live gateway smoke (health/ready, `/v1/`
  calls against a running gateway) — no backend is running in this
  VM and no Docker is installed. The static-serving smoke above is
  the limit of what this environment can prove; the gateway smoke
  belongs to the machine that runs `docker compose up --build`.
- **Full backend pytest suite NOT re-run:** this step touches no
  backend code (frontend + compose/Traefik YAML only); the twin
  step's full-suite tally (595 passed / 14 failed / 2 skipped /
  5 errors, all environmental) stands.

## Post-extraction oracle gate — NOT RUN, ready and waiting

**The post-extraction oracle gate was NOT run.** Per Nishant's
standing rule (23 Nov 2026 15:49 IST), no test may run with Muse as
the LLM backend until he is told first — he will have another LLM do
it instead. No mock-server run with model `muse-spark` was started
and no Muse tokens were spent on a gate run in this step.

**The gate is ready and waiting for Nishant's other LLM.** The
frozen Phase 1 gate to re-run after this extraction (the last of the
five): Miller 22/38 (tie accepted for proceeding), OMNIPRO 24/35,
discriminating test 17/17 Miller / 8/9 OMNIPRO, citations 79/79
Miller / 35/35 OMNIPRO. Re-ingest from the PDFs against Postgres
(compose: `docker compose up --build`, then upload via the gateway)
— do NOT dump/restore the SQLite data. Kill tests to include
(already verified at the API level in earlier steps): with Qdrant
stopped, `/v1/query` returns 503 in the standard envelope and
`/ready` returns 503 naming Qdrant, while `/health` still returns
200; with the ingestion API stopped, diagnosis `/ready` returns 503
naming ingestion; with the twin-verifier stopped, turns complete
with verdicts NOT_VERIFIED. Do NOT redo Phase 1 ranking work in the
gate — the tie and the ranking bottleneck are a separate follow-up,
not a blocker for the split. The whole split (gateway, ingestion,
diagnosis-agent, twin-verifier, frontend) is now extracted.
