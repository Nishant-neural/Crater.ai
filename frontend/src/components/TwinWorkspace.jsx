import { useEffect, useRef, useState } from "react";
import {
  approveTwin, compileSimulationRevision, getRevisionSimulation,
  integrateKnowledge,
} from "../api/client";
import { getMachineModel } from "../api/knowledge";
import DigitalTwin from "./DigitalTwin";
import SimulationAgent from "./SimulationAgent";
import VisualTwin from "./VisualTwin";

const SUB_VIEWS = [
  ["overview", "Overview"],
  ["simulate", "Simulate"],
  ["visual", "Visual"],
  ["rules", "Rules"],
];

function normalizeTwin(raw) {
  if (!raw) return null;
  return {
    twin_id: raw.twin_id || raw.id,
    revision_id: raw.revision_id,
    definition: raw.definition,
    snapshot: raw.snapshot,
    status: raw.status || raw.snapshot?.status || null,
    warnings: raw.warnings || raw.definition?.metadata?.compiler_warnings || [],
  };
}

function RulesView({ definition }) {
  const transitions = definition?.transitions || [];
  const seed = definition?.metadata?.expert_seed || null;
  const warnings = definition?.metadata?.compiler_warnings || [];
  return <div className="twin-rules">
    {seed && <section className="model-panel"><h3>Expert seed — {seed.seed_name || seed.seed_id}</h3>
      <p className="muted">Rules applied: {(seed.rules_applied || []).map((r) => typeof r === "string" ? r : r.name || r.id || JSON.stringify(r)).join(", ") || "none"}</p>
      {(seed.skipped || []).length > 0 && <p className="muted">Skipped — ambiguous names are never guessed: {seed.skipped.map((s) => `${s.kind || "rule"}: ${s.reason || s.name || ""}`).join(" · ")}</p>}
    </section>}
    {warnings.length > 0 && <section className="model-panel"><h3>Compiler warnings</h3>{warnings.map((w, i) => <p key={i} className="muted">{w}</p>)}</section>}
    {transitions.length === 0 && <div className="model-empty">No simulation rules yet. This twin is topology and state aware, but no explicit simulation rule was extracted or seeded for it, so nothing can change state.</div>}
    {transitions.map((t) => <article className="model-panel" key={t.id}>
      <div className="model-card-head"><h3>{t.name}</h3><span className="model-badge">{t.id}</span></div>
      {t.description && <p className="muted">{t.description}</p>}
      <div className="model-two-col">
        <div><h4>Conditions</h4><pre className="model-json">{JSON.stringify(t.conditions, null, 2)}</pre></div>
        <div><h4>Effects</h4><pre className="model-json">{JSON.stringify(t.effects, null, 2)}</pre></div>
      </div>
      {t.safety_notes?.length > 0 && <p className="muted">Safety: {t.safety_notes.join(" · ")}</p>}
      {t.evidence?.length > 0 && <p className="muted">Evidence: {t.evidence.map((e) => `${e.fact || e.source_id || ""}${e.page ? ` · p.${e.page}` : ""}`).join(" · ")}</p>}
    </article>)}
  </div>;
}

export default function TwinWorkspace({ productId, revisionId, product, revision, subView = "overview", onSubViewChange, diagnosticContext, visualContext, autoCompileRequest = 0, onAutoCompileDone }) {
  const [twin, setTwin] = useState(null);
  const [model, setModel] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [twinRefreshKey, setTwinRefreshKey] = useState(0);
  const consumedCompileRequest = useRef(0);

  const machineName = product ? `${product.manufacturer} ${product.family} · ${product.model}` : "Machine";
  const revisionLabel = revision?.label || "";

  async function load() {
    const [t, m] = await Promise.all([
      getRevisionSimulation(revisionId).catch(() => null),
      getMachineModel(revisionId).catch(() => null),
    ]);
    setTwin(normalizeTwin(t));
    setModel(m);
  }
  useEffect(() => { if (revisionId) load(); }, [revisionId]); // eslint-disable-line react-hooks/exhaustive-deps

  async function compile() {
    if (!revisionId) return null;
    setBusy(true); setError("");
    try {
      const c = await compileSimulationRevision(revisionId);
      const next = normalizeTwin(c);
      setTwin(next);
      setTwinRefreshKey((k) => k + 1);
      const m = await getMachineModel(revisionId).catch(() => null);
      if (m) setModel(m);
      return next;
    } catch (e) { setError(e.message || "Compile failed."); return null; }
    finally { setBusy(false); }
  }

  async function approve() {
    if (!twin?.twin_id) return;
    setBusy(true); setError("");
    try {
      await approveTwin(twin.twin_id);
      await load();
      setTwinRefreshKey((k) => k + 1);
    } catch (e) { setError(e.message || "Approve failed."); }
    finally { setBusy(false); }
  }

  async function buildKnowledge() {
    setBusy(true); setError("");
    try { await integrateKnowledge(revisionId); await load(); }
    catch (e) { setError(e.message || "Integration failed."); }
    finally { setBusy(false); }
  }

  // Guided flow: Machines → "Compile twin" opens this workspace and compiles,
  // then drops the user into the Visual sub-view with the twin loaded.
  useEffect(() => {
    if (!autoCompileRequest || autoCompileRequest === consumedCompileRequest.current) return;
    consumedCompileRequest.current = autoCompileRequest;
    (async () => {
      const next = await compile();
      if (next) onSubViewChange?.("visual");
      onAutoCompileDone?.();
    })();
  }, [autoCompileRequest]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!revisionId) return <div className="twin-workspace"><div className="model-empty">Select a machine on the Machines view first.</div></div>;

  const definition = twin?.definition || null;
  const components = definition?.components || [];
  const topology = definition?.metadata?.topology || [];
  const transitions = definition?.transitions || [];
  const warnings = twin?.warnings || [];
  const seed = definition?.metadata?.expert_seed || null;
  const hasModel = !!model;

  let primary = null;
  if (!twin && !hasModel) primary = <button className="primary-button" disabled title="Compile is disabled until the machine model is integrated">Compile twin</button>;
  else if (!twin) primary = <button className="primary-button" onClick={compile} disabled={busy}>{busy ? "Compiling…" : "Compile twin"}</button>;
  else if (twin.status === "draft") primary = <button className="primary-button" onClick={approve} disabled={busy}>{busy ? "Working…" : "Approve twin"}</button>;

  return <div className="twin-workspace">
    <header className="view-header">
      <div>
        <div className="eyebrow">DIGITAL TWIN</div>
        <h2>{machineName}{revisionLabel ? ` — ${revisionLabel}` : ""}</h2>
        <p className="muted">Compiled from the canonical machine model. The twin replays only behaviour written explicitly in the manual or the expert seed.</p>
      </div>
      <div className="view-header__actions">
        {twin && <span className={`status-pill ${twin.status === "active" ? "ok" : "warn"}`}>{twin.status}</span>}
        {primary}
        {twin && <button className="secondary-button" onClick={compile} disabled={busy}>Recompile</button>}
      </div>
    </header>
    {!twin && !hasModel && <p className="muted">Compile is disabled until the machine model is integrated. <button className="quiet-button" onClick={buildKnowledge} disabled={busy}>Build machine knowledge</button></p>}
    {error && <div className="diag-error">{error}</div>}
    <nav className="twin-subnav">
      {SUB_VIEWS.map(([id, label]) => <button key={id} className={subView === id ? "active" : ""} onClick={() => onSubViewChange?.(id)}>{label}</button>)}
    </nav>

    {subView === "overview" && <>
      <div className="twin-stats">
        <div><span>STATUS</span><b>{twin ? twin.status : "No twin"}</b></div>
        <div><span>COMPONENTS</span><b>{components.length}</b></div>
        <div><span>RELATIONS</span><b>{topology.length}</b></div>
        <div><span>TRANSITIONS</span><b>{transitions.length}</b></div>
        <div><span>MODEL VERSION</span><b>{model ? `v${model.version}` : "—"}</b></div>
      </div>
      {warnings.length > 0 && <section className="model-panel"><h3>Compiler warnings</h3>{warnings.map((w, i) => <p key={i} className="muted">{w}</p>)}</section>}
      {seed && <section className="model-panel"><h3>Expert seed — {seed.seed_name || seed.seed_id}</h3>
        <p className="muted">Rules applied: {(seed.rules_applied || []).map((r) => typeof r === "string" ? r : r.name || r.id || JSON.stringify(r)).join(", ") || "none"} · States applied: {(seed.states_applied || []).length} · Skipped: {(seed.skipped || []).length}</p>
      </section>}
      <div className="safety-note"><b>Simulation is not proof of physical safety.</b><span>The twin only replays behaviour written explicitly in the manual or the expert seed. It never invents physical behaviour.</span></div>
      <DigitalTwin productId={productId} revisionId={revisionId} initialTwinId={twin?.twin_id || ""} />
    </>}

    {subView === "simulate" && (twin ? <SimulationAgent initialRevisionId={revisionId} diagnosticContext={diagnosticContext} refreshKey={twinRefreshKey} /> : <div className="model-empty">No twin compiled yet. <button className="primary-button" onClick={compile} disabled={busy || !hasModel}>Compile twin</button>{!hasModel && <p className="muted">Compile is disabled until the machine model is integrated.</p>}</div>)}

    {subView === "visual" && (twin ? <VisualTwin initialRevisionId={revisionId} visualContext={visualContext} refreshKey={twinRefreshKey} /> : <div className="model-empty">No twin compiled yet. <button className="primary-button" onClick={compile} disabled={busy || !hasModel}>Compile twin</button>{!hasModel && <p className="muted">Compile is disabled until the machine model is integrated.</p>}</div>)}

    {subView === "rules" && (twin ? <RulesView definition={definition} /> : <div className="model-empty">No twin compiled yet — compile the twin to see its rules.</div>)}
  </div>;
}
