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

def test_validated_then_failed_flags_row_and_history(proof):
    db, product, revision = proof["db"], proof["product"], proof["revision"]
    session = _session(db, product, revision)
    client = _diag_client(db)
    # Verify first: the twin VALIDATES the case-1(a) repair, and the
    # RepairAction is presented safety-first (steal 6).
    resp = client.post(f"/diagnose/{session.id}/verify-repair",
                       json={"cause": CAUSE_A})
    assert resp.status_code == 200, resp.text
    verify = resp.json()
    assert verify["available"] is True, verify
    assert verify["verdict"] == "validated"
    safety_first = verify.get("repair_action_safety_first")
    assert safety_first is not None
    assert safety_first["order"][0] == "Safety"
    assert "lockout" in safety_first["Safety"][0].lower()
    assert safety_first["Preconditions"], "preconditions must be present"

    # Now the field says the repair FAILED: twin VALIDATED + field
    # FAILED must flag the implicated archetype row for review.
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


def test_successful_outcome_writes_history_without_flag(proof):
    db, product, revision = proof["db"], proof["product"], proof["revision"]
    session = _session(db, product, revision)
    client = _diag_client(db)
    verify = client.post(f"/diagnose/{session.id}/verify-repair",
                         json={"cause": CAUSE_A}).json()
    assert verify["verdict"] == "validated"
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


# --- Close-out-note mining (steal 5) ------------------------------------------

def _outcome(db, product, revision, session_id, cause, notes, outcome="fixed"):
    session = _session(db, product, revision, timeline=[])
    session.id = session_id
    db.add(DiagnosticOutcome(session_id=session_id, product_id=product.id,
                             revision_id=revision.id, outcome=outcome,
                             confirmed_cause=cause, technician_notes=notes))
    db.flush()
    return session


def test_closeout_template_is_labelled_and_never_cited():
    from backend.diagnostics.closeout import mine_closeout_notes
    db = _db()
    asset, asset_rev = _product(db)
    other, other_rev = _product(db, family="OMNIPRO", model="999")
    template = "Replaced part. Tested OK."
    _outcome(db, asset, asset_rev, "s-t1", "Feed Tensioner is too tight.", template)
    _outcome(db, other, other_rev, "s-t2", "Feed Tensioner is too tight.", template)
    # A terse note is reported, never over-read.
    _outcome(db, asset, asset_rev, "s-t3", None, "ok")
    db.commit()
    mined = mine_closeout_notes(db)
    assert mined["coverage"]["total_notes"] == 3
    assert mined["coverage"]["usable"] == 2
    assert mined["coverage"]["terse"] == 1
    assert len(mined["templates"]) == 1
    template_group = mined["templates"][0]
    assert template_group["is_template"] is True
    assert "same text on 2 assets" in template_group["template_label"]
    assert "not specific to this machine" in template_group["template_label"]


def _product(db, manufacturer="Harbor Freight", family="OMNIPRO", model="220"):
    product = Product(manufacturer=manufacturer, family=family, model=model)
    db.add(product)
    db.flush()
    revision = Revision(product_id=product.id, label="Rev A")
    db.add(revision)
    db.flush()
    return product, revision


def test_closeout_same_model_recurrence_is_a_fleet_pattern_and_unresolved_flagged():
    from backend.diagnostics.closeout import mine_closeout_notes
    db = _db()
    asset, asset_rev = _product(db)
    sibling_rev = _revision(db, asset, "Rev B")
    note = "Tensioner was too tight again, still slipping after adjustment."
    _outcome(db, asset, asset_rev, "s-f1", "Feed Tensioner is too tight.", note)
    _outcome(db, asset, sibling_rev, "s-f2", "Feed Tensioner is too tight.", note)
    db.commit()
    mined = mine_closeout_notes(db, symptom=SYMPTOM, product_id=asset.id)
    assert len(mined["groups"]) == 1
    group = mined["groups"][0]
    assert group["is_template"] is False  # same model, not a template
    assert group["unresolved"] is True
    assert "again" in group["unresolved_signals"]
    assert "still" in group["unresolved_signals"]
    assert group["fleet_pattern"] is not None
    assert group["fleet_pattern"]["cause"] == "Feed Tensioner is too tight."
    assert group["fleet_pattern"]["asset_count"] == 2


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
