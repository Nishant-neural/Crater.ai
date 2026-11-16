"""3C M800V alarm -> observable sample: the artifact is internally consistent
and its measured rates recompute from the data (not from hardcoded claims).

The sample is 12 alarm codes across 4 families from the M800V Alarm Manual
(IB-1501808(ENG)-C, READ-ONLY). O2 is a lookup rate (does the alarm code map
to a concrete observable?); O3 is the distinguishability of the sampled alarms
by their observable + remedy vectors, reported separately. There is no M800V
twin in 3C, so these are lookup/discriminability rates, not twin-verified
rates. See m800v-alarm-observable-sample-v1.json/.md in the goal files folder.
"""

import itertools
import json
from pathlib import Path

GOAL_FILES = Path(__file__).resolve().parents[3] / "goals" / \
    "crater-ai-diagnosis-and-machine-model-workflow-improvement" / "files"
SAMPLE = GOAL_FILES / "m800v-alarm-observable-sample-v1.json"


def _load():
    assert SAMPLE.exists(), f"M800V sample artifact missing: {SAMPLE}"
    return json.loads(SAMPLE.read_text())


def _vector(alarm):
    """The O3 vector: the observable vector plus the canonical checks (the
    discriminating test), excluding the alarm code itself (every code
    trivially differs). The canonical checks — not the free-text remedy —
    are compared, because the manual words the same remedy differently in
    the M and T sections (M01 0006 vs T01 0105)."""
    return (tuple(sorted(alarm["observables"].items())),
            tuple(sorted(alarm["checks"])))


def test_sample_covers_at_least_10_codes_across_at_least_3_families():
    data = _load()
    alarms = data["alarms"]
    assert len(alarms) >= 10, f"sample has {len(alarms)} alarms, need >= 10"
    families = {a["family"] for a in alarms}
    assert len(families) >= 3, f"sample spans {families}, need >= 3 families"
    codes = [a["code"] for a in alarms]
    assert len(set(codes)) == len(codes), f"duplicate alarm codes: {codes}"


def test_o2_lookup_rate_recomputes():
    data = _load()
    alarms = data["alarms"]
    mapped = [a for a in alarms if a["observables"]]
    assert len(mapped) == len(alarms), "every sampled alarm must map to an observable"
    measured = data["measured"]["o2_lookup"]
    assert measured["mapped"] == len(mapped) == 12
    assert measured["denominator"] == len(alarms) == 12
    assert measured["rate"] == 1.0
    # Fault-known breakdown recomputes from the entries.
    known = sum(1 for a in alarms if a["fault_known"] is True)
    partial = sum(1 for a in alarms if a["fault_known"] == "partial")
    unknown = sum(1 for a in alarms if a["fault_known"] is False)
    assert (known, partial, unknown) == (10, 1, 1), \
        f"fault-known breakdown is {(known, partial, unknown)}, expected (10, 1, 1)"
    assert measured["fault_known"] == {"known": known, "partial": partial,
                                        "unknown": unknown}


def test_o3_discriminability_recomputes():
    data = _load()
    alarms = data["alarms"]
    pairs = list(itertools.combinations(alarms, 2))
    assert len(pairs) == 66, f"C(12,2) = 66, got {len(pairs)}"
    indistinguishable = [(a["code"], b["code"]) for a, b in pairs
                         if _vector(a) == _vector(b)]
    assert indistinguishable == [("M01 0006", "T01 0105")], \
        f"indistinguishable pairs: {indistinguishable}"
    measured = data["measured"]["o3_discriminability"]
    assert measured["pairs_total"] == 66
    assert measured["pairs_separated"] == 65
    assert abs(measured["rate"] - 65 / 66) < 1e-9
    assert [tuple(p["codes"]) for p in measured["indistinguishable_pairs"]] == \
        [("M01 0006", "T01 0105")]


def test_controller_unknown_stays_unknown():
    data = _load()
    z02 = next(a for a in data["alarms"] if a["code"] == "Z02")
    assert z02["fault_known"] is False
    assert z02["implied_fault"] is None
    assert z02["observables"] == {"system_error": "error"}
    assert "controller_output_note" in z02
