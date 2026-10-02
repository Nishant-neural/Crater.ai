"""Smoke tests for chunking logic that don't need Qdrant/Anthropic/tesseract running."""
from backend.ingestion.chunking import _split_text, _table_to_markdown


def test_split_text_short_returns_single_chunk():
    assert _split_text("hello world", size=100, overlap=10) == ["hello world"]


def test_split_text_empty_returns_no_chunks():
    assert _split_text("   ", size=100, overlap=10) == []


def test_split_text_respects_overlap():
    text = "a" * 250
    chunks = _split_text(text, size=100, overlap=20)
    assert len(chunks) > 1
    # consecutive chunks should share `overlap` characters at the boundary
    assert chunks[0][-20:] == chunks[1][:20]


def test_table_to_markdown_basic():
    table = [["Terminal", "Voltage"], ["X12", "24V"], ["X14", "0V"]]
    md = _table_to_markdown(table)
    assert "| Terminal | Voltage |" in md
    assert "| X12 | 24V |" in md


def test_table_to_markdown_empty():
    assert _table_to_markdown([]) == ""


def test_pages_to_chunks_combines_context_across_pages(monkeypatch):
    from backend.ingestion import chunking
    from backend.ingestion.pdf_loader import RawPage

    monkeypatch.setattr(chunking, "ocr_page_if_needed", lambda _pdf, _page, text: text)
    pages = [
        RawPage(1, "MOTOR CONTROL\nThe main motor M1 is protected by breaker Q1. " + "details " * 100),
        RawPage(2, "The breaker trips when current exceeds the specified limit. " + "more details " * 100),
    ]
    chunks = chunking.pages_to_chunks("manual.pdf", pages)
    text_chunks = [c for c in chunks if c.chunk_type.value == "text"]
    assert text_chunks
    assert any((c.extra or {}).get("page_start") == 1 and (c.extra or {}).get("page_end") == 2 for c in text_chunks)
    assert any("breaker trips" in c.content for c in text_chunks)
    assert max(len(c.content) for c in text_chunks) <= chunking.settings.chunk_max_chars
