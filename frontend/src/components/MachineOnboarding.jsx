import { useRef, useState } from "react";
import { createMachine, createRevision } from "../api/machines";
import {
  getIngestionJob,
  integrateKnowledge,
  uploadManual,
} from "../api/client";

const STAGES = {
  machine: "Creating machine…",
  revision: "Creating revision…",
  upload: "Uploading and ingesting manual…",
  knowledge: "Building machine knowledge…",
};

export default function MachineOnboarding({ onReady, onCancel }) {
  const [form, setForm] = useState({
    manufacturer: "",
    family: "",
    model: "",
    revision: "Rev A",
  });
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState("");
  const [error, setError] = useState("");
  const fileInputRef = useRef(null);

  function update(key, value) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function selectFile(event) {
    const selected = event.target.files?.[0] || null;
    setFile(selected);
    setError("");
  }

  async function submit(event) {
    event.preventDefault();

    const manufacturer = form.manufacturer.trim();
    const family = form.family.trim();
    const model = form.model.trim();
    const revisionLabel = form.revision.trim();

    if (!manufacturer || !family || !model || !revisionLabel) {
      setError("Enter manufacturer, product family, model, and revision.");
      return;
    }

    if (!file) {
      setError("Select a PDF machine manual.");
      return;
    }

    if (!file.name.toLowerCase().endsWith(".pdf")) {
      setError("Only PDF manuals are supported.");
      return;
    }

    setBusy(true);
    setError("");

    try {
      setStage(STAGES.machine);
      const product = await createMachine({
        manufacturer,
        family,
        model,
      });

      setStage(STAGES.revision);
      const revision = await createRevision(product.id, {
        label: revisionLabel,
      });

      const idempotencyKey = [
        revision.id,
        file.name,
        file.size,
        file.lastModified,
      ].join(":");

      setStage(STAGES.upload);
      const job = await uploadManual({
        revisionId: revision.id,
        file,
        title: file.name,
        idempotencyKey,
      });

      // Current backend ingestion is synchronous, but keep this guard so
      // the UI also works if ingestion is changed to a queued job later.
      let finalJob = job;
      if (job?.job_id && job.status !== "completed" && job.status !== "failed") {
        for (let attempt = 0; attempt < 60; attempt += 1) {
          await new Promise((resolve) => setTimeout(resolve, 1000));
          finalJob = await getIngestionJob(job.job_id);
          if (finalJob.status === "completed" || finalJob.status === "failed") break;
        }
      }

      if (finalJob?.status !== "completed") {
        throw new Error(
          finalJob?.error_message ||
            `Manual ingestion ended with status: ${finalJob?.status || "unknown"}`
        );
      }

      setStage(STAGES.knowledge);

      // Global integration can require an LLM configuration. The extracted
      // document/chunks remain usable even when this optional step fails.
      let knowledgeWarning = "";
      try {
        await integrateKnowledge(revision.id);
      } catch (integrationError) {
        knowledgeWarning =
          ` Manual ingested successfully, but global knowledge integration was not completed: ${integrationError.message}`;
      }

      onReady({
        productId: product.id,
        revisionId: revision.id,
        product,
        revision,
        ingestionJob: finalJob,
        knowledgeWarning,
      });
    } catch (err) {
      setError(err?.message || "Unable to onboard machine.");
    } finally {
      setBusy(false);
      setStage("");
    }
  }

  return (
    <div className="onboarding-overlay">
      <form className="onboarding-card" onSubmit={submit}>
        <div className="eyebrow">MACHINE ONBOARDING</div>
        <h2>Bring a machine into Crater.</h2>
        <p className="muted">
          Create the machine and revision, ingest its technical manual, then
          make that revision available to the diagnostic workstation.
        </p>

        <div className="onboarding-grid">
          <label>
            Manufacturer
            <input
              value={form.manufacturer}
              onChange={(e) => update("manufacturer", e.target.value)}
              placeholder="e.g. Mitsubishi Electric"
              disabled={busy}
            />
          </label>

          <label>
            Product family
            <input
              value={form.family}
              onChange={(e) => update("family", e.target.value)}
              placeholder="e.g. M800V"
              disabled={busy}
            />
          </label>

          <label>
            Model / variant
            <input
              value={form.model}
              onChange={(e) => update("model", e.target.value)}
              placeholder="e.g. M850VW"
              disabled={busy}
            />
          </label>

          <label>
            Revision
            <input
              value={form.revision}
              onChange={(e) => update("revision", e.target.value)}
              placeholder="e.g. Ver.M"
              disabled={busy}
            />
          </label>
        </div>

        <label
          className="file-drop"
          onClick={(e) => {
            if (e.target === e.currentTarget) fileInputRef.current?.click();
          }}
        >
          PDF service / technical manual
          <input
            ref={fileInputRef}
            type="file"
            accept="application/pdf,.pdf"
            onChange={selectFile}
            disabled={busy}
          />
          <span>
            {file ? `✓ ${file.name}` : "Choose a PDF manual"}
          </span>
        </label>

        {stage && (
          <div className="ingestion-progress">
            <span className="status-pill live">WORKING</span>
            <span>{stage}</span>
          </div>
        )}

        {error && <div className="diag-error">{error}</div>}

        <div className="onboarding-actions">
          <button type="button" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button className="primary-button" type="submit" disabled={busy}>
            {busy ? "Onboarding machine…" : "Ingest & start diagnosis"}
          </button>
        </div>
      </form>
    </div>
  );
}
