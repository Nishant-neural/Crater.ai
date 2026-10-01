import { useEffect, useMemo, useState } from "react";
import { getMachineModel, getMachineKnowledge, getMachineVerification } from "../api/knowledge";
import { listMachines } from "../api/machines";
import { listRevisions } from "../api/revisions";

const SECTIONS = [
  ["overview", "Overview"],
  ["entities", "Entities"],
  ["relations", "Relations"],
  ["facts", "Facts"],
  ["procedures", "Procedures"],
  ["failures", "Failure modes"],
  ["conflicts", "Conflicts"],
  ["evidence", "Evidence"],
];

function Count({ value, label }) {
  return <div className="model-stat"><strong>{value ?? 0}</strong><span>{label}</span></div>;
}

function Badge({ children, tone = "" }) {
  return <span className={`model-badge ${tone}`}>{children}</span>;
}

function JsonValue({ value }) {
  if (value === null || value === undefined || value === "") return <span className="muted">—</span>;
  if (typeof value === "object") return <pre className="model-json">{JSON.stringify(value, null, 2)}</pre>;
  return <span>{String(value)}</span>;
}

function EvidenceCard({ evidence }) {
  return <div className="model-evidence-card">
    <div className="model-evidence-head">
      <Badge tone={evidence.claim_status === "observed" ? "ok" : evidence.claim_status === "uncertain" ? "warn" : "info"}>
        {evidence.claim_status || "observed"}
      </Badge>
      {evidence.confidence !== undefined && <span className="muted">confidence {Math.round(Number(evidence.confidence || 0) * 100)}%</span>}
    </div>
    <p>{evidence.fact || "No evidence text recorded."}</p>
    <div className="model-source">
      <b>{evidence.source_document || "Source document"}</b>
      {evidence.page != null && <span>p. {evidence.page}</span>}
      {evidence.chunk && <span>chunk {String(evidence.chunk).slice(0, 12)}</span>}
      {evidence.source_type && <span>{evidence.source_type}</span>}
    </div>
  </div>;
}

function EntityDetail({ entity, relations, evidenceByItem, onClose }) {
  if (!entity) return null;
  const outgoing = relations.filter(r => r.subject_id === entity.id);
  const incoming = relations.filter(r => r.object_id === entity.id);
  const evidence = evidenceByItem.get(entity.id) || [];
  return <div className="model-detail-drawer">
    <div className="model-detail-head">
      <div><div className="eyebrow">ENTITY</div><h3>{entity.name}</h3><Badge>{entity.entity_type}</Badge></div>
      <button className="secondary-button" onClick={onClose}>Close</button>
    </div>
    <div className="model-detail-grid">
      <div><label>ID</label><code>{entity.id}</code></div>
      <div><label>Sources</label><span>{entity.source_ids?.length || 0}</span></div>
    </div>
    <h4>Properties</h4>
    {Object.keys(entity.properties || {}).length ? <div className="model-properties">{Object.entries(entity.properties).map(([k,v]) => <div key={k}><span>{k}</span><JsonValue value={v}/></div>)}</div> : <p className="muted">No additional properties.</p>}
    {(entity.ports?.length > 0 || entity.states?.length > 0) && <>
      <h4>Ports & states</h4>
      <div className="model-chip-list">{entity.ports?.map(p => <Badge key={`p-${p}`}>port: {p}</Badge>)}{entity.states?.map(s => <Badge key={`s-${s}`}>state: {s}</Badge>)}</div>
    </>}
    <h4>Relationships</h4>
    <div className="model-relation-list">
      {outgoing.map(r => <div key={r.id || `${r.subject_id}-${r.object_id}-${r.relation_type}`}><span>→</span><b>{r.relation_type}</b><span>{r.object_name}</span></div>)}
      {incoming.map(r => <div key={`in-${r.id || `${r.subject_id}-${r.object_id}-${r.relation_type}`}`}><span>←</span><b>{r.relation_type}</b><span>{r.subject_name}</span></div>)}
      {!outgoing.length && !incoming.length && <p className="muted">No relationships recorded.</p>}
    </div>
    <h4>Evidence</h4>
    {evidence.length ? evidence.map((e,i) => <EvidenceCard key={e.id || i} evidence={e}/>) : <p className="muted">No direct evidence record attached.</p>}
  </div>;
}

export default function MachineModelViewer({ initialProductId = "", initialRevisionId = "" }) {
  const [machines, setMachines] = useState([]);
  const [revisions, setRevisions] = useState([]);
  const [productId, setProductId] = useState(initialProductId);
  const [revisionId, setRevisionId] = useState(initialRevisionId);
  const [snapshot, setSnapshot] = useState(null);
  const [raw, setRaw] = useState(null);
  const [verification, setVerification] = useState(null);
  const [section, setSection] = useState("overview");
  const [query, setQuery] = useState("");
  const [selectedEntity, setSelectedEntity] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => { listMachines().then(rows => setMachines(rows || [])).catch(e => setError(e.message)); }, []);
  useEffect(() => {
    if (!productId) { setRevisions([]); return; }
    listRevisions(productId).then(rows => {
      const next = rows || [];
      setRevisions(next);
      setRevisionId(current => current && next.some(r => r.id === current) ? current : (next[0]?.id || ""));
    }).catch(e => setError(e.message));
  }, [productId]);

  async function loadModel(id = revisionId) {
    if (!id) return;
    setLoading(true); setError(""); setSelectedEntity(null);
    try {
      const [model, knowledge, verify] = await Promise.all([
        getMachineModel(id),
        getMachineKnowledge(id),
        getMachineVerification(id).catch(() => null),
      ]);
      setSnapshot(model); setRaw(knowledge); setVerification(verify);
      setSection("overview");
    } catch (e) { setError(e.message || "Unable to load machine model."); }
    finally { setLoading(false); }
  }

  useEffect(() => { if (revisionId) loadModel(revisionId); }, [revisionId]);

  const model = snapshot?.model || {};
  const entities = model.entities || [];
  const relations = model.relations || [];
  const facts = raw?.facts || [];
  const procedures = model.procedures || [];
  const failures = model.failure_modes || [];
  const conflicts = model.conflicts || [];
  const unresolved = model.unresolved_facts || [];
  const evidence = raw?.evidence || [];
  const evidenceByItem = useMemo(() => {
    const map = new Map();
    evidence.forEach(e => { const list = map.get(e.item_id) || []; list.push(e); map.set(e.item_id, list); });
    return map;
  }, [evidence]);

  const filteredEntities = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? entities.filter(e => `${e.name} ${e.entity_type} ${JSON.stringify(e.properties || {})}`.toLowerCase().includes(q)) : entities;
  }, [entities, query]);
  const filteredRelations = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? relations.filter(r => `${r.subject_name} ${r.relation_type} ${r.object_name}`.toLowerCase().includes(q)) : relations;
  }, [relations, query]);
  const selectedProduct = machines.find(m => m.id === productId);
  const issues = verification?.issues || [];
  const errors = issues.filter(i => i.severity === "error");
  const warnings = issues.filter(i => i.severity !== "error");

  if (!revisionId) return <div className="machine-model machine-model--empty"><div><div className="eyebrow">MACHINE MODEL</div><h2>No revision selected</h2><p>Select a machine revision to inspect its canonical knowledge model.</p></div></div>;

  return <div className="machine-model">
    <header className="machine-model__header">
      <div>
        <div className="eyebrow">CANONICAL MACHINE KNOWLEDGE</div>
        <h2>{selectedProduct ? `${selectedProduct.manufacturer} · ${selectedProduct.model}` : "Machine model"}</h2>
        <p className="muted">Revision-wide model produced by extraction → global integration → deterministic verification.</p>
      </div>
      <div className="machine-model__controls">
        <select value={productId} onChange={e => setProductId(e.target.value)}>
          <option value="">Select machine</option>
          {machines.map(m => <option key={m.id} value={m.id}>{m.manufacturer} · {m.family} · {m.model}</option>)}
        </select>
        <select value={revisionId} onChange={e => setRevisionId(e.target.value)} disabled={!productId}>
          <option value="">Select revision</option>
          {revisions.map(r => <option key={r.id} value={r.id}>{r.label}</option>)}
        </select>
        <button className="secondary-button" onClick={() => loadModel()} disabled={loading}>{loading ? "Loading…" : "Refresh model"}</button>
      </div>
    </header>

    {error && <div className="diag-error">{error}</div>}
    {!snapshot && !loading && <div className="model-empty">No canonical machine model exists for this revision yet. Complete ingestion and integration first.</div>}
    {loading && <div className="model-empty">Loading canonical model…</div>}
    {snapshot && <>
      <div className="model-health">
        <div><span>MODEL VERSION</span><b>v{snapshot.version}</b></div>
        <div><span>VERIFICATION</span><Badge tone={verification?.valid ? "ok" : errors.length ? "error" : "warn"}>{verification?.valid ? "PASS" : errors.length ? "FAIL" : "WARNINGS"}</Badge></div>
        <div><span>COMPLETENESS</span><b>{model.completeness?.complete === false ? "Incomplete" : "Accounted"}</b></div>
        <div><span>UNRESOLVED</span><b>{unresolved.length}</b></div>
      </div>

      <nav className="model-nav">{SECTIONS.map(([id,label]) => <button key={id} className={section === id ? "active" : ""} onClick={() => setSection(id)}>{label}</button>)}</nav>

      {section !== "overview" && section !== "conflicts" && section !== "evidence" && <div className="model-search"><input value={query} onChange={e => setQuery(e.target.value)} placeholder={`Search ${section}…`} /></div>}

      {section === "overview" && <Overview model={model} raw={raw} verification={verification} onSelectSection={setSection} />}
      {section === "entities" && <div className="model-list-grid">{filteredEntities.map(e => <button className="model-entity-card" key={e.id} onClick={() => setSelectedEntity(e)}><div><b>{e.name}</b><Badge>{e.entity_type}</Badge></div><span>{Object.keys(e.properties || {}).length} properties · {e.source_ids?.length || 0} sources</span></button>)}{!filteredEntities.length && <div className="model-empty">No entities match this search.</div>}</div>}
      {section === "relations" && <div className="model-relation-table">{filteredRelations.map((r,i) => <div className="model-relation-row" key={r.id || i}><b>{r.subject_name}</b><Badge>{r.relation_type}</Badge><b>{r.object_name}</b><span>{r.description || ""}</span></div>)}{!filteredRelations.length && <div className="model-empty">No relationships match this search.</div>}</div>}
      {section === "facts" && <FactList facts={facts} query={query} />}
      {section === "procedures" && <ProcedureList procedures={procedures} query={query} />}
      {section === "failures" && <FailureList failures={failures} query={query} />}
      {section === "conflicts" && <ConflictList conflicts={conflicts} unresolved={unresolved} />}
      {section === "evidence" && <EvidenceList evidence={evidence} query={query} />}

      {selectedEntity && <EntityDetail entity={selectedEntity} relations={relations} evidenceByItem={evidenceByItem} onClose={() => setSelectedEntity(null)} />}
    </>}
  </div>;
}

function Overview({ model, raw, verification, onSelectSection }) {
  const counts = [
    ["entities", model.entities?.length, "Entities"], ["relations", model.relations?.length, "Relations"],
    ["facts", raw?.facts?.length, "Facts"], ["procedures", model.procedures?.length, "Procedures"],
    ["failures", model.failure_modes?.length, "Failure modes"], ["evidence", raw?.evidence?.length, "Evidence"],
    ["conflicts", model.conflicts?.length, "Conflicts"], ["conflicts", model.unresolved_facts?.length, "Unresolved"],
  ];
  const warnings = (verification?.issues || []).filter(i => i.severity !== "error");
  const errors = (verification?.issues || []).filter(i => i.severity === "error");
  return <div className="model-overview">
    <div className="model-stat-grid">{counts.map(([section,value,label],i) => <button key={`${label}-${i}`} onClick={() => onSelectSection(section)}><Count value={value} label={label}/></button>)}</div>
    <div className="model-overview-grid">
      <section className="model-panel"><h3>Machine topology</h3><MiniGraph entities={model.entities || []} relations={model.relations || []}/></section>
      <section className="model-panel"><h3>Model integrity</h3><div className="integrity-row"><span>Verification</span><Badge tone={verification?.valid ? "ok" : errors.length ? "error" : "warn"}>{verification?.valid ? "PASS" : errors.length ? "FAIL" : "WARNINGS"}</Badge></div><div className="integrity-row"><span>Verification errors</span><b>{errors.length}</b></div><div className="integrity-row"><span>Verification warnings</span><b>{warnings.length}</b></div><div className="integrity-row"><span>Unresolved facts</span><b>{model.unresolved_facts?.length || 0}</b></div><div className="integrity-row"><span>Revision lineage</span><b>{model.revision_lineage?.length || 1}</b></div></section>
    </div>
    {(errors.length || warnings.length) ? <section className="model-panel"><h3>Verification findings</h3>{[...errors,...warnings].slice(0,12).map((i,n)=><div className="verification-item" key={n}><Badge tone={i.severity === "error" ? "error" : "warn"}>{i.severity}</Badge><span>{i.message}</span></div>)}</section> : <section className="model-panel model-success"><b>Canonical model passed deterministic verification.</b><span>No topology errors were reported.</span></section>}
  </div>;
}

function MiniGraph({ entities, relations }) {
  const nodes = entities.slice(0, 24); if (!nodes.length) return <div className="model-empty">No entities.</div>;
  const width=760, height=Math.max(220, Math.ceil(nodes.length/5)*78); const pos=Object.fromEntries(nodes.map((n,i)=>[n.id,{x:90+(i%5)*150,y:45+Math.floor(i/5)*72}]));
  return <div className="model-mini-graph"><svg viewBox={`0 0 ${width} ${height}`}>{relations.map((r,i)=>{const a=pos[r.subject_id],b=pos[r.object_id];if(!a||!b)return null;return <g key={i}><line x1={a.x} y1={a.y} x2={b.x} y2={b.y} className="model-edge"/><text x={(a.x+b.x)/2} y={(a.y+b.y)/2-4}>{r.relation_type}</text></g>})}{nodes.map(n=><g key={n.id}><rect x={pos[n.id].x-55} y={pos[n.id].y-18} width="110" height="36" rx="8"/><text x={pos[n.id].x} y={pos[n.id].y+3} textAnchor="middle">{n.name.slice(0,16)}</text></g>)}</svg></div>;
}

function FactList({ facts, query }) { const q=query.toLowerCase(); const rows=facts.filter(f=>!q||`${f.fact_type} ${f.fact_key} ${JSON.stringify(f.payload)}`.toLowerCase().includes(q)); return <div className="model-card-list">{rows.map(f=><article className="model-panel" key={f.id}><div className="model-card-head"><Badge>{f.fact_type}</Badge><code>{f.fact_key}</code></div><JsonValue value={f.payload}/></article>)}{!rows.length&&<div className="model-empty">No facts match this search.</div>}</div>; }
function ProcedureList({ procedures, query }) { const q=query.toLowerCase(); const rows=procedures.filter(p=>!q||`${p.name} ${p.procedure_type} ${p.steps.join(" ")}`.toLowerCase().includes(q)); return <div className="model-card-list">{rows.map(p=><article className="model-panel" key={p.id}><div className="model-card-head"><h3>{p.name}</h3><Badge>{p.procedure_type}</Badge></div><ol>{p.steps.map((s,i)=><li key={i}>{s}</li>)}</ol><div className="muted">{p.entity_ids?.length || 0} referenced entities · {p.source_ids?.length || 0} sources</div></article>)}{!rows.length&&<div className="model-empty">No procedures match this search.</div>}</div>; }
function FailureList({ failures, query }) { const q=query.toLowerCase(); const rows=failures.filter(f=>!q||`${f.name} ${f.symptoms.join(" ")} ${f.possible_causes.join(" ")} ${f.diagnostic_test||""}`.toLowerCase().includes(q)); return <div className="model-card-list">{rows.map(f=><article className="model-panel" key={f.id}><div className="model-card-head"><h3>{f.name}</h3><Badge>failure mode</Badge></div><div className="model-two-col"><div><h4>Symptoms</h4><ul>{f.symptoms.map((x,i)=><li key={i}>{x}</li>)}</ul></div><div><h4>Possible causes</h4><ul>{f.possible_causes.map((x,i)=><li key={i}>{x}</li>)}</ul></div></div>{f.diagnostic_test&&<p><b>Diagnostic test:</b> {f.diagnostic_test}</p>}{f.expected_observation&&<p><b>Expected observation:</b> {f.expected_observation}</p>}</article>)}{!rows.length&&<div className="model-empty">No failure modes match this search.</div>}</div>; }
function ConflictList({ conflicts, unresolved }) { return <div className="model-card-list">{conflicts.map(c=><article className="model-panel model-conflict" key={c.id}><div className="model-card-head"><Badge tone="warn">{c.status || "unresolved"}</Badge><h3>{c.subject}</h3></div><p><b>{c.property}</b></p><ul>{c.values.map((v,i)=><li key={i}><JsonValue value={v}/></li>)}</ul>{c.resolution&&<p><b>Resolution:</b> {c.resolution}</p>}</article>)}{unresolved.map(u=><article className="model-panel" key={`u-${u.source_id}`}><div className="model-card-head"><Badge tone="warn">unresolved source</Badge><code>{u.source_id}</code></div><p>{u.reason}</p><JsonValue value={u.payload}/></article>)}{!conflicts.length&&!unresolved.length&&<div className="model-success"><b>No conflicts or unresolved facts.</b></div>}</div>; }
function EvidenceList({ evidence, query }) { const q=query.toLowerCase(); const rows=evidence.filter(e=>!q||`${e.fact} ${e.source_document||""} ${e.claim_status||""} ${e.chunk||""}`.toLowerCase().includes(q)); return <div className="model-card-list">{rows.map((e,i)=><EvidenceCard key={e.id||i} evidence={e}/>) }{!rows.length&&<div className="model-empty">No evidence matches this search.</div>}</div>; }
