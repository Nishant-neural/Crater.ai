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


# ---------------------------------------------------------------------------
# Step 1 (Phase 1, P-02): one table = one chunk. The old page-level
# grouping merged every table on a page into one chunk (up to 15,000
# chars), burying the Miller table at character 3,418 -- past every
# downstream cut. Text chunking keeps the large-context extraction
# sizing until Step 2; this governs tables only.
# ---------------------------------------------------------------------------
def test_one_table_one_chunk(monkeypatch):
    from backend.ingestion import chunking
    from backend.ingestion.pdf_loader import RawPage

    monkeypatch.setattr(chunking, "ocr_page_if_needed", lambda _pdf, _page, text: text)
    page = RawPage(35, "Troubleshooting\n")
    page.tables = [
        [["Trouble", "Remedy"], ["No weld output.", "Check the switch."]],
        [["Problem", "Possible Causes", "Remedy"], ["No arc.", "Bad tip.", "Replace tip."]],
    ]
    chunks = chunking.pages_to_chunks("manual.pdf", [page])
    table_chunks = [c for c in chunks if c.chunk_type.value == "table"]
    assert len(table_chunks) == 2
    assert all((c.extra or {}).get("chunking") == "atomic_table" for c in table_chunks)
    assert [c.extra["table_index"] for c in table_chunks] == [0, 1]
    assert "No weld output." in table_chunks[0].content
    assert "No arc." not in table_chunks[0].content
    assert "No arc." in table_chunks[1].content
    assert "No weld output." not in table_chunks[1].content


def test_split_table_markdown_repeats_header_never_splits_rows():
    from backend.ingestion.chunking import _split_table_markdown

    md = "| A | B |\n| --- | --- |\n" + "\n".join(f"| r{i} | {'x' * 40} |" for i in range(10))
    parts = _split_table_markdown(md, 150)
    assert len(parts) > 1
    for part in parts:
        assert part.startswith("| A | B |\n| --- | --- |")
        assert len(part) <= 150 or part.count("\n") == 2  # a single long row stays whole
    rows_seen = [ln for p in parts for ln in p.splitlines() if ln.startswith("| r")]
    assert len(rows_seen) == 10


def test_oversized_table_splits_into_row_groups(monkeypatch):
    from backend.ingestion import chunking
    from backend.ingestion.pdf_loader import RawPage

    monkeypatch.setattr(chunking, "ocr_page_if_needed", lambda _pdf, _page, text: text)
    monkeypatch.setattr(chunking.settings, "retrieval_chunk_max_chars", 150)
    page = RawPage(35, "Troubleshooting\n")
    page.tables = [[["Trouble", "Remedy"]] + [[f"Trouble {i}", "x" * 40] for i in range(10)]]
    chunks = chunking.pages_to_chunks("manual.pdf", [page])
    table_chunks = [c for c in chunks if c.chunk_type.value == "table"]
    assert len(table_chunks) > 1
    assert all((c.extra or {}).get("table_index") == 0 for c in table_chunks)
    groups = [c.extra["table_group"] for c in table_chunks]
    assert groups == sorted(groups) == list(range(len(table_chunks)))
    assert all("| Trouble | Remedy |" in c.content for c in table_chunks)
