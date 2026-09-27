import { useState } from "react";
import { createMachine, createRevision } from "../api/machines";
import { integrateKnowledge, uploadManual } from "../api/client";

export default function MachineOnboarding({ onReady, onCancel }) {
  const [form, setForm] = useState({ manufacturer: "", family: "", model: "", revision: "Rev A" });
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState("");
  const [error, setError] = useState("");

  function update(key, value) { setForm((v) => ({ ...v, [key]: value })); }

  async function submit(e) {
    e.preventDefault();
    if (!form.manufacturer.trim() || !form.family.trim() || !form.model.trim() || !form.revision.trim() || !file) {
      setError("Enter the machine details and select a PDF manual.");
      return;
    }
    setBusy(true); setError("");
    try {
      setStage("Creating machine…");
      const product = await createMachine({ manufacturer: form.manufacturer.trim(), family: form.family.trim(), model: form.model.trim() });
      setStage("Creating revision…");
      const revision = await createRevision(product.id, { label: form.revision.trim() });
      setStage("Ingesting manual…");
      const job = await uploadManual({ revisionId: revision.id, file, title: file.name, idempotencyKey: `${revision.id}:${file.name}:${file.size}:${file.lastModified}` });
      if (job.status !== "completed") throw new Error(job.error_message || `Ingestion ended with status: ${job.status}`);
      setStage("Building machine knowledge…");
      try { await integrateKnowledge(revision.id); } catch (knowledgeError) {
        // A manual can still be diagnosed using the extracted legacy knowledge if global integration is unavailable.
        setStage(`Manual ingested. Knowledge integration unavailable: ${knowledgeError.message}`);
      }
      onReady({ productId: product.id, revisionId: revision.id, product, revision });
    } catch (err) {
      setError(err.message || "Unable to onboard machine.");
    } finally { setBusy(false); }
  }

  return <div className="onboarding-overlay">
    <form className="onboarding-card" onSubmit={submit}>
      <div className="eyebrow">MACHINE ONBOARDING</div>
      <h2>Bring a machine into Crater.</h2>
      <p className="muted">Create its product/revision, ingest the service manual, build machine knowledge, then start diagnosis.</p>
      <div className="onboarding-grid">
        <label>Manufacturer<input value={form.manufacturer} onChange={(e)=>update("manufacturer",e.target.value)} placeholder="e.g. Acme" /></label>
        <label>Product family<input value={form.family} onChange={(e)=>update("family",e.target.value)} placeholder="e.g. CNC" /></label>
        <label>Model<input value={form.model} onChange={(e)=>update("model",e.target.value)} placeholder="e.g. CNC-500X" /></label>
        <label>Revision<input value={form.revision} onChange={(e)=>update("revision",e.target.value)} placeholder="e.g. Rev C" /></label>
      </div>
      <label className="file-drop">Service / technical manual (PDF)
        <input type="file" accept="application/pdf,.pdf" onChange={(e)=>setFile(e.target.files?.[0] || null)} />
        <span>{file ? file.name : "Choose a PDF manual"}</span>
      </label>
      {stage && <div className="ingestion-progress"><span className="status-pill live">{busy ? "WORKING" : "DONE"}</span>{stage}</div>}
      {error && <div className="diag-error">{error}</div>}
      <div className="onboarding-actions">
        <button type="button" onClick={onCancel} disabled={busy}>Cancel</button>
        <button className="primary-button" type="submit" disabled={busy}>{busy ? "Building machine…" : "Ingest & start diagnosis"}</button>
      </div>
    </form>
  </div>;
}
