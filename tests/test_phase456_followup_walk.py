"""Phase 4–6 follow-up (14 Nov 2026): the upstream walk, wired into the
live diagnosis loop + Tier 2 Option C default guard.

The verifier caught that upstream_walk / apply_test_answer /
generate_pair_hypotheses were called only by scripts/run_phase4_offtable.py
and the unit tests — no route or agent.py code called them. These tests
prove the walk is reachable via the API/route:

- POST /diagnose/{id}/respond auto-runs the walk when a turn exhausts
  the table causes, returning walk-generated hypotheses (5 live + 1
  ranked reserve), persists them, and is idempotent while the walk
  is live.
- POST /diagnose/{id}/respond advances the live walk with the
  technician's answers ("no" rules the leading walk hypothesis out and
  promotes a reserve; once the singles are exhausted, pairs from the top
  of the ranking appear) — no harness script involved.
- The walk refuses when the table is not exhausted (unless forced),
  driven through the helper directly since the standalone
  POST /diagnose/{id}/upstream-walk endpoint was removed in the
  Phase 3 extraction (§2.5).
- The live cap is enforced by split_live_and_reserves (MAX_LIVE_HYPOTHESES
  = 5, actually referenced now).
- Tier 2 Option C: the registry default stays ON (tier2_default True);
  flipping it to off is a later decision, not this build.

The LLM turn is stubbed at gateway.complete_result (no provider is
configured in this environment): one stub returns a valid turn in which
the model rules the table causes out; the other returns unparseable
output, which degrades to keeping the (walk-advanced) hypotheses — the
documented no-LLM behaviour. The tests assert the walk wiring, not the
model.
"""
import json
import sys
import types

import pytest

import backend.diagnostics.agent as agent_module


@pytest.fixture(autouse=True)
def _stub_retrieval(monkeypatch):
    """No Qdrant runs in this VM, and since Phase 1 Step 2 a dead
    semantic search fails loudly (503) instead of silently falling
    back to BM25-only — so these walk-wiring tests must not depend on
    live retrieval. They stub the LLM already; retrieval returns no
    evidence, which is all the walk path needs."""
    monkeypatch.setattr(agent_module, "hybrid_retrieve", lambda *a, **k: [])


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

from backend.diagnostics.backtracking import (
    MAX_LIVE_HYPOTHESES, split_live_and_reserves, upstream_walk,
)
from backend.knowledge.repair_cases import load_registry
from backend.simulation.archetypes import ARCHETYPES
from backend.simulation.edges import load_edge_list

CASE1_SYMPTOM = "Wire Feed Motor Runs but Wire Does Not Feed Properly"

CASE1_CAUSES = [
    "Insufficient wire feed pressure.",
    "Incorrect wire feed roller size.",
    "Damaged MIG Gun, cable, or liner assembly.",
    "Feed Tensioner is too tight.",
]


def _exhausted_session(db, product, revision):
    """A session whose table causes are all ruled out (the off-table path)."""
    from backend.db.models import DiagnosticSession, DiagnosticStatus
    from backend.diagnostics.schema import DiagnosticState
    state = DiagnosticState(
        symptoms=[CASE1_SYMPTOM],
        hypotheses=[],
        eliminated_hypotheses=[
            {"cause": cause, "rationale": "p42", "confidence": 0.4,
             "supporting_evidence_chunk_ids": []}
            for cause in CASE1_CAUSES],
        current_step={"type": "question",
                      "content": "What do you observe?",
                      "rationale": "agent question"},
        # 0.5: the walk loop needs 6 turns and the degraded (stubbed-LLM)
        # turns in these tests keep the confidence they are given.
        # (Pre-fix-3 this also kept the session out of the
        # forced-escalation guard for low confidence; that guard was
        # removed in fix-3, so the 0.5 is now just the walk's working
        # confidence, not a guard dodge.)
        confidence=0.5, turns_taken=1)
    session = DiagnosticSession(product_id=product.id, revision_id=revision.id,
                                status=DiagnosticStatus.active,
                                state=state.model_dump(mode="json"))
    db.add(session)
    db.commit()
    return session


def _db_with_proof_twin():
    """In-memory DB + approved proof twin (compiler v3), like the
    Phase 4 early-tournament endpoint test. Compiles the proof twin
    once (~40 s CPU)."""
    from backend.api.routes import diagnostics as diagnostics_routes  # noqa: F401
    from backend.db.models import (
        Base, DiagnosticSession, DiagnosticStatus, DigitalTwin,  # noqa: F401
        MachineKnowledgeModelSnapshot, Product, Revision, TwinStatus,
    )
    from backend.simulation.compiler import compile_machine_model
    from backend.simulation.expert_rules import apply_expert_seed, load_seed_for_product
    from backend.simulation.omnipro_proof import (
        build_omnipro_proof_model, initial_twin_state, snapshot_model_for_tournament,
    )

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    product = Product(manufacturer="Harbor Freight", family="OMNIPRO", model="220")
    db.add(product)
    db.flush()
    revision = Revision(product_id=product.id, label="Rev A")
    db.add(revision)
    db.flush()

    proof_model = build_omnipro_proof_model()
    seed = load_seed_for_product("Harbor Freight", "220", "OMNIPRO")
    if seed is not None:
        proof_model, _, _ = apply_expert_seed(proof_model, seed)
    definition, _warnings = compile_machine_model(proof_model, name="Phase 4 follow-up proof twin")
    twin = DigitalTwin(product_id=product.id, revision_id=revision.id,
                       name="Phase 4 follow-up proof twin",
                       status=TwinStatus.active,
                       definition=definition.model_dump(mode="json"),
                       state=initial_twin_state(definition))
    db.add(twin)
    snapshot = MachineKnowledgeModelSnapshot(
        revision_id=revision.id, version=1,
        model=snapshot_model_for_tournament())
    db.add(snapshot)
    db.flush()
    session = _exhausted_session(db, product, revision)
    return db, session


def _client(db):
    from backend.api.routes import diagnostics as diagnostics_routes
    from backend.db.session import get_session
    app = FastAPI()
    app.include_router(diagnostics_routes.router)

    def _override():
        yield db

    app.dependency_overrides[get_session] = _override
    return TestClient(app)


# --- the live cap ---------------------------------------------------------

def test_split_live_and_reserves_enforces_cap():
    assert MAX_LIVE_HYPOTHESES == 5
    edges = [(e["source_component"], e["target_component"])
             for e in load_edge_list()["edges"]]
    bindings = {"Wire Feed Motor": "A3", "Trigger": "A2", "Welder": "A1",
                "Input Power Cord": "A1", "Reset Button": "A2"}
    walk = upstream_walk("Wire Feed Mechanism", edges, bindings, ARCHETYPES,
                         exclude={"liner assembly", "MIG Gun", "Contact Tip", "Welding Arc"})
    assert len(walk) == 6  # the full upstream chain, over the live cap
    live, reserves = split_live_and_reserves(walk)
    assert len(live) == 5
    assert len(reserves) == 1
    # Order preserved; nothing dropped: live + reserves is the whole walk.
    assert live + reserves == walk
    assert reserves[0].component == walk[5].component


# --- the walk, via the API -------------------------------------------------

def test_upstream_walk_generates_live_plus_reserves_and_answers_advance_to_pairs(monkeypatch):
    """Phase 3 extraction: POST /diagnose/{id}/upstream-walk is removed
    (§2.5) — the walk runs inside POST /respond when a turn exhausts
    the table causes. This test drives the walk through respond (the
    LLM turn rules the table causes out) and keeps the same assertions
    on the walk it generates, its persistence, its idempotency, and
    the technician's answers advancing it to pairs."""
    db, session = _db_with_proof_twin()
    client = _client(db)

    import backend.diagnostics.agent as agent_module

    def _table_exhausted(*args, **kwargs):
        return json.dumps({
            "hypotheses": [],
            "eliminated_hypotheses": [
                {"cause": cause, "rationale": "ruled out by test",
                 "confidence": 0.0, "supporting_evidence_chunk_ids": []}
                for cause in CASE1_CAUSES],
            "overall_confidence": 0.2,
            "next_step": {"type": "question",
                          "content": "What do you observe next?",
                          "rationale": "table exhausted"},
        }), False

    monkeypatch.setattr(agent_module.gateway, "complete_result", _table_exhausted)
    resp = client.post(f"/diagnose/{session.id}/respond",
                       json={"input": "The wire still does not feed",
                             "input_kind": "observation"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "active"
    state = body["state"]
    assert len(state["hypotheses"]) == 5
    # The live cap is enforced: 5 live + 1 ranked reserve, never dropped.
    walk_entries = [e for e in state["timeline"] if e.get("kind") == "upstream_walk"]
    assert len(walk_entries) == 1
    entry = walk_entries[0]
    assert entry["available"] is True
    assert entry["symptom_component"] == "Wire Feed Mechanism"
    assert entry["case_id"] == "omni-01-wire-not-feeding"
    assert len(entry["live"]) == 5
    assert len(entry["reserves"]) == 1
    components = [h["cause"].split(":")[0].split(" ")[0] for h in entry["live"]]
    live_text = " ".join(h["cause"] for h in entry["live"] + entry["reserves"])
    assert "Wire Feed Motor" in live_text
    assert "Trigger" in live_text
    assert "Input Power Cord" in live_text
    assert entry["discriminating_test"]
    assert all(h["rationale"].startswith("Upstream walk (topology + archetype):")
               for h in entry["live"])
    # Persisted: the session state carries the walk hypotheses and the
    # timeline entry records the reserve.
    db.refresh(session)
    from backend.diagnostics.schema import DiagnosticState
    persisted = DiagnosticState.model_validate(session.state)
    assert len(persisted.hypotheses) == 5
    walk_entries = [e for e in persisted.timeline if e.get("kind") == "upstream_walk"]
    assert len(walk_entries) == 1
    assert len(walk_entries[0]["reserves"]) == 1
    assert walk_entries[0]["edges_source"] in ("twin_relations", "reviewed_edge_list", "canonical_model")

    # Idempotent while the walk is live: a second walk call returns the
    # same live walk instead of re-walking and losing the state.
    from backend.api.routes.diagnostics import _run_upstream_walk
    again = _run_upstream_walk(db, session, persisted)
    assert again is not None and again["already_live"] is True
    assert [h["cause"] for h in again["live"]] == \
        [h["cause"] for h in entry["live"]]

    # The technician answers "no" through POST /respond: the walk
    # advances deterministically (leading hypothesis ruled out, reserve
    # promoted), and once the singles are exhausted, pairs from the top
    # of the ranking appear. The LLM turn is stubbed to unparseable
    # output, which degrades to keeping the walk-advanced hypotheses.
    def _unparseable(*args, **kwargs):
        return "not json", False

    monkeypatch.setattr(agent_module.gateway, "complete_result", _unparseable)
    for _ in range(6):
        resp = client.post(f"/diagnose/{session.id}/respond",
                           json={"input": "no", "input_kind": "observation"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "active"
    db.refresh(session)
    final = DiagnosticState.model_validate(session.state)
    assert len(final.hypotheses) <= 5  # the live cap holds through the loop
    assert any("(two faults)" in h.cause for h in final.hypotheses), \
        "singles exhausted but no pair hypothesis was generated"
    assert any(e.get("kind") == "upstream_walk_answer" for e in final.timeline)
    # Ruled-out walk hypotheses are recorded as eliminated, not lost.
    assert len(final.eliminated_hypotheses) >= 5

    # The endpoint itself is gone from the diagnosis service (§2.5).
    resp = client.post(f"/diagnose/{session.id}/upstream-walk", json={})
    assert resp.status_code == 404, resp.text


def test_respond_auto_walks_when_turn_exhausts_table_causes(monkeypatch):
    db, session = _db_with_proof_twin()
    client = _client(db)

    # The LLM turn rules the table causes out (hypotheses empty).
    import backend.diagnostics.agent as agent_module

    def _table_exhausted(*args, **kwargs):
        return json.dumps({
            "hypotheses": [],
            "eliminated_hypotheses": [
                {"cause": cause, "rationale": "ruled out by test",
                 "confidence": 0.0, "supporting_evidence_chunk_ids": []}
                for cause in CASE1_CAUSES],
            "overall_confidence": 0.2,
            "next_step": {"type": "question",
                          "content": "What do you observe next?",
                          "rationale": "table exhausted"},
        }), False

    monkeypatch.setattr(agent_module.gateway, "complete_result", _table_exhausted)
    resp = client.post(f"/diagnose/{session.id}/respond",
                       json={"input": "The wire still does not feed",
                             "input_kind": "observation"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "active"
    state = body["state"]
    # The live loop walked upstream instead of stopping at an attempt:
    # 5 live walk hypotheses, with the walk marker on every rationale.
    assert len(state["hypotheses"]) == 5
    assert all(h["rationale"].startswith("Upstream walk (topology + archetype):")
               for h in state["hypotheses"])
    assert any(e.get("kind") == "upstream_walk" for e in state["timeline"])
    live_text = " ".join(h["cause"] for h in state["hypotheses"])
    assert "Wire Feed Motor" in live_text


def test_upstream_walk_refuses_when_table_not_exhausted():
    """Phase 3 extraction: POST /diagnose/{id}/upstream-walk is removed
    (§2.5) — the refusal and the force override are driven through the
    walk helper directly here, and the endpoint asserted 404."""
    db, session = _db_with_proof_twin()
    client = _client(db)
    # Put a table cause back as a live hypothesis: the walk must not run.
    from backend.api.routes.diagnostics import _run_upstream_walk
    from backend.diagnostics.schema import DiagnosticState, Hypothesis
    state = DiagnosticState.model_validate(session.state)
    state.hypotheses = [Hypothesis(
        cause="Insufficient wire feed pressure.", rationale="p42",
        confidence=0.4, supporting_evidence_chunk_ids=[])]
    state.eliminated_hypotheses = []
    session.state = state.model_dump(mode="json")
    db.commit()
    assert _run_upstream_walk(db, session, state) is None
    # force=true runs it anyway (the explicit override).
    forced = _run_upstream_walk(db, session, state, force=True)
    assert forced is not None and forced["available"] is True
    assert len(forced["live"]) == 5
    # The endpoint itself is gone from the diagnosis service (§2.5).
    resp = client.post(f"/diagnose/{session.id}/upstream-walk", json={})
    assert resp.status_code == 404, resp.text


# --- Tier 2 Option C: the default stays ON ---------------------------------

def test_tier2_default_stays_on_after_option_c_approval():
    """Option C approved 14 Nov 2026 does NOT flip the default: Tier 2
    is still extracted (Phase 1 behaviour). The flip to off is a later
    decision, for when per-case coverage data justifies it."""
    registry = load_registry()
    assert registry.tier2_default is True
