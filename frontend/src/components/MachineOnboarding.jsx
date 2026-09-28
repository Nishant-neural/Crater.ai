import { useEffect, useMemo, useState } from "react";
import { createMachine, createRevision } from "../api/machines";
import { getIngestionJob, integrateKnowledge, uploadManual } from "../api/client";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function parseStage(stage = "") {
  const parts = stage.split("|");
  if (parts.length >= 3 && /^\d+$/.test(parts[1])) {
    return { percent: Number(parts[1]), label: parts.slice(2).join("|") };
  }
  if (stage === "completed") return { percent: 100, label: "Ingestion complete" };
  if (stage === "failed") return { percent: 0, label: "Ingestion failed" };
  return { percent: 0, label: stage || "Queued" };
}

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** i).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export default function MachineOnboarding({ onReady, onCancel }) {
  const [form, setForm] = useState({ manufacturer: "", family: "", model: "", revision: "Rev A" });
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState("Ready to upload");
  const [error, setError] = useState("");
  const [uploadPercent, setUploadPercent] = useState(0);
  const [uploadBytes, setUploadBytes] = useState({ loaded: 0, total: 0 });
  const [processingPercent, setProcessingPercent] = useState(0);

  function update(key, value) { setForm((v) => ({ ...v, [key]: value })); }

  async function waitForJob(id) {
    for (;;) {
      const job = await getIngestionJob(id);
      const parsed = parseStage(job.stage);
      setProcessingPercent(parsed.percent);
      setStage(parsed.label);
      if (job.status === "completed") return job;
      if (job.status === "failed") throw new Error(job.error_message || "Manual ingestion failed.");
      await sleep(900);
    }
  }

  async function submit(e) {
    e.preventDefault();
    if (!form.manufacturer.trim() || !form.family.trim() || !form.model.trim() || !form.revision.trim() || !file) {
      setError("Enter the machine details and select a PDF manual.");
      return;
    }
    setBusy(true);
    setError("");
    setUploadPercent(0);
    setUploadBytes({ loaded: 0, total: file.size });
    setProcessingPercent(0);

    try {
      setStage("Creating machine…");
      const product = await createMachine({
        manufacturer: form.manufacturer.trim(),
        family: form.family.trim(),
        model: form.model.trim(),
      });

      setStage("Creating revision…");
      const revision = await createRevision(product.id, { label: form.revision.trim() });

      setStage("Uploading PDF…");
      const job = await uploadManual({
        revisionId: revision.id,
        file,
        title: file.name,
        idempotencyKey: `${revision.id}:${file.name}:${file.size}:${file.lastModified}`,
        onUploadProgress: ({ loaded, total, percent }) => {
          setUploadBytes({ loaded, total });
          setUploadPercent(percent);
        },
      });

      setUploadPercent(100);
      setUploadBytes({ loaded: file.size, total: file.size });
      setStage("Upload complete — processing manual…");

      // Poll here as well so the submit flow does not depend on the effect.
      await waitForJob(job.job_id);

      setStage("Building machine knowledge…");
      try {
        await integrateKnowledge(revision.id);
      } catch (knowledgeError) {
        setStage(`Manual ingested. Knowledge integration unavailable: ${knowledgeError.message}`);
      }

      onReady({ productId: product.id, revisionId: revision.id, product, revision });
    } catch (err) {
      setError(err.message || "Unable to onboard machine.");
    } finally {
      setBusy(false);
    }
  }

  const uploadText = useMemo(() => {
    if (!file) return "Choose a PDF manual";
    return `${file.name} · ${formatBytes(file.size)}`;
  }, [file]);

  const overallPercent = uploadPercent < 100
    ? Math.round(uploadPercent * 0.35)
    : Math.round(35 + processingPercent * 0.65);

  return (
    <div className="onboarding-overlay">
      <form className="onboarding-card" onSubmit={submit}>
        <div className="eyebrow">MACHINE ONBOARDING</div>
        <h2>Bring a machine into Crater.</h2>
        <p className="muted">Create its product/revision, upload the technical manual, build machine knowledge, then start diagnosis.</p>

        <div className="onboarding-grid">
          <label>Manufacturer<input value={form.manufacturer} onChange={(e) => update("manufacturer", e.target.value)} placeholder="e.g. Mitsubishi Electric" disabled={busy} /></label>
          <label>Product family<input value={form.family} onChange={(e) => update("family", e.target.value)} placeholder="e.g. M800V" disabled={busy} /></label>
          <label>Model<input value={form.model} onChange={(e) => update("model", e.target.value)} placeholder="e.g. M850VW" disabled={busy} /></label>
          <label>Revision<input value={form.revision} onChange={(e) => update("revision", e.target.value)} placeholder="e.g. Ver. M" disabled={busy} /></label>
        </div>

        <label className="file-drop">
          Service / technical manual (PDF)
          <input type="file" accept="application/pdf,.pdf" onChange={(e) => setFile(e.target.files?.[0] || null)} disabled={busy} />
          <span>{uploadText}</span>
        </label>

        {(busy || uploadPercent > 0 || processingPercent > 0) && (
          <div className="ingestion-progress-panel">
            <div className="progress-heading">
              <span>{stage}</span>
              <strong>{overallPercent}%</strong>
            </div>

            <div className="progress-track" aria-label="Overall onboarding progress">
              <div className="progress-fill" style={{ width: `${overallPercent}%` }} />
            </div>

            <div className="progress-details">
              <div>
                <span className={uploadPercent >= 100 ? "step done" : "step live"}>●</span>
                <span>PDF upload</span>
                <strong>{uploadPercent}%</strong>
              </div>
              <div>
                <span className={processingPercent >= 100 ? "step done" : processingPercent > 0 ? "step live" : "step"}>●</span>
                <span>Manual processing</span>
                <strong>{processingPercent}%</strong>
              </div>
            </div>

            {uploadBytes.total > 0 && uploadPercent < 100 && (
              <div className="progress-subtext">
                {formatBytes(uploadBytes.loaded)} / {formatBytes(uploadBytes.total)} uploaded
              </div>
            )}
          </div>
        )}

        {error && <div className="diag-error">{error}</div>}

        <div className="onboarding-actions">
          <button type="button" onClick={onCancel} disabled={busy}>Cancel</button>
          <button className="primary-button" type="submit" disabled={busy}>
            {busy ? "Processing manual…" : "Upload & start diagnosis"}
          </button>
        </div>
      </form>
    </div>
  );
}
