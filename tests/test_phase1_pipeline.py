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


# ---------------------------------------------------------------------------
# Step 1 (Phase 1, P-01): a Miller §6-5 2-col chunk is parsed
# deterministically through ingest_pdf -- no LLM call, no fallback --
# even though no Miller registry gates this product (deterministic
# parsing is not registry-gated; only Tier-3 gating is).
# ---------------------------------------------------------------------------
_MILLER_6_5_MD = _table_to_markdown([
    ["Trouble", "Remedy"],
    ["No weld output; wire does not feed.", "Be sure line disconnect switch is On (see Section 4-13)."],
    ["", "Replace building line fuse or reset circuit breaker if open (see Section 4-13)."],
    ["", "Secure gun trigger connections (see welding gun Owner’s Manual)."],
    ["", "HL.P 001 appears on meters. Turn power switch off and back on, if HL.P 001 appears on meters again, have Factory Authorized Service Agent check unit."],
    ["", "HL.P 002 appears on meters. Thermistor T is detecting an overheating condition. Wait for unit to cool allowing the fan to run. After unit is cool, if HL.P 002 message remains, have Factory Authorized Service Agent check for an open Thermistor T (see Section 6-2)."],
    ["", "HL.P 004 appears on meters. Reset message by releasing the trigger or removing stuck wire causing short circuit (see Section 5-2) . If message remains, have Factory Authorized Service Agent check for shorted trigger leads."],
    ["", "Have Factory Authorized Service Agent check Power switch."],
    ["", "Have Factory Authorized Service Agent check all board connections and main control board."],
    ["No Weld Output; wire feeds.", "Connect work clamp to get good metal to metal contact."],
    ["", "Replace contact tip (see welding gun Owner’s Manual."],
    ["", "HL.P 003 appears on meters, have Factory Authorized Service Agent check main control board and main rectifier."],
    ["Low weld output.", "Connect unit to proper input voltage or check for low line voltage (see Section 4-13)."],
    ["", "Check input voltage jumper links and correct position if necessary (see Section 4-11)."],
    ["", "Have Factory Authorized Service Agent check main control board."],
    ["Fan motor does not run.", "Have Factory Authorized Service Agent check fan-on-demand circuit."],
    ["Low, high, or erratic wire speed.", "Readjust front panel settings (see Section 5-1)."],
    ["", "Change to correct size drive rolls (see Section 6-3)."],
    ["", "Readjust drive roll pressure (see Section 4-14)."],
    ["", "Replace inlet guide, contact tip, and/or liner if necessary (see welding gun Owner’s Manual)."],
    ["", "Check position of input jumper links (see Section 4-11)."],
    ["", "Have Factory Authorized Service Agent check main control board."],
])


def test_miller_6_5_two_col_chunk_parsed_deterministically_no_llm(monkeypatch, tmp_path):
    llm_calls = []

    def fail_if_called(task, messages, max_tokens=None):
        llm_calls.append(task)
        raise AssertionError(f"LLM must not be called for deterministic 2-col chunks (task={task})")

    monkeypatch.setattr(ce.gateway, "complete", fail_if_called)
    chunks = [
        PendingChunk(ChunkType.table, 35, _MILLER_6_5_MD, {"source_type": "troubleshooting_table_2col", "pages": [35]}),
    ]
    session = _setup(monkeypatch, tmp_path, chunks, product_id="prod-miller")
    # _setup registers an OMNIPRO product; replace it with the Miller product
    # so the OMNIPRO registry does not match (deterministic parsing is not
    # registry-gated, so the 2-col parse must still run).
    session.query(Product).delete()
    session.add(Product(id="prod-miller", manufacturer="Miller Electric", family="Millermatic", model="252"))
    session.commit()
    pl.ingest_pdf(
        session, tmp_path / "fake.pdf", revision_id="rev-1", product_id="prod-miller",
        doc_type=DocType.manual, title="fake.pdf", image_out_dir=str(tmp_path),
        run_knowledge_extraction=True, run_schematic_extraction=False, job_id=None,
    )
    session.commit()
    assert llm_calls == []
    modes = session.query(FailureMode).all()
    assert len(modes) == 5
    # NOTE: persist_machine_knowledge stores the symptom doubled as
    # "<name>: <symptoms>" (pre-existing behaviour -- the OMNIPRO
    # pipeline test matches on substring for the same reason), so
    # match on substring here too.
    by_symptom = {}
    for m in modes:
        for key in ("No weld output; wire does not feed.", "No Weld Output; wire feeds.",
                    "Low weld output.", "Fan motor does not run.",
                    "Low, high, or erratic wire speed."):
            if key in (m.symptom or ""):
                by_symptom[key] = m
    assert len(by_symptom) == 5
    assert len(by_symptom["No weld output; wire does not feed."].possible_causes) == 8
    assert len(by_symptom["No Weld Output; wire feeds."].possible_causes) == 3
    assert len(by_symptom["Low weld output."].possible_causes) == 3
    assert len(by_symptom["Fan motor does not run."].possible_causes) == 1
    assert len(by_symptom["Low, high, or erratic wire speed."].possible_causes) == 6
    assert "HL.P 001 appears on meters." in by_symptom["No weld output; wire does not feed."].possible_causes[3]
    # The meter code rides on the cause text; it is not a symptom.
    assert not any("HL.P" in m.symptom for m in modes)
