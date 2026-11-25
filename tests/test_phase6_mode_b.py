"""Phase 6 tests: Mode B on-demand path.

Hermetic: in-memory SQLite + TestClient. No PDF, no LLM, no Qdrant —
the cold-start job is fabricated at the seam the endpoints read: an
IngestionJob row (processing, Tier 2 deferred), Chunk rows for the
manual's pages, a checkpoint dict (monkeypatched load_checkpoint), and
a MachineKnowledgeModelSnapshot row standing in for the pipeline's
partial model (built from Tier 1 knowledge). The diagnosis-agent
HTTP call is stubbed (no LLM) for the ready path.
"""
import sys
import types

_bm25 = types.ModuleType("rank_bm25")


class _BM25Okapi:
    def __init__(self, *args, **kwargs):
        pass


_bm25.BM25Okapi = _BM25Okapi
sys.modules.setdefault("rank_bm25", _bm25)

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.models import (
    Base, Chunk, ChunkType, DiagnosticSession, DiagnosticStatus,
    DocType, Document, IngestionJob, IngestionStatus,
    MachineKnowledgeModelSnapshot, Product, Revision,
)

CASE1_SYMPTOM = "Wire Feed Motor Runs but Wire Does Not Feed Properly"
CASE5_SYMPTOM = "Welder Does Not Function When Switched On"
TIER1_PAGES = [7, 12, 13, 17, 41, 42, 43]
TIER2_SAMPLE_PAGES = [19, 20, 21]


def _db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


@pytest.fixture()
def cold_start(monkeypatch):
    """A Mode B cold start: Tier 1 extracted, Tier 2 deferred, the job
    still processing in the background, the partial model snapshotted."""
    db = _db()
    product = Product(manufacturer="Harbor Freight", family="OMNIPRO", model="220")
    db.add(product)
    db.flush()
    revision = Revision(product_id=product.id, label="Rev A")
    db.add(revision)
    db.flush()
    document = Document(revision_id=revision.id, doc_type=DocType.manual,
                        title="OMNIPRO 220 Owner's Manual", source_path="/tmp/omnipro.pdf",
                        page_count=48)
    db.add(document)
    db.flush()
    knowledge_done, tier2_deferred = [], []
    for page in TIER1_PAGES:
        chunk = Chunk(document_id=document.id, chunk_type=ChunkType.text,
                      page_number=page, content=f"Tier 1 content p{page}",
                      extra={"pages": [page], "source_type": "manual"})
        db.add(chunk)
        db.flush()
        knowledge_done.append(chunk.id)
    for page in TIER2_SAMPLE_PAGES:
        chunk = Chunk(document_id=document.id, chunk_type=ChunkType.text,
                      page_number=page, content=f"Tier 2 content p{page}",
                      extra={"pages": [page], "source_type": "manual"})
        db.add(chunk)
        db.flush()
        tier2_deferred.append(chunk.id)
    job = IngestionJob(revision_id=revision.id, idempotency_key="mode-b-cold-start",
                       content_sha256="0" * 64, doc_type=DocType.manual,
                       title="OMNIPRO 220 Owner's Manual", source_path="/tmp/omnipro.pdf",
                       document_id=document.id, status=IngestionStatus.processing,
                       stage="processing|45|Building canonical machine knowledge")
    db.add(job)
    db.flush()
    db.add(MachineKnowledgeModelSnapshot(revision_id=revision.id, version=1,
                                         model={"components": []}))
    db.commit()
    checkpoint = {"stage": "knowledge",
                  "completed_pages": sorted(TIER1_PAGES + TIER2_SAMPLE_PAGES),
                  "knowledge_done": knowledge_done,
                  "tier2_deferred": tier2_deferred,
                  "ingest_tier2": False, "integration_done": True}
    store = {"checkpoint": dict(checkpoint)}

    import backend.api.routes.mode_b as mode_b_routes
    import backend.ingestion.checkpoints as checkpoints

    def fake_load(job_id):
        return dict(store["checkpoint"])

    def fake_save(job_id, **updates):
        store["checkpoint"].update(updates)
        return dict(store["checkpoint"])

    monkeypatch.setattr(mode_b_routes, "load_checkpoint", fake_load)
    monkeypatch.setattr(checkpoints, "load_checkpoint", fake_load)
    monkeypatch.setattr(checkpoints, "save_checkpoint", fake_save)

    # Stub the diagnosis-agent HTTP call: no LLM — a row, like the
    # Phase 4/5 tests do. Phase 3 extraction: Mode B diagnose calls
    # the diagnosis-agent service over HTTP instead of importing
    # start_session, so the stub replaces _start_diagnosis_session.
    def fake_start_diagnosis_session(product_id, revision_id, symptom):
        from backend.diagnostics.schema import DiagnosticState
        row = DiagnosticSession(
            product_id=product_id, revision_id=revision_id,
            status=DiagnosticStatus.active,
            state=DiagnosticState(symptoms=[symptom]).model_dump(mode="json"))
        db.add(row)
        db.commit()
        return {"session_id": row.id}

    monkeypatch.setattr(mode_b_routes, "_start_diagnosis_session",
                        fake_start_diagnosis_session)
    return {"db": db, "product": product, "revision": revision,
            "document": document, "job": job, "store": store}


def _client(db):
    from backend.api.routes import mode_b as mode_b_routes
    from backend.db.session import get_session
    app = FastAPI()
    app.include_router(mode_b_routes.router)

    def _override():
        yield db

    app.dependency_overrides[get_session] = _override
    return TestClient(app)


def test_mode_b_progress_shows_per_section_progress(cold_start):
    db, job = cold_start["db"], cold_start["job"]
    client = _client(db)
    resp = client.get(f"/mode-b/jobs/{job.id}/progress")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "on_demand"
    assert body["status"] == "processing"  # background ingestion proceeds
    assert body["ingest_tier2"] is False
    sections = {s["tier"]: s for s in body["sections"]}
    assert sections[1]["status"] == "done"
    assert sections[1]["pages_done"] == 7
    assert sections[1]["percent"] == 100
    assert sections[2]["status"] == "in_progress"
    assert sections[2]["pages_deferred"] == 3
    assert sections[3]["status"] == "skipped"
    assert body["partial_model"]["available"] is True
    assert body["tier2_deferred_chunks"] == 3


def test_mode_b_diagnoses_case1_from_partial_model_while_background_ingests(cold_start):
    db, job = cold_start["db"], cold_start["job"]
    client = _client(db)
    resp = client.post(f"/mode-b/jobs/{job.id}/diagnose",
                       json={"symptom": CASE1_SYMPTOM})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["available"] is True
    assert body["case_id"] == "omni-01-wire-not-feeding"
    assert body["session_id"]
    session = db.get(DiagnosticSession, body["session_id"])
    assert session is not None
    # The background job is untouched: still processing, Tier 2 still deferred.
    db.refresh(job)
    assert job.status == IngestionStatus.processing
    assert len(cold_start["store"]["checkpoint"]["tier2_deferred"]) == 3


def test_mode_b_symptom_needing_tier2_section_says_so_plainly(cold_start):
    db, job = cold_start["db"], cold_start["job"]
    client = _client(db)
    # Case 5's referenced page 19 is Tier 2 and still deferred.
    resp = client.post(f"/mode-b/jobs/{job.id}/diagnose",
                       json={"symptom": CASE5_SYMPTOM})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["available"] is False
    assert body["reason"] == "section_not_yet_ingested"
    assert body["case_id"] == "omni-05-does-not-function"
    assert "Tier 2" in body["wait_message"]
    assert "19" in body["wait_message"]
    assert body["missing_pages"] == [19]
    assert body["pages_to_lazy_ingest"] == [19]
    # No session was started on pages that do not exist yet.
    assert db.query(DiagnosticSession).count() == 0


def test_mode_b_diagnose_before_partial_model_waits(cold_start):
    db, job = cold_start["db"], cold_start["job"]
    cold_start["store"]["checkpoint"]["integration_done"] = False
    cold_start["store"]["checkpoint"]["knowledge_done"] = []
    db.query(MachineKnowledgeModelSnapshot).delete()
    db.commit()
    client = _client(db)
    resp = client.post(f"/mode-b/jobs/{job.id}/diagnose",
                       json={"symptom": CASE1_SYMPTOM})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["available"] is False
    assert body["reason"] == "partial_model_not_ready"
    assert "still ingesting" in body["wait_message"]
    assert "wait" in body["wait_message"].lower()


def test_mode_b_unmatched_symptom_starts_by_composition(cold_start):
    db, job = cold_start["db"], cold_start["job"]
    client = _client(db)
    resp = client.post(f"/mode-b/jobs/{job.id}/diagnose",
                       json={"symptom": "The welder makes a strange buzzing noise"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["available"] is True
    assert body["case_id"] is None
    assert "composition" in body["note"]


def test_mode_b_start_registers_on_demand_and_graduate_to_prebuilt(cold_start):
    db, job, product = cold_start["db"], cold_start["job"], cold_start["product"]
    client = _client(db)
    resp = client.post("/mode-b/start",
                       json={"revision_id": cold_start["revision"].id, "job_id": job.id})
    assert resp.status_code == 200, resp.text
    assert resp.json()["mode"] == "on_demand"
    mode = client.get(f"/mode-b/products/{product.id}").json()
    assert mode["mode"] == "on_demand"

    resp = client.post(f"/mode-b/products/{product.id}/graduate",
                       json={"actor": "Nishant", "job_id": job.id})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "prebuilt"
    assert body["tier2_chunks_released"] == 3
    assert body["ingest_tier2"] is True
    checkpoint = cold_start["store"]["checkpoint"]
    assert checkpoint["tier2_deferred"] == []
    assert checkpoint["graduation_released"] and len(checkpoint["graduation_released"]) == 3
    assert checkpoint["graduated_to_prebuilt"] is True
    mode = client.get(f"/mode-b/products/{product.id}").json()
    assert mode["mode"] == "prebuilt"
    assert mode["graduated_by"] == "Nishant"
    # Graduation without a named actor is refused.
    resp = client.post(f"/mode-b/products/{product.id}/graduate",
                       json={"actor": "  ", "job_id": job.id})
    assert resp.status_code == 422
