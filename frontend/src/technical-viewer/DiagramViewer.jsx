import { useEffect, useMemo, useRef, useState } from "react";
import { diagramImageUrl, getInteractiveDiagram } from "../api/client";

const MIN_SCALE = 0.5;
const MAX_SCALE = 6;

/**
 * Renders one diagram (backend/visualization/interactive_diagram.py manifest)
 * as a pannable/zoomable canvas with clickable node overlays. Highlights a
 * given set of labels (e.g. from a procedure step) when `highlightLabels`
 * is passed, so the same component doubles as the "annotated schematic"
 * view and the procedure step viewer's diagram panel.
 */
export default function DiagramViewer({ chunkId, highlightLabels = [], onSelectNode }) {
  const [manifest, setManifest] = useState(null);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState(null);
  const [transform, setTransform] = useState({ scale: 1, x: 0, y: 0 });
  const dragState = useRef(null);

  useEffect(() => {
    setManifest(null);
    setError(null);
    setSelected(null);
    setTransform({ scale: 1, x: 0, y: 0 });
    if (!chunkId) return;
    getInteractiveDiagram(chunkId)
      .then((m) => (m ? setManifest(m) : setError("No interactive diagram found for this chunk.")))
      .catch((e) => setError(e.message));
  }, [chunkId]);

  const highlightSet = useMemo(() => new Set(highlightLabels), [highlightLabels]);

  function onWheel(e) {
    e.preventDefault();
    const delta = e.deltaY > 0 ? -0.1 : 0.1;
    setTransform((t) => ({ ...t, scale: Math.min(MAX_SCALE, Math.max(MIN_SCALE, t.scale + delta)) }));
  }

  function onMouseDown(e) {
    dragState.current = { startX: e.clientX, startY: e.clientY, origin: transform };
  }
  function onMouseMove(e) {
    if (!dragState.current) return;
    const { startX, startY, origin } = dragState.current;
    setTransform({ ...origin, x: origin.x + (e.clientX - startX), y: origin.y + (e.clientY - startY) });
  }
  function onMouseUp() {
    dragState.current = null;
  }

  if (error) return <div className="diagram-viewer diagram-viewer--empty">{error}</div>;
  if (!chunkId) return <div className="diagram-viewer diagram-viewer--empty">Select a diagram to view it.</div>;
  if (!manifest) return <div className="diagram-viewer diagram-viewer--empty">Loading diagram…</div>;

  return (
    <div className="diagram-viewer">
      <div className="diagram-viewer__toolbar">
        <span>{manifest.document_title}{manifest.page_number ? ` — p.${manifest.page_number}` : ""}</span>
        <button onClick={() => setTransform({ scale: 1, x: 0, y: 0 })}>Reset view</button>
      </div>
      <div
        className="diagram-viewer__canvas"
        onWheel={onWheel}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={onMouseUp}
      >
        <div
          className="diagram-viewer__stage"
          style={{
            width: manifest.image_width,
            height: manifest.image_height,
            transform: `translate(${transform.x}px, ${transform.y}px) scale(${transform.scale})`,
          }}
        >
          <img src={diagramImageUrl(chunkId)} alt={manifest.document_title} draggable={false} />
          <svg
            className="diagram-viewer__overlay"
            width={manifest.image_width}
            height={manifest.image_height}
            viewBox={`0 0 ${manifest.image_width} ${manifest.image_height}`}
          >
            {manifest.edges.map((edge, i) => {
              const from = manifest.nodes.find((n) => n.id === edge.from_id);
              const to = manifest.nodes.find((n) => n.id === edge.to_id);
              if (!from?.bbox || !to?.bbox) return null;
              const fx = from.bbox.x + from.bbox.w / 2;
              const fy = from.bbox.y + from.bbox.h / 2;
              const tx = to.bbox.x + to.bbox.w / 2;
              const ty = to.bbox.y + to.bbox.h / 2;
              return <line key={i} x1={fx} y1={fy} x2={tx} y2={ty} className={`wire wire--${edge.wire_type}`} />;
            })}
            {manifest.nodes.map((node) => {
              if (!node.bbox) return null;
              const isHighlighted = highlightSet.has(node.label);
              const isSelected = selected?.id === node.id;
              return (
                <g
                  key={node.id}
                  className={`node ${isHighlighted ? "node--highlighted" : ""} ${isSelected ? "node--selected" : ""}`}
                  onClick={() => {
                    setSelected(node);
                    onSelectNode?.(node);
                  }}
                >
                  <rect x={node.bbox.x} y={node.bbox.y} width={node.bbox.w} height={node.bbox.h} />
                  <text x={node.bbox.x} y={Math.max(10, node.bbox.y - 4)}>{node.label}</text>
                </g>
              );
            })}
          </svg>
        </div>
      </div>
      {selected && (
        <div className="diagram-viewer__inspector">
          <strong>{selected.label}</strong> — {selected.symbol_type}
          {selected.component_name && <div>Component: {selected.component_name}</div>}
          {selected.description && <div>{selected.description}</div>}
        </div>
      )}
    </div>
  );
}
