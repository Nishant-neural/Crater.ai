import { useState } from "react";
import ComponentExplorer from "./components/ComponentExplorer";
import ProcedureViewer from "./components/ProcedureViewer";
import DiagramViewer from "./technical-viewer/DiagramViewer";
import "./App.css";

const TABS = { EXPLORER: "explorer", PROCEDURE: "procedure" };

/**
 * Phase 4 — Technical Visualization shell.
 *
 * There's no product/revision picker UI wired up yet (Phases 1-3 don't
 * expose a "browse everything I've ingested" endpoint) — a technician or
 * developer pastes in the revision/procedure id they're working with.
 * The left panel drives which diagram chunk the technical viewer shows;
 * the technical viewer itself never fetches anything unprompted.
 */
export default function App() {
  const [tab, setTab] = useState(TABS.EXPLORER);
  const [revisionId, setRevisionId] = useState("");
  const [procedureId, setProcedureId] = useState("");
  const [activeChunkId, setActiveChunkId] = useState("");
  const [highlightLabels, setHighlightLabels] = useState([]);

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
        <h1>Crater.ai — Technical Visualization</h1>
        <nav className="app__tabs">
          <button className={tab === TABS.EXPLORER ? "active" : ""} onClick={() => setTab(TABS.EXPLORER)}>
            Component Explorer
          </button>
          <button className={tab === TABS.PROCEDURE ? "active" : ""} onClick={() => setTab(TABS.PROCEDURE)}>
            Procedure Viewer
          </button>
        </nav>
      </header>

      <div className="app__body">
        <aside className="app__sidebar">
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
          <DiagramViewer chunkId={activeChunkId} highlightLabels={highlightLabels} />
        </main>
      </div>
    </div>
  );
}
