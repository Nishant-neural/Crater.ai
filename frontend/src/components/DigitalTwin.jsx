import { useEffect, useState } from "react";
import {
  createDemoDigitalTwin, getDigitalTwin, listDigitalTwins, runDigitalTwinCommand,
} from "../api/client";

const initial = { productId: "", revisionId: "", twinId: "" };

export default function DigitalTwin() {
  const [ids, setIds] = useState(initial);
  const [twins, setTwins] = useState([]);
  const [twin, setTwin] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function refresh() {
    try { setTwins(await listDigitalTwins()); } catch (e) { setError(e.message); }
  }
  useEffect(() => { refresh(); }, []);

  async function load() {
    if (!ids.twinId) return;
    setTwin(await getDigitalTwin(ids.twinId));
  }

  async function demo() {
    setBusy(true); setError("");
    try {
      const result = await createDemoDigitalTwin(ids.productId, ids.revisionId);
      setTwin(result); setIds((x) => ({...x, twinId: result.id})); await refresh();
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }

  async function command(payload) {
    if (!twin) return;
    setBusy(true); setError("");
    try { setTwin(await runDigitalTwinCommand(twin.id, payload)); await refresh(); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  }

  const state = twin?.snapshot?.state || {};
  const c = state.components || {};
  const s = state.signals || {};

  return (
    <section className="twin">
      <h2>Functional Digital Twin</h2>
      <p className="muted">Deterministic virtual machine state model. Simulation is not proof of physical safety.</p>
      <div className="twin__setup">
        <input placeholder="Product ID" value={ids.productId} onChange={e => setIds({...ids, productId:e.target.value.trim()})}/>
        <input placeholder="Revision ID" value={ids.revisionId} onChange={e => setIds({...ids, revisionId:e.target.value.trim()})}/>
        <button disabled={busy || !ids.productId || !ids.revisionId} onClick={demo}>Create demo twin</button>
        <select value={ids.twinId} onChange={e => setIds({...ids, twinId:e.target.value})}>
          <option value="">Select existing twin</option>
          {twins.map(t => <option key={t.id} value={t.id}>{t.name} — {t.id.slice(0,8)}</option>)}
        </select>
        <button disabled={busy || !ids.twinId} onClick={load}>Load</button>
      </div>
      {error && <div className="error">{error}</div>}
      {twin && (
        <>
          <div className="twin__controls">
            <button disabled={busy} onClick={() => command({command:"set_signal",target:"start_command",value:true})}>Start command ON</button>
            <button disabled={busy} onClick={() => command({command:"set_signal",target:"start_command",value:false})}>Start command OFF</button>
            <button disabled={busy} onClick={() => command({command:"set_component_state",target:"door_interlock",field:"closed",value:false})}>Open door</button>
            <button disabled={busy} onClick={() => command({command:"set_component_state",target:"door_interlock",field:"closed",value:true})}>Close door</button>
            <button disabled={busy} onClick={() => command({command:"set_component_state",target:"x12",field:"connected",value:false,reason:"Injected open connector fault"})}>Disconnect X12</button>
            <button disabled={busy} onClick={() => command({command:"set_component_state",target:"x12",field:"connected",value:true})}>Reconnect X12</button>
            <button disabled={busy} onClick={() => command({command:"reset"})}>Reset</button>
          </div>
          <div className="twin__grid">
            <div className="panel"><h3>Signals</h3><pre>{JSON.stringify(s,null,2)}</pre></div>
            <div className="panel"><h3>Components</h3><pre>{JSON.stringify(c,null,2)}</pre></div>
            <div className="panel"><h3>Derived machine state</h3><pre>{JSON.stringify(twin.snapshot.derived,null,2)}</pre></div>
            <div className="panel"><h3>Execution trace</h3><pre>{(twin.snapshot.trace || []).slice(-12).join("\n") || "No transitions yet."}</pre></div>
          </div>
        </>
      )}
    </section>
  );
}
