import { useEffect, useMemo, useState } from "react";
import { getComponentExplorer } from "../api/client";

/** Phase 4 "Component explorer": browse a revision's parts, see where each
 * one appears on a diagram, and jump the technical viewer to it. */
export default function ComponentExplorer({ revisionId, onOpenAppearance }) {
  const [explorer, setExplorer] = useState(null);
  const [error, setError] = useState(null);
  const [query, setQuery] = useState("");
  const [expandedId, setExpandedId] = useState(null);

  useEffect(() => {
    setExplorer(null);
    setError(null);
    if (!revisionId) return;
    getComponentExplorer(revisionId)
      .then((data) => (data ? setExplorer(data) : setError("Revision not found.")))
      .catch((e) => setError(e.message));
  }, [revisionId]);

  const filtered = useMemo(() => {
    if (!explorer) return [];
    const q = query.trim().toLowerCase();
    if (!q) return explorer.components;
    return explorer.components.filter(
      (c) => c.name.toLowerCase().includes(q) || (c.function || "").toLowerCase().includes(q)
    );
  }, [explorer, query]);

  if (!revisionId) return <div className="component-explorer component-explorer--empty">Enter a revision to browse its components.</div>;
  if (error) return <div className="component-explorer component-explorer--empty">{error}</div>;
  if (!explorer) return <div className="component-explorer component-explorer--empty">Loading components…</div>;

  return (
    <div className="component-explorer">
      <input
        className="component-explorer__search"
        placeholder="Search components…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      <ul className="component-explorer__list">
        {filtered.map((c) => {
          const expanded = expandedId === c.id;
          return (
            <li key={c.id} className="component-explorer__item">
              <button className="component-explorer__row" onClick={() => setExpandedId(expanded ? null : c.id)}>
                <span className="component-explorer__name">{c.name}</span>
                {c.part_number && <span className="component-explorer__part-number">{c.part_number}</span>}
              </button>
              {expanded && (
                <div className="component-explorer__detail">
                  {c.function && <p>{c.function}</p>}
                  {c.location_description && <p className="muted">{c.location_description}</p>}

                  {c.appearances.length > 0 && (
                    <div>
                      <h4>Appears on</h4>
                      <ul>
                        {c.appearances.map((a) => (
                          <li key={`${a.chunk_id}-${a.label}`}>
                            <button onClick={() => onOpenAppearance?.(a, c)}>
                              {a.document_title} ({a.label})
                            </button>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {c.related_components.length > 0 && (
                    <div>
                      <h4>Related</h4>
                      <ul>
                        {c.related_components.map((r, i) => (
                          <li key={i}>
                            {r.direction === "outgoing" ? "→" : "←"} {r.name} ({r.relation_type})
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}
            </li>
          );
        })}
        {filtered.length === 0 && <li className="muted">No components match "{query}".</li>}
      </ul>
    </div>
  );
}
