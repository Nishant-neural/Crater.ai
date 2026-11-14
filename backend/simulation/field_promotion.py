"""Phase 3D — Layer 3 field-promotion scaffolding.

Design: ``phase3-design-v3-archetype-oracle.md`` §9 (the moat is the
validated loop + field data) and the third Amendments item 6 (twin
VALIDATED and field SUCCESSFUL are separate statuses).

Two rules, enforced in code:

1. **Nothing auto-promotes, not even after many validations.** A type is
   promoted only by an explicit human approval record. The promotion gate
   counts FIELD outcomes only, never simulator verdicts alone — a twin
   VALIDATED 50 times in simulation is not field evidence.
2. **Twin VALIDATED and field SUCCESSFUL are separate statuses.** The
   twin verdict (``VALIDATED`` / ``REJECTED`` / unavailable) lives on the
   experiment; the field outcome (``SUCCESSFUL`` / ``FAILED``) lives on
   the repair record, entered by a human (the technician). A twin
   VALIDATED followed by field FAILED is a learning signal, recorded,
   not hidden — it is exactly the contradiction that teaches the library.

``validation_history`` (``{validated_count, rejected_count,
field_instances}``) already exists on the Archetype model; this module
wires it: field outcomes are recorded against it, and the promotion gate
reads it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Field outcomes (entered by a human, never by the simulator)
# ---------------------------------------------------------------------------

FIELD_OUTCOMES = ("SUCCESSFUL", "FAILED")
TWIN_VERDICTS = ("VALIDATED", "REJECTED", "unavailable")


class FieldOutcome(BaseModel):
    """One field validation: a technician used the type in the field and
    reported the outcome. Entered by a human — the simulator never writes
    a field outcome."""

    archetype_id: str
    row_id: str | None = None
    machine_variant: str | None = None
    technician: str
    outcome: str  # "SUCCESSFUL" | "FAILED"
    discriminating_test: str | None = None
    test_outcome: str | None = None
    timestamp: str = Field(default_factory=lambda:
                           datetime.now(timezone.utc).isoformat())
    notes: str | None = None

    def model_post_init(self, __context: Any) -> None:
        if self.outcome not in FIELD_OUTCOMES:
            raise ValueError(f"field outcome must be one of {FIELD_OUTCOMES}, "
                             f"got {self.outcome!r}")


def record_field_outcome(archetype: Any, outcome: FieldOutcome) -> dict[str, Any]:
    """Append a field outcome to the archetype's validation_history and
    return the updated history. The history counts FIELD outcomes only —
    simulator verdicts are never written here."""
    history = dict(getattr(archetype, "validation_history", None) or {})
    history.setdefault("validated_count", 0)
    history.setdefault("rejected_count", 0)
    history.setdefault("field_instances", 0)
    if outcome.outcome == "SUCCESSFUL":
        history["validated_count"] = int(history["validated_count"]) + 1
    else:
        history["rejected_count"] = int(history["rejected_count"]) + 1
    history["field_instances"] = int(history["field_instances"]) + 1
    instances = history.setdefault("field_outcomes", [])
    instances.append(outcome.model_dump(mode="json"))
    archetype.validation_history = history
    return history


# ---------------------------------------------------------------------------
# The promotion gate (human approval required; field outcomes only)
# ---------------------------------------------------------------------------


class PromotionApproval(BaseModel):
    """The human approval that alone can promote a type. A list, so a
    second approver (an OEM expert) can be added without changing the
    schema. The approver is Nishant now."""

    approver: str
    timestamp: str = Field(default_factory=lambda:
                           datetime.now(timezone.utc).isoformat())
    reason: str
    # The field evidence the approver judged on (counts, not verdicts).
    field_successful: int = 0
    field_failed: int = 0


class PromotionDecision(BaseModel):
    archetype_id: str
    promoted: bool
    reason: str
    # The evidence the decision counted: FIELD outcomes only. The
    # simulator verdicts are deliberately absent — they are not evidence
    # for promotion.
    field_evidence: dict[str, int] = Field(default_factory=dict)
    approval: PromotionApproval | None = None


def evaluate_promotion(archetype: Any,
                       approval: PromotionApproval | None = None
                       ) -> PromotionDecision:
    """Decide whether to promote a type.

    NOTHING auto-promotes: with no approval record the answer is always
    "not promoted", no matter how many field validations the type has.
    With an approval record, the decision is "promoted" and the record
    carries the FIELD counts the approver judged on. Simulator verdicts
    (oracle pass rates, twin VALIDATED counts) are never counted here —
    promotion counts field outcomes only.
    """
    history = dict(getattr(archetype, "validation_history", None) or {})
    field_evidence = {
        "field_successful": int(history.get("validated_count", 0)),
        "field_failed": int(history.get("rejected_count", 0)),
        "field_instances": int(history.get("field_instances", 0)),
    }
    if approval is None:
        return PromotionDecision(
            archetype_id=archetype.archetype_id, promoted=False,
            reason=("No human approval record. Nothing auto-promotes, not "
                    f"even after {field_evidence['field_instances']} field "
                    f"validation(s) — promotion needs an explicit human "
                    f"approval."),
            field_evidence=field_evidence)
    return PromotionDecision(
        archetype_id=archetype.archetype_id, promoted=True,
        reason=(f"Promoted by human approval ({approval.approver}): "
                f"{approval.reason}. Field evidence counted: "
                f"{field_evidence['field_successful']} successful / "
                f"{field_evidence['field_failed']} failed over "
                f"{field_evidence['field_instances']} field instance(s). "
                f"Simulator verdicts were not counted."),
        field_evidence=field_evidence, approval=approval)


# ---------------------------------------------------------------------------
# Twin VALIDATED vs field SUCCESSFUL — separate statuses
# ---------------------------------------------------------------------------


class RepairRecord(BaseModel):
    """A repair record carries TWO statuses, kept separate:

    - ``twin_verdict``: what the twin said (VALIDATED / REJECTED /
      unavailable) — lives on the experiment.
    - ``field_outcome``: what happened in the field (SUCCESSFUL / FAILED)
      — lives on the repair record, entered by a human.

    A twin VALIDATED followed by field FAILED is a learning signal: the
    twin's model of that repair is wrong, and the contradiction is
    recorded here (``learning_signal``), not hidden.
    """

    repair_id: str
    case_id: str
    cause: str
    twin_verdict: str | None = None
    field_outcome: str | None = None
    field_technician: str | None = None
    field_timestamp: str | None = None
    learning_signal: str | None = None

    def record_twin_verdict(self, verdict: str) -> None:
        if verdict not in TWIN_VERDICTS:
            raise ValueError(f"twin verdict must be one of {TWIN_VERDICTS}, "
                             f"got {verdict!r}")
        self.twin_verdict = verdict
        self._refresh_learning_signal()

    def record_field_outcome(self, outcome: str, technician: str) -> None:
        if outcome not in FIELD_OUTCOMES:
            raise ValueError(f"field outcome must be one of {FIELD_OUTCOMES}, "
                             f"got {outcome!r}")
        self.field_outcome = outcome
        self.field_technician = technician
        self.field_timestamp = datetime.now(timezone.utc).isoformat()
        self._refresh_learning_signal()

    def _refresh_learning_signal(self) -> None:
        if self.twin_verdict == "VALIDATED" and self.field_outcome == "FAILED":
            self.learning_signal = (
                "Twin VALIDATED but field FAILED: the twin's model of this "
                "repair is wrong. This is a learning signal, not a "
                "contradiction to hide — the archetype rows for this repair "
                "need review against the field outcome.")
        else:
            self.learning_signal = None
