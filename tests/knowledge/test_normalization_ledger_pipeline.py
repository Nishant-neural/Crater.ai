from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.db.models import Base, UnresolvedFact
from backend.knowledge.normalization import normalize_payload, persist_unresolved_fact


def test_normalization_maps_common_aliases_and_keeps_original():
    raw = {"component": "Motor M1", "behavior": "overheats", "source_id": "chunk-1"}
    normalized = normalize_payload(raw, kind="behavior")
    assert normalized["subject_name"] == "Motor M1"
    assert normalized["description"] == "overheats"
    assert normalized["_original_payload"] == raw
    assert normalized["id"]


def test_unresolved_fact_ledger_is_durable_and_idempotent():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        persist_unresolved_fact(session, revision_id=None, source_id="chunk-1", fact_type="behavior",
                                payload={"component": "M1"}, reason="missing_entity")
        persist_unresolved_fact(session, revision_id=None, source_id="chunk-1", fact_type="behavior",
                                payload={"component": "M1", "behavior": "hot"}, reason="missing_entity")
        session.commit()
        rows = session.query(UnresolvedFact).all()
        assert len(rows) == 1
        assert rows[0].payload["behavior"] == "hot"
        assert rows[0].status == "open"
