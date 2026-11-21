"""
The Phase 2 payoff endpoints: start a troubleshooting session on a symptom,
then keep feeding it observations/measurements until it concludes or
ends with the ranked best guesses.

Phase 4 corrected loop order (13 Nov 2026 correction, plan.md Phases 4-5
carried the old ordering): hypotheses ranked -> the tournament runs EARLY
and re-ranks them (POST /diagnose/{id}/early-tournament, also applied
best-effort by POST /diagnose/{id}/respond when an approved twin exists)
-> the agent asks the discriminating physical test for the top remaining
cause -> measurement -> grounded conclusion -> verify the chosen repair.
Verify / tournament are NOT gated behind the grounded conclusion any more.
Existing endpoints are unchanged in shape; verify-repair and the tournament
now also attach an "attempt" (ranked guesses + discriminating test + what is missing) whenever the twin cannot evaluate a cause, so a bare
"unavailable" is never the answer.

Phase 4 follow-up (14 Nov 2026): the upstream walk is wired into the live
loop. When a turn exhausts the table causes (or no table cause matches),
POST /diagnose/{id}/respond runs the walk (and POST
/diagnose/{id}/upstream-walk runs it on demand); the technician's next
answer advances the walk via apply_test_answer, and pairs come from the
top of the ranking once the singles are exhausted. The live cap
(MAX_LIVE_HYPOTHESES = 5) is enforced by split_live_and_reserves: the
top 5 are live, the rest are ranked reserves recorded on the timeline
and returned in the response, never dropped silently.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from backend.db.models import (
    DiagnosticSession, DigitalTwin, Product, Revision, Document, Component, ComponentRelationship,
    MachineKnowledgeModelSnapshot, DiagnosticOutcome, Expert, ExpertInterview, ExpertInterviewTurn,
    KnowledgeVersion, ExpertKnowledge, KnowledgeStatus, KnowledgeType, InterviewStatus, TwinStatus,
)
from backend.db.session import get_session
import json as _json
import logging as _logging
from pathlib import Path as _Path
_logger = _logging.getLogger(__name__)
from backend.diagnostics.agent import run_turn, start_session
from backend.diagnostics.twin_verification import (
    load_twin_for_session,
    run_tournament_for_state,
)
from backend.simulation.schema import DigitalTwinDefinition
from backend.simulation.tournament import (
    ISOLATED_TWIN_WARNING, UNAVAILABLE_LABEL, run_tournament, verify_single_cause,
)
from backend.diagnostics.downtime import close_downtime
from backend.diagnostics.fleet import sibling_search
from backend.diagnostics.learning import apply_field_outcome
from backend.diagnostics.pm import draft_pm_interval
from backend.simulation.repair_actions import (
    load_repair_actions, render_repair_action_safety_first)
from backend.diagnostics.safety import check_safety, safety_evidence_from_state
from backend.diagnostics.backtracking import (
    MAX_LIVE_HYPOTHESES, _texts_match, apply_test_answer,
    cite_hypotheses, generate_pair_hypotheses, present_hypotheses,
    select_discriminating_test,
    signatures_from_registry, signatures_from_vocabulary,
    split_live_and_reserves, unavailable_with_attempt, upstream_walk,
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


def _attempt_for_state(state: DiagnosticState, reason: str) -> dict:
    """Phase 4 (d): an unavailable answer that still carries an attempt."""
    guesses = [{"cause": h.cause, "confidence": h.confidence,
                "evidence": h.rationale, "source": "session_hypothesis"}
               for h in (state.hypotheses or [])]
    signatures = _signatures_for_session()
    guesses = cite_hypotheses(guesses, signatures)
    test = None
    if len(guesses) >= 2:
        test = select_discriminating_test(signatures, guesses)
    return unavailable_with_attempt(guesses, test, [reason], reason)


# --- Phase 4 follow-up (14 Nov 2026): the upstream walk, wired into the
# live loop ---------------------------------------------------------------
# The verifier caught that upstream_walk / apply_test_answer /
# generate_pair_hypotheses were called only by scripts/run_phase4_offtable.py
# and the unit tests — the live loop's off-table behaviour was
# _attempt_for_state alone. The helpers below wire the walk into the live
# loop (POST /diagnose/{id}/upstream-walk + POST /diagnose/{id}/respond):
# when a turn exhausts the table causes (or no table cause matches), the
# walk generates ranked hypotheses from topology + archetype states; the
# technician's next answer advances the walk via apply_test_answer; pairs
# come from the top of the ranking only once the singles are exhausted.
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
    """(edges, source) — the twin's own relations first, else the
    reviewed edge list. Never invented: an empty list means the walk
    cannot run and the caller returns unavailable-with-attempt."""
    if session.revision_id:
        try:
            components = db.query(Component).filter(
                Component.revision_id == session.revision_id).all()
            by_id = {c.id: c.name for c in components}
            rels = db.query(ComponentRelationship).filter(
                ComponentRelationship.from_component_id.in_(list(by_id) or [""]),
                ComponentRelationship.to_component_id.in_(list(by_id) or [""])).all()
            edges = [(by_id[r.from_component_id], by_id[r.to_component_id])
                     for r in rels
                     if r.from_component_id in by_id and r.to_component_id in by_id]
            if edges:
                return edges, "twin_relations"
        except Exception as exc:  # noqa: BLE001 - fall back to the edge list
            _logger.warning("diagnostics: twin relations unavailable for walk: %s", exc)
    try:
        edge_list = load_edge_list()
        return ([(e["source_component"], e["target_component"])
                 for e in edge_list.get("edges") or []], "reviewed_edge_list")
    except Exception as exc:  # noqa: BLE001 - no edges is an attempt, not a crash
        _logger.warning("diagnostics: reviewed edge list unavailable for walk: %s", exc)
        return [], "none"


def _walk_bindings(db, session):
    """(bindings, archetypes_by_code).

    Bindings are component name -> A-code ("A3"), from the approved
    twin's own metadata first (evidence-based binding, entity id ->
    archetype), then the canonical snapshot's library_type, then a
    unique exact-alias match only (a name that equals exactly one
    archetype alias). A component with no binding still walks — it gets
    the archetype-shaped generic fault — it is never guessed into an
    archetype.
    """
    bindings: dict[str, str] = {}
    loaded = _load_twin_for_session(db, session)
    if loaded is not None:
        definition, _twin_state, snapshot_model = loaded
        by_id = {c.id: c.name for c in definition.components}
        for entity_id, record in (definition.metadata.get("archetype_bindings") or {}).items():
            archetype_id = (record or {}).get("archetype_id")
            code = _ARCHETYPE_CODE_BY_NAME.get(str(archetype_id or ""))
            if code and entity_id in by_id:
                bindings[by_id[entity_id]] = code
        for entity in (snapshot_model.get("entities") or []):
            name = entity.get("name") or ""
            code = _LIBRARY_TYPE_TO_ARCHETYPE_CODE.get(str(entity.get("library_type") or ""))
            if code and name and name not in bindings:
                bindings[name] = code
    elif session.revision_id:
        # No twin: the canonical snapshot (if any) still carries library_type.
        try:
            snapshot_row = (db.query(MachineKnowledgeModelSnapshot)
                            .filter_by(revision_id=session.revision_id)
                            .order_by(MachineKnowledgeModelSnapshot.version.desc()).first())
            model = dict(snapshot_row.model or {}) if snapshot_row else {}
            for entity in (model.get("entities") or []):
                name = entity.get("name") or ""
                code = _LIBRARY_TYPE_TO_ARCHETYPE_CODE.get(str(entity.get("library_type") or ""))
                if code and name:
                    bindings.setdefault(name, code)
        except Exception as exc:  # noqa: BLE001 - alias fallback still applies
            _logger.warning("diagnostics: snapshot bindings unavailable for walk: %s", exc)
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


def _load_twin_for_session(db, session):
    """(definition, twin_state, snapshot_model) or None when no approved twin exists.

    Thin wrapper over the shared helper in backend.diagnostics.twin_verification
    (the agent's pre-turn run and conclusion gate use the same loader).
    """
    return load_twin_for_session(db, session)


def _apply_early_tournament(db, session, state: DiagnosticState) -> dict | None:
    """Run the tournament EARLY (corrected loop order) and re-rank in place.

    Returns the tournament summary or None when no twin / no hypotheses.
    Never raises: a tournament failure must not break the diagnosis turn.
    With no approved twin the absence is recorded on the timeline
    (``twin_unavailable``) instead of silently skipping (fix-3) -- a
    diagnosis must never run with verification silently absent.
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
             if entry.get("kind") in ("conclusion_held", "conclusion_unexpressible")
             and entry.get("turn") == state.turns_taken
             and entry.get("results") is not None),
            None)
        if _held is not None:
            summary = {"available": True, "results": _held["results"],
                       "counts": _held["counts"],
                       "reranked": {"hypotheses": [h.model_dump() for h in state.hypotheses],
                                    "reranked": True, "counts": _held["counts"]}}
        else:
            summary = run_tournament_for_state(db, session, state, repair_name)
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
    product = db.get(Product, payload.product_id)
    if not product:
        raise HTTPException(404, "Product not found")
    if payload.revision_id:
        revision = db.get(Revision, payload.revision_id)
        if not revision:
            raise HTTPException(404, "Revision not found")
        if revision.product_id != payload.product_id:
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
        # stopping at _attempt_for_state. Only when a registry case
        # matches or the session has no hypotheses left — an unmatched
        # symptom with live LLM hypotheses stays the LLM's to reason
        # over until the technician explicitly asks for the walk.
        if (not _walk_is_live(_state)
                and (not _state.hypotheses or _matched_case(_state) is not None)
                and _is_off_table(_state)):
            _run_upstream_walk(db, session, _state)
            db.refresh(session)
            _state = DiagnosticState.model_validate(session.state)
    # Steal item 4: a safety implication leads, before any
    # diagnosis. The lockout line is prepended to the next step
    # (never below it) and recorded on the timeline.
    _safety = check_safety(safety_evidence_from_state(_state))
    if _safety["safety_flagged"] and _state.current_step is not None:
        _content = _state.current_step.content or ""
        if not _content.startswith(_safety["lockout_line"]):
            _state.current_step.content = _safety["lockout_line"] + "\n\n" + _content
            _state.timeline.append({"turn": _state.turns_taken, "kind": "safety_check",
                                    "input": _safety["safety_reason"],
                                    "safety_flagged": True,
                                    "triggers": _safety["triggers"],
                                    "step_type": "safety_check",
                                    "step": _safety["lockout_line"]})
            session.state = _state.model_dump(mode="json")
            db.commit()
            db.refresh(session)
    return _to_view(session)


@router.get("/{session_id}/context")
def get_diagnostic_context(session_id: str, db: DBSession = Depends(get_session)):
    """Return stable machine context for the Phase 9 diagnostic workstation."""
    session = db.get(DiagnosticSession, session_id)
    if not session:
        raise HTTPException(404, "Diagnostic session not found")
    product = db.get(Product, session.product_id)
    revision = db.get(Revision, session.revision_id) if session.revision_id else None

    docs = []
    if revision:
        docs = [
            {"id": d.id, "title": d.title, "doc_type": d.doc_type.value, "page_count": d.page_count}
            for d in db.query(Document).filter(Document.revision_id == revision.id).all()
        ]
    components = []
    relationships = []
    if revision:
        components = [
            {"id": c.id, "name": c.name, "function": c.function, "part_number": c.part_number}
            for c in db.query(Component).filter(Component.revision_id == revision.id).all()
        ]
        comp_ids = {c["id"] for c in components}
        rels = db.query(ComponentRelationship).filter(
            ComponentRelationship.from_component_id.in_(comp_ids or [""]),
            ComponentRelationship.to_component_id.in_(comp_ids or [""]),
        ).all()
        by_id = {c["id"]: c["name"] for c in components}
        relationships = [
            {"id": r.id, "from": by_id.get(r.from_component_id, r.from_component_id),
             "to": by_id.get(r.to_component_id, r.to_component_id),
             "type": r.relation_type.value, "description": r.description}
            for r in rels
        ]

    snapshot = None
    if revision:
        row = db.query(MachineKnowledgeModelSnapshot).filter_by(
            revision_id=revision.id
        ).order_by(MachineKnowledgeModelSnapshot.version.desc()).first()
        if row:
            model = row.model or {}
            snapshot = {
                "version": row.version,
                "entity_count": len(model.get("entities", [])),
                "relation_count": len(model.get("relations", [])),
                "fact_count": sum(len(model.get(k, [])) for k in ("ports", "quantities", "states", "events", "constraints")),
                "conflict_count": len(model.get("conflicts", [])),
                "completeness": model.get("completeness", {}),
            }

    return {
        "product": (
            {"id": product.id, "manufacturer": product.manufacturer, "family": product.family, "model": product.model}
            if product else None
        ),
        "revision": (
            {"id": revision.id, "label": revision.label, "parent_revision_id": revision.parent_revision_id}
            if revision else None
        ),
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


@router.get("/{session_id}/next-test")
def most_informative_test(session_id: str, db: DBSession = Depends(get_session)):
    """Rank documented diagnostic tests by how well their cause scope separates live hypotheses."""
    session = db.get(DiagnosticSession, session_id)
    if not session:
        raise HTTPException(404, "Diagnostic session not found")
    state = session.state or {}
    hypotheses = state.get("hypotheses", [])
    if len(hypotheses) < 2:
        return {"available": False, "reason": "At least two competing hypotheses are needed to rank discriminating tests.", "tests": []}
    from backend.db.models import FailureMode
    modes = db.query(FailureMode).filter_by(revision_id=session.revision_id).all() if session.revision_id else []
    names = [str(h.get("cause", "")).casefold() for h in hypotheses[:5]]
    tests = []
    seen = set()
    for mode in modes:
        if not mode.diagnostic_test:
            continue
        key = mode.diagnostic_test.strip().casefold()
        if key in seen:
            continue
        seen.add(key)
        causes = [str(c).casefold() for c in (mode.possible_causes or [])]
        matched = [i for i, name in enumerate(names) if any(c and (c in name or name in c) for c in causes)]
        # Reward tests tied to some but not all hypotheses; do not pretend to calculate true information gain.
        separation = min(len(matched), len(names) - len(matched))
        coverage = len(matched) / len(names)
        score = separation + (0.25 if 0 < coverage < 1 else 0)
        tests.append({"test": mode.diagnostic_test, "expected_observation": mode.expected_observation,
                      "related_symptom": mode.symptom, "matched_hypotheses": [hypotheses[i].get("cause") for i in matched],
                      "score": round(score, 3), "evidence_chunk_id": mode.source_chunk_id,
                      "selection_basis": "heuristic overlap between documented possible causes and current hypotheses"})
    tests.sort(key=lambda x: (x["score"], len(x["matched_hypotheses"])), reverse=True)
    return {"available": bool(tests), "method": "heuristic_discrimination_not_probabilistic_information_gain",
            "tests": tests[:5], "note": "Only documented tests are shown; a low score or empty list means the stored knowledge cannot currently distinguish these hypotheses."}


@router.get("/{session_id}/guided-checks")
def guided_physical_checks(session_id: str, db: DBSession = Depends(get_session)):
    """Return physical checks grounded in documented tests and located schematic labels."""
    session = db.get(DiagnosticSession, session_id)
    if not session:
        raise HTTPException(404, "Diagnostic session not found")
    state = session.state or {}
    hypotheses = state.get("hypotheses", [])
    modes = db.query(FailureMode).filter_by(revision_id=session.revision_id).all() if session.revision_id else []
    docs = db.query(Document).filter_by(revision_id=session.revision_id).all() if session.revision_id else []
    doc_ids = [d.id for d in docs]
    from backend.db.models import SchematicNode
    nodes = db.query(SchematicNode).filter(SchematicNode.document_id.in_(doc_ids or [""])).all()
    labels = sorted({n.label for n in nodes if n.label})
    checks = []
    for mode in modes:
        if not mode.diagnostic_test:
            continue
        cause_text = " ".join(mode.possible_causes or []).casefold()
        matched = [h for h in hypotheses[:5] if any(w and w in cause_text for w in str(h.get("cause", "")).casefold().split() if len(w) > 3)]
        if not matched and hypotheses:
            continue
        checks.append({
            "instruction": mode.diagnostic_test,
            "target_hypotheses": [h.get("cause") for h in matched],
            "expected_observation": mode.expected_observation,
            "source_chunk_id": mode.source_chunk_id,
            "candidate_schematic_labels": [label for label in labels if label.casefold() in mode.diagnostic_test.casefold() or mode.diagnostic_test.casefold() in label.casefold()],
            "safety_note": "Follow the machine's approved isolation/LOTO procedure. Do not probe energized circuits unless the manufacturer's procedure and site rules explicitly permit it.",
            "limits": "No voltage, pin number, or pass/fail threshold is inferred unless explicitly present in the stored diagnostic test.",
        })
    return {"session_id": session_id, "checks": checks[:10],
            "message": "Instructions are limited to stored documented tests; schematic label matches are candidates, not verified pin-level wiring."}


class VerifyRepairRequest(BaseModel):
    cause: str | None = None
    repair_name: str | None = None


@router.post("/{session_id}/verify-repair")
def verify_repair(session_id: str, payload: VerifyRepairRequest, db: DBSession = Depends(get_session)):
    """Prove the leading repair in the approved twin before anyone touches the machine.

    Always returns HTTP 200 with ``available`` false when the twin cannot
    evaluate the repair (no approved twin, cause does not resolve to one twin
    component, no fault/recovery rule) -- unavailable is an answer, not an
    error. A verdict is only reported when the twin actually ran the loop.
    """
    session = db.get(DiagnosticSession, session_id)
    if not session:
        raise HTTPException(404, "Diagnostic session not found")
    state = DiagnosticState.model_validate(session.state)

    cause = (payload.cause or "").strip() or (state.hypotheses[0].cause if state.hypotheses else "")
    if not cause:
        raise HTTPException(409, "No hypothesis to verify: this session has no leading cause yet")
    repair_name = (payload.repair_name or "").strip() or (
        state.current_step.content if state.current_step else cause)

    def _unavailable(reason: str, fault_component_id: str | None = None) -> dict:
        state.timeline.append({
            "turn": state.turns_taken, "kind": "verify_repair", "input": repair_name,
            "cause": cause, "available": False, "verdict": None, "reason": reason,
            "fault_component_id": fault_component_id, "step_type": "verify_repair",
            "step": f"Repair verification unavailable: {reason}",
        })
        session.state = state.model_dump(mode="json")
        db.commit()
        return {"session_id": session.id, "available": False, "verdict": None,
                "reason": reason, "cause": cause, "repair_name": repair_name,
                "fault_component_id": fault_component_id}

    if not session.revision_id:
        return _unavailable("This session has no revision, so there is no twin to verify against")
    twin = (db.query(DigitalTwin)
            .filter_by(revision_id=session.revision_id, status=TwinStatus.active)
            .order_by(DigitalTwin.created_at.desc()).first())
    if not twin:
        return _unavailable("No approved twin exists for this revision yet; compile and approve one first")
    definition = DigitalTwinDefinition.model_validate(twin.definition)

    snapshot_row = (db.query(MachineKnowledgeModelSnapshot)
                    .filter_by(revision_id=session.revision_id)
                    .order_by(MachineKnowledgeModelSnapshot.version.desc()).first())
    snapshot_model = dict(snapshot_row.model or {}) if snapshot_row else {}

    result = verify_single_cause(
        definition, twin.state or {}, snapshot_model, state, cause, repair_name)
    # Phase 5 (d) + steal item 6: when the repair is a reviewed
    # RepairAction, present it safety-first — the safety line
    # BEFORE the repair steps.
    if result.get("repair_action_id"):
        _action = next((a for a in load_repair_actions()
                      if a.id == result["repair_action_id"]), None)
        if _action is not None:
            result["repair_action_safety_first"] = render_repair_action_safety_first(_action)
    if not result.get("available"):
        # Phase 4 (d): unavailable still carries ranked guesses,
        # the discriminating test and what is missing — never bare.
        result["attempt"] = _attempt_for_state(state, result.get("reason") or "the twin cannot evaluate this cause")

    summary = (
        f"Repair verification: {result['verdict']}" if result["available"]
        else f"Repair verification unavailable: {result['reason']}")
    entry = {
        "turn": state.turns_taken, "kind": "verify_repair", "input": repair_name,
        "cause": cause, "available": result["available"], "verdict": result["verdict"],
        "reason": result["reason"], "fault_component_id": result["fault_component_id"],
        "step_type": "verify_repair", "step": summary,
    }
    if "symptom_path" in result:
        entry["symptom_path"] = result["symptom_path"]
    if "rules_used" in result:
        entry["rules_used"] = result["rules_used"]
    state.timeline.append(entry)
    session.state = state.model_dump(mode="json")
    db.commit()

    return {"session_id": session.id, **result}


@router.post("/{session_id}/early-tournament")
def early_tournament(session_id: str, db: DBSession = Depends(get_session)):
    """Phase 4 corrected loop order: run the tournament EARLY, before any
    grounded conclusion, and re-rank the session's hypotheses by its
    verdicts (VALIDATED first, REJECTED demoted — never eliminated —
    unavailable unchanged). Also returns the discriminating test the
    signatures select for the top remaining cause. This is the same
    step POST /diagnose/{id}/respond applies best-effort after every
    turn when an approved twin exists; this endpoint exposes it
    explicitly so the UI can call it before the conclusion. The
    session state is updated (re-ranked hypotheses + timeline entry);
    nothing else about the session changes.
    """
    session = db.get(DiagnosticSession, session_id)
    if not session:
        raise HTTPException(404, "Diagnostic session not found")
    state = DiagnosticState.model_validate(session.state)
    if not state.hypotheses:
        raise HTTPException(409, "No hypotheses to verify: this session has none yet")
    summary = _apply_early_tournament(db, session, state)
    if summary is None:
        reason = ("No approved twin exists for this revision yet; compile and approve one first"
                  if session.revision_id else
                  "This session has no revision, so there is no twin to verify against")
        return {"session_id": session.id, "available": False, "verdict": None,
                "reason": reason, "results": [],
                "counts": {"validated": 0, "rejected": 0, "unavailable": 0},
                "unavailable_label": UNAVAILABLE_LABEL,
                "attempt": _attempt_for_state(state, reason),
                "hypotheses": [h.model_dump() for h in state.hypotheses],
                "discriminating_test": None}
    db.refresh(session)
    reranked = summary["reranked"]
    for row in summary["results"]:
        if row.get("status") == "unavailable":
            row["attempt"] = _attempt_for_state(state, row.get("reason") or "the twin cannot evaluate this cause")
    _signatures = _signatures_for_session()
    _fleet = None
    try:
        _symptom = (state.symptoms or [""])[0]
        if _symptom:
            _fleet = sibling_search(db, session.product_id, _symptom,
                                  revision_id=session.revision_id)
    except Exception as exc:  # noqa: BLE001 - fleet is advisory
        _logger.warning("diagnostics: fleet search failed: %s", exc)
    _cited = cite_hypotheses(reranked["hypotheses"], _signatures, _fleet,
                           summary["results"])
    _registry = None
    try:
        _registry = load_registry()
    except Exception:
        pass
    return {
        "session_id": session.id, "available": True,
        "results": summary["results"], "counts": summary["counts"],
        "unavailable_label": UNAVAILABLE_LABEL,
        "hypotheses": _cited,
        "presentation": present_hypotheses(_cited, _signatures, _registry),
        "discriminating_test": summary["discriminating_test"],
        "fleet": _fleet,
        "safety": check_safety(safety_evidence_from_state(state)),
        "warnings": [ISOLATED_TWIN_WARNING],
    }


class UpstreamWalkRequest(BaseModel):
    force: bool = False


@router.post("/{session_id}/upstream-walk")
def upstream_walk_endpoint(session_id: str, payload: UpstreamWalkRequest, db: DBSession = Depends(get_session)):
    """Phase 4 follow-up: run the upstream walk into the live loop.

    The off-table path, on demand: when the table causes are exhausted
    (or no table cause matches), generate ranked hypotheses from the
    reviewed topology + archetype states, capped at MAX_LIVE_HYPOTHESES
    live with the rest as ranked reserves. The same step runs
    automatically at the end of POST /diagnose/{id}/respond when a turn
    exhausts the table; this endpoint exposes it explicitly so the UI
    (or a test) can drive it without an LLM turn. Idempotent while a
    walk is live (returns the live walk); pass force=true to re-walk.
    A walk that cannot run returns available=False with an attempt —
    never a bare unavailable.
    """
    session = db.get(DiagnosticSession, session_id)
    if not session:
        raise HTTPException(404, "Diagnostic session not found")
    if session.status.value != "active":
        raise HTTPException(400, f"Session already {session.status.value}; start a new session to continue troubleshooting.")
    state = DiagnosticState.model_validate(session.state)
    result = _run_upstream_walk(db, session, state, force=payload.force)
    db.refresh(session)
    state = DiagnosticState.model_validate(session.state)
    if result is None:
        return {"session_id": session.id, "available": False,
                "reason": ("Table causes are not exhausted yet — the walk runs when "
                           "a turn exhausts the table causes or no table cause matches. "
                           "Pass force=true to run it anyway."),
                "symptom_component": None, "case_id": None,
                "live": [h.model_dump() for h in state.hypotheses],
                "reserves": [], "discriminating_test": None,
                "hypotheses": [h.model_dump() for h in state.hypotheses],
                "attempt": None}
    return {"session_id": session.id, **result}


@router.post("/{session_id}/verify-hypotheses")
def verify_hypotheses(session_id: str, db: DBSession = Depends(get_session)):
    """Rank the session's hypotheses by running each one through the
    verified-repair loop in the approved twin.

    Returns a ranked leaderboard (VALIDATED first, REJECTED second,
    unavailable last). An unavailable row means the twin cannot evaluate
    that cause -- it is not a rejection, and no hypothesis is eliminated
    or changed: the tournament only appends one timeline entry.
    """
    session = db.get(DiagnosticSession, session_id)
    if not session:
        raise HTTPException(404, "Diagnostic session not found")
    state = DiagnosticState.model_validate(session.state)
    if not state.hypotheses:
        raise HTTPException(409, "No hypotheses to verify: this session has none yet")

    repair_name = (state.current_step.content if state.current_step else "") or ""

    def _unavailable(reason: str) -> dict:
        state.timeline.append({
            "turn": state.turns_taken, "kind": "verify_hypotheses",
            "input": repair_name or "hypothesis tournament",
            "available": False, "verdict": None, "reason": reason,
            "step_type": "verify_hypotheses",
            "step": f"Hypothesis tournament unavailable: {reason}",
        })
        session.state = state.model_dump(mode="json")
        db.commit()
        return {"session_id": session.id, "available": False, "verdict": None,
                "reason": reason, "results": [],
                "counts": {"validated": 0, "rejected": 0, "unavailable": 0},
                "unavailable_label": UNAVAILABLE_LABEL}

    if not session.revision_id:
        return _unavailable("This session has no revision, so there is no twin to verify against")
    twin = (db.query(DigitalTwin)
            .filter_by(revision_id=session.revision_id, status=TwinStatus.active)
            .order_by(DigitalTwin.created_at.desc()).first())
    if not twin:
        return _unavailable("No approved twin exists for this revision yet; compile and approve one first")
    definition = DigitalTwinDefinition.model_validate(twin.definition)
    snapshot_row = (db.query(MachineKnowledgeModelSnapshot)
                    .filter_by(revision_id=session.revision_id)
                    .order_by(MachineKnowledgeModelSnapshot.version.desc()).first())
    snapshot_model = dict(snapshot_row.model or {}) if snapshot_row else {}

    results, counts = run_tournament(
        definition, twin.state or {}, snapshot_model, state, repair_name)
    for row in results:
        if row.get("status") == "unavailable":
            row["attempt"] = _attempt_for_state(state, row.get("reason") or "the twin cannot evaluate this cause")

    parts = []
    if counts["validated"]:
        parts.append(f"{counts['validated']} validated")
    if counts["rejected"]:
        parts.append(f"{counts['rejected']} rejected")
    if counts["unavailable"]:
        parts.append(f"{counts['unavailable']} unavailable (the twin cannot evaluate these causes)")
    summary = "Hypothesis tournament: " + (", ".join(parts) if parts else "no hypotheses evaluated")
    state.timeline.append({
        "turn": state.turns_taken, "kind": "verify_hypotheses",
        "input": repair_name or "hypothesis tournament",
        "available": True, "verdict": None, "reason": None,
        "counts": counts,
        "results": [{"cause": r["cause"], "status": r["status"], "verdict": r["verdict"],
                     "fault_component_id": r["fault_component_id"],
                     "reason": r["reason"]} for r in results],
        "step_type": "verify_hypotheses", "step": summary,
    })
    session.state = state.model_dump(mode="json")
    db.commit()

    return {
        "session_id": session.id, "available": True,
        "results": results, "counts": counts,
        "unavailable_label": UNAVAILABLE_LABEL,
        "warnings": [ISOLATED_TWIN_WARNING],
    }
@router.get("/{session_id}/safety")
def get_safety(session_id: str, db: DBSession = Depends(get_session)):
    """Phase 4 (steal item 4): the safety assessment for this session.

    The flag comes only from canonical-model safety facts / manual
    safety notes / the technician's own observations. The lockout
    line is returned first, flagged or not; the disclaimer travels
    with every diagnosis.
    """
    session = db.get(DiagnosticSession, session_id)
    if not session:
        raise HTTPException(404, "Diagnostic session not found")
    state = DiagnosticState.model_validate(session.state)
    return {"session_id": session.id, **check_safety(safety_evidence_from_state(state))}


@router.get("/{session_id}/fleet-patterns")
def get_fleet_patterns(session_id: str, db: DBSession = Depends(get_session)):
    """Phase 4 (steal item 1): sibling / fleet search for this session's symptom.

    Same-model siblings' failure modes and recorded outcomes, with the
    machine identity named; when this asset has no history, the
    fallback (manual + same-model siblings) is stated as such.
    """
    session = db.get(DiagnosticSession, session_id)
    if not session:
        raise HTTPException(404, "Diagnostic session not found")
    state = DiagnosticState.model_validate(session.state)
    symptom = (state.symptoms or [""])[0]
    if not symptom:
        return {"available": False, "reason": "This session has no symptom yet",
                "asset_records": [], "sibling_records": [], "fleet_pattern": None,
                "fallback_used": None, "counts": {}}
    return {"session_id": session.id,
            **sibling_search(db, session.product_id, symptom,
                             revision_id=session.revision_id)}


