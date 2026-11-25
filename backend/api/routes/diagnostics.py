"""
The Phase 2 payoff endpoints: start a troubleshooting session on a symptom,
then keep feeding it observations/measurements until it concludes or
ends with the ranked best guesses.

Phase 4 corrected loop order (13 Nov 2026 correction, plan.md Phases 4-5
carried the old ordering): hypotheses ranked -> the tournament runs EARLY
and re-ranks them (applied best-effort by POST /diagnose/{id}/respond
when an approved twin exists, and pre-turn by the graph via the thin
TwinVerifyClient) -> the agent asks the discriminating physical test
for the top remaining cause -> measurement -> grounded conclusion ->
verify the chosen repair. Verify / tournament are NOT gated behind the
grounded conclusion any more.

Phase 3 diagnosis-agent extraction (contracts README §2.5): the
on-demand endpoints verify-repair, verify-hypotheses,
early-tournament and upstream-walk, the next-test and guided-checks
GETs, and the safety and fleet-patterns GETs do NOT exist under /v1/
any more (deleted at the end of this file) — the turn response is the
only public surface for tests, safety, and verification status. The
tournament and the upstream walk themselves stay, driven by respond().
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from backend.db.models import (
    DiagnosticSession, DigitalTwin, DiagnosticOutcome, Expert, ExpertInterview, ExpertInterviewTurn,
    KnowledgeVersion, ExpertKnowledge, KnowledgeStatus, KnowledgeType, InterviewStatus, TwinStatus,
)
from backend.db.session import get_session
import httpx
import json as _json
import logging as _logging
from pathlib import Path as _Path
_logger = _logging.getLogger(__name__)
from backend.config import settings
from backend.diagnostics.agent import run_turn, start_session
from backend.diagnostics.twin_verification import TwinVerifyClient
from backend.retrieval import ingestion_client as ingestion_client_mod
from backend.simulation.schema import DigitalTwinDefinition
from backend.diagnostics.downtime import close_downtime
from backend.diagnostics.learning import apply_field_outcome
from backend.diagnostics.pm import draft_pm_interval
from backend.diagnostics.backtracking import (
    MAX_LIVE_HYPOTHESES, _texts_match, apply_test_answer,
    generate_pair_hypotheses,
    select_discriminating_test,
    signatures_from_registry, signatures_from_vocabulary,
    split_live_and_reserves, unavailable_with_attempt, upstream_walk,
    rerank_with_tournament,
)
from backend.diagnostics.schema import (
    DiagnosticSessionView, DiagnosticState, Hypothesis, NextStep,
)
from backend.knowledge.repair_cases import load_registry
from backend.simulation.archetypes import ARCHETYPES
from backend.simulation.edges import load_edge_list

router = APIRouter(prefix="/diagnose", tags=["diagnostics"])


class StartDiagnosticRequest(BaseModel):
    product_id: str
    revision_id: str | None = None
    symptom: str


class RespondRequest(BaseModel):
    input: str
    input_kind: str = "observation"  # "symptom" | "observation" | "measurement"


def _load_vocabulary_signatures():
    """Oracle-vocabulary signatures when the reviewed file is present;
    None otherwise (callers fall back to registry scope signatures)."""
    try:
        vocab_path = (_Path.home() / "workspace" / "goals" /
                      "crater-ai-diagnosis-and-machine-model-workflow-improvement" /
                      "files" / "oracle_vocabulary_v1.json")
        if vocab_path.exists():
            return signatures_from_vocabulary(_json.loads(vocab_path.read_text(encoding="utf-8")))
    except Exception as exc:  # noqa: BLE001 - vocabulary is optional here
        _logger.warning("diagnostics: oracle vocabulary unavailable: %s", exc)
    return None


def _signatures_for_session():
    """Best available signatures: oracle vocabulary first, registry scope second."""
    signatures = _load_vocabulary_signatures()
    if signatures:
        return signatures
    try:
        return signatures_from_registry(load_registry())
    except Exception as exc:  # noqa: BLE001 - no signatures is not fatal
        _logger.warning("diagnostics: registry signatures unavailable: %s", exc)
        return []


# --- Phase 4 follow-up (14 Nov 2026): the upstream walk, wired into the
# live loop ---------------------------------------------------------------
# The verifier caught that upstream_walk / apply_test_answer /
# generate_pair_hypotheses were called only by scripts/run_phase4_offtable.py
# and the unit tests — the live loop's off-table behaviour was
# an unavailable-with-attempt alone. The helpers below wire the walk
# into the live loop (POST /diagnose/{id}/respond): when a turn
# exhausts the table causes (or no table cause matches), the walk
# generates ranked hypotheses from topology + archetype states; the
# technician's next answer advances the walk via apply_test_answer;
# pairs come from the top of the ranking only once the singles are
# exhausted. (The on-demand POST /diagnose/{id}/upstream-walk endpoint
# was deleted in the Phase 3 diagnosis-agent extraction, contracts
# §2.5; the walk itself stays, driven by respond.)
# The live cap (MAX_LIVE_HYPOTHESES = 5) is enforced by
# split_live_and_reserves — the top 5 are live, the rest are ranked
# reserves recorded on the timeline and returned in the response, never
# dropped silently. Fail-closed throughout: no invented edges (twin
# relations first, else the reviewed edge list), no invented bindings
# (twin metadata first, then the canonical snapshot's library_type, then
# a unique exact-alias match only), and unavailable-with-attempt whenever
# the walk cannot run — a bare unavailable is still a miss.

_ARCHETYPE_CODE_BY_NAME = {
    "source": "A1", "switch": "A2", "converter": "A3",
    "transmission": "A4", "sensor": "A5", "controller": "A6",
    "load": "A7", "conductor": "A8", "storage": "A9",
    "valve": "A10", "interface": "A11",
}
_LIBRARY_TYPE_TO_ARCHETYPE_CODE = {"wire_feeder": "A4", "dc_motor": "A3", "relay": "A2"}
_WALK_RATIONALE_PREFIX = "Upstream walk (topology + archetype): "


def _load_vocabulary():
    """Raw oracle-vocabulary JSON when present; None otherwise."""
    try:
        vocab_path = (_Path.home() / "workspace" / "goals" /
                      "crater-ai-diagnosis-and-machine-model-workflow-improvement" /
                      "files" / "oracle_vocabulary_v1.json")
        if vocab_path.exists():
            return _json.loads(vocab_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - vocabulary is optional here
        _logger.warning("diagnostics: oracle vocabulary unavailable: %s", exc)
    return None


def _matched_case(state):
    """Registry case whose symptom matches a session symptom, or None."""
    try:
        registry = load_registry()
    except Exception:
        return None
    for symptom in (state.symptoms or []):
        for case in registry.cases:
            if _texts_match(symptom, case.symptom):
                return case
    return None


def _symptom_component_for_state(state):
    """(symptom_component, downstream_exclude, case_id) or None.

    The symptom component is the component whose output is absent: the
    first twin-expressible vocabulary row's fault component for the
    matched case (for case 1: the Wire Feed Mechanism), falling back to
    the case's first observable component. Downstream components come
    from the case's required_chain (effects, not causes) — the same
    exclusion the off-table harness uses, so the arc -> welder feedback
    edge cannot name the Welding Arc as a producer of the welder.
    """
    case = _matched_case(state)
    if case is None:
        return None
    signatures = [s for s in _signatures_for_session() if s.case_id == case.id]
    symptom_component = ""
    for sig in signatures:
        if sig.component:
            symptom_component = sig.component
            break
    vocab = _load_vocabulary()
    chain: list[str] = []
    if vocab is not None:
        entry = (vocab.get("cases") or {}).get(case.id) or {}
        if not symptom_component:
            observable_components = entry.get("observable_components") or {}
            if observable_components:
                symptom_component = next(iter(observable_components.values()))
        chain = [str(step.get("component") or "")
                 for step in entry.get("required_chain") or []]
    if not symptom_component:
        return None
    downstream: set[str] = set()
    if symptom_component in chain:
        downstream = set(chain[chain.index(symptom_component) + 1:])
    return symptom_component, downstream, case.id


def _walk_edges(db, session):
    """(edges, source) — the canonical model's relations first (via
    ingestion's HTTP API), else the reviewed edge list. Never
    invented: an empty list means the walk cannot run and the caller
    returns unavailable-with-attempt.

    Phase 3 diagnosis-agent extraction: the old Component /
    ComponentRelationship DB reads are deleted; the canonical model
    endpoint carries the same entities + relations. ``db`` is kept in
    the signature (callers pass it) but no longer read here.
    """
    if session.revision_id:
        try:
            payload = ingestion_client_mod.get_ingestion_client().get_model(session.revision_id)
            if payload:
                model = payload.get("model") or {}
                by_id = {e.get("id"): e.get("name")
                         for e in model.get("entities", []) if e.get("id")}
                edges = [(by_id[r.get("subject_id")], by_id[r.get("object_id")])
                         for r in model.get("relations", [])
                         if r.get("subject_id") in by_id and r.get("object_id") in by_id]
                if edges:
                    return edges, "canonical_model"
        except Exception as exc:  # noqa: BLE001 - fall back to the edge list
            _logger.warning("diagnostics: canonical model unavailable for walk: %s", exc)
    try:
        edge_list = load_edge_list()
        return ([(e["source_component"], e["target_component"])
                 for e in edge_list.get("edges") or []], "reviewed_edge_list")
    except Exception as exc:  # noqa: BLE001 - no edges is an attempt, not a crash
        _logger.warning("diagnostics: reviewed edge list unavailable for walk: %s", exc)
        return [], "none"


def _fetch_twin_definition(revision_id: str | None) -> dict | None:
    """The approved twin's definition for a revision, from the
    twin-verifier service (GET /v1/simulation/revisions/{id}/twin).

    Phase 3 twin-verifier extraction: the walk reads the twin over
    HTTP now, never from the twin tables. None when the service is
    unconfigured, unreachable, or holds no twin — the caller falls
    back to the canonical model. Never raises."""
    if not revision_id or not settings.twin_verifier_base_url:
        return None
    try:
        with httpx.Client(timeout=2.0) as client:
            resp = client.get(
                f"{settings.twin_verifier_base_url.rstrip('/')}"
                f"/v1/simulation/revisions/{revision_id}/twin")
        if resp.status_code != 200:
            return None
        definition = (resp.json() or {}).get("definition") or {}
        return definition if isinstance(definition, dict) else None
    except Exception as exc:  # noqa: BLE001 - no twin is a fallback, not a crash
        _logger.warning("diagnostics: twin context unavailable for walk: %s", exc)
        return None


def _walk_bindings(db, session):
    """(bindings, archetypes_by_code).

    Bindings are component name -> A-code ("A3"), from the approved
    twin's own metadata first (evidence-based binding, entity id ->
    archetype, fetched from the twin-verifier service), then the
    canonical model's library_type (fetched from ingestion's HTTP
    API), then a unique exact-alias match only (a name that equals
    exactly one archetype alias). A component with no binding still
    walks — it gets the archetype-shaped generic fault — it is never
    guessed into an archetype.
    """
    bindings: dict[str, str] = {}
    definition = _fetch_twin_definition(session.revision_id)
    if definition is not None:
        by_id = {c.get("id"): c.get("name")
                 for c in definition.get("components") or []
                 if isinstance(c, dict) and c.get("id")}
        for entity_id, record in (
                (definition.get("metadata") or {}).get("archetype_bindings") or {}).items():
            archetype_id = (record or {}).get("archetype_id")
            code = _ARCHETYPE_CODE_BY_NAME.get(str(archetype_id or ""))
            if code and entity_id in by_id:
                bindings[by_id[entity_id]] = code
    if session.revision_id:
        # The canonical model still carries library_type (Phase 3
        # extraction: fetched from ingestion's HTTP API, never read
        # from the snapshot table).
        try:
            payload = ingestion_client_mod.get_ingestion_client().get_model(session.revision_id)
            model = dict(payload.get("model") or {}) if payload else {}
            for entity in (model.get("entities") or []):
                name = entity.get("name") or ""
                code = _LIBRARY_TYPE_TO_ARCHETYPE_CODE.get(str(entity.get("library_type") or ""))
                if code and name and name not in bindings:
                    bindings[name] = code
        except Exception as exc:  # noqa: BLE001 - alias fallback still applies
            _logger.warning("diagnostics: canonical model bindings unavailable for walk: %s", exc)
    # Unique exact-alias match only, over every component the edges name.
    # An alias shared by two archetypes binds nothing — it stays unbound.
    edges, _source = _walk_edges(db, session)
    alias_codes: dict[str, set[str]] = {}
    for human, code in _ARCHETYPE_CODE_BY_NAME.items():
        archetype = ARCHETYPES.get(human)
        if archetype is None:
            continue
        for alias in (archetype.aliases or []):
            alias_codes.setdefault(" ".join(str(alias).strip().lower().split()), set()).add(code)
    for name in {n for pair in edges for n in pair}:
        if name in bindings:
            continue
        codes = alias_codes.get(" ".join(str(name).strip().lower().split())) or set()
        if len(codes) == 1:
            bindings[name] = next(iter(codes))
    archetypes_by_code = {code: ARCHETYPES[human]
                          for human, code in _ARCHETYPE_CODE_BY_NAME.items()
                          if human in ARCHETYPES}
    return bindings, archetypes_by_code


def _walk_is_live(state) -> bool:
    return any((h.rationale or "").startswith(_WALK_RATIONALE_PREFIX)
               for h in (state.hypotheses or []))


def _latest_walk_entry(state):
    """Most recent upstream_walk / upstream_walk_answer timeline entry.

    Reserves live here, not only on the original walk entry: each
    answer promotes reserves into live, so re-reading the original
    entry would promote the same reserve twice (a duplicate live
    hypothesis) and the live set would never shrink to the pair stage.
    """
    for entry in reversed(state.timeline or []):
        if entry.get("kind") in ("upstream_walk", "upstream_walk_answer"):
            return entry
    return None


def _is_off_table(state) -> bool:
    """Table causes exhausted, or no table cause matches at all."""
    if not state.hypotheses:
        return True
    case = _matched_case(state)
    if case is None:
        return True
    table_causes = [c.cause for c in case.causes]
    if not any(_texts_match(h.cause, cause)
               for h in state.hypotheses for cause in table_causes):
        return True
    eliminated = [h.cause for h in (state.eliminated_hypotheses or [])]
    if eliminated and all(any(_texts_match(h.cause, cause) for cause in eliminated)
                          for h in state.hypotheses):
        return True
    return False


def _walk_hypothesis_dict(walk_hyp) -> dict:
    return {
        "cause": walk_hyp.cause,
        "rationale": (_WALK_RATIONALE_PREFIX + walk_hyp.evidence +
                      " | Discriminating test (walk-generated, archetype-shaped, "
                      "not a manual-cited test): " + walk_hyp.discriminating_test),
        "confidence": walk_hyp.confidence,
        "supporting_evidence_chunk_ids": [],
        "discriminating_test": walk_hyp.discriminating_test,
        "source": "upstream_walk",
    }


def _record_walk_unavailable(db, session, state, reason, missing):
    """Persist an unavailable-with-attempt for a walk that cannot run."""
    attempt = unavailable_with_attempt([], None, missing, reason)
    state.timeline.append({
        "turn": state.turns_taken, "kind": "upstream_walk",
        "input": "; ".join(state.symptoms or []),
        "available": False, "reason": reason,
        "step_type": "upstream_walk",
        "step": "Upstream walk unavailable: " + reason})
    session.state = state.model_dump(mode="json")
    db.commit()
    return {"available": False, "symptom_component": None, "case_id": None,
            "live": [], "reserves": [],
            "hypotheses": [h.model_dump() for h in state.hypotheses],
            "discriminating_test": None, "attempt": attempt}


def _run_upstream_walk(db, session, state, *, force=False):
    """Run the upstream walk into the live session state. Returns the
    walk summary dict, or None when the table is not exhausted and the
    walk was not forced. Never raises: a walk failure is an
    unavailable-with-attempt, never a broken turn."""
    try:
        if _walk_is_live(state) and not force:
            entry = _latest_walk_entry(state)
            return {"available": True, "already_live": True,
                    "symptom_component": (entry or {}).get("symptom_component"),
                    "case_id": (entry or {}).get("case_id"),
                    "live": [h.model_dump() for h in state.hypotheses],
                    "reserves": (entry or {}).get("reserves") or [],
                    "discriminating_test": (entry or {}).get("discriminating_test"),
                    "hypotheses": [h.model_dump() for h in state.hypotheses],
                    "attempt": None}
        if not force and not _is_off_table(state):
            return None
        located = _symptom_component_for_state(state)
        if located is None:
            return _record_walk_unavailable(
                db, session, state,
                "No symptom component could be identified for the upstream walk "
                "(no registry case matches this session's symptom)",
                ["a registry case matching the symptom"])
        symptom_component, downstream, case_id = located
        edges, edges_source = _walk_edges(db, session)
        if not edges:
            return _record_walk_unavailable(
                db, session, state,
                "No topology edges are available for the upstream walk "
                "(neither twin relations nor the reviewed edge list)",
                ["reviewed topology for this machine"])
        bindings, archetypes_by_code = _walk_bindings(db, session)
        walk = upstream_walk(symptom_component, edges, bindings,
                             archetypes_by_code, exclude=downstream)
        if not walk:
            return _record_walk_unavailable(
                db, session, state,
                f"Upstream walk from {symptom_component!r} produced no hypotheses",
                [f"an upstream component of {symptom_component}"])
        live, reserves = split_live_and_reserves(walk)
        live_dicts = [_walk_hypothesis_dict(h) for h in live]
        reserve_dicts = [_walk_hypothesis_dict(h) for h in reserves]
        # Pre-existing live hypotheses are demoted to reserves, never dropped.
        for h in (state.hypotheses or []):
            if not (h.rationale or "").startswith(_WALK_RATIONALE_PREFIX):
                reserve_dicts.append({**h.model_dump(), "source": "session_hypothesis"})
        discriminating_test = live[0].discriminating_test if live else ""
        state.hypotheses = [Hypothesis.model_validate(h) for h in live_dicts]
        state.current_step = NextStep(
            type="question",
            content=("Upstream walk (table causes exhausted): " + discriminating_test +
                     " — this is a walk-generated question (archetype-shaped, not a "
                     "manual-cited test). Report what you observe."),
            rationale=("All table causes are exhausted / no table cause matches, so "
                       "the upstream walk generated ranked hypotheses from topology "
                       "and archetype states; the top one is tested first."),
        )
        state.timeline.append({
            "turn": state.turns_taken, "kind": "upstream_walk",
            "input": "; ".join(state.symptoms or []),
            "available": True, "symptom_component": symptom_component,
            "case_id": case_id, "edges_source": edges_source,
            "live": live_dicts, "reserves": reserve_dicts,
            "discriminating_test": discriminating_test,
            "step_type": "upstream_walk",
            "step": (f"Upstream walk from {symptom_component}: {len(live_dicts)} live + "
                     f"{len(reserve_dicts)} ranked reserves"),
        })
        session.state = state.model_dump(mode="json")
        db.commit()
        return {"available": True, "already_live": False,
                "symptom_component": symptom_component, "case_id": case_id,
                "live": live_dicts, "reserves": reserve_dicts,
                "discriminating_test": discriminating_test,
                "hypotheses": live_dicts, "attempt": None}
    except Exception as exc:  # noqa: BLE001 - a walk failure must not break the turn
        _logger.warning("diagnostics: upstream walk failed: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
        return {"available": False, "symptom_component": None, "case_id": None,
                "live": [], "reserves": [],
                "hypotheses": [h.model_dump() for h in state.hypotheses],
                "discriminating_test": None,
                "attempt": unavailable_with_attempt(
                    [], None, ["a working upstream walk"],
                    f"Upstream walk failed: {exc}")}


def _map_walk_answer(answer_text: str) -> str:
    text = " ".join(str(answer_text or "").strip().lower().split())
    if "sometimes" in text or "intermittent" in text:
        return "sometimes"
    if text in ("not sure", "not_sure", "unsure", "don't know", "dont know", "unknown"):
        return "not_sure"
    if text.startswith("yes") or text == "y":
        return "yes"
    if text.startswith("no") or text == "n":
        return "no"
    return text  # a test-outcome value (e.g. "stops"); signatures decide


def _apply_walk_answer(db, session, state, answer_text, input_kind):
    """Advance a live upstream walk with the technician's answer.

    Runs BEFORE the LLM turn in respond(): the deterministic
    backtracking update first, then the LLM reasons over the updated
    set. Only yes/no/not-sure/sometimes advance the walk
    deterministically — an outcome value ("stops") is left to the LLM,
    because walk causes carry no vocabulary signatures to match it
    against; apply_test_answer keeps such a hypothesis instead of
    ruling it out. Returns the summary or None when no walk is live.
    """
    if input_kind == "symptom" or not _walk_is_live(state):
        return None
    live_dicts = [h.model_dump() for h in state.hypotheses
                  if (h.rationale or "").startswith(_WALK_RATIONALE_PREFIX)]
    if not live_dicts:
        return None
    entry = _latest_walk_entry(state)
    reserves = [dict(r) for r in ((entry or {}).get("reserves") or [])]
    # Never promote a reserve whose cause is already live (a stale
    # timeline entry must not duplicate a hypothesis).
    live_causes = {h.get("cause") for h in live_dicts}
    reserves = [r for r in reserves if r.get("cause") not in live_causes]
    test_key = ""
    for candidate in ((entry or {}).get("kept") or (entry or {}).get("live") or []) + \
            ((entry or {}).get("reserves") or []):
        if candidate.get("cause") == live_dicts[0]["cause"]:
            test_key = candidate.get("discriminating_test") or ""
            break
    mapped = _map_walk_answer(answer_text)
    out = apply_test_answer(live_dicts, _signatures_for_session(),
                            test_key or "upstream_walk_test", mapped)
    kept = [dict(h) for h in out["kept"]]
    eliminated = [dict(h) for h in out["eliminated"]]
    # Promote ranked reserves to refill the live cap.
    while len(kept) < MAX_LIVE_HYPOTHESES and reserves:
        kept.append(reserves.pop(0))
    # Singles exhausted: pairs from the top of the ranking. The pool is
    # the remaining singles plus the just-ruled-out top singles (a pair
    # can explain what each single alone could not); pairs are flagged
    # "(two faults)" by generate_pair_hypotheses.
    pairs: list[dict] = []
    singles = [h for h in kept if "(two faults)" not in str(h.get("cause") or "")]
    has_pairs = any("(two faults)" in str(h.get("cause") or "") for h in kept)
    if not has_pairs and len(singles) <= 1:
        # Pool: remaining singles + reserves + every walk hypothesis
        # ruled out so far (walk-marked only — the table causes ruled
        # out before the walk are not walk material). Eliminated walk
        # hypotheses carry confidence 0.0, so the sort keeps the
        # surviving single first and the ruled-out ones in elimination
        # order after it (stable sort).
        walk_eliminated = [h.model_dump() for h in state.eliminated_hypotheses
                           if (h.rationale or "").startswith(_WALK_RATIONALE_PREFIX)]
        pool = sorted(singles + reserves + walk_eliminated + eliminated,
                      key=lambda h: -float(h.get("confidence") or 0.0))
        deduped: list[dict] = []
        seen_causes: set[str] = set()
        for h in pool:
            if h.get("cause") not in seen_causes:
                seen_causes.add(h.get("cause"))
                deduped.append(h)
        pairs = generate_pair_hypotheses(deduped)
        for pair in pairs:
            # Pairs are walk hypotheses too: carry the walk marker so the
            # walk stays live (and a fresh walk does not overwrite them)
            # once the singles are exhausted.
            pair["rationale"] = _WALK_RATIONALE_PREFIX + str(pair.get("rationale") or "")
            if len(kept) < MAX_LIVE_HYPOTHESES:
                kept.append(pair)
            else:
                reserves.append(pair)
    # Non-walk live hypotheses (if any) stay live, after the walk ones.
    others = [h.model_dump() for h in state.hypotheses
              if not (h.rationale or "").startswith(_WALK_RATIONALE_PREFIX)]
    state.hypotheses = [Hypothesis.model_validate(h) for h in kept + others]
    for h in eliminated:
        # Exact dedup, NOT the loose _texts_match: walk causes share
        # vocabulary ("not_connected" appears on the Welder, the cord
        # and the button), so a loose match would wrongly treat the
        # cord as already eliminated when the Welder was ruled out.
        if not any(str(h.get("cause") or "").strip().lower() == e.cause.strip().lower()
                   for e in state.eliminated_hypotheses):
            state.eliminated_hypotheses.append(Hypothesis.model_validate(h))
    # Next step: the top remaining walk hypothesis's discriminating
    # question, or the pair's rationale when a pair leads.
    if kept:
        top = kept[0]
        test_text = ""
        for candidate in ((entry or {}).get("kept") or (entry or {}).get("live") or []) + \
                ((entry or {}).get("reserves") or []):
            if candidate.get("cause") == top.get("cause"):
                test_text = candidate.get("discriminating_test") or ""
                break
        if "(two faults)" in str(top.get("cause") or ""):
            content = ("Singles ruled out; trying a pair from the top of the ranking: " +
                       str(top.get("cause")) + ". " + str(top.get("rationale") or ""))
        elif test_text:
            content = ("Upstream walk: " + test_text +
                       " — walk-generated question (archetype-shaped, not a manual-cited test). "
                       "Report what you observe.")
        else:
            content = ("Upstream walk: what is the single most informative observation on the "
                       "leading cause — " + str(top.get("cause")) + "?")
        state.current_step = NextStep(type="question", content=content,
                                      rationale="Walk advanced by the technician's test answer.")
    state.timeline.append({
        "turn": state.turns_taken, "kind": "upstream_walk_answer",
        "input": answer_text, "answer_mapped": mapped,
        "kept": kept, "eliminated": eliminated, "pairs": pairs,
        "reserves": reserves, "notes": out.get("notes") or [],
        "step_type": "upstream_walk_answer",
        "step": ("Upstream walk advanced: " + str(len(kept)) + " live, " +
                 str(len(eliminated)) + " ruled out" +
                 (f", {len(pairs)} pair(s) generated" if pairs else "")),
    })
    session.state = state.model_dump(mode="json")
    db.commit()
    return {"kept": kept, "eliminated": eliminated, "pairs": pairs,
            "reserves": reserves, "notes": out.get("notes") or []}


def _apply_early_tournament(db, session, state: DiagnosticState,
                            twin_client=None) -> dict | None:
    """Run the tournament EARLY (corrected loop order) and re-rank in place.

    Returns the tournament summary or None when no twin / no hypotheses.
    Never raises: a tournament failure must not break the diagnosis turn.
    With no approved twin the absence is recorded on the timeline
    (``twin_unavailable``) instead of silently skipping (fix-3) -- a
    diagnosis must never run with verification silently absent.

    Phase 3 twin-verifier extraction: the tournament runs over HTTP
    via the thin ``TwinVerifyClient`` (POST /v1/verify -> 202 + job,
    poll), never in-process — the ``run_tournament_for_state`` /
    ``load_twin_for_session`` in-process path is deleted. The re-rank
    itself (``rerank_with_tournament``) is diagnosis-owned and stays
    here. ``twin_client`` is injectable for tests; otherwise one is
    built from ``TWIN_VERIFIER_BASE_URL``.
    """
    try:
        if not state.hypotheses:
            return None
        repair_name = (state.current_step.content if state.current_step else "") or ""
        # Fix-3: when the conclusion gate already ran the tournament on
        # this turn's hypotheses (a demoted conclusion, or a
        # cannot-express ending), reuse its verdicts instead of running
        # the tournament a second time on the same set -- the re-rank's
        # confidence adjustments are not idempotent. The
        # discriminating-test selection below still runs on the re-ranked list.
        _held = next(
            (entry for entry in reversed(state.timeline)
             if entry.get("kind") in ("conclusion_held", "conclusion_unexpressible",
                                      "tournament")
             and entry.get("turn") == state.turns_taken
             and entry.get("results") is not None),
            None)
        if _held is not None:
            summary = {"available": True, "results": _held["results"],
                       "counts": _held["counts"],
                       "reranked": {"hypotheses": [h.model_dump() for h in state.hypotheses],
                                    "reranked": True, "counts": _held["counts"]}}
        else:
            client = twin_client or TwinVerifyClient(
                base_url=settings.twin_verifier_base_url)
            summary = client.verify_sync(
                [h.model_dump() for h in state.hypotheses],
                revision_id=session.revision_id, session_id=session.id,
                repair_name=repair_name)
            if summary["available"]:
                reranked = rerank_with_tournament(
                    [h.model_dump() for h in state.hypotheses],
                    summary["results"])
                state.hypotheses = [Hypothesis.model_validate(h)
                                    for h in reranked["hypotheses"]]
                summary = {**summary, "reranked": reranked}
            else:
                summary = {**summary, "reranked": {
                    "hypotheses": [h.model_dump() for h in state.hypotheses],
                    "reranked": False, "counts": summary["counts"]}}
        if not summary["available"]:
            state.timeline.append({
                "turn": state.turns_taken, "kind": "twin_unavailable",
                "input": repair_name or "hypothesis tournament (early)",
                "available": False, "reason": summary["reason"],
                "step_type": "twin_unavailable",
                "step": f"Twin verification unavailable: {summary['reason']}",
            })
            session.state = state.model_dump(mode="json")
            db.commit()
            return None
        results, counts = summary["results"], summary["counts"]
        reranked = summary["reranked"]
        # The discriminating test for the top remaining cause replaces the
        # LLM's question only when the signatures actually split the
        # remaining hypotheses -- otherwise the agent's question stands.
        # Fix-3: a test already asked-and-answered in an earlier turn is
        # excluded, so the same discriminating test is never asked twice.
        discriminating = None
        if state.current_step is not None and state.current_step.type.value in ("question", "action"):
            asked_tests = {
                (entry.get("discriminating_test") or {}).get("test")
                for entry in state.timeline
                if entry.get("kind") == "early_tournament"
                and (entry.get("turn") or 0) < state.turns_taken
                and (entry.get("discriminating_test") or {}).get("test")
            }
            discriminating = select_discriminating_test(
                _signatures_for_session(), [h.model_dump() for h in state.hypotheses],
                exclude_tests=asked_tests)
            # A cannot-express ending is the answer, not a question to
            # vary: never rewrite it into a discriminating-test question.
            _unexpressible = any(
                entry.get("kind") == "conclusion_unexpressible"
                and entry.get("turn") == state.turns_taken
                for entry in state.timeline)
            if discriminating is not None and not _unexpressible:
                test_name = discriminating["test"]
                state.current_step.content = (
                    "Discriminating test: " + test_name + " -- " +
                    discriminating["rationale"] + " What do you observe?")
                state.current_step.rationale = discriminating["rationale"]
        state.timeline.append({
            "turn": state.turns_taken, "kind": "early_tournament",
            "input": repair_name or "hypothesis tournament (early)",
            "available": True, "counts": counts,
            "cause": state.hypotheses[0].cause if state.hypotheses else None,
            "discriminating_test": discriminating,
            "results": [{"cause": r["cause"], "status": r["status"], "verdict": r["verdict"],
                         "reason": r["reason"],
                         "cannot_express": r.get("cannot_express", False)} for r in results],
            "step_type": "early_tournament",
            "step": "Hypothesis tournament (early): " + ", ".join(
                f"{k} {v}" for k, v in counts.items() if v),
        })
        session.state = state.model_dump(mode="json")
        db.commit()
        return {"available": True, "results": results, "counts": counts,
                "discriminating_test": discriminating, "reranked": reranked}
    except Exception as exc:  # noqa: BLE001 - best-effort by design
        _logger.warning("diagnostics: early tournament failed: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
        return None


def _to_view(session: DiagnosticSession) -> DiagnosticSessionView:
    return DiagnosticSessionView(
        session_id=session.id,
        product_id=session.product_id,
        revision_id=session.revision_id,
        status=session.status.value,
        state=DiagnosticState.model_validate(session.state),
    )


@router.post("/start", response_model=DiagnosticSessionView)
def start(payload: StartDiagnosticRequest, db: DBSession = Depends(get_session)):
    # Phase 3 diagnosis-agent extraction: product / revision existence
    # is validated against ingestion's HTTP API — no Product / Revision
    # DB reads here. An ingestion failure raises IngestionUnavailableError
    # (503, ingestion named).
    client = ingestion_client_mod.get_ingestion_client()
    revisions = client.list_revisions(payload.product_id)
    if revisions is None:
        raise HTTPException(404, "Product not found")
    if payload.revision_id:
        if client.get_revision_summary(payload.revision_id) is None:
            raise HTTPException(404, "Revision not found")
        if not any(r.get("id") == payload.revision_id for r in revisions):
            raise HTTPException(400, "revision_id must belong to product_id")

    session = start_session(db, payload.product_id, payload.revision_id, payload.symptom)
    return _to_view(session)


@router.get("/sessions")
def list_sessions(product_id: str | None = None, revision_id: str | None = None, db: DBSession = Depends(get_session)):
    """Phase 2: list past diagnostic sessions for a machine, most recent
    first, so the Diagnose view can restore one after navigating away.
    Registered before /{session_id} so "sessions" is never read as a
    session id. A light row per session -- the full state comes from
    GET /diagnose/{session_id} when the user restores one.
    """
    query = db.query(DiagnosticSession)
    if product_id:
        query = query.filter(DiagnosticSession.product_id == product_id)
    if revision_id:
        query = query.filter(DiagnosticSession.revision_id == revision_id)
    rows = query.order_by(DiagnosticSession.created_at.desc()).all()
    out = []
    for session in rows:
        state = session.state or {}
        out.append({
            "session_id": session.id,
            "product_id": session.product_id,
            "revision_id": session.revision_id,
            "status": session.status.value,
            "symptom": (state.get("symptoms") or [None])[0],
            "confidence": state.get("confidence", 0.0),
            "turns_taken": state.get("turns_taken", 0),
            "created_at": session.created_at.isoformat() if session.created_at else None,
        })
    return {"sessions": out}


@router.post("/{session_id}/respond", response_model=DiagnosticSessionView)
def respond(session_id: str, payload: RespondRequest, db: DBSession = Depends(get_session)):
    session = db.get(DiagnosticSession, session_id)
    if not session:
        raise HTTPException(404, "Diagnostic session not found")
    if session.status.value != "active":
        raise HTTPException(400, f"Session already {session.status.value}; start a new session to continue troubleshooting.")

    # Phase 4 follow-up: advance a live upstream walk with the
    # technician's answer BEFORE the LLM turn, so the model reasons
    # over the backtracked set instead of resurrecting ruled-out causes.
    _pre_state = DiagnosticState.model_validate(session.state)
    _apply_walk_answer(db, session, _pre_state, payload.input, payload.input_kind)
    db.refresh(session)
    run_turn(db, session, payload.input, input_kind=payload.input_kind)
    db.refresh(session)
    # Corrected loop order (Phase 4): the tournament runs early and
    # re-ranks the hypotheses the turn just produced, before any
    # grounded conclusion. Best-effort — no twin means no-op.
    _state = DiagnosticState.model_validate(session.state)
    if session.status.value == "active":
        _apply_early_tournament(db, session, _state)
        db.refresh(session)
        _state = DiagnosticState.model_validate(session.state)
        # Phase 4 follow-up: if this turn exhausted the table causes
        # (or none matched), the live loop walks upstream instead of
        # stopping at an unavailable-with-attempt. Only when a registry case
        # matches or the session has no hypotheses left — an unmatched
        # symptom with live LLM hypotheses stays the LLM's to reason
        # over until the technician explicitly asks for the walk.
        if (not _walk_is_live(_state)
                and (not _state.hypotheses or _matched_case(_state) is not None)
                and _is_off_table(_state)):
            _run_upstream_walk(db, session, _state)
            db.refresh(session)
            _state = DiagnosticState.model_validate(session.state)
    # Safety moved into run_turn (Phase 1 Step 5): the graph's safety
    # node runs first on every turn, including turn 1, and prepends the
    # lockout line there. Nothing is re-checked here.
    return _to_view(session)


@router.get("/{session_id}/context")
def get_diagnostic_context(session_id: str, db: DBSession = Depends(get_session)):
    """Return stable machine context for the Phase 9 diagnostic workstation.

    Phase 3 diagnosis-agent extraction: product / revision / documents
    / components / relationships / the canonical-model summary all come
    from ingestion's HTTP API (products list, revisions list, chunks
    endpoint for document metadata, /model for entities + relations) —
    no Product / Revision / Document / Component / ComponentRelationship
    / MachineKnowledgeModelSnapshot DB reads here. An ingestion failure
    raises IngestionUnavailableError (503, ingestion named).

    Shape notes (the HTTP sources carry slightly less than the old
    rows): documents are the distinct documents of the revision's
    chunks (a document with no chunks does not appear); components
    come from the canonical model's entities, so ``function`` /
    ``part_number`` are read from the entity properties when the model
    carries them, else null.
    """
    session = db.get(DiagnosticSession, session_id)
    if not session:
        raise HTTPException(404, "Diagnostic session not found")

    product = None
    revision = None
    docs: list[dict] = []
    components: list[dict] = []
    relationships: list[dict] = []
    snapshot = None
    if session.revision_id:
        client = ingestion_client_mod.get_ingestion_client()
        summary = client.get_revision_summary(session.revision_id)
        if summary is not None:
            product_id = summary.get("product_id")
            products = client.list_products()
            product_row = next((p for p in products if p.get("id") == product_id), None)
            if product_row is not None:
                product = {"id": product_row["id"],
                           "manufacturer": product_row.get("manufacturer"),
                           "family": product_row.get("family"),
                           "model": product_row.get("model")}
            revisions = client.list_revisions(product_id) or []
            revision_row = next((r for r in revisions if r.get("id") == session.revision_id), None)
            if revision_row is not None:
                revision = {"id": revision_row["id"], "label": revision_row.get("label"),
                            "parent_revision_id": revision_row.get("parent_revision_id")}

            seen_docs: dict[str, dict] = {}
            for chunk in client.fetch_all_chunks(session.revision_id) or []:
                doc_id = chunk.get("document_id")
                if doc_id and doc_id not in seen_docs:
                    seen_docs[doc_id] = {
                        "id": doc_id, "title": chunk.get("document_title"),
                        "doc_type": chunk.get("doc_type"),
                        "page_count": chunk.get("document_page_count")}
            docs = list(seen_docs.values())

            payload = client.get_model(session.revision_id)
            if payload is not None:
                model = payload.get("model") or {}
                components = [
                    {"id": e.get("id"), "name": e.get("name"),
                     "function": (e.get("properties") or {}).get("function"),
                     "part_number": (e.get("properties") or {}).get("part_number")}
                    for e in model.get("entities", [])]
                relationships = [
                    {"id": r.get("id"),
                     "from": r.get("subject_name") or r.get("subject_id"),
                     "to": r.get("object_name") or r.get("object_id"),
                     "type": r.get("relation_type"),
                     "description": r.get("description")}
                    for r in model.get("relations", [])]
                snapshot = {
                    "version": payload.get("version"),
                    "entity_count": len(model.get("entities", [])),
                    "relation_count": len(model.get("relations", [])),
                    "fact_count": sum(len(model.get(k, [])) for k in ("ports", "quantities", "states", "events", "constraints")),
                    "conflict_count": len(model.get("conflicts", [])),
                    "completeness": model.get("completeness", {}),
                }

    return {
        "product": product,
        "revision": revision,
        "documents": docs,
        "components": components,
        "relationships": relationships,
        "knowledge": snapshot,
    }


@router.get("/{session_id}", response_model=DiagnosticSessionView)
def get_session_view(session_id: str, db: DBSession = Depends(get_session)):
    session = db.get(DiagnosticSession, session_id)
    if not session:
        raise HTTPException(404, "Diagnostic session not found")
    return _to_view(session)


class OutcomeFeedbackRequest(BaseModel):
    outcome: str  # fixed | not_fixed | inconclusive
    confirmed_cause: str | None = None
    repair_performed: str | None = None
    technician_notes: str | None = None
    submit_correction_for_review: bool = False
    technician_name: str = "Diagnostic technician"


@router.post("/{session_id}/outcome")
def record_outcome(session_id: str, payload: OutcomeFeedbackRequest, db: DBSession = Depends(get_session)):
    """Record repair result. Optional corrections become draft expert knowledge for review."""
    if payload.outcome not in {"fixed", "not_fixed", "inconclusive"}:
        raise HTTPException(422, "outcome must be fixed, not_fixed, or inconclusive")
    session = db.get(DiagnosticSession, session_id)
    if not session:
        raise HTTPException(404, "Diagnostic session not found")
    existing = db.query(DiagnosticOutcome).filter_by(session_id=session_id).first()
    if existing:
        raise HTTPException(409, "Outcome already recorded for this session")
    state = session.state or {}
    outcome = DiagnosticOutcome(
        session_id=session.id, product_id=session.product_id, revision_id=session.revision_id,
        outcome=payload.outcome, confirmed_cause=payload.confirmed_cause,
        repair_performed=payload.repair_performed, technician_notes=payload.technician_notes,
        review_status="pending_review" if payload.submit_correction_for_review and (payload.confirmed_cause or payload.technician_notes) else "recorded",
    )
    db.add(outcome)
    db.flush()
    if outcome.review_status == "pending_review":
        expert = Expert(name=(payload.technician_name.strip() or "Diagnostic technician"), role="Repair outcome feedback")
        db.add(expert); db.flush()
        interview = ExpertInterview(
            expert_id=expert.id, product_id=session.product_id, revision_id=session.revision_id,
            topic=f"Outcome feedback for diagnostic session {session.id}",
            status=InterviewStatus.completed,
        )
        db.add(interview); db.flush()
        transcript = [
            ("interviewer", f"Diagnostic symptom: {'; '.join(state.get('symptoms', []))}"),
            ("expert", f"Reported outcome: {payload.outcome}. Repair performed: {payload.repair_performed or 'not specified'}. Confirmed cause/correction: {payload.confirmed_cause or 'not specified'}. Notes: {payload.technician_notes or 'none'}."),
        ]
        for idx, (speaker, content) in enumerate(transcript, 1):
            db.add(ExpertInterviewTurn(interview_id=interview.id, sequence=idx, speaker=speaker, content=content))
        version = KnowledgeVersion(interview_id=interview.id, revision_id=session.revision_id, version=1, status=KnowledgeStatus.draft)
        db.add(version); db.flush()
        db.add(ExpertKnowledge(
            knowledge_version_id=version.id, knowledge_type=KnowledgeType.heuristic,
            title=f"Field outcome: {payload.confirmed_cause or payload.outcome}",
            symptom="; ".join(state.get("symptoms", [])) or None,
            condition=payload.technician_notes, action=payload.repair_performed,
            failure_mode=payload.confirmed_cause, confidence=0.5,
            applicable_models=[], applicable_revisions=[], evidence_turn_ids=[],
            source_quote=f"Diagnostic session {session.id}; outcome={payload.outcome}",
            safety_notes=["Draft derived from technician feedback; verify before operational use."],
        ))
        outcome.knowledge_version_id = version.id
    db.commit()
    # Phase 5: the recorded field outcome flows end to end —
    # validation_history, VALIDATED-then-FAILED flag, learning-log
    # proposal, PM draft, downtime close. Best-effort: a Phase 5
    # failure is reported in the response, never silent, and never
    # rolls back the recorded outcome.
    phase5: dict = {}
    try:
        _twin_definition = None
        if session.revision_id:
            _twin = (db.query(DigitalTwin)
                     .filter_by(revision_id=session.revision_id, status=TwinStatus.active)
                     .order_by(DigitalTwin.created_at.desc()).first())
            if _twin is not None:
                _twin_definition = DigitalTwinDefinition.model_validate(_twin.definition)
        phase5 = apply_field_outcome(db, outcome, state, _twin_definition)
        _failure_mode = payload.confirmed_cause or "; ".join(state.get("symptoms", []))
        _pm = draft_pm_interval(db, session.product_id, _failure_mode,
                              evidence=f"outcome {payload.outcome} for session {session.id}")
        db.commit()
        phase5["pm_draft"] = {"id": _pm.id, "status": _pm.status,
                             "proposed_interval": _pm.proposed_interval,
                             "proposed_change": _pm.proposed_change}
        _downtime = close_downtime(db, session.id, cause=payload.confirmed_cause,
                                 confirmed=payload.outcome == "fixed",
                                 safety_flagged=False)
        if _downtime is not None:
            phase5["downtime"] = {"duration_minutes": _downtime.duration_minutes,
                                 "ended_at": _downtime.ended_at.isoformat() if _downtime.ended_at else None}
    except Exception as exc:  # noqa: BLE001 - outcome already recorded
        _logger.warning("diagnostics: phase 5 outcome flow failed: %s", exc)
        phase5 = {"error": f"Phase 5 outcome flow failed: {exc}"}
    return {"outcome_id": outcome.id, "session_id": session_id, "outcome": outcome.outcome,
            "review_status": outcome.review_status, "knowledge_version_id": outcome.knowledge_version_id,
            "phase5": phase5,
            "message": "Outcome recorded. Any generated knowledge remains draft until reviewed and approved."}


@router.get("/{session_id}/outcome")
def get_outcome(session_id: str, db: DBSession = Depends(get_session)):
    row = db.query(DiagnosticOutcome).filter_by(session_id=session_id).first()
    if not row:
        raise HTTPException(404, "No repair outcome recorded")
    return {"outcome_id": row.id, "session_id": row.session_id, "outcome": row.outcome,
            "confirmed_cause": row.confirmed_cause, "repair_performed": row.repair_performed,
            "technician_notes": row.technician_notes, "review_status": row.review_status,
            "knowledge_version_id": row.knowledge_version_id}


# --- Removed in the Phase 3 diagnosis-agent extraction ---------
# (contracts README §2.5): the following public endpoints do not
# exist under /v1/ after the split — a client calling any of them
# gets 404. verify-repair, verify-hypotheses, early-tournament
# and upstream-walk are internal turn steps / async twin calls
# (the tournament runs in the graph via the thin TwinVerifyClient
# and in respond() via _apply_early_tournament; the upstream walk
# runs in respond() via _run_upstream_walk). next-test and
# guided-checks are folded into the turn response, served by the
# one surviving test picker (backtracking.select_discriminating_test).
# The safety GET and fleet-patterns GET are deleted; context reads
# move to GET /diagnose/{session_id}/context above (gateway /
# knowledge). The frontend callers still referencing these paths
# (frontend/src/api/client.js: verifyRepair, verifyHypotheses,
# getMostInformativeTest, getGuidedPhysicalChecks) are noted in
# PHASE3_DIAGNOSIS_BUILD_NOTES.md — the frontend split is a later
# step and removes them there.
