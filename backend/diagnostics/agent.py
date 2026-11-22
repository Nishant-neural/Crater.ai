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
  inside state_json, and the component/failure-mode block is filtered
  to what is relevant to this turn instead of dumping all ~300
  components every time.
"""
from __future__ import annotations

import ast
import json
import logging
import re

from sqlalchemy.orm import Session as DBSession

from backend.db.models import Component, DiagnosticSession, DiagnosticStatus, FailureMode
from backend.diagnostics.backtracking import _texts_match, unavailable_with_attempt
from backend.diagnostics.prompts import DIAGNOSTIC_SYSTEM_PROMPT
from backend.diagnostics.schema import DiagnosticState, EvidenceRef, Hypothesis, NextStep, NextStepType
from backend.diagnostics.twin_verification import (
    cannot_express_cause,
    conclusion_cause,
    render_verification_block,
    run_tournament_for_state,
    verdict_for_cause,
)
from backend.diagnostics.downtime import start_downtime
from backend.llm import gateway
from backend.retrieval.hybrid import retrieve_revision_context

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
_MAX_COMPONENT_LINES = 25
_MAX_FAILURE_MODE_LINES = 12
_MAX_KNOWLEDGE_ITEM_CHARS = 1200

# JSON Schema handed to the provider's structured-output mechanism, so
# "return ONLY JSON" is enforced by the API instead of only asked for
# in prose. Mirrors the shape in diagnostics/prompts.py exactly.
_DIAGNOSIS_OUTPUT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "hypotheses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "cause": {"type": "string"},
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
                    "rationale": {"type": "string"},
                    "confidence": {"type": "number"},
                    "supporting_evidence_chunk_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["cause", "rationale", "confidence"],
            },
        },
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
    "required": ["hypotheses", "eliminated_hypotheses", "overall_confidence", "next_step"],
}

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _query_tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(text.lower()) if len(t) >= 3}


def _ranked_lines(scored: list[tuple[float, str, str]], limit: int) -> list[str]:
    """Best-first by relevance score, then by name for determinism."""
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [line for _score, _name, line in scored[:limit]]


def _load_product_knowledge(db: DBSession, revision_id: str | None, query: str = "") -> str:
    """Pull structured Component/FailureMode rows for this revision into a compact text block.

    Filtered to this turn: every component/failure mode used to be dumped
    (~300 components / ~18k chars on the OMNIPRO revision), most of it
    unrelated to the symptom, which buried the relevant rows and bloated
    the prompt. Now only the highest-overlap rows are sent, plus any
    component named in a live hypothesis cause (so the model never loses
    a hypothesis it is already tracking).
    """
    if not revision_id:
        return "(no revision specified — component/failure-mode knowledge unavailable)"

    components = db.query(Component).filter(Component.revision_id == revision_id).all()
    failure_modes = db.query(FailureMode).filter(FailureMode.revision_id == revision_id).all()
    # Approved expert knowledge is part of the revision-scoped Product Brain.
    # Draft/rejected knowledge is deliberately invisible to the diagnostic agent.
    from backend.db.models import ExpertKnowledge, KnowledgeStatus, KnowledgeVersion
    approved_versions = db.query(KnowledgeVersion).filter(
        KnowledgeVersion.revision_id == revision_id,
        KnowledgeVersion.status == KnowledgeStatus.approved,
    ).all()
    approved_version_ids = [v.id for v in approved_versions]
    expert_knowledge = []
    if approved_version_ids:
        expert_knowledge = db.query(ExpertKnowledge).filter(
            ExpertKnowledge.knowledge_version_id.in_(approved_version_ids)
        ).all()

    tokens = _query_tokens(query)
    lines: list[str] = []
    if components:
        scored: list[tuple[float, str, str]] = []
        for c in components:
            text = f"{c.name} {c.function or ''} {c.location_description or ''}"
            overlap = len(tokens & _query_tokens(text)) if tokens else 0
            line = (
                f"- {c.name}: {(c.function or 'function unknown')[:120]} "
                f"({(c.location_description or 'location unknown')[:120]})"
            )
            scored.append((float(overlap), c.name, line))
        lines.append("Components (most relevant to this turn):")
        lines.extend(_ranked_lines(scored, _MAX_COMPONENT_LINES))
    if failure_modes:
        scored = []
        for f in failure_modes:
            text = f"{f.symptom} {' '.join(f.possible_causes)}"
            overlap = len(tokens & _query_tokens(text)) if tokens else 0
            scored.append((float(overlap), f.symptom, f"- Symptom: {f.symptom} | Possible causes: {', '.join(f.possible_causes)}"))
        lines.append("Known failure modes (most relevant to this turn):")
        lines.extend(_ranked_lines(scored, _MAX_FAILURE_MODE_LINES))
    if expert_knowledge:
        lines.append("Approved senior-engineer knowledge:")
        for k in expert_knowledge:
            scope = ", ".join(k.applicable_revisions or []) or "revision scope from interview"
            lines.append(
                f"- {k.title} [{k.knowledge_type.value}; confidence={k.confidence:.2f}; scope={scope}] "
                f"Symptom={k.symptom or 'n/a'} Condition={k.condition or 'n/a'} "
                f"Action={k.action or 'n/a'} Expected={k.expected_observation or 'n/a'} "
                f"Safety={'; '.join(k.safety_notes or []) or 'none stated'}"
            )

    return "\n".join(lines) if lines else "(no structured component/failure-mode/expert knowledge extracted yet for this revision)"


def _build_query(state: DiagnosticState, latest_input: str) -> str:
    """Retrieval query = symptoms + latest observation, not the whole state history (keeps it focused)."""
    parts = state.symptoms[-2:] + [latest_input]
    return " ".join(p for p in parts if p)


def _knowledge_context(knowledge) -> str:
    if not knowledge:
        return "(no canonical machine knowledge retrieved)"
    lines = []
    for item in knowledge:
        # Payloads carry long evidence lists; the evidence itself is
        # already in the evidence block, so strip it here and cap the
        # remainder — a full payload dump used to add ~2k chars per item.
        payload = {k: v for k, v in item.payload.items() if k != "evidence"}
        text = json.dumps(payload, ensure_ascii=False)
        if len(text) > _MAX_KNOWLEDGE_ITEM_CHARS:
            text = text[:_MAX_KNOWLEDGE_ITEM_CHARS] + "… (truncated)"
        lines.append(f"[{item.kind}:{item.item_id}; path={item.retrieval_path}; score={item.score:.2f}] {text}")
    return "\n".join(lines)


def _compact_state_json(state: DiagnosticState) -> str:
    """State for the prompt, without the full evidence text.

    The old code dumped state.model_dump_json(indent=2), which embedded
    the FULL content of every evidence chunk (~50k chars for 8 chunks on
    crater-8) even though the evidence block already carries the same
    chunks truncated to 800 chars each. The model needs the evidence
    ids/pages to cite, not a second copy of the text — so evidence is
    reduced to references and the timeline is capped to the last 10
    entries. No indent: JSON does not need pretty-printing to be read.
    """
    data = state.model_dump(mode="json")
    data["evidence"] = [
        {
            "chunk_id": e.get("chunk_id"),
            "page_number": e.get("page_number"),
            "source_document": e.get("source_document"),
        }
        for e in data.get("evidence", [])
    ]
    data["timeline"] = data.get("timeline", [])[-10:]
    return json.dumps(data, ensure_ascii=False)


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
    asked_causes: set[str] = set()
    asked_tests: set[str] = set()
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


def _normalize_result(state: DiagnosticState, parsed: dict) -> dict | None:
    """Validate a parsed turn the way ingestion validates a chunk: keep
    every good hypothesis, quarantine only the malformed ones, coerce
    percent confidences, and never let a missing/malformed next_step
    crash the turn — it becomes a question instead."""
    if not isinstance(parsed, dict):
        return None

    def _valid_hypotheses(raw_list, default_confidence: float) -> list[dict]:
        valid: list[dict] = []
        if not isinstance(raw_list, list):
            return valid
        for item in raw_list:
            if not isinstance(item, dict):
                logger.warning("diagnosis: dropping non-object hypothesis: %.200s", repr(item))
                continue
            item = dict(item)
            item["confidence"] = _coerce_confidence(item.get("confidence"), default_confidence)
            try:
                valid.append(Hypothesis.model_validate(item).model_dump())
            except Exception as exc:
                logger.warning("diagnosis: dropping malformed hypothesis %r: %s", item.get("cause"), exc)
        return valid

    hypotheses = _valid_hypotheses(parsed.get("hypotheses"), state.confidence)
    eliminated = _valid_hypotheses(parsed.get("eliminated_hypotheses"), 0.0)
    overall = _coerce_confidence(
        parsed.get("overall_confidence"),
        max([h["confidence"] for h in hypotheses], default=state.confidence),
    )
    try:
        next_step = NextStep.model_validate(parsed.get("next_step")).model_dump()
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
    return {
        "hypotheses": hypotheses,
        "eliminated_hypotheses": eliminated,
        "overall_confidence": overall,
        "next_step": next_step,
    }


def _repair_json(raw: str) -> str | None:
    """One repair call: ask the model to fix its own JSON, not to
    re-diagnose. Returns the raw repaired text or None."""
    try:
        repaired, _truncated = gateway.complete_result(
            "diagnosis",
            messages=[{"role": "user", "content": (
                "The following is a diagnostic turn that is not valid JSON. "
                "Return ONLY the corrected valid JSON object in exactly the same shape, "
                "without adding, removing, or changing any hypothesis, cause, or confidence. "
                "Do not wrap it in code fences.\n\n" + raw[:12000]
            )}],
            json_schema=_DIAGNOSIS_OUTPUT_SCHEMA,
            temperature=0.0,
        )
        return repaired
    except Exception as exc:
        logger.warning("diagnosis: JSON repair call failed: %s", exc)
        return None


def _run_llm_turn(state: DiagnosticState, evidence: list[EvidenceRef], knowledge_block: str,
                  verification_block: str = "") -> dict:
    evidence_block = "\n".join(
        f"[{e.chunk_id}] (p.{e.page_number}, {e.source_document}): {e.content[:800]}" for e in evidence
    ) or "(no relevant evidence retrieved)"

    prompt = DIAGNOSTIC_SYSTEM_PROMPT.format(
        state_json=_compact_state_json(state),
        evidence_block=evidence_block,
        knowledge_block=knowledge_block,
        verification_block=verification_block or "(twin verification unavailable for this turn)",
    )
    try:
        raw, truncated = gateway.complete_result(
            "diagnosis",
            messages=[{"role": "user", "content": prompt}],
            json_schema=_DIAGNOSIS_OUTPUT_SCHEMA,
            temperature=_DIAGNOSIS_TEMPERATURE,
        )
    except Exception as exc:
        # A provider/transport failure (429, network) is not a diagnosis
        # and must not kill the session either — degrade to a question.
        logger.warning("diagnosis: LLM call failed: %s", exc)
        return _degraded_result(
            state,
            f"The diagnostic model call failed ({exc}); no hypotheses were changed — please report the most informative observation and I will retry the reasoning.",
        )
    if not raw:
        # No provider configured — degrade to a question, never fabricate
        # reasoning and never escalate (escalation was removed from the
        # loop, fix-3). The session stays active with its hypotheses
        # intact; the technician is told plainly what is missing.
        return _degraded_result(
            state,
            "Diagnostic reasoning requires a configured LLM provider, and none is configured — "
            "no hypotheses were changed. Configure a provider, or report the most informative "
            "observation and I will retry the reasoning once one is available.",
        )

    parsed = _parse_lenient_json(raw)
    if (parsed is None or truncated) and raw:
        # Truncated output (finish reason hit the ceiling) parses the
        # same way as malformed output, so it gets the same one repair
        # attempt instead of ending the turn.
        if truncated:
            logger.warning("diagnosis: output truncated at the ceiling; attempting one JSON repair")
        repaired = _repair_json(raw)
        if repaired:
            parsed = _parse_lenient_json(repaired)
    if parsed is None:
        logger.warning(
            "diagnosis: unparseable model output after lenient parse + one repair "
            "(prompt_chars=%d, truncated=%s): %.2000s",
            len(prompt), truncated, raw,
        )
        return _degraded_result(
            state,
            "The diagnostic model returned an unparseable response — no hypotheses were changed, so please report the most informative observation and I will update the ranking instead of guessing.",
        )

    normalized = _normalize_result(state, parsed)
    if normalized is None:
        logger.warning("diagnosis: parsed output was not an object; degrading to a question")
        return _degraded_result(
            state,
            "The diagnostic model returned a response in an unexpected shape — no hypotheses were changed.",
        )
    return normalized


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
                           row_reason: str) -> tuple[dict, str]:
    """Build the ending step for a concluded cause the twin cannot
    express, plus the reason string for the timeline.

    The step keeps the loop's ending shape (a question-type step carrying
    the unavailable-with-attempt, the same shape as the turn-bound
    ending): there is no unavailable status in ``DiagnosticStatus``, and
    a conclusion step would falsely mark the session concluded. The
    content itself asks no question -- the ending is the answer.
    The discriminating test is None here, as in the turn-bound ending:
    no signature source exists in the agent layer, so the attempt
    carries the ranked guesses and what is missing, and the test line
    appears only when one is known.
    """
    hypotheses = state.hypotheses or []
    guesses = [{"cause": h.cause, "confidence": h.confidence,
                "evidence": h.rationale, "source": "session_hypothesis"}
               for h in hypotheses]
    reason = f"The twin cannot express this cause ({cause_text}): {row_reason}"
    attempt = unavailable_with_attempt(
        guesses, None,
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
            guesses, None,
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


def _verification_block_for_turn(db: DBSession, session: DiagnosticSession,
                                 state: DiagnosticState) -> str:
    """Run the early tournament on the ranking carried into this turn and
    render the block the LLM reasons over before it picks its next step.

    When the twin runs, the re-ranked hypotheses and a pre-turn
    ``early_tournament`` timeline entry are recorded (turn = the turn
    about to run), so the timeline shows the verdicts the model saw.
    When the twin cannot run, the block says so plainly — the model is
    told verification is unavailable instead of the turn silently
    proceeding without it.
    """
    asked_causes, asked_tests = _asked_causes_and_tests(state)
    summary = run_tournament_for_state(
        db, session, state,
        repair_name=(state.current_step.content if state.current_step else "") or "",
        rerank=False)  # verdicts for the prompt only: the gate / post-turn
    # run owns the authoritative re-rank, so one hypothesis set is never
    # re-ranked twice in a turn (see run_tournament_for_state).
    if summary.get("available"):
        state.timeline.append({
            "turn": state.turns_taken + 1, "kind": "early_tournament",
            "input": "hypothesis tournament (early, pre-turn)",
            "available": True, "counts": summary["counts"],
            "results": [{"cause": r["cause"], "status": r["status"], "verdict": r["verdict"],
                         "reason": r["reason"],
                         "cannot_express": r.get("cannot_express", False)} for r in summary["results"]],
            "step_type": "early_tournament",
            "step": "Hypothesis tournament (early, pre-turn): " + ", ".join(
                f"{k} {v}" for k, v in summary["counts"].items() if v),
        })
    return render_verification_block(summary, asked_causes=asked_causes,
                                     asked_tests=asked_tests)


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


def _apply_conclusion_gate(db: DBSession, session: DiagnosticSession,
                           state: DiagnosticState, result: dict) -> dict:
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
    summary = run_tournament_for_state(
        db, session, state, repair_name=step.get("content") or "")
    if not summary.get("available"):
        reason = summary["reason"]
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
        ending_step, attempt_reason = _cannot_express_ending(state, cause.cause, row_reason)
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
) -> DiagnosticState:
    """
    Run one diagnostic turn: record the technician's input, retrieve
    evidence, ask the LLM to update hypotheses and pick the next step,
    apply deterministic safety guardrails, persist, and return the new state.
    """
    state = DiagnosticState.model_validate(session.state)

    if input_kind == "symptom":
        state.symptoms.append(latest_input)
    elif input_kind == "measurement":
        state.measurements.append(latest_input)
    else:
        state.observations.append(latest_input)

    query = _build_query(state, latest_input)
    # One retrieval call per turn, not two: retrieve_revision_context
    # already returns the chunks AND the canonical knowledge, so the old
    # separate hybrid_retrieve call (whose chunks were then discarded)
    # was a second rerank LLM call per turn for nothing.
    retrieved, knowledge = (
        retrieve_revision_context(db, query, product_id=session.product_id, revision_id=session.revision_id)
        if session.revision_id else ([], [])
    )
    # Preserve provenance at the boundary so the UI can trace every claim
    # back to the source document/page without inventing citations.
    from backend.db.models import Document
    evidence = []
    for r in retrieved:
        doc = db.get(Document, r.document_id)
        evidence.append(EvidenceRef(
            chunk_id=r.chunk_id,
            content=r.content,
            source_document=doc.title if doc else r.document_id,
            page_number=r.page_number,
        ))
    state.evidence = evidence  # replace with this turn's evidence; history stays in symptoms/observations

    # Relevance filter for the legacy block: this turn's query plus the
    # causes of the hypotheses currently being tracked.
    filter_query = query + " " + " ".join(h.cause for h in state.hypotheses)
    legacy_block = _load_product_knowledge(db, session.revision_id, filter_query)
    knowledge_block = legacy_block + "\n\nCanonical knowledge + graph retrieval:\n" + _knowledge_context(knowledge)
    # Fix-3: the tournament runs BEFORE the LLM turn as well as after it,
    # so the model reasons over the verified ranking (and over what was
    # already asked) before it picks its next step — the concluding turn
    # used to be the one turn whose verdicts the model never saw.
    verification_block = _verification_block_for_turn(db, session, state)
    result = _run_llm_turn(state, evidence, knowledge_block, verification_block)

    state.hypotheses = [Hypothesis.model_validate(h) for h in result.get("hypotheses", [])]
    state.eliminated_hypotheses = [Hypothesis.model_validate(h) for h in result.get("eliminated_hypotheses", [])]
    state.confidence = float(result.get("overall_confidence", 0.0))
    state.current_step = NextStep.model_validate(result["next_step"])
    # Fix-3: verify-before-conclusion — a conclusion stands only when the
    # tournament has run on this turn's hypotheses and its cause is
    # VALIDATED; otherwise it is demoted to a question (unless the cause
    # is one the twin cannot express at all, in which case the gate ends
    # the session at once with the attempt). Then the deterministic
    # guards (escalate-to-question mapping, turn-bound unavailable
    # ending), then the no-repeat rewrite for questions that re-ask an
    # already-checked cause. The turn-bound ending and the
    # cannot-express ending are never rewritten: they are the ending,
    # not a question to vary.
    result = _apply_conclusion_gate(db, session, state, result)
    result = _apply_forced_safeguards(state, result)
    if not result.get("turn_bound_reached") and not result.get("cannot_express_ending"):
        result = _apply_no_repeat_gate(state, result)
    state.current_step = NextStep.model_validate(result["next_step"])
    state.turns_taken += 1

    ending_reason = None
    ending_step = None
    if result.get("turn_bound_reached"):
        ending_reason = "Turn limit reached without a twin-validated conclusion"
        ending_step = ("Diagnosis ended without a twin-validated conclusion: ranked best "
                       "guesses and what is missing were given instead.")
    elif result.get("cannot_express_ending"):
        ending_reason = (result.get("cannot_express_reason")
                         or "The twin cannot express the concluded cause")
        ending_step = ("Diagnosis ended: the twin cannot express the concluded cause; "
                       "ranked best guesses and what is missing were given instead.")
    if ending_reason:
        state.timeline.append({
            "turn": state.turns_taken,
            "kind": "diagnosis_unavailable",
            "input": latest_input,
            "available": False,
            "verdict": None,
            "reason": ending_reason,
            "step_type": "diagnosis_unavailable",
            "step": ending_step,
        })

    # A compact audit trail for the technician-facing timeline. This is
    # deliberately event-level, not chain-of-thought.
    state.timeline.append({
        "turn": state.turns_taken,
        "kind": input_kind,
        "input": latest_input,
        "hypotheses": [
            {"cause": h.cause, "confidence": h.confidence, "status": "active"}
            for h in state.hypotheses
        ],
        "step_type": state.current_step.type.value,
        "step": state.current_step.content,
    })

    session.state = state.model_dump(mode="json")
    if state.current_step.type == NextStepType.conclusion:
        session.status = DiagnosticStatus.concluded
    else:
        # No escalated transition any more (fix-3): the loop never sets
        # DiagnosticStatus.escalated. The value survives only for sessions
        # escalated before fix-3.
        session.status = DiagnosticStatus.active

    db.add(session)
    db.commit()
    db.refresh(session)

    return state


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
