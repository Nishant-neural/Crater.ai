import { useEffect, useState } from "react";
import ComponentExplorer from "./components/ComponentExplorer";
import ProcedureViewer from "./components/ProcedureViewer";
import DiagramViewer from "./technical-viewer/DiagramViewer";
import ExpertKnowledge from "./components/ExpertKnowledge";
import DigitalTwin from "./components/DigitalTwin";
import SimulationAgent from "./components/SimulationAgent";
import MachineModelViewer from "./components/MachineModelViewer";
import DiagnosticWorkspace from "./components/DiagnosticWorkspace";
import "./App.css";

const TABS = { DIAGNOSTIC: "diagnostic", MODEL: "model", EXPLORER: "explorer", PROCEDURE: "procedure", EXPERT: "expert", TWIN: "twin", SIMULATION: "simulation" };

/**
 * Crater.ai technician workstation. Phase 9 adds the end-to-end machine
 * onboarding and diagnostic flow; the legacy visualization/expert/simulation
 * surfaces remain available as secondary workspaces.
 */
export default function App() {
  const [tab, setTab] = useState(TABS.DIAGNOSTIC);
  const [revisionId, setRevisionId] = useState("");
  const [procedureId, setProcedureId] = useState("");
  const [activeChunkId, setActiveChunkId] = useState("");
  const [highlightLabels, setHighlightLabels] = useState([]);
  const [simulationContext, setSimulationContext] = useState(null);

  useEffect(() => {
    const handler = (event) => {
      setSimulationContext(event.detail || null);
      setRevisionId(event.detail?.revisionId || revisionId);
      setTab(TABS.SIMULATION);
    };
    window.addEventListener("crater-open-simulation", handler);
    return () => window.removeEventListener("crater-open-simulation", handler);
  }, [revisionId]);

  function openAppearance(appearance) {
    setActiveChunkId(appearance.chunk_id);
    setHighlightLabels([appearance.label]);
  }

  function onProcedureFrameChange(frame) {
    if (!frame) {
      setHighlightLabels([]);
      return;
    }
    setActiveChunkId(frame.chunk_id);
    setHighlightLabels(frame.labels);
  }

  return (
    <div className="app">
      <header className="app__header">
        <h1>Crater.ai — Technical Intelligence</h1>
        <nav className="app__tabs">
          <button className={tab === TABS.DIAGNOSTIC ? "active" : ""} onClick={() => setTab(TABS.DIAGNOSTIC)}>
            Diagnose
          </button>
          <button className={tab === TABS.MODEL ? "active" : ""} onClick={() => setTab(TABS.MODEL)}>
            Machine Model
          </button>
          <button className={tab === TABS.EXPLORER ? "active" : ""} onClick={() => setTab(TABS.EXPLORER)}>
            Component Explorer
          </button>
          <button className={tab === TABS.PROCEDURE ? "active" : ""} onClick={() => setTab(TABS.PROCEDURE)}>
            Procedure Viewer
          </button>
          <button className={tab === TABS.EXPERT ? "active" : ""} onClick={() => setTab(TABS.EXPERT)}>
            Capture Rajesh
          </button>
          <button className={tab === TABS.TWIN ? "active" : ""} onClick={() => setTab(TABS.TWIN)}>
            Digital Twin
          </button>
          <button className={tab === TABS.SIMULATION ? "active" : ""} onClick={() => setTab(TABS.SIMULATION)}>
            Simulation Agent
          </button>
        </nav>
      </header>

      <div className="app__body">
        <aside className="app__sidebar">
          {tab === TABS.MODEL && (
            <div className="model-sidebar-note">Canonical model: entities, topology, facts, procedures, failure modes, conflicts and source evidence.</div>
          )}
          {tab === TABS.EXPLORER && (
            <>
              <label>
                Revision ID
                <input value={revisionId} onChange={(e) => setRevisionId(e.target.value.trim())} placeholder="revision uuid" />
              </label>
              <ComponentExplorer revisionId={revisionId} onOpenAppearance={openAppearance} />
            </>
          )}
          {tab === TABS.PROCEDURE && (
            <>
              <label>
                Procedure ID
                <input value={procedureId} onChange={(e) => setProcedureId(e.target.value.trim())} placeholder="procedure uuid" />
              </label>
              <ProcedureViewer procedureId={procedureId} onFrameChange={onProcedureFrameChange} />
            </>
          )}

        </aside>

        <main className="app__main">
          {tab === TABS.DIAGNOSTIC ? <DiagnosticWorkspace onMachineReady={({revisionId}) => { setRevisionId(revisionId); setTab(TABS.MODEL); }} /> : tab === TABS.MODEL ? <MachineModelViewer initialRevisionId={revisionId} /> : tab === TABS.EXPERT ? <ExpertKnowledge /> : tab === TABS.TWIN ? <DigitalTwin /> : tab === TABS.SIMULATION ? <SimulationAgent initialRevisionId={revisionId} diagnosticContext={simulationContext} /> : <DiagramViewer chunkId={activeChunkId} highlightLabels={highlightLabels} />}
        </main>
      </div>
    </div>
  );
}
