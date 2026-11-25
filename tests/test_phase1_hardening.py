import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.api.routes.query import QueryRequest
from backend.db.models import Base, DocType, Document, Product, Revision
from backend.ingestion import pipeline
from backend.retrieval import hybrid


def test_query_request_requires_revision_id():
    with pytest.raises(ValidationError):
        QueryRequest(question="What should I inspect?")


def test_hybrid_retrieval_fails_loudly_when_semantic_search_fails(monkeypatch):
    """Phase 1 Step 2 (infra P-06): the silent BM25-only fallback is
    deleted. A Qdrant failure raises RetrievalUnavailableError (the
    API maps it to HTTP 503) and BM25 is never consulted as a
    fallback -- a dead Qdrant must not look like a valid run.
    (BM25 stays a leg of the normal fusion path; only the fallback
    on semantic failure is gone.)"""
    class Row:
        id = "chunk-1"
        content = "Terminal X12 should read 24 V."
        page_number = 7
        document_id = "document-1"

        class chunk_type:
            value = "text"

    class Query:
        def filter(self, *_args):
            return self

        def all(self):
            return [Row()]

    def unavailable(*_args, **_kwargs):
        raise ConnectionError("Qdrant is unavailable")

    import pytest as _pytest

    def _bm25_must_not_run(*_args, **_kwargs):
        raise AssertionError("BM25 must not run as a fallback when semantic search fails")

    monkeypatch.setattr(hybrid, "semantic_search", unavailable)
    monkeypatch.setattr(hybrid, "bm25_search", _bm25_must_not_run)
    monkeypatch.setattr(hybrid, "rerank", lambda _query, candidates, _top_k: [cid for cid, _ in candidates])

    with _pytest.raises(hybrid.RetrievalUnavailableError) as excinfo:
        hybrid.hybrid_retrieve("What voltage is expected at X12?", revision_id="rev-1")

    assert "semantic retrieval unavailable" in str(excinfo.value)
    assert "Qdrant is unavailable" in excinfo.value.reason


def test_ingest_pdf_accepts_existing_document_id_for_retry_resume(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    product = Product(manufacturer="Acme", family="CNC", model="CNC-500X")
    session.add(product)
    session.flush()
    revision = Revision(product_id=product.id, label="Rev C")
    session.add(revision)
    session.commit()

    existing = Document(
        revision_id=revision.id,
        doc_type=DocType.manual,
        title="Existing document",
        source_path="/tmp/existing.pdf",
        page_count=1,
    )
    session.add(existing)
    session.commit()

    pdf_path = tmp_path / "manual.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(pipeline, "load_pdf", lambda *_args, **_kwargs: [])

    result = pipeline.ingest_pdf(
        session=session,
        pdf_path=pdf_path,
        revision_id=revision.id,
        product_id=product.id,
        doc_type=DocType.manual,
        title="Retry title",
        image_out_dir=tmp_path,
        existing_document_id=existing.id,
    )

    assert result.id == existing.id
    assert session.query(Document).filter_by(revision_id=revision.id).count() == 1
