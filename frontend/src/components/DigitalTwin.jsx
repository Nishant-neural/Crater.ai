import { useEffect, useState } from "react";
import {
  createDemoDigitalTwin, getDigitalTwin, listDigitalTwins, runDigitalTwinCommand,
} from "../api/client";

export default function DigitalTwin({ productId = "", revisionId = "", initialTwinId = "" }) {
  const [twinId, setTwinId] = useState(initialTwinId);
  const [twins, setTwins] = useState([]);
  const [twin, setTwin] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function refresh() {
    try { setTwins(await listDigitalTwins()); } catch (e) { setError(e.message); }
  }
  useEffect(() => { refresh(); }, []);
  useEffect(() => {
    if (!initialTwinId) return;
    setTwinId(initialTwinId);
    getDigitalTwin(initialTwinId).then(setTwin).catch((e) => setError(e.message));
  }, [initialTwinId]);

  async function load() {
    if (!twinId) return;
    setBusy(true); setError("");
    try { setTwin(await getDigitalTwin(twinId)); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  }

  async function demo() {
    setBusy(true); setError("");
    try {
      const result = await createDemoDigitalTwin(productId, revisionId);
      setTwin(result); setTwinId(result.id); await refresh();
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
        <select value={twinId} onChange={e => setTwinId(e.target.value)}>
          <option value="">Select a twin for this machine</option>
          {(twins.filter(t => t.revision_id === revisionId).length ? twins.filter(t => t.revision_id === revisionId) : twins).map(t => <option key={t.id} value={t.id}>{t.name} — {t.status}</option>)}
        </select>
        <button disabled={busy || !twinId} onClick={load}>Load</button>
        <button disabled={busy || !productId || !revisionId} onClick={demo}>Create demo twin</button>
        {twin && <span className="muted">{twin.name} · {twin.status}</span>}
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
