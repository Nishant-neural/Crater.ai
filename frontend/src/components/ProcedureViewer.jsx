import { useEffect, useRef, useState } from "react";
import { getProcedureVisualization, procedureFrameUrl } from "../api/client";

const FRAME_INTERVAL_MS = 1800;

/**
 * Phase 4 "Procedure visualization" + "Repair animations". A repair
 * animation here is a played-through sequence of annotated-diagram frames
 * (backend/visualization/procedure_viz.py), not rendered video — see
 * docs/Phase4.md for why that's the deliberate scope for now.
 */
export default function ProcedureViewer({ procedureId, onFrameChange }) {
  const [viz, setViz] = useState(null);
  const [error, setError] = useState(null);
  const [activeStep, setActiveStep] = useState(0);
  const [playingFrame, setPlayingFrame] = useState(null);
  const timerRef = useRef(null);

  useEffect(() => {
    setViz(null);
    setError(null);
    setActiveStep(0);
    stopPlayback();
    if (!procedureId) return;
    getProcedureVisualization(procedureId)
      .then((data) => (data ? setViz(data) : setError("Procedure not found.")))
      .catch((e) => setError(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [procedureId]);

  useEffect(() => () => stopPlayback(), []);

  function stopPlayback() {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    setPlayingFrame(null);
  }

  function playAnimation() {
    if (!viz || viz.animation_frames.length === 0) return;
    let i = 0;
    setPlayingFrame(viz.animation_frames[0]);
    onFrameChange?.(viz.animation_frames[0]);
    timerRef.current = setInterval(() => {
      i += 1;
      if (i >= viz.animation_frames.length) {
        stopPlayback();
        return;
      }
      setPlayingFrame(viz.animation_frames[i]);
      onFrameChange?.(viz.animation_frames[i]);
    }, FRAME_INTERVAL_MS);
  }

  if (!procedureId) return <div className="procedure-viewer procedure-viewer--empty">Enter a procedure to visualize it.</div>;
  if (error) return <div className="procedure-viewer procedure-viewer--empty">{error}</div>;
  if (!viz) return <div className="procedure-viewer procedure-viewer--empty">Loading procedure…</div>;

  const step = viz.steps[activeStep];

  return (
    <div className="procedure-viewer">
      <div className="procedure-viewer__header">
        <h3>{viz.name}</h3>
        <span className="muted">{viz.procedure_type}</span>
        <button
          disabled={viz.animation_frames.length === 0}
          onClick={playingFrame ? stopPlayback : playAnimation}
        >
          {playingFrame ? "Stop animation" : `Play repair animation (${viz.animation_frames.length} frames)`}
        </button>
      </div>

      <ol className="procedure-viewer__steps">
        {viz.steps.map((s, i) => (
          <li
            key={s.step_index}
            className={i === activeStep ? "active" : ""}
            onClick={() => {
              stopPlayback();
              setActiveStep(i);
              onFrameChange?.(s.frames[0] ?? null);
            }}
          >
            {s.instruction}
            {s.referenced_components.length > 0 && (
              <span className="procedure-viewer__refs"> — {s.referenced_components.join(", ")}</span>
            )}
          </li>
        ))}
      </ol>

      {!playingFrame && step && step.frames.length === 0 && (
        <p className="muted">No located components to highlight for this step.</p>
      )}
      {playingFrame && (
        <div className="procedure-viewer__frame">
          <img src={procedureFrameUrl(procedureId, playingFrame.frame_index)} alt={`Frame ${playingFrame.frame_index}`} />
          <span>Highlighting: {playingFrame.labels.join(", ")}</span>
        </div>
      )}
    </div>
  );
}
