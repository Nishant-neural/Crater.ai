"""Phase 1 Step 5 gate tests: the LangGraph loop (audit Step 4).

The turn is a LangGraph graph checkpointed per session — safety first
on every turn including turn 1, the two-call rank-then-ask shape inside
the graph nodes, explicit ``asked_causes`` / ``asked_tests`` sets, and
confidence computed from the tournament + the discriminating test's
information gain (never the ask call's number).

Checkpointer: SQLite here (the dev default); the Postgres path is the
same factory with a Postgres URL and is exercised by
``test_checkpoint_survives_restart_postgres`` when
``CRATER_TEST_POSTGRES_DSN`` is set (a real Postgres is required — the
CI/dev VM has none by default, so the test skips with the reason
instead of faking a Postgres).
"""
import json
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db.models import Base, DiagnosticSession, DiagnosticStatus, Product
from backend.diagnostics import agent as diag_agent
from backend.diagnostics.agent import compute_confidence
from backend.diagnostics.checkpoints import open_checkpointer
from backend.diagnostics.graph import build_diagnosis_graph
from backend.diagnostics.schema import DiagnosticState, EvidenceRef

PRESSURE = "Insufficient wire feed pressure."
TIGHT = "Feed Tensioner is too tight."
GUN = "Damaged MIG Gun, cable, or liner assembly."


def _rank(*causes, citations=("c1",)):
    return {
        "hypotheses": [
            {"cause": c, "component": "Feed Tensioner", "value": "low",
             "rationale": "p42", "confidence": 0.5 - 0.1 * i,
             "supporting_evidence_chunk_ids": list(citations)}
            for i, c in enumerate(causes)
        ],
        "eliminated_hypotheses": [],
    }


def _ask(step_type="question", content="What did the check show?", confidence=0.99):
    # confidence 0.99 on purpose: computed confidence must NOT be this.
    return {"overall_confidence": confidence,
            "next_step": {"type": step_type, "content": content,
                          "rationale": "test", "safety_notes": []}}


def _script(*turns):
    script = []
    for rank, ask in turns:
        script.append((json.dumps(rank), False))
        script.append((json.dumps(ask), False))
    return script


def _fake_gateway(monkeypatch, script):
    calls = {"n": 0}
    responses = list(script)

    def complete_result(task, messages, **kwargs):
        calls["n"] += 1
        return responses.pop(0)

    monkeypatch.setattr(diag_agent.gateway, "complete_result", complete_result)
    return calls


@pytest.fixture()
def db():
    # StaticPool + check_same_thread False: the TestClient runs the app
    # in a worker thread, and the graph/client share this session.
    from sqlalchemy.pool import StaticPool
    engine = create_engine("sqlite:///:memory:",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    session = factory()
    yield session
    session.close()


def _make_session(db, product_id="p1", revision_id="r1",
                  symptoms=("Wire Feed Motor Runs but Wire Does Not Feed Properly",)):
    session = DiagnosticSession(
        product_id=product_id, revision_id=revision_id, status=DiagnosticStatus.active,
        state=DiagnosticState(symptoms=list(symptoms)).model_dump(mode="json"))
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def _graph(monkeypatch):
    """A graph with an in-memory SQLite checkpointer (fresh per test).

    ``SqliteSaver.from_conn_string`` is a context manager in this
    langgraph version, so the long-lived saver is built directly on a
    connection (same thread, ``check_same_thread=False`` for the graph's
    use) instead of through the factory."""
    import sqlite3

    from langgraph.checkpoint.sqlite import SqliteSaver
    saver = SqliteSaver(sqlite3.connect(":memory:", check_same_thread=False))
    saver.setup()
    return build_diagnosis_graph(saver)


# ---------------------------------------------------------------------------
# Safety runs first, on turn 1
# ---------------------------------------------------------------------------

def test_safety_runs_on_turn_1(monkeypatch, db):
    """The Recheck bug: the start handler never safety-checked, so turn 1
    was unchecked. run_turn now runs safety first in the graph: a
    guard-bypass symptom on turn 1 leads with the lockout line."""
    _fake_gateway(monkeypatch, _script((_rank(PRESSURE, TIGHT, GUN), _ask())))
    monkeypatch.setattr(diag_agent, "hybrid_retrieve", lambda *a, **k: [])
    session = _make_session(
        db, symptoms=("The guard is bypassed on this machine.",))
    state = diag_agent.run_turn(
        db, session, "The guard is bypassed on this machine.",
        input_kind="symptom", graph=_graph(monkeypatch))
    assert state.current_step.content.startswith(
        "Follow your plant's lockout procedure before anyone works on this machine.")
    assert any(e.get("kind") == "safety_check" and e.get("turn") == 1
               for e in state.timeline)


def test_safety_quiet_when_no_trigger(monkeypatch, db):
    _fake_gateway(monkeypatch, _script((_rank(PRESSURE, TIGHT, GUN), _ask())))
    monkeypatch.setattr(diag_agent, "hybrid_retrieve", lambda *a, **k: [])
    session = _make_session(db)
    state = diag_agent.run_turn(db, session, "wire not feeding",
                                graph=_graph(monkeypatch))
    assert not state.current_step.content.startswith("Follow your plant's lockout")
    assert not any(e.get("kind") == "safety_check" for e in state.timeline)


# ---------------------------------------------------------------------------
# Explicit asked sets
# ---------------------------------------------------------------------------

def test_asked_sets_are_explicit_and_persist(monkeypatch, db):
    """asked_causes / asked_tests are schema fields now, maintained by
    the graph — not regex over prose. After a turn they name the test
    and the cause the step asked about, and they survive a second turn."""
    _fake_gateway(monkeypatch, _script(
        (_rank(PRESSURE, TIGHT, GUN), _ask()),
        (_rank(PRESSURE, TIGHT, GUN), _ask()),
    ))
    monkeypatch.setattr(diag_agent, "hybrid_retrieve", lambda *a, **k: [])
    session = _make_session(db)
    graph = _graph(monkeypatch)
    state = diag_agent.run_turn(db, session, "wire not feeding", graph=graph)
    assert state.asked_tests, "the discriminating test is recorded"
    assert PRESSURE in state.asked_causes
    state2 = diag_agent.run_turn(db, session, "tensioner looks fine", graph=graph)
    assert state2.turns_taken == 2
    assert state2.asked_tests, "asked tests survive the second turn"
    assert PRESSURE in state2.asked_causes


# ---------------------------------------------------------------------------
# Confidence is computed, not asked
# ---------------------------------------------------------------------------

def test_confidence_computed_not_asked(monkeypatch, db):
    """The ask call returns overall_confidence 0.99; the stored
    confidence must be the computed value (0.50 base here: no twin, so
    no verdicts and no gain), never 0.99."""
    _fake_gateway(monkeypatch, _script((_rank(PRESSURE, TIGHT, GUN), _ask())))
    monkeypatch.setattr(diag_agent, "hybrid_retrieve", lambda *a, **k: [])
    session = _make_session(db)
    state = diag_agent.run_turn(db, session, "wire not feeding",
                                graph=_graph(monkeypatch))
    assert state.confidence == pytest.approx(0.5)
    assert state.confidence != 0.99


def test_compute_confidence_from_tournament_and_gain():
    hyps = [{"cause": "a"}, {"cause": "b"}, {"cause": "c"}]
    validated = {"results": [
        {"cause": "a", "status": "validated"},
        {"cause": "b", "status": "rejected"},
        {"cause": "c", "status": "unavailable"}]}
    # Top validated (+0.30), share (1-1)/3 = 0, gain 1.0 of log2(3).
    assert compute_confidence(hyps, validated, {"information_gain_bits": 1.0}) == pytest.approx(
        0.5 + 0.30 + 0.10 * 1.0 / 1.584962500721156)
    # Top rejected: 0.50 - 0.25 + share term, no gain.
    rejected = {"results": [
        {"cause": "a", "status": "rejected"},
        {"cause": "b", "status": "validated"},
        {"cause": "c", "status": "unavailable"}]}
    assert compute_confidence(hyps, rejected, None) == pytest.approx(0.25)
    # No verification at all: the base, never the model's number.
    assert compute_confidence(hyps, None, None) == pytest.approx(0.5)
    assert compute_confidence(hyps, {"results": []}, None) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Checkpoint: state survives a restart
# ---------------------------------------------------------------------------

def test_checkpoint_survives_restart_sqlite(monkeypatch, db):
    """A fresh graph instance on the same checkpointer resumes the
    loop: turn 2 sees turn 1's hypotheses, asked sets and turns_taken
    from the checkpoint, not from a re-read."""
    _fake_gateway(monkeypatch, _script(
        (_rank(PRESSURE, TIGHT, GUN), _ask()),
        (_rank(PRESSURE, TIGHT, GUN), _ask()),
    ))
    monkeypatch.setattr(diag_agent, "hybrid_retrieve", lambda *a, **k: [])
    session = _make_session(db)
    from langgraph.checkpoint.sqlite import SqliteSaver
    with SqliteSaver.from_conn_string(":memory:") as saver:
        saver.setup()
        graph1 = build_diagnosis_graph(saver)
        diag_agent.run_turn(db, session, "wire not feeding", graph=graph1)
        # "Restart": a new graph instance, same checkpointer.
        graph2 = build_diagnosis_graph(saver)
        resumed = graph2.get_state(
            {"configurable": {"thread_id": f"diagnostic-session:{session.id}"}})
        assert resumed.values["state"]["turns_taken"] == 1
        assert len(resumed.values["state"]["hypotheses"]) == 3
        assert resumed.values["state"]["asked_tests"]
        state2 = diag_agent.run_turn(db, session, "tensioner looks fine", graph=graph2)
        assert state2.turns_taken == 2
        assert len(state2.hypotheses) == 3


@pytest.mark.skipif(not os.environ.get("CRATER_TEST_POSTGRES_DSN"),
                    reason="needs a live Postgres (CRATER_TEST_POSTGRES_DSN)")
def test_checkpoint_survives_restart_postgres(monkeypatch, db):
    """Same resume proof against a real Postgres checkpointer: the
    production path. The checkpoint rows are in the Postgres
    ``checkpoints`` table, and a fresh graph instance resumes from
    them."""
    from langgraph.checkpoint.postgres import PostgresSaver
    import psycopg

    dsn = os.environ["CRATER_TEST_POSTGRES_DSN"]
    _fake_gateway(monkeypatch, _script((_rank(PRESSURE, TIGHT, GUN), _ask())))
    monkeypatch.setattr(diag_agent, "hybrid_retrieve", lambda *a, **k: [])
    session = _make_session(db)
    with PostgresSaver.from_conn_string(dsn) as saver:
        saver.setup()
        graph1 = build_diagnosis_graph(saver)
        diag_agent.run_turn(db, session, "wire not feeding", graph=graph1)
        with psycopg.connect(dsn) as conn:
            rows = conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0]
        assert rows >= 1, "the Postgres checkpoints table holds the turn"
        graph2 = build_diagnosis_graph(saver)
        resumed = graph2.get_state(
            {"configurable": {"thread_id": f"diagnostic-session:{session.id}"}})
        assert resumed.values["state"]["turns_taken"] == 1
        assert len(resumed.values["state"]["hypotheses"]) == 3


def test_checkpointer_factory_selects_postgres_for_postgres_url(monkeypatch):
    """The factory picks PostgresSaver for a postgresql:// URL without
    needing a live server to construct the selector (the connection is
    only opened inside the context manager)."""
    import backend.diagnostics.checkpoints as cp

    seen = {}

    class _FakeSaver:
        def __init__(self, *a, **k):
            seen["made"] = True

        def setup(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("langgraph.checkpoint.postgres.PostgresSaver.from_conn_string",
                        classmethod(lambda cls, url: _FakeSaver()))
    with cp.open_checkpointer("postgresql://user@127.0.0.1:5432/crater") as saver:
        assert seen["made"]


def test_checkpointer_factory_selects_sqlite_for_sqlite_url(tmp_path):
    db_path = tmp_path / "app.db"
    with open_checkpointer(f"sqlite:///{db_path}") as saver:
        assert saver is not None
    assert (tmp_path / "app_checkpoints.db").exists()
