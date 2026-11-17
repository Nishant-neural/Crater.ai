"""Phase 4 (steals, item 4) — safety implication leads, before any diagnosis.

From the breakdown-diagnosis skill: where a failure has a safety
implication, surface it first — before any diagnosis. Never let a
diagnostic suggestion precede a safety flag. Every diagnosis opens
with the lockout line, flagged or not:

    "Follow your plant's lockout procedure before anyone works on this
    machine. This is not a safety assessment."

Rules enforced here, in code:

- The flag comes ONLY from canonical-model safety facts / manual
  safety notes / the technician's own observations, each carried as
  an evidence item with a named source. A bare string with no source
  is not evidence and is ignored (never invented, never set on a
  feeling).
- Triggers (the skill's list): seized/locked rotating part;
  defeated/bypassed/failed guard or interlock; stored energy;
  hydraulic/pneumatic pressure to bleed; electrical fault; a fault
  cleared by restart now repeating.
- An uncertain stored-energy hazard is still a safety implication:
  hedging words ("maybe", "possibly", "uncertain") never cancel a
  stored-energy trigger.
- When no trigger is shown, the flag is false and the reason says
  "none of the safety triggers shown".

No LLM calls. No sensor data is used (Crater has no live signals).
"""
from __future__ import annotations

from typing import Any

LOCKOUT_LINE = ("Follow your plant's lockout procedure before anyone works "
                "on this machine. This is not a safety assessment.")
DISCLAIMER = ("This is not a safety assessment. Simulation is deterministic "
              "and virtual; it is not proof of physical safety or "
              "real-machine behavior.")

# Sources that may carry a safety implication. Anything else (a bare
# string, an LLM guess, a paraphrase with no source) is not evidence.
ALLOWED_SOURCES = {"manual_safety_note", "canonical_model_safety_fact",
                   "technician_observation", "diagnosis_evidence"}

_ROTATING_PARTS = ("spindle", "chuck", "bar feeder", "conveyor", "rotat",
                   "fan", "flywheel", "drum")
_LOCK_WORDS = ("seized", "locked", "jammed", "stuck", "frozen")
_GUARD_WORDS = ("guard", "interlock")
_GUARD_STATE_WORDS = ("defeated", "bypassed", "failed", "removed", "open",
                      "missing", "disabled")
_STORED_ENERGY_WORDS = ("spring", "counterweight", "raised", "under load",
                        "stored energy", "tensioned", "compressed")
_PRESSURE_WORDS = ("hydraulic", "pneumatic")
_PRESSURE_ACTION_WORDS = ("bleed", "vent", "release", "trapped", "pressure")
_ELECTRICAL_WORDS = ("trip", "tripped", "burnt smell", "burning smell",
                     "exposed wiring", "damaged wiring", "short circuit",
                     "electric shock", "shock")
_RESTART_WORDS = ("restart", "restarted", "ran off and restarted", "power cycled")
_REPEAT_WORDS = ("again", "repeat", "repeating", "still", "keeps", "keep")


def _contains(text: str, words: tuple[str, ...]) -> str | None:
    lowered = text.lower()
    for word in words:
        if word in lowered:
            return word
    return None


def _trigger_for(text: str) -> tuple[str, str] | None:
    """(trigger, matched word) for one evidence text, or None.

    Stored energy is checked first and hedging never cancels it: an
    uncertain stored-energy hazard is still a safety implication.
    """
    if _contains(text, _STORED_ENERGY_WORDS):
        return ("stored_energy", _contains(text, _STORED_ENERGY_WORDS) or "")
    if _contains(text, _GUARD_WORDS) and _contains(text, _GUARD_STATE_WORDS):
        return ("guard_or_interlock_defeated",
                f"{_contains(text, _GUARD_WORDS)} {_contains(text, _GUARD_STATE_WORDS)}")
    if _contains(text, _LOCK_WORDS) and _contains(text, _ROTATING_PARTS):
        return ("seized_or_locked_rotating_part",
                f"{_contains(text, _LOCK_WORDS)} {_contains(text, _ROTATING_PARTS)}")
    if _contains(text, _PRESSURE_WORDS) and _contains(text, _PRESSURE_ACTION_WORDS):
        return ("hydraulic_or_pneumatic_pressure_to_bleed",
                f"{_contains(text, _PRESSURE_WORDS)} {_contains(text, _PRESSURE_ACTION_WORDS)}")
    if _contains(text, _ELECTRICAL_WORDS):
        return ("electrical_fault", _contains(text, _ELECTRICAL_WORDS) or "")
    if _contains(text, _RESTART_WORDS) and _contains(text, _REPEAT_WORDS):
        return ("fault_cleared_by_restart_now_repeating",
                f"{_contains(text, _RESTART_WORDS)} {_contains(text, _REPEAT_WORDS)}")
    return None


def check_safety(evidence: list[dict[str, Any]]) -> dict[str, Any]:
    """Assess safety implications from sourced evidence items.

    Each item is {"text": ..., "source": ...} with source in
    ALLOWED_SOURCES. Items with any other source are ignored and
    reported as ignored — the flag is never set on unsourced text.
    """
    triggers: list[dict[str, Any]] = []
    ignored: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in evidence or []:
        text = str(item.get("text") or "")
        source = str(item.get("source") or "")
        if source not in ALLOWED_SOURCES or not text.strip():
            ignored.append({"text": text[:120], "source": source or "(none)",
                            "reason": "not sourced safety evidence — ignored"})
            continue
        found = _trigger_for(text)
        if found is None:
            continue
        trigger, matched = found
        if trigger in seen:
            continue
        seen.add(trigger)
        triggers.append({"trigger": trigger, "matched": matched,
                         "evidence": text, "source": source})
    if triggers:
        reasons = "; ".join(
            f"{t['trigger']} ({t['matched']!r}, via {t['source']})" for t in triggers)
        return {"safety_flagged": True,
                "safety_reason": f"Safety implication shown: {reasons}",
                "triggers": triggers,
                "lockout_line": LOCKOUT_LINE,
                "disclaimer": DISCLAIMER,
                "ignored_evidence": ignored}
    return {"safety_flagged": False,
            "safety_reason": "none of the safety triggers shown",
            "triggers": [],
            "lockout_line": LOCKOUT_LINE,
            "disclaimer": DISCLAIMER,
            "ignored_evidence": ignored}


def safety_evidence_from_state(state: Any) -> list[dict[str, Any]]:
    """Build the sourced evidence list for a diagnostic session state.

    Symptoms / observations / measurements are the technician's own
    words (source: technician_observation). The state's
    safety_constraints are canonical-model safety facts.
    """
    evidence: list[dict[str, Any]] = []
    for text in list(getattr(state, "symptoms", None) or []) + \
            list(getattr(state, "observations", None) or []) + \
            list(getattr(state, "measurements", None) or []):
        evidence.append({"text": str(text), "source": "technician_observation"})
    for text in list(getattr(state, "safety_constraints", None) or []):
        evidence.append({"text": str(text), "source": "canonical_model_safety_fact"})
    return evidence
