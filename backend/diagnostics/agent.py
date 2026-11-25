"""
DiagnosticAgent: runs one turn of the plan.md §6 workflow.

A "turn" = technician provides a new symptom/observation/measurement ->
agent retrieves evidence -> agent updates hypotheses -> agent picks the
next step (question / action / conclusion).

The agent does NOT decide product/revision identification (plan.md §6
step 1-2) — that happens at session-start time via the API, where the
caller already knows which Product/Revision they're working on. Baking
"identify the product" into every turn would blur the one thing this
class should be trustworthy about: reasoning over evidence for a KNOWN
product/revision.

Robustness (v12): the diagnostic turn is the one LLM call in the
codebase whose output decides whether a session lives or dies, so it
is now treated like extraction instead of trusting a bare json.loads:

- structured output is requested from the provider (Gemini JSON mode /
  Claude forced tool use) with temperature 0.2, and the output ceiling
  is the task route's 8192 tokens, not a per-call 2000 — a turn's real
  output is ~1k tokens, so the ceiling is a safety net the model never
  approaches, and truncation is detected off the provider's finish
  reason instead of surfacing as a mystery parse failure;
- unparseable output goes through the same lenient ladder extraction
  uses (fence strip, brace hunt, trailing-comma repair, literal
  parsing, regex repair) plus ONE repair call, and only then degrades;
- a degraded turn asks the technician a question and keeps the
  session ACTIVE with the previous hypotheses intact — a parse failure
  is a model-output failure, not a diagnostic failure, so it must never
  end the session or lose the hypotheses. There is no escalation any more
  (removed fix-3, on Nishant's instruction): a turn that cannot be
  reasoned about degrades to a question, and the session only ends with
  a twin-validated conclusion or the ranked best guesses at the turn limit;
- verify-before-conclusion (fix-3, the Phase 0 requirement): a conclusion
  step is only accepted when the early tournament has run on this turn's
  hypotheses and the cause the conclusion is about is VALIDATED in the
  approved twin; otherwise the conclusion is demoted to a question and
  the session stays active. The one exception is a concluded cause the
  twin cannot express at all (no component in this twin can carry it --
  the "Insufficient wire feed pressure" trap: an adjustment-type cause
  with no entity_ids): that ends the session at once with the
  unavailable-with-attempt and a plain statement, instead of demoting
  and looping to the turn limit, because no observation can make the
  twin express it. The tournament also runs BEFORE the LLM turn,
  so the model reasons over the verified ranking (and over which causes /
  tests were already asked-and-answered) before it picks its next step.
  With no approved twin, a held conclusion is labelled UNVERIFIED in the
  step content and on the timeline — verification is never silently absent.
  A discriminating test / leading-cause question is never repeated for a
  cause already asked-and-answered in the session: the next step says
  what changed (or did not change) or moves to the next untested cause.
- a malformed hypothesis is quarantined on its own (the good ones from
  the same turn are kept), the same per-fact rule as ingestion;
- the prompt no longer carries the full text of every evidence chunk
  inside state_json, and there is no separate knowledge block at all:
  the prompt carries ONE copy of the facts — the whole retrieved
  evidence rows (Phase 1 Step 3). The old component/failure-mode
  loader and the canonical graph JSON dump were second and third
  copies of the same facts and are deleted, along with every
  truncation cut (the 800-char evidence cut, the 1000-char reranker
  cut, the 1,200-char knowledge cap). A whole evidence item that does not fit
  the evidence budget is dropped whole, never cut.
- rank-then-ask (Phase 1 Step 4, audit Step 3): one turn is TWO
  structured-output calls, not one. Call 1 ranks 3-5 hypotheses
  (server-enforced, structured component + value, citations
  intersected with the chunks retrieved this turn, uncited hypotheses
  labelled guesses, never a single guess, never an empty ranking).
  Call 2 asks / concludes from that ranking, with the
  discriminating-test candidate in its prompt. The discriminating
  test comes from the single backtracking picker
  (select_discriminating_test over the product's repair-case registry
  signatures); when no signature test splits the ranking — the Miller
  registry carries no per-cause test at all — a deterministic
  ranking-fallback question names the leading unasked cause instead,
  so every turn 1 still carries a discriminating_test.
- LangGraph loop (Phase 1 Step 5, audit Step 4): the turn is a graph
  (backend/diagnostics/graph.py) — safety, retrieve, verify-pre,
  rank, discriminating-test, ask, verify-post, confidence, gate,
  safeguards, persist — checkpointed in Postgres (file-backed SQLite
  under the dev default). Safety runs first on every turn including
  turn 1, inside run_turn. The twin is a thin async client
  (twin_verification.py) speaking POST /v1/verify; a twin that is
  down degrades to NOT_VERIFIED, never a silent pass. Confidence is
  computed from the tournament on the new ranking + the
  discriminating test's information gain (see compute_confidence) —
  the ask call's overall_confidence is parsed and discarded.
  ``_run_llm_turn`` stays as the standalone rank+ask implementation
  the Step 4 tests exercise; the graph nodes call the same rank/ask
  helpers, so the two-call shape is identical in both paths.
"""
from __future__ import annotations

import ast
import json
import logging
import re

from sqlalchemy.orm import Session as DBSession

from backend.config import settings
from backend.db.models import DiagnosticSession, DiagnosticStatus
from backend.retrieval import ingestion_client as ingestion_client_mod
from backend.diagnostics.backtracking import (
    _texts_match,
    select_discriminating_test,
    signatures_from_registry,
    unavailable_with_attempt,
)
from backend.diagnostics.prompts import ASK_SYSTEM_PROMPT, RANK_SYSTEM_PROMPT
from backend.diagnostics.schema import DiagnosticState, EvidenceRef, Hypothesis, NextStep, NextStepType
from backend.diagnostics.twin_verification import (
    TwinVerifyClient,
    cannot_express_cause,
    conclusion_cause,
    verdict_for_cause,
)
from backend.diagnostics.downtime import start_downtime
from backend.knowledge.repair_cases import (
    load_miller_registry,
    load_registry,
    registry_matches_product,
)
from backend.llm import gateway
from backend.retrieval.hybrid import hybrid_retrieve  # noqa: F401 - re-exported: tests stub diag_agent.hybrid_retrieve, and graph.retrieve_node calls it via this module

logger = logging.getLogger(__name__)

# Turn bound (fix-3): the turn on which the loop stops asking new
# diagnostic questions and ends with the unavailable-with-attempt
# (ranked best guesses + what is missing) instead of an escalation.
# Escalation was removed from the loop, but the bound stays so a loop
# that cannot conclude still ends.
_MAX_TURNS_BEFORE_UNAVAILABLE_ENDING = 8

# Output ceiling for a diagnostic turn (config route). A real turn is
# ~1k tokens of JSON; 8192 is a safety net, never a target — the model
# stops at the end of the JSON object, so a large ceiling costs nothing
# extra and can never truncate a turn the way the old 2000-token cap did.
_DIAGNOSIS_TEMPERATURE = 0.2

# Prompt budget (Phase 1 Step 3, audit p.8): about 4,700 characters
# (~1,250 tokens) per turn — system ~1,200, session state ~800,
# evidence ~2,500 (top 5 whole items, table rows first), verification
# ~200; audit gate (p.13) is at or below about 5,000 characters.
# Evidence is whole items or nothing: an item that does not fit the
# evidence budget is dropped whole, never cut — except the top-ranked
# item, which is always included whole, so a large table chunk carrying
# the answer rows can never fall off a cut the way the Miller §6-5
# table did at character 3,418 of its old chunk.
_PROMPT_EVIDENCE_BUDGET_CHARS = 2500
_PROMPT_TOTAL_BUDGET_CHARS = 5000

# JSON Schemas handed to the provider's structured-output mechanism, so
# "return ONLY JSON" is enforced by the API instead of only asked for
# in prose. One schema per call of the rank-then-ask split (Phase 1
# Step 4): the rank call returns hypotheses + eliminations only, the
# ask call returns the overall confidence + next step only. The old
# single _DIAGNOSIS_OUTPUT_SCHEMA (all four fields in one call) is
# deleted with the single call it belonged to.
_RANK_OUTPUT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "hypotheses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "cause": {"type": "string"},
                    "component": {"type": "string"},
                    "value": {"type": "string"},
                    "rationale": {"type": "string"},
                    "confidence": {"type": "number"},
                    "supporting_evidence_chunk_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["cause", "rationale", "confidence"],
            },
        },
        "eliminated_hypotheses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "cause": {"type": "string"},
                    "component": {"type": "string"},
                    "value": {"type": "string"},
                    "rationale": {"type": "string"},
                    "confidence": {"type": "number"},
                    "supporting_evidence_chunk_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["cause", "rationale", "confidence"],
            },
        },
    },
    "required": ["hypotheses", "eliminated_hypotheses"],
}

_ASK_OUTPUT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "overall_confidence": {"type": "number"},
        "next_step": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["question", "action", "conclusion"]},
                "content": {"type": "string"},
                "rationale": {"type": "string"},
                "safety_notes": {"type": "array", "items": {"type": "string"}},
                "evidence_chunk_ids": {"type": "array", "items": {"type": "string"}},
                "procedure_steps": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["type", "content"],
        },
    },
    "required": ["overall_confidence", "next_step"],
}

# Rank-then-ask (Phase 1 Step 4, audit p.13): the server enforces a
# minimum of 3 and a maximum of 5 ranked hypotheses. Fewer than 3 is
# never a ranking — the model gets one repair call, and if it still
# cannot rank 3 the turn degrades (previous hypotheses kept, question
# asked) instead of persisting a single guess. More than 5 is cut to
# the top 5 in ranked order.
_MIN_RANKED_HYPOTHESES = 3
_MAX_RANKED_HYPOTHESES = 5

def _build_query(state: DiagnosticState, latest_input: str) -> str:
    """Retrieval query = symptoms + latest observation, not the whole state history (keeps it focused)."""
    parts = state.symptoms[-2:] + [latest_input]
    return " ".join(p for p in parts if p)


def _state_summary(state: DiagnosticState, include_hypotheses: bool = True) -> str:
    """Typed state summary for the prompt (audit budget ~800 chars),
    not a JSON dump of everything.

    Symptoms, the latest observations/measurements, live and eliminated
    hypothesis causes with confidences, and the current step. Evidence
    is NOT repeated here — it lives once in the evidence block, as
    whole rows. Bounded by item count (latest 3 observations /
    measurements), never by cutting an item's text. The ask call
    passes ``include_hypotheses=False``: its ranked block already
    carries the ranking, so repeating it here would be a second copy.
    """
    lines = [f"Symptoms: {'; '.join(state.symptoms) or '(none reported yet)'}"]
    if state.observations:
        lines.append(f"Observations: {'; '.join(state.observations[-3:])}")
    if state.measurements:
        lines.append(f"Measurements: {'; '.join(state.measurements[-3:])}")
    if include_hypotheses:
        if state.hypotheses:
            lines.append("Hypotheses: " + "; ".join(
                f"{h.cause} ({h.confidence:.2f})" for h in state.hypotheses))
        else:
            lines.append("Hypotheses: none yet.")
        if state.eliminated_hypotheses:
            lines.append("Eliminated: " + "; ".join(h.cause for h in state.eliminated_hypotheses))
    lines.append(f"Overall confidence: {state.confidence:.2f}; turns taken: {state.turns_taken}")
    if state.current_step is not None:
        lines.append(f"Current step ({state.current_step.type.value}): {state.current_step.content}")
    return "\n".join(lines)


def _select_evidence_items(evidence: list[EvidenceRef],
                           budget: int = _PROMPT_EVIDENCE_BUDGET_CHARS) -> list[EvidenceRef]:
    """Whole items or nothing, in rerank order.

    The top-ranked item is always included whole — it carries the
    answer rows for this turn, and a large table chunk must never fall
    off a size cut. Each further item is included whole only while the
    running content total stays within the evidence budget; an item
    that does not fit is dropped whole, never cut. ``hybrid_retrieve``
    already returns at most ``final_top_k`` (5) items, so this selects
    at most 5 whole items.
    """
    if not evidence:
        return []
    selected = [evidence[0]]
    used = len(evidence[0].content)
    for item in evidence[1:]:
        if used + len(item.content) <= budget:
            selected.append(item)
            used += len(item.content)
    return selected


def _render_evidence_block(evidence: list[EvidenceRef]) -> str:
    """The ONE copy of the facts in the prompt: whole retrieved rows.

    No truncation anywhere — each item's full content is emitted, with
    its chunk_id header for citation. Items dropped by
    ``_select_evidence_items`` do not appear at all.
    """
    items = _select_evidence_items(evidence)
    if not items:
        return "(no relevant evidence retrieved)"
    return "\n\n".join(
        f"[{e.chunk_id}] (p.{e.page_number}, {e.source_document}):\n{e.content}"
        for e in items
    )


def build_diagnosis_prompt(state: DiagnosticState, evidence: list[EvidenceRef],
                           verification_block: str = "") -> str:
    """Assemble the rank call's prompt (Phase 1 Step 4: call 1 of
    rank-then-ask).

    System instructions + typed state summary + whole evidence rows +
    verification block. No knowledge block: the facts appear exactly
    once, as evidence. Target about 4,700 characters; the audit gate
    is at or below about 5,000 (see ``_PROMPT_TOTAL_BUDGET_CHARS``).
    """
    return RANK_SYSTEM_PROMPT.format(
        state_summary=_state_summary(state),
        evidence_block=_render_evidence_block(evidence),
        verification_block=verification_block or "(twin verification unavailable for this turn)",
    )


def _render_ranked_block(hypotheses: list[dict]) -> str:
    """The ranking for the ask call: one line per hypothesis with its
    structured fields and surviving citations. No evidence text is
    repeated here — the citations point back at the rank call's
    evidence block."""
    if not hypotheses:
        return "(no hypotheses ranked)"
    lines = []
    for index, h in enumerate(hypotheses, 1):
        cited = ", ".join(h.get("supporting_evidence_chunk_ids") or [])
        lines.append(
            f"{index}. {h.get('cause')} "
            f"(component: {h.get('component') or '-'}, "
            f"value: {h.get('value') or '-'}, "
            f"confidence {float(h.get('confidence') or 0.0):.2f}, "
            f"{h.get('citation_status') or 'guess'}"
            + (f", cites {cited}" if cited else ", uncited")
            + f") — {h.get('rationale') or ''}")
    return "\n".join(lines)


def _render_discriminating_block(discriminating_test: dict | None) -> str:
    """The discriminating-test candidate for the ask call: the test,
    what it separates, and why. When the picker found no splitting
    test, the fallback text says so plainly instead of inventing a
    test name."""
    if not discriminating_test:
        return ("(no discriminating test could be named for this ranking — "
                "ask for the most informative observation instead)")
    separates = discriminating_test.get("separates") or []
    lines = [f"Test: {discriminating_test.get('test')}"]
    if discriminating_test.get("rationale"):
        lines.append(f"Why: {discriminating_test.get('rationale')}")
    if separates:
        lines.append("Separates: " + "; ".join(str(c) for c in separates))
    gain = discriminating_test.get("information_gain_bits")
    if gain is not None:
        lines.append(f"Information gain: {gain} bits")
    return "\n".join(lines)


def build_ask_prompt(state: DiagnosticState, hypotheses: list[dict],
                     discriminating_test: dict | None,
                     verification_block: str = "") -> str:
    """Assemble the ask call's prompt (Phase 1 Step 4: call 2 of
    rank-then-ask).

    System instructions + typed state summary (without the hypotheses
    — the ranked block carries them) + ranked hypotheses +
    discriminating-test candidate + verification block. No evidence
    block: the ask call reasons over the ranking, not over raw chunks,
    so it stays well under the ~5,000-char gate on its own.
    """
    return ASK_SYSTEM_PROMPT.format(
        state_summary=_state_summary(state, include_hypotheses=False),
        ranked_block=_render_ranked_block(hypotheses),
        discriminating_block=_render_discriminating_block(discriminating_test),
        verification_block=verification_block or "(twin verification unavailable for this turn)",
    )


# --- Lenient JSON parsing for diagnostic-turn output -------------------
# Same ladder extraction uses, minus the LLM repair (the caller does
# exactly one repair call so the count stays visible): strict JSON,
# brace hunt, trailing-comma repair, Python-literal parsing (single
# quotes + trailing commas), regex repair for unquoted keys. Returns
# None instead of raising — the caller decides how to degrade.
def _strip_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _brace_hunt(text: str) -> str:
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        return text[start:end + 1]
    return text


def _strict_json(text: str):
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _outside_strings_replace(text: str, mapping: dict[str, str]) -> str:
    """Replace whole words only when they are outside quoted strings, so
    a rationale containing the word "true" is not corrupted."""
    out: list[str] = []
    i = 0
    quote = None
    while i < len(text):
        ch = text[i]
        if quote is not None:
            out.append(ch)
            if ch == "\\" and i + 1 < len(text):
                out.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("\"", "'"):
            quote = ch
            out.append(ch)
            i += 1
            continue
        matched = False
        for src_word, dst in mapping.items():
            if text.startswith(src_word, i):
                before = text[i - 1] if i else " "
                after = text[i + len(src_word)] if i + len(src_word) < len(text) else " "
                if not (before.isalnum() or before == "_") and not (after.isalnum() or after == "_"):
                    out.append(dst)
                    i += len(src_word)
                    matched = True
                    break
        if not matched:
            out.append(ch)
            i += 1
    return "".join(out)


def _literal_json(text: str):
    converted = _outside_strings_replace(text, {"true": "True", "false": "False", "null": "None"})
    try:
        parsed = ast.literal_eval(converted)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _regex_repair_json(text: str):
    repaired = re.sub(r",\s*([}\]])", r"\1", text)
    repaired = re.sub(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:", r'\1"\2":', repaired)
    return _strict_json(repaired)


def _parse_lenient_json(raw: str | None) -> dict | None:
    if not raw:
        return None
    text = _strip_fence(raw)
    for cand in (text, _brace_hunt(text)):
        for attempt in (
            _strict_json,
            lambda c: _strict_json(re.sub(r",\s*([}\]])", r"\1", c)),
            _literal_json,
            _regex_repair_json,
        ):
            parsed = attempt(cand)
            if parsed is not None:
                return parsed
    return None


def _coerce_confidence(value, default: float) -> float:
    """Confidence as a 0..1 float. Models sometimes answer in percent
    (45 instead of 0.45); rescale that instead of dropping the hypothesis."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if 1.0 < number <= 100.0:
        number /= 100.0
    return max(0.0, min(1.0, number))


# The default-question phrasing ("check the leading cause — X?"), so the
# timeline can tell which cause a past question was about.
_LEADING_CAUSE_RE = re.compile(r"leading cause\s*[—\-–]\s*(.+?)(?:\?|\.|$)", re.IGNORECASE)


def _asked_causes_and_tests(state: DiagnosticState) -> tuple[set[str], set[str]]:
    """(causes, discriminating tests) already asked-and-answered in this session.

    Read off the timeline — the natural record, no new persistence layer.
    A cause counts as asked when a previous turn's question/action step
    mentioned it, or when an earlier early-tournament entry ran its
    discriminating test; a test counts as asked when an earlier
    early-tournament entry selected it. "Asked-and-answered" holds for a
    previous turn because the technician's input this turn is the answer
    to it (``run_turn`` only runs on a new input). Causes are returned as
    the canonical strings of the session's current/eliminated hypotheses,
    so callers can compare them exactly. The current turn's own entries
    are not in the timeline yet when this runs, so they are never counted.
    """
    # Phase 1 Step 5: the explicit sets own the answer; the timeline
    # read below is the union for sessions stored before the sets
    # existed (their history must not be lost).
    asked_causes: set[str] = set(state.asked_causes or [])
    asked_tests: set[str] = set(state.asked_tests or [])
    candidates = [h.cause for h in list(state.hypotheses) + list(state.eliminated_hypotheses)]
    for entry in state.timeline:
        if entry.get("kind") == "early_tournament":
            discriminating = entry.get("discriminating_test") or {}
            if discriminating.get("test"):
                asked_tests.add(discriminating["test"])
            if entry.get("cause"):
                for candidate in candidates:
                    if _texts_match(entry["cause"], candidate):
                        asked_causes.add(candidate)
        # Phase 1 Step 4: the turn itself now carries its
        # discriminating test on the timeline entry, so a test the
        # agent attached (not only one the routes-level tournament
        # attached) counts as asked-and-answered next turn. Walk
        # entries store the test as a plain string, turn entries as a
        # dict — both count.
        own_test = entry.get("discriminating_test")
        if isinstance(own_test, dict):
            if own_test.get("test"):
                asked_tests.add(own_test["test"])
        elif isinstance(own_test, str) and own_test:
            asked_tests.add(own_test)
        step = entry.get("step") or ""
        if step and (entry.get("step_type") or "") in ("question", "action"):
            match = _LEADING_CAUSE_RE.search(step)
            if match:
                for candidate in candidates:
                    if _texts_match(match.group(1).strip(), candidate):
                        asked_causes.add(candidate)
            for candidate in candidates:
                if candidate and _texts_match(step, candidate):
                    asked_causes.add(candidate)
    return asked_causes, asked_tests


def _default_question_step(state: DiagnosticState, rationale: str) -> dict:
    """The fallback question for a degraded turn — never a repeat.

    Asks about the leading cause that has NOT been asked-and-answered yet;
    when every live hypothesis was already checked and the fault persists,
    it asks what changed instead of repeating the same question (fix-3:
    Nishant's live session asked the same leading-cause question on two
    consecutive turns).
    """
    asked, _tests = _asked_causes_and_tests(state)
    unasked = [h for h in state.hypotheses if h.cause not in asked]
    if unasked:
        cause = unasked[0].cause
        content = (
            f"What do you observe when you check the leading cause — {cause}? "
            "Report the result and I will update the ranking."
        )
    elif state.hypotheses:
        checked = "; ".join(h.cause for h in state.hypotheses)
        content = (
            f"You already checked {checked} and the fault is still present — "
            "what changed since those checks? Report what is different now, "
            "or what exactly the last test showed, and I will update the ranking "
            "instead of re-asking the same question."
        )
    else:
        content = (
            "What is the single most informative observation you can report next "
            "(what you see, hear, or measure)?"
        )
    return {
        "type": "question",
        "content": content,
        "rationale": rationale,
        "safety_notes": [],
        "evidence_chunk_ids": [],
        "procedure_steps": [],
    }


def _degraded_result(state: DiagnosticState, reason: str) -> dict:
    """A turn that keeps the session alive: previous hypotheses intact,
    overall confidence unchanged, next step a question. A model-output
    failure must never end the session or lose the hypotheses."""
    return {
        "hypotheses": [h.model_dump() for h in state.hypotheses],
        "eliminated_hypotheses": [h.model_dump() for h in state.eliminated_hypotheses],
        "overall_confidence": state.confidence,
        "next_step": _default_question_step(state, reason),
    }


def _normalize_evidence_text(evidence: list[EvidenceRef]) -> str:
    """The prompted evidence as one lower-cased string, for the
    component-grounding check: a ranked component must occur in the
    evidence the model actually saw."""
    return "\n".join(e.content for e in _select_evidence_items(evidence)).lower()


def _valid_hypotheses(raw_list, default_confidence: float,
                      prompted_chunk_ids: set[str],
                      evidence_text: str) -> list[dict]:
    """Validate one hypothesis list the way ingestion validates a chunk:
    keep every good hypothesis, quarantine only the malformed ones,
    coerce percent confidences — plus the Step 4 server rules (audit
    P-14 / P-16):

    - citation intersection: cited chunk ids are intersected with the
      chunk ids actually prompted this turn; ids that were not
      retrieved are dropped, never honoured. A hypothesis with zero
      surviving citations is kept and labelled ``guess`` (P-15), not
      dropped — an uncited ranking is a guess, not a fabrication to
      hide.
    - structured component: a non-empty component must occur in the
      prompted evidence text; one that does not is blanked (the
      hypothesis is kept, the unverified field is quarantined), so a
      component is never asserted from free text.
    """
    valid: list[dict] = []
    if not isinstance(raw_list, list):
        return valid
    for item in raw_list:
        if not isinstance(item, dict):
            logger.warning("diagnosis: dropping non-object hypothesis: %.200s", repr(item))
            continue
        item = dict(item)
        item["confidence"] = _coerce_confidence(item.get("confidence"), default_confidence)
        cited = [c for c in (item.get("supporting_evidence_chunk_ids") or [])
                 if isinstance(c, str) and c in prompted_chunk_ids]
        if len(cited) != len(item.get("supporting_evidence_chunk_ids") or []):
            logger.warning(
                "diagnosis: dropping %d unretrieved citation(s) from hypothesis %r",
                len(item.get("supporting_evidence_chunk_ids") or []) - len(cited),
                item.get("cause"))
        item["supporting_evidence_chunk_ids"] = cited
        item["citation_status"] = "grounded" if cited else "guess"
        component = str(item.get("component") or "").strip()
        if component and component.lower() not in evidence_text:
            logger.warning(
                "diagnosis: blanking ungrounded component %r on hypothesis %r "
                "(not in the prompted evidence)",
                component, item.get("cause"))
            item["component"] = ""
        try:
            valid.append(Hypothesis.model_validate(item).model_dump())
        except Exception as exc:
            logger.warning("diagnosis: dropping malformed hypothesis %r: %s", item.get("cause"), exc)
    return valid


def _normalize_ranked(state: DiagnosticState, parsed: dict,
                      evidence: list[EvidenceRef]) -> dict | None:
    """Validate a parsed RANK call and enforce the 3–5 rule
    server-side (audit p.13).

    Returns ``{"hypotheses": [...], "eliminated_hypotheses": [...]}``
    with 3–5 live hypotheses (more than 5 are cut to the top 5 in
    ranked order), or None when the ranking cannot be used at all —
    fewer than 3 hypotheses, an empty ranking, or a non-object — in
    which case the caller attempts one repair call and then degrades.
    A single guess is never persisted as the ranking.
    """
    if not isinstance(parsed, dict):
        return None
    prompted_chunk_ids = {e.chunk_id for e in _select_evidence_items(evidence)}
    evidence_text = _normalize_evidence_text(evidence)
    hypotheses = _valid_hypotheses(
        parsed.get("hypotheses"), state.confidence, prompted_chunk_ids, evidence_text)
    eliminated = _valid_hypotheses(
        parsed.get("eliminated_hypotheses"), 0.0, prompted_chunk_ids, evidence_text)
    if len(hypotheses) < _MIN_RANKED_HYPOTHESES:
        logger.warning(
            "diagnosis: rank call returned %d hypotheses, below the minimum of %d",
            len(hypotheses), _MIN_RANKED_HYPOTHESES)
        return None
    if len(hypotheses) > _MAX_RANKED_HYPOTHESES:
        logger.warning(
            "diagnosis: rank call returned %d hypotheses, keeping the top %d",
            len(hypotheses), _MAX_RANKED_HYPOTHESES)
        hypotheses = hypotheses[:_MAX_RANKED_HYPOTHESES]
    return {"hypotheses": hypotheses, "eliminated_hypotheses": eliminated}


def _normalize_ask(state: DiagnosticState, parsed: dict,
                   ranked_hypotheses: list[dict]) -> dict | None:
    """Validate a parsed ASK call: overall confidence + next step.

    A missing/malformed next step becomes a question instead of
    crashing the turn; an escalate step is degraded to a question
    (escalation was removed from the loop, fix-3). The ranking from
    call 1 is never re-decided here — this call only picks the step.
    """
    if not isinstance(parsed, dict):
        return None
    overall = _coerce_confidence(
        parsed.get("overall_confidence"),
        max([h["confidence"] for h in ranked_hypotheses], default=state.confidence),
    )
    try:
        # mode="json": the next step is checkpointed by the LangGraph
        # loop, so its ``type`` must be a plain string, not a
        # NextStepType enum instance (the msgpack serde warns on
        # unregistered enum types and will block them).
        next_step = NextStep.model_validate(parsed.get("next_step")).model_dump(mode="json")
    except Exception as exc:
        logger.warning("diagnosis: malformed next_step, defaulting to question: %s", exc)
        next_step = _default_question_step(
            state, "The model's next step was malformed, so asking for the most informative observation instead."
        )
    if next_step.get("type") == NextStepType.escalate.value:
        # Escalation was removed from the loop (fix-3): a model that still
        # returns an escalate step — the schema no longer offers it — is
        # degraded to a question, never honoured. NextStepType.escalate
        # survives only so stored sessions still validate.
        logger.warning("diagnosis: model returned an escalate step; degrading to a question")
        next_step = _default_question_step(
            state, "Escalation has been removed from the diagnosis loop, so asking for the most informative observation instead."
        )
    return {"overall_confidence": overall, "next_step": next_step}


def _repair_json(raw: str, json_schema: dict, instruction: str) -> str | None:
    """One repair call: ask the model to fix its own JSON, not to
    re-diagnose. Returns the raw repaired text or None. ``instruction``
    states the shape the repair must return (rank shape with 3–5
    hypotheses, or ask shape) — the repair never re-ranks or
    re-decides, it only repairs the JSON."""
    try:
        repaired, _truncated = gateway.complete_result(
            "diagnosis",
            messages=[{"role": "user", "content": (
                "The following is a diagnostic response that is not valid JSON, "
                "or does not satisfy the required shape. "
                "Return ONLY the corrected valid JSON object in exactly the same shape, "
                f"{instruction} "
                "Do not wrap it in code fences.\n\n" + raw
            )}],
            json_schema=json_schema,
            temperature=0.0,
        )
        return repaired
    except Exception as exc:
        logger.warning("diagnosis: JSON repair call failed: %s", exc)
        return None


class _NoProviderError(Exception):
    """The gateway returned no text: no LLM provider is configured.
    Raised (not returned) so the caller can report it plainly instead
    of treating it as an unparseable model output."""


def _call_with_one_repair(prompt: str, json_schema: dict,
                          repair_instruction: str) -> dict | None:
    """One structured-output call with the lenient-parse ladder plus
    exactly ONE repair attempt: strict JSON, brace hunt,
    trailing-comma repair, Python-literal parsing, regex repair, then
    the repair call. Returns the parsed dict, or None when the output
    is unparseable after the repair — the caller decides how to
    degrade. A provider/transport failure propagates to the caller (it
    degrades the turn); an empty (no-provider) response raises
    ``_NoProviderError``.
    """
    raw, truncated = gateway.complete_result(
        "diagnosis",
        messages=[{"role": "user", "content": prompt}],
        json_schema=json_schema,
        temperature=_DIAGNOSIS_TEMPERATURE,
    )
    if not raw:
        # No provider configured — degrade to a question, never fabricate
        # reasoning and never escalate (escalation was removed from the
        # loop, fix-3). The caller reports this plainly.
        raise _NoProviderError(
            "Diagnostic reasoning requires a configured LLM provider, and none is configured")
    parsed = _parse_lenient_json(raw)
    if (parsed is None or truncated) and raw:
        # Truncated output (finish reason hit the ceiling) parses the
        # same way as malformed output, so it gets the same one repair
        # attempt instead of ending the turn.
        if truncated:
            logger.warning("diagnosis: output truncated at the ceiling; attempting one JSON repair")
        repaired = _repair_json(raw, json_schema, repair_instruction)
        if repaired:
            parsed = _parse_lenient_json(repaired)
    if parsed is None:
        logger.warning(
            "diagnosis: unparseable model output after lenient parse + one repair "
            "(prompt_chars=%d, truncated=%s): %.2000s",
            len(prompt), truncated, raw,
        )
    return parsed


def _signatures_for_product(session: DiagnosticSession) -> list:
    """Fault signatures for this session's product, from its
    repair-case registry — the single picker input (audit P-12: feed
    the one picker from a registry / manual test rows per family).

    Best-effort and registry-only: the oracle vocabulary JSON is the
    scorer's answer key and must never enter the request path (audit
    P-17/18), so only the reviewed registries are consulted, matched
    to the product by manufacturer/family/model. The product itself is
    ingestion-owned data, so it is fetched from ingestion's HTTP API
    (GET /v1/products) — never read from ingestion's tables (Phase 3
    extraction). No matching product or registry (or any load failure)
    means no signatures — the picker returns None and the ranking
    fallback names the test instead. Never raises.
    """
    try:
        products = ingestion_client_mod.get_ingestion_client().list_products()
        product = next((p for p in products if p.get("id") == session.product_id), None)
        if product is None:
            return []
        for loader in (load_registry, load_miller_registry):
            try:
                registry = loader()
            except Exception:
                continue
            if registry_matches_product(
                    registry, product.get("manufacturer"), product.get("family"), product.get("model")):
                return signatures_from_registry(registry)
    except Exception as exc:  # noqa: BLE001 - no signatures is not fatal
        logger.warning("diagnosis: registry signatures unavailable: %s", exc)
    return []


def _fallback_discriminating_test(hypotheses: list[dict],
                                  asked_causes: set[str]) -> dict | None:
    """Deterministic discriminating test when the picker cannot split
    the ranking (no signature test separates the remaining causes —
    the Miller registry carries no per-cause test at all, so this is
    the normal Miller path, not an error).

    NOT a second picker: it selects no test among candidates by gain.
    It names the leading not-yet-asked cause as the check to run and
    the top two ranked causes as the pair it separates, and labels
    itself ``ranking_fallback`` so a reader can tell it from a
    signature test. None when fewer than two hypotheses remain, or
    when every ranked cause was already asked — then there is no
    discriminating question left to ask, and the no-repeat gate asks
    what changed instead.
    """
    if len(hypotheses) < 2:
        return None
    unasked = [h for h in hypotheses if h.get("cause") not in asked_causes]
    if not unasked:
        return None
    lead = unasked[0]
    top_two = [h.get("cause") for h in hypotheses[:2]]
    return {
        "test": (
            f"Run the manual's check for the leading cause — {lead.get('cause')} — "
            "and report the result."),
        "rationale": (
            "No signature test splits this ranking, so check the leading "
            "not-yet-checked cause first."),
        "source": "ranking_fallback",
        "separates": top_two,
        "information_gain_bits": None,
        "score": None,
        "cost": None,
        "safety": None,
    }


def _discriminating_test_for_ranking(hypotheses: list[dict], signatures: list,
                                     state: DiagnosticState) -> dict | None:
    """The discriminating test for this turn's ranking, from the single
    backtracking picker (audit P-12).

    ``select_discriminating_test`` picks by information gain − cost −
    safety over the registry signatures, excluding tests already
    asked-and-answered. When it returns None (fewer than two live
    signatures match, or no signature test splits the ranking), the
    deterministic ranking fallback names the test instead — a turn
    never ends without a discriminating test while an unasked cause
    remains. Never raises: a picker failure means the fallback, and a
    fallback failure means None.
    """
    asked_causes, asked_tests = _asked_causes_and_tests(state)
    try:
        picked = select_discriminating_test(
            signatures, hypotheses, exclude_tests=asked_tests)
    except Exception as exc:  # noqa: BLE001 - picker failure is not fatal
        logger.warning("diagnosis: discriminating-test picker failed: %s", exc)
        picked = None
    if picked is not None:
        return picked
    return _fallback_discriminating_test(hypotheses, asked_causes)


def _attach_discriminating_test(state: DiagnosticState, result: dict,
                                discriminating_test: dict | None) -> dict:
    """Carry the discriminating test into a question/action step.

    The step content becomes the canonical discriminating-test
    question — the test, and the leading cause to check — and the
    rationale names the separated causes, so the step (content +
    rationale) frames the test as separating at least two ranked
    causes. The separated causes live in the rationale, not the
    content: the asked-tracking reads step content, and a cause named
    only as "separated by this test" was not itself checked — putting
    both names in the content would mark both asked after one turn.
    A step that already carries the test text is left alone, and
    conclusion steps are never rewritten — the test is recorded on
    the state, not in the conclusion.
    """
    if not discriminating_test:
        return result
    step = result.get("next_step") or {}
    if step.get("type") not in (NextStepType.question.value, NextStepType.action.value):
        return result
    test_text = str(discriminating_test.get("test") or "")
    if test_text and test_text in (step.get("content") or ""):
        return result
    ranked = result.get("hypotheses") or []
    asked_causes, _asked_tests = _asked_causes_and_tests(state)
    lead = next((h.get("cause") for h in ranked
                 if h.get("cause") and h.get("cause") not in asked_causes),
                (ranked[0].get("cause") if ranked else None))
    separates = discriminating_test.get("separates") or [h.get("cause") for h in ranked[:2]]
    separates = [c for c in separates if c]
    rationale = str(discriminating_test.get("rationale") or "")
    if separates:
        rationale = (rationale + " It separates: " + "; ".join(separates) + ".").strip()
    content = f"Discriminating test: {test_text} "
    if lead:
        content += (f"What do you observe when you check the leading cause — {lead}? "
                    "Report the result and I will update the ranking.")
    else:
        content += "What do you observe?"
    result["next_step"] = {**step, "content": content, "rationale": rationale}
    return result


def compute_confidence(hypotheses: list[dict], verification: dict | None,
                       discriminating_test: dict | None) -> float:
    """Confidence, computed — not asked (audit p.6, Phase 1 Step 5).

    Derived from the tournament on the NEW ranking (the post-rank
    verify) + the discriminating test's split — never from the ask
    call's ``overall_confidence``, which the graph parses for schema
    compatibility and discards. Base 0.50; the leading cause's verdict
    moves it most (VALIDATED +0.30, REJECTED -0.25, CANNOT_EXPRESS
    -0.20, NOT_VERIFIED 0); the validated-minus-rejected share adds up
    to +/-0.10; the test's information gain, normalised by log2 of the
    ranking size, adds up to +0.10. Clamped to [0.05, 0.95] so a
    computed confidence never claims certainty or impossibility.
    """
    import math

    ranked = hypotheses or []
    n = max(len(ranked), 1)
    results = (verification or {}).get("results") or []
    by_cause = {r.get("cause"): r for r in results}
    top_status = "unavailable"
    top_cannot_express = False
    if ranked:
        top_row = by_cause.get(ranked[0].get("cause") or "")
        if top_row is not None:
            top_status = top_row.get("status") or "unavailable"
            top_cannot_express = bool(top_row.get("cannot_express"))
    validated = sum(1 for r in results if r.get("status") == "validated")
    rejected = sum(1 for r in results if r.get("status") == "rejected")
    score = 0.50
    if top_status == "validated":
        score += 0.30
    elif top_status == "rejected":
        score -= 0.25
    elif top_cannot_express:
        score -= 0.20
    score += 0.10 * ((validated - rejected) / n)
    gain = (discriminating_test or {}).get("information_gain_bits")
    if gain is not None and n > 1:
        try:
            gain_value = float(gain)
        except (TypeError, ValueError):
            gain_value = 0.0
        score += 0.10 * min(max(gain_value, 0.0), math.log2(n)) / math.log2(n)
    return max(0.05, min(0.95, score))


def _run_rank_call(state: DiagnosticState, evidence: list[EvidenceRef],
                   verification_block: str = "") -> dict | None:
    """Call 1 of rank-then-ask: rank 3–5 hypotheses from the evidence.

    Returns the normalized ranking, or None when the ranking cannot be
    used — unparseable after one repair, or fewer than 3 hypotheses
    after one repair. The caller degrades the turn (previous
    hypotheses kept, question asked) instead of persisting a single
    guess or an empty ranking. A provider failure raises to the caller.
    """
    prompt = build_diagnosis_prompt(state, evidence, verification_block)
    parsed = _call_with_one_repair(
        prompt, _RANK_OUTPUT_SCHEMA,
        "Rank 3 to 5 hypotheses in the hypotheses array (never fewer than 3), "
        "with eliminated_hypotheses alongside, without adding, removing, "
        "or changing any cause or confidence.")
    if parsed is None:
        logger.warning("diagnosis: rank call returned no usable output; degrading turn")
        return None
    ranked = _normalize_ranked(state, parsed, evidence)
    if ranked is not None:
        return ranked
    # Fewer than 3 hypotheses (or an empty ranking): one repair call
    # asking for a full ranking, then re-validate. A second failure
    # degrades — the turn never persists a single guess.
    logger.warning("diagnosis: rank call below the 3-hypothesis minimum; attempting one rank repair")
    repaired = _repair_json(
        json.dumps(parsed), _RANK_OUTPUT_SCHEMA,
        "Rank 3 to 5 hypotheses in the hypotheses array (never fewer than 3), "
        "with eliminated_hypotheses alongside, without adding, removing, "
        "or changing any cause or confidence.")
    if repaired:
        ranked = _normalize_ranked(state, _parse_lenient_json(repaired) or {}, evidence)
    if ranked is None:
        logger.warning("diagnosis: rank call still below the 3-hypothesis minimum after repair; degrading turn")
    return ranked


def _run_ask_call(state: DiagnosticState, ranked: dict,
                  discriminating_test: dict | None,
                  verification_block: str = "") -> dict | None:
    """Call 2 of rank-then-ask: decide the next step from the ranking.

    Returns the normalized ask (overall confidence + next step), or
    None when the output is unparseable after one repair — the caller
    keeps the ranking and asks the default question instead.
    """
    prompt = build_ask_prompt(
        state, ranked["hypotheses"], discriminating_test, verification_block)
    parsed = _call_with_one_repair(
        prompt, _ASK_OUTPUT_SCHEMA,
        "Return the overall_confidence and next_step only, without "
        "adding, removing, or changing any hypothesis, cause, or confidence.")
    if parsed is None:
        logger.warning("diagnosis: ask call returned no usable output; keeping the ranking with a default question")
        return None
    return _normalize_ask(state, parsed, ranked["hypotheses"])


def _run_llm_turn(state: DiagnosticState, evidence: list[EvidenceRef],
                  verification_block: str = "", signatures: list | None = None) -> dict:
    """One diagnostic turn as two structured-output calls (Phase 1
    Step 4, audit Step 3): rank, then ask.

    Call 1 ranks 3–5 hypotheses (server-enforced); the discriminating
    test is picked from that ranking by the single backtracking
    picker; call 2 decides the next step from the ranking + the
    candidate test. The result keeps the old shape — hypotheses,
    eliminated_hypotheses, overall_confidence, next_step — plus
    ``discriminating_test``. A failure in either call degrades the
    turn to a question with the previous hypotheses intact (a rank
    failure keeps the previous ranking; an ask failure keeps the new
    ranking); a provider/transport failure degrades the same way, and
    a missing provider is reported plainly instead of fabricated.
    """
    try:
        ranked = _run_rank_call(state, evidence, verification_block)
    except _NoProviderError as exc:
        # No provider configured — degrade to a question, never
        # fabricate reasoning and never escalate (escalation was
        # removed from the loop, fix-3). The session stays active with
        # its hypotheses intact; the technician is told plainly what
        # is missing.
        degraded = _degraded_result(
            state,
            f"{exc} — no hypotheses were changed. Configure a provider, or report "
            "the most informative observation and I will retry the reasoning "
            "once one is available.",
        )
        degraded["discriminating_test"] = (
            _discriminating_test_for_ranking(
                [h.model_dump() for h in state.hypotheses], signatures or [], state)
            if state.hypotheses else None)
        return degraded
    except Exception as exc:
        # A provider/transport failure (429, network) is not a diagnosis
        # and must not kill the session either — degrade to a question.
        logger.warning("diagnosis: rank call failed: %s", exc)
        degraded = _degraded_result(
            state,
            f"The diagnostic model call failed ({exc}); no hypotheses were changed — please report the most informative observation and I will retry the reasoning.",
        )
        degraded["discriminating_test"] = (
            _discriminating_test_for_ranking(
                [h.model_dump() for h in state.hypotheses], signatures or [], state)
            if state.hypotheses else None)
        return degraded
    if ranked is None:
        degraded = _degraded_result(
            state,
            "The diagnostic model returned a ranking with fewer than 3 hypotheses (or an unparseable response) — no hypotheses were changed, so please report the most informative observation and I will update the ranking instead of guessing.",
        )
        # A degraded turn can still carry a test for the ranking it
        # kept (previous hypotheses), so the state is never left
        # without one while ranked causes remain.
        degraded["discriminating_test"] = (
            _discriminating_test_for_ranking(
                [h.model_dump() for h in state.hypotheses], signatures or [], state)
            if state.hypotheses else None)
        return degraded

    discriminating_test = _discriminating_test_for_ranking(
        ranked["hypotheses"], signatures or [], state)
    try:
        asked = _run_ask_call(state, ranked, discriminating_test, verification_block)
    except Exception as exc:
        logger.warning("diagnosis: ask call failed: %s", exc)
        asked = None
    if asked is None:
        result = {
            "hypotheses": ranked["hypotheses"],
            "eliminated_hypotheses": ranked["eliminated_hypotheses"],
            "overall_confidence": max(
                [h["confidence"] for h in ranked["hypotheses"]], default=state.confidence),
            "next_step": _default_question_step(
                state,
                "The diagnostic model returned an unparseable next step — the ranking was kept, so please report the most informative observation and I will update it instead of guessing."),
            "discriminating_test": discriminating_test,
        }
        return _attach_discriminating_test(state, result, discriminating_test)

    result = {
        "hypotheses": ranked["hypotheses"],
        "eliminated_hypotheses": ranked["eliminated_hypotheses"],
        "overall_confidence": asked["overall_confidence"],
        "next_step": asked["next_step"],
        "discriminating_test": discriminating_test,
    }
    return _attach_discriminating_test(state, result, discriminating_test)


def _render_unavailable_attempt(attempt: dict) -> str:
    """Readable text for an unavailable-with-attempt ending: ranked best
    guesses, the discriminating test when known, and what is missing."""
    lines = [
        "The diagnosis cannot be concluded from the evidence so far — the turn limit "
        "was reached without a twin-validated conclusion. Best guesses, ranked:",
    ]
    for index, guess in enumerate(attempt.get("ranked_guesses") or [], 1):
        lines.append(
            f"{index}. {guess.get('cause')} "
            f"(confidence {float(guess.get('confidence') or 0.0):.2f}) — "
            f"{guess.get('evidence') or 'no evidence recorded'}")
    test = attempt.get("discriminating_test") or None
    if test:
        lines.append(
            f"Discriminating test: {test.get('test')} — {test.get('rationale') or ''}")
    missing = attempt.get("missing") or []
    if missing:
        lines.append("What is missing: " + "; ".join(missing))
    lines.append(
        "What do you observe when you run the discriminating test above, or which "
        "of these guesses matches what you see? Report it and the ranking will update.")
    return "\n".join(lines)


def _render_cannot_express_attempt(attempt: dict, cause: str, reason: str) -> str:
    """Readable text for a cannot-express ending: a plain statement that
    the twin cannot express the cause, ranked best guesses, the
    discriminating test when known, and what is missing. It asks NO
    further question (no "?" anywhere in the text): no observation can
    make the twin express this cause, so asking for the next observation
    would only loop. Shares the guesses/test/missing shape of
    ``_render_unavailable_attempt`` with a different opening and closing.
    """
    lines = [
        f"The twin cannot express this cause — {cause}. {reason}",
        "No further observation can make the twin express it, so this diagnosis "
        "ends here with the best available answer instead of asking for the next "
        "observation or waiting for the turn limit. Best guesses, ranked:",
    ]
    for index, guess in enumerate(attempt.get("ranked_guesses") or [], 1):
        lines.append(
            f"{index}. {guess.get('cause')} "
            f"(confidence {float(guess.get('confidence') or 0.0):.2f}) — "
            f"{guess.get('evidence') or 'no evidence recorded'}")
    test = attempt.get("discriminating_test") or None
    if test:
        lines.append(
            f"Discriminating test: {test.get('test')} — {test.get('rationale') or ''}")
    missing = attempt.get("missing") or []
    if missing:
        lines.append("What is missing: " + "; ".join(missing))
    lines.append(
        "The leading guess above is the best available answer, and it is not "
        "twin-verified.")
    return "\n".join(lines)


def _cannot_express_ending(state: DiagnosticState, cause_text: str,
                           row_reason: str,
                           discriminating_test: dict | None = None) -> tuple[dict, str]:
    """Build the ending step for a concluded cause the twin cannot
    express, plus the reason string for the timeline.

    The step keeps the loop's ending shape (a question-type step carrying
    the unavailable-with-attempt, the same shape as the turn-bound
    ending): there is no unavailable status in ``DiagnosticStatus``, and
    a conclusion step would falsely mark the session concluded. The
    content itself asks no question -- the ending is the answer.
    The attempt carries this turn's discriminating test when one is
    known (Phase 1 Step 4, audit P-09: the endings used to pass None
    here). The test line adds no "?" — neither the picker test texts
    nor the fallback test text is phrased as a question.
    """
    hypotheses = state.hypotheses or []
    guesses = [{"cause": h.cause, "confidence": h.confidence,
                "evidence": h.rationale, "source": "session_hypothesis"}
               for h in hypotheses]
    reason = f"The twin cannot express this cause ({cause_text}): {row_reason}"
    attempt = unavailable_with_attempt(
        guesses, discriminating_test,
        [f"a twin that models {cause_text}",
         "a check of this cause outside the twin (the manual's own test for it)"],
        reason)
    step = {
        "type": NextStepType.question.value,
        "content": _render_cannot_express_attempt(attempt, cause_text, row_reason),
        "rationale": reason,
        "safety_notes": [],
        "evidence_chunk_ids": [],
        "procedure_steps": [],
    }
    return step, reason


def _apply_forced_safeguards(state: DiagnosticState, result: dict) -> dict:
    """Deterministic guardrails around the diagnostic turn (plan.md §25
    as amended by fix-3: escalation is removed from the loop entirely).

    - An escalate step from the model (legacy / non-conforming output)
      becomes a question — the loop never escalates.
    - The turn bound still exists, but it now ends the loop with the
      unavailable-with-attempt instead of an escalation: when the turn
      about to run is the 8th, the next step is a question carrying the
      ranked best guesses and what is missing — never a bare stop, and
      the session stays active so the technician can still answer the
      discriminating test. A validated conclusion is never overridden
      by the bound (the gate runs before this, and a conclusion step is
      left alone here). ``result["turn_bound_reached"]`` is set so
      ``run_turn`` can record the ending on the timeline.
    """
    if result.get("cannot_express_ending"):
        # The gate already ended the turn with the cannot-express
        # attempt; the turn-bound guard must not overwrite it with the
        # generic turn-limit text (not even on turn 8).
        return result
    step = result.get("next_step") or {}
    if step.get("type") == NextStepType.escalate.value:
        result["next_step"] = _default_question_step(
            state, "Escalation has been removed from the diagnosis loop, so asking for the most informative observation instead.")
        return result
    if (state.turns_taken + 1 >= _MAX_TURNS_BEFORE_UNAVAILABLE_ENDING
            and step.get("type") != NextStepType.conclusion.value):
        hypotheses = state.hypotheses or [
            Hypothesis.model_validate(h) for h in result.get("hypotheses", [])
            if isinstance(h, dict) and h.get("cause")]
        guesses = [{"cause": h.cause, "confidence": h.confidence,
                    "evidence": h.rationale, "source": "session_hypothesis"}
                   for h in hypotheses]
        attempt = unavailable_with_attempt(
            guesses, result.get("discriminating_test"),
            ["a twin-validated conclusion within 8 turns",
             "the outcome of the discriminating test for the leading cause"],
            "Turn limit reached without a twin-validated conclusion")
        result["next_step"] = {
            "type": NextStepType.question.value,
            "content": _render_unavailable_attempt(attempt),
            "rationale": "Turn limit reached without a twin-validated conclusion.",
            "safety_notes": [],
            "evidence_chunk_ids": [],
            "procedure_steps": [],
        }
        result["turn_bound_reached"] = True
    return result


def _demoted_conclusion_step(state: DiagnosticState, original_step: dict, why: str) -> dict:
    """The question step a held conclusion becomes: labelled UNVERIFIED,
    saying why it was held, and asking about the next untested cause
    (or the most informative observation when every cause was checked)."""
    asked, _tests = _asked_causes_and_tests(state)
    unasked = [h for h in state.hypotheses if h.cause not in asked]
    if unasked:
        question = (
            f"What do you observe when you check the next untested cause — {unasked[0].cause}? "
            "Report the result and I will update the ranking.")
    else:
        question = (
            "What is the single most informative observation you can report next "
            "(what you see, hear, or measure)?")
    return {
        "type": NextStepType.question.value,
        "content": (
            f"UNVERIFIED — the conclusion \"{original_step.get('content') or ''}\" was held "
            f"because {why}. It has not been verified in the twin, so it is not a "
            f"recommendation to act on. {question}"),
        "rationale": f"Conclusion held for lack of twin validation: {why}",
        "safety_notes": [],
        "evidence_chunk_ids": [],
        "procedure_steps": [],
    }


def _apply_conclusion_gate(state: DiagnosticState, result: dict,
                             summary: dict | None = None) -> dict:
    """Verify-before-conclusion (the Phase 0 requirement, fix-3).

    A conclusion step is only accepted when the tournament has run on
    this turn's hypotheses and the cause the conclusion is about carries
    a VALIDATED verdict. Otherwise the conclusion is demoted to a
    question step and the session stays active — a conclusion never goes
    out with its cause unvalidated or unverified. Escalation is not an
    alternative: it was removed from the loop. The gate runs after the
    LLM turn and before the turn-bound guard, so a validated conclusion
    at the turn limit still concludes. Non-conclusion steps pass through
    untouched. Appends a ``conclusion_verified`` or ``conclusion_held``
    timeline entry (turn = the turn about to run).

    Phase 1 Step 5: the verdicts come from the graph's post-rank verify
    node (``summary`` — the twin client's summary shape), so the
    tournament runs once per turn on the new ranking instead of three
    times. ``summary=None`` means no verification ran: the conclusion
    is held as unverified, never accepted on an absent verdict.

    The one exception is the cannot-express path: when the concluded
    cause carries the tournament's ``cannot_express`` signal (no
    component in this twin can carry it -- no matching component / no
    entity_ids, or the reviewed RepairAction for it references
    components this twin does not have, or the matched component has
    no simulation rule describing a fault in it), the conclusion is NOT demoted
    and the session does NOT wait for the turn limit. No observation can
    make the twin express such a cause, so demoting would loop forever
    waiting for a twin pass that cannot come. Instead the session ends
    at once with the unavailable-with-attempt (ranked best guesses +
    what is missing) and a plain "the twin cannot express this cause"
    statement, with a ``conclusion_unexpressible`` timeline entry and
    ``result["cannot_express_ending"]`` set so ``run_turn`` records the
    ending and the later guards leave it alone.
    """
    step = result.get("next_step") or {}
    if step.get("type") != NextStepType.conclusion.value:
        return result
    turn_no = state.turns_taken + 1
    cause = conclusion_cause(state, step.get("content") or "")
    if summary is None or not summary.get("available"):
        reason = (summary or {}).get("reason") or "no twin verification ran for this turn"
        result["next_step"] = _demoted_conclusion_step(
            state, step, f"the twin could not verify it ({reason})")
        state.timeline.append({
            "turn": turn_no, "kind": "conclusion_held",
            "input": step.get("content") or "",
            "cause": cause.cause if cause else None,
            "available": False, "verdict": None, "reason": reason,
            "step_type": "conclusion_held",
            "step": f"Conclusion held as unverified: {reason}",
        })
        return result
    verdict = verdict_for_cause(summary["results"], cause.cause) if cause else "unavailable"
    if verdict == "validated":
        state.timeline.append({
            "turn": turn_no, "kind": "conclusion_verified",
            "input": step.get("content") or "",
            "cause": cause.cause if cause else None,
            "available": True, "verdict": "validated",
            "reason": None,
            "step_type": "conclusion_verified",
            "step": f"Conclusion verified in twin: {cause.cause} VALIDATED",
        })
        return result
    row = next((r for r in summary["results"]
                if cause is not None and _texts_match(cause.cause, r.get("cause", ""))), None)
    if cause is not None and cannot_express_cause(summary["results"], cause.cause):
        # The twin cannot express this cause at all: do NOT demote and
        # ask about the next cause, do NOT ask for more observations,
        # and do NOT wait for the turn limit -- none of those can make
        # the twin express it. End at once with the attempt, plainly.
        row_reason = (row or {}).get("reason") or "no component in this twin can carry it"
        ending_step, attempt_reason = _cannot_express_ending(
            state, cause.cause, row_reason, result.get("discriminating_test"))
        result["next_step"] = ending_step
        result["cannot_express_ending"] = True
        result["cannot_express_reason"] = attempt_reason
        state.timeline.append({
            "turn": turn_no, "kind": "conclusion_unexpressible",
            "input": step.get("content") or "",
            "cause": cause.cause,
            "available": True, "verdict": "unavailable", "reason": attempt_reason,
            "results": summary["results"], "counts": summary["counts"],
            "step_type": "conclusion_unexpressible",
            "step": f"Conclusion held: the twin cannot express {cause.cause} — {row_reason}",
        })
        return result
    why = f"the twin returned {verdict} for {cause.cause if cause else 'the leading cause'}" + (
        f" ({row['reason']})" if row and row.get("reason") else "")
    result["next_step"] = _demoted_conclusion_step(state, step, why)
    state.timeline.append({
        "turn": turn_no, "kind": "conclusion_held",
        "input": step.get("content") or "",
        "cause": cause.cause if cause else None,
        "available": True, "verdict": verdict, "reason": why,
        "results": summary["results"], "counts": summary["counts"],
        "step_type": "conclusion_held",
        "step": f"Conclusion held: {why}",
    })
    return result


def _apply_no_repeat_gate(state: DiagnosticState, result: dict) -> dict:
    """Never ask the same discriminating question twice for one cause
    (fix-3: Nishant's live session asked the same leading-cause question
    on turns #3 and #4).

    When the next step is a question/action whose every mentioned cause
    was already asked-and-answered in an earlier turn, rewrite it: ask
    about the next untested cause, or — when every live cause was
    already checked — ask what changed since those checks. A step that
    mentions a not-yet-asked cause is left alone.
    """
    step = result.get("next_step") or {}
    if step.get("type") not in (NextStepType.question.value, NextStepType.action.value):
        return result
    content = step.get("content") or ""
    mentioned = [h for h in list(state.hypotheses) + list(state.eliminated_hypotheses)
                 if h.cause and _texts_match(content, h.cause)]
    if not mentioned:
        return result
    asked, _tests = _asked_causes_and_tests(state)
    if not all(h.cause in asked for h in mentioned):
        return result
    unasked = [h for h in state.hypotheses if h.cause not in asked]
    if unasked:
        new_content = (
            f"What do you observe when you check the next untested cause — {unasked[0].cause}? "
            "Report the result and I will update the ranking.")
        rationale = (
            f"{mentioned[0].cause} was already checked and the fault persists — moving to "
            "the next untested cause instead of repeating the question.")
    else:
        checked = "; ".join(sorted(asked)) or "the leading causes"
        new_content = (
            f"You already checked {checked} and the fault is still present — what changed "
            "since those checks? Report what is different now, or what exactly the last "
            "test showed, and I will update the ranking instead of re-asking the same question.")
        rationale = (
            "All leading causes were already checked and the fault persists; asking what "
            "changed instead of repeating the question.")
    result["next_step"] = {**step, "content": new_content, "rationale": rationale}
    return result


def run_turn(
    db: DBSession,
    session: DiagnosticSession,
    latest_input: str,
    input_kind: str = "observation",  # "symptom" | "observation" | "measurement"
    *,
    graph=None,
    checkpointer=None,
    twin_client=None,
) -> DiagnosticState:
    """
    Run one diagnostic turn through the LangGraph loop (Phase 1 Step 5).

    The turn is one graph invocation (see ``backend/diagnostics/graph.py``):
    safety first (every turn, including turn 1 — the safety check used to
    live in the routes respond handler and turn 1 was never checked),
    retrieve, verify-pre, rank, discriminating-test, ask, verify-post,
    computed confidence, conclusion gate, safeguards, persist. The graph
    is checkpointed under ``thread_id =
    "diagnostic-session:{session.id}"``, so the loop state survives a
    restart; the session row remains the API-visible store and is
    written here after the graph returns, exactly as before.

    ``graph`` / ``checkpointer`` / ``twin_client`` are injectable for
    tests. The default twin client is the thin async client over HTTP
    to the twin-verifier service (``TWIN_VERIFIER_BASE_URL``; POST
    /v1/verify -> 202 + job, poll; never blocks a turn). The default
    graph uses the default checkpointer (Postgres when the app is
    configured for Postgres, file-backed SQLite otherwise).
    """
    # Local import: graph.py imports this module for the node helpers,
    # so importing it at module level would be circular.
    from backend.diagnostics.graph import get_diagnosis_graph

    state = DiagnosticState.model_validate(session.state)

    if input_kind == "symptom":
        state.symptoms.append(latest_input)
    elif input_kind == "measurement":
        state.measurements.append(latest_input)
    else:
        state.observations.append(latest_input)

    # Seed the explicit asked sets from the timeline once, for sessions
    # stored before the sets existed; the graph maintains them after.
    asked_causes, asked_tests = _asked_causes_and_tests(state)
    state.asked_causes = sorted(set(state.asked_causes or []) | asked_causes)
    state.asked_tests = sorted(set(state.asked_tests or []) | asked_tests)

    # Signatures for the discriminating-test picker, from this product's
    # repair-case registry (best-effort; no matching registry means the
    # picker cannot split and the ranking fallback names the test
    # instead).
    signatures = _signatures_for_product(session)

    if twin_client is None:
        # Split path (Phase 3 twin-verifier extraction): the thin
        # client talks HTTP to twin-verifier (POST /v1/verify -> 202 +
        # job, poll; never blocks a turn). With no base URL configured
        # the client degrades every call to NOT_VERIFIED with the
        # reason recorded — the temporary in-monolith
        # LocalVerifyBackend is gone, and verification is never
        # silently absent.
        twin_client = TwinVerifyClient(base_url=settings.twin_verifier_base_url)
    if graph is None:
        graph = get_diagnosis_graph(checkpointer)

    final = graph.invoke(
        # Per-turn scratch keys are reset explicitly: LangGraph merges
        # the input over the checkpointed state, so without this the
        # previous turn's ``result`` / ``ranked`` / ``verification``
        # would leak into this turn and the ask node would skip. The
        # durable cross-turn state is the ``state`` dict alone.
        {"state": state.model_dump(mode="json"),
         "input": latest_input, "input_kind": input_kind,
         "evidence": [], "verification_block": "",
         "ranked": None, "discriminating_test": None,
         "verification": None, "pre_verification": None,
         "result": None, "safety": None},
        config={"configurable": {
            "thread_id": f"diagnostic-session:{session.id}",
            "db": db, "session": session,
            "twin_client": twin_client, "signatures": signatures,
        }},
    )
    new_state = DiagnosticState.model_validate(final["state"])

    session.state = new_state.model_dump(mode="json")
    if new_state.current_step.type == NextStepType.conclusion:
        session.status = DiagnosticStatus.concluded
    else:
        # No escalated transition any more (fix-3): the loop never sets
        # DiagnosticStatus.escalated. The value survives only for sessions
        # escalated before fix-3.
        session.status = DiagnosticStatus.active

    db.add(session)
    db.commit()
    db.refresh(session)

    return new_state


def start_session(
    db: DBSession,
    product_id: str,
    revision_id: str | None,
    initial_symptom: str,
) -> DiagnosticSession:
    """Create a new diagnostic session and run its first turn on the initial symptom."""
    session = DiagnosticSession(
        product_id=product_id,
        revision_id=revision_id,
        status=DiagnosticStatus.active,
        state=DiagnosticState().model_dump(mode="json"),
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    # Phase 5 (steal item 8): open the downtime record for this
    # diagnosis. Best-effort — a downtime failure must never break a
    # session start.
    try:
        start_downtime(db, session)
    except Exception as exc:  # noqa: BLE001 - downtime is advisory here
        logging.getLogger(__name__).warning("diagnostics: downtime start failed: %s", exc)
    run_turn(db, session, initial_symptom, input_kind="symptom")
    return session
