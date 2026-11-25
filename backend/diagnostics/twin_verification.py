"""Thin async twin-verification client for the diagnosis loop.

Phase 1 Step 5 (twin audit p.7, p.11-12): the old 164-line in-process
module is reduced to this client. It calls ``POST /v1/verify`` (202 +
job) and polls ``GET /v1/verify/jobs/{job_id}`` for the verdicts, and
it **never imports ``backend.simulation.*``** — the tournament and
verification logic lives in ``backend/simulation/verify_service.py``,
behind the job contract, run by the twin-verifier service's Celery
worker. Phase 3 twin-verifier extraction removed the temporary
in-monolith ``LocalVerifyBackend``: the client is constructed with
the twin-verifier's base URL (``TWIN_VERIFIER_BASE_URL``) and nothing
else; with no base URL every call degrades to NOT_VERIFIED with the
reason recorded — verification is never silently absent.

Verdicts (audit p.12): VALIDATED / REJECTED / CANNOT_EXPRESS /
NOT_VERIFIED. Two rules hold on this side of the contract:

- **No result without ``rules_used`` may be VALIDATED.** A row that
  arrives VALIDATED with empty ``rules_used`` is demoted to
  NOT_VERIFIED here (the service enforces the same rule, so this is
  defence in depth, not the only enforcement).
- **A twin that is unavailable or times out degrades to NOT_VERIFIED,
  never a silent pass.** ``verify`` returns ``available: False`` with
  a reason and one NOT_VERIFIED row per hypothesis; the turn proceeds
  with ranking and questions, and a conclusion is never accepted on
  that basis.

The pure helpers the loop already used (``verdict_for_cause``,
``cannot_express_cause``, ``conclusion_cause``,
``render_verification_block``) stay here: they operate only on result
dicts, import nothing from simulation, and the agent, the routes and
the prompt-budget tests import them from this module.
"""
from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx

from backend.diagnostics.backtracking import _texts_match
from backend.diagnostics.schema import DiagnosticState, Hypothesis

logger = logging.getLogger(__name__)

# Twin-verifier contract version: the archetype library stays frozen.
TWIN_LIBRARY_VERSION = "3c-1.0.0"

_CONTRACT_TO_STATUS = {
    "VALIDATED": "validated",
    "REJECTED": "rejected",
    "CANNOT_EXPRESS": "unavailable",
    "NOT_VERIFIED": "unavailable",
}


def _not_verified_rows(hypotheses: list[dict], reason: str) -> list[dict]:
    return [{
        "cause": h.get("cause"), "status": "unavailable",
        "verdict": "NOT_VERIFIED", "reason": reason,
        "fault_component_id": h.get("fault_component_id"),
        "rules_used": [], "source_ids": [], "evidence_pages": [],
        "cannot_express": False,
        "twin_library_version": TWIN_LIBRARY_VERSION,
    } for h in hypotheses]


def _translate_job(job: dict, hypotheses: list[dict]) -> dict:
    """A /v1/verify job result into the loop's summary shape.

    ``{"available", "results", "counts", "reason?"}`` — the shape the
    old in-process ``run_tournament_for_state`` returned, so the
    conclusion gate and the verification block need no change. A row
    is ``available`` only when the job ran against a real twin; a job
    that succeeded with no twin reads available False with the reason
    the job carried. The rules_used rule is re-checked on every row.
    """
    results: list[dict] = []
    for row in job.get("results") or []:
        verdict = row.get("verdict") or "NOT_VERIFIED"
        rules_used = list(row.get("rules_used") or [])
        if verdict == "VALIDATED" and not rules_used:
            logger.warning(
                "diagnostics: demoting VALIDATED without rules_used for %r",
                row.get("cause"))
            verdict = "NOT_VERIFIED"
            row = {**row, "reason": ((row.get("reason") or "")
                   + " (validated without rules_used — demoted)").strip()}
        results.append({
            "cause": row.get("cause"),
            "status": _CONTRACT_TO_STATUS.get(verdict, "unavailable"),
            "verdict": verdict,
            "reason": row.get("reason") or "",
            "fault_component_id": row.get("fault_component_id"),
            "symptom_path": row.get("symptom_path"),
            "rules_used": rules_used,
            "source_ids": list(row.get("source_ids") or []),
            "evidence_pages": list(row.get("evidence_pages") or []),
            "cannot_express": bool(row.get("cannot_express")),
            "twin_library_version": row.get("twin_library_version") or TWIN_LIBRARY_VERSION,
            "verified_at": row.get("verified_at"),
        })
    counts = {"validated": 0, "rejected": 0, "unavailable": 0}
    for row in results:
        counts[row["status"]] += 1
    twin_missing = job.get("twin_id") is None
    if twin_missing:
        reason = (results[0].get("reason") if results
                  else "no approved twin for this revision yet")
        return {"available": False, "reason": reason, "results": results,
                "counts": counts}
    return {"available": True, "results": results, "counts": counts}


class TwinVerifyClient:
    """Thin async client for POST /v1/verify.

    Construct with ``base_url`` (the twin-verifier service, reached
    over HTTP) and optionally an httpx ``transport`` (tests inject a
    MockTransport here). With no base URL, every call degrades to
    NOT_VERIFIED with the reason recorded — verification is never
    silently absent.
    """

    def __init__(self, base_url: str | None = None, transport=None,
                 timeout_ms: int = 2000):
        self._base_url = (base_url or "").rstrip("/") or None
        self._transport = transport
        self._timeout_ms = timeout_ms

    def _payload(self, hypotheses, revision_id, session_id, twin_id, repair_name):
        return {
            "twin_id": twin_id, "revision_id": revision_id,
            "session_id": session_id,
            "hypotheses": [{
                "cause": h.get("cause"),
                "repair_name": repair_name or h.get("cause"),
                "fault_component_id": h.get("fault_component_id"),
                "failure_mode_name": h.get("failure_mode_name"),
            } for h in hypotheses],
            "timeout_ms": self._timeout_ms,
        }

    async def _verify_http(self, payload: dict) -> dict:
        timeout = httpx.Timeout(self._timeout_ms / 1000.0 + 1.0)
        async with httpx.AsyncClient(base_url=self._base_url,
                                     transport=self._transport,
                                     timeout=timeout) as client:
            created = await client.post("/v1/verify", json=payload)
            if created.status_code != 202:
                raise httpx.HTTPStatusError(
                    f"verify job rejected: HTTP {created.status_code}",
                    request=created.request, response=created)
            job_id = created.json()["job_id"]
            deadline = time.monotonic() + self._timeout_ms / 1000.0
            while True:
                resp = await client.get(f"/v1/verify/jobs/{job_id}")
                if resp.status_code == 200:
                    job = resp.json()
                    if job.get("status") in ("succeeded", "failed"):
                        return job
                if time.monotonic() >= deadline:
                    raise TimeoutError("twin verification timed out")
                await asyncio.sleep(0.05)

    async def verify(self, hypotheses: list[dict], revision_id: str | None = None,
                     session_id: str | None = None, twin_id: str | None = None,
                     repair_name: str = "") -> dict:
        """Verify hypotheses; never raises, never silently passes.

        Returns the loop's summary shape (see ``_translate_job``). On
        any failure — no transport configured, HTTP error, job failure,
        timeout — the summary reads available False with the reason and
        one NOT_VERIFIED row per hypothesis.
        """
        hyps = [dict(h) for h in hypotheses]
        if not hyps:
            return {"available": False, "reason": "no hypotheses to verify",
                    "results": [], "counts": {"validated": 0, "rejected": 0, "unavailable": 0}}
        payload = self._payload(hyps, revision_id, session_id, twin_id, repair_name)
        try:
            if self._base_url is not None:
                job = await self._verify_http(payload)
            else:
                raise RuntimeError("no twin verifier configured (no base_url)")
            if job.get("status") == "failed":
                raise RuntimeError(f"verify job failed: {job.get('reason') or job}")
            return _translate_job(job, hyps)
        except Exception as exc:  # noqa: BLE001 - twin-down must not break the turn
            logger.warning("diagnostics: twin verification unavailable: %s", exc)
            reason = f"twin verification unavailable: {exc}"
            return {"available": False, "reason": reason,
                    "results": _not_verified_rows(hyps, reason),
                    "counts": {"validated": 0, "rejected": 0, "unavailable": len(hyps)}}

    def verify_sync(self, hypotheses: list[dict], revision_id: str | None = None,
                    session_id: str | None = None, twin_id: str | None = None,
                    repair_name: str = "") -> dict:
        """Synchronous wrapper for the sync diagnosis loop.

        Runs the coroutine on the calling thread when no event loop is
        running (the normal sync-route case); when one is already
        running, the coroutine runs in a helper thread with its own
        loop, so the call never blocks an existing loop and never
        raises ``asyncio.run() cannot be called from a running event
        loop``.
        """
        kwargs = dict(revision_id=revision_id, session_id=session_id,
                      twin_id=twin_id, repair_name=repair_name)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.verify(hypotheses, **kwargs))
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(
                lambda: asyncio.run(self.verify(hypotheses, **kwargs))).result()


# --- Pure verdict helpers (unchanged behaviour, no simulation import) -----

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
    component has no simulation rule describing a fault in it)."""
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
