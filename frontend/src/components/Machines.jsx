import { useEffect, useState } from "react";
import { listMachines, listRevisions } from "../api/machines";
import { getMachineModel } from "../api/knowledge";
import { getRevisionIngestionStatus, getRevisionSimulation, integrateKnowledge } from "../api/client";
import MachineOnboarding from "./MachineOnboarding";

const STAGES = ["Upload", "Extracting", "Integrate", "Ready"];

function stageOf(ingestion, hasModel) {
  if (!ingestion) return { index: 0, label: "Upload", tone: "" };
  if (ingestion.status === "processing") return { index: 1, label: "Extracting", tone: "live" };
  if (ingestion.status === "failed") return { index: 1, label: "Ingestion failed", tone: "warn" };
  if (ingestion.status === "ready" && !hasModel) return { index: 2, label: "Integrate", tone: "live" };
  if (ingestion.status === "ready" && hasModel) return { index: 3, label: "Ready", tone: "ok" };
  return { index: 0, label: "Upload", tone: "" };
}

function Pipeline({ current }) {
  return <ol className="pipeline">
    {STAGES.map((label, i) => <li key={label} className={i < current ? "pipeline__step done" : i === current ? "pipeline__step live" : "pipeline__step"}><span>{i + 1}</span>{label}</li>)}
  </ol>;
}

function RevisionCard({ product, revision, selected, onSelectMachine, onCompileTwin, onOpenTwin, onDiagnose, onViewModel, onAddManual }) {
  const [ingestion, setIngestion] = useState(null);
  const [model, setModel] = useState(null);
  const [twin, setTwin] = useState(null);
  const [loading, setLoading] = useState(true);
  const [integrating, setIntegrating] = useState(false);
  const [integrateError, setIntegrateError] = useState("");

  async function refresh() {
    setLoading(true);
    const [ing, mod, tw] = await Promise.all([
      getRevisionIngestionStatus(revision.id).catch(() => null),
      getMachineModel(revision.id).catch(() => null),
      getRevisionSimulation(revision.id).catch(() => null),
    ]);
    setIngestion(ing); setModel(mod); setTwin(tw); setLoading(false);
  }
  useEffect(() => { refresh(); }, [revision.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const hasModel = !!model;
  const stage = stageOf(ingestion, hasModel);
  const entities = model?.model?.entities?.length ?? 0;
  const relations = model?.model?.relations?.length ?? 0;
  const conflicts = (model?.model?.conflicts?.length ?? 0) + (model?.model?.unresolved_facts?.length ?? 0);
  const twinComponents = twin?.definition?.components?.length ?? 0;
  const twinTransitions = twin?.definition?.transitions?.length ?? 0;
  const activeJob = ingestion?.jobs?.find((j) => ["processing", "queued"].includes(j.status));

  async function buildKnowledge() {
    setIntegrating(true); setIntegrateError("");
    try {
      await integrateKnowledge(revision.id);
      await refresh();
    } catch (e) { setIntegrateError(e.message || "Integration failed."); }
    finally { setIntegrating(false); }
  }

  let primary = null;
  if (loading) primary = <button className="primary-button" disabled>Loading…</button>;
  else if (stage.index === 0) primary = <button className="primary-button" onClick={() => onAddManual(product, revision)}>Add manual</button>;
  else if (stage.label === "Ingestion failed") primary = <button className="primary-button" onClick={() => onAddManual(product, revision)}>Retry ingestion</button>;
  else if (stage.index === 1) primary = <button className="primary-button" disabled title="Ingestion is still running">Ingesting…</button>;
  else if (stage.index === 2) primary = <button className="primary-button" onClick={buildKnowledge} disabled={integrating}>{integrating ? "Building…" : "Build machine knowledge"}</button>;
  else if (!twin) primary = <button className="primary-button" onClick={() => onCompileTwin({ productId: product.id, revisionId: revision.id })}>Compile twin</button>;
  else primary = <button className="primary-button" onClick={() => onOpenTwin({ productId: product.id, revisionId: revision.id })}>Open twin</button>;

  return <article className={selected ? "machine-card selected" : "machine-card"} onClick={() => onSelectMachine?.({ productId: product.id, revisionId: revision.id })}>
    <div className="machine-card__head">
      <div>
        <h3>{product.manufacturer} · {product.family} · {product.model}</h3>
        <p className="muted">{revision.label}{ingestion ? ` · ${ingestion.document_count} manual(s)` : ""}</p>
      </div>
      <div className="machine-card__actions">
        {primary}
        <button className="quiet-button" onClick={() => onDiagnose({ productId: product.id, revisionId: revision.id })}>Diagnose</button>
        <button className="quiet-button" onClick={() => onViewModel({ productId: product.id, revisionId: revision.id })}>View model</button>
      </div>
    </div>
    <Pipeline current={stage.index} />
    <div className="machine-card__stats">
      <span><b>{entities}</b> entities</span>
      <span><b>{relations}</b> relations</span>
      <span><b>{conflicts}</b> conflicts / unresolved</span>
      <span className={`status-pill ${stage.tone}`}>{stage.label}</span>
      {selected && <span className="status-pill ok">Selected</span>}
      {twin && <span className="muted">Twin {twin.snapshot?.status || ""} · {twinComponents} components · {twinTransitions} transitions</span>}
    </div>
    {stage.index === 1 && stage.label === "Extracting" && <p className="muted">{activeJob?.stage_label || "Extraction is running"} — a dense manual can take several minutes per batch.</p>}
    {integrateError && <div className="diag-error">{integrateError}</div>}
  </article>;
}

function ProductCard({ product, selectedRevisionId, onSelectMachine, ...rest }) {
  const [revisions, setRevisions] = useState([]);
  useEffect(() => { listRevisions(product.id).then((rows) => setRevisions(rows || [])).catch(() => {}); }, [product.id]);
  return <section className="machine-product">
    {revisions.map((r) => <RevisionCard key={r.id} product={product} revision={r} selected={r.id === selectedRevisionId} onSelectMachine={onSelectMachine} {...rest} />)}
    {revisions.length === 0 && <p className="muted">No revisions yet for {product.manufacturer} {product.model}.</p>}
  </section>;
}

export default function Machines({ onSelectMachine, selectedRevisionId, onCompileTwin, onOpenTwin, onDiagnose, onViewModel }) {
  const [machines, setMachines] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [onboarding, setOnboarding] = useState(null);

  async function load() {
    setLoading(true);
    try { setMachines((await listMachines()) || []); } catch (e) { setError(e.message || "Unable to load machines."); }
    finally { setLoading(false); }
  }
  useEffect(() => { load(); }, []);

  return <div className="machines">
    <header className="view-header">
      <div>
        <div className="eyebrow">MACHINES</div>
        <h2>Your machines</h2>
        <p className="muted">Onboard a manual, watch ingestion and integration, then compile the twin — all from here.</p>
      </div>
      <div className="view-header__actions">
        <button className="primary-button" onClick={() => setOnboarding({ mode: "new" })}>Onboard a machine</button>
      </div>
    </header>
    {error && <div className="diag-error">{error}</div>}
    {loading && <p className="muted">Loading machines…</p>}
    {!loading && machines.length === 0 && <div className="onboarding-empty">
      <div className="empty-icon">＋</div>
      <h3>No machines onboarded</h3>
      <p>Start by uploading a machine manual. Crater will create the machine, revision, extract knowledge, and make it available here.</p>
      <button className="primary-button" onClick={() => setOnboarding({ mode: "new" })}>Upload manual & onboard machine</button>
    </div>}
    {machines.map((m) => <ProductCard key={m.id} product={m} selectedRevisionId={selectedRevisionId} onSelectMachine={onSelectMachine} onCompileTwin={onCompileTwin} onOpenTwin={onOpenTwin} onDiagnose={onDiagnose} onViewModel={onViewModel} onAddManual={(product, revision) => setOnboarding({ mode: "existing", productId: product.id, revisionId: revision.id })} />)}
    {onboarding && <MachineOnboarding
      initialProductId={onboarding.mode === "existing" ? onboarding.productId : ""}
      initialRevisionId={onboarding.mode === "existing" ? onboarding.revisionId : ""}
      onCancel={() => setOnboarding(null)}
      onReady={async ({ productId, revisionId }) => {
        setOnboarding(null);
        await load();
        onSelectMachine?.({ productId, revisionId });
      }}
    />}
  </div>;
}
