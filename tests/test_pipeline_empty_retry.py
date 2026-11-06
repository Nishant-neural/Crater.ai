"""Pipeline empty-retry test (Nov 2026 batch fix).

A batch that parses as empty used to be marked knowledge_done and never
revisited. This test mocks the PDF/chunk/vector layers and the gateway
so no LLM, Qdrant, or real PDF is needed, then checks that a chunk
whose batch result is empty is retried as a single and its single
result is persisted, instead of being recorded as empty.
"""
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db.models import Base, Chunk, Component, DocType, UnresolvedFact
from backend.ingestion import pipeline as pl
from backend.ingestion.chunking import PendingChunk
from backend.db.models import ChunkType
from backend.knowledge import component_extraction as ce


def _empty_batch_json(chunk_ids):
    return json.dumps({"chunks": [
        {"source_chunk_id": cid, "knowledge": {"components": []}}
        for cid in chunk_ids
    ]})


def _single_json():
    return json.dumps({"components": [{"name": "Rescued Motor", "function": "Drives the fan"}]})


def test_empty_batch_chunk_is_retried_as_single(monkeypatch, tmp_path):
    # Gateway: batch calls (prompt contains MULTIPLE) return empty for
    # both chunks; single calls return one component.
    def fake_complete(task, messages, max_tokens=None):
        content = messages[0]["content"] if messages else ""
        if "MULTIPLE" in content:
            # Chunk ids are not known before chunking, so parse them out
            # of the batch prompt excerpts.
            import re
            ids = re.findall(r"SOURCE_CHUNK_ID: ([0-9a-f-]+)", content)
            return _empty_batch_json(ids)
        return _single_json()

    monkeypatch.setattr(ce.gateway, "complete", fake_complete)
    # Replace the PDF-loading wrapper directly: it is only used to get
    # a page list, and pages_to_chunks is mocked below, so the page
    # object only needs a page_number for the chunking check.
    monkeypatch.setattr(pl, "_load_pages_optional_ocr", lambda pdf_path, image_out_dir: ([type("P", (), {"page_number": 1})()], None))

    def fake_chunks(pdf_path, pages):
        return [
            PendingChunk(ChunkType.text, 1, "first chunk text", {}),
            PendingChunk(ChunkType.text, 2, "second chunk text", {}),
        ]

    monkeypatch.setattr(pl, "pages_to_chunks", fake_chunks)
    monkeypatch.setattr(pl, "upsert_chunks", lambda **kwargs: None)

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    doc = pl.ingest_pdf(
        session, tmp_path / "fake.pdf", revision_id="rev-1", product_id="prod-1",
        doc_type=DocType.manual, title="fake.pdf", image_out_dir=str(tmp_path),
        run_knowledge_extraction=True, run_schematic_extraction=False, job_id=None,
    )
    session.commit()

    # Both chunks were rescued by the single retry, so components exist
    # and no chunk_extraction_empty record was created for them.
    components = session.query(Component).all()
    assert [c.name for c in components] == ["Rescued Motor", "Rescued Motor"]
    empties = session.query(UnresolvedFact).filter_by(fact_type="chunk_extraction_empty").all()
    assert empties == []
    assert doc is not None


def test_empty_batch_chunk_stays_empty_when_single_also_empty(monkeypatch, tmp_path):
    # If the single retry also returns empty, the chunk must still be
    # recorded as empty and the job must complete, not fail.
    def fake_complete(task, messages, max_tokens=None):
        content = messages[0]["content"] if messages else ""
        if "MULTIPLE" in content:
            import re
            ids = re.findall(r"SOURCE_CHUNK_ID: ([0-9a-f-]+)", content)
            return _empty_batch_json(ids)
        return json.dumps({"components": []})

    monkeypatch.setattr(ce.gateway, "complete", fake_complete)
    monkeypatch.setattr(pl, "_load_pages_optional_ocr", lambda pdf_path, image_out_dir: ([type("P", (), {"page_number": 1})()], None))
    monkeypatch.setattr(pl, "pages_to_chunks", lambda pdf_path, pages: [
        PendingChunk(ChunkType.text, 1, "first chunk text", {}),
    ])
    monkeypatch.setattr(pl, "upsert_chunks", lambda **kwargs: None)

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    pl.ingest_pdf(
        session, tmp_path / "fake.pdf", revision_id="rev-1", product_id="prod-1",
        doc_type=DocType.manual, title="fake.pdf", image_out_dir=str(tmp_path),
        run_knowledge_extraction=True, run_schematic_extraction=False, job_id=None,
    )
    session.commit()
    assert session.query(Component).all() == []
    empties = session.query(UnresolvedFact).filter_by(fact_type="chunk_extraction_empty").all()
    assert len(empties) == 1
