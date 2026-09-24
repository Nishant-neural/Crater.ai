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
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session as DBSession

from backend.db.models import Component, DiagnosticSession, DiagnosticStatus, FailureMode
from backend.diagnostics.prompts import DIAGNOSTIC_SYSTEM_PROMPT
from backend.diagnostics.schema import DiagnosticState, EvidenceRef, Hypothesis, NextStep, NextStepType
from backend.llm import gateway
from backend.retrieval.hybrid import retrieve_revision_context

_MAX_TURNS_BEFORE_FORCED_ESCALATION = 8
_LOW_CONFIDENCE_ESCALATION_THRESHOLD = 0.35
_TURNS_BEFORE_LOW_CONFIDENCE_CHECK = 5


def _load_product_knowledge(db: DBSession, revision_id: str | None) -> str:
    """Pull structured Component/FailureMode rows for this revision into a compact text block."""
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

    lines: list[str] = []
    if components:
        lines.append("Components:")
        for c in components:
            lines.append(f"- {c.name}: {c.function or 'function unknown'} ({c.location_description or 'location unknown'})")
    if failure_modes:
        lines.append("Known failure modes:")
        for f in failure_modes:
            lines.append(f"- Symptom: {f.symptom} | Possible causes: {', '.join(f.possible_causes)}")
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
    lines=[]
    for item in knowledge:
        payload=item.payload
        lines.append(f"[{item.kind}:{item.item_id}; path={item.retrieval_path}; score={item.score:.2f}] {json.dumps(payload, ensure_ascii=False)}")
    return "\n".join(lines)

def _run_llm_turn(state: DiagnosticState, evidence: list[EvidenceRef], knowledge_block: str) -> dict:
    evidence_block = "\n".join(
        f"[{e.chunk_id}] (p.{e.page_number}, {e.source_document}): {e.content[:800]}" for e in evidence
    ) or "(no relevant evidence retrieved)"

    prompt = DIAGNOSTIC_SYSTEM_PROMPT.format(
        state_json=state.model_dump_json(indent=2),
        evidence_block=evidence_block,
        knowledge_block=knowledge_block,
    )
    raw = gateway.complete("diagnosis",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2000,
    )
    if not raw:
        # No provider configured — degrade to a safe, honest placeholder rather than fabricate reasoning.
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

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {
            "hypotheses": [h.model_dump() for h in state.hypotheses],
            "eliminated_hypotheses": [h.model_dump() for h in state.eliminated_hypotheses],
            "overall_confidence": state.confidence,
            "next_step": {
                "type": "escalate",
                "content": "The diagnostic model returned an unparseable response — escalating rather than guessing.",
                "rationale": "LLM output was not valid JSON.",
                "safety_notes": [],
            },
        }


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
    retrieved = hybrid_retrieve(db, query, product_id=session.product_id, revision_id=session.revision_id)
    evidence = [
        EvidenceRef(chunk_id=r.chunk_id, content=r.content, page_number=r.page_number)
        for r in retrieved
    ]
    state.evidence = evidence  # replace with this turn's evidence; history stays in symptoms/observations

    _, knowledge = retrieve_revision_context(db, query, product_id=session.product_id, revision_id=session.revision_id) if session.revision_id else ([], [])
    legacy_block = _load_product_knowledge(db, session.revision_id)
    knowledge_block = legacy_block + "\n\nCanonical knowledge + graph retrieval:\n" + _knowledge_context(knowledge)
    result = _run_llm_turn(state, evidence, knowledge_block)
    result = _apply_forced_safeguards(state, result)

    state.hypotheses = [Hypothesis.model_validate(h) for h in result.get("hypotheses", [])]
    state.eliminated_hypotheses = [Hypothesis.model_validate(h) for h in result.get("eliminated_hypotheses", [])]
    state.confidence = float(result.get("overall_confidence", 0.0))
    state.current_step = NextStep.model_validate(result["next_step"])
    state.turns_taken += 1

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
