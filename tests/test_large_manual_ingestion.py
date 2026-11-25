from backend.knowledge import component_extraction as ce
from backend.ingestion.checkpoints import load_checkpoint, save_checkpoint, clear_checkpoint


def test_batch_extraction_requires_every_source_id(monkeypatch):
    payload = {
        "chunks": [
            {"source_chunk_id": "c1", "knowledge": {"components": [{"name": "Servo"}]}},
            {"source_chunk_id": "c2", "knowledge": {"components": []}},
        ]
    }

    monkeypatch.setattr(ce.gateway, "complete", lambda *args, **kwargs: __import__("json").dumps(payload))
    result = ce.extract_chunk_knowledge_batch([("c1", "servo text"), ("c2", "empty text")])
    assert set(result) == {"c1", "c2"}
    assert result["c1"][0].components[0].name == "Servo"


def test_checkpoint_preserves_vector_chunk_progress():
    # Phase 3 ingestion step: checkpoints are Postgres rows now
    # (ingestion_job_progress), not files -- same round-trip contract,
    # no CHECKPOINT_DIR to point at a tmp dir.
    job = "large-manual-test-postgres"
    try:
        save_checkpoint(job, stage="vectors", vector_chunks_done=["c1", "c2"], vectors_done=False)
        cp = load_checkpoint(job)
        assert cp["vector_chunks_done"] == ["c1", "c2"]
        assert cp["vectors_done"] is False
        assert cp["stage"] == "vectors"
    finally:
        clear_checkpoint(job)
    assert load_checkpoint(job)["vector_chunks_done"] == []


def test_malformed_batch_response_is_not_marked_retryable(monkeypatch):
    monkeypatch.setattr(ce.gateway, "complete", lambda *args, **kwargs: "not json")
    try:
        ce.extract_chunk_knowledge_batch([("c1", "servo text"), ("c2", "motor text")])
    except ce.ExtractionError as exc:
        assert exc.retryable is False
        assert exc.rate_limited is False
    else:
        raise AssertionError("malformed response should raise ExtractionError")
