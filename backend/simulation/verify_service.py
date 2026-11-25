"""Twin verification jobs — the execution side of POST /v1/verify.

Twin-verifier audit p.11-12: verification is a job, not a call. The
diagnosis loop never calls the twin in-process any more; it talks to the
thin async client in ``backend/diagnostics/twin_verification.py``, which
speaks the ``POST /v1/verify`` (202 + job) contract. This module is the
twin-verifier service's execution of that contract, run by the Celery
worker (``backend/simulation/tasks.py``) and backed by the existing
tournament and verification logic — nothing about the verdict logic
changed, and the archetype library stays frozen at 3c-1.0.0.

Four verdicts (audit p.12), enforced here AND in the client:

- VALIDATED — the fault reproduced the symptom and the repair restored
  healthy. The result must carry the trace, the ``rules_used`` transition
  ids, source_ids and evidence pages. **No result without ``rules_used``
  may be VALIDATED** — a validated row with empty ``rules_used`` is
  demoted to NOT_VERIFIED here, so the rule holds even if a caller
  bypasses the client.
- REJECTED — the twin ran the repair and the symptom did not recover.
  Evidence against the cause; it never deletes the hypothesis.
- CANNOT_EXPRESS — no component can carry the cause, a matched component
  has no rule describing the fault, or a reviewed repair action
  references components the twin lacks. Terminal for that cause.
- NOT_VERIFIED — ambiguity, a family with no repair-action registry yet,
  an experiment that could not complete, or a twin that is unavailable
  or timed out. Explicitly not a rejection and not a block on ranking.

``load_twin_for_session`` / ``run_tournament_for_state`` lived here
in the monolith (Phase 1 Step 5) so the diagnosis routes could run
the tournament in-process; at the container split they are deleted —
diagnosis reaches the twin only over HTTP now (``POST /v1/verify``),
and the worker runs the tournament through ``execute_verify_job``
below. Nothing in diagnosis imports this module any more.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from backend.db.models import DigitalTwin, MachineKnowledgeModelSnapshot, TwinStatus
from backend.diagnostics.schema import DiagnosticState, Hypothesis
from backend.simulation.library_versions import CURRENT_LIBRARY_VERSION
from backend.simulation.schema import DigitalTwinDefinition
from backend.simulation.tournament import run_tournament

logger = logging.getLogger(__name__)


# --- The /v1/verify contract -------------------------------------------------

def map_verdict_row(row: dict) -> dict:
    """Map one tournament row to its contract verdict row.

    ``status`` is the tournament's lowercase status (validated /
    rejected / unavailable); ``verdict`` is the contract verdict
    (VALIDATED / REJECTED / CANNOT_EXPRESS / NOT_VERIFIED). The
    rules_used rule is enforced here: a validated row with no
    ``rules_used`` is demoted to NOT_VERIFIED with the reason recorded,
    so no VALIDATED result can ever leave this module without the
    transition ids that produced it. The reason is never empty
    (contracts §5.3): the verified-repair success path never sets one,
    so VALIDATED / REJECTED rows without a reason get the contract's
    plain sentence here.
    """
    status = row.get("status") or "unavailable"
    rules_used = list(row.get("rules_used") or [])
    reason = row.get("reason") or ""
    if status == "validated" and not rules_used:
        return {**_base_row(row), "verdict": "NOT_VERIFIED",
                "reason": (reason + " (validated without rules_used — demoted: "
                           "no result without rules_used may be VALIDATED)").strip()}
    if status == "validated":
        verdict = "VALIDATED"
        if not reason:
            reason = ("The fault reproduced the symptom and the repair "
                      "restored healthy.")
    elif status == "rejected":
        verdict = "REJECTED"
        if not reason:
            reason = ("The twin ran the repair and the symptom did not "
                      "recover.")
    elif row.get("cannot_express"):
        verdict = "CANNOT_EXPRESS"
        if not reason:
            reason = ("The twin cannot express this cause: no component, "
                      "rule, or repair action in this twin describes it.")
    else:
        verdict = "NOT_VERIFIED"
        if not reason:
            reason = "The twin could not verify this cause."
    return {**_base_row(row), "verdict": verdict, "reason": reason}


def _evidence_from_rules(rules_used: list) -> tuple[list, list]:
    """Source evidence for a verdict row, from its ``rules_used`` rule
    summaries (``_rule_summary`` in verification.py carries
    ``source_ids`` + ``evidence`` per transition). Rule entries may
    also be plain id strings (older callers / unit tests) — those
    carry no evidence and are skipped, never invented."""
    source_ids: list = []
    pages: list[int] = []
    for rule in rules_used:
        if not isinstance(rule, dict):
            continue
        for source_id in rule.get("source_ids") or []:
            if source_id not in source_ids:
                source_ids.append(source_id)
        for ev in rule.get("evidence") or []:
            if isinstance(ev, dict) and isinstance(ev.get("page"), int):
                pages.append(ev["page"])
    return source_ids, sorted(set(pages))


def _base_row(row: dict) -> dict:
    rules_used = list(row.get("rules_used") or [])
    source_ids, evidence_pages = _evidence_from_rules(rules_used)
    return {
        "cause": row.get("cause"),
        "status": row.get("status") or "unavailable",
        "fault_component_id": row.get("fault_component_id"),
        "symptom_path": row.get("symptom_path"),
        "rules_used": rules_used,
        "trace": row.get("fault_trace") or row.get("trace") or [],
        "cannot_express": bool(row.get("cannot_express")),
        # Source evidence (contracts §5.3): a VALIDATED result must
        # carry the transition ids (rules_used) AND the source evidence
        # behind them. The rule summaries carry both; a proof twin
        # whose transitions carry evidence pages but no source ids
        # still reports its pages honestly (empty source_ids, not an
        # invented one).
        "source_ids": source_ids,
        "evidence_pages": evidence_pages,
        "twin_library_version": CURRENT_LIBRARY_VERSION,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }


def execute_verify_job(db, payload: dict) -> dict:
    """Execute one verify job synchronously and return its job result.

    Payload: ``{twin_id?, revision_id, session_id?,
    hypotheses: [{cause, repair_name?, fault_component_id?,
    failure_mode_name?}], timeout_ms?}``. The twin is loaded from the
    DB (by ``twin_id`` when given, else the active twin for
    ``revision_id``); each hypothesis runs through the verified-repair
    loop via ``run_tournament``. With no twin, the job still succeeds —
    every hypothesis reads NOT_VERIFIED with the reason, never a
    silent pass and never an exception. Returns
    ``{job_id, status: "succeeded", results, counts}`` where counts is
    ``{validated, rejected, cannot_express, not_verified}``.
    """
    import uuid

    job_id = payload.get("idempotency_key") or str(uuid.uuid4())
    hypotheses = payload.get("hypotheses") or []
    twin_id = payload.get("twin_id")
    revision_id = payload.get("revision_id")
    twin = None
    if twin_id:
        twin = db.get(DigitalTwin, twin_id)
    elif revision_id:
        twin = (db.query(DigitalTwin)
                .filter_by(revision_id=revision_id, status=TwinStatus.active)
                .order_by(DigitalTwin.created_at.desc()).first())
    results: list[dict] = []
    if twin is None:
        reason = ("no approved twin for this revision yet; compile and approve one first"
                  if revision_id else
                  "this session has no revision, so there is no twin to verify against")
        for hyp in hypotheses:
            results.append({
                "cause": hyp.get("cause"), "status": "unavailable",
                "fault_component_id": hyp.get("fault_component_id"),
                "symptom_path": None, "rules_used": [], "trace": [],
                "cannot_express": False,
                "source_ids": [], "evidence_pages": [],
                "twin_library_version": CURRENT_LIBRARY_VERSION,
                "verified_at": datetime.now(timezone.utc).isoformat(),
                "verdict": "NOT_VERIFIED", "reason": reason,
            })
        return {"job_id": job_id, "status": "succeeded", "twin_id": None,
                "results": results,
                "counts": {"validated": 0, "rejected": 0, "cannot_express": 0,
                           "not_verified": len(results)}}
    definition = DigitalTwinDefinition.model_validate(twin.definition)
    snapshot_row = None
    if twin.revision_id:
        snapshot_row = (db.query(MachineKnowledgeModelSnapshot)
                        .filter_by(revision_id=twin.revision_id)
                        .order_by(MachineKnowledgeModelSnapshot.version.desc()).first())
    snapshot_model = dict(snapshot_row.model or {}) if snapshot_row else {}
    state = DiagnosticState(hypotheses=[
        Hypothesis(cause=str(h.get("cause") or ""), rationale="",
                   confidence=0.5)
        for h in hypotheses if h.get("cause")])
    repair_name = next((str(h.get("repair_name") or "")
                        for h in hypotheses if h.get("repair_name")), "")
    rows, _counts = run_tournament(
        definition, twin.state or {}, snapshot_model, state, repair_name)
    results = [map_verdict_row(row) for row in rows]
    counts = {"validated": 0, "rejected": 0, "cannot_express": 0, "not_verified": 0}
    for row in results:
        if row["verdict"] == "VALIDATED":
            counts["validated"] += 1
        elif row["verdict"] == "REJECTED":
            counts["rejected"] += 1
        elif row["verdict"] == "CANNOT_EXPRESS":
            counts["cannot_express"] += 1
        else:
            counts["not_verified"] += 1
    return {"job_id": job_id, "status": "succeeded", "twin_id": twin.id,
            "results": results, "counts": counts}

