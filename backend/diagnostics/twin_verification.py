"""Shared twin-verification helpers for the diagnosis loop (fix-3).

Single place that loads the approved twin and runs the early tournament
on a session's hypotheses. Both the agent (the pre-turn run and the
verify-before-conclusion gate in ``run_turn``) and the API routes (the
post-turn ``_apply_early_tournament``) call this module, so the verdict
logic lives in exactly one place.

No DB writes and no timeline side effects here: callers own persistence.
The tournament's verdict logic (VALIDATED / REJECTED / unavailable) and
the oracle are untouched — this module only loads the twin and calls
``run_tournament`` / ``rerank_with_tournament``.
"""
from __future__ import annotations

import logging

from backend.db.models import DigitalTwin, MachineKnowledgeModelSnapshot, TwinStatus
from backend.diagnostics.backtracking import _texts_match, rerank_with_tournament
from backend.diagnostics.schema import DiagnosticState, Hypothesis
from backend.simulation.schema import DigitalTwinDefinition
from backend.simulation.tournament import run_tournament

logger = logging.getLogger(__name__)


def load_twin_for_session(db, session):
    """(definition, twin_state, snapshot_model) or None when no approved twin exists."""
    if not session.revision_id:
        return None
    twin = (db.query(DigitalTwin)
            .filter_by(revision_id=session.revision_id, status=TwinStatus.active)
            .order_by(DigitalTwin.created_at.desc()).first())
    if not twin:
        return None
    definition = DigitalTwinDefinition.model_validate(twin.definition)
    snapshot_row = (db.query(MachineKnowledgeModelSnapshot)
                    .filter_by(revision_id=session.revision_id)
                    .order_by(MachineKnowledgeModelSnapshot.version.desc()).first())
    snapshot_model = dict(snapshot_row.model or {}) if snapshot_row else {}
    return definition, twin.state or {}, snapshot_model


def no_twin_reason(session) -> str:
    """Human-readable reason verification is unavailable: no approved twin."""
    if not session.revision_id:
        return ("this session has no revision, so there is no twin to verify "
                "against (no approved twin)")
    return "no approved twin for this revision yet; compile and approve one first"


def run_tournament_for_state(db, session, state: DiagnosticState,
                             repair_name: str = "", rerank: bool = True) -> dict:
    """Run the early tournament on the state's current hypotheses.

    Re-ranks ``state.hypotheses`` in place (VALIDATED first, REJECTED
    demoted, unavailable unchanged — the tournament's own rule) and
    returns ``{"available": True, "results", "counts", "reranked"}``.
    With ``rerank=False`` (the pre-turn run: the verdicts are for the
    prompt, not for mutating the ranking twice) the verdicts are
    reported without touching the hypotheses — the authoritative re-rank
    is the gate's / the post-turn run's, so one hypothesis set is never
    re-ranked twice in a turn (the confidence adjustments are not
    idempotent: VALIDATED +0.1, REJECTED x0.5 per application).
    When there is nothing to verify or no approved twin, returns
    ``{"available": False, "reason": ...}`` instead of silently doing
    nothing: the caller must surface the reason. Never raises: a
    tournament failure is reported the same way.
    """
    if not state.hypotheses:
        return {"available": False, "reason": "no hypotheses to verify"}
    loaded = load_twin_for_session(db, session)
    if loaded is None:
        return {"available": False, "reason": no_twin_reason(session)}
    try:
        definition, twin_state, snapshot_model = loaded
        results, counts = run_tournament(
            definition, twin_state, snapshot_model, state, repair_name or "")
        if rerank:
            reranked = rerank_with_tournament(
                [h.model_dump() for h in state.hypotheses], results)
            state.hypotheses = [Hypothesis.model_validate(h) for h in reranked["hypotheses"]]
        else:
            reranked = {"hypotheses": [h.model_dump() for h in state.hypotheses],
                        "reranked": False, "counts": counts}
        return {"available": True, "results": results, "counts": counts,
                "reranked": reranked}
    except Exception as exc:  # noqa: BLE001 - a tournament failure must not break the turn
        logger.warning("diagnostics: tournament failed: %s", exc)
        return {"available": False, "reason": f"tournament failed: {exc}"}


def verdict_for_cause(results: list[dict] | None, cause: str) -> str:
    """Tournament status ("validated" | "rejected" | "unavailable") for one cause."""
    for row in results or []:
        if cause and _texts_match(cause, row.get("cause", "")):
            return row.get("status") or "unavailable"
    return "unavailable"


def cannot_express_cause(results: list[dict] | None, cause: str) -> bool:
    """True only when the tournament row for one cause carries the
    cannot-express signal: no component in this twin can carry the cause
    (no matching component / no entity_ids, or the reviewed RepairAction
    for it references components this twin does not have, or the matched
    component has no simulation rule describing a fault in it). Mirrors
    ``verdict_for_cause``; the verdict logic itself is untouched -- a
    cause the twin evaluated and rejected, or one whose experiment ran
    but could not complete, reads False here."""
    for row in results or []:
        if cause and _texts_match(cause, row.get("cause", "")):
            return bool(row.get("cannot_express"))
    return False


def conclusion_cause(state: DiagnosticState, content: str) -> Hypothesis | None:
    """The hypothesis a conclusion step is about.

    The first hypothesis (in ranked order) whose cause the conclusion
    content mentions; the top hypothesis when the content names none of
    them (a repair-only conclusion such as "Adjust the Feed Tensioner"
    is read as being about the leading cause).
    """
    if not state.hypotheses:
        return None
    for hypothesis in state.hypotheses:
        if hypothesis.cause and _texts_match(content or "", hypothesis.cause):
            return hypothesis
    return state.hypotheses[0]


def render_verification_block(summary: dict, asked_causes=(),
                              asked_tests=()) -> str:
    """Prompt block: the twin verdicts the LLM must reason over before it
    picks its next step, plus what was already asked-and-answered (never
    to be repeated)."""
    lines = ["Twin verification (early tournament, run on the ranking carried into this turn):"]
    if summary.get("available"):
        for row in summary["results"]:
            lines.append(
                f"- {row.get('status', 'unavailable').upper()}: {row.get('cause')} "
                f"({row.get('reason') or row.get('verdict') or 'no reason recorded'})")
        lines.append(
            "Only conclude a repair whose cause is listed VALIDATED above. "
            "A REJECTED cause, or one the twin cannot evaluate, must not be concluded.")
    elif (summary.get("reason") or "") == "no hypotheses to verify":
        lines.append("- No hypotheses yet, so nothing has been verified. Do not conclude.")
    else:
        lines.append(
            f"- UNAVAILABLE: the twin could not verify any hypothesis "
            f"({summary.get('reason') or 'no reason recorded'}). "
            "Do not present any conclusion as verified; keep asking for the "
            "most informative observation.")
    if asked_causes:
        lines.append(
            "Already asked and answered in this session — do NOT ask again for "
            "these causes: " + "; ".join(sorted(asked_causes)) + ". "
            "If the leading cause was already checked and the fault persists, "
            "say what changed (or did not change) and move to the next untested cause.")
    if asked_tests:
        lines.append(
            "Discriminating tests already asked and answered — do NOT ask again: "
            + "; ".join(sorted(asked_tests)) + ".")
    return "\n".join(lines)
