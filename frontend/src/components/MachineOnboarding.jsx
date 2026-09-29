import { useEffect, useMemo, useState } from "react";
import { createMachine, createRevision, listMachines, listRevisions } from "../api/machines";
import {
  getIngestionJob,
  getRevisionIngestionStatus,
  integrateKnowledge,
  retryIngestionJob,
  uploadManual,
} from "../api/client";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function parseStage(stage = "") {
  const p = stage.split("|");
  const percent = Number.isFinite(Number(p[1])) ? Number(p[1]) : stage.startsWith("completed") ? 100 : 0;
  return { percent: Math.max(0, Math.min(100, percent)), label: p[2] || p[0] || "Queued" };
}
function bytes(n) {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB"];
  const i = Math.min(Math.floor(Math.log(n) / Math.log(1024)), 3);
  return `${(n / 1024 ** i).toFixed(i ? 1 : 0)} ${u[i]}`;
}

export default function MachineOnboarding({ onReady, onCancel, initialProductId = "", initialRevisionId = "" }) {
  const [form, setForm] = useState({ manufacturer: "", family: "", model: "", revision: "Rev A" });
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState(null);
  const [stage, setStage] = useState("Ready to upload");
  const [error, setError] = useState("");
  const [upload, setUpload] = useState(0);
  const [uploadBytes, setUploadBytes] = useState({ loaded: 0, total: 0 });
  const [processing, setProcessing] = useState(0);
  const [existingMode, setExistingMode] = useState(Boolean(initialProductId && initialRevisionId));
  const [machines, setMachines] = useState([]);
  const [revisions, setRevisions] = useState([]);
  const [selectedProductId, setSelectedProductId] = useState(initialProductId);
  const [selectedRevisionId, setSelectedRevisionId] = useState(initialRevisionId);
  const [restoredStatus, setRestoredStatus] = useState(null);

  const update = (k, v) => setForm((x) => ({ ...x, [k]: v }));

  async function watch(jobId) {
    for (;;) {
      const j = await getIngestionJob(jobId);
      setJob(j);
      const s = parseStage(j.stage);
      setProcessing(j.percent ?? s.percent);
      setStage(j.stage_label || s.label);
      if (j.status === "completed" || j.status === "failed") return j;
      await sleep(900);
    }
  }

  async function restoreRevisionJobs(revisionId) {
    if (!revisionId) return;
    try {
      const status = await getRevisionIngestionStatus(revisionId);
      setRestoredStatus(status);
      const latest = status?.jobs?.find((j) => ["processing", "queued", "failed"].includes(j.status));
      if (latest) {
        setJob(latest);
        setStage(latest.stage_label || "Recovered ingestion job");
        setProcessing(latest.percent || 0);
        if (latest.status === "processing" || latest.status === "queued") await watch(latest.job_id);
      }
    } catch (e) {
      setError(e.message || "Unable to restore ingestion state.");
    }
  }

  useEffect(() => {
    if (!existingMode) return;
    listMachines()
      .then((rows) => setMachines(rows || []))
      .catch(() => {});
  }, [existingMode]);

  useEffect(() => {
    if (!existingMode || !selectedProductId) return;
    listRevisions(selectedProductId)
      .then((rows) => setRevisions(rows || []))
      .catch(() => {});
  }, [existingMode, selectedProductId]);

  useEffect(() => {
    if (existingMode && selectedRevisionId) restoreRevisionJobs(selectedRevisionId);
  }, [existingMode, selectedRevisionId]);

  async function retry() {
    if (!job) return;
    setBusy(true);
    setError("");
    try {
      const j = await retryIngestionJob(job.job_id);
      setJob(j);
      setStage("Retry queued — resuming from saved checkpoint…");
      const done = await watch(j.job_id);
      if (done.status === "failed") setError(done.error_message || "Ingestion failed again.");
      else await finish(done);
    } catch (e) {
      setError(e.message || "Retry failed.");
    } finally {
      setBusy(false);
    }
  }

  async function finish(done) {
    const rid = done.revision_id || selectedRevisionId || job?.revision_id;
    const pid = selectedProductId || job?.product_id;
    setStage("Building machine knowledge…");
    try { await integrateKnowledge(rid); } catch (_) {}
    onReady({ productId: pid, revisionId: rid });
  }

  async function submit(e) {
    e.preventDefault();
    if (!file) { setError("Select a PDF manual."); return; }
    if (!existingMode && (!form.manufacturer.trim() || !form.family.trim() || !form.model.trim() || !form.revision.trim())) {
      setError("Enter the machine details and revision."); return;
    }
    if (existingMode && !selectedRevisionId) { setError("Select a machine revision."); return; }

    setBusy(true); setError(""); setJob(null); setUpload(0); setProcessing(0);
    try {
      let product;
      let revision;
      if (existingMode) {
        product = machines.find((x) => x.id === selectedProductId);
        revision = revisions.find((x) => x.id === selectedRevisionId) || { id: selectedRevisionId };
      } else {
        setStage("Creating machine…");
        product = await createMachine({ manufacturer: form.manufacturer.trim(), family: form.family.trim(), model: form.model.trim() });
        setStage("Creating revision…");
        revision = await createRevision(product.id, { label: form.revision.trim() });
      }

      setStage("Uploading PDF…");
      const j = await uploadManual({
        revisionId: revision.id,
        file,
        title: file.name,
        idempotencyKey: `${revision.id}:${crypto.randomUUID()}`,
        onUploadProgress: ({ loaded, total, percent }) => {
          setUpload(percent); setUploadBytes({ loaded, total });
        },
      });
      setUpload(100); setUploadBytes({ loaded: file.size, total: file.size });
      setJob({ ...j, revision_id: revision.id, product_id: product?.id });
      const done = await watch(j.job_id);
      if (done.status === "failed") { setError(done.error_message || "Ingestion failed."); return; }
      await finish({ ...done, revision_id: revision.id, product_id: product?.id });
    } catch (e) {
      setError(e.message || "Unable to ingest manual.");
    } finally {
      setBusy(false);
    }
  }

  const uploadText = useMemo(() => file ? `${file.name} · ${bytes(file.size)}` : "Choose a PDF manual", [file]);
  const overall = upload < 100 ? Math.round(upload * 0.35) : Math.round(35 + processing * 0.65);
  const failed = job?.status === "failed";
  const ready = job?.status === "completed";

  return <div className="onboarding-overlay">
    <form className="onboarding-card" onSubmit={submit}>
      <div className="eyebrow">MACHINE ONBOARDING</div>
      <h2>{existingMode ? "Add another manual to this revision." : "Bring a machine into Crater."}</h2>
      <p className="muted">Manuals are stored independently under a revision. A failed extraction keeps its completed chunks and can resume from its checkpoint.</p>

      <div className="onboarding-mode">
        <button type="button" className={existingMode ? "primary-button" : "secondary-button"} onClick={() => setExistingMode(true)} disabled={busy}>Add to existing revision</button>
        <button type="button" className={!existingMode ? "primary-button" : "secondary-button"} onClick={() => { setExistingMode(false); setJob(null); setRestoredStatus(null); }} disabled={busy}>New machine / revision</button>
      </div>

      {existingMode ? <div className="onboarding-grid">
        <label>Machine<select value={selectedProductId} onChange={(e) => { setSelectedProductId(e.target.value); setSelectedRevisionId(""); }} disabled={busy}>
          <option value="">Select machine</option>
          {machines.map((m) => <option key={m.id} value={m.id}>{m.manufacturer} · {m.family} · {m.model}</option>)}
        </select></label>
        <label>Revision<select value={selectedRevisionId} onChange={(e) => setSelectedRevisionId(e.target.value)} disabled={busy || !selectedProductId}>
          <option value="">Select revision</option>
          {revisions.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
        </select></label>
      </div> : <div className="onboarding-grid">
        <label>Manufacturer<input value={form.manufacturer} onChange={(e) => update("manufacturer", e.target.value)} placeholder="e.g. Mitsubishi Electric" disabled={busy}/></label>
        <label>Product family<input value={form.family} onChange={(e) => update("family", e.target.value)} placeholder="e.g. M800V" disabled={busy}/></label>
        <label>Model<input value={form.model} onChange={(e) => update("model", e.target.value)} placeholder="e.g. M850VW" disabled={busy}/></label>
        <label>Revision<input value={form.revision} onChange={(e) => update("revision", e.target.value)} placeholder="e.g. Ver. M" disabled={busy}/></label>
      </div>}

      <label className="file-drop">Service / technical manual (PDF)
        <input type="file" accept="application/pdf,.pdf" onChange={(e) => setFile(e.target.files?.[0] || null)} disabled={busy}/>
        <span>{uploadText}</span>
      </label>

      {restoredStatus && <div className="progress-subtext">Revision contains {restoredStatus.document_count} manual(s). {restoredStatus.active_count ? "An ingestion is still running and remains resumable if this window is closed." : ""}{restoredStatus.failed_count ? " A failed ingestion is available to retry." : ""}</div>}

      {(busy || job) && <div className={`ingestion-progress-panel status-${failed ? "failed" : ready ? "ready" : "processing"}`}>
        <div className="progress-heading"><span>{failed ? "Ingestion failed" : ready ? "Ingestion ready" : stage}</span><strong>{ready ? 100 : overall}%</strong></div>
        <div className="progress-track"><div className="progress-fill" style={{ width: `${ready ? 100 : overall}%` }}/></div>
        <div className="progress-details"><div><span className={upload >= 100 ? "step done" : "step live"}>●</span><span>PDF upload</span><strong>{upload}%</strong></div><div><span className={processing >= 100 ? "step done" : processing > 0 ? "step live" : "step"}>●</span><span>Manual processing</span><strong>{processing}%</strong></div></div>
        {uploadBytes.total > 0 && upload < 100 && <div className="progress-subtext">{bytes(uploadBytes.loaded)} / {bytes(uploadBytes.total)} uploaded</div>}
        {job?.ocr_warning && <div className="progress-subtext">⚠ {job.ocr_warning}</div>}
        {failed && <><div className="diag-error">{job.error_message || error || "The pipeline stopped. Your uploaded PDF is retained."}</div><div className="onboarding-actions"><button type="button" className="primary-button" onClick={retry} disabled={busy}>Retry from saved checkpoint</button></div></>}
        {ready && <div className="progress-subtext">✓ PDF retained · ✓ ingestion complete · manual is part of this revision</div>}
      </div>}
      {error && !failed && <div className="diag-error">{error}</div>}
      <div className="onboarding-actions"><button type="button" onClick={onCancel} disabled={busy}>Close</button>{!failed && <button className="primary-button" type="submit" disabled={busy || ready}>{ready ? "Ready" : "Upload & ingest manual"}</button>}</div>
    </form>
  </div>;
}
