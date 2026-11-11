"""Hypothesis tournament: rank the session's hypotheses by running each one
through the verified-repair loop in the approved twin.

Guards:
1. Leaderboard ordering: VALIDATED first, REJECTED second, unavailable
   last (stable within one status).
2. Unavailable is not a rejection: no verdict, available False, and the
   twin cannot evaluate the cause -- while a REJECTED row means the twin
   ran the repair and the symptom still did not recover.
3. The tournament never mutates a hypothesis: no elimination, no edit.
4. Capped at 5 hypotheses; every row carries playback states when the
   twin ran, and per-row evidence with manual source ids.
5. Single-cause verify is unchanged by the refactor: the per-cause
   result carries every key the v9 verify-repair response carried.
6. The HTTP endpoints: POST /diagnose/{id}/verify-hypotheses returns the
   ranked leaderboard with exactly one timeline entry, and POST
   /diagnose/{id}/verify-repair still returns the v9 response shape.
"""
import sys
import types

# The test venv has no rank_bm25 (the retrieval import chain needs it only
# at import time). Stub it before importing the diagnostics routes, the
# way the module itself only needs the symbol to exist.
_bm25 = types.ModuleType("rank_bm25")


class _BM25Okapi:
    def __init__(self, *args, **kwargs):
        pass


_bm25.BM25Okapi = _BM25Okapi
sys.modules.setdefault("rank_bm25", _bm25)

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.routes import diagnostics as diagnostics_routes
from backend.db.models import (
    Base, DiagnosticSession, DiagnosticStatus, DigitalTwin, MachineKnowledgeModelSnapshot,
    Product, Revision, TwinStatus,
)
from backend.db.session import get_session
from backend.diagnostics.schema import DiagnosticState
from backend.simulation.schema import DigitalTwinDefinition
from backend.simulation.tournament import (
    MAX_HYPOTHESES, UNAVAILABLE_LABEL, rank_tournament_rows, run_tournament,
    tournament_status, verify_single_cause,
)


# ---------------------------------------------------------------------------
# Fixture: the OMNIPRO wire-feed twin (seed rules), plus a Wire Feed Motor
# that has no fault rule, so some causes are unevaluable by construction.
# ---------------------------------------------------------------------------

def _tournament_definition(blocker=False):
    transitions = [
        {"id": "seed:feed-tension-too-tight", "name": "Feed tensioner too tight stops wire feed",
         "conditions": {"entity:feed-tensioner.tension_ok": False},
         "effects": {"entity:welding-wire.feeding": False},
         "evidence": [{"fact": "Feed tension too tight stops wire feed.", "page": 42}]},
        {"id": "seed:feed-tension-ok-restores-feed", "name": "Correct feed tension restores wire feed",
         "conditions": {"entity:feed-tensioner.tension_ok": True},
         "effects": {"entity:welding-wire.feeding": True},
         "evidence": [{"fact": "Correct feed tension restores wire feed.", "page": 42}]},
        {"id": "seed:wingnut-loose-spool-spins", "name": "Loose wingnut lets spool spin freely",
         "conditions": {"entity:wingnut.tension_ok": False},
         "effects": {"entity:wire-spool.spinning_freely": True},
         "evidence": [{"fact": "A loose wingnut lets the spool spin freely.", "page": 10}]},
        {"id": "seed:wingnut-tight-stops-spool-spin", "name": "Tight wingnut stops spool spinning",
         "conditions": {"entity:wingnut.tension_ok": True},
         "effects": {"entity:wire-spool.spinning_freely": False},
         "evidence": [{"fact": "Tightening the wingnut stops the spool.", "page": 10}]},
    ]
    if blocker:
        # A second fault holds the symptom after the tension repair: the
        # twin propagates the repair and the symptom still does not
        # recover, so the tensioner cause is REJECTED, not unavailable.
        transitions.append(
            {"id": "rule:liner-blocks-feed", "name": "Clogged liner blocks feed",
             "conditions": {"entity:feed-tensioner.tension_ok": True},
             "effects": {"entity:welding-wire.feeding": False}})
    return DigitalTwinDefinition(
        name="Test OMNIPRO wire feed",
        components=[
            {"id": "entity:feed-tensioner", "name": "Feed Tensioner", "component_type": "other",
             "initial_state": {"tension_ok": True}},
            {"id": "entity:welding-wire", "name": "Welding Wire", "component_type": "other",
             "initial_state": {"feeding": True}},
            {"id": "entity:wingnut", "name": "Wingnut", "component_type": "other",
             "initial_state": {"tension_ok": True}},
            {"id": "entity:wire-spool", "name": "Wire Spool", "component_type": "other",
             "initial_state": {"spinning_freely": False}},
            {"id": "entity:wire-feed-motor", "name": "Wire Feed Motor", "component_type": "other",
             "initial_state": {"running": True}},
        ],
        initial_signals={},
        transitions=transitions,
    )


def _initial_state(definition):
    return {"signals": dict(definition.initial_signals),
            "components": {c.id: dict(c.initial_state) for c in definition.components}}


_SNAPSHOT_MODEL = {
    "failure_modes": [
        {"name": "Wire Does Not Feed",
         "symptoms": ["Wire Feed Motor Runs but Wire Does Not Feed Properly"],
         "entity_ids": ["entity:feed-tensioner"], "evidence": []},
    ],
    "procedures": [
        {"name": "Adjust the Feed Tensioner",
         "steps": ["Loosen the Feed Tensioner knob", "Feed wire and check tension"]},
    ],
}

TENSIONER_CAUSE = "Wire Feed Motor Runs but Wire Does Not Feed Properly"
WINGNUT_CAUSE = "Wingnut is loose, spool spins freely"
MOTOR_CAUSE = "Wire Feed Motor is overheating"
GHOST_CAUSE = "Control board capacitor has failed"
AMBIGUOUS_CAUSE = "Feed Tensioner and Welding Wire fault"


def _state(causes, repair="Adjust the Feed Tensioner"):
    return DiagnosticState(
        symptoms=["Wire Feed Motor Runs but Wire Does Not Feed Properly"],
        hypotheses=[{"cause": c, "rationale": "test", "confidence": 0.5} for c in causes],
        current_step={"type": "conclusion", "content": repair,
                      "procedure_steps": ["Loosen the Feed Tensioner knob"]},
    )


# ---------------------------------------------------------------------------
# 1-2. Per-cause outcomes: validated, rejected, unavailable
# ---------------------------------------------------------------------------

def test_verify_single_cause_validates_feed_tensioner():
    definition = _tournament_definition()
    state = _state([TENSIONER_CAUSE])
    row = verify_single_cause(definition, _initial_state(definition), _SNAPSHOT_MODEL,
                              state, TENSIONER_CAUSE, "Adjust the Feed Tensioner")
    assert row["available"] is True
    assert row["verdict"] == "validated"
    assert tournament_status(row) == "validated"
    assert row["fault_component_id"] == "entity:feed-tensioner"
    assert row["fault_component_name"] == "Feed Tensioner"
    assert row["failure_mode"] == "Wire Does Not Feed"
    assert row["procedure"]["name"] == "Adjust the Feed Tensioner"
    assert [r["id"] for r in row["rules_used"]] == [
        "seed:feed-tension-too-tight", "seed:feed-tension-ok-restores-feed"]
    # Playback states: healthy -> fault -> repaired.
    assert row["baseline_state"]["components"]["entity:welding-wire"]["feeding"] is True
    assert row["fault_state"]["components"]["entity:welding-wire"]["feeding"] is False
    assert row["final_state"]["components"]["entity:welding-wire"]["feeding"] is True
    assert row["intervention_trace"]  # a real transition fired


def test_verify_single_cause_unavailable_when_component_has_no_fault_rule():
    definition = _tournament_definition()
    state = _state([MOTOR_CAUSE])
    row = verify_single_cause(definition, _initial_state(definition), _SNAPSHOT_MODEL,
                              state, MOTOR_CAUSE, "Replace the Wire Feed Motor")
    assert row["available"] is False
    assert row["verdict"] is None
    assert tournament_status(row) == "unavailable"
    assert row["fault_component_id"] == "entity:wire-feed-motor"
    assert "No simulation rule" in row["reason"]


def test_verify_single_cause_unavailable_when_cause_matches_nothing():
    definition = _tournament_definition()
    state = _state([GHOST_CAUSE])
    row = verify_single_cause(definition, _initial_state(definition), _SNAPSHOT_MODEL,
                              state, GHOST_CAUSE, "Replace the control board")
    assert row["available"] is False
    assert row["verdict"] is None
    assert tournament_status(row) == "unavailable"
    assert row["fault_component_id"] is None
    assert "Could not match the cause" in row["reason"]


def test_verify_single_cause_unavailable_when_cause_is_ambiguous():
    definition = _tournament_definition()
    state = _state([AMBIGUOUS_CAUSE])
    row = verify_single_cause(definition, _initial_state(definition), _SNAPSHOT_MODEL,
                              state, AMBIGUOUS_CAUSE, "Inspect the wire feed")
    assert row["available"] is False
    assert row["verdict"] is None
    assert tournament_status(row) == "unavailable"
    assert "ambiguous" in row["reason"]


def test_verify_single_cause_rejects_when_a_second_fault_holds_the_symptom():
    definition = _tournament_definition(blocker=True)
    state = _state([TENSIONER_CAUSE])
    row = verify_single_cause(definition, _initial_state(definition), _SNAPSHOT_MODEL,
                              state, TENSIONER_CAUSE, "Adjust the Feed Tensioner")
    assert row["available"] is True
    assert row["verdict"] == "rejected"
    assert tournament_status(row) == "rejected"
    assert row["checks"] and row["checks"][0]["passed"] is False
    assert row["intervention_trace"]  # the repair ran, the symptom stayed


# ---------------------------------------------------------------------------
# 3. Leaderboard ordering + unavailable is not a rejection
# ---------------------------------------------------------------------------

def test_tournament_ranks_validated_rejected_unavailable():
    definition = _tournament_definition(blocker=True)
    # Session order is deliberately not the leaderboard order.
    state = _state([MOTOR_CAUSE, TENSIONER_CAUSE, WINGNUT_CAUSE, GHOST_CAUSE])
    ranked, counts = run_tournament(
        definition, _initial_state(definition), _SNAPSHOT_MODEL, state,
        "Adjust the Feed Tensioner")
    assert [r["cause"] for r in ranked] == [
        WINGNUT_CAUSE, TENSIONER_CAUSE, MOTOR_CAUSE, GHOST_CAUSE]
    assert [r["status"] for r in ranked] == [
        "validated", "rejected", "unavailable", "unavailable"]
    assert counts == {"validated": 1, "rejected": 1, "unavailable": 2}
    # Stable within one status: the two unavailable rows keep session order.
    assert ranked[2]["cause"] == MOTOR_CAUSE and ranked[3]["cause"] == GHOST_CAUSE


def test_unavailable_is_not_a_rejection_and_no_hypothesis_is_mutated():
    definition = _tournament_definition(blocker=True)
    state = _state([MOTOR_CAUSE, TENSIONER_CAUSE, WINGNUT_CAUSE])
    before = state.model_dump(mode="json")
    ranked, _counts = run_tournament(
        definition, _initial_state(definition), _SNAPSHOT_MODEL, state,
        "Adjust the Feed Tensioner")
    unavailable = [r for r in ranked if r["status"] == "unavailable"]
    rejected = [r for r in ranked if r["status"] == "rejected"]
    assert len(unavailable) == 1 and unavailable[0]["verdict"] is None
    assert unavailable[0]["available"] is False
    assert len(rejected) == 1 and rejected[0]["verdict"] == "rejected"
    assert rejected[0]["available"] is True
    # The tournament only reads the state: hypotheses and the eliminated
    # list are byte-identical afterwards.
    assert state.model_dump(mode="json") == before
    assert state.eliminated_hypotheses == []


def test_rank_tournament_rows_is_stable_and_labels_unavailable():
    rows = [
        {"verdict": None, "cause": "a"},
        {"verdict": "rejected", "cause": "b"},
        {"verdict": "validated", "cause": "c"},
        {"verdict": None, "cause": "d"},
    ]
    ranked = rank_tournament_rows(rows)
    assert [r["cause"] for r in ranked] == ["c", "b", "a", "d"]
    assert UNAVAILABLE_LABEL == "the twin cannot evaluate this cause"


def test_tournament_caps_at_five_hypotheses():
    definition = _tournament_definition()
    causes = [TENSIONER_CAUSE, WINGNUT_CAUSE, MOTOR_CAUSE, GHOST_CAUSE,
              AMBIGUOUS_CAUSE, "Spare cause one", "Spare cause two"]
    assert len(causes) == MAX_HYPOTHESES + 2
    state = _state(causes)
    ranked, counts = run_tournament(
        definition, _initial_state(definition), _SNAPSHOT_MODEL, state,
        "Adjust the Feed Tensioner")
    assert len(ranked) == MAX_HYPOTHESES
    assert sum(counts.values()) == MAX_HYPOTHESES
    assert "Spare cause one" not in [r["cause"] for r in ranked]


# ---------------------------------------------------------------------------
# 4-5. Result shape: v9 single-cause contract + per-row evidence
# ---------------------------------------------------------------------------

_V9_SINGLE_CAUSE_KEYS = {
    "available", "verdict", "reason", "cause", "repair_name",
    "fault_component_id", "symptom_path", "symptom_expected", "failure_mode",
    "procedure", "rules_used", "checks", "fault_trace", "intervention_trace",
    "baseline_state", "fault_state", "final_state", "explanation", "warnings",
}


def test_single_cause_result_keeps_the_v9_response_keys():
    definition = _tournament_definition()
    state = _state([TENSIONER_CAUSE])
    row = verify_single_cause(definition, _initial_state(definition), _SNAPSHOT_MODEL,
                              state, TENSIONER_CAUSE, "Adjust the Feed Tensioner")
    assert _V9_SINGLE_CAUSE_KEYS <= set(row.keys())
    # The unavailable no-match dict keeps the exact v9 small shape.
    ghost = verify_single_cause(definition, _initial_state(definition), _SNAPSHOT_MODEL,
                                state, GHOST_CAUSE, "Replace the control board")
    assert set(ghost.keys()) == {"available", "verdict", "reason", "cause",
                                 "repair_name", "fault_component_id"}


def test_tournament_rows_carry_rule_evidence_with_manual_source_ids():
    definition = _tournament_definition()
    state = _state([TENSIONER_CAUSE])
    ranked, _counts = run_tournament(
        definition, _initial_state(definition), _SNAPSHOT_MODEL, state,
        "Adjust the Feed Tensioner")
    row = ranked[0]
    assert row["rules_used"][0]["evidence"][0]["page"] == 42
    assert "Feed tension too tight" in row["rules_used"][0]["evidence"][0]["fact"]


# ---------------------------------------------------------------------------
# 6. HTTP endpoints (in-memory DB, diagnostics router only)
# ---------------------------------------------------------------------------

def _endpoint_client():
    # StaticPool + check_same_thread=False: one shared in-memory DB, so the
    # TestClient thread sees the tables and rows this thread created.
    engine = create_engine("sqlite:///:memory:",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    product = Product(manufacturer="Harbor Freight", family="OMNIPRO", model="220")
    db.add(product)
    db.flush()  # column defaults fire at flush, so product.id is None until here
    revision = Revision(product_id=product.id, label="test")
    db.add(revision)
    db.flush()
    definition = _tournament_definition()
    state = _state([MOTOR_CAUSE, TENSIONER_CAUSE, WINGNUT_CAUSE])
    session = DiagnosticSession(
        id="session-tournament", product_id=product.id, revision_id=revision.id,
        status=DiagnosticStatus.active, state=state.model_dump(mode="json"))
    twin = DigitalTwin(
        product_id=product.id, revision_id=revision.id, name="test twin",
        definition=definition.model_dump(mode="json"),
        state=_initial_state(definition), status=TwinStatus.active)
    snapshot = MachineKnowledgeModelSnapshot(
        revision_id=revision.id, version=1, model=dict(_SNAPSHOT_MODEL))
    db.add_all([session, twin, snapshot])
    db.commit()

    app = FastAPI()
    app.include_router(diagnostics_routes.router)
    app.dependency_overrides[get_session] = lambda: db
    return TestClient(app), db


def test_verify_hypotheses_endpoint_returns_ranked_leaderboard():
    client, db = _endpoint_client()
    res = client.post("/diagnose/session-tournament/verify-hypotheses")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["available"] is True
    assert body["unavailable_label"] == UNAVAILABLE_LABEL
    assert [r["cause"] for r in body["results"]] == [
        TENSIONER_CAUSE, WINGNUT_CAUSE, MOTOR_CAUSE]
    assert [r["status"] for r in body["results"]] == [
        "validated", "validated", "unavailable"]
    assert body["counts"] == {"validated": 2, "rejected": 0, "unavailable": 1}
    # Exactly one timeline entry for the tournament run, and the session's
    # hypotheses are unchanged by it.
    stored = db.get(DiagnosticSession, "session-tournament")
    stored_state = DiagnosticState.model_validate(stored.state)
    assert [t["kind"] for t in stored_state.timeline] == ["verify_hypotheses"]
    assert [h.cause for h in stored_state.hypotheses] == [
        MOTOR_CAUSE, TENSIONER_CAUSE, WINGNUT_CAUSE]
    assert stored_state.eliminated_hypotheses == []


def test_verify_repair_endpoint_unchanged_after_refactor():
    client, db = _endpoint_client()
    res = client.post("/diagnose/session-tournament/verify-repair",
                      json={"cause": TENSIONER_CAUSE,
                            "repair_name": "Adjust the Feed Tensioner"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["available"] is True
    assert body["verdict"] == "validated"
    assert _V9_SINGLE_CAUSE_KEYS <= set(body.keys())
    assert body["baseline_state"]["components"]["entity:welding-wire"]["feeding"] is True
    assert body["fault_state"]["components"]["entity:welding-wire"]["feeding"] is False
    assert body["final_state"]["components"]["entity:welding-wire"]["feeding"] is True
    assert [r["id"] for r in body["rules_used"]] == [
        "seed:feed-tension-too-tight", "seed:feed-tension-ok-restores-feed"]
    stored = db.get(DiagnosticSession, "session-tournament")
    stored_state = DiagnosticState.model_validate(stored.state)
    assert [t["kind"] for t in stored_state.timeline] == ["verify_repair"]
    assert stored_state.timeline[0]["step"] == "Repair verification: validated"


def test_verify_hypotheses_endpoint_errors():
    client, _db = _endpoint_client()
    res = client.post("/diagnose/no-such-session/verify-hypotheses")
    assert res.status_code == 404
    # A session with no hypotheses gets a 409, like verify-repair.
    empty = DiagnosticState(symptoms=["x"])
    db = _db
    session = DiagnosticSession(
        id="session-empty", product_id="p", revision_id=None,
        status=DiagnosticStatus.active, state=empty.model_dump(mode="json"))
    db.add(session)
    db.commit()
    res = client.post("/diagnose/session-empty/verify-hypotheses")
    assert res.status_code == 409
