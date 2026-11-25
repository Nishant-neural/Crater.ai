"""Phase 5 tests: verify/repair/learn hardening + steals 5-9.

Hermetic: in-memory SQLite + TestClient, same fixture shape as the
Phase 4 tests. The proof twin is compiled once per module (~40 s CPU)
and shared; each endpoint test creates its own session row (an outcome
is recorded once per session).
"""
import sys
import types

_bm25 = types.ModuleType("rank_bm25")


class _BM25Okapi:
    def __init__(self, *args, **kwargs):
        pass


_bm25.BM25Okapi = _BM25Okapi
sys.modules.setdefault("rank_bm25", _bm25)

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.models import (
    Base, DiagnosticOutcome, DiagnosticSession, DiagnosticStatus,
    DigitalTwin, MachineKnowledgeModelSnapshot, Product, Revision, TwinStatus,
)
from backend.diagnostics.schema import DiagnosticState

SYMPTOM = "Wire Feed Motor Runs but Wire Does Not Feed Properly"
CAUSE_A = "Insufficient wire feed pressure."


def _db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _revision(db, product, label):
    revision = Revision(product_id=product.id, label=label)
    db.add(revision)
    db.flush()
    return revision


@pytest.fixture(scope="module")
def proof():
    """One in-memory DB with the proof twin compiled, shared by the
    endpoint tests. Each test creates its own session row."""
    from backend.simulation.compiler import compile_machine_model
    from backend.simulation.expert_rules import apply_expert_seed, load_seed_for_product
    from backend.simulation.omnipro_proof import (
        build_omnipro_proof_model, initial_twin_state, snapshot_model_for_tournament,
    )
    db = _db()
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
    definition, _warnings = compile_machine_model(proof_model, name="Phase 5 proof twin")
    twin = DigitalTwin(product_id=product.id, revision_id=revision.id, name="Phase 5 proof twin",
                       status=TwinStatus.active,
                       definition=definition.model_dump(mode="json"),
                       state=initial_twin_state(definition))
    db.add(twin)
    db.add(MachineKnowledgeModelSnapshot(revision_id=revision.id, version=1,
                                         model=snapshot_model_for_tournament()))
    db.commit()
    return {"db": db, "product": product, "revision": revision, "twin": twin}


def _session(db, product, revision, hypotheses=None, timeline=None):
    state = DiagnosticState(
        symptoms=[SYMPTOM],
        hypotheses=hypotheses or [
            {"cause": CAUSE_A, "rationale": "p42", "confidence": 0.9,
             "supporting_evidence_chunk_ids": []}],
        current_step={"type": "question", "content": "What do you observe?",
                      "rationale": "agent question"},
        confidence=0.9, turns_taken=1)
    dumped = state.model_dump(mode="json")
    if timeline:
        dumped["timeline"] = timeline
    row = DiagnosticSession(product_id=product.id, revision_id=revision.id,
                            status=DiagnosticStatus.active, state=dumped)
    db.add(row)
    db.flush()
    return row


def _client(db, *routers):
    from backend.db.session import get_session
    app = FastAPI()
    for router in routers:
        app.include_router(router)

    def _override():
        yield db

    app.dependency_overrides[get_session] = _override
    return TestClient(app)


def _diag_client(db):
    from backend.api.routes import diagnostics as diagnostics_routes
    return _client(db, diagnostics_routes.router)


def _learning_client(db):
    from backend.api.routes import learning as learning_routes
    return _client(db, learning_routes.router)


# --- Phase 5 gate: outcome flows end to end -------------------------------

def _drive_turn_to_tournament(monkeypatch, db, session):
    """Drive one POST /respond turn with the LLM stubbed to unparseable
    output (the degraded turn keeps the seeded hypotheses) and
    retrieval stubbed to no evidence, so respond()'s early tournament
    runs against the proof twin and writes a real early_tournament
    timeline entry. Phase 3 extraction: POST
    /diagnose/{id}/verify-repair is removed (§2.5), so the twin
    verdict for the outcome flow comes from this live path instead."""
    import backend.api.routes.diagnostics as diagnostics_routes
    import backend.diagnostics.agent as agent_module
    import backend.retrieval.ingestion_client as ingestion_client_mod
    from tests.twin_testkit import make_twin_client

    class _FakeIngestionClient:
        def list_products(self):
            return []

    # Phase 3 twin split: the twin client is HTTP-only now. Both
    # run_turn (agent) and respond()'s early tournament (diagnostics
    # route) construct their own TwinVerifyClient; point both at the
    # real twin-verifier app over this test DB (inline dispatch, no
    # broker) -- the HTTP equivalent of the removed LocalVerifyBackend.
    _twin_factory = lambda **kwargs: make_twin_client(db, monkeypatch)
    monkeypatch.setattr(agent_module, "TwinVerifyClient", _twin_factory)
    monkeypatch.setattr(diagnostics_routes, "TwinVerifyClient", _twin_factory)
    monkeypatch.setattr(agent_module, "hybrid_retrieve", lambda *a, **k: [])
    monkeypatch.setattr(agent_module.gateway, "complete_result",
                        lambda *a, **k: ("not json", False))
    monkeypatch.setattr(ingestion_client_mod, "get_ingestion_client",
                        lambda *a, **k: _FakeIngestionClient())
    client = _diag_client(db)
    resp = client.post(f"/diagnose/{session.id}/respond",
                       json={"input": "no change", "input_kind": "observation"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "active"
    db.refresh(session)
    from backend.diagnostics.schema import DiagnosticState
    state = DiagnosticState.model_validate(session.state)
    entries = [e for e in state.timeline if e.get("kind") == "early_tournament"]
    # Two entries land: run_turn's pre-turn tournament and respond()'s
    # early tournament (which reuses the first run's verdicts). The
    # latest is what the outcome flow reads.
    assert entries, state.timeline
    assert entries[-1]["counts"]["validated"] >= 1
    return state


def test_validated_then_failed_flags_row_and_history(proof, monkeypatch):
    db, product, revision = proof["db"], proof["product"], proof["revision"]
    session = _session(db, product, revision)
    # The twin VALIDATES the case-1(a) repair via respond()'s early
    # tournament (the safety-first RepairAction presentation itself is
    # covered by test_phase456_readiness_repairs.py).
    _drive_turn_to_tournament(monkeypatch, db, session)

    # Now the field says the repair FAILED: twin VALIDATED + field
    # FAILED must flag the implicated archetype row for review.
    client = _diag_client(db)
    resp = client.post(f"/diagnose/{session.id}/outcome",
                       json={"outcome": "not_fixed", "confirmed_cause": CAUSE_A,
                             "repair_performed": "Increased wire feed pressure",
                             "technician_notes": "Still does not feed."})
    assert resp.status_code == 200, resp.text
    phase5 = resp.json()["phase5"]
    assert phase5["field_outcome"] == "FAILED"
    assert phase5["twin_verdict"] == "VALIDATED"
    assert phase5["twin_verdict_field_outcome_separate"] is True
    assert phase5["learning_signal"] is not None
    assert "Twin VALIDATED but field FAILED" in phase5["learning_signal"]
    assert phase5["history_written"], "history must be written for the implicated row"
    assert phase5["flags_created"], "the implicated row must be flagged"
    assert phase5["downtime"]["duration_minutes"] >= 1
    assert phase5["pm_draft"]["status"] == "draft"

    # The flag and the history are visible in the review view.
    review = _learning_client(db).get("/learning/review").json()
    assert review["counts"]["flagged_rows"] >= 1
    assert any(f["archetype_id"] == phase5["flags_created"][0]["archetype_id"]
               for f in review["flagged_rows"])
    assert any(h["rejected_count"] >= 1 for h in review["field_validation_history"])
    # Twin verdict and field outcome stay separate: the outcome row
    # still says not_fixed, the flag does not rewrite it.
    outcome = db.query(DiagnosticOutcome).filter_by(session_id=session.id).first()
    assert outcome.outcome == "not_fixed"


def test_successful_outcome_writes_history_without_flag(proof, monkeypatch):
    db, product, revision = proof["db"], proof["product"], proof["revision"]
    session = _session(db, product, revision)
    # Twin VALIDATES via respond()'s early tournament (verify-repair
    # endpoint removed in the Phase 3 extraction, §2.5).
    _drive_turn_to_tournament(monkeypatch, db, session)
    client = _diag_client(db)
    resp = client.post(f"/diagnose/{session.id}/outcome",
                       json={"outcome": "fixed", "confirmed_cause": CAUSE_A,
                             "repair_performed": "Increased wire feed pressure"})
    assert resp.status_code == 200, resp.text
    phase5 = resp.json()["phase5"]
    assert phase5["field_outcome"] == "SUCCESSFUL"
    assert phase5["learning_signal"] is None
    assert phase5["flags_created"] == []
    assert phase5["history_written"]
    assert all(h["validated_count"] >= 1 for h in phase5["history_written"])


def test_inconclusive_outcome_writes_no_history(proof):
    db, product, revision = proof["db"], proof["product"], proof["revision"]
    session = _session(db, product, revision)
    client = _diag_client(db)
    resp = client.post(f"/diagnose/{session.id}/outcome",
                       json={"outcome": "inconclusive", "confirmed_cause": CAUSE_A})
    assert resp.status_code == 200, resp.text
    phase5 = resp.json()["phase5"]
    assert phase5["field_outcome"] is None
    assert phase5["history_written"] == []
    assert "not field evidence" in phase5["note"]


def test_unseen_cause_proposes_pending_entry_and_gate(proof):
    db, product, revision = proof["db"], proof["product"], proof["revision"]
    session = _session(db, product, revision)
    client = _diag_client(db)
    unseen = "The feed motor hums but the spool wobbles sideways."
    resp = client.post(f"/diagnose/{session.id}/outcome",
                       json={"outcome": "fixed", "confirmed_cause": unseen,
                             "repair_performed": "Reseated the spool hub"})
    assert resp.status_code == 200, resp.text
    entry_ref = resp.json()["phase5"]["learning_entry"]
    assert entry_ref is not None and entry_ref["status"] == "pending"

    # Before approval the case is NOT in the merged registry.
    from backend.diagnostics.learning import load_registry_with_learned
    before = load_registry_with_learned(db)
    assert not any(c.id.startswith("learned-") for c in before.cases)

    # The review view shows the pending entry; the gate needs a reviewer.
    lclient = _learning_client(db)
    review = lclient.get("/learning/review").json()
    assert any(e["id"] == entry_ref["id"] for e in review["pending_learning_entries"])
    resp = lclient.post(f"/learning/entries/{entry_ref['id']}/review",
                        json={"decision": "approved", "reviewer": "",
                              "reason": "looks right"})
    assert resp.status_code == 409  # reviewer required
    resp = lclient.post(f"/learning/entries/{entry_ref['id']}/review",
                        json={"decision": "approved", "reviewer": "Nishant",
                              "reason": "field-verified fix"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "approved"
    # A second review is rejected: the gate is decided once.
    resp = lclient.post(f"/learning/entries/{entry_ref['id']}/review",
                        json={"decision": "rejected", "reviewer": "Nishant"})
    assert resp.status_code == 409
    # After approval the case IS in the merged registry.
    after = load_registry_with_learned(db)
    learned = [c for c in after.cases if c.id.startswith("learned-")]
    assert len(learned) == 1
    assert learned[0].causes[0]["cause"] == unseen


# --- Versioning (5c) ---------------------------------------------------------

def test_library_version_pin_recompile_and_rollback(proof):
    from backend.simulation import library_versions
    from backend.simulation.library_versions import (
        pin_library_version, recompile_with_version, rollback_library_version,
    )
    db = proof["db"]
    twin = proof["twin"]
    assert pin_library_version(twin) == "3c-1.0.0"
    assert twin.library_version == "3c-1.0.0"
    with pytest.raises(ValueError, match="unknown archetype-library version"):
        pin_library_version(twin, "9.9.9")
    # With the older version known (a version this tree has shipped),
    # recompiling against it without explicit=True refuses; with
    # explicit=True it is recorded; rollback is a real operation.
    old_known = library_versions.KNOWN_LIBRARY_VERSIONS
    library_versions.KNOWN_LIBRARY_VERSIONS = ("3c-1.0.0", "3b-1.0.0")
    try:
        with pytest.raises(ValueError, match="requires explicit=True"):
            recompile_with_version(twin, "3b-1.0.0", explicit=False)
        assert recompile_with_version(twin, "3b-1.0.0", explicit=True)["target_version"] == "3b-1.0.0"
        with pytest.raises(ValueError, match="requires a named actor"):
            rollback_library_version(db, twin, "3b-1.0.0", actor="", reason="bad pin")
        result = rollback_library_version(db, twin, "3b-1.0.0",
                                          actor="Nishant", reason="3c rows misbehave in the field")
        assert result["from_version"] == "3c-1.0.0"
        assert result["to_version"] == "3b-1.0.0"
        assert twin.library_version == "3b-1.0.0"
        # Rolling back to the version already pinned is a no-op error.
        with pytest.raises(ValueError, match="already pinned"):
            rollback_library_version(db, twin, "3b-1.0.0", actor="Nishant", reason="again")
    finally:
        library_versions.KNOWN_LIBRARY_VERSIONS = old_known
        twin.library_version = "3c-1.0.0"
        db.commit()


# --- Full RepairAction (5d) + safety-first (steal 6) -------------------------

def test_full_repair_action_has_preconditions_and_safety():
    from backend.simulation.repair_actions import (
        load_repair_actions, render_repair_action_safety_first,
    )
    actions = load_repair_actions()
    assert len(actions) == 20
    for action in actions:
        assert action.preconditions, f"{action.id} has no preconditions"
        assert action.safety_requirements, f"{action.id} has no safety_requirements"
        rendered = render_repair_action_safety_first(action)
        assert rendered["order"] == ["Safety", "Preconditions", "Steps", "Expected trace"]
        assert rendered["Safety"], "safety section is always present"
        assert rendered["Steps"], "procedure refs carry the page evidence"


# --- Close-out-note mining (steal 5) — CUT in Phase 1 Step 5 -----------
# The two tests below assert the cut (module + route gone); the mining
# behaviour tests were removed with backend/diagnostics/closeout.py.

def test_closeout_module_is_cut():
    """Phase 1 Step 5 CUT: closeout (``backend/diagnostics/closeout.py``)
    serves neither the core loop nor scalability, so the module is gone.
    The learning routes that depended on nothing but it are gone with
    it; learning / fleet / downtime / pm stay (DEFER, scalability base)."""
    import pytest as _pytest
    with _pytest.raises(ModuleNotFoundError):
        import backend.diagnostics.closeout  # noqa: F401


def _product(db, manufacturer="Harbor Freight", family="OMNIPRO", model="220"):
    product = Product(manufacturer=manufacturer, family=family, model=model)
    db.add(product)
    db.flush()
    revision = Revision(product_id=product.id, label="Rev A")
    db.add(revision)
    db.flush()
    return product, revision


def test_closeout_route_is_gone_but_learning_review_stays():
    """Phase 1 Step 5 CUT sweep: GET /learning/closeout is gone with
    the module, while the deferred learning surface (review view,
    bad-actors, downtime) is intact."""
    db = _db()
    client = _learning_client(db)
    assert client.get("/learning/closeout").status_code == 404
    review = client.get("/learning/review")
    assert review.status_code == 200
    assert review.json()["counts"]["flagged_rows"] == 0


# --- PM-interval draft (steal 7) ----------------------------------------------

def test_pm_draft_stays_draft_and_degrades_without_current_interval():
    from backend.diagnostics.pm import draft_pm_interval, review_pm_draft
    db = _db()
    asset, _rev = _product(db)
    with_interval = draft_pm_interval(db, asset.id, "Feed Tensioner is too tight.",
                                      current_interval="every 6 months",
                                      evidence="recurring failure")
    assert with_interval.status == "draft"
    assert "half" in (with_interval.proposed_interval or "")
    without_interval = draft_pm_interval(db, asset.id, "Feed Tensioner is too tight.")
    assert without_interval.status == "draft"
    assert without_interval.proposed_interval is None
    assert "What is today's PM interval" in without_interval.proposed_change
    reviewed = review_pm_draft(db, with_interval.id, "approved", "Nishant")
    assert reviewed.status == "approved"
    with pytest.raises(ValueError, match="already approved"):
        review_pm_draft(db, with_interval.id, "rejected", "Nishant")


# --- Downtime + bad-actor ranking (steals 8-9) ---------------------------------

def test_downtime_null_while_ongoing_then_closed_and_bad_actor_ranking():
    from backend.diagnostics.downtime import (
        bad_actor_ranking, close_downtime, downtime_report, start_downtime,
    )
    db = _db()
    assert bad_actor_ranking(db)["available"] is False
    assert "no outcome data" in bad_actor_ranking(db)["reason"]
    asset, asset_rev = _product(db)
    other, other_rev = _product(db, family="OMNIPRO", model="999")
    session_a = _session(db, asset, asset_rev)
    record = start_downtime(db, session_a)
    assert record.duration_minutes is None  # ongoing: null, never zero
    assert downtime_report(db, session_a.id)["ongoing"] is True
    closed = close_downtime(db, session_a.id, cause=CAUSE_A, confirmed=True)
    assert closed.duration_minutes is not None and closed.duration_minutes >= 1
    session_b = _session(db, other, other_rev)
    close_downtime(db, session_b.id, cause="Other cause", confirmed=True)
    # Two recorded outcomes (one per asset) + downtime on both.
    db.add(DiagnosticOutcome(session_id=session_a.id, product_id=asset.id,
                             revision_id=asset_rev.id, outcome="fixed",
                             confirmed_cause=CAUSE_A))
    db.add(DiagnosticOutcome(session_id=session_b.id, product_id=other.id,
                             revision_id=other_rev.id, outcome="fixed",
                             confirmed_cause="Other cause"))
    db.commit()
    ranking = bad_actor_ranking(db)
    assert ranking["available"] is True
    assert len(ranking["by_failure_frequency"]) == 2
    assert len(ranking["by_downtime_minutes"]) == 2
    assert ranking["window"]["outcomes"] == 2
    # An asset with sessions but no outcomes is unreported, not a good actor.
    third, third_rev = _product(db, family="OMNIPRO", model="555")
    _session(db, third, third_rev)
    db.commit()
    ranking = bad_actor_ranking(db)
    assert any(a["product_id"] == third.id for a in ranking["unreported_assets"])
