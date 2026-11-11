"""
DiagnosticAgent: runs one turn of the plan.md §6 workflow.

A "turn" = technician provides a new symptom/observation/measurement ->
agent retrieves evidence -> agent updates hypotheses -> agent picks the
next step (question / action / conclusion / escalate).

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
  escalate the session or lose the hypotheses. Escalation stays for the
  real guards only (turn limit, sustained low confidence, no provider
  configured at all);
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
from backend.diagnostics.prompts import DIAGNOSTIC_SYSTEM_PROMPT
from backend.diagnostics.schema import DiagnosticState, EvidenceRef, Hypothesis, NextStep, NextStepType
from backend.llm import gateway
from backend.retrieval.hybrid import retrieve_revision_context

logger = logging.getLogger(__name__)

_MAX_TURNS_BEFORE_FORCED_ESCALATION = 8
_LOW_CONFIDENCE_ESCALATION_THRESHOLD = 0.35
_TURNS_BEFORE_LOW_CONFIDENCE_CHECK = 5

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
                "type": {"type": "string", "enum": ["question", "action", "conclusion", "escalate"]},
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


def _default_question_step(state: DiagnosticState, rationale: str) -> dict:
    if state.hypotheses:
        cause = state.hypotheses[0].cause
        content = (
            f"What do you observe when you check the leading cause — {cause}? "
            "Report the result and I will update the ranking."
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
    failure must never escalate the session or lose the hypotheses."""
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


def _run_llm_turn(state: DiagnosticState, evidence: list[EvidenceRef], knowledge_block: str) -> dict:
    evidence_block = "\n".join(
        f"[{e.chunk_id}] (p.{e.page_number}, {e.source_document}): {e.content[:800]}" for e in evidence
    ) or "(no relevant evidence retrieved)"

    prompt = DIAGNOSTIC_SYSTEM_PROMPT.format(
        state_json=_compact_state_json(state),
        evidence_block=evidence_block,
        knowledge_block=knowledge_block,
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
        # No provider configured — degrade to a safe, honest escalation
        # rather than fabricate reasoning. This is the one case where
        # escalate is still the answer: there is no model to reason.
        return {
            "hypotheses": [h.model_dump() for h in state.hypotheses],
            "eliminated_hypotheses": [h.model_dump() for h in state.eliminated_hypotheses],
            "overall_confidence": state.confidence,
            "next_step": {
                "type": "escalate",
                "content": "Diagnostic reasoning requires a configured LLM provider.",
                "rationale": "No LLM configured to reason over evidence.",
                "safety_notes": [],
            },
        }

    parsed = _parse_lenient_json(raw)
    if (parsed is None or truncated) and raw:
        # Truncated output (finish reason hit the ceiling) parses the
        # same way as malformed output, so it gets the same one repair
        # attempt instead of an immediate escalation.
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


def _apply_forced_safeguards(state: DiagnosticState, result: dict) -> dict:
    """
    Enforce plan.md §25 escalation rules even if the LLM's own judgement
    doesn't: too many turns, or sustained low confidence, force an escalate
    step. These are cheap, deterministic guardrails around a probabilistic
    component — the agent can choose to escalate earlier, but not later
    than these limits.
    """
    if state.turns_taken + 1 >= _MAX_TURNS_BEFORE_FORCED_ESCALATION:
        result["next_step"] = {
            "type": NextStepType.escalate.value,
            "content": "This case has run for many turns without a confident conclusion — escalating to a human expert.",
            "rationale": "Turn limit reached without sufficient confidence.",
            "safety_notes": [],
        }
    elif (
        state.turns_taken + 1 >= _TURNS_BEFORE_LOW_CONFIDENCE_CHECK
        and result.get("overall_confidence", 0.0) < _LOW_CONFIDENCE_ESCALATION_THRESHOLD
    ):
        result["next_step"] = {
            "type": NextStepType.escalate.value,
            "content": "Confidence remains low after several diagnostic turns — escalating to a human expert rather than guessing further.",
            "rationale": f"Confidence {result.get('overall_confidence', 0.0):.2f} below threshold after {state.turns_taken + 1} turns.",
            "safety_notes": [],
        }
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
    result = _run_llm_turn(state, evidence, knowledge_block)
    result = _apply_forced_safeguards(state, result)

    state.hypotheses = [Hypothesis.model_validate(h) for h in result.get("hypotheses", [])]
    state.eliminated_hypotheses = [Hypothesis.model_validate(h) for h in result.get("eliminated_hypotheses", [])]
    state.confidence = float(result.get("overall_confidence", 0.0))
    state.current_step = NextStep.model_validate(result["next_step"])
    state.turns_taken += 1

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
    elif state.current_step.type == NextStepType.escalate:
        session.status = DiagnosticStatus.escalated
    else:
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

    run_turn(db, session, initial_symptom, input_kind="symptom")
    return session
