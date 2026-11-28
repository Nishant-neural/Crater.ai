"""Phase 4 — backtracking diagnostics: upstream walk, discriminating
tests, answer handling, pairs, unavailable-with-attempt, early
tournament re-ranking, and the Tier-2 flag (Option C).

Pure-logic tests are fast (no twin compile). The early-tournament
endpoint test compiles the proof twin once (~40 s CPU, same as the
3B/3D tests) and runs the corrected loop order end to end on the
in-memory DB: tournament early -> re-ranked hypotheses ->
discriminating test.
"""
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

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
    apply_test_answer,
    generate_pair_hypotheses,
    rerank_with_tournament,
    select_discriminating_test,
    signatures_from_registry,
    signatures_from_vocabulary,
    unavailable_with_attempt,
    upstream_walk,
)
from backend.ingestion.pipeline import lazy_ingest_summary, tier2_partition
from backend.knowledge.repair_cases import load_registry
from backend.simulation.archetypes import ARCHETYPES
from backend.simulation.edges import load_edge_list

VOCAB_PATH = (Path.home() / "workspace" / "goals" /
              "crater-ai-diagnosis-and-machine-model-workflow-improvement" /
              "files" / "oracle_vocabulary_v1.json")

_LIBRARY_TYPE_TO_ARCHETYPE = {"wire_feeder": "A4", "dc_motor": "A3", "relay": "A2"}


def _vocab_signatures(case_id=None):
    vocab = json.loads(VOCAB_PATH.read_text(encoding="utf-8"))
    sigs = signatures_from_vocabulary(vocab)
    return [s for s in sigs if case_id is None or s.case_id == case_id]


def _case1_hypotheses():
    return [
        {"cause": "Insufficient wire feed pressure.", "rationale": "p42", "confidence": 0.4,
         "supporting_evidence_chunk_ids": []},
        {"cause": "Incorrect wire feed roller size.", "rationale": "p42", "confidence": 0.3,
         "supporting_evidence_chunk_ids": []},
        {"cause": "Damaged MIG Gun, cable, or liner assembly.", "rationale": "p42", "confidence": 0.2,
         "supporting_evidence_chunk_ids": []},
        {"cause": "Feed Tensioner is too tight.", "rationale": "p42", "confidence": 0.1,
         "supporting_evidence_chunk_ids": []},
    ]


# --- upstream walk -------------------------------------------------------

def test_walk_from_feeder_reaches_motor_trigger_and_cord():
    edges = [(e["source_component"], e["target_component"])
             for e in load_edge_list()["edges"]]
    bindings = {"Wire Feed Motor": "A3", "Trigger": "A2", "Welder": "A1",
                "Input Power Cord": "A1", "Reset Button": "A2"}
    hyps = upstream_walk("Wire Feed Mechanism", edges, bindings, ARCHETYPES,
                         exclude={"liner assembly", "MIG Gun", "Contact Tip", "Welding Arc"})
    assert 1 <= len(hyps) <= 6
    components = [h.component for h in hyps]
    assert "Wire Feed Motor" in components
    # One hypothesis per component first, so the walk reaches the whole
    # power chain (trigger, button, cord) instead of spending every
    # slot on the motor's failure modes.
    assert "Trigger" in components
    assert "Input Power Cord" in components
    # The arc is downstream of the feeder in case 1's required chain,
    # so it is excluded as a cause even though the arc -> welder
    # feedback edge would otherwise make the walk name it.
    assert "Welding Arc" not in components
    # The walk is bounded by topology: every hypothesis names a component
    # on the symptom's upstream chain, with the chain recorded.
    assert all(h.chain and h.chain[0] == "Wire Feed Mechanism" for h in hyps)
    assert all(h.evidence for h in hyps)
    assert all(h.discriminating_test for h in hyps)


def test_walk_uses_archetype_failure_modes_not_generic_text():
    edges = [(e["source_component"], e["target_component"])
             for e in load_edge_list()["edges"]]
    bindings = {"Wire Feed Motor": "A3"}
    hyps = upstream_walk("Wire Feed Mechanism", edges, bindings, ARCHETYPES,
                         max_hypotheses=5)
    motor_hyps = [h for h in hyps if h.component == "Wire Feed Motor"]
    assert motor_hyps
    assert all(h.archetype_id == "A3" for h in motor_hyps)


def test_walk_unknown_component_yields_no_hypotheses():
    hyps = upstream_walk("No Such Component", [("A", "B")], {}, ARCHETYPES)
    assert hyps == []


# --- discriminating-test selection ---------------------------------------

def test_select_discriminating_test_splits_case1():
    sigs = _vocab_signatures("omni-01-wire-not-feeding")
    assert len(sigs) == 4
    best = select_discriminating_test(sigs, _case1_hypotheses())
    assert best is not None
    assert best["test"] in {"step27_wood_test", "post_release_spin", "groove_check"}
    assert best["information_gain_bits"] > 0
    assert best["pairs_separated"] >= 1


def test_select_test_returns_none_when_nothing_splits():
    sigs = _vocab_signatures("omni-01-wire-not-feeding")[:1]
    assert select_discriminating_test(sigs, _case1_hypotheses()[:1]) is None


def test_registry_signatures_split_by_scope():
    sigs = signatures_from_registry(load_registry())
    case1 = [s for s in sigs if s.case_id == "omni-01-wire-not-feeding"]
    assert len(case1) == 4
    # Registry signatures carry test scope only — they can still pick a
    # test that is named for some causes and not others.
    best = select_discriminating_test(case1, _case1_hypotheses())
    assert best is None or best["information_gain_bits"] >= 0


# --- answer handling ------------------------------------------------------

def test_wood_test_stops_rules_out_gun_keeps_unmodelled_tensioner():
    sigs = _vocab_signatures("omni-01-wire-not-feeding")
    out = apply_test_answer(_case1_hypotheses(), sigs, "step27_wood_test", "stops")
    eliminated = [h["cause"] for h in out["eliminated"]]
    kept = [h["cause"] for h in out["kept"]]
    assert "Damaged MIG Gun, cable, or liner assembly." in eliminated
    # (d) tension-too-tight has an unmodelled wood outcome: kept at low
    # confidence, never ruled out by a test that cannot speak about it.
    assert "Feed Tensioner is too tight." in kept
    tensioner = [h for h in out["kept"] if "Tensioner" in h["cause"]][0]
    assert tensioner["confidence"] < 0.1 + 1e-9 or tensioner["confidence"] <= 0.15
    assert "Insufficient wire feed pressure." in kept


def test_sometimes_keeps_everything_at_low_confidence():
    out = apply_test_answer(_case1_hypotheses(),
                            _vocab_signatures("omni-01-wire-not-feeding"),
                            "step27_wood_test", "sometimes")
    assert out["eliminated"] == []
    assert len(out["kept"]) == 4
    assert all(h["confidence"] <= 0.3 for h in out["kept"])
    assert any(h.get("intermittent") for h in out["kept"])


def test_not_sure_rules_nothing_out_and_halves_confidence():
    out = apply_test_answer(_case1_hypotheses(),
                            _vocab_signatures("omni-01-wire-not-feeding"),
                            "step27_wood_test", "not_sure")
    assert out["eliminated"] == []
    assert out["kept"][0]["confidence"] == 0.2  # 0.4 halved


def test_yes_confirms_leading_no_rules_it_out():
    sigs = _vocab_signatures("omni-01-wire-not-feeding")
    yes = apply_test_answer(_case1_hypotheses(), sigs, "step27_wood_test", "yes")
    assert yes["kept"][0]["cause"].startswith("Insufficient")
    assert yes["kept"][0]["confidence"] > 0.4
    no = apply_test_answer(_case1_hypotheses(), sigs, "step27_wood_test", "no")
    assert any("Insufficient" in h["cause"] for h in no["eliminated"])
    assert len(no["kept"]) == 3


# --- two faults ------------------------------------------------------------

def test_pairs_come_from_top_of_ranking_only():
    hyps = _case1_hypotheses()
    pairs = generate_pair_hypotheses(hyps)
    assert pairs
    assert all(p.get("pair") for p in pairs)
    # Every pair is built from the top 3 singles.
    top3 = {h["cause"] for h in hyps[:3]}
    for pair in pairs:
        assert any(cause in pair["cause"] for cause in top3)
    assert pairs[0]["confidence"] < min(h["confidence"] for h in hyps[:2])


# --- unavailable-with-attempt ----------------------------------------------

def test_unavailable_with_attempt_is_never_bare():
    attempt = unavailable_with_attempt(
        [{"cause": "motor has no power", "confidence": 0.4,
          "evidence": "upstream walk", "source": "upstream_walk"}],
        {"test": "step27_wood_test", "information_gain_bits": 0.5},
        ["the trigger's state"], "twin cannot evaluate the cause")
    assert attempt["available"] is False
    assert attempt["ranked_guesses"]
    assert attempt["discriminating_test"]["test"] == "step27_wood_test"
    assert attempt["missing"] == ["the trigger's state"]


def test_unavailable_with_no_guesses_still_carries_an_attempt():
    attempt = unavailable_with_attempt([], None, [], "no twin")
    assert attempt["ranked_guesses"]
    assert attempt["ranked_guesses"][0]["confidence"] == 0.1
    assert attempt["missing"]


# --- tournament re-ranking ---------------------------------------------------

def test_rerank_validated_first_rejected_demoted_not_eliminated():
    hyps = _case1_hypotheses()
    rows = [
        {"cause": "Insufficient wire feed pressure.", "status": "rejected", "verdict": "rejected"},
        {"cause": "Incorrect wire feed roller size.", "status": "validated", "verdict": "validated"},
        {"cause": "Damaged MIG Gun, cable, or liner assembly.", "status": "unavailable", "verdict": None},
        {"cause": "Feed Tensioner is too tight.", "status": "unavailable", "verdict": None},
    ]
    out = rerank_with_tournament(hyps, rows)
    assert out["reranked"] is True
    assert out["counts"] == {"validated": 1, "rejected": 1, "unavailable": 2}
    assert out["hypotheses"][0]["cause"].startswith("Incorrect")
    rejected = [h for h in out["hypotheses"] if "Insufficient" in h["cause"]][0]
    assert rejected["confidence"] == 0.2  # 0.4 demoted by half, kept
    assert len(out["hypotheses"]) == 4  # nothing eliminated


# --- Tier-2 flag (Option C) ---------------------------------------------------

def _fake_chunk(chunk_id, page, extra_pages=None):
    return SimpleNamespace(id=chunk_id, page_number=page,
                           extra={"pages": extra_pages} if extra_pages else {})


def test_tier2_flag_off_defers_tier2_only():
    registry = load_registry()
    tier1 = _fake_chunk("c1", 42)   # Tier 1 (p42 troubleshooting)
    tier2 = _fake_chunk("c2", 10)   # Tier 2 (p10 spool setup, not Tier 1/3)
    extract, deferred = tier2_partition([tier1, tier2], registry, ingest_tier2=False)
    assert [c.id for c in extract] == ["c1"]
    assert [c.id for c in deferred] == ["c2"]


def test_tier2_flag_on_extracts_everything_like_phase1():
    registry = load_registry()
    chunks = [_fake_chunk("c1", 42), _fake_chunk("c2", 10)]
    extract, deferred = tier2_partition(chunks, registry, ingest_tier2=True)
    assert [c.id for c in extract] == ["c1", "c2"]
    assert deferred == []


def test_tier2_lazy_pages_release_only_those_pages():
    registry = load_registry()
    chunks = [_fake_chunk("c1", 42), _fake_chunk("c2", 10), _fake_chunk("c3", 15)]
    extract, deferred = tier2_partition(chunks, registry, ingest_tier2=False, lazy_pages=[10])
    assert [c.id for c in extract] == ["c1", "c2"]
    assert [c.id for c in deferred] == ["c3"]


def test_lazy_ingest_summary_without_deferred_chunks():
    # No checkpoint / no document: a pure read that reports nothing released.
    from sqlalchemy import create_engine as _create_engine
    from sqlalchemy.orm import sessionmaker as _sessionmaker
    from sqlalchemy.pool import StaticPool as _StaticPool
    from backend.db.models import Base
    engine = _create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                            poolclass=_StaticPool)
    Base.metadata.create_all(bind=engine)
    db = _sessionmaker(bind=engine)()
    summary = lazy_ingest_summary(db, "no-such-job", [10])
    assert summary["chunks_released"] == []
    assert summary["remaining_deferred"] == []
    assert summary["pages"] == [10]


# --- early tournament endpoint (corrected loop order) ------------------------

def test_early_tournament_reranks_and_selects_discriminating_test(monkeypatch):
    from backend.db.models import (
        Base, DiagnosticSession, DiagnosticStatus, DigitalTwin,
        MachineKnowledgeModelSnapshot, Product, Revision, TwinStatus,
    )
    from backend.diagnostics.schema import DiagnosticState
    from backend.simulation.compiler import compile_machine_model
    from backend.simulation.edges import load_edge_list_for_product
    from backend.simulation.expert_rules import apply_expert_seed, load_seed_for_product
    from backend.simulation.omnipro_proof import (
        build_omnipro_proof_model, initial_twin_state, snapshot_model_for_tournament,
    )
    from tests.twin_testkit import make_twin_client

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
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

    proof_model = build_omnipro_proof_model()
    seed = load_seed_for_product("Harbor Freight", "220", "OMNIPRO")
    if seed is not None:
        proof_model, _, _ = apply_expert_seed(proof_model, seed)
    definition, _warnings = compile_machine_model(
        proof_model, name="Phase 4 proof twin", edge_list=load_edge_list_for_product("Harbor Freight", "220", "OMNIPRO"))
    twin = DigitalTwin(product_id=product.id, revision_id=revision.id, name="Phase 4 proof twin",
                       status=TwinStatus.active,
                       definition=definition.model_dump(mode="json"),
                       state=initial_twin_state(definition))
    db.add(twin)
    snapshot = MachineKnowledgeModelSnapshot(
        revision_id=revision.id, version=1,
        model=snapshot_model_for_tournament())
    db.add(snapshot)
    db.flush()

    # Deliberately wrong order: the rejected-looking cause leads.
    state = DiagnosticState(
        symptoms=["Wire Feed Motor Runs but Wire Does Not Feed Properly"],
        hypotheses=[
            {"cause": "Feed Tensioner is too tight.", "rationale": "p42", "confidence": 0.9,
             "supporting_evidence_chunk_ids": []},
            {"cause": "Insufficient wire feed pressure.", "rationale": "p42", "confidence": 0.4,
             "supporting_evidence_chunk_ids": []},
            {"cause": "Incorrect wire feed roller size.", "rationale": "p42", "confidence": 0.3,
             "supporting_evidence_chunk_ids": []},
            {"cause": "Damaged MIG Gun, cable, or liner assembly.", "rationale": "p42", "confidence": 0.2,
             "supporting_evidence_chunk_ids": []},
        ],
        current_step={"type": "question",
                      "content": "What do you observe?",
                      "rationale": "agent question"},
        confidence=0.9, turns_taken=1)
    session = DiagnosticSession(product_id=product.id, revision_id=revision.id,
                                status=DiagnosticStatus.active,
                                state=state.model_dump(mode="json"))
    db.add(session)
    db.commit()

    # Phase 3 extraction: POST /early-tournament is removed (§2.5) —
    # the tournament runs inside POST /respond instead. The helper is
    # driven directly here and the endpoint asserted 404 below.
    # Phase 3 twin split: the tournament now runs over HTTP to the
    # twin-verifier service; the test kit wires the client to the real
    # twin app over this test DB (inline dispatch, no broker).
    from backend.api.routes.diagnostics import _apply_early_tournament
    summary = _apply_early_tournament(
        db, session, state, twin_client=make_twin_client(db, monkeypatch))
    assert summary is not None
    assert summary["available"] is True
    assert summary["counts"]["validated"] == 4
    # All four case-1 causes validate, so the re-rank keeps all four —
    # and the discriminating test is selected from the signatures.
    assert len(summary["reranked"]["hypotheses"]) == 4
    assert summary["discriminating_test"] is not None
    assert summary["discriminating_test"]["test"] in {
        "step27_wood_test", "post_release_spin", "groove_check"}
    # The persisted session carries the timeline entry and the re-rank.
    db.refresh(session)
    persisted = DiagnosticState.model_validate(session.state)
    assert any(entry.get("kind") == "early_tournament" for entry in persisted.timeline)

    # The endpoint itself is gone from the diagnosis service (§2.5).
    from backend.api.routes import diagnostics as diagnostics_routes
    from backend.db.session import get_session

    app = FastAPI()
    app.include_router(diagnostics_routes.router)

    def _override():
        yield db

    app.dependency_overrides[get_session] = _override
    client = TestClient(app)
    resp = client.post(f"/diagnose/{session.id}/early-tournament")
    assert resp.status_code == 404, resp.text
