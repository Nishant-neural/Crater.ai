"""The diagnosis loop as a LangGraph graph (Phase 1 Step 5, audit Step 4).

The hand-rolled multi-turn loop state in ``agent.run_turn`` is replaced
by this graph. LangGraph owns the loop state — hypotheses, the explicit
``asked_causes`` / ``asked_tests`` sets, ``turns_taken`` — and the
state is checkpointed (Postgres in production, file-backed SQLite
under the dev default; see ``checkpoints.py``) under
``thread_id = "diagnostic-session:{session_id}"``, so a turn resumes
after a restart from the checkpoint, not from a re-read of the session
row alone. LangGraph owns state, never verdicts: the nodes below call
the same deterministic helpers the loop always used.

Nodes, in order (the safety node runs first on every turn, including
turn 1 — the audit's safety fix):

- ``safety`` — ``check_safety`` over the sourced evidence (symptoms /
  observations / measurements + canonical safety facts). The flag is
  recorded on the graph state; the lockout line is prepended to the
  turn's step in ``persist``. ``safety.py`` itself is verbatim.
- ``retrieve`` — one ``hybrid_retrieve`` call; evidence is whole rows.
- ``verify_pre`` — the twin client verifies the ranking carried into
  this turn (verdicts for the prompt only, no re-rank) and the
  verification block is rendered for the rank call.
- ``rank`` — call 1 of rank-then-ask (Step 4): 3–5 hypotheses,
  server-enforced, citations intersected, components grounded.
- ``discriminating`` — the single backtracking picker over the
  product's registry signatures (or the deterministic ranking
  fallback when no signature test splits the ranking).
- ``ask`` — call 2 of rank-then-ask: the next step from the ranking +
  the discriminating-test candidate.
- ``verify_post`` — the twin client verifies the NEW ranking, once,
  and the ranking is re-ranked by its verdicts (VALIDATED +0.1,
  REJECTED x0.5) exactly once per turn.
- ``confidence`` — confidence is COMPUTED from the tournament on the
  new ranking + the discriminating test's information gain, not asked:
  the ask call's ``overall_confidence`` is parsed (schema compat) and
  discarded.
- ``gate`` — verify-before-conclusion on the same verdicts (no second
  tournament run): a conclusion stands only on VALIDATED, demotes to
  a question otherwise, ends at once on CANNOT_EXPRESS.
- ``safeguards`` — escalate-to-question, the turn-bound ending, and
  the no-repeat rewrite.
- ``persist`` — syncs the result into the state, records the explicit
  asked sets and the timeline entries (including the safety check),
  and increments ``turns_taken``. The DB write itself stays in
  ``agent.run_turn`` (the session row remains the API-visible store).

The DB session, the ORM session row, the twin client and the registry
signatures travel in ``config["configurable"]`` — they are runtime
wiring, not checkpointed state. A degraded rank/ask turn sets
``result`` early and the later nodes pass it through (the kept
ranking still gets its post-turn verification + computed confidence
when a pre-turn summary already covers it).
"""
from __future__ import annotations

import logging

from langgraph.graph import END, START, StateGraph
from typing import TypedDict

from backend.diagnostics import agent as _agent
from backend.diagnostics.backtracking import rerank_with_tournament, _texts_match
from backend.diagnostics.checkpoints import default_checkpointer
from backend.diagnostics.safety import check_safety, safety_evidence_from_state
from backend.diagnostics.schema import DiagnosticState, EvidenceRef, Hypothesis, NextStep
from backend.diagnostics.twin_verification import render_verification_block

logger = logging.getLogger(__name__)


class TurnGraphState(TypedDict, total=False):
    """Checkpointed turn state. Everything here must be JSON-able: the
    DiagnosticState as a plain dict, evidence as dicts, summaries as
    dicts. The DB session, ORM row, twin client and signatures are NOT
    here — they travel in config["configurable"]."""

    state: dict
    input: str
    input_kind: str
    evidence: list
    verification_block: str
    ranked: dict | None
    discriminating_test: dict | None
    verification: dict | None
    pre_verification: dict | None
    result: dict | None
    safety: dict | None


def _obj(graph_state: TurnGraphState) -> DiagnosticState:
    return DiagnosticState.model_validate(graph_state["state"])


def _cfg(config) -> dict:
    return config["configurable"]


def _sync_state_from_result(obj: DiagnosticState, result: dict) -> DiagnosticState:
    """Carry a turn result into the state object (hypotheses,
    eliminations, confidence, discriminating test, current step)."""
    obj.hypotheses = [Hypothesis.model_validate(h) for h in result.get("hypotheses", [])]
    obj.eliminated_hypotheses = [Hypothesis.model_validate(h) for h in result.get("eliminated_hypotheses", [])]
    obj.confidence = float(result.get("overall_confidence", 0.0))
    obj.discriminating_test = result.get("discriminating_test")
    obj.current_step = NextStep.model_validate(result["next_step"])
    return obj


# --- Nodes -------------------------------------------------------------------

def safety_node(graph_state: TurnGraphState, config) -> dict:
    """Safety first, every turn, including turn 1 (audit Recheck p.3).

    The flag comes only from sourced evidence (see safety.py); the
    lockout prepend happens in ``persist_node`` once the step exists.
    """
    obj = _obj(graph_state)
    return {"safety": check_safety(safety_evidence_from_state(obj))}


def retrieve_node(graph_state: TurnGraphState, config) -> dict:
    """One retrieval call per turn: the top 5 whole evidence rows."""
    cfg = _cfg(config)
    session = cfg["session"]
    obj = _obj(graph_state)
    query = _agent._build_query(obj, graph_state["input"])
    retrieved = (
        # Via the agent module attribute (not a direct import) so the
        # tests' diag_agent.hybrid_retrieve stub still applies.
        _agent.hybrid_retrieve(query, product_id=session.product_id, revision_id=session.revision_id)
        if session.revision_id else []
    )
    evidence = []
    for r in retrieved:
        # The source document title rides on the chunk row ingestion's
        # API returned — no Document DB read (Phase 3 extraction).
        evidence.append(EvidenceRef(
            chunk_id=r.chunk_id, content=r.content,
            source_document=r.source_document or r.document_id,
            page_number=r.page_number,
        ))
    return {"evidence": [e.model_dump(mode="json") for e in evidence]}


def verify_pre_node(graph_state: TurnGraphState, config) -> dict:
    """Tournament on the ranking carried into this turn, via the thin
    twin client — verdicts for the prompt only, never a re-rank."""
    cfg = _cfg(config)
    obj = _obj(graph_state)
    client, session = cfg["twin_client"], cfg["session"]
    repair_name = (obj.current_step.content if obj.current_step else "") or ""
    summary = client.verify_sync(
        [h.model_dump() for h in obj.hypotheses],
        revision_id=session.revision_id, session_id=session.id,
        repair_name=repair_name)
    asked_causes, asked_tests = _agent._asked_causes_and_tests(obj)
    block = render_verification_block(summary, asked_causes=asked_causes,
                                      asked_tests=asked_tests)
    if summary.get("available"):
        obj.timeline.append({
            "turn": obj.turns_taken + 1, "kind": "early_tournament",
            "input": "hypothesis tournament (early, pre-turn)",
            "available": True, "counts": summary["counts"],
            "results": [{"cause": r["cause"], "status": r["status"], "verdict": r["verdict"],
                         "reason": r["reason"],
                         "cannot_express": r.get("cannot_express", False)} for r in summary["results"]],
            "step_type": "early_tournament",
            "step": "Hypothesis tournament (early, pre-turn): " + ", ".join(
                f"{k} {v}" for k, v in summary["counts"].items() if v),
        })
    return {"verification_block": block, "pre_verification": summary,
            "state": obj.model_dump(mode="json")}


def rank_node(graph_state: TurnGraphState, config) -> dict:
    """Call 1 of rank-then-ask. A rank failure degrades the turn
    (previous hypotheses kept, question asked) — the same degrade
    branches ``_run_llm_turn`` uses, so a single guess is never
    persisted as the ranking."""
    obj = _obj(graph_state)
    evidence = [EvidenceRef.model_validate(e) for e in graph_state.get("evidence") or []]
    block = graph_state.get("verification_block") or ""
    try:
        ranked = _agent._run_rank_call(obj, evidence, block)
    except _agent._NoProviderError as exc:
        ranked = None
        reason = (f"{exc} — no hypotheses were changed. Configure a provider, or report "
                  "the most informative observation and I will retry the reasoning "
                  "once one is available.")
        degraded = _agent._degraded_result(obj, reason)
        degraded["discriminating_test"] = (
            _agent._discriminating_test_for_ranking(
                [h.model_dump() for h in obj.hypotheses], _cfg(config)["signatures"], obj)
            if obj.hypotheses else None)
        return {"result": degraded}
    except Exception as exc:  # noqa: BLE001 - provider/transport failure degrades the turn
        logger.warning("diagnosis: rank call failed: %s", exc)
        ranked = None
        degraded = _agent._degraded_result(
            obj,
            f"The diagnostic model call failed ({exc}); no hypotheses were changed — please report the most informative observation and I will retry the reasoning.",
        )
        degraded["discriminating_test"] = (
            _agent._discriminating_test_for_ranking(
                [h.model_dump() for h in obj.hypotheses], _cfg(config)["signatures"], obj)
            if obj.hypotheses else None)
        return {"result": degraded}
    if ranked is None:
        degraded = _agent._degraded_result(
            obj,
            "The diagnostic model returned a ranking with fewer than 3 hypotheses (or an unparseable response) — no hypotheses were changed, so please report the most informative observation and I will update the ranking instead of guessing.",
        )
        degraded["discriminating_test"] = (
            _agent._discriminating_test_for_ranking(
                [h.model_dump() for h in obj.hypotheses], _cfg(config)["signatures"], obj)
            if obj.hypotheses else None)
        return {"result": degraded}
    return {"ranked": ranked}


def discriminating_node(graph_state: TurnGraphState, config) -> dict:
    """The discriminating test for this turn's ranking, from the single
    backtracking picker (or the deterministic ranking fallback). A
    degraded turn already carries its test from the rank node."""
    if graph_state.get("result") is not None or not graph_state.get("ranked"):
        return {}
    obj = _obj(graph_state)
    test = _agent._discriminating_test_for_ranking(
        graph_state["ranked"]["hypotheses"], _cfg(config)["signatures"], obj)
    return {"discriminating_test": test}


def ask_node(graph_state: TurnGraphState, config) -> dict:
    """Call 2 of rank-then-ask: the next step from the ranking + the
    discriminating-test candidate. An ask failure keeps the ranking
    with the default question."""
    if graph_state.get("result") is not None or not graph_state.get("ranked"):
        return {}
    obj = _obj(graph_state)
    ranked = graph_state["ranked"]
    test = graph_state.get("discriminating_test")
    block = graph_state.get("verification_block") or ""
    try:
        asked = _agent._run_ask_call(obj, ranked, test, block)
    except Exception as exc:  # noqa: BLE001 - ask failure keeps the ranking
        logger.warning("diagnosis: ask call failed: %s", exc)
        asked = None
    if asked is None:
        result = {
            "hypotheses": ranked["hypotheses"],
            "eliminated_hypotheses": ranked["eliminated_hypotheses"],
            "overall_confidence": max(
                [h["confidence"] for h in ranked["hypotheses"]], default=obj.confidence),
            "next_step": _agent._default_question_step(
                obj,
                "The diagnostic model returned an unparseable next step — the ranking was kept, so please report the most informative observation and I will update it instead of guessing."),
            "discriminating_test": test,
        }
        return {"result": _agent._attach_discriminating_test(obj, result, test)}
    result = {
        "hypotheses": ranked["hypotheses"],
        "eliminated_hypotheses": ranked["eliminated_hypotheses"],
        "overall_confidence": asked["overall_confidence"],
        "next_step": asked["next_step"],
        "discriminating_test": test,
    }
    return {"result": _agent._attach_discriminating_test(obj, result, test)}


def verify_post_node(graph_state: TurnGraphState, config) -> dict:
    """Tournament on the NEW ranking, via the twin client, exactly
    once — and the one authoritative re-rank of the turn. A degraded
    turn reuses the pre-turn summary: its kept ranking IS the ranking
    the pre-turn run verified, so no second call is spent on it."""
    result = graph_state.get("result")
    if result is None or not graph_state.get("ranked"):
        return {"verification": graph_state.get("pre_verification")}
    cfg = _cfg(config)
    obj = _obj(graph_state)
    client, session = cfg["twin_client"], cfg["session"]
    summary = client.verify_sync(
        result["hypotheses"],
        revision_id=session.revision_id, session_id=session.id,
        repair_name=(result.get("next_step") or {}).get("content") or "")
    if summary.get("available"):
        reranked = rerank_with_tournament(result["hypotheses"], summary["results"])
        result = {**result, "hypotheses": reranked["hypotheses"]}
        obj = _sync_state_from_result(obj, result)
        obj.timeline.append({
            "turn": obj.turns_taken + 1, "kind": "tournament",
            "input": "hypothesis tournament (post-rank)",
            "available": True, "counts": summary["counts"],
            "results": [{"cause": r["cause"], "status": r["status"], "verdict": r["verdict"],
                         "reason": r["reason"],
                         "cannot_express": r.get("cannot_express", False)} for r in summary["results"]],
            "step_type": "tournament",
            "step": "Hypothesis tournament: " + ", ".join(
                f"{k} {v}" for k, v in summary["counts"].items() if v),
        })
        return {"verification": summary, "result": result,
                "state": obj.model_dump(mode="json")}
    return {"verification": summary}


def confidence_node(graph_state: TurnGraphState, config) -> dict:
    """Confidence is computed, not asked (audit p.6): from the
    tournament on the new ranking + the discriminating test's
    information gain. The ask call's overall_confidence was parsed
    for schema compatibility and is discarded here."""
    result = graph_state.get("result")
    if result is None:
        return {}
    computed = _agent.compute_confidence(
        result.get("hypotheses") or [],
        graph_state.get("verification"),
        graph_state.get("discriminating_test"))
    return {"result": {**result, "overall_confidence": computed}}


def gate_node(graph_state: TurnGraphState, config) -> dict:
    """Verify-before-conclusion on the post-rank verdicts — no second
    tournament run. A conclusion stands only on VALIDATED; otherwise
    it demotes to a question, or ends at once on CANNOT_EXPRESS."""
    result = graph_state.get("result")
    if result is None:
        return {}
    obj = _obj(graph_state)
    obj = _sync_state_from_result(obj, result)
    result = _agent._apply_conclusion_gate(obj, result, graph_state.get("verification"))
    return {"result": result, "state": obj.model_dump(mode="json")}


def safeguards_node(graph_state: TurnGraphState, config) -> dict:
    """Deterministic guards: escalate-to-question, the turn-bound
    ending, and the no-repeat rewrite for already-checked causes."""
    result = graph_state.get("result")
    if result is None:
        return {}
    obj = _obj(graph_state)
    result = _agent._apply_forced_safeguards(obj, result)
    if not result.get("turn_bound_reached") and not result.get("cannot_express_ending"):
        result = _agent._apply_no_repeat_gate(obj, result)
    return {"result": result}


def persist_node(graph_state: TurnGraphState, config) -> dict:
    """Sync the result into the state, record the explicit asked sets
    and the timeline, apply the safety lockout line, and take the
    turn. The DB write stays in ``agent.run_turn``."""
    obj = _obj(graph_state)
    result = graph_state["result"]
    obj = _sync_state_from_result(obj, result)
    obj.turns_taken += 1

    # Explicit asked sets (audit schema p.5): this turn's step + test,
    # unioned with the timeline-derived sets (old sessions stored
    # before the sets existed keep their history this way).
    asked_causes, asked_tests = _agent._asked_causes_and_tests(obj)
    asked_causes |= set(obj.asked_causes or [])
    asked_tests |= set(obj.asked_tests or [])
    if obj.discriminating_test:
        test_text = obj.discriminating_test.get("test")
        if test_text:
            asked_tests.add(test_text)
    step_content = obj.current_step.content if obj.current_step else ""
    for h in list(obj.hypotheses) + list(obj.eliminated_hypotheses):
        if h.cause and _texts_match(step_content, h.cause):
            asked_causes.add(h.cause)
    obj.asked_causes = sorted(asked_causes)
    obj.asked_tests = sorted(asked_tests)

    ending_reason = None
    ending_step = None
    if result.get("turn_bound_reached"):
        ending_reason = "Turn limit reached without a twin-validated conclusion"
        ending_step = ("Diagnosis ended without a twin-validated conclusion: ranked best "
                       "guesses and what is missing were given instead.")
    elif result.get("cannot_express_ending"):
        ending_reason = (result.get("cannot_express_reason")
                         or "The twin cannot express the concluded cause")
        ending_step = ("Diagnosis ended: the twin cannot express the concluded cause; "
                       "ranked best guesses and what is missing were given instead.")
    if ending_reason:
        obj.timeline.append({
            "turn": obj.turns_taken,
            "kind": "diagnosis_unavailable",
            "input": graph_state["input"],
            "available": False,
            "verdict": None,
            "reason": ending_reason,
            "step_type": "diagnosis_unavailable",
            "step": ending_step,
        })

    # Safety (moved into run_turn from the routes respond handler):
    # a flagged turn leads with the lockout line, recorded on the
    # timeline. Turn 1 is covered — the safety node ran first.
    safety = graph_state.get("safety") or {}
    if safety.get("safety_flagged") and obj.current_step is not None:
        content = obj.current_step.content or ""
        if not content.startswith(safety["lockout_line"]):
            obj.current_step.content = safety["lockout_line"] + "\n\n" + content
            obj.timeline.append({
                "turn": obj.turns_taken, "kind": "safety_check",
                "input": safety["safety_reason"],
                "safety_flagged": True,
                "triggers": safety["triggers"],
                "step_type": "safety_check",
                "step": safety["lockout_line"]})

    obj.timeline.append({
        "turn": obj.turns_taken,
        "kind": graph_state.get("input_kind") or "observation",
        "input": graph_state["input"],
        "hypotheses": [
            {"cause": h.cause, "confidence": h.confidence, "status": "active"}
            for h in obj.hypotheses
        ],
        "discriminating_test": obj.discriminating_test,
        "step_type": obj.current_step.type.value,
        "step": obj.current_step.content,
    })
    return {"state": obj.model_dump(mode="json"), "result": result}


def build_diagnosis_graph(checkpointer=None):
    """Compile the diagnosis graph, checkpointed when a checkpointer
    is given (``None`` compiles without persistence — used only by
    callers that manage state themselves)."""
    builder = StateGraph(TurnGraphState)
    builder.add_node("safety", safety_node)
    builder.add_node("retrieve", retrieve_node)
    builder.add_node("verify_pre", verify_pre_node)
    builder.add_node("rank", rank_node)
    builder.add_node("discriminating", discriminating_node)
    builder.add_node("ask", ask_node)
    builder.add_node("verify_post", verify_post_node)
    builder.add_node("confidence", confidence_node)
    builder.add_node("gate", gate_node)
    builder.add_node("safeguards", safeguards_node)
    builder.add_node("persist", persist_node)
    builder.add_edge(START, "safety")
    builder.add_edge("safety", "retrieve")
    builder.add_edge("retrieve", "verify_pre")
    builder.add_edge("verify_pre", "rank")
    builder.add_edge("rank", "discriminating")
    builder.add_edge("discriminating", "ask")
    builder.add_edge("ask", "verify_post")
    builder.add_edge("verify_post", "confidence")
    builder.add_edge("confidence", "gate")
    builder.add_edge("gate", "safeguards")
    builder.add_edge("safeguards", "persist")
    builder.add_edge("persist", END)
    return builder.compile(checkpointer=checkpointer)


_GRAPHS: dict[int, object] = {}


def get_diagnosis_graph(checkpointer=None):
    """The process-cached compiled graph for ``checkpointer``
    (``None`` means the default checkpointer from ``checkpoints``)."""
    if checkpointer is None:
        checkpointer = default_checkpointer()
    key = id(checkpointer)
    if key not in _GRAPHS:
        _GRAPHS[key] = build_diagnosis_graph(checkpointer)
    return _GRAPHS[key]
