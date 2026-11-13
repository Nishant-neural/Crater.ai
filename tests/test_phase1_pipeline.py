"""Phase 1 pipeline wiring tests (§6.3 provenance, §6.4 tier gate).

Mocks the PDF/chunk/vector layers and the LLM gateway, like
tests/test_pipeline_empty_retry.py: no LLM, Qdrant, or real PDF needed.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db.models import Base, ChunkType, DocType, FailureMode, Product
from backend.ingestion import pipeline as pl
from backend.ingestion.chunking import PendingChunk
from backend.ingestion.chunking import _table_to_markdown
from backend.knowledge import component_extraction as ce

_P42_MD = _table_to_markdown([
    ["Problem", "Possible Causes", "Likely Solutions"],
    [
        "Wire Feed Motor Runs but Wire Does Not Feed Properly",
        "1. Insufficient wire feed pressure.\n2. Incorrect wire feed roller size.\n"
        "3. Damaged MIG Gun, cable, or liner assembly.\n4. Feed Tensioner is too tight.",
        "1. Increase wire feed pressure properly (step 27, page 17).\n"
        "2. Flip roll to correct size (page 12).\n"
        "3. Have a qualified technician inspect and replace as necessary.\n"
        "4. Loosen Feed Tensioner.",
    ],
])


def _setup(monkeypatch, tmp_path, chunks, product_id="prod-omni"):
    monkeypatch.setattr(
        pl, "_load_pages_optional_ocr",
        lambda pdf_path, image_out_dir: ([type("P", (), {"page_number": 1})()], None),
    )
    monkeypatch.setattr(pl, "pages_to_chunks", lambda pdf_path, pages: chunks)
    monkeypatch.setattr(pl, "upsert_chunks", lambda **kwargs: None)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Product(id=product_id, manufacturer="Harbor Freight", family="OMNIPRO", model="220"))
    session.commit()
    return session


def test_troubleshooting_table_chunk_parsed_deterministically_and_tier3_skipped(monkeypatch, tmp_path):
    llm_calls = []

    def fail_if_called(task, messages, max_tokens=None):
        llm_calls.append(task)
        raise AssertionError(f"LLM must not be called for deterministic/Tier-3 chunks (task={task})")

    monkeypatch.setattr(ce.gateway, "complete", fail_if_called)
    chunks = [
        PendingChunk(ChunkType.table, 42, _P42_MD, {"source_type": "troubleshooting_table", "pages": [42]}),
        PendingChunk(ChunkType.text, 35, "welding technique prose " * 20, {"source_type": "other", "pages": [35]}),
    ]
    session = _setup(monkeypatch, tmp_path, chunks)
    pl.ingest_pdf(
        session, tmp_path / "fake.pdf", revision_id="rev-1", product_id="prod-omni",
        doc_type=DocType.manual, title="fake.pdf", image_out_dir=str(tmp_path),
        run_knowledge_extraction=True, run_schematic_extraction=False, job_id=None,
    )
    session.commit()
    assert llm_calls == []
    modes = session.query(FailureMode).all()
    assert len(modes) == 1
    assert "Wire Feed Motor Runs" in modes[0].symptom
    assert modes[0].possible_causes == [
        "Insufficient wire feed pressure.",
        "Incorrect wire feed roller size.",
        "Damaged MIG Gun, cable, or liner assembly.",
        "Feed Tensioner is too tight.",
    ]


def test_other_product_keeps_v12_llm_path(monkeypatch, tmp_path):
    # A product the registry does not match must NOT be Tier-3 gated:
    # its chunks still go through the LLM extractor.
    called = []

    def fake_complete(task, messages, max_tokens=None):
        called.append(task)
        import json
        return json.dumps({"components": [{"name": "Other Motor", "function": "Drives"}]})

    monkeypatch.setattr(ce.gateway, "complete", fake_complete)
    chunks = [
        PendingChunk(ChunkType.text, 35, "some prose about another machine " * 10, {"source_type": "other", "pages": [35]}),
    ]
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Product(id="prod-other", manufacturer="Acme", family="CNC", model="500"))
    session.commit()
    monkeypatch.setattr(
        pl, "_load_pages_optional_ocr",
        lambda pdf_path, image_out_dir: ([type("P", (), {"page_number": 1})()], None),
    )
    monkeypatch.setattr(pl, "pages_to_chunks", lambda pdf_path, pages: chunks)
    monkeypatch.setattr(pl, "upsert_chunks", lambda **kwargs: None)
    pl.ingest_pdf(
        session, tmp_path / "fake.pdf", revision_id="rev-1", product_id="prod-other",
        doc_type=DocType.manual, title="fake.pdf", image_out_dir=str(tmp_path),
        run_knowledge_extraction=True, run_schematic_extraction=False, job_id=None,
    )
    session.commit()
    assert called, "non-registry product should still use the LLM path"
