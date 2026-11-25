// Frontend smoke tests — static, no deps, run with `npm test`
// (node --test). There was no frontend test runner before this step
// (frontend audit OQ4); this suite pins the Phase 3 frontend
// extraction: no calls to deleted endpoints, no CUT components, the
// runtime-injected base URL, and the container wiring. It does not
// render the UI and makes no LLM or network call.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const repo = join(root, "..");
const read = (rel) => readFileSync(join(root, rel), "utf8");
const src = (rel) => read(join("src", rel));

const DELETED_ENDPOINTS = [
  "verify-repair", "verify-hypotheses", "early-tournament",
  "upstream-walk", "next-test", "guided-checks", "fleet-patterns",
  "/v1/expert", "/expert/",
];
const DELETED_CLIENT_FNS = [
  "verifyRepair", "verifyHypotheses", "getMostInformativeTest",
  "getGuidedPhysicalChecks", "createDigitalTwin", "listDigitalTwins",
  "getDigitalTwin", "runDigitalTwinCommand",
];

test("no calls to deleted §2.5 endpoints or expert routes remain in src", () => {
  for (const f of ["src/api/client.js", "src/api/diagnostics.js", "src/components/DiagnosticWorkspace.jsx", "src/App.jsx"]) {
    const text = read(f);
    for (const needle of DELETED_ENDPOINTS) assert(!text.includes(needle), `${f} still mentions ${needle}`);
    for (const fn of DELETED_CLIENT_FNS) assert(!text.includes(fn), `${f} still mentions ${fn}`);
  }
});

test("no calls to deleted §2.5 endpoints remain in the built bundle", () => {
  const html = readFileSync(join(root, "dist/index.html"), "utf8");
  const asset = html.match(/assets\/[^"]+\.js/)[0];
  const bundle = readFileSync(join(root, "dist", asset), "utf8");
  for (const needle of DELETED_ENDPOINTS) assert(!bundle.includes(needle), `built bundle still mentions ${needle}`);
});

test("CUT components are gone and unreferenced", () => {
  for (const f of ["ExpertKnowledge.jsx", "ProcedureViewer.jsx", "DigitalTwin.jsx"]) {
    assert(!existsSync(join(root, "src/components", f)), `${f} still exists`);
  }
  const all = ["src/App.jsx", "src/components/DiagnosticWorkspace.jsx", "src/components/TwinWorkspace.jsx", "src/api/client.js"].map(read).join("\n");
  for (const name of ["ExpertKnowledge", "ProcedureViewer", "DigitalTwin.jsx"]) assert(!all.includes(name), `still references ${name}`);
  assert(!read("src/App.css").includes(".expert"), "App.css still has .expert selectors");
  assert(read("src/App.css").includes(".twin"), "App.css lost the .twin selectors shared with kept VisualTwin.jsx");
});

test("no CustomEvent bus remains; deep link is a direct prop", () => {
  for (const f of ["src/App.jsx", "src/components/DiagnosticWorkspace.jsx"]) {
    const text = read(f);
    assert(!text.includes("dispatchEvent"), `${f} still dispatches CustomEvents`);
    assert(!text.includes("addEventListener"), `${f} still listens for CustomEvents`);
  }
  assert(read("src/components/DiagnosticWorkspace.jsx").includes("onOpenSimulation"), "DiagnosticWorkspace lost its onOpenSimulation prop");
});

test("base URL is runtime-injected, not build-time hardcoded", () => {
  const client = src("api/client.js");
  assert(client.includes("__CRATER_CONFIG__"), "client.js does not read the runtime config");
  assert(client.includes("VITE_API_BASE"), "client.js lost the local-dev VITE_API_BASE fallback");
  assert(read("public/config.js").includes("__CRATER_CONFIG__"), "public/config.js missing");
  assert(read("index.html").includes('<script src="/config.js"></script>'), "index.html does not load /config.js before the bundle");
  assert(readFileSync(join(repo, "docker/frontend/config.js"), "utf8").includes('apiBase: "/v1"'), "container runtime config does not point at the gateway /v1");
});

test("frontend talks only to the gateway: no backend service host in src or container config", () => {
  const haystack = ["src/api/client.js", "src/api/machines.js", "src/api/diagnostics.js", "src/api/knowledge.js", "src/api/revisions.js"].map(read).join("\n")
    + readFileSync(join(repo, "docker/frontend/config.js"), "utf8") + readFileSync(join(repo, "docker/frontend/Dockerfile"), "utf8");
  for (const host of ["ingestion:8000", "diagnosis-agent", "twin-verifier", "qdrant:", "postgres:", "redis:"]) {
    assert(!haystack.includes(host), `frontend config references backend host ${host}`);
  }
  assert(readFileSync(join(repo, "docker-compose.yml"), "utf8").includes("frontend:"), "compose has no frontend service");
});

test("live /respond flow is the only diagnosis write path", () => {
  const ws = src("components/DiagnosticWorkspace.jsx");
  assert(ws.includes('respondDiagnostic'), "DiagnosticWorkspace no longer calls /respond");
  assert(ws.includes("early_tournament"), "DiagnosticWorkspace does not read the tournament from the timeline");
  assert(ws.includes("discriminating_test"), "DiagnosticWorkspace does not surface the discriminating test");
  assert(!ws.includes("Motor M1 is overheating"), "hardcoded symptom default is back");
  assert(ws.includes("observations"), "no observations input at diagnosis start");
});

test("slice caps are gone (audit P-08)", () => {
  for (const [f, cap] of [
    ["src/components/DiagnosticWorkspace.jsx", "slice(0, 18)"],
    ["src/components/MachineModelViewer.jsx", "slice(0, 24)"],
    ["src/components/SimulationAgent.jsx", "slice(0, 24)"],
  ]) assert(!read(f).includes(cap), `${f} still has ${cap}`);
});
