"""Hypothesis tournament: rank a session's hypotheses by running each one
through the verified-repair loop in the approved twin.

One row per hypothesis (capped at 5), ranked VALIDATED first, REJECTED
second, unavailable last. An unavailable row means the twin cannot
evaluate this cause -- it is not a rejection, and no hypothesis is ever
eliminated or mutated by the tournament: a REJECTED verdict is evidence
for the technician, not a state change.

No new LLM calls: every row is a deterministic twin run on an isolated
copy, reusing the single-cause verify-repair machinery exactly. No
extraction, compiler, engine or integration change.
"""
from __future__ import annotations

from typing import Any

from backend.diagnostics.schema import DiagnosticState
from backend.simulation.schema import DigitalTwinDefinition
from backend.simulation.verification import run_verified_repair

#: Label shown for rows the twin cannot evaluate. It is never a rejection.
UNAVAILABLE_LABEL = "the twin cannot evaluate this cause"

#: The tournament loops the session's leading hypotheses only.
MAX_HYPOTHESES = 5

ISOLATED_TWIN_WARNING = (
    "Verification runs in an isolated twin copy; it is not proof of physical safety."
)

# Rank order for the leaderboard: evaluated first, unevaluable last.
_RANK = {"validated": 0, "rejected": 1, "unavailable": 2}


def _norm_text(value: str | None) -> str:
    return " ".join(str(value or "").strip().lower().split())


def tournament_status(row: dict[str, Any]) -> str:
    """One of "validated" | "rejected" | "unavailable".

    Unavailable is decided only by the absence of a verdict: the twin
    could not evaluate the cause. A verdict of "rejected" means the twin
    DID run the repair and the symptom still did not recover.
    """
    if row.get("verdict") == "validated":
        return "validated"
    if row.get("verdict") == "rejected":
        return "rejected"
    return "unavailable"


def rank_tournament_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stable rank: VALIDATED first, REJECTED second, unavailable last.

    Within one status the session's hypothesis order is kept, so the
    leaderboard never reorders two rows the twin judged the same.
    """
    return sorted(rows, key=lambda r: _RANK[tournament_status(r)])


def verify_single_cause(
    definition: DigitalTwinDefinition,
    twin_state: dict[str, Any],
    snapshot_model: dict[str, Any],
    state: DiagnosticState,
    cause: str,
    repair_name: str,
) -> dict[str, Any]:
    """Run one cause through the verified-repair loop.

    This is the per-cause machinery of POST /diagnose/{id}/verify-repair,
    unchanged: the matched snapshot failure mode is the first source for
    the fault component, then a unique name match against twin components
    (ambiguity stays open, never guessed), then run_verified_repair with
    its pre-flight gates. No DB access and no timeline side effect, so
    both the single-cause endpoint and the tournament can call it.

    Returns the same result dict verify-repair returns (minus the
    session id): available / verdict / reason, fault component, symptom
    path, rules used, checks, traces and the baseline / fault / final
    states for visual-twin playback. When the twin cannot evaluate the
    cause, available is False, verdict is None and the reason says why.
    """
    # The matched snapshot failure mode is the first source for the fault
    # component; the snapshot also supplies the procedure citation.
    failure_mode: dict | None = None
    norm_cause = _norm_text(cause)
    for fm in snapshot_model.get("failure_modes") or []:
        haystacks = [_norm_text(fm.get("name"))] + [_norm_text(s) for s in fm.get("symptoms") or []]
        if any(h and (h in norm_cause or norm_cause in h) for h in haystacks):
            failure_mode = fm
            break

    # Resolve the cause to ONE twin component: failure-mode entity_ids first,
    # then a unique name match against twin components. Ambiguity stays open.
    fault_component_id: str | None = None
    component_ids = {c.id for c in definition.components}
    for entity_id in (failure_mode or {}).get("entity_ids") or []:
        if entity_id in component_ids:
            fault_component_id = str(entity_id)
            break
    if fault_component_id is None:
        matches = [c for c in definition.components
                   if _norm_text(c.name) and _norm_text(c.name) in norm_cause]
        if len(matches) == 1:
            fault_component_id = matches[0].id
        elif len(matches) == 0:
            return {"available": False, "verdict": None,
                    "reason": f"Could not match the cause {cause!r} to a component in this twin; "
                              "the twin cannot express this failure",
                    "cause": cause, "repair_name": repair_name,
                    "fault_component_id": None}
        else:
            return {"available": False, "verdict": None,
                    "reason": f"The cause {cause!r} matches {len(matches)} twin components "
                              f"({', '.join(c.name for c in matches[:5])}); ambiguous, so no guess was made",
                    "cause": cause, "repair_name": repair_name,
                    "fault_component_id": None}

    fault_component_name = next(
        (c.name for c in definition.components if c.id == fault_component_id), None)

    outcome = run_verified_repair(
        definition, twin_state or {},
        fault_component_id=fault_component_id,
        repair_name=repair_name,
        hypothesis=f"Repairing {cause} restores the machine to its healthy state.",
    )

    procedure: dict | None = None
    for proc in snapshot_model.get("procedures") or []:
        if _norm_text(proc.get("name")) and _norm_text(proc.get("name")) in _norm_text(repair_name):
            procedure = proc
            break
    if procedure is None and state.current_step and state.current_step.procedure_steps:
        procedure = {"name": repair_name, "steps": list(state.current_step.procedure_steps)}

    rules_used = list(outcome.fault_rules) + list(outcome.recovery_rules)
    result = outcome.result
    return {
        "available": outcome.available,
        "verdict": outcome.verdict, "reason": outcome.reason,
        "cause": cause, "repair_name": repair_name,
        "fault_component_id": fault_component_id,
        "fault_component_name": fault_component_name,
        "symptom_path": outcome.symptom_path,
        "symptom_expected": outcome.symptom_expected,
        "failure_mode": (failure_mode or {}).get("name"),
        "procedure": procedure,
        "rules_used": rules_used,
        "checks": result.checks if result else [],
        "fault_trace": result.fault_trace if result else [],
        "intervention_trace": result.intervention_trace if result else [],
        "baseline_state": result.baseline_state if result else None,
        "fault_state": result.fault_state if result else None,
        "final_state": result.final_state if result else None,
        "explanation": result.explanation if result else None,
        "warnings": (result.warnings if result else []) + [ISOLATED_TWIN_WARNING],
    }


def run_tournament(
    definition: DigitalTwinDefinition,
    twin_state: dict[str, Any],
    snapshot_model: dict[str, Any],
    state: DiagnosticState,
    repair_name: str,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Run the session's hypotheses through the verified-repair loop.

    Loops ``state.hypotheses[:MAX_HYPOTHESES]`` and returns the ranked
    leaderboard rows plus counts by status. Each row carries a "status"
    field ("validated" | "rejected" | "unavailable") alongside the
    single-cause result. The session state is only read, never changed.
    """
    rows: list[dict[str, Any]] = []
    for hypothesis in list(state.hypotheses or [])[:MAX_HYPOTHESES]:
        row = verify_single_cause(
            definition, twin_state, snapshot_model, state,
            hypothesis.cause, repair_name or hypothesis.cause)
        row["status"] = tournament_status(row)
        rows.append(row)
    ranked = rank_tournament_rows(rows)
    counts = {"validated": 0, "rejected": 0, "unavailable": 0}
    for row in ranked:
        counts[row["status"]] += 1
    return ranked, counts
