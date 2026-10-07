import { useEffect, useMemo, useState } from "react";
import { compileSimulationRevision, getKnowledgeModel, getRevisionSimulation, runSimulationExperiment } from "../api/client";

function pathFor(component, field) { return `components.${component}.${field}`; }

export default function SimulationAgent({ initialRevisionId = "", diagnosticContext = null }) {
  const [revisionId, setRevisionId] = useState(initialRevisionId || "");
  const [model, setModel] = useState(null);
  const [twin, setTwin] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const [selectedComponent, setSelectedComponent] = useState("");
  const [field, setField] = useState("fault");
  const [faultValue, setFaultValue] = useState("true");
  const [interventionValue, setInterventionValue] = useState("false");
  const [assertionPath, setAssertionPath] = useState("");
  const [hypothesis, setHypothesis] = useState(diagnosticContext?.hypothesis || "");

  useEffect(() => { if (initialRevisionId) setRevisionId(initialRevisionId); }, [initialRevisionId]);
  useEffect(() => { if (diagnosticContext?.hypothesis) setHypothesis(diagnosticContext.hypothesis); }, [diagnosticContext]);

  async function load() {
    if (!revisionId) return setError("Select a machine revision first.");
    setBusy(true); setError(""); setResult(null);
    try {
      const [m, t] = await Promise.all([getKnowledgeModel(revisionId), getRevisionSimulation(revisionId).catch(() => null)]);
      setModel(m);
      setTwin(t);
      if (t?.definition?.components?.length && !selectedComponent) setSelectedComponent(t.definition.components[0].id);
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }

  async function compile() {
    if (!revisionId) return;
    setBusy(true); setError(""); setResult(null);
    try {
      const compiled = await compileSimulationRevision(revisionId);
      setTwin({ twin_id: compiled.twin_id, revision_id: revisionId, definition: compiled.definition, snapshot: compiled.snapshot });
      setModel(await getKnowledgeModel(revisionId));
      if (compiled.definition.components?.length) setSelectedComponent(compiled.definition.components[0].id);
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }

  async function runExperiment() {
    if (!twin?.twin_id || !selectedComponent) return setError("Compile/load the simulation and select a component.");
    const fault = faultValue === "true" ? true : faultValue === "false" ? false : faultValue;
    const intervention = interventionValue === "true" ? true : interventionValue === "false" ? false : interventionValue;
    const targetPath = pathFor(selectedComponent, field);
    const assertion = assertionPath.trim() ? [{ path: assertionPath.trim(), expected: true }] : [];
    setBusy(true); setError("");
    try {
      setResult(await runSimulationExperiment(twin.twin_id, {
        name: "Diagnostic hypothesis verification",
        hypothesis: hypothesis || `Test whether ${selectedComponent}.${field} causes the reported symptom.`,
        fault_commands: [{ command: "set_component_state", target: selectedComponent, field, value: fault, reason: "Diagnostic simulation experiment" }],
        intervention_commands: [{ command: "set_component_state", target: selectedComponent, field, value: intervention, reason: "Simulated intervention" }],
        assertions: assertion,
      }));
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }

  const components = twin?.definition?.components || [];
  const relations = twin?.definition?.metadata?.topology || [];
  const selected = components.find(c => c.id === selectedComponent);
  const state = twin?.snapshot?.state || {};
  const derived = twin?.snapshot?.derived || {};

  return <section className="simulation-agent">
    <div className="simulation-header">
      <div><div className="eyebrow">MACHINE MODEL → SIMULATION → DIAGNOSTICS</div><h2>Functional Simulation Workbench</h2><p className="muted">The simulator is compiled from the canonical revision model. It preserves documented topology and state, and never invents physical behaviour.</p></div>
    </div>
    <div className="simulation-toolbar">
      <input placeholder="Revision ID" value={revisionId} onChange={e=>setRevisionId(e.target.value.trim())}/>
      <button disabled={busy || !revisionId} onClick={load}>Load model</button>
      <button className="primary-button" disabled={busy || !revisionId} onClick={compile}>{busy ? "Working…" : "Compile simulation"}</button>
    </div>
    {error && <div className="error">{error}</div>}
    {diagnosticContext && <div className="simulation-diagnostic-link"><b>Diagnostic hypothesis</b><span>{diagnosticContext.hypothesis || "No leading hypothesis supplied."}</span><small>Run a controlled virtual experiment before treating the hypothesis as verified.</small></div>}
    {model?.model && <div className="simulation-summary"><span><b>{model.model.entities?.length || 0}</b> entities</span><span><b>{model.model.relations?.length || 0}</b> relations</span><span><b>{model.model.states?.length || 0}</b> states</span><span><b>{model.model.failure_modes?.length || 0}</b> failure modes</span><span><b>{model.model.procedures?.length || 0}</b> procedures</span></div>}
    {twin && <>
      <div className="simulation-layout">
        <section className="panel simulation-topology">
          <h3>Machine topology</h3>
          <div className="simulation-nodes">
            {components.map(c=><button key={c.id} className={selectedComponent===c.id?"simulation-node selected":"simulation-node"} onClick={()=>setSelectedComponent(c.id)}><b>{c.name}</b><span>{c.component_type}</span></button>)}
          </div>
          <div className="simulation-relations">{relations.slice(0,24).map((r,i)=><div key={r.id || i}>{r.subject_name} <b>→ {r.relation_type} →</b> {r.object_name}</div>)}</div>
        </section>
        <section className="panel simulation-state"><h3>Virtual machine state</h3><pre>{JSON.stringify(state,null,2)}</pre><h4>Derived</h4><pre>{JSON.stringify(derived,null,2)}</pre></section>
      </div>
      <section className="panel simulation-experiment">
        <h3>Verify diagnostic hypothesis</h3>
        <label>Hypothesis<textarea value={hypothesis} onChange={e=>setHypothesis(e.target.value)} rows={2}/></label>
        <div className="simulation-form-grid">
          <label>Component<select value={selectedComponent} onChange={e=>setSelectedComponent(e.target.value)}>{components.map(c=><option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
          <label>State / fault field<input value={field} onChange={e=>setField(e.target.value)}/></label>
          <label>Fault value<input value={faultValue} onChange={e=>setFaultValue(e.target.value)}/></label>
          <label>Intervention value<input value={interventionValue} onChange={e=>setInterventionValue(e.target.value)}/></label>
          <label>Expected state path <input placeholder="optional: components.X.running" value={assertionPath} onChange={e=>setAssertionPath(e.target.value)}/></label>
        </div>
        <button className="primary-button" disabled={busy || !selected} onClick={runExperiment}>Run isolated experiment</button>
      </section>
    </>}
    {result && <div className="simulation__grid"><div className="panel"><h3>Verdict</h3><div className={result.validated?"badge badge--ok":"badge"}>{result.verdict}</div><p>{result.explanation}</p>{result.warnings?.map((w,i)=><p className="muted" key={i}>{w}</p>)}</div><div className="panel"><h3>State changes</h3><pre>{JSON.stringify(result.state_changes,null,2)}</pre></div><div className="panel"><h3>Fault trace</h3><pre>{result.fault_trace.join("\n")}</pre></div><div className="panel"><h3>Intervention trace</h3><pre>{result.intervention_trace.join("\n")}</pre></div></div>}
  </section>;
}
