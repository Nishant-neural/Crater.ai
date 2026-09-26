import { useEffect, useMemo, useState } from "react";
import { listMachines } from "../api/machines";
import { listRevisions } from "../api/revisions";
import { startDiagnostic, getDiagnostic, getDiagnosticContext, respondDiagnostic } from "../api/diagnostics";

function Card({title, children, className=""}) {
  return <section className={`diag-card ${className}`}><div className="diag-card__title">{title}</div>{children}</section>;
}

function StatusPill({children, tone=""}) { return <span className={`status-pill ${tone}`}>{children}</span>; }

function MachineGraph({components, relationships, focus}) {
  const nodes = useMemo(() => components.slice(0, 18), [components]);
  if (!nodes.length) return <div className="empty-state">No component topology is available for this revision.</div>;
  const width = 620, height = Math.max(250, Math.ceil(nodes.length / 4) * 100);
  const pos = Object.fromEntries(nodes.map((n,i) => [n.id, {x: 80+(i%4)*150, y: 55+Math.floor(i/4)*95}]));
  return <div className="machine-graph">
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Machine relationship graph">
      {relationships.map(r => {
        const a=pos[r.from], b=pos[r.to]; if(!a||!b) return null;
        return <g key={r.id}><line x1={a.x} y1={a.y} x2={b.x} y2={b.y} className="graph-edge"/>
          <text x={(a.x+b.x)/2} y={(a.y+b.y)/2-5} className="graph-edge-label">{r.type}</text></g>
      })}
      {nodes.map(n => <g key={n.id} className={focus && n.name.toLowerCase().includes(focus.toLowerCase()) ? "graph-node graph-node--focus" : "graph-node"}>
        <rect x={pos[n.id].x-55} y={pos[n.id].y-24} width="110" height="48" rx="10"/>
        <text x={pos[n.id].x} y={pos[n.id].y+4} textAnchor="middle">{n.name.slice(0,18)}</text>
      </g>)}
    </svg>
  </div>;
}

export default function DiagnosticWorkspace() {
  const [machines,setMachines]=useState([]);
  const [revisions,setRevisions]=useState([]);
  const [productId,setProductId]=useState("");
  const [revisionId,setRevisionId]=useState("");
  const [symptom,setSymptom]=useState("Motor M1 is overheating after 20 minutes.");
  const [session,setSession]=useState(null);
  const [context,setContext]=useState(null);
  const [answer,setAnswer]=useState("");
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");
  const [selectedEvidence,setSelectedEvidence]=useState(null);

  useEffect(()=>{ listMachines().then(setMachines).catch(e=>setError(e.message)); },[]);
  useEffect(()=>{
    if(!productId){setRevisions([]);setRevisionId("");return;}
    listRevisions(productId).then(rows=>{setRevisions(rows||[]);setRevisionId(rows?.[0]?.id||"");}).catch(e=>setError(e.message));
  },[productId]);

  async function begin(){
    if(!productId||!revisionId||!symptom.trim()) return;
    setBusy(true);setError("");
    try {
      const s=await startDiagnostic({product_id:productId,revision_id:revisionId,symptom:symptom.trim()});
      setSession(s);
      setContext(await getDiagnosticContext(s.session_id));
    } catch(e){setError(e.message)} finally{setBusy(false)}
  }
  async function sendAnswer(value=answer){
    if(!session||!value.trim()) return;
    setBusy(true);setError("");
    try {
      const s=await respondDiagnostic(session.session_id,{input:value.trim(),input_kind:"observation"});
      setSession(s);setAnswer("");
      if(!context) setContext(await getDiagnosticContext(s.session_id));
    } catch(e){setError(e.message)} finally{setBusy(false)}
  }
  async function refresh(){
    if(!session) return;
    try { setSession(await getDiagnostic(session.session_id)); setContext(await getDiagnosticContext(session.session_id)); }
    catch(e){setError(e.message)}
  }

  const state=session?.state;
  const step=state?.current_step;
  const leading=state?.hypotheses?.[0];
  const isQuestion=step?.type==="question";
  const isConclusion=step?.type==="conclusion";
  const machine=context?.product;
  const revision=context?.revision;
  const docs=context?.documents||[];
  const evidence=state?.evidence||[];

  if(!session) return <div className="diagnostic">
    <div className="diag-hero">
      <div>
        <div className="eyebrow">PHASE 9 · DIAGNOSTIC WORKSTATION</div>
        <h2>Investigate a machine failure.</h2>
        <p>Move from symptom → evidence → hypotheses → targeted question → repair, without turning the product into a generic chatbot.</p>
      </div>
      <div className="hero-flow"><span>Machine</span><b>→</b><span>Evidence</span><b>→</b><span>Diagnosis</span><b>→</b><span>Repair</span></div>
    </div>
    <div className="setup-grid">
      <Card title="1 · Machine">
        <label>Product
          <select value={productId} onChange={e=>setProductId(e.target.value)}>
            <option value="">Select machine</option>
            {machines.map(m=><option key={m.id} value={m.id}>{m.manufacturer} · {m.family} · {m.model}</option>)}
          </select>
        </label>
        <label>Revision
          <select value={revisionId} onChange={e=>setRevisionId(e.target.value)} disabled={!productId}>
            <option value="">Select revision</option>
            {revisions.map(r=><option key={r.id} value={r.id}>{r.label}</option>)}
          </select>
        </label>
        {revisionId && <div className="ready-check"><StatusPill tone="ok">READY</StatusPill> Revision selected</div>}
      </Card>
      <Card title="2 · Symptom">
        <label>What is happening with the machine?
          <textarea value={symptom} onChange={e=>setSymptom(e.target.value)} rows={5} placeholder="Describe the symptom..."/>
        </label>
        <button className="primary-button" disabled={busy||!productId||!revisionId||!symptom.trim()} onClick={begin}>
          {busy ? "Starting investigation…" : "Start investigation"}
        </button>
      </Card>
    </div>
    {error && <div className="diag-error">{error}</div>}
  </div>;

  return <div className="diagnostic diagnostic--session">
    <header className="diagnostic-header">
      <div>
        <div className="eyebrow">LIVE DIAGNOSTIC SESSION</div>
        <h2>{machine?.model || "Machine"} <span>/ {revision?.label || "Revision"}</span></h2>
      </div>
      <div className="header-actions"><StatusPill tone={session.status==="active"?"live":session.status==="concluded"?"ok":"warn"}>{session.status}</StatusPill><button onClick={refresh} disabled={busy}>Refresh</button></div>
    </header>
    {error && <div className="diag-error">{error}</div>}
    <div className="diagnostic-grid">
      <aside className="diagnostic-left">
        <Card title="MACHINE CONTEXT">
          <div className="context-stat"><b>{context?.knowledge?.entity_count ?? 0}</b><span>entities</span></div>
          <div className="context-stat"><b>{context?.knowledge?.relation_count ?? 0}</b><span>relationships</span></div>
          <div className="context-stat"><b>{context?.knowledge?.conflict_count ?? 0}</b><span>conflicts</span></div>
          <div className="context-docs"><b>Sources</b>{docs.slice(0,5).map(d=><span key={d.id}>{d.title}</span>)}</div>
        </Card>
        <Card title="MACHINE RELATIONSHIPS">
          <MachineGraph components={context?.components||[]} relationships={context?.relationships||[]} focus={leading?.cause}/>
        </Card>
        <Card title="INVESTIGATION TIMELINE">
          <div className="timeline">
            {(state?.timeline||[]).map((t,i)=><div className="timeline-item" key={i}><span>#{t.turn}</span><div><b>{t.kind}</b><p>{t.input}</p></div></div>)}
          </div>
        </Card>
      </aside>
      <main className="diagnostic-center">
        <Card title="SYMPTOM"><div className="symptom-box">{state?.symptoms?.[0] || symptom}</div></Card>
        <Card title={`HYPOTHESES · ${state?.hypotheses?.length||0}`}>
          <div className="hypothesis-list">
            {(state?.hypotheses||[]).map((h,i)=><article className={`hypothesis ${i===0?"hypothesis--leading":""}`} key={h.cause}>
              <div className="hypothesis-top"><div><span className="rank">{String(i+1).padStart(2,"0")}</span><b>{h.cause}</b></div><span>{Math.round(h.confidence*100)}%</span></div>
              <div className="confidence-track"><i style={{width:`${Math.max(0,Math.min(100,h.confidence*100))}%`}}/></div>
              <p>{h.rationale}</p>
              {h.supporting_evidence_chunk_ids?.length>0 && <div className="evidence-tags">{h.supporting_evidence_chunk_ids.map(id=><button key={id} onClick={()=>setSelectedEvidence(evidence.find(e=>e.chunk_id===id))}>Evidence {id.slice(0,8)}</button>)}</div>}
            </article>)}
            {!state?.hypotheses?.length && <div className="empty-state">The diagnostic agent has not produced a usable hypothesis yet.</div>}
          </div>
        </Card>
        <Card title="EVIDENCE">
          <div className="evidence-list">{evidence.map(e=><button className={`evidence-item ${selectedEvidence?.chunk_id===e.chunk_id?"selected":""}`} key={e.chunk_id} onClick={()=>setSelectedEvidence(e)}>
            <div><b>{e.source_document||"Source document"}</b><span>{e.page_number?`p.${e.page_number}`:"page unavailable"}</span></div>
            <p>{e.content}</p>
          </button>)}</div>
          {!evidence.length && <div className="empty-state">No source evidence was retrieved for this turn.</div>}
        </Card>
      </main>
      <aside className="diagnostic-right">
        <Card title="NEXT DIAGNOSTIC STEP" className="question-card">
          <StatusPill tone={isQuestion?"live":isConclusion?"ok":"warn"}>{step?.type || "waiting"}</StatusPill>
          <h3>{step?.content || "No next step is available."}</h3>
          {step?.rationale && <p className="muted">{step.rationale}</p>}
          {step?.safety_notes?.length>0 && <div className="safety-box"><b>Safety notes</b>{step.safety_notes.map((x,i)=><span key={i}>{x}</span>)}</div>}
          {isQuestion && <div className="answer-actions">
            <button onClick={()=>sendAnswer("Yes")}>YES</button><button onClick={()=>sendAnswer("No")}>NO</button><button onClick={()=>sendAnswer("Not sure")}>NOT SURE</button>
            <div className="answer-input"><input value={answer} onChange={e=>setAnswer(e.target.value)} onKeyDown={e=>e.key==="Enter"&&sendAnswer()} placeholder="Or enter an observation…"/><button onClick={()=>sendAnswer()} disabled={!answer.trim()||busy}>Send</button></div>
          </div>}
          {step?.type==="action" && <div className="answer-input"><input value={answer} onChange={e=>setAnswer(e.target.value)} placeholder="Enter the measurement / result…"/><button onClick={()=>sendAnswer()} disabled={!answer.trim()||busy}>Submit</button></div>}
        </Card>
        <Card title="REPAIR / RECOMMENDATION">
          {isConclusion ? <div className="repair">
            <StatusPill tone="ok">RECOMMENDATION READY</StatusPill>
            <h3>{step.content}</h3>
            <p>{step.rationale || "Grounded in the evidence shown in this session."}</p>
            {evidence.slice(0,4).map(e=><div className="repair-source" key={e.chunk_id}>↳ {e.source_document}{e.page_number?` · p.${e.page_number}`:""}</div>)}
          </div> : <div className="empty-state">A structured repair recommendation will appear after the diagnostic state reaches a grounded conclusion.</div>}
        </Card>
        <Card title="VERIFICATION">
          <div className="verification-unavailable"><b>Simulation contract</b><p>Verification is only marked PASS/FAIL when a real digital-twin experiment is executed. No simulated success is shown here.</p><button onClick={()=>window.dispatchEvent(new CustomEvent("crater-open-simulation"))}>Open simulation workspace</button></div>
        </Card>
      </aside>
    </div>
  </div>;
}
