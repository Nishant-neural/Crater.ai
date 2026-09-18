import { useState } from "react";
import { runSimulationExperiment, runSimulationHypotheses } from "../api/client";

const x12Experiment = {
  name: "X12 connector recovery",
  hypothesis: "An open X12 connector causes the motor to stop.",
  fault_commands: [{command:"set_component_state", target:"x12", field:"connected", value:false, reason:"Simulated open connector"}],
  intervention_commands: [{command:"set_component_state", target:"x12", field:"connected", value:true}],
  assertions: [{path:"components.motor.running", expected:true}],
};

const doorExperiment = {
  name: "Door interlock recovery",
  hypothesis: "An open door interlock prevents the motor from running.",
  fault_commands: [{command:"set_signal", target:"start_command", value:true}, {command:"set_component_state", target:"door_interlock", field:"closed", value:false}],
  intervention_commands: [{command:"set_component_state", target:"door_interlock", field:"closed", value:true}],
  assertions: [{path:"components.motor.running", expected:true}],
};

export default function SimulationAgent() {
  const [twinId, setTwinId] = useState("");
  const [result, setResult] = useState(null);
  const [batch, setBatch] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function experiment(payload) {
    if (!twinId) return setError("Enter a digital twin ID first.");
    setBusy(true); setError(""); setBatch(null);
    try { setResult(await runSimulationExperiment(twinId, payload)); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  }

  async function compare() {
    if (!twinId) return setError("Enter a digital twin ID first.");
    setBusy(true); setError(""); setResult(null);
    try { setBatch(await runSimulationHypotheses(twinId, {experiments:[x12Experiment, doorExperiment]})); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  }

  return (
    <section className="simulation-agent">
      <h2>Simulation Agent</h2>
      <p className="muted">Run isolated fault → intervention experiments against a digital twin. The persisted twin is never mutated.</p>
      <div className="twin__setup">
        <input placeholder="Digital Twin ID" value={twinId} onChange={e => setTwinId(e.target.value.trim())}/>
        <button disabled={busy} onClick={() => experiment(x12Experiment)}>Test X12 repair</button>
        <button disabled={busy} onClick={() => experiment(doorExperiment)}>Test door repair</button>
        <button disabled={busy} onClick={compare}>Compare hypotheses</button>
      </div>
      {error && <div className="error">{error}</div>}
      {result && <ExperimentResult result={result}/>}
      {batch && (
        <div className="panel">
          <h3>Hypothesis comparison</h3>
          {batch.results.map((r) => (
            <div className="simulation-result" key={r.name}>
              <strong>{r.name}</strong>
              <span className={r.validated ? "badge badge--ok" : "badge"}>{r.verdict}</span>
              <p>{r.hypothesis}</p>
              <p className="muted">{r.explanation}</p>
              {r.checks.map((c) => <div key={c.path}>{c.path}: {JSON.stringify(c.actual)} → expected {JSON.stringify(c.expected)} {c.passed ? "✓" : "✗"}</div>)}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function ExperimentResult({ result }) {
  return (
    <div className="simulation__grid">
      <div className="panel">
        <h3>Verdict</h3>
        <div className={result.validated ? "badge badge--ok" : "badge"}>{result.verdict}</div>
        <p>{result.explanation}</p>
        {result.checks.map(c => <div key={c.path}>{c.path}: {JSON.stringify(c.actual)} → expected {JSON.stringify(c.expected)} {c.passed ? "✓" : "✗"}</div>)}
      </div>
      <div className="panel"><h3>State changes</h3><pre>{JSON.stringify(result.state_changes, null, 2)}</pre></div>
      <div className="panel"><h3>Fault trace</h3><pre>{result.fault_trace.join("\n") || "No fault commands."}</pre></div>
      <div className="panel"><h3>Intervention trace</h3><pre>{result.intervention_trace.join("\n") || "No intervention commands."}</pre></div>
    </div>
  );
}
