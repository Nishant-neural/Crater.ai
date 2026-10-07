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


def test_checkpoint_preserves_vector_chunk_progress(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.ingestion.checkpoints.CHECKPOINT_DIR", tmp_path)
    job = "large-manual-test"
    save_checkpoint(job, stage="vectors", vector_chunks_done=["c1", "c2"], vectors_done=False)
    cp = load_checkpoint(job)
    assert cp["vector_chunks_done"] == ["c1", "c2"]
    assert cp["vectors_done"] is False
    clear_checkpoint(job)
