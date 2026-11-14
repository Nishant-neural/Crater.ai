"""Phase 2 frontend session restore: GET /diagnose/sessions (design §5
group 7).

The list endpoint is registered before /{session_id} so "sessions" is
never read as a session id. Hermetic: in-memory SQLite + TestClient,
same fixture shape as tests/test_hypothesis_tournament.py.
"""
import sys
import types

_bm25 = types.ModuleType("rank_bm25")


class _BM25Okapi:
    def __init__(self, *args, **kwargs):
        pass


_bm25.BM25Okapi = _BM25Okapi
sys.modules.setdefault("rank_bm25", _bm25)

from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.routes import diagnostics as diagnostics_routes
from backend.db.models import (
    Base, DiagnosticSession, DiagnosticStatus, Product, Revision,
)
from backend.db.session import get_session
from backend.diagnostics.schema import DiagnosticState


def _client_with_sessions():
    engine = create_engine("sqlite:///:memory:",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    product = Product(manufacturer="Harbor Freight", family="OMNIPRO", model="220")
    db.add(product)
    db.flush()
    revision = Revision(product_id=product.id, label="Rev A")
    db.add(revision)
    db.flush()
    other_revision = Revision(product_id=product.id, label="Rev B")
    db.add(other_revision)
    db.flush()

    def _session(session_id, rev, status, symptom, turns, confidence, created):
        state = DiagnosticState(
            symptoms=[symptom], confidence=confidence, turns_taken=turns,
            current_step={"type": "conclusion", "content": "Fix it"},
        )
        row = DiagnosticSession(
            id=session_id, product_id=product.id, revision_id=rev.id,
            status=status, state=state.model_dump(mode="json"))
        row.created_at = created
        return row

    base = datetime(2026, 11, 13, 10, 0, 0)
    db.add_all([
        _session("s-old", revision, DiagnosticStatus.concluded,
                 "Wire Feed Motor Runs but Wire Does Not Feed Properly",
                 4, 0.95, base),
        _session("s-new", revision, DiagnosticStatus.active,
                 "LCD Display Does Not Light", 1, 0.4, base + timedelta(hours=2)),
        _session("s-other-rev", other_revision, DiagnosticStatus.active,
                 "Welder Does Not Function When Switched On", 2, 0.6,
                 base + timedelta(hours=1)),
    ])
    db.commit()

    app = FastAPI()
    app.include_router(diagnostics_routes.router)
    app.dependency_overrides[get_session] = lambda: db
    return TestClient(app), product, revision, other_revision


def test_list_sessions_most_recent_first_for_the_revision():
    client, product, revision, _ = _client_with_sessions()
    res = client.get(f"/diagnose/sessions?product_id={product.id}&revision_id={revision.id}")
    assert res.status_code == 200, res.text
    rows = res.json()["sessions"]
    assert [r["session_id"] for r in rows] == ["s-new", "s-old"]
    assert rows[0]["symptom"] == "LCD Display Does Not Light"
    assert rows[0]["status"] == "active"
    assert rows[0]["turns_taken"] == 1
    assert rows[0]["confidence"] == 0.4
    assert rows[1]["symptom"] == "Wire Feed Motor Runs but Wire Does Not Feed Properly"
    assert rows[1]["confidence"] == 0.95


def test_list_sessions_filters_by_product_and_revision():
    client, product, revision, other_revision = _client_with_sessions()
    res = client.get(f"/diagnose/sessions?revision_id={other_revision.id}")
    assert [r["session_id"] for r in res.json()["sessions"]] == ["s-other-rev"]
    res = client.get("/diagnose/sessions")
    assert len(res.json()["sessions"]) == 3


def test_sessions_is_not_read_as_a_session_id():
    # The literal route must win over /{session_id}: "sessions" is not
    # a session, and must not 404 as a missing session id.
    client, _, _, _ = _client_with_sessions()
    res = client.get("/diagnose/sessions")
    assert res.status_code == 200
    res = client.get("/diagnose/no-such-session")
    assert res.status_code == 404


def test_restored_session_keeps_timeline_and_conclusion():
    # What the frontend restores: GET /diagnose/{id} returns the full
    # persisted state (timeline, hypotheses, conclusion) intact.
    client, _, _, _ = _client_with_sessions()
    res = client.get("/diagnose/s-old")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["session_id"] == "s-old"
    assert body["status"] == "concluded"
    assert body["state"]["symptoms"] == [
        "Wire Feed Motor Runs but Wire Does Not Feed Properly"]
    assert body["state"]["turns_taken"] == 4
    assert body["state"]["confidence"] == 0.95
