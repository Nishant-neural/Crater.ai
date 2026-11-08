import { useEffect, useMemo, useRef, useState } from "react";
import { getRevisionSimulation, getTwinVisual } from "../api/client";

/**
 * Visual twin: the compiled twin drawn from its real relations, animated
 * from an experiment trace. Nodes are twin components, edges are the
 * canonical relations (nothing is inferred: a relation says "connected",
 * never "fault propagates"), and playback steps through the three states
 * an experiment already returns -- baseline, fault, repair -- highlighting
 * exactly which components changed at each step. With no playback the
 * view shows the twin's current state.
 *
 * Opened from the diagnostic workspace's verification panel (which passes
 * the verified-repair result as `visualContext.playback`), or standalone
 * by revision id.
 */
const PHASES = [
  { key: "baseline", label: "Baseline", stateKey: "baseline_state", traceKey: null },
  { key: "fault", label: "Fault injected", stateKey: "fault_state", traceKey: "fault_trace" },
  { key: "repair", label: "Repair applied", stateKey: "final_state", traceKey: "intervention_trace" },
];

function stateLines(state) {
  return Object.entries(state || {}).map(([k, v]) => `${k}: ${String(v)}`);
}

function changedIds(prev, next) {
  const out = new Set();
  const keys = new Set([...Object.keys(prev || {}), ...Object.keys(next || {})]);
  for (const id of keys) {
    if (JSON.stringify(prev?.[id] ?? null) !== JSON.stringify(next?.[id] ?? null)) out.add(id);
  }
  return out;
}

export default function VisualTwin({ initialRevisionId, visualContext }) {
  const [revisionId, setRevisionId] = useState(initialRevisionId || "");
  const [visual, setVisual] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [selectedId, setSelectedId] = useState(null);
  const [phaseIdx, setPhaseIdx] = useState(0);
  const [playing, setPlaying] = useState(false);
  const playTimer = useRef(null);

  const playback = visualContext?.playback || null;

  useEffect(() => { if (initialRevisionId) setRevisionId(initialRevisionId); }, [initialRevisionId]);
  useEffect(() => { if (visualContext?.revisionId) setRevisionId(visualContext.revisionId); }, [visualContext]);

  useEffect(() => { return () => { if (playTimer.current) clearTimeout(playTimer.current); }; }, []);

  async function load() {
    if (!revisionId) return;
    setBusy(true); setError(""); setVisual(null); setSelectedId(null); setPhaseIdx(0); setPlaying(false);
    try {
      const twin = await getRevisionSimulation(revisionId);
      setVisual(await getTwinVisual(twin.twin_id));
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  useEffect(() => { if (revisionId) load(); }, [revisionId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Playback: advance one phase per tick until the repair phase is shown.
  useEffect(() => {
    if (!playing) return;
    if (phaseIdx >= PHASES.length - 1) { setPlaying(false); return; }
    playTimer.current = setTimeout(() => setPhaseIdx((i) => Math.min(i + 1, PHASES.length - 1)), 1400);
    return () => { if (playTimer.current) clearTimeout(playTimer.current); };
  }, [playing, phaseIdx]);

  const phase = PHASES[phaseIdx];
  // Phase state: the experiment's component states for this phase; fall
  // back to the twin's live state when there is no playback.
  const phaseState = useMemo(() => {
    if (!playback) return null;
    return playback[phase.stateKey]?.components || playback[phase.stateKey] || null;
  }, [playback, phase]);
  const prevPhaseState = useMemo(() => {
    if (!playback || phaseIdx === 0) return null;
    const prev = PHASES[phaseIdx - 1];
    return playback[prev.stateKey]?.components || playback[prev.stateKey] || null;
  }, [playback, phaseIdx]);
  const changed = useMemo(() => {
    if (!playback || !phaseState) return new Set();
    const prev = prevPhaseState || visual?.nodes?.reduce((acc, n) => ({ ...acc, [n.id]: n.initial_state }), {}) || {};
    return changedIds(prev, phaseState);
  }, [playback, phaseState, prevPhaseState, visual]);

  const nodes = visual?.nodes || [];
  const selected = nodes.find((n) => n.id === selectedId) || null;
  const selectedTransitions = (visual?.transitions || []).filter((t) =>
    Object.keys(t.conditions || {}).some((p) => p.startsWith(`${selected?.id}.`) || p.startsWith(`${selected?.name}.`)) ||
    Object.keys(t.effects || {}).some((p) => p.startsWith(`${selected?.id}.`) || p.startsWith(`${selected?.name}.`)));
  const nodeState = (n) => (phaseState ? (phaseState[n.id] || n.initial_state) : n.state);

  return (
    <section className="twin">
      <h2>Visual Twin</h2>
      <p className="muted">Drawn from the twin&rsquo;s real relations. Simulation is not proof of physical safety.</p>
      <div className="twin__setup">
        <input placeholder="Revision ID" value={revisionId} onChange={(e) => setRevisionId(e.target.value.trim())} />
        <button disabled={busy || !revisionId} onClick={load}>Load twin</button>
        {visual && <span className="muted">{visual.name} · {visual.status} · {nodes.length} components · {visual.edges.length} relations · {visual.transitions.length} transitions</span>}
      </div>
      {error && <div className="error">{error}</div>}
      {visual?.warnings?.length > 0 && <div className="panel"><b>Compiler warnings</b>{visual.warnings.map((w, i) => <p key={i}>{w}</p>)}</div>}
      {visual && visual.transitions.length === 0 && (
        <div className="panel"><b>No simulation rules yet</b><p>This twin is topology and state aware, but no explicit simulation rule was extracted or seeded for it, so nothing can change state. Compile a revision whose model has simulation rules, or use the expert-seeded OMNIPRO wire-feed twin.</p></div>
      )}

      {playback && visual && (
        <div className="twin__controls">
          <b>Trace playback</b>
          {PHASES.map((p, i) => (
            <button key={p.key} className={i === phaseIdx ? "active" : ""} onClick={() => { setPlaying(false); setPhaseIdx(i); }}>{p.label}</button>
          ))}
          <button onClick={() => { setPhaseIdx(0); setPlaying(true); }} disabled={playing}>{playing ? "Playing…" : "▶ Play fault → repair"}</button>
          {playback.verdict && <span className="muted">Verdict: <b>{playback.verdict.toUpperCase()}</b></span>}
        </div>
      )}

      {visual && (
        <div className="twin__grid" style={{ gridTemplateColumns: "minmax(0, 2fr) minmax(240px, 1fr)" }}>
          <div className="panel">
            <svg width={visual.canvas.width} height={visual.canvas.height} role="img" aria-label="Visual twin diagram">
              {visual.edges.map((e, i) => {
                const from = nodes.find((n) => n.id === e.from);
                const to = nodes.find((n) => n.id === e.to);
                if (!from || !to) return null;
                const x1 = from.x + from.width, y1 = from.y + from.height / 2;
                const x2 = to.x, y2 = to.y + to.height / 2;
                const midX = (x1 + x2) / 2;
                return (
                  <g key={i}>
                    <path d={`M ${x1} ${y1} C ${midX} ${y1}, ${midX} ${y2}, ${x2} ${y2}`} fill="none" stroke="#999" strokeWidth="1.5" />
                    <text x={midX} y={(y1 + y2) / 2 - 4} fontSize="10" fill="#666" textAnchor="middle">{e.label}</text>
                  </g>
                );
              })}
              {nodes.map((n) => {
                const isChanged = changed.has(n.id);
                const ring = !playback || !isChanged ? "#bbb" : phase.key === "fault" ? "#c0392b" : "#1e8449";
                const lines = stateLines(nodeState(n)).slice(0, 3);
                return (
                  <g key={n.id} onClick={() => setSelectedId(n.id)} style={{ cursor: "pointer" }}>
                    <rect x={n.x} y={n.y} width={n.width} height={n.height} rx="8"
                      fill={selectedId === n.id ? "#eaf2f8" : "#fff"} stroke={ring} strokeWidth={isChanged ? 3 : 1.5} />
                    <text x={n.x + 10} y={n.y + 20} fontSize="13" fontWeight="bold" fill="#222">{n.name}</text>
                    <text x={n.x + 10} y={n.y + 36} fontSize="11" fill="#666">{n.type}</text>
                    {lines.map((line, i) => (
                      <text key={i} x={n.x + 10} y={n.y + 52 + i * 14} fontSize="11" fill="#333">{line}</text>
                    ))}
                  </g>
                );
              })}
            </svg>
            <p className="muted">Click a component for its state, rules and evidence. Red ring = changed when the fault hit. Green ring = changed when the repair was applied.</p>
          </div>
          <div className="panel">
            {selected ? (
              <>
                <h3>{selected.name}</h3>
                <p className="muted">{selected.type} · layer {selected.layer}</p>
                <h4>State ({phase.label})</h4>
                <pre>{JSON.stringify(nodeState(selected), null, 2)}</pre>
                {selectedTransitions.length > 0 ? (
                  <>
                    <h4>Rules involving this component</h4>
                    {selectedTransitions.map((t) => (
                      <div key={t.id}>
                        <b>{t.name}</b>
                        <pre>{JSON.stringify({ conditions: t.conditions, effects: t.effects }, null, 2)}</pre>
                        {t.evidence?.length > 0 && <p className="muted">Evidence: {t.evidence.map((e) => e.fact || e.source_id).join(" · ")}</p>}
                      </div>
                    ))}
                  </>
                ) : <p className="muted">No simulation rule mentions this component.</p>}
              </>
            ) : (
              <>
                <h3>Transitions</h3>
                {visual.transitions.length === 0 && <p className="muted">No rules in this twin.</p>}
                {visual.transitions.map((t) => (
                  <div key={t.id}><b>{t.name}</b><p className="muted">{t.description}</p></div>
                ))}
                {playback && phase.traceKey && playback[phase.traceKey]?.length > 0 && (
                  <>
                    <h3>{phase.label} trace</h3>
                    {playback[phase.traceKey].map((line, i) => <p key={i}>{line}</p>)}
                  </>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
