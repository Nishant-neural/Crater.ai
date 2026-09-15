import { useEffect, useState } from "react";
import {
  listExperts, createExpertInterview, addExpertTurn, completeExpertInterview,
  extractExpertKnowledge, reviewExpertKnowledge, generateExpertGraph,
} from "../api/client";

export default function ExpertKnowledge() {
  const [experts, setExperts] = useState([]);
  const [expertId, setExpertId] = useState("");
  const [productId, setProductId] = useState("");
  const [revisionId, setRevisionId] = useState("");
  const [topic, setTopic] = useState("");
  const [interview, setInterview] = useState(null);
  const [answer, setAnswer] = useState("");
  const [knowledge, setKnowledge] = useState(null);
  const [graph, setGraph] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => { listExperts().then(setExperts).catch(e => setError(e.message)); }, []);

  async function start() {
    setBusy(true); setError("");
    try {
      setInterview(await createExpertInterview({
        expert_id: expertId, product_id: productId,
        revision_id: revisionId || null, topic,
      }));
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }

  async function send() {
    if (!answer.trim() || !interview) return;
    setBusy(true); setError("");
    try {
      setInterview(await addExpertTurn(interview.interview_id, {content: answer, speaker: "expert"}));
      setAnswer("");
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }

  async function finish() {
    setBusy(true); setError("");
    try { setInterview(await completeExpertInterview(interview.interview_id)); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  }

  async function extract() {
    setBusy(true); setError("");
    try { setKnowledge(await extractExpertKnowledge(interview.interview_id)); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  }

  async function review(decision) {
    setBusy(true); setError("");
    try {
      const v = await reviewExpertKnowledge(knowledge.id, {
        decision, reviewer: "technical-reviewer", notes: decision === "approved" ? "Reviewed in Phase 5 UI." : "Needs correction.",
      });
      setKnowledge(v);
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }

  async function buildGraph() {
    setBusy(true); setError("");
    try { setGraph(await generateExpertGraph(knowledge.id)); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  }

  if (!interview) return (
    <section className="expert">
      <h2>Capture Rajesh</h2>
      <p>Interview a senior engineer and convert tacit service knowledge into reviewable diagnostic rules.</p>
      <select value={expertId} onChange={e => setExpertId(e.target.value)}>
        <option value="">Select expert</option>
        {experts.map(e => <option key={e.id} value={e.id}>{e.name}{e.role ? ` — ${e.role}` : ""}</option>)}
      </select>
      <input value={productId} onChange={e => setProductId(e.target.value)} placeholder="Product ID" />
      <input value={revisionId} onChange={e => setRevisionId(e.target.value)} placeholder="Revision ID (optional)" />
      <input value={topic} onChange={e => setTopic(e.target.value)} placeholder="Failure/topic, e.g. spindle won't start" />
      <button disabled={busy || !expertId || !productId || !topic} onClick={start}>Start interview</button>
      {error && <p className="error">{error}</p>}
    </section>
  );

  return (
    <section className="expert">
      <h2>Expert Interview</h2>
      <p><b>Topic:</b> {interview.topic}</p>
      <div className="transcript">
        {interview.turns.map(t => <div key={t.id}><b>{t.speaker}:</b> {t.content}</div>)}
      </div>
      {interview.status === "active" && <>
        <div className="question">{interview.next_question || "Describe a concrete case."}</div>
        <textarea value={answer} onChange={e => setAnswer(e.target.value)} placeholder="Expert answer..." />
        <button disabled={busy || !answer.trim()} onClick={send}>Record answer</button>
        <button disabled={busy} onClick={finish}>Finish interview</button>
      </>}
      {interview.status === "completed" && !knowledge && <button disabled={busy} onClick={extract}>Extract knowledge</button>}
      {knowledge && <div className="knowledge-review">
        <h3>Knowledge v{knowledge.version} — {knowledge.status}</h3>
        {knowledge.items.map(i => (
          <article key={i.id}>
            <b>{i.title}</b>
            <p>{i.symptom || "No symptom"} → {i.action || i.failure_mode || "rule"}</p>
            {i.expected_observation && <small>Expected: {i.expected_observation}</small>}
            {i.source_quote && <blockquote>{i.source_quote}</blockquote>}
          </article>
        ))}
        {knowledge.status === "draft" && <>
          <button disabled={busy} onClick={() => review("approved")}>Approve</button>
          <button disabled={busy} onClick={() => review("rejected")}>Reject</button>
        </>}
        {knowledge.status === "approved" && <button disabled={busy} onClick={buildGraph}>Generate diagnostic graph</button>}
      </div>}
      {graph && <div><h3>Diagnostic Graph v{graph.version}</h3><p>{graph.nodes.length} nodes · {graph.edges.length} edges</p></div>}
      {error && <p className="error">{error}</p>}
    </section>
  );
}
