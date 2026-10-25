from pathlib import Path


def test_extraction_progress_uses_defined_character_counter():
    source = Path("backend/ingestion/pipeline.py").read_text(encoding="utf-8")
    assert "{prompt_chars:,} chars" in source
    assert "{chars:,} chars" not in source


def test_extraction_defaults_fit_gemini_tpm_budget():
    source = Path("backend/config.py").read_text(encoding="utf-8")
    assert "chunk_size_chars: int = 12000" in source
    assert "chunk_max_chars: int = 15000" in source
    assert "extraction_gemini_rpm: int = 10" in source
    assert "extraction_batch_max_chars: int = 80000" in source
