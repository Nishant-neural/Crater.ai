import { useEffect, useMemo, useState } from "react";
import { getRevisionIngestionStatus } from "../api/client";
import { startDiagnostic, getDiagnostic, listDiagnostics, getDiagnosticContext, respondDiagnostic,
  recordDiagnosticOutcome } from "../api/diagnostics";
import MachineOnboarding from "./MachineOnboarding";

function Card({title, children, className=""}) {
  return <section className={`diag-card ${className}`}><div className="diag-card__title">{title}</div>{children}</section>;
}

function StatusPill({children, tone=""}) { return <span className={`status-pill ${tone}`}>{children}</span>; }

function MachineGraph({components, relationships, focus}) {
  // Audit P-08: no slice cap — render every component; the container
  // scrolls (.machine-graph in App.css) instead of truncating.
  const nodes = useMemo(() => components, [components]);
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

export default function DiagnosticWorkspace({ onMachineReady, initialProductId = "", initialRevisionId = "",
  machines: machinesProp = [], revisions: revisionsProp = [], onRefreshMachines,
  onOpenSimulation }) {
  const [machines,setMachines]=useState(machinesProp);
  const [revisions,setRevisions]=useState(revisionsProp);
  const [productId,setProductId]=useState(initialProductId);
  const [revisionId,setRevisionId]=useState(initialRevisionId);
  // Audit P-03: no hardcoded symptom — the technician types what is
  // actually happening. Observations/measurements are real inputs too:
  // StartDiagnosticRequest accepts none (contracts §4.1 — the schema
  // change has not landed), so they are sent via the live /respond
  // flow right after start (see begin()), never invented on start.
  const [symptom,setSymptom]=useState("");
  const [observations,setObservations]=useState("");
  const [session,setSession]=useState(null);
  const [context,setContext]=useState(null);
  const [answer,setAnswer]=useState("");
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");
  const [selectedEvidence,setSelectedEvidence]=useState(null);
  const [showOnboarding,setShowOnboarding]=useState(false);
  const [revisionIngestion,setRevisionIngestion]=useState(null);
  const [outcomeForm,setOutcomeForm]=useState({outcome:"fixed",confirmed_cause:"",repair_performed:"",technician_notes:"",submit_correction_for_review:false});
  const [outcomeResult,setOutcomeResult]=useState(null);
  const [feedbackBusy,setFeedbackBusy]=useState(false);
  const [pastSessions,setPastSessions]=useState([]);
  const [pastLoading,setPastLoading]=useState(false);

  // Audit Step 4: the machine/revision lists are owned by the shell
  // (App.jsx) and passed down — this view no longer fetches them
  // itself. It only picks the first revision when the current one is
  // not in the shell's list.
  useEffect(()=>{ setMachines(machinesProp); setProductId(current => current || machinesProp[0]?.id || ""); },[machinesProp]);
  useEffect(()=>{ setRevisions(revisionsProp); },[revisionsProp]);
  // Follow the machine picked on the Machines view while no session is running.
  useEffect(()=>{ if(!session && initialProductId) setProductId(initialProductId); },[initialProductId, session]);
  useEffect(()=>{ if(!session && initialRevisionId) setRevisionId(initialRevisionId); },[initialRevisionId, session]);
  useEffect(()=>{
    if(!productId){ setRevisionId(""); return; }
    setRevisionId(current =>
      current && revisions.some(row => row.id === current)
        ? current
        : (revisions[0]?.id || "")
    );
  },[productId, revisions]);

  useEffect(()=>{
    if(!revisionId){ setRevisionIngestion(null); return; }
    getRevisionIngestionStatus(revisionId)
      .then(setRevisionIngestion)
      .catch(e=>setRevisionIngestion({status:"unknown",error:e.message}));
  },[revisionId]);

  // Phase 2: past sessions for this machine, most recent first, so a
  // session survives navigating away from Diagnose. Loads whenever no
  // session is open.
  useEffect(()=>{
    if(session || !productId || !revisionId){ setPastSessions([]); return; }
    setPastLoading(true);
    listDiagnostics(productId, revisionId)
      .then(res=>setPastSessions(res?.sessions || []))
      .catch(()=>setPastSessions([]))
      .finally(()=>setPastLoading(false));
  },[session, productId, revisionId]);

  async function restoreSession(row){
    setBusy(true);setError("");
    try {
      const s=await getDiagnostic(row.session_id);
      if(!s) { setError("That session no longer exists."); return; }
      // Verification / tournament / outcome state is not persisted in
      // the session -- only the timeline is -- so clear it on restore.
      setOutcomeResult(null);
      setProductId(s.product_id); setRevisionId(s.revision_id);
      setSession(s);
      setContext(await getDiagnosticContext(s.session_id));
    } catch(e){setError(e.message)} finally{setBusy(false)}
  }
  function backToSessions(){
    // Leave the session open on the backend; the past-sessions list
    // restores it. Clear only the per-session UI state that is not
    // persisted (outcome).
    setSession(null); setContext(null);
    setOutcomeResult(null);
  }

  async function begin(){
    if(!productId||!revisionId||!symptom.trim()) return;
    setBusy(true);setError("");
    try {
      const s=await startDiagnostic({product_id:productId,revision_id:revisionId,symptom:symptom.trim()});
      setSession(s);
      setContext(await getDiagnosticContext(s.session_id));
      // Observations/measurements go through the live /respond flow:
      // the start schema accepts symptom only, so a real observation
      // typed at setup is sent as the first observation turn, not
      // dropped and not invented into the start payload.
      const obs = observations.trim();
      if (obs) {
        const s2 = await respondDiagnostic(s.session_id, {input: obs, input_kind: "observation"});
        setSession(s2);
        setContext(await getDiagnosticContext(s2.session_id));
      }
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

  async function submitOutcome(){
    if(!session) return;
    setFeedbackBusy(true); setError("");
    try { setOutcomeResult(await recordDiagnosticOutcome(session.session_id, outcomeForm)); }
    catch(e){setError(e.message)} finally{setFeedbackBusy(false)}
  }

  const state=session?.state;
  // Phase 3 frontend step: the tournament runs inside POST /respond
  // (contracts §2.5) — there is no endpoint to call for it. Its
  // results arrive on the session timeline as early_tournament entries
  // (results: cause/status/verdict/reason, counts, discriminating_test)
  // or as a twin_unavailable entry when no twin could run. The cards
  // below read that timeline state; nothing here calls a deleted
  // endpoint, and no verification is shown before a turn has run one.
  const timeline = state?.timeline || [];
  const latestTournament = [...timeline].reverse().find(t => t.kind === "early_tournament") || null;
  const latestTwinEntry = [...timeline].reverse().find(t => t.kind === "early_tournament" || t.kind === "twin_unavailable") || null;
  const discriminatingTest = latestTournament?.discriminating_test || null;
  const step=state?.current_step;
  const leading=state?.hypotheses?.[0];
  const isQuestion=step?.type==="question";
  const isConclusion=step?.type==="conclusion";
  const machine=context?.product;
  const revision=context?.revision;
  const docs=context?.documents||[];
  const evidence=state?.evidence||[];
  const selectedMachine = machines.find(m => m.id === productId);
  const selectedRevision = revisions.find(r => r.id === revisionId);
  const machineLabel = selectedMachine ? `${selectedMachine.manufacturer} · ${selectedMachine.family} · ${selectedMachine.model}${selectedRevision ? ` — ${selectedRevision.label}` : ""}` : "";

  if(!session) return <div className="diagnostic">
    {showOnboarding && <MachineOnboarding
      initialProductId={productId}
      initialRevisionId={revisionId}
      onCancel={()=>setShowOnboarding(false)}
      onReady={async ({productId: readyProductId, revisionId: readyRevisionId, knowledgeWarning})=>{
        setShowOnboarding(false);
        setError(knowledgeWarning || "");
        setProductId(readyProductId);
        setRevisionId(readyRevisionId);
        await onRefreshMachines?.();
        onMachineReady?.({ productId: readyProductId, revisionId: readyRevisionId });
      }}
    />}
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
        {machines.length > 0 ? <>
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
          {revisionId && revisionIngestion?.status === "ready" && <div className="ready-check"><StatusPill tone="ok">READY</StatusPill> {revisionIngestion.document_count} manual(s) ingested{revisionIngestion.active_count ? " · another manual is processing" : ""}</div>}
          {revisionId && revisionIngestion?.status === "processing" && <div className="ready-check"><StatusPill tone="live">INGESTING</StatusPill> {revisionIngestion.completed_count} completed manual(s) · {revisionIngestion.active_count} still processing</div>}
          {revisionId && revisionIngestion?.status === "failed" && <div className="ready-check"><StatusPill tone="warn">INGESTION FAILED</StatusPill> No completed manual is available yet. Open onboarding to retry from the checkpoint.</div>}
          <button className="secondary-button full-width" onClick={()=>setShowOnboarding(true)}>+ Add manual to this revision</button>
        </> : <div className="onboarding-empty">
          <div className="empty-icon">＋</div>
          <h3>No machines onboarded</h3>
          <p>Start by uploading a machine manual. Crater will create the machine, revision, extract knowledge, and make it available here.</p>
          <button className="primary-button" onClick={()=>setShowOnboarding(true)}>Upload manual & onboard machine</button>
        </div>}
      </Card>
      <Card title="2 · Symptom">
        <label>What is happening with the machine?
          <textarea value={symptom} onChange={e=>setSymptom(e.target.value)} rows={5} placeholder="Describe the symptom..."/>
        </label>
        <label>Observations / measurements (optional)
          <textarea value={observations} onChange={e=>setObservations(e.target.value)} rows={3} placeholder="What you have already seen or measured, e.g. voltage readings, sounds, smells..."/>
        </label>
        <button className="primary-button" disabled={busy||!productId||!revisionId||!symptom.trim()} onClick={begin}>
          {busy ? "Starting investigation…" : "Start investigation"}
        </button>
      </Card>
    </div>
    {productId && revisionId && <Card title="Past sessions" className="diag-past-sessions">
      {pastLoading ? <p className="muted">Loading past sessions…</p>
      : pastSessions.length ? <div className="model-card-list">{pastSessions.map(row => <div key={row.session_id} className="model-panel">
        <div className="model-card-head"><h3>{row.symptom || "No symptom recorded"}</h3><StatusPill tone={row.status==="active"?"live":row.status==="concluded"?"ok":"warn"}>{row.status}</StatusPill></div>
        <div className="muted">{row.turns_taken} turn(s) · confidence {Math.round(Number(row.confidence || 0)*100)}%{row.created_at ? ` · ${new Date(row.created_at).toLocaleString()}` : ""}</div>
        <button className="secondary-button" disabled={busy} onClick={()=>restoreSession(row)}>Restore session</button>
      </div>)}</div>
      : <p className="muted">No past sessions for this machine yet. Start an investigation above.</p>}
    </Card>}
    {error && <div className="diag-error">{error}</div>}
  </div>;

  return <div className="diagnostic diagnostic--session">
    <header className="diagnostic-header">
      <div>
        <div className="eyebrow">LIVE DIAGNOSTIC SESSION</div>
        <h2>{machine?.model || "Machine"} <span>/ {revision?.label || "Revision"}</span></h2>
      </div>
      <div className="header-actions"><StatusPill tone={session.status==="active"?"live":session.status==="concluded"?"ok":"warn"}>{session.status}</StatusPill><button onClick={backToSessions}>Sessions</button><button onClick={refresh} disabled={busy}>Refresh</button></div>
    </header>
    {error && <div className="diag-error">{error}</div>}
    <div className="diagnostic-grid">
      <aside className="diagnostic-left">
        <Card title="MACHINE CONTEXT">
          <div className="context-stat"><b>{context?.knowledge?.entity_count ?? 0}</b><span>entities</span></div>
          <div className="context-stat"><b>{context?.knowledge?.relation_count ?? 0}</b><span>relationships</span></div>
          <div className="context-stat"><b>{context?.knowledge?.conflict_count ?? 0}</b><span>conflicts</span></div>
          <div className="context-docs"><b>Sources</b>{docs.map(d=><span key={d.id}>{d.title}</span>)}</div>
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
            {evidence.map(e=><div className="repair-source" key={e.chunk_id}>↳ {e.source_document}{e.page_number?` · p.${e.page_number}`:""}</div>)}
          </div> : <div className="empty-state">A structured repair recommendation will appear after the diagnostic state reaches a grounded conclusion.</div>}
        </Card>
        <Card title="MOST INFORMATIVE NEXT TEST">
          <p className="muted">The discriminating test for the current hypotheses, picked by the turn itself (the early tournament runs inside each turn). This is a documented test that may separate the leading hypotheses — not calculated probabilistic information gain.</p>
          {discriminatingTest ? <article className="guided-check">
            <b>{discriminatingTest.test}</b>
            {discriminatingTest.rationale && <p>{discriminatingTest.rationale}</p>}
            {discriminatingTest.separates?.length > 0 && <small>May distinguish: {discriminatingTest.separates.join(", ")}</small>}
          </article> : <p className="muted">No documented discriminating test separated the current hypotheses yet — answer the question above and the next turn picks one.</p>}
        </Card>
        <Card title="DID THE REPAIR WORK?">
          {outcomeResult ? <div className="ready-check"><StatusPill tone="ok">RECORDED</StatusPill> Outcome saved · {outcomeResult.review_status}{outcomeResult.knowledge_version_id ? <p>Draft knowledge version: {outcomeResult.knowledge_version_id}. It stays pending review and does not influence diagnosis until reviewed.</p> : null}</div> : <>
            <label>Repair outcome<select value={outcomeForm.outcome} onChange={e=>setOutcomeForm(v=>({...v,outcome:e.target.value}))}>
              <option value="fixed">Fixed</option><option value="not_fixed">Not fixed</option><option value="inconclusive">Inconclusive / not yet verified</option>
            </select></label>
            <label>Confirmed cause / correction<input value={outcomeForm.confirmed_cause} onChange={e=>setOutcomeForm(v=>({...v,confirmed_cause:e.target.value}))} placeholder="What was actually wrong?"/></label>
            <label>Repair performed<input value={outcomeForm.repair_performed} onChange={e=>setOutcomeForm(v=>({...v,repair_performed:e.target.value}))} placeholder="What did the technician change?"/></label>
            <label>Notes<textarea rows={2} value={outcomeForm.technician_notes} onChange={e=>setOutcomeForm(v=>({...v,technician_notes:e.target.value}))} placeholder="What happened after repair?"/></label>
            <label className="feedback-checkbox"><input type="checkbox" checked={outcomeForm.submit_correction_for_review} onChange={e=>setOutcomeForm(v=>({...v,submit_correction_for_review:e.target.checked}))}/> Submit correction as draft knowledge for review</label>
            <button onClick={submitOutcome} disabled={feedbackBusy}>{feedbackBusy ? "Saving…" : "Save repair outcome"}</button>
          </>}
        </Card>
        <Card title="VERIFICATION">
          {!latestTwinEntry && <div className="verification-unavailable"><b>Simulation contract</b><p>Verification is only marked PASS/FAIL when a real digital-twin experiment is executed. No simulated success is shown here. The tournament runs inside each turn — answer the first question and its result appears here.</p></div>}
          {latestTwinEntry?.kind === "twin_unavailable" && <div className="verification-unavailable"><b>Cannot verify yet</b><p>Twin verification unavailable: {latestTwinEntry.reason}</p><button onClick={()=>onOpenSimulation?.({revisionId, sessionId:session?.session_id, hypothesis:leading?.cause || ""})}>Open simulation workspace</button></div>}
          {latestTwinEntry?.kind === "early_tournament" && <div className="verification-result">
            <StatusPill tone={latestTournament.counts?.validated ? "ok" : "warn"}>{latestTournament.counts?.validated ? "VALIDATED" : "NOT VALIDATED"}</StatusPill>
            <p className="muted">{latestTournament.counts?.validated || 0} validated · {latestTournament.counts?.rejected || 0} rejected · {latestTournament.counts?.unavailable || 0} unavailable — from the tournament that ran inside the latest turn. A rejected row is evidence, not an elimination.</p>
            <button onClick={()=>onOpenSimulation?.({revisionId, sessionId:session?.session_id, hypothesis:leading?.cause || ""})}>Open simulation workspace</button>
          </div>}
        </Card>
        {latestTournament && <Card title="HYPOTHESIS TOURNAMENT">
          <div className="tournament">
            <p className="muted">{latestTournament.counts?.validated || 0} validated · {latestTournament.counts?.rejected || 0} rejected · {latestTournament.counts?.unavailable || 0} unavailable</p>
            {(latestTournament.results || []).map((row,i)=><article className="tournament-row" key={`${row.cause}-${i}`}>
              <div className="hypothesis-top"><div><span className="rank">{String(i+1).padStart(2,"0")}</span><b>{row.cause}</b></div><StatusPill tone={row.status==="validated"?"ok":row.status==="rejected"?"warn":""}>{row.status==="validated"?"VALIDATED":row.status==="rejected"?"REJECTED":"UNAVAILABLE"}</StatusPill></div>
              {row.reason && <p className="muted">{row.reason}</p>}
              {row.cannot_express && <p className="muted">The twin cannot express this cause — no further observation can make it verifiable; the session ends this line with ranked guesses and a manual test instead.</p>}
            </article>)}
            <p className="muted">A rejected row is evidence, not an elimination — no hypothesis was changed by this run. Unavailable means the twin cannot evaluate that cause.</p>
          </div>
        </Card>}
      </aside>
    </div>
  </div>;
}
