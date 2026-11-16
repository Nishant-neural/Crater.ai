"""Phase 4 — backtracking diagnostics (plan.md Phase 4).

Pure, deterministic logic — no LLM calls, no DB access, no engine
change. The diagnosis agent (LLM) still proposes hypotheses; this
module is the machinery that makes the loop *backtrack* instead of
stopping at the table:

1. **Upstream walk** — from a symptom component, walk the reviewed
   topology backwards (edges are the human-confirmed
   ``omnipro_edge_list.json`` list, or the twin's own relations) and
   generate hypotheses from the library behaviour of each upstream
   component (its archetype's failure modes, else a generic
   archetype-shaped fault). Hypotheses come from composition
   (topology + archetype states), never from lookup alone.
2. **Discriminating-test selection** — the test that splits the
   remaining fault signatures, by information gain (v3 §13), plus
   test cost and safety, which the signature alone does not capture.
   Signatures are the oracle vocabulary's observable + test-outcome
   vectors where available; where only the registry exists, a test's
   scope (which causes name it) is the split.
3. **Answer handling** — NO rules a hypothesis out; NOT SURE lowers
   confidence and picks a different test; "sometimes" (intermittent)
   keeps a hypothesis alive at low confidence instead of killing it
   (scenario §4).
4. **Two faults** — singles first; pairs only from the top of the
   ranking, once the singles are exhausted (scenario §4).
5. **Unavailable-with-attempt** — a bare "unavailable" is a miss: the
   attempt always carries ranked best guesses with confidence /
   evidence, the discriminating test, and what is missing.
6. **Corrected loop order (13 Nov 2026)** — the tournament runs
   EARLY and re-ranks hypotheses; verify is the single-cause
   confirmation of the chosen repair, not the first time the twin is
   consulted. ``rerank_with_tournament`` is the pure re-ranking step;
   the endpoint wiring lives in ``backend/api/routes/diagnostics.py``
   (``POST /diagnose/{id}/early-tournament`` + ``respond``).

Confidence numbers here are priors for ranking, not measured
probabilities: upstream candidates start at 0.5 minus 0.05 per hop
(distance from the symptom), pair hypotheses at the product of their
singles halved. They are labelled as priors wherever they surface.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any


def _texts_match(a: str, b: str) -> bool:
    """Loose cause-text match (same rule as the registry/tournament):
    normalised containment either way, or at least two distinctive
    shared tokens covering >= 0.5 of the shorter text. The registry
    paraphrases causes ("Unit not connected to outlet properly.")
    while the vocabulary quotes the manual ("Unit is not connected to
    outlet properly.") — exact matching silently drops those
    signatures. Generic domain words (wire, feed, motor, welder) do
    not count as distinctive: "Incorrect wire feed roller size" and
    "Insufficient wire feed pressure" share only those, and are
    different causes. A signature that matches no hypothesis is
    excluded from the split, never guessed into it."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na in nb or nb in na:
        return True
    ta = set(re.findall(r"[a-z0-9]+", na)) - _GENERIC_TOKENS
    tb = set(re.findall(r"[a-z0-9]+", nb)) - _GENERIC_TOKENS
    if not ta or not tb:
        return False
    shared = ta & tb
    return len(shared) >= 2 and len(shared) / min(len(ta), len(tb)) >= 0.5


# Words too generic in this domain to distinguish two causes.
_GENERIC_TOKENS = {"wire", "feed", "motor", "welder", "the", "is", "a", "an",
                   "of", "to", "too", "does", "it", "in", "on", "for", "with",
                   "and", "or", "are", "was", "be", "by", "at", "from", "as",
                   "this", "that", "has", "have", "its", "when"}

# ---------------------------------------------------------------------------
# Signatures
# ---------------------------------------------------------------------------

# Outcomes that carry no discriminating information: the test is not
# applicable to this cause, or the twin cannot model it. They never
# split a pair and never rule a hypothesis out.
NON_INFORMATIVE = {"unmodelled", "not_applicable", None, ""}


@dataclass
class FaultSignature:
    """One cause's signature: observable vector + test-outcome vector.

    ``source`` is where the vector came from — "oracle_vocabulary"
    (reviewed, per-cause vectors) or "registry" (test scope only: the
    test is named for this cause, its outcome is unknown). A registry
    signature can split hypotheses by scope; it cannot rule one out
    by outcome.
    """

    cause: str
    case_id: str = ""
    component: str = ""
    fault: dict[str, Any] | None = None
    observables: dict[str, Any] = field(default_factory=dict)
    tests: dict[str, Any] = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)
    source: str = "registry"


def signatures_from_vocabulary(vocab: dict[str, Any]) -> list[FaultSignature]:
    """Build signatures from the reviewed oracle vocabulary JSON.

    Only twin-expressible rows carry a fault; the other categories
    (settings / technique / cross-reference) have no fault injection
    and are returned with ``fault=None`` — they are still attempt
    material, never silently dropped.
    """
    signatures: list[FaultSignature] = []
    for row in vocab.get("rows") or []:
        fault = row.get("fault") or {}
        signatures.append(FaultSignature(
            cause=str(row.get("cause") or ""),
            case_id=str(row.get("case_id") or ""),
            component=str(fault.get("component") or ""),
            fault=dict(fault) if fault else None,
            observables=dict(row.get("observables") or {}),
            tests=dict(row.get("tests") or {}),
            evidence=[str(row.get("basis") or "")] if row.get("basis") else [],
            source="oracle_vocabulary",
        ))
    return signatures


def signatures_from_registry(registry: Any) -> list[FaultSignature]:
    """Build scope-only signatures from the repair-case registry.

    Each cause's ``test`` names the discriminating test for that cause;
    the outcome is unknown, so the test value is recorded as the scope
    marker "named" — it splits "causes this test is named for" from
    "causes it is not named for", nothing more.
    """
    signatures: list[FaultSignature] = []
    cases = getattr(registry, "cases", None) or []
    for case in cases:
        for cause in case.causes or []:
            tests: dict[str, Any] = {}
            if cause.test:
                tests[_test_key(cause.test)] = "named"
            signatures.append(FaultSignature(
                cause=cause.cause,
                case_id=case.id,
                component=(cause.components[0] if cause.components else ""),
                fault=None,
                observables={},
                tests=tests,
                evidence=[f"{case.id} p{case.table_page}"],
                source="registry",
            ))
    return signatures


def _test_key(text: str) -> str:
    return " ".join(str(text or "").strip().lower().split())


# ---------------------------------------------------------------------------
# Upstream walk
# ---------------------------------------------------------------------------

# Generic fault a component can have, by archetype, when the archetype
# carries no failure mode of its own for this walk. These are
# archetype-shaped (a switch opens, a source loses power), not invented
# behaviour: the twin's propagation rows decide whether the fault
# actually explains the symptom — the walk proposes, the tournament
# disposes.
_GENERIC_FAULT_BY_ARCHETYPE: dict[str, str] = {
    "A1": "has no power",
    "A2": "is stuck open",
    "A3": "is burnt out / produces no motion",
    "A4": "is jammed",
    "A5": "reads incorrectly",
    "A6": "produces no output",
    "A7": "produces no output",
    "A8": "is open / broken",
    "A9": "is empty",
    "A10": "is stuck closed",
    "A11": "shows nothing",
}

MAX_LIVE_HYPOTHESES = 5
MAX_WALK_DEPTH = 6
# The walk's candidate pool is the full upstream chain, not the live
# cap: OMNIPRO's power chain from the feeder is 5 hops
# (feeder <- motor <- trigger <- welder <- button <- cord), so depth 6
# covers it with one hop of margin. The session's LIVE cap stays 5
# (plan §1 / tournament MAX_HYPOTHESES): the top 5 walk candidates by
# distance become live, the rest are ranked reserves carried in the
# attempt — never silently dropped. One hypothesis per component in
# the first pass (see upstream_walk): spending all slots on one
# component's failure modes is what stranded the trigger, button and
# cord in the first Phase 4 run.
MAX_WALK_CANDIDATES = 6

# Generic discriminating question per archetype, for a walk-generated
# hypothesis (scenario §3: "Does the motor hum or run at all when you
# pull the trigger?"). These are walk-generated questions, not
# manual-cited tests — they are labelled as such wherever they surface,
# and the oracle-vocabulary test wins whenever a signature exists.
_GENERIC_TEST_BY_ARCHETYPE: dict[str, str] = {
    "A1": "Check power at the unit (is the outlet live and the breaker not tripped?)",
    "A2": "Does the motor run at all when the trigger is pulled?",
    "A3": "Does the motor hum or run when power is applied?",
    "A4": "Is the mechanism jammed, or turning freely with no load?",
    "A5": "Does the sensor reading change when the condition changes?",
    "A6": "Does the controller produce any output when commanded?",
    "A7": "Does the load show any output at all?",
    "A8": "Is there continuity through the cable / liner path?",
    "A9": "Is the spool / bottle loaded and feeding?",
    "A10": "Does gas flow when the valve opens?",
    "A11": "Does the display / interface show anything at all?",
}


def split_live_and_reserves(
    hypotheses: list[Any],
    max_live: int = MAX_LIVE_HYPOTHESES,
) -> tuple[list[Any], list[Any]]:
    """Enforce the live cap (plan §1: ≤ 5 live hypotheses).

    The walk's candidate pool is ``MAX_WALK_CANDIDATES`` (6) by design,
    so a full walk always overflows the live cap by one: the top
    ``max_live`` hypotheses (ranked order, as passed in) become live
    and the rest are returned as ranked reserves — never dropped
    silently. Callers persist the reserves (session timeline / API
    response) and promote from them when a live hypothesis is ruled
    out. This is the one place the cap is enforced; ``upstream_walk``
    itself stays capped at the pool size so the harness keeps measuring
    the full chain.
    """
    live = list(hypotheses[:max_live])
    reserves = list(hypotheses[max_live:])
    return live, reserves


@dataclass
class WalkHypothesis:
    cause: str
    component: str
    archetype_id: str | None
    confidence: float  # prior for ranking, not a measured probability
    evidence: str
    chain: list[str] = field(default_factory=list)
    source: str = "upstream_walk"
    pair: bool = False
    # Walk-generated discriminating question (archetype-shaped, not a
    # manual-cited test). The oracle-vocabulary test wins when one exists.
    discriminating_test: str = ""


def upstream_walk(
    symptom_component: str,
    edges: list[tuple[str, str]],
    bindings: dict[str, str | None] | None = None,
    archetypes: dict[str, Any] | None = None,
    max_hypotheses: int = MAX_WALK_CANDIDATES,
    max_depth: int = MAX_WALK_DEPTH,
    exclude: set[str] | None = None,
) -> list[WalkHypothesis]:
    """Walk topology backwards from the symptom component.

    ``edges`` are (source, target) component-name pairs in the
    direction energy/material flows. The walk follows them backwards,
    so the symptom's producers come first. Per component it proposes
    ONE hypothesis first (the archetype's preferred loss-of-input
    failure mode — see ``_preferred_mode``), then round-robins the
    remaining modes across components until the cap. One-per-component
    first is what keeps the walk broad: the first Phase 4 run spent
    all 5 slots on the motor's 4 modes + the spool and never reached
    the trigger, button or cord. Hypotheses are ranked by distance
    (nearer first) and capped at ``max_hypotheses`` — the walk follows
    topology only and never searches the whole model.
    """
    bindings = bindings or {}
    archetypes = archetypes or {}
    # ``exclude``: components downstream of the symptom (effects, not
    # causes). On OMNIPRO the arc feeds back into the welder
    # (arc -> welder edge), so a plain reverse walk reaches the Welding
    # Arc as a "producer" of the welder — but the arc is downstream of
    # the feeder in the case's own required chain (feeder -> liner ->
    # gun -> tip -> arc), so it cannot cause the wire not to feed.
    # Callers pass the required chain's downstream components here;
    # without it the walk still works, it just also names the loop.
    excluded = {_norm(name) for name in (exclude or set())}
    # Reverse adjacency: target -> [sources], deterministic order.
    upstream: dict[str, list[str]] = {}
    for source, target in edges:
        upstream.setdefault(_norm(target), []).append(source)
    for targets in upstream.values():
        targets.sort(key=_norm)

    seen: set[str] = {_norm(symptom_component)}
    frontier: list[tuple[str, int, list[str]]] = [(symptom_component, 0, [symptom_component])]
    per_component: list[list[WalkHypothesis]] = []
    while frontier:
        current, depth, chain = frontier.pop(0)
        if depth >= max_depth:
            continue
        for producer in upstream.get(_norm(current), []):
            if _norm(producer) in seen or _norm(producer) in excluded:
                continue
            seen.add(_norm(producer))
            new_chain = chain + [producer]
            per_component.append(_hypotheses_for_component(
                producer, bindings.get(producer) or bindings.get(_norm(producer)),
                archetypes, depth + 1, new_chain))
            frontier.append((producer, depth + 1, new_chain))
    # Round-robin: one hypothesis per component per round, in BFS
    # (distance) order, until the cap.
    hypotheses: list[WalkHypothesis] = []
    round_index = 0
    while len(hypotheses) < max_hypotheses:
        added = False
        for candidates in per_component:
            if round_index < len(candidates) and len(hypotheses) < max_hypotheses:
                hypotheses.append(candidates[round_index])
                added = True
        if not added:
            break
        round_index += 1
    return hypotheses[:max_hypotheses]


# Preferred loss-of-input failure mode per archetype, by fault_state /
# mode name (normalised containment). The walk proposes the mode that
# means "this component stops passing energy/material onward" — the
# shape an upstream walk is looking for — before the archetype's other
# modes. A component with no preferred mode falls back to its first
# listed mode; a component with no modes at all gets the generic
# archetype-shaped fault.
_PREFERRED_MODE_BY_ARCHETYPE: dict[str, list[str]] = {
    "A1": ["not_connected", "low input voltage", "thermal_trip", "protection active"],
    "A2": ["tripped", "stuck_open", "no coil signal", "faulty"],
    "A3": ["no power", "burnt_out", "stalled", "overheated"],
    "A4": ["jammed", "slipping", "mis-adjusted", "worn"],
    "A5": ["reads incorrectly"],
    "A6": ["produces no output"],
    "A7": ["no arc", "weak arc", "unstable arc"],
    "A8": ["open", "loose", "damaged", "clogged"],
    "A9": ["empty", "tangled"],
    "A10": ["stuck_closed", "stuck_open"],
    "A11": ["blank display", "warning displayed"],
}


def _preferred_mode(modes: list[Any], archetype_id: str | None) -> list[Any]:
    """Modes ordered with the preferred loss-of-input mode first."""
    if not modes:
        return modes
    preferred = _PREFERRED_MODE_BY_ARCHETYPE.get(archetype_id or "", [])
    def _rank(mode: Any) -> int:
        name = _norm(getattr(mode, "fault_state", None) or getattr(mode, "name", None) or str(mode))
        for index, want in enumerate(preferred):
            if _norm(want) in name or name in _norm(want):
                return index
        return len(preferred)
    return sorted(modes, key=_rank)


def _hypotheses_for_component(component: str, archetype_id: str | None,
                              archetypes: dict[str, Any], depth: int,
                              chain: list[str]) -> list[WalkHypothesis]:
    prior = round(max(0.15, 0.5 - 0.05 * depth), 3)
    evidence = " <- ".join(reversed(chain)) + " (reviewed topology)"
    archetype = archetypes.get(archetype_id) if archetype_id else None
    modes = list(getattr(archetype, "failure_modes", None) or []) if archetype is not None else []
    modes = _preferred_mode(modes, archetype_id)
    out: list[WalkHypothesis] = []
    generic_test = _GENERIC_TEST_BY_ARCHETYPE.get(
        archetype_id or "", "Report the single most informative observation on this component.")
    for mode in modes:
        name = getattr(mode, "name", None) or str(mode)
        fault = getattr(mode, "fault_state", None) or getattr(mode, "parameter", None) or ""
        cause = f"{component}: {name}" + (f" ({fault})" if fault else "")
        test = getattr(mode, "discriminating_test", None) or generic_test
        out.append(WalkHypothesis(
            cause=cause, component=component, archetype_id=archetype_id,
            confidence=prior, evidence=evidence, chain=list(chain),
            discriminating_test=test))
    if not out:
        generic = _GENERIC_FAULT_BY_ARCHETYPE.get(archetype_id or "", "is faulty")
        out.append(WalkHypothesis(
            cause=f"{component} {generic}", component=component,
            archetype_id=archetype_id, confidence=prior,
            evidence=evidence, chain=list(chain),
            discriminating_test=generic_test))
    return out


def _norm(value: str | None) -> str:
    # Underscores/hyphens are word separators too ("no_power" == "no power"):
    # archetype fault_states use them, mode names and manual text do not.
    return " ".join(str(value or "").strip().lower().split()).replace("_", " ").replace("-", " ")


# ---------------------------------------------------------------------------
# Discriminating-test selection (information gain + cost + safety)
# ---------------------------------------------------------------------------

# Ordinal cost (technician time / effort) and safety (0 = no hazard,
# 1 = needs isolation / energised-circuit care, 2 = do not do without
# the manual's procedure) for the vocabulary's tests. Defaults for an
# unknown test are cost 2, safety 1 — conservative, never assumed
# cheap or safe. Sources are the vocabulary test descriptions
# (page citations in the vocabulary file itself).
TEST_COST: dict[str, int] = {
    "step27_wood_test": 1, "post_release_spin": 1, "groove_check": 1,
    "connector_seated": 1, "tip_size_check": 1, "tip_condition": 1,
    "liner_check": 2, "cable_check": 1, "spool_check": 1,
    "ground_check": 1, "polarity_check": 2, "plug_check": 1,
    "outlet_check": 1, "breaker_check": 1, "fuse_check": 1,
    "gas_flow_check": 1, "gas_bottle_check": 1, "valve_check": 1,
    "lcd_check": 1, "power_check": 2, "voltage_check": 2,
}
TEST_SAFETY: dict[str, int] = {
    "step27_wood_test": 0, "post_release_spin": 0, "groove_check": 0,
    "connector_seated": 0, "tip_size_check": 0, "tip_condition": 0,
    "liner_check": 0, "cable_check": 0, "spool_check": 0,
    "ground_check": 1, "polarity_check": 1, "plug_check": 1,
    "outlet_check": 1, "breaker_check": 1, "fuse_check": 1,
    "gas_flow_check": 0, "gas_bottle_check": 0, "valve_check": 0,
    "lcd_check": 0, "power_check": 1, "voltage_check": 1,
}


def _entropy(weights: list[float]) -> float:
    total = sum(weights)
    if total <= 0:
        return 0.0
    return -sum((w / total) * math.log2(w / total) for w in weights if w > 0)


def select_discriminating_test(
    signatures: list[FaultSignature],
    hypotheses: list[dict[str, Any]] | list[str],
    *,
    costs: dict[str, int] | None = None,
    safety: dict[str, int] | None = None,
) -> dict[str, Any] | None:
    """Pick the test that splits the remaining signatures best.

    Score = information gain (bits, over confidence-weighted
    hypotheses) − 0.1 × cost − 0.2 × safety. A test that separates
    nothing scores ≤ 0 and is never returned: the caller then falls
    back to asking for the most informative observation. "unmodelled"
    / "not_applicable" outcomes never split a pair — they are their
    own uninformative bucket.
    """
    costs = costs or TEST_COST
    safety = safety or TEST_SAFETY
    names = [_hyp_name(h) for h in hypotheses]
    weights = [_hyp_confidence(h) for h in hypotheses]
    live = [sig for sig in signatures
            if any(_texts_match(sig.cause, n) for n in names if n)]
    if len(live) < 2:
        return None
    # Weight per signature: the matching hypothesis's confidence.
    sig_weights: list[float] = []
    for sig in live:
        w = 0.0
        for name, weight in zip(names, weights):
            if _texts_match(sig.cause, name):
                w = max(w, weight)
        sig_weights.append(w or 0.1)
    base_entropy = _entropy(sig_weights)
    test_ids: list[str] = []
    for sig in live:
        for test_id in sig.tests:
            if test_id not in test_ids:
                test_ids.append(test_id)
    best: dict[str, Any] | None = None
    for test_id in test_ids:
        groups: dict[str, list[float]] = {}
        for sig, w in zip(live, sig_weights):
            outcome = sig.tests.get(test_id)
            key = "uninformative" if outcome in NON_INFORMATIVE else str(outcome)
            groups.setdefault(key, []).append(w)
        if len(groups) < 2 or set(groups) == {"uninformative"}:
            continue
        remaining = sum(_entropy(ws) * (sum(ws) / sum(sig_weights)) for ws in groups.values())
        gain = base_entropy - remaining
        cost = int(costs.get(test_id, 2))
        safe = int(safety.get(test_id, 1))
        score = gain - 0.1 * cost - 0.2 * safe
        separates = sum(1 for a in range(len(live)) for b in range(a + 1, len(live))
                        if _outcomes_differ(live[a].tests.get(test_id), live[b].tests.get(test_id)))
        candidate = {
            "test": test_id,
            "information_gain_bits": round(gain, 3),
            "score": round(score, 3),
            "cost": cost,
            "safety": safe,
            "splits": {k: len(v) for k, v in groups.items()},
            "pairs_separated": separates,
            "separates": [sig.cause for sig in live],
            "rationale": (
                f"{test_id} splits {len(live)} remaining hypotheses into "
                f"{len(groups)} outcome groups (gain {gain:.2f} bits, cost {cost}, "
                f"safety {safe}); it separates {separates} cause pair(s)."),
        }
        if best is None or candidate["score"] > best["score"]:
            best = candidate
    if best is None or best["information_gain_bits"] <= 0:
        return None
    return best


def _outcomes_differ(a: Any, b: Any) -> bool:
    return (a not in NON_INFORMATIVE and b not in NON_INFORMATIVE and a != b)


def _hyp_name(h: Any) -> str:
    return h.get("cause", "") if isinstance(h, dict) else str(h)


def _hyp_confidence(h: Any) -> float:
    if isinstance(h, dict):
        try:
            return float(h.get("confidence") or 0.0)
        except (TypeError, ValueError):
            return 0.0
    return 0.0


# ---------------------------------------------------------------------------
# Answer handling: rule out, lower, keep-at-low-confidence
# ---------------------------------------------------------------------------

LOW_CONFIDENCE_FLOOR = 0.15
INTERMITTENT_CONFIDENCE_CAP = 0.3


def apply_test_answer(
    hypotheses: list[dict[str, Any]],
    signatures: list[FaultSignature],
    test: str,
    answer: str,
) -> dict[str, Any]:
    """Apply one technician answer to the live hypotheses.

    ``answer`` is either a test outcome value (e.g. "stops"), or one
    of "yes" / "no" / "not_sure" / "sometimes" / "unknown".

    - A known outcome that differs from a hypothesis's expected
      outcome rules that hypothesis out (it moves to ``eliminated``).
    - A hypothesis whose outcome for this test is unmodelled /
      not-applicable is NOT ruled out — it is kept at low confidence
      (×0.5, floor 0.15): the test simply cannot speak about it.
    - "not_sure" lowers every live confidence (×0.5) and rules nothing
      out; the caller picks a different test next.
    - "sometimes" (intermittent, scenario §4) rules nothing out and
      caps every live confidence at 0.3: the hypothesis stays alive
      at low confidence instead of being killed by a good-day test.
    - "yes" confirms the leading hypothesis's expected outcome;
      "no" rules the leading hypothesis out. (Only used for tests
      whose outcome the technician reports as yes/no.)
    """
    answer_norm = _norm(answer)
    sig_by_cause = {_norm(s.cause): s for s in signatures}
    kept: list[dict[str, Any]] = []
    eliminated: list[dict[str, Any]] = []
    notes: list[str] = []

    def _match(h: dict[str, Any]) -> FaultSignature | None:
        sig = sig_by_cause.get(_norm(h.get("cause", "")))
        if sig is not None:
            return sig
        for key, candidate in sig_by_cause.items():
            if key and _texts_match(h.get("cause", ""), candidate.cause):
                return candidate
        return None

    if answer_norm == "sometimes":
        for h in hypotheses:
            kept.append({**h, "confidence": min(float(h.get("confidence") or 0.0),
                                                INTERMITTENT_CONFIDENCE_CAP) or LOW_CONFIDENCE_FLOOR,
                         "intermittent": True})
        notes.append("Intermittent ('sometimes'): no hypothesis ruled out; "
                     "all kept at low confidence (cap 0.3).")
        return {"kept": kept, "eliminated": eliminated, "notes": notes}
    if answer_norm == "not sure":
        for h in hypotheses:
            kept.append({**h, "confidence": max(float(h.get("confidence") or 0.0) * 0.5,
                                                LOW_CONFIDENCE_FLOOR)})
        notes.append("NOT SURE: no hypothesis ruled out; confidences halved, "
                     "pick a different test next.")
        return {"kept": kept, "eliminated": eliminated, "notes": notes}
    if answer_norm == "unknown":
        notes.append("No observation reported: hypotheses unchanged.")
        return {"kept": list(hypotheses), "eliminated": eliminated, "notes": notes}

    for h in hypotheses:
        sig = _match(h)
        conf = float(h.get("confidence") or 0.0)
        if answer_norm in ("yes", "no"):
            # Yes/no is only meaningful against the leading hypothesis.
            if h is hypotheses[0]:
                if answer_norm == "yes":
                    kept.append({**h, "confidence": min(conf * 1.2, 0.95)})
                    notes.append(f"YES confirms the leading cause ({h.get('cause')}).")
                else:
                    eliminated.append({**h, "confidence": 0.0})
                    notes.append(f"NO rules out the leading cause ({h.get('cause')}).")
            else:
                kept.append(dict(h))
            continue
        if sig is None or test not in (sig.tests or {}):
            kept.append(dict(h))
            continue
        expected = sig.tests.get(test)
        if expected in NON_INFORMATIVE:
            kept.append({**h, "confidence": max(conf * 0.5, LOW_CONFIDENCE_FLOOR)})
            notes.append(f"{h.get('cause')}: test outcome unmodelled for this cause — "
                         "kept at low confidence, not ruled out.")
        elif _norm(str(expected)) == answer_norm:
            kept.append({**h, "confidence": min(conf * 1.1, 0.95)})
        else:
            eliminated.append({**h, "confidence": 0.0})
            notes.append(f"{h.get('cause')}: expected {expected!r}, observed "
                         f"{answer!r} — ruled out.")
    return {"kept": kept, "eliminated": eliminated, "notes": notes}


# ---------------------------------------------------------------------------
# Two faults (singles first, then pairs from the top of the ranking)
# ---------------------------------------------------------------------------

def generate_pair_hypotheses(hypotheses: list[dict[str, Any]],
                             max_pairs: int = 3) -> list[dict[str, Any]]:
    """Pairs from the top of the ranking, for when the singles are
    exhausted. A pair's confidence is the product of its singles,
    halved — two simultaneous faults are a priori less likely than
    either alone (scenario §4). Pairs are flagged ``pair: True`` so
    the technician sees this is a combination hypothesis, not a
    table cause."""
    ranked = sorted(hypotheses, key=lambda h: -float(h.get("confidence") or 0.0))
    top = ranked[:3]
    pairs: list[dict[str, Any]] = []
    for i in range(len(top)):
        for j in range(i + 1, len(top)):
            conf = float(top[i].get("confidence") or 0.0) * float(top[j].get("confidence") or 0.0) * 0.5
            pairs.append({
                "cause": f"{top[i].get('cause')} + {top[j].get('cause')} (two faults)",
                "rationale": ("Singles ruled out; trying a pair from the top of the "
                              "ranking (scenario §4: two simultaneous faults)."),
                "confidence": round(conf, 3),
                "supporting_evidence_chunk_ids": [],
                "pair": True,
            })
            if len(pairs) >= max_pairs:
                return pairs
    return pairs


# ---------------------------------------------------------------------------
# Unavailable-with-attempt (a bare unavailable is a miss)
# ---------------------------------------------------------------------------

def unavailable_with_attempt(
    ranked_guesses: list[dict[str, Any]],
    discriminating_test: dict[str, Any] | None,
    missing: list[str],
    reason: str,
) -> dict[str, Any]:
    """Build the unavailable answer that still carries an attempt.

    Never returns a bare unavailable: even with no guesses at all it
    carries one low-confidence guess ("unknown upstream fault") plus
    the most informative observation to report and what is missing.
    """
    guesses = list(ranked_guesses)
    if not guesses:
        guesses = [{
            "cause": "unknown upstream fault (not in the table, not yet walkable)",
            "confidence": 0.1,
            "evidence": "no signature and no upstream component could be named",
            "source": "unavailable_with_attempt",
        }]
        if not missing:
            missing = ["a component-level observation of the symptom path"]
    return {
        "available": False,
        "verdict": None,
        "reason": reason,
        "ranked_guesses": guesses,
        "discriminating_test": discriminating_test,
        "missing": list(missing),
        "note": ("A bare 'unavailable' counts as a miss (plan §1, second "
                 "metric): this answer carries ranked best guesses, the "
                 "test that would distinguish them, and what is missing."),
    }


# ---------------------------------------------------------------------------
# Corrected loop order: tournament re-ranking (pure step)
# ---------------------------------------------------------------------------

_TOURNAMENT_RANK = {"validated": 0, "rejected": 1, "unavailable": 2}


def rerank_with_tournament(
    hypotheses: list[dict[str, Any]],
    tournament_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Re-rank hypotheses by the early tournament's verdicts.

    VALIDATED first (confidence +0.1, capped 0.95), REJECTED demoted
    (confidence ×0.5 — demoted, never eliminated: a REJECTED verdict
    is evidence, not a state change), unavailable unchanged. The
    session's hypothesis order is kept within one status, so the
    leaderboard never reorders two rows the twin judged the same.
    Returns {"hypotheses": [...], "reranked": bool, "counts": {...}}.
    """
    by_cause = {_norm(r.get("cause")): r for r in tournament_rows}
    counts = {"validated": 0, "rejected": 0, "unavailable": 0}
    scored: list[tuple[int, float, dict[str, Any]]] = []
    for index, h in enumerate(hypotheses):
        row = None
        for key, candidate in by_cause.items():
            if key and _texts_match(h.get("cause", ""), candidate.get("cause", "")):
                row = candidate
                break
        status = (row or {}).get("status") or "unavailable"
        if status not in counts:
            status = "unavailable"
        counts[status] += 1
        conf = float(h.get("confidence") or 0.0)
        updated = dict(h)
        if status == "validated":
            updated["confidence"] = round(min(conf + 0.1, 0.95), 3)
        elif status == "rejected":
            updated["confidence"] = round(max(conf * 0.5, LOW_CONFIDENCE_FLOOR), 3)
        scored.append((_TOURNAMENT_RANK[status], -updated["confidence"], updated))
    scored.sort(key=lambda item: (item[0], item[1]))
    return {"hypotheses": [item[2] for item in scored],
            "reranked": True, "counts": counts}


# ---------------------------------------------------------------------------
# Steals (items 2-3): citation rule + fixed-shape presentation
# ---------------------------------------------------------------------------

GUESS_LABEL = "guess — reasoning, not evidence"


def cite_hypotheses(
    hypotheses: list[dict[str, Any]],
    signatures: list[FaultSignature] | None = None,
    fleet: dict[str, Any] | None = None,
    tournament_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Attach a citation to every hypothesis (steal item 2).

    A citation names its source: the manual (case + page, from the
    signature/registry), the twin trace (a tournament verdict for
    this cause), or a prior outcome (a fleet record id). A hypothesis
    with no citation is labelled ``GUESS_LABEL`` — shown, not omitted.
    Citations live on the API response, not in DiagnosticState (the
    Hypothesis model has no citation field); callers re-derive them
    per response from the same signatures.
    """
    signatures = signatures or []
    fleet_records = []
    if fleet is not None:
        fleet_records = list(fleet.get("asset_records") or []) + \
            list(fleet.get("sibling_records") or [])
    by_cause = {_norm(r.get("cause")): r for r in (tournament_rows or [])}
    cited: list[dict[str, Any]] = []
    for h in hypotheses:
        citations: list[dict[str, Any]] = []
        for sig in signatures:
            if _texts_match(h.get("cause", ""), sig.cause):
                ref = f"{sig.case_id}" if sig.case_id else "manual"
                if sig.evidence:
                    ref += f" ({sig.evidence[0][:120]})"
                citations.append({"source": "manual", "ref": ref,
                                  "text": sig.cause})
                break
        for key, row in by_cause.items():
            if key and _texts_match(h.get("cause", ""), row.get("cause", "")):
                if row.get("verdict"):
                    citations.append({"source": "twin_trace",
                                      "ref": f"tournament: {row.get('status')}",
                                      "text": f"twin verdict {row.get('verdict')}"})
                break
        for record in fleet_records:
            cause = (record.get("confirmed_cause") or "")
            if cause and _texts_match(h.get("cause", ""), cause):
                citations.append({"source": "prior_outcome",
                                  "ref": str(record.get("outcome_id") or record.get("session_id") or ""),
                                  "text": f"prior outcome on {record.get('product_id') or 'this asset'}: {cause}"})
                break
        updated = dict(h)
        updated["citations"] = citations
        updated["is_guess"] = not citations
        updated["label"] = GUESS_LABEL if not citations else "grounded"
        cited.append(updated)
    return cited


def present_hypotheses(
    hypotheses: list[dict[str, Any]],
    signatures: list[FaultSignature] | None = None,
    registry: Any = None,
    discriminating_tests: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Fixed-shape presentation (steal item 3): every hypothesis under
    the same three headings, in order — Evidence / First check /
    Expected fix. A missing expected fix shows as an empty heading so
    a reader can see it ("" — never silently dropped, never filled
    with a guess). ``discriminating_tests`` maps a cause to the
    selected test dict (from select_discriminating_test); the registry
    supplies the fix/test text per cause where it has one.
    """
    registry_causes: list[Any] = []
    if registry is not None:
        for case in getattr(registry, "cases", None) or []:
            registry_causes.extend(case.causes or [])
    presented: list[dict[str, Any]] = []
    for h in hypotheses:
        cause = h.get("cause", "")
        evidence_parts: list[str] = []
        if h.get("rationale"):
            evidence_parts.append(str(h["rationale"]))
        for citation in h.get("citations") or []:
            evidence_parts.append(f"[{citation.get('source')}] {citation.get('ref')}")
        if h.get("is_guess"):
            evidence_parts.append(GUESS_LABEL)
        first_check = ""
        expected_fix = ""
        for rc in registry_causes:
            if _texts_match(cause, rc.cause):
                first_check = rc.test or ""
                expected_fix = rc.fix or ""
                break
        if discriminating_tests is not None:
            test = discriminating_tests.get(cause) or discriminating_tests.get(_norm(cause))
            if test is not None:
                first_check = str(test.get("test") or test)
        presented.append({
            "cause": cause,
            "confidence": h.get("confidence"),
            "label": h.get("label", GUESS_LABEL if h.get("is_guess") else "grounded"),
            "Evidence": "; ".join(evidence_parts),
            "First check": first_check,
            "Expected fix": expected_fix,
        })
    return presented


def render_hypothesis_markdown(presented: dict[str, Any]) -> str:
    """Render one presented hypothesis with the three headings in
    order, exactly as the technician reads it."""
    lines = [f"### {presented.get('cause', '')} "
             f"(confidence {presented.get('confidence', 0.0):.2f}, "
             f"{presented.get('label', '')})", "",
             "Evidence", presented.get("Evidence", ""), "",
             "First check", presented.get("First check", ""), "",
             "Expected fix", presented.get("Expected fix", "")]
    return "\n".join(lines)
