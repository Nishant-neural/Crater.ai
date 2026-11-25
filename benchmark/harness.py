#!/usr/bin/env python3
"""Crater Industrial Benchmark v0.2 — harness.

Runs benchmark cases against a Crater instance and scores the diagnosis.
Read BUILD_NOTES_v0.2.md first: Test 1 / Test 1b runs wait for Gate 0
(Nishant's fresh OMNIPRO live check). The only execution allowed in the
v0.2 build is --dry-run, which loads every case file and checks the
scoring logic against stubbed responses. Do not run live mode until
Gate 0 passes.

Usage:
    python3 harness.py --list
    python3 harness.py --case c002-lincoln --dry-run
    python3 harness.py --case c002b-miller --dry-run
    python3 harness.py --case c006-dow-corning --dry-run
    python3 harness.py --case c002-lincoln --base-url http://localhost:8000 \
        --product-id <id> --revision-id <id>          # live (after Gate 0)

Dependencies: Python 3 stdlib only for --dry-run and --list. `requests`
is imported lazily inside the live-run path only, so --dry-run works
even if requests is absent.

What live mode does and does not do
-----------------------------------
Live mode drives the v12 diagnosis API only:
  POST {base-url}/v1/diagnose/start   {product_id, revision_id, symptom}
  POST {base-url}/v1/diagnose/{id}/respond  {input, input_kind: "observation"}
(Gateway Step 1: the harness moved to /v1/ in the same change that
mounted the prefix; --base-url stays the host, the /v1/ is appended
in live_run.)
and reads the returned session state (ranked hypotheses, current step,
evidence refs). The product/revision must already exist in the Crater
instance — ingestion is a separate step through the normal pipeline;
this harness never ingests, never starts a server, and never touches
the product tree.

The harness NEVER sends the held-out answer key. Every request payload
is built from session-input fields only (symptom + transcribed
observations), and a guard asserts that no answer-key string appears in
the serialised payload before it is sent.

Scoring (per the benchmark spec):
- Oracle cases (Lincoln / Miller): one diagnosis session per symptom.
  Every twin-expressible row of that symptom is scored against the same
  ranked hypothesis list: top-5 rank of the true cause (1 if the row's
  cause appears in the top 5, else 0), discriminating-test quality, and
  citations per claim. Settings / technique / unmappable rows are
  recorded, not scored as fault rows — the denominator split is always
  shown. O1/O2/O3 are twin checks, not diagnosis checks: they need the
  compiled twin and the v12 oracle module (benchmark/oracle.py)
  and CANNOT be run over HTTP. Live mode records that limitation and
  does not fake O1/O2/O3; it scores the diagnosis level only.
- Session cases: one session per case (per sub-case where the transcript
  has sub-cases). The documented root cause is scored by top-5 rank of
  the true cause, discriminating-test quality, and citations per claim.
  C003 (Haas) has no documented incident root cause: it is scored on
  whether BOTH competing explanations appear in the ranked hypotheses,
  with a discriminating test — see the transcript's scoring rule.

Matching: hypothesis causes are matched to the true cause by normalised
text (lowercase, whitespace collapsed, punctuation treated as a word
boundary so hyphenated and spaced phrasings normalise the same way).
A match also counts if one normalised string contains the other, because the
diagnosis may phrase the same cause more fully than the table does.
Every match is recorded with both strings, so a human can audit it.
Fault-aware matching (added for the Miller §6-5 rows, whose `cause`
field holds remedy text, not a cause): a ranked cause also matches an
oracle row when it names the row's structured `fault` — the fault's
component AND its value must both appear as whole words in the ranked
cause (see fault_matches). A component mention without the value never
matches. Fault matching is token-based and uses its own tokenizer
(`_fault_tokens`); its rule is unchanged by the Phase 0 reporting fix.

Reporting (Phase 0 fix): causes, citations, and the denominator split
are separate axes and are never merged into one headline number.
`summarize_oracle_breakdown` reports rankable rows (twin-expressible),
symptoms with 0 rankable rows, and unrankable rows (settings /
technique / unmappable) separately, and citations are reported per
claim, not per cause row — a symptom can score 4/4 on causes and 0/4
on citations, and both numbers stay visible.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EVAL = ROOT / "evaluations"

ORACLE_FILES = {"c002-lincoln": "c002-lincoln.json",
                "c002b-miller": "c002b-miller.json"}
# Distinct case_ids. (Resolved 21 Nov 2026: the spec originally used
# C011 for both the Miller oracle and the hydrocracking session test;
# the Miller oracle was renamed C002b and the hydrocracking session
# test keeps C011, so a bare `c011` now resolves to it — see main().)
SESSION_GLOB = "*-session.json"


# ---------------------------------------------------------------------------
# Case loading
# ---------------------------------------------------------------------------

def load_cases() -> dict[str, dict]:
    """All cases, keyed by distinct case_id. Oracle files carry their
    case_id in the JSON; session files carry it in `case_id`."""
    cases: dict[str, dict] = {}
    for case_id, filename in ORACLE_FILES.items():
        path = EVAL / filename
        data = json.loads(path.read_text(encoding="utf-8"))
        cases[case_id] = {"kind": "oracle", "path": path, "data": data}
    for path in sorted(EVAL.glob(SESSION_GLOB)):
        data = json.loads(path.read_text(encoding="utf-8"))
        case_id = data["case_id"]
        if case_id in cases:
            raise SystemExit(f"duplicate case_id {case_id!r}: {path} collides")
        cases[case_id] = {"kind": "session", "path": path, "data": data}
    return cases


def describe_cases(cases: dict[str, dict]) -> list[str]:
    lines = []
    for case_id in sorted(cases):
        entry = cases[case_id]
        data = entry["data"]
        if entry["kind"] == "oracle":
            denom = data["denominator"]
            lines.append(
                f"{case_id:22s} oracle           "
                f"{denom['total']} rows "
                f"(twin-expressible {denom['twin_expressible']}, "
                f"settings {denom['settings']}, "
                f"technique {denom['technique']}, "
                f"unmappable {denom.get('unmappable', 0)})  {entry['path'].name}")
        else:
            lines.append(
                f"{case_id:22s} hand_run_session  {data['symptom'][:70]}  "
                f"{entry['path'].name}")
    return lines


# ---------------------------------------------------------------------------
# Scoring (pure functions — exercised by --dry-run against stubs)
# ---------------------------------------------------------------------------

def normalize_text(value: str) -> str:
    """Lowercase, collapse whitespace, and treat every punctuation
    character as a word boundary (replaced with a space, not deleted).
    Phase 0 fix: the old version deleted punctuation, so 'fan-on-demand'
    became 'fanondemand' and never matched the spaced phrasing 'fan on
    demand' — hyphenated causes were penalised in the string match and
    in score_discriminating_test, which compares normalised cause
    phrases against normalised step text. Hyphens, dots, underscores
    and other punctuation now all normalise to a space, so
    'fan-on-demand', 'fan on demand' and 'Fan-On-Demand' all become
    'fan on demand', and 'HL.P 001' becomes 'hl p 001'. The fault-aware
    matching rule itself (fault_matches / _fault_tokens) is unchanged.
    Note the one intentional behaviour change: a glued spelling
    ('wirefeed') no longer matches a hyphenated/spaced one
    ('wire-feed' / 'wire feed'), because neither normalised string
    contains the other; both sides are normalised the same way, so a
    match now requires the same word boundaries on both sides."""
    text = re.sub(r"\s+", " ", str(value or "").replace("’", "'").replace("‘", "'"))
    text = re.sub(r"[^a-z0-9 ]", " ", text.strip().lower())
    return re.sub(r"\s+", " ", text).strip()


def cause_matches(hypothesis_cause: str, true_cause: str,
                    fault: dict | None = None) -> bool:
    """Normalised match, or one side containing the other (the diagnosis
    may phrase the same cause more fully than the table). If the oracle
    row carries a structured fault, a fault match (component AND value
    both named) also counts — see fault_matches. fault=None means
    string-match only. The string match is unchanged."""
    hyp, true = normalize_text(hypothesis_cause), normalize_text(true_cause)
    if hyp and true and (hyp == true or true in hyp or hyp in true):
        return True
    return fault_matches(hypothesis_cause, fault)


def _fault_tokens(value: str) -> list[str]:
    """Tokenise for fault matching: lowercase, then split on every run
    of non-alphanumeric characters, so '-', '_', '.', '/', whitespace
    and other punctuation are all word boundaries. This is deliberately
    a separate tokenizer from normalize_text: fault matching needs
    whole-token sequences (see fault_matches), while normalize_text is
    a string used for equality/containment. Since the Phase 0 fix
    normalize_text also treats punctuation as a word boundary, the two
    now agree on hyphenation ('fan-on-demand' -> ['fan', 'on',
    'demand'] in both); the fault tokenizer is kept as-is because the
    fault-matching rule must not change."""
    text = str(value or "").replace("’", "'").replace("‘", "'").lower()
    return [tok for tok in re.split(r"[^a-z0-9]+", text) if tok]


def _contains_tokens(haystack: list[str], needle: list[str]) -> bool:
    """True when needle appears as a contiguous subsequence of haystack."""
    if not needle:
        return False
    n = len(needle)
    return any(haystack[i:i + n] == needle
               for i in range(len(haystack) - n + 1))


def fault_matches(hypothesis_cause: str, fault: dict | None) -> bool:
    """True when the ranked cause names the oracle row's structured
    fault: the fault's component AND its value must both appear as
    whole-word token sequences in the ranked cause.

    Rules, all conservative:
    - Component and value are each matched as a contiguous token
      subsequence of the ranked cause's tokens. Whole-token comparison
      is what keeps near-misses apart: the value 'off' matches only
      the token 'off', never 'on' (antonym), 'official', or 'offer';
      'low' matches only 'low', never 'below'; 'open' never 'opened';
      'tripped' never 'trip'; 'faulty' never 'fault'. A value that is
      a substring of another word therefore cannot match.
    - Multi-word values must appear whole and in order: 'HL.P 001'
      tokenises to ['hl', 'p', '001'] and needs all three tokens,
      contiguously, in the ranked cause. Underscored values split on
      the underscore: 'poor_connection' -> ['poor', 'connection'] and
      'wiring_fault' -> ['wiring', 'fault'], so the natural phrasing
      'poor connection' matches.
    - A component mention WITHOUT the value does NOT match: distinct
      faults on the same component (e.g. Wire Feed Rolls tension low
      vs high vs groove mismatch) would otherwise collapse into one.
    - Boolean fault values (groove_matches / size_matches /
      connector_seated False) have no surface wording — 'false' never
      appears in a ranked cause — so they can never fault-match and
      stay string-match only. False is never coerced to the string
      'false'. Non-dict / missing / empty component or value: no match.
    - Negation is NOT understood: 'switch is not off' would still
      match a fault value of 'off', because token presence is all
      that is checked. No ranked cause in the saved Miller report
      uses such a negated phrasing; every new match is listed in
      HARNESS_SCORER_FIX_NOTES.md so a reader can check it by eye.
    """
    if not isinstance(fault, dict):
        return False
    component = fault.get("component")
    value = fault.get("value")
    if not isinstance(component, str) or not component.strip():
        return False
    if not isinstance(value, str) or not value.strip():
        return False
    hyp_tokens = _fault_tokens(hypothesis_cause)
    comp_tokens = _fault_tokens(component)
    value_tokens = _fault_tokens(value)
    if not hyp_tokens or not comp_tokens or not value_tokens:
        return False
    return (_contains_tokens(hyp_tokens, comp_tokens)
            and _contains_tokens(hyp_tokens, value_tokens))


def rank_of_true_cause(ranked_causes: list[str], true_cause: str,
                       fault: dict | None = None) -> int | None:
    """1-based rank of the true cause in the ranked hypothesis list,
    or None if it does not appear."""
    for index, cause in enumerate(ranked_causes, 1):
        if cause_matches(cause, true_cause, fault):
            return index
    return None


def score_top5(ranked_causes: list[str], true_cause: str,
               fault: dict | None = None) -> dict:
    rank = rank_of_true_cause(ranked_causes, true_cause, fault)
    return {"rank": rank, "score": 1.0 if rank is not None and rank <= 5 else 0.0,
            "in_top5": bool(rank is not None and rank <= 5)}


def score_citations(hypotheses: list[dict],
                      chunk_pages: dict | None = None) -> dict:
    """Citations per claim: each ranked hypothesis is a claim; it is
    cited when it carries at least one supporting evidence chunk id.
    That is a chunk-id-presence check ONLY — it does not verify a page
    number. (An earlier docstring promised a page-number check the code
    never performed: the function never received any page data, so the
    promise was unimplementable as written. Phase 0 fix: the docstring
    now says what the code does, and the page check is implemented as
    an opt-in: pass chunk_pages mapping chunk id -> page number (or
    None when the page is unknown) and a hypothesis counts as cited
    only when at least one of its chunk ids has a page in the mapping.
    Callers that have no page data pass nothing; their result carries
    page_verified=False so a chunk-id-only score is never mistaken
    for a page-verified one.)

    The scoring rule is unchanged when chunk_pages is None: same
    cited/total as before. A response with no citations scores 0.
    Citations are a separate axis from causes: report them alongside
    the top-5 cause score, never merged into it."""
    if not hypotheses:
        return {"score": 0.0, "cited": 0, "total": 0,
                "page_verified": chunk_pages is not None}
    if chunk_pages is None:
        cited = sum(1 for h in hypotheses
                    if h.get("supporting_evidence_chunk_ids"))
    else:
        cited = sum(
            1 for h in hypotheses
            if any(chunk_pages.get(chunk_id) is not None
                   for chunk_id in
                   (h.get("supporting_evidence_chunk_ids") or [])))
    return {"score": cited / len(hypotheses), "cited": cited,
            "total": len(hypotheses),
            "page_verified": chunk_pages is not None}


def score_discriminating_test(current_step: dict | None,
                              ranked_causes: list[str]) -> dict:
    """Discriminating-test quality. Scores 1 only when the system's next
    step is a test/question AND its text names at least two distinct
    top-5 hypothesis causes (i.e. the test is framed as separating
    them). The step text is always recorded verbatim for human review:
    this string check is a heuristic, not a judgement that the test
    actually separates the causes."""
    if not current_step or current_step.get("type") not in ("action", "question"):
        return {"score": 0.0, "step": current_step, "separates": []}
    text = normalize_text(
        (current_step.get("content") or "") + " " + (current_step.get("rationale") or ""))
    # Named causes: the step text must contain the full normalised cause
    # phrase of at least two distinct top-5 hypotheses.
    named = [cause for cause in ranked_causes[:5]
             if normalize_text(cause) in text]
    return {"score": 1.0 if len(named) >= 2 else 0.0,
            "step": current_step, "separates": named}


# ---------------------------------------------------------------------------
# Reporting split (Phase 0 fix): causes, citations, and the denominator
# are separate axes. Nothing here changes a scoring rule — it only
# aggregates scores that were already computed per symptom.
# ---------------------------------------------------------------------------

def summarize_oracle_breakdown(oracle: dict,
                               per_symptom: dict[str, dict]) -> dict:
    """Split an oracle run into its honest parts.

    per_symptom maps symptom id -> {"rows_scored", "rows_in_top5",
    "citations": {"cited", "total", ...}} as produced by live_run's
    score_session (or, for --rescore, rebuilt from the saved report
    plus the re-scored top-5 counts).

    Returns:
    - causes_top5: in_top5 over rankable (twin-expressible) rows ONLY.
      Symptoms with 0 twin-expressible rows contribute 0 to both the
      numerator and the denominator, and are listed separately instead
      of vanishing into the headline.
    - citations: cited/total over CLAIMS (ranked hypotheses), a
      different denominator from causes — never merged with it.
    - symptoms: rankable vs zero-row symptom ids. E.g. Miller has 11
      rankable symptoms and 6 zero-row symptoms (miller-08..miller-13),
      whose 11 rows are all settings/technique.
    - unrankable_rows: rows whose category is not twin_expressible
      (settings / technique / unmappable), grouped by category with
      row ids. They are excluded from the causes denominator and shown
      here instead of being silently dropped. NOTE: rows whose cause
      text is generic ('Have Factory Authorized Service Agent check
      ...', 'HL.P 001 appears on meters ...') are NOT unrankable —
      the oracle gives them structured faults, so they are rankable
      twin-expressible rows and stay in the causes denominator.
    - denominator: the oracle's own denominator block, copied verbatim.
    """
    rows = oracle["rows"]
    rankable = [r for r in rows if r["category"] == "twin_expressible"]
    unrankable = [r for r in rows if r["category"] != "twin_expressible"]
    by_category: dict[str, list[str]] = {}
    for row in unrankable:
        by_category.setdefault(row["category"], []).append(row["row_id"])
    rankable_by_symptom: dict[str, int] = {}
    for row in rankable:
        rankable_by_symptom[row["case_id"]] = \
            rankable_by_symptom.get(row["case_id"], 0) + 1
    rankable_symptoms = sorted(rankable_by_symptom)
    zero_row_symptoms = sorted(
        case_id for case_id in oracle["cases"]
        if rankable_by_symptom.get(case_id, 0) == 0)
    causes_in_top5 = sum(
        (per_symptom.get(symptom_id) or {}).get("rows_in_top5", 0)
        for symptom_id in rankable_symptoms)
    cited = sum(
        ((per_symptom.get(symptom_id) or {}).get("citations") or {}).get("cited", 0)
        for symptom_id in per_symptom)
    claims = sum(
        ((per_symptom.get(symptom_id) or {}).get("citations") or {}).get("total", 0)
        for symptom_id in per_symptom)
    page_verified = all(
        ((per_symptom.get(symptom_id) or {}).get("citations") or {}).get(
            "page_verified", False)
        for symptom_id in per_symptom) if per_symptom else False
    return {
        "causes_top5": {"in_top5": causes_in_top5,
                        "rankable_rows": len(rankable)},
        "citations": {"cited": cited, "claims": claims,
                      "page_verified": page_verified},
        "symptoms": {"rankable": rankable_symptoms,
                     "rankable_count": len(rankable_symptoms),
                     "zero_rows": zero_row_symptoms,
                     "zero_row_count": len(zero_row_symptoms)},
        "unrankable_rows": {
            "total": len(unrankable),
            "by_category": {cat: len(ids)
                            for cat, ids in sorted(by_category.items())},
            "row_ids": [r["row_id"] for r in unrankable]},
        "denominator": dict(oracle["denominator"]),
    }


def format_headline(breakdown: dict) -> str:
    """One honest headline line from a breakdown dict: the three axes
    side by side, with the denominator of each stated."""
    causes = breakdown["causes_top5"]
    cites = breakdown["citations"]
    symptoms = breakdown["symptoms"]
    unrank = breakdown["unrankable_rows"]
    denom = breakdown["denominator"]
    by_cat = ", ".join(f"{cat} {n}"
                       for cat, n in sorted(unrank["by_category"].items()))
    page = "page-verified" if cites["page_verified"] else "page NOT verified"
    return (
        f"causes {causes['in_top5']}/{causes['rankable_rows']} rankable rows "
        f"across {symptoms['rankable_count']} rankable symptoms "
        f"({symptoms['zero_row_count']} symptoms with 0 rankable rows: "
        f"{', '.join(symptoms['zero_rows']) or 'none'}); "
        f"citations {cites['cited']}/{cites['claims']} claims "
        f"({page}, chunk-id presence only when not page-verified); "
        f"unrankable rows excluded from causes: {unrank['total']}/"
        f"{denom['total']} ({by_cat})")


# ---------------------------------------------------------------------------
# Session input construction (answer key never included)
# ---------------------------------------------------------------------------

def session_inputs_oracle(oracle: dict, case_id: str) -> dict:
    """Diagnosis input for one oracle symptom: the symptom text only.
    The oracle rows' causes and the answer key are NOT included."""
    case = oracle["cases"][case_id]
    return {"symptom": case["symptom"], "observations": []}


def session_inputs_session(transcript: dict) -> dict:
    """Diagnosis input for one hand-run session transcript: the symptom
    plus the transcribed session observations only. The answer key is
    NOT included. Sub-cases keep only their own observations plus the
    shared ones (sub_case == 'both' or absent)."""
    sub_cases = transcript.get("sub_cases") or [None]
    inputs = {}
    for sub in sub_cases:
        observations = []
        for obs in transcript["session_observations"]:
            if sub is None or obs.get("sub_case") in (None, "both", sub):
                observations.append(obs["observation"])
        inputs[sub or "main"] = {
            "symptom": transcript["symptom"],
            "observations": observations,
        }
    return inputs


def assert_no_answer_key(payload: str, forbidden: list[str], where: str) -> None:
    """Defence in depth: the held-out answer key must never appear in a
    request payload. Raises before anything is sent."""
    for secret in forbidden:
        if secret and normalize_text(secret) and normalize_text(secret) in normalize_text(payload):
            raise SystemExit(
                f"REFUSING TO SEND: answer-key text would leak into the "
                f"request ({where}): {secret[:80]!r}")


# ---------------------------------------------------------------------------
# Live run (requests imported lazily; never run before Gate 0)
# ---------------------------------------------------------------------------

def live_run(base_url: str, product_id: str, revision_id: str | None,
             case_id: str, entry: dict) -> dict:
    import requests  # noqa: PLC0415  (lazy: --dry-run must work without it)

    base = base_url.rstrip("/") + "/v1"  # gateway Step 1: /v1/ paths
    session = requests.Session()

    def start(symptom: str) -> dict:
        payload = {"product_id": product_id, "symptom": symptom}
        if revision_id:
            payload["revision_id"] = revision_id
        resp = session.post(f"{base}/diagnose/start", json=payload, timeout=120)
        resp.raise_for_status()
        return resp.json()

    def respond(session_id: str, text: str) -> dict:
        resp = session.post(f"{base}/diagnose/{session_id}/respond",
                            json={"input": text, "input_kind": "observation"},
                            timeout=120)
        resp.raise_for_status()
        return resp.json()

    def score_session(view: dict, true_causes: list[str],
                      faults: list[dict | None] | None = None) -> dict:
        """faults is a parallel list to true_causes (one structured
        fault per oracle row, or None). Session cases pass no faults,
        so they stay string-match only."""
        state = view.get("state") or {}
        hypotheses = state.get("hypotheses") or []
        ranked = [h.get("cause", "") for h in hypotheses]
        fault_list = faults if faults is not None else [None] * len(true_causes)
        per_cause = {cause: score_top5(ranked, cause, fault)
                     for cause, fault in zip(true_causes, fault_list)}
        citations = score_citations(hypotheses)
        disc = score_discriminating_test(state.get("current_step"), ranked)
        return {"session_id": view.get("session_id"),
                "status": view.get("status"),
                "ranked_causes": ranked,
                "per_true_cause_top5": per_cause,
                "citations": citations,
                "discriminating_test": disc,
                "current_step": state.get("current_step")}

    report: dict = {"case_id": case_id, "kind": entry["kind"],
                    "base_url": base, "product_id": product_id,
                    "revision_id": revision_id,
                    "o1_o2_o3": "NOT_RUN_OVER_HTTP — O1/O2/O3 are twin checks needing "
                                "the compiled twin and benchmark/oracle.py; "
                                "live mode scores the diagnosis level only."}
    if entry["kind"] == "oracle":
        oracle = entry["data"]
        forbidden = [row["cause"] for row in oracle["rows"]]
        forbidden += [a["corrective_action"]
                      for a in oracle["answer_key"].values()]
        per_symptom = {}
        for oracle_case_id, case in oracle["cases"].items():
            symptom = case["symptom"]
            rows = [r for r in oracle["rows"]
                    if r["case_id"] == oracle_case_id
                    and r["category"] == "twin_expressible"]
            # The symptom is the only input sent; the causes and the
            # answer key are scoring-only. Guard the payload anyway.
            assert_no_answer_key(symptom, forbidden, f"symptom {oracle_case_id}")
            view = start(symptom)
            # No observations are fed for oracle cases: the symptom alone
            # is the held-out test input (same as the OMNIPRO oracle).
            scored = score_session(view, [r["cause"] for r in rows],
                                   [r.get("fault") for r in rows])
            scored["rows_scored"] = len(rows)
            scored["rows_in_top5"] = sum(
                1 for v in scored["per_true_cause_top5"].values() if v["in_top5"])
            per_symptom[oracle_case_id] = scored
        report["per_symptom"] = per_symptom
        # Phase 0 fix: report the three axes separately instead of
        # letting the 0/0 symptoms hide inside the rankable total.
        report["breakdown"] = summarize_oracle_breakdown(oracle, per_symptom)
        report["headline"] = format_headline(report["breakdown"])
    else:
        transcript = entry["data"]
        answer = transcript["answer_key"]
        forbidden = []
        if isinstance(answer, dict):
            for value in answer.values():
                if isinstance(value, dict):
                    forbidden += [str(v) for v in value.values() if v]
                elif value:
                    forbidden.append(str(value))
        per_sub = {}
        for sub, inputs in session_inputs_session(transcript).items():
            payload_text = json.dumps(inputs)
            assert_no_answer_key(payload_text, forbidden, f"session {sub}")
            view = start(inputs["symptom"])
            for observation in inputs["observations"]:
                assert_no_answer_key(observation, forbidden, f"session {sub}")
                view = respond(view["session_id"], observation)
            true_causes = []
            if sub == "main":
                root = answer.get("documented_root_cause")
                if root:
                    true_causes = [root]
            else:
                sub_answer = (answer.get(sub) or {})
                root = sub_answer.get("documented_root_cause")
                if root:
                    true_causes = [root]
            per_sub[sub] = score_session(view, true_causes)
        report["per_sub_case"] = per_sub
    return report


# ---------------------------------------------------------------------------
# Dry run: validate inputs and check scoring against stubbed responses
# ---------------------------------------------------------------------------

# STUB — clearly labelled. These are not Crater responses; they exist only
# to check that the scoring functions behave as specified.
STUB_PERFECT = {
    "label": "STUB perfect response",
    "hypotheses": [
        {"cause": "Insufficient wire feed pressure.",
         "supporting_evidence_chunk_ids": ["chunk-1"]},
        {"cause": "Incorrect wire feed roller size.",
         "supporting_evidence_chunk_ids": ["chunk-2"]},
    ],
    "current_step": {"type": "action",
                     "content": "Run the wood test to separate insufficient wire "
                                "feed pressure from incorrect wire feed roller size.",
                     "rationale": "The two causes give the same symptom."},
}
STUB_WRONG_CAUSE = {
    "label": "STUB wrong-cause response",
    "hypotheses": [
        {"cause": "Damaged MIG Gun, cable, or liner assembly.",
         "supporting_evidence_chunk_ids": ["chunk-1"]},
        {"cause": "Feed Tensioner is too tight.",
         "supporting_evidence_chunk_ids": ["chunk-2"]},
    ],
    "current_step": {"type": "action",
                     "content": "Check the gun for damage.",
                     "rationale": "The gun may be damaged."},
}
STUB_NO_CITATIONS = {
    "label": "STUB no-citations response",
    "hypotheses": [
        {"cause": "Insufficient wire feed pressure.",
         "supporting_evidence_chunk_ids": []},
        {"cause": "Incorrect wire feed roller size.",
         "supporting_evidence_chunk_ids": []},
    ],
    "current_step": {"type": "action",
                     "content": "Run the wood test to separate insufficient wire "
                                "feed pressure from incorrect wire feed roller size.",
                     "rationale": "The two causes give the same symptom."},
}


def dry_run(case_id: str, entry: dict) -> dict:
    """Validate the case file and exercise the scoring logic on stubs.
    No network, no server, no answer key sent anywhere."""
    checks: list[str] = []
    if entry["kind"] == "oracle":
        oracle = entry["data"]
        rows = oracle["rows"]
        checks.append(f"oracle {case_id}: {len(rows)} rows, "
                      f"denominator {oracle['denominator']}")
        assert set(oracle["answer_key"]) == {r["row_id"] for r in rows}
        checks.append("answer_key covers every row_id exactly once; "
                      "no remedy on any row")
        for row in rows:
            assert "remedy" not in row and "corrective_action" not in row
        # Phase 0 fix: the reporting split must hold on the real oracle
        # with no live scores at all — denominators and the zero-row
        # symptom list come from the oracle file alone, so a dry run
        # can prove them. (rows_in_top5 / citations are 0 here; this
        # checks structure, not a live result.)
        empty_per_symptom = {
            case_id: {"rows_scored": sum(
                1 for r in rows
                if r["case_id"] == case_id
                and r["category"] == "twin_expressible"),
                "rows_in_top5": 0,
                "citations": {"cited": 0, "total": 0,
                              "page_verified": False}}
            for case_id in oracle["cases"]}
        breakdown = summarize_oracle_breakdown(oracle, empty_per_symptom)
        assert breakdown["causes_top5"]["rankable_rows"] == \
            oracle["denominator"]["twin_expressible"]
        assert breakdown["unrankable_rows"]["total"] == \
            oracle["denominator"]["total"] - \
            oracle["denominator"]["twin_expressible"]
        assert (breakdown["symptoms"]["rankable_count"]
                + breakdown["symptoms"]["zero_row_count"]
                == len(oracle["cases"]))
        checks.append(
            "reporting split: causes denominator is "
            f"{breakdown['causes_top5']['rankable_rows']} rankable rows "
            f"across {breakdown['symptoms']['rankable_count']} symptoms; "
            f"{breakdown['symptoms']['zero_row_count']} symptoms with 0 "
            f"rankable rows ({', '.join(breakdown['symptoms']['zero_rows']) or 'none'}); "
            f"{breakdown['unrankable_rows']['total']} unrankable rows "
            f"excluded ({', '.join(f'{c} {n}' for c, n in sorted(breakdown['unrankable_rows']['by_category'].items()))})")
        true_cause = rows[0]["cause"]
        session_inputs = session_inputs_oracle(oracle, rows[0]["case_id"])
        checks.append(f"session input for {rows[0]['case_id']}: symptom only, "
                      f"{len(session_inputs['observations'])} observations")
    else:
        transcript = entry["data"]
        inputs = session_inputs_session(transcript)
        checks.append(f"session {case_id}: {len(inputs)} sub-case input(s), "
                      f"{sum(len(v['observations']) for v in inputs.values())} "
                      f"observations, answer key held out")
        true_cause = None
        answer = transcript["answer_key"]
        if isinstance(answer, dict):
            true_cause = answer.get("documented_root_cause")
        if true_cause is None and case_id != "c003-haas":
            checks.append("WARNING: no documented root cause in answer key")

    # Scoring checks against the stubs. The true cause used here is the
    # OMNIPRO case-1 cause (a), which the stubs are written around; the
    # point is to check the scoring functions, not this case's content.
    stub_true = "Insufficient wire feed pressure."
    results = {}
    for stub in (STUB_PERFECT, STUB_WRONG_CAUSE, STUB_NO_CITATIONS):
        ranked = [h["cause"] for h in stub["hypotheses"]]
        top5 = score_top5(ranked, stub_true)
        cites = score_citations(stub["hypotheses"])
        disc = score_discriminating_test(stub["current_step"], ranked)
        results[stub["label"]] = {
            "top5": top5, "citations": cites,
            "discriminating_test": {"score": disc["score"],
                                     "separates": disc["separates"]},
        }
    # Assertions: the specified behaviours must hold, or dry-run fails.
    assert results["STUB perfect response"]["top5"]["score"] == 1.0
    assert results["STUB perfect response"]["top5"]["rank"] == 1
    assert results["STUB perfect response"]["citations"]["score"] == 1.0
    assert results["STUB perfect response"]["discriminating_test"]["score"] == 1.0
    assert results["STUB wrong-cause response"]["top5"]["score"] == 0.0
    assert results["STUB wrong-cause response"]["top5"]["rank"] is None
    assert results["STUB no-citations response"]["citations"]["score"] == 0.0
    assert results["STUB no-citations response"]["top5"]["score"] == 1.0
    checks.append("stub checks passed: perfect scores full; wrong cause "
                  "scores 0 on top-5; no citations scores 0 on citations")
    # Fault-aware matching guards. The stubs above pass fault=None, so
    # they exercise string-match only and are unaffected by the change.
    # The fault used here is the Miller miller-01 row: Line Disconnect
    # Switch, state off; its cause field holds remedy text that never
    # string-matches ('Be sure line disconnect switch is On ...').
    miller01_fault = {"component": "Line Disconnect Switch",
                      "field": "state", "value": "off", "kind": "parameter"}
    miller01_remedy = "Be sure line disconnect switch is On (see Section 4-13)."
    # Same fault, different words: must score 1 at rank 1.
    assert score_top5(["Line disconnect switch is off"],
                      miller01_remedy, miller01_fault) == {
                          "rank": 1, "score": 1.0, "in_top5": True}
    # Component without the value must NOT match.
    assert score_top5(["Check the line disconnect switch"],
                      miller01_remedy, miller01_fault)["score"] == 0.0
    # Antonym value must NOT match: 'on' is not 'off'.
    assert score_top5(["Line disconnect switch is off"],
                      miller01_remedy,
                      {**miller01_fault, "value": "on"})["score"] == 0.0
    # A value that is a substring of another word must NOT match:
    # 'low' is not the token 'below'.
    assert score_top5(["Input power is below normal"],
                      "Connect unit to proper input voltage or check for low "
                      "line voltage (see Section 4-13).",
                      {"component": "Input Power", "field": "input_voltage",
                       "value": "low", "kind": "parameter"})["score"] == 0.0
    # Underscored multi-word value: 'poor_connection' matches the
    # natural phrasing 'poor connection'.
    assert score_top5(["Work clamp has a poor connection"],
                      "Connect work clamp to get good metal to metal contact.",
                      {"component": "Work Clamp", "field": "fault",
                       "value": "poor_connection",
                       "kind": "fault_state"})["score"] == 1.0
    # Boolean fault values have no surface wording: never fault-match.
    assert score_top5(["Incorrect wire feed rolls."],
                      "Change to correct size drive rolls (see Section 6-3).",
                      {"component": "Wire Feed Rolls",
                       "field": "groove_matches", "value": False,
                       "kind": "parameter"})["score"] == 0.0
    # A wrong cause must not score via fault matching.
    assert score_top5([h["cause"] for h in STUB_WRONG_CAUSE["hypotheses"]],
                      stub_true, miller01_fault)["score"] == 0.0
    # Phase 0 fix: normalize_text treats punctuation as a word
    # boundary, so hyphenated and spaced phrasings match. This is
    # what the old deleter broke: 'fan-on-demand' -> 'fanondemand'.
    assert normalize_text("Faulty fan-on-demand circuit") == \
        "faulty fan on demand circuit"
    assert normalize_text("HL.P 001 appears on meters.") == \
        "hl p 001 appears on meters"
    assert cause_matches("Faulty fan-on-demand circuit",
                         "Faulty fan on demand circuit")
    # The discriminating test names causes by normalised phrase: a
    # hyphenated ranked cause must be found in step text that writes
    # it spaced (the old normaliser missed this and scored 0).
    assert score_discriminating_test(
        {"type": "action",
         "content": "Run a test to separate faulty fan on demand circuit "
                    "from main control board faulty.",
         "rationale": ""},
        ["Faulty fan-on-demand circuit", "Main Control Board faulty"]
    )["score"] == 1.0
    checks.append("hyphen normalisation guards passed: 'fan-on-demand' "
                  "normalises to 'fan on demand', hyphenated causes match "
                  "spaced phrasing, and a discriminating test naming a "
                  "hyphenated cause scores 1")
    checks.append("fault-aware matching guards passed: same fault in "
                  "different words scores 1; component without value, "
                  "antonym value, substring value, and boolean fault "
                  "values score 0; wrong cause scores 0 via fault match")
    return {"case_id": case_id, "kind": entry["kind"], "mode": "dry-run",
            "checks": checks, "stub_results": results}


def dry_run_all(cases: dict[str, dict]) -> dict:
    reports = {}
    for case_id in sorted(cases):
        reports[case_id] = dry_run(case_id, cases[case_id])
    return reports


# ---------------------------------------------------------------------------
# Re-score: re-run top-5 scoring on a saved live report with the current
# matcher. No server, no live sessions — reads the saved report's
# per_symptom ranked_causes and the oracle JSON only. The saved report
# file is never modified.
# ---------------------------------------------------------------------------

def rescore_report(report_path: str) -> dict:
    path = Path(report_path)
    report = json.loads(path.read_text(encoding="utf-8-sig"))
    case_id = report.get("case_id")
    filename = ORACLE_FILES.get(case_id)
    if filename is None:
        raise SystemExit(
            f"--rescore only supports saved oracle reports; {case_id!r} "
            f"is not an oracle case")
    oracle = json.loads((EVAL / filename).read_text(encoding="utf-8-sig"))
    rows_by_symptom: dict[str, list[dict]] = {}
    for row in oracle["rows"]:
        if row["category"] == "twin_expressible":
            rows_by_symptom.setdefault(row["case_id"], []).append(row)

    before_total = after_total = total = 0
    newly_matched: list[dict] = []
    regressions: list[dict] = []
    warnings: list[str] = []
    per_symptom: dict[str, list[dict]] = {}
    for symptom_id, scored in sorted((report.get("per_symptom") or {}).items()):
        ranked = scored.get("ranked_causes") or []
        old_per = scored.get("per_true_cause_top5") or {}
        rows = rows_by_symptom.get(symptom_id, [])
        causes = [r["cause"] for r in rows]
        if len(set(causes)) != len(causes):
            warnings.append(
                f"{symptom_id}: duplicate cause strings within one symptom; "
                f"the saved per_true_cause_top5 dict cannot tell the rows "
                f"apart, so 'before' for those rows shares one entry")
        entries = []
        for row in rows:
            old = old_per.get(row["cause"], {})
            was = bool(old.get("in_top5"))
            new = score_top5(ranked, row["cause"], row.get("fault"))
            total += 1
            before_total += 1 if was else 0
            after_total += 1 if new["in_top5"] else 0
            entry = {"row_id": row["row_id"],
                     "cause": row["cause"],
                     "fault": row.get("fault"),
                     "ranked_causes": ranked,
                     "matched_ranked_cause":
                         ranked[new["rank"] - 1] if new["rank"] else None,
                     "rank_before": old.get("rank"),
                     "in_top5_before": was,
                     "rank_after": new["rank"],
                     "in_top5_after": new["in_top5"]}
            if new["in_top5"] and not was:
                newly_matched.append(entry)
            if was and not new["in_top5"]:
                regressions.append(entry)
            entries.append(entry)
        per_symptom[symptom_id] = entries
    # Phase 0 fix: the same three-axis split as live runs, with the
    # re-scored ("after") top-5 counts. Citations come from the saved
    # report (re-score does not recompute them).
    rescore_per_symptom = {
        symptom_id: {
            "rows_scored": len(rows_by_symptom.get(symptom_id, [])),
            "rows_in_top5": sum(
                1 for e in per_symptom.get(symptom_id, [])
                if e["in_top5_after"]),
            "citations": (report.get("per_symptom") or {}).get(
                symptom_id, {}).get("citations") or {},
        } for symptom_id in oracle["cases"]}
    breakdown = summarize_oracle_breakdown(oracle, rescore_per_symptom)
    return {"mode": "rescore", "case_id": case_id,
            "report": str(path), "oracle": filename,
            "top5_before": f"{before_total}/{total}",
            "top5_after": f"{after_total}/{total}",
            "rows_scored": total,
            "breakdown": breakdown,
            "headline": format_headline(breakdown),
            "newly_matched_rows": newly_matched,
            "regressions_previously_matching_now_not": regressions,
            "warnings": warnings,
            "per_symptom": per_symptom}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Crater Industrial Benchmark v0.2 harness.")
    parser.add_argument("--case", help="case id (see --list)")
    parser.add_argument("--base-url", default="http://localhost:8000",
                        help="Crater instance base URL (live mode only)")
    parser.add_argument("--product-id", help="live mode: product id in the "
                        "Crater instance (ingestion is separate)")
    parser.add_argument("--revision-id", help="live mode: revision id")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate inputs and check scoring on stubs; "
                             "no network, no server")
    parser.add_argument("--list", action="store_true",
                        help="list all cases and their type")
    parser.add_argument("--rescore", metavar="REPORT.json",
                        help="re-score top-5 on a saved oracle live report "
                             "with the current matcher; no server, no live "
                             "sessions; the saved report is not modified")
    args = parser.parse_args(argv)

    if args.rescore:
        report = rescore_report(args.rescore)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        if report["regressions_previously_matching_now_not"]:
            print("REGRESSION: a row that matched before no longer matches",
                  file=sys.stderr)
            return 1
        return 0

    cases = load_cases()
    if args.list:
        print("Crater Industrial Benchmark v0.2 — cases")
        for line in describe_cases(cases):
            print("  " + line)
        print("  (Bare 'c011' resolves to the hydrocracking session test; "
              "the Miller oracle is 'c002b-miller'.)")
        return 0

    if args.case is None:
        parser.error("--case is required (or use --list)")
    if args.case == "c011":
        # The Miller oracle was C011 until 21 Nov 2026, when it was
        # renamed C002b; the hydrocracking session test keeps C011, so
        # a bare c011 is no longer ambiguous and resolves to it.
        args.case = "c011-hydrocracking"
    if args.case not in cases:
        print(f"Unknown case {args.case!r}. Known cases:", file=sys.stderr)
        for line in describe_cases(cases):
            print("  " + line, file=sys.stderr)
        return 2

    entry = cases[args.case]
    if args.dry_run:
        report = dry_run(args.case, entry)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    # Live mode — gated: do not run before Gate 0 passes.
    if not args.product_id:
        print("Live mode needs --product-id (and --revision-id when the "
              "product has one). The product must already exist in the "
              "Crater instance: ingestion is a separate step through the "
              "normal pipeline. Live runs wait for Gate 0.",
              file=sys.stderr)
        return 2
    report = live_run(args.base_url, args.product_id, args.revision_id,
                      args.case, entry)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
