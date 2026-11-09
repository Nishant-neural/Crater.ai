import { useEffect, useState } from "react";
import { listMachines, listRevisions } from "./api/machines";
import { getRevisionSimulation } from "./api/client";
import Machines from "./components/Machines";
import DiagnosticWorkspace from "./components/DiagnosticWorkspace";
import MachineModelViewer from "./components/MachineModelViewer";
import TwinWorkspace from "./components/TwinWorkspace";
import "./App.css";

const VIEWS = { MACHINES: "machines", DIAGNOSTIC: "diagnostic", MODEL: "model", TWIN: "twin" };

/**
 * Crater.ai technician workstation (v11).
 * Four destinations, one machine context: Machines (home + onboarding),
 * Diagnose, Machine Model, and one Twin workspace (Overview / Simulate /
 * Visual / Rules). The machine context bar under the header always shows
 * which machine the current view is about — no view ever asks for a UUID.
 */
export default function App() {
  const [view, setView] = useState(VIEWS.MACHINES);
  const [productId, setProductId] = useState("");
  const [revisionId, setRevisionId] = useState("");
  const [twinSubView, setTwinSubView] = useState("overview");
  const [simulationContext, setSimulationContext] = useState(null);
  const [visualContext, setVisualContext] = useState(null);
  const [compileRequest, setCompileRequest] = useState(0);
  const [machines, setMachines] = useState([]);
  const [revisions, setRevisions] = useState([]);
  const [twinStatus, setTwinStatus] = useState(null);

  useEffect(() => { listMachines().then((rows) => setMachines(rows || [])).catch(() => {}); }, []);
  useEffect(() => {
    if (!productId) { setRevisions([]); return; }
    listRevisions(productId).then((rows) => setRevisions(rows || [])).catch(() => {});
  }, [productId]);
  useEffect(() => {
    if (!revisionId) { setTwinStatus(null); return; }
    getRevisionSimulation(revisionId)
      .then((t) => setTwinStatus(t?.snapshot?.status || null))
      .catch(() => setTwinStatus(null));
  }, [revisionId]);

  // Deep links from the diagnostic workspace: verification and tournament
  // playback route into the Twin workspace instead of a separate tab.
  useEffect(() => {
    const handler = (event) => {
      setSimulationContext(event.detail || null);
      if (event.detail?.revisionId) setRevisionId(event.detail.revisionId);
      setTwinSubView("simulate");
      setView(VIEWS.TWIN);
    };
    window.addEventListener("crater-open-simulation", handler);
    return () => window.removeEventListener("crater-open-simulation", handler);
  }, []);
  useEffect(() => {
    const openVisual = (event) => {
      setVisualContext(event.detail || null);
      if (event.detail?.revisionId) setRevisionId(event.detail.revisionId);
      setTwinSubView("visual");
      setView(VIEWS.TWIN);
    };
    window.addEventListener("crater-open-visual-twin", openVisual);
    return () => window.removeEventListener("crater-open-visual-twin", openVisual);
  }, []);

  const selectedProduct = machines.find((m) => m.id === productId) || null;
  const selectedRevision = revisions.find((r) => r.id === revisionId) || null;
  const machineLabel = selectedProduct
    ? `${selectedProduct.manufacturer} · ${selectedProduct.family} · ${selectedProduct.model}`
    : "";
  const contextLabel = machineLabel
    ? `${machineLabel}${selectedRevision ? ` — ${selectedRevision.label}` : ""}`
    : "";

  function selectMachine({ productId: pid, revisionId: rid }) {
    if (pid) setProductId(pid);
    if (rid) setRevisionId(rid);
  }
  // Guided post-ingestion flow: Machines → "Compile twin" opens the Twin
  // workspace and compiles; the workspace drops the user into Visual.
  function compileTwinFor({ productId: pid, revisionId: rid }) {
    selectMachine({ productId: pid, revisionId: rid });
    setTwinSubView("overview");
    setView(VIEWS.TWIN);
    setCompileRequest((n) => n + 1);
  }
  function openTwinFor({ productId: pid, revisionId: rid }) {
    selectMachine({ productId: pid, revisionId: rid });
    setTwinSubView("overview");
    setView(VIEWS.TWIN);
  }

  const needsMachine = !revisionId;

  return (
    <div className="app">
      <header className="app__header">
        <h1>Crater.ai — Technical Intelligence</h1>
        <nav className="app__tabs">
          <button className={view === VIEWS.MACHINES ? "active" : ""} onClick={() => setView(VIEWS.MACHINES)}>
            Machines
          </button>
          <button className={view === VIEWS.DIAGNOSTIC ? "active" : ""} onClick={() => setView(VIEWS.DIAGNOSTIC)}>
            Diagnose
          </button>
          <button className={view === VIEWS.MODEL ? "active" : ""} disabled={needsMachine} title={needsMachine ? "Select a machine on the Machines view first" : ""} onClick={() => setView(VIEWS.MODEL)}>
            Machine Model
          </button>
          <button className={view === VIEWS.TWIN ? "active" : ""} disabled={needsMachine} title={needsMachine ? "Select a machine on the Machines view first" : ""} onClick={() => setView(VIEWS.TWIN)}>
            Twin
          </button>
        </nav>
      </header>

      <div className="machine-context-bar">
        {revisionId ? <>
          <span className="machine-context-bar__machine">{contextLabel || "Machine"}</span>
          <span className="machine-context-bar__pill">Twin: {twinStatus || "not compiled"}</span>
          <button className="machine-context-bar__switch" onClick={() => setView(VIEWS.MACHINES)}>Switch machine</button>
        </> : <span className="muted">No machine selected — pick one on the Machines view to diagnose, inspect the model, or open the twin.</span>}
      </div>

      <div className="app__body">
        <main className="app__main">
          {view === VIEWS.MACHINES && <Machines
            onSelectMachine={selectMachine}
            onCompileTwin={compileTwinFor}
            onOpenTwin={openTwinFor}
            onDiagnose={({ productId: pid, revisionId: rid }) => { selectMachine({ productId: pid, revisionId: rid }); setView(VIEWS.DIAGNOSTIC); }}
            onViewModel={({ productId: pid, revisionId: rid }) => { selectMachine({ productId: pid, revisionId: rid }); setView(VIEWS.MODEL); }}
          />}
          {view === VIEWS.DIAGNOSTIC && <DiagnosticWorkspace
            initialProductId={productId}
            initialRevisionId={revisionId}
            onMachineReady={({ productId: pid, revisionId: rid }) => { selectMachine({ productId: pid, revisionId: rid }); setView(VIEWS.MACHINES); }}
          />}
          {view === VIEWS.MODEL && <MachineModelViewer productId={productId} revisionId={revisionId} onSelectMachine={selectMachine} />}
          {view === VIEWS.TWIN && <TwinWorkspace
            productId={productId}
            revisionId={revisionId}
            product={selectedProduct}
            revision={selectedRevision}
            subView={twinSubView}
            onSubViewChange={setTwinSubView}
            diagnosticContext={simulationContext}
            visualContext={visualContext}
            autoCompileRequest={compileRequest}
            onAutoCompileDone={() => setCompileRequest(0)}
          />}
        </main>
      </div>
    </div>
  );
}
