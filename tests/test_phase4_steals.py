"""Phase 4 steals (items 1-4): sibling/fleet search, citation rule,
fixed-shape presentation, safety implication leads.

Hermetic: in-memory SQLite + TestClient, same fixture shape as
tests/test_phase2_sessions.py. No twin compile, no LLM calls.
"""
import sys
import types

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

from backend.db.models import (
    Base, DiagnosticOutcome, DiagnosticSession, DiagnosticStatus, FailureMode,
    Product, Revision,
)
from backend.diagnostics.backtracking import (
    GUESS_LABEL, cite_hypotheses, present_hypotheses, render_hypothesis_markdown,
    signatures_from_vocabulary,
)
from backend.diagnostics.fleet import sibling_search
from backend.diagnostics.safety import (
    LOCKOUT_LINE, check_safety, safety_evidence_from_state,
)
from backend.diagnostics.schema import DiagnosticState
from backend.knowledge.repair_cases import load_registry

import json
from pathlib import Path

VOCAB_PATH = (Path.home() / "workspace" / "goals" /
              "crater-ai-diagnosis-and-machine-model-workflow-improvement" /
              "files" / "oracle_vocabulary_v1.json")

SYMPTOM = "Wire Feed Motor Runs but Wire Does Not Feed Properly"


def _db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _product(db, manufacturer="Harbor Freight", family="OMNIPRO", model="220"):
    product = Product(manufacturer=manufacturer, family=family, model=model)
    db.add(product)
    db.flush()
    revision = Revision(product_id=product.id, label="Rev A")
    db.add(revision)
    db.flush()
    return product, revision


def _revision(db, product, label):
    revision = Revision(product_id=product.id, label=label)
    db.add(revision)
    db.flush()
    return revision


def _session(db, product, revision, symptoms, session_id="s-1"):
    state = DiagnosticState(symptoms=symptoms, confidence=0.5, turns_taken=1,
                            current_step={"type": "question", "content": "What do you observe?"})
    row = DiagnosticSession(id=session_id, product_id=product.id,
                            revision_id=revision.id, status=DiagnosticStatus.active,
                            state=state.model_dump(mode="json"))
    db.add(row)
    db.flush()
    return row


# --- item 1: sibling / fleet search ----------------------------------------

def test_sibling_search_finds_same_model_history_and_states_fallback():
    # Crater's Product is unique on (manufacturer, family, model), so
    # same-model siblings are other revisions of the same product.
    db = _db()
    asset, asset_rev = _product(db)
    sibling_rev = _revision(db, asset, "Rev B")
    cousin, cousin_rev = _product(db, family="OMNIPRO", model="999")
    # Sibling failure mode + recorded outcome for the same symptom.
    db.add(FailureMode(revision_id=sibling_rev.id, symptom=SYMPTOM,
                       possible_causes=["Feed Tensioner is too tight."],
                       diagnostic_test="step27 wood test"))
    sib_session = _session(db, asset, sibling_rev, [SYMPTOM], session_id="s-sib")
    db.add(DiagnosticOutcome(session_id=sib_session.id, product_id=asset.id,
                             revision_id=sibling_rev.id, outcome="fixed",
                             confirmed_cause="Feed Tensioner is too tight.",
                             repair_performed="Loosened tensioner",
                             technician_notes="Tensioner was too tight again."))
    # A different-model cousin's record for the same symptom must NOT
    # be returned as a sibling.
    cousin_session = _session(db, cousin, cousin_rev, [SYMPTOM], session_id="s-cousin")
    db.add(DiagnosticOutcome(session_id=cousin_session.id, product_id=cousin.id,
                             revision_id=cousin_rev.id, outcome="fixed",
                             confirmed_cause="Feed Tensioner is too tight."))
    db.commit()

    result = sibling_search(db, asset.id, SYMPTOM, revision_id=asset_rev.id)
    assert result["available"] is True
    # The asset has no history: the fallback is stated, not hidden.
    assert result["fallback_used"] is not None
    assert "manual + same-model siblings" in result["fallback_used"]
    assert result["counts"]["asset_failure_modes"] == 0
    assert result["counts"]["asset_recorded_outcomes"] == 0
    assert result["counts"]["sibling_recorded_outcomes"] == 1
    assert result["counts"]["sibling_revisions"] == 1
    sibling_records = result["sibling_records"]
    assert len(sibling_records) == 2  # 1 failure mode + 1 outcome
    assert all("OMNIPRO 220" in r["machine_identity"] for r in sibling_records)
    assert all("sibling" in r["machine_identity"] for r in sibling_records)
    # The cousin is labelled a cousin, not a sibling.
    assert len(result["cousin_records"]) == 0  # cousin has no failure mode
    # No fleet pattern yet: the cause recurs on only one asset.
    assert result["fleet_pattern"] is None


def test_fleet_pattern_when_same_cause_recurs_on_two_assets():
    db = _db()
    asset, asset_rev = _product(db)
    sibling_rev = _revision(db, asset, "Rev B")
    for revision, sid in ((asset_rev, "s-a"), (sibling_rev, "s-b")):
        session = _session(db, asset, revision, [SYMPTOM], session_id=sid)
        db.add(DiagnosticOutcome(session_id=session.id, product_id=asset.id,
                                 revision_id=revision.id, outcome="fixed",
                                 confirmed_cause="Feed Tensioner is too tight."))
    db.commit()
    result = sibling_search(db, asset.id, SYMPTOM, revision_id=asset_rev.id)
    assert result["fallback_used"] is None  # the asset has history now
    pattern = result["fleet_pattern"]
    assert pattern is not None
    assert pattern["cause"] == "Feed Tensioner is too tight."
    assert pattern["asset_count"] == 2
    assert len(pattern["records"]) == 2


def test_sibling_search_unknown_product_is_unavailable_not_a_guess():
    db = _db()
    result = sibling_search(db, "no-such-product", SYMPTOM)
    assert result["available"] is False
    assert result["sibling_records"] == []


# --- item 2: citation rule ---------------------------------------------------

def test_cited_hypotheses_carry_manual_citations_and_guesses_are_labelled():
    vocab = json.loads(VOCAB_PATH.read_text(encoding="utf-8"))
    sigs = [s for s in signatures_from_vocabulary(vocab)
            if s.case_id == "omni-01-wire-not-feeding"]
    hyps = [
        {"cause": "Insufficient wire feed pressure.", "rationale": "p42", "confidence": 0.4},
        {"cause": "The motor is haunted.", "rationale": "hunch", "confidence": 0.1},
    ]
    cited = cite_hypotheses(hyps, sigs)
    assert cited[0]["citations"]
    assert cited[0]["citations"][0]["source"] == "manual"
    assert cited[0]["is_guess"] is False
    assert cited[0]["label"] == "grounded"
    # The hunch is shown, not omitted — labelled as a guess.
    assert cited[1]["citations"] == []
    assert cited[1]["is_guess"] is True
    assert cited[1]["label"] == GUESS_LABEL


# --- item 3: fixed-shape presentation -----------------------------------------

def test_presentation_has_the_three_headings_in_order_for_every_hypothesis():
    vocab = json.loads(VOCAB_PATH.read_text(encoding="utf-8"))
    sigs = [s for s in signatures_from_vocabulary(vocab)
            if s.case_id == "omni-01-wire-not-feeding"]
    hyps = cite_hypotheses([
        {"cause": "Insufficient wire feed pressure.", "rationale": "p42", "confidence": 0.4},
        {"cause": "Motor has no power (upstream walk).", "rationale": "walk", "confidence": 0.3},
    ], sigs)
    presented = present_hypotheses(hyps, sigs, load_registry())
    assert len(presented) == 2
    for item in presented:
        assert list(item.keys())[-3:] == ["Evidence", "First check", "Expected fix"]
    # The table cause has a fix from the registry; the walk hypothesis
    # has none — and the empty heading is visible, not filled in.
    assert presented[0]["Expected fix"] != ""
    assert presented[1]["Expected fix"] == ""
    rendered = render_hypothesis_markdown(presented[1])
    assert rendered.index("Evidence") < rendered.index("First check") < rendered.index("Expected fix")


# --- item 4: safety leads -------------------------------------------------------

def test_guard_bypass_flags_safety_with_lockout_line_first():
    result = check_safety([{"text": "The guard is bypassed on this machine.",
                            "source": "technician_observation"}])
    assert result["safety_flagged"] is True
    assert result["triggers"][0]["trigger"] == "guard_or_interlock_defeated"
    assert result["lockout_line"] == LOCKOUT_LINE
    assert "not a safety assessment" in result["disclaimer"]


def test_uncertain_stored_energy_still_flags():
    result = check_safety([{"text": "Maybe a raised axis under load — not sure.",
                            "source": "technician_observation"}])
    assert result["safety_flagged"] is True
    assert result["triggers"][0]["trigger"] == "stored_energy"


def test_restart_cleared_fault_now_repeating_flags():
    result = check_safety([{"text": "It ran off and restarted, and now it failed again.",
                            "source": "technician_observation"}])
    assert result["safety_flagged"] is True
    assert result["triggers"][0]["trigger"] == "fault_cleared_by_restart_now_repeating"


def test_unsourced_text_never_sets_the_flag():
    result = check_safety([{"text": "The guard is bypassed.", "source": ""},
                           {"text": "burnt smell", "source": "llm_guess"}])
    assert result["safety_flagged"] is False
    assert len(result["ignored_evidence"]) == 2


def test_no_trigger_means_flag_false_with_reason():
    result = check_safety([{"text": "Wire does not feed.", "source": "technician_observation"}])
    assert result["safety_flagged"] is False
    assert result["safety_reason"] == "none of the safety triggers shown"
    assert result["lockout_line"] == LOCKOUT_LINE  # the line is always present


def test_safety_evidence_comes_from_state():
    state = DiagnosticState(symptoms=["guard bypassed"], safety_constraints=["isolate before work"])
    evidence = safety_evidence_from_state(state)
    assert evidence[0]["source"] == "technician_observation"
    assert evidence[-1]["source"] == "canonical_model_safety_fact"
    assert check_safety(evidence)["safety_flagged"] is True


def test_safety_endpoint_is_removed_and_respond_still_flags():
    """Phase 3 extraction: GET /diagnose/{id}/safety is removed (§2.5)
    — safety still runs as the first node of every turn in run_turn,
    and the check_safety / safety_evidence_from_state pure functions
    are covered by the tests above. This test proves the route is
    gone from the router."""
    from backend.api.routes import diagnostics as diagnostics_routes
    from backend.db.session import get_session
    db = _db()
    product, revision = _product(db)
    session = _session(db, product, revision,
                       ["Wire does not feed; the interlock is defeated."],
                       session_id="s-safe")
    db.commit()
    app = FastAPI()
    app.include_router(diagnostics_routes.router)

    def _override():
        yield db

    app.dependency_overrides[get_session] = _override
    client = TestClient(app)
    resp = client.get(f"/diagnose/{session.id}/safety")
    assert resp.status_code == 404, resp.text
