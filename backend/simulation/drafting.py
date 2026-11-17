"""Phase 3D — Layer 2 LLM slot-filling (drafting workflow).

Design: ``phase3-design-v3-archetype-oracle.md`` §7 and
``phase3-layer2-drafting-workflow-v1.md``. The LLM's only roles are at
*authoring* time, and all four are slot-filling against a fixed archetype
(classify a part, extract parameters, propose fault modes, extract
cause -> symptom rows for review). No LLM call at runtime, no LLM call in
the propagation engine or the oracle.

What this module IS: the machinery around the LLM — the drafting brief
builder, the draft schema (Pydantic, structured output), the six
pre-review code checks (with the oracle as check 5), the authorization
states (enforced in code), the bounded revision loop (max two rounds),
and the review-view data. The LLM itself is a pluggable slot
(``DraftLLM``), implemented for real by ``gateway_draft_llm`` on the
existing gateway (task ``"drafting"``, the same ``complete_result``
interface diagnosis uses). No new provider, no new dependency, no new
API-key handling. Tests stub the gateway; no live LLM call happens in
the suite. The first REAL draft + the 15-minute review measurement is
still a live check for the user (it needs his LLM credentials and a
chosen first Layer 2 type).

Candidate ``0.x`` versions compile ONLY in sandbox test twins, never an
approved one. The sandbox is enforced by ``sandbox_archetype_library``:
the drafted archetype is registered in the global archetype library
only inside the context manager, and the test twin it compiles is
in-memory only, never persisted, never pinned.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol

from pydantic import BaseModel, Field

from backend.llm import gateway
from .archetypes import (
    ARCHETYPE_LIBRARY_VERSION,
    ARCHETYPES,
    QUALITATIVE_DOMAINS,
    Archetype,
)
from .binding import bind_model
from .compiler import compile_machine_model
from .lint import lint_archetype_table, validate_row
from .omnipro_proof import build_omnipro_proof_model
from .oracle import load_vocabulary, run_oracle
from .repair_actions import load_registry_causes

# ---------------------------------------------------------------------------
# The LLM slot — implemented on the existing gateway (readiness item C)
# ---------------------------------------------------------------------------

# Gateway task for drafting. The task has no entry in
# settings.model_routes, so the gateway would fall back to its default
# provider/model with a 2000-token ceiling; a full archetype JSON (ports,
# states, parameters, propagation rows, failure modes) needs more, so the
# call passes DRAFTING_MAX_TOKENS explicitly — the diagnosis ceiling.
DRAFTING_TASK = "drafting"
DRAFTING_MAX_TOKENS = 8192

DRAFTING_INSTRUCTION = (
    "You are drafting a machine-behaviour archetype for Crater.ai. "
    "Return ONLY the draft archetype as JSON matching the supplied JSON schema. "
    "Do not wrap it in code fences. No invented values: unbound is always an "
    "acceptable answer. A template condition may only reference declared ports, "
    "states and parameters. Every field needs a source citation or an explicit "
    "unbound mark. A fault proposal must name its discriminating test."
)


class DraftLLM(Protocol):
    """The LLM's slot: given a drafting brief (dict), return a draft
    archetype (dict, validated against ``DraftArchetype``).

    Implemented for real by ``gateway_draft_llm`` below, on the existing
    gateway (task ``"drafting"``) — the same ``complete_result`` interface
    diagnosis uses, with structured output against the draft schema. A
    caller that needs a stub (tests) or a different model passes its own
    ``DraftLLM`` to ``draft_archetype`` explicitly.
    """

    def __call__(self, brief: dict[str, Any]) -> dict[str, Any]:
        ...


def gateway_draft_llm(brief: dict[str, Any]) -> dict[str, Any]:
    """The real DraftLLM: the existing gateway, task ``"drafting"``.

    Structured output against the ``DraftArchetype`` schema, temperature
    0.0, exactly the way diagnosis calls the gateway. No new provider, no
    new dependency, no new API-key handling: the gateway routes by its
    configured provider/model.

    Fail-closed: no provider configured -> RuntimeError (never a
    fabricated draft); truncated output -> RuntimeError (a partial
    archetype must never reach review); unparseable output -> ValueError.
    The returned dict is schema-validated downstream by the pre-review
    gate (check 1); this function only guarantees it is a JSON object.
    """
    raw, truncated = gateway.complete_result(
        DRAFTING_TASK,
        messages=[{"role": "user", "content": (
            DRAFTING_INSTRUCTION + "\n\nDrafting brief (JSON):\n"
            + json.dumps(brief))}],
        json_schema=DraftArchetype.model_json_schema(),
        temperature=0.0,
        max_tokens=DRAFTING_MAX_TOKENS,
    )
    if raw is None:
        raise RuntimeError(
            "No LLM provider is configured for drafting (task 'drafting'); "
            "configure the provider before calling draft_archetype "
            "without an LLM.")
    if truncated:
        raise RuntimeError(
            "The drafting LLM output was truncated at the token ceiling; "
            "no draft is returned (a partial archetype must never be "
            "reviewed).")
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        try:
            parsed = json.loads(_repair_json_once(text))
        except json.JSONDecodeError as exc:
            raise ValueError(
                "The drafting LLM did not return parseable JSON; no draft "
                f"is returned (first 200 chars: {text[:200]!r})") from exc
    if not isinstance(parsed, dict):
        raise ValueError(
            "The drafting LLM did not return a JSON object; no draft is "
            "returned.")
    return parsed


def draft_archetype(brief: dict[str, Any],
                    llm: DraftLLM | None = None) -> dict[str, Any]:
    """Produce a draft archetype from a brief.

    With ``llm=None`` the gateway-backed ``gateway_draft_llm`` is used
    (the existing gateway, task ``"drafting"``). Pass ``llm`` explicitly
    for a stub (tests) or a different model.
    """
    if llm is None:
        llm = gateway_draft_llm
    return llm(brief)


# ---------------------------------------------------------------------------
# The drafting brief (v3 §7.2)
# ---------------------------------------------------------------------------

HARD_CONSTRAINTS: list[str] = [
    "No invented values: unbound is always an acceptable answer.",
    "A template condition may only reference declared ports, states and parameters.",
    "Every field needs a source citation or an explicit unbound mark.",
    "A fault proposal must name its discriminating test.",
    "A parameter value must appear in the allow-list of quantities extracted "
    "from the cited pages, or be marked unbound.",
    "The draft is a candidate only; it never pins to an approved twin.",
]

GOLD_EXAMPLE_ARCHETYPES: tuple[str, ...] = ("switch", "converter", "transmission")


class BriefCase(BaseModel):
    case_id: str
    symptom: str
    causes: list[dict[str, Any]] = Field(default_factory=list)
    table_page: int | None = None


class BriefStructuredFacts(BaseModel):
    """Only the structured facts for the case, from the canonical model:
    failure modes, quantities with page citations, procedures, and the
    topology around the symptom component. A few thousand tokens, not
    48 pages."""

    failure_modes: list[dict[str, Any]] = Field(default_factory=list)
    quantities: list[dict[str, Any]] = Field(default_factory=list)
    procedures: list[dict[str, Any]] = Field(default_factory=list)
    topology: list[dict[str, Any]] = Field(default_factory=list)


class DraftingBrief(BaseModel):
    """What the LLM receives — not the manual (v3 §7.2)."""

    version: str = "drafting-brief-v1"
    case: BriefCase
    facts: BriefStructuredFacts
    # The archetype schema as JSON schema for structured output (the same
    # mechanism v12 uses for diagnosis) — the LLM fills the schema.
    archetype_json_schema: dict[str, Any] = Field(default_factory=dict)
    # The nearest existing archetype instances, top 2 by port / parameter
    # similarity (for a contactor: the relay instance). Delta-drafting.
    nearest_archetypes: list[dict[str, Any]] = Field(default_factory=list)
    # The 3 hand-written archetype instances as gold examples.
    gold_examples: list[dict[str, Any]] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=lambda: list(HARD_CONSTRAINTS))
    drafting_mode: str = "B"
    # Mode B (human skeleton) for the first two Layer 2 instances to
    # calibrate, then D (delta-drafting). A never as the default.
    mode_note: str = ("Start Layer 2 with mode B for the first two instances "
                      "to calibrate the brief and the checks, then mode D.")


def _port_similarity(entity_ports: list[str],
                     archetype: Archetype) -> float:
    """Jaccard similarity of the port-name sets (plus parameter names).
    The nearest archetype is the one sharing the most ports/parameters."""
    arch_fields = {p.name for p in archetype.ports} | \
        {p.name for p in archetype.parameters}
    entity_fields = set(entity_ports)
    if not arch_fields and not entity_fields:
        return 0.0
    return len(arch_fields & entity_fields) / len(arch_fields | entity_fields)


def build_drafting_brief(case_id: str, *,
                         registry_path: str | Path | None = None,
                         vocabulary_path: str | Path | None = None,
                         drafting_mode: str = "B") -> DraftingBrief:
    """Build the drafting brief for one top repair case.

    One case per brief; a type serving two cases gets one brief per case
    and the drafts are merged by the gate, not by the LLM.
    """
    causes = load_registry_causes(registry_path)
    case_causes = [c for c in causes if c["case_id"] == case_id]
    if not case_causes:
        raise ValueError(f"no registry case {case_id!r}")
    symptom = case_causes[0]["case_symptom"]
    model = build_omnipro_proof_model()
    # The symptom component: the first component named by the case's causes.
    symptom_names = {name for c in case_causes for name in c["components"]}
    symptom_entity = next(
        (e for e in model.entities if e.name in symptom_names), None)
    # Structured facts: quantities with page citations + failure modes
    # from the canonical model, and the topology around the symptom
    # component (its relations, one hop). Quantities are read from the
    # model's typed field: all_facts() returns (kind, id, payload)
    # tuples, not objects.
    quantities: list[dict[str, Any]] = [{
        "entity": q.entity_id, "name": q.name,
        "value": str(q.value) if q.value is not None else None,
        "unit": q.unit,
        "source_pages": sorted({e.page for e in (q.evidence or []) if e.page}),
    } for q in (model.quantities or [])]
    failure_modes = [{
        "name": fm.name, "symptoms": list(fm.symptoms or []),
        "entity_ids": list(fm.entity_ids or []),
        "diagnostic_test": fm.diagnostic_test,
    } for fm in (model.failure_modes or [])]
    topology: list[dict[str, Any]] = []
    if symptom_entity is not None:
        for rel in model.relations or []:
            if rel.subject_id == symptom_entity.id or \
                    rel.object_id == symptom_entity.id:
                topology.append({"source_id": rel.subject_id,
                                 "target_id": rel.object_id,
                                 "relation": rel.relation_type})
    # Nearest archetypes, top 2 by port/parameter similarity to the
    # symptom component's ports. The entity's ports are plain port-name
    # strings on the proof twin.
    entity_ports = list(getattr(symptom_entity, "ports", None) or []) \
        if symptom_entity is not None else []
    ranked = sorted(
        ARCHETYPES.values(),
        key=lambda a: _port_similarity(entity_ports, a), reverse=True)
    nearest = [{
        "archetype_id": a.archetype_id,
        "similarity": round(_port_similarity(entity_ports, a), 3),
        "ports": [p.name for p in a.ports],
        "parameters": [p.name for p in a.parameters],
    } for a in ranked[:2]]
    gold = [ARCHETYPES[aid].model_dump(mode="json")
            for aid in GOLD_EXAMPLE_ARCHETYPES]
    return DraftingBrief(
        case=BriefCase(case_id=case_id, symptom=symptom,
                       causes=[{k: c[k] for k in
                                ("cause_letter", "cause", "components", "fix",
                                 "test", "fault_injection")} for c in case_causes]),
        facts=BriefStructuredFacts(
            failure_modes=failure_modes, quantities=quantities,
            procedures=[], topology=topology),
        archetype_json_schema=Archetype.model_json_schema(),
        nearest_archetypes=nearest,
        gold_examples=gold,
        drafting_mode=drafting_mode,
    )


# ---------------------------------------------------------------------------
# The draft schema (what the LLM must output — v3 §7.2 / workflow §3)
# ---------------------------------------------------------------------------


class DraftParameter(BaseModel):
    """A parameter slot: {value, source_page, source_field} OR
    {unbound: true, reason}. A value not in the allow-list is flagged,
    never silently accepted (gate check 2)."""

    name: str
    value: Any = None
    source_page: int | None = None
    source_field: str | None = None
    unbound: bool = False
    unbound_reason: str | None = None
    # The parameter's domain (from the archetype schema). An unbound
    # parameter still carries its domain — without it a boolean parameter
    # (groove_matches) would be inferred as qualitative and its rows
    # (groove_matches: True/False) would never match any enumerated
    # combo. The domain is part of the schema the LLM fills, not a value.
    domain: str | None = None
    # The archetype's nominal operating point for this parameter (e.g.
    # tension=normal for a transmission). This is part of the archetype
    # definition, not a bound model value: the compiler seeds the nominal
    # state from it when the model binds no value. Without it the nominal
    # rows never fire (a fault on a downstream component leaves the
    # feeder's tension/groove unset, so the feeder reads unknown).
    nominal_default: Any = None


class DraftPort(BaseModel):
    name: str
    direction: str
    domain: str
    unit: str | None = None
    source_page: int | None = None
    # Or an explicit inferred_from_template mark naming which gold
    # contract it was copied from.
    inferred_from_template: str | None = None
    # A required port must be present on the entity for the signature to
    # match (binding §8). An optional port is a capability the archetype
    # offers that this instance may not carry — it is part of the
    # signature for the collision check, but it does not block binding.
    required: bool = True


class DraftRow(BaseModel):
    """A propagation row: {condition, effect, source_pages}. The condition
    may only reference declared ports/states/parameters (gate check 4)."""

    row_id: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    fault: str | None = "none"
    outputs: dict[str, Any] = Field(default_factory=dict)
    next_state: str
    source_pages: list[int] = Field(default_factory=list)


class DraftFailureMode(BaseModel):
    """Fault-first (v2 §8): mandatory in every draft, not optional."""

    name: str
    fault_state: str | None = None
    parameter: str | None = None
    discriminating_test: str
    expected_trace: list[str] = Field(default_factory=list)
    source_pages: list[int] = Field(default_factory=list)


class DraftArchetype(BaseModel):
    """The LLM's structured output: an archetype draft, not free prose."""

    archetype_id: str
    human_name: str
    aliases: list[str] = Field(default_factory=list)
    ports: list[DraftPort] = Field(default_factory=list)
    states: list[str] = Field(default_factory=list)
    parameters: list[DraftParameter] = Field(default_factory=list)
    propagation_table: list[DraftRow] = Field(default_factory=list)
    failure_modes: list[DraftFailureMode] = Field(default_factory=list)
    # Candidate versions are 0.x; only approved versions pin to a twin.
    version: str = "0.1.0"
    authoring_layer: str = "layer2-llm-drafted"
    intended_entity: str | None = None
    # The entity this draft is meant to bind (by name). The binding check
    # (gate 6) verifies it binds there and nowhere it should not.
    # The archetype(s) this draft replaces for its intended entity. A
    # draft that refines an existing archetype (a gas regulator refining
    # the valve) cannot be evaluated while the existing archetype is also
    # registered — the §8 unique-match rule would make the binding
    # ambiguous and the sandbox twin would break. So the sandbox removes
    # the superseded archetype(s) for the duration of the evaluation
    # only, and restores them on exit. The draft declares what it
    # supersedes explicitly (an empty list means it is a new archetype
    # for an entity no existing archetype binds).
    supersedes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Authorization states (v3 §7.5) — enforced in code, not honorary
# ---------------------------------------------------------------------------

DRAFT_STATES = ("draft", "candidate", "in review", "approved",
                "rejected", "needs revision")

# Allowed transitions. "approved" is terminal for a version (a new version
# starts as "draft"); "rejected" is terminal. "needs revision" goes back
# to "draft" (the revision loop) — bounded by DraftingLoop, not by the
# state machine.
ALLOWED_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "draft": ("candidate", "rejected"),
    "candidate": ("in review", "rejected"),
    "in review": ("approved", "rejected", "needs revision"),
    "needs revision": ("draft", "rejected"),
    "approved": (),
    "rejected": (),
}


class ApprovalRecord(BaseModel):
    """Approval is a record: reviewer identity, timestamp, checklist hash,
    version. Only approved versions can be pinned to a twin."""

    reviewer: str
    timestamp: str
    checklist_hash: str
    version: str
    # A list, so a second approver (an OEM expert) can be added without
    # changing the schema. The approver is Nishant now.
    approvers: list[str] = Field(default_factory=list)


class DraftAuthorization(BaseModel):
    draft_id: str
    state: str = "draft"
    version: str = "0.1.0"
    history: list[dict[str, Any]] = Field(default_factory=list)
    approval: ApprovalRecord | None = None

    def transition(self, to_state: str, *,
                   reviewer: str | None = None,
                   checklist_hash: str | None = None) -> None:
        """Move to a new state. Raises ValueError on an illegal transition
        or on approving without an approval record."""
        if to_state not in ALLOWED_TRANSITIONS.get(self.state, ()):
            raise ValueError(
                f"illegal draft transition {self.state!r} -> {to_state!r}; "
                f"allowed from {self.state!r}: "
                f"{list(ALLOWED_TRANSITIONS.get(self.state, ()))}")
        if to_state == "approved":
            if reviewer is None or checklist_hash is None:
                raise ValueError(
                    "approving a draft requires a reviewer and a checklist "
                    "hash (the approval record); a bare approve is how "
                    "rubber-stamping happens.")
            self.approval = ApprovalRecord(
                reviewer=reviewer,
                timestamp=datetime.now(timezone.utc).isoformat(),
                checklist_hash=checklist_hash,
                version=self.version,
                approvers=[reviewer])
        self.history.append({"from": self.state, "to": to_state,
                             "at": datetime.now(timezone.utc).isoformat(),
                             "reviewer": reviewer})
        self.state = to_state


def archetype_to_draft(archetype: Archetype,
                       intended_entity: str | None = None,
                       version: str = "0.1.0") -> DraftArchetype:
    """Convert a hand-written archetype to a draft (for calibrating Layer 2:
    the gold examples round-trip through the draft schema, and a draft that
    copies an existing archetype's rows is the baseline the oracle check
    measures a new draft against). The parameters are marked unbound (the
    archetype's nominal defaults are not bound model values)."""
    return DraftArchetype(
        archetype_id=archetype.archetype_id,
        human_name=archetype.human_name,
        aliases=list(archetype.aliases),
        ports=[DraftPort(name=p.name, direction=p.direction, domain=p.domain,
                         unit=p.unit) for p in archetype.ports],
        states=list(archetype.states),
        parameters=[DraftParameter(name=p.name, unbound=True,
                                   unbound_reason="archetype nominal default, "
                                                  "not a bound model value",
                                   domain=p.domain,
                                   nominal_default=p.nominal_default)
                    for p in archetype.parameters],
        propagation_table=[DraftRow(
            row_id=r.row_id, inputs=dict(r.inputs), fault=r.fault,
            outputs=dict(r.outputs), next_state=r.next_state,
            source_pages=[]) for r in archetype.propagation_table],
        failure_modes=[DraftFailureMode(
            name=f.name, fault_state=f.fault_state, parameter=f.parameter,
            discriminating_test=f.discriminating_test,
            expected_trace=[], source_pages=[])
            for f in archetype.failure_modes],
        version=version,
        authoring_layer="layer1-hand-written (calibration draft)",
        intended_entity=intended_entity)


# ---------------------------------------------------------------------------
# The sandbox (candidate 0.x versions compile ONLY in sandbox test twins)
# ---------------------------------------------------------------------------


@contextmanager
def sandbox_archetype_library(draft: DraftArchetype) -> Iterator[Archetype]:
    """Register the drafted archetype in the library ONLY inside this
    context, then remove it. The test twin compiled inside is in-memory
    only, never persisted, never pinned to an approved twin.

    A draft that supersedes an existing archetype (``draft.supersedes``)
    has that archetype removed for the duration of the context, so the
    §8 unique-match rule can bind the intended entity to the draft and
    the oracle can evaluate it. The superseded archetype is restored on
    exit, so the approved library is never changed by an evaluation.

    Not thread-safe (it mutates the global library); it is only for the
    offline drafting gate, never a runtime path.
    """
    for superseded_id in draft.supersedes:
        if superseded_id not in ARCHETYPES:
            raise ValueError(
                f"draft {draft.archetype_id!r} supersedes {superseded_id!r}, "
                f"which is not in the archetype library")
    archetype = Archetype(
        archetype_id=draft.archetype_id,
        human_name=draft.human_name,
        aliases=list(draft.aliases),
        ports=[{"name": p.name, "direction": p.direction, "domain": p.domain,
                "unit": p.unit, "required": p.required} for p in draft.ports],
        states=list(draft.states),
        parameters=[{"name": p.name,
                     "domain": _parameter_domain(p),
                     "values": _parameter_values(p),
                     # The archetype's nominal operating point (declared
                     # on the draft) seeds the nominal state; a bound
                     # value serves as the nominal when no explicit
                     # nominal_default is given.
                     "nominal_default": (p.nominal_default
                                         if p.nominal_default is not None
                                         else (p.value if not p.unbound else None)),
                     "has_nominal_default": (p.nominal_default is not None
                                             or not p.unbound),
                     "source": f"p{p.source_page}" if p.source_page else ""}
                    for p in draft.parameters],
        propagation_table=[{"row_id": r.row_id, "inputs": dict(r.inputs),
                            "fault": r.fault, "outputs": dict(r.outputs),
                            "next_state": r.next_state,
                            "sources": [f"p{pg}" for pg in r.source_pages]}
                           for r in draft.propagation_table],
        failure_modes=[{"name": f.name, "fault_state": f.fault_state,
                        "parameter": f.parameter,
                        "discriminating_test": f.discriminating_test}
                       for f in draft.failure_modes],
        version=draft.version,
        provenance={"author": "Layer 2 (LLM-drafted, candidate)",
                    "authoring_layer": "layer2-llm-drafted",
                    "review": "candidate — not approved"},
    )
    previous = ARCHETYPES.get(draft.archetype_id)
    superseded = {aid: ARCHETYPES.pop(aid) for aid in draft.supersedes}
    ARCHETYPES[draft.archetype_id] = archetype
    try:
        yield archetype
    finally:
        if previous is None:
            ARCHETYPES.pop(draft.archetype_id, None)
        else:
            ARCHETYPES[draft.archetype_id] = previous
        ARCHETYPES.update(superseded)


def _parameter_domain(parameter: DraftParameter) -> str:
    """The parameter's domain. The draft schema carries it (an unbound
    parameter still has a domain — a boolean parameter is boolean even
    when its value is unbound). Only when the draft omits the domain is
    it inferred from the value (or "qualitative" when unbound)."""
    if parameter.domain:
        return parameter.domain
    if parameter.unbound or parameter.value is None:
        return "qualitative"
    if isinstance(parameter.value, bool):
        return "boolean"
    if isinstance(parameter.value, str) and \
            parameter.value in QUALITATIVE_DOMAINS.get("electrical", []):
        return "electrical"
    return "qualitative"


def _parameter_values(parameter: DraftParameter) -> list[Any]:
    domain = QUALITATIVE_DOMAINS.get(_parameter_domain(parameter), [])
    if parameter.unbound or parameter.value is None:
        return list(domain)
    return list(domain) if parameter.value in domain else [parameter.value]


# ---------------------------------------------------------------------------
# The six pre-review checks (v3 §7.3) — code, zero LLM judgement
# ---------------------------------------------------------------------------

MAX_SCHEMA_REPAIR_RETRIES = 1
MAX_REVISION_ROUNDS = 2


class GateCheck(BaseModel):
    check: str
    passed: bool
    # "reject" | "flag" | "pass". A flag means the draft continues to
    # review with the flag on it (check 2 provenance, check 5 O3).
    outcome: str = "pass"
    reasons: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)


class PreReviewGateResult(BaseModel):
    draft_id: str
    # "candidate" (all six pass, or pass with flags) or "rejected".
    verdict: str
    checks: list[GateCheck] = Field(default_factory=list)
    # Fields quarantined by check 2 (value provenance): flagged, visible,
    # never silently dropped.
    quarantined_fields: list[str] = Field(default_factory=list)
    # Check 5's oracle vectors, attached so the failure is visible.
    oracle_vectors: dict[str, Any] = Field(default_factory=dict)


def _repair_json_once(raw: str) -> str:
    """One schema-repair retry: strip trailing commas before } or ].
    Malformed output gets one repair retry, then the draft is rejected."""
    import re
    return re.sub(r",\s*([}\]])", r"\1", raw)


def check_schema(draft_raw: str | dict[str, Any]) -> tuple[DraftArchetype | None, GateCheck]:
    """Check 1 — schema validation. Malformed -> one repair retry, then reject."""
    if isinstance(draft_raw, dict):
        try:
            return DraftArchetype.model_validate(draft_raw), GateCheck(
                check="1-schema", passed=True)
        except Exception as exc:  # noqa: BLE001 - the reason is the evidence
            return None, GateCheck(check="1-schema", passed=False,
                                   outcome="reject",
                                   reasons=[f"schema validation failed: {exc}"])
    try:
        return DraftArchetype.model_validate_json(draft_raw), GateCheck(
            check="1-schema", passed=True)
    except Exception:  # noqa: BLE001 - one repair retry, then reject
        pass
    try:
        repaired = _repair_json_once(draft_raw)
        return DraftArchetype.model_validate_json(repaired), GateCheck(
            check="1-schema", passed=True,
            reasons=["schema validation passed after one repair retry"])
    except Exception as exc:  # noqa: BLE001 - the reason is the evidence
        return None, GateCheck(check="1-schema", passed=False,
                               outcome="reject",
                               reasons=[f"schema validation failed after one "
                                        f"repair retry: {exc}"])


def _allow_list(brief: DraftingBrief) -> set[Any]:
    """The allow-list of quantities extracted from the cited pages:
    every quantity value in the brief's structured facts."""
    return {q.get("value") for q in brief.facts.quantities
            if q.get("value") is not None}


def check_value_provenance(draft: DraftArchetype,
                           brief: DraftingBrief) -> GateCheck:
    """Check 2 — value provenance. Every parameter value must be in the
    allow-list or marked unbound. A non-allow-list value quarantines that
    field (flagged, visible, never silently dropped) and the draft
    continues to review WITH the flag. This check never rejects."""
    allow = _allow_list(brief)
    quarantined: list[str] = []
    for param in draft.parameters:
        if param.unbound:
            continue
        if param.value not in allow:
            quarantined.append(param.name)
    if not quarantined:
        return GateCheck(check="2-value-provenance", passed=True)
    return GateCheck(
        check="2-value-provenance", passed=True, outcome="flag",
        reasons=[f"parameter {name!r} has a value outside the allow-list "
                 f"of quantities extracted from the cited pages; quarantined "
                 f"(flagged, visible, never silently dropped)" for name in quarantined],
        evidence={"quarantined_fields": quarantined})


def check_port_signature_collision(draft: DraftArchetype) -> GateCheck:
    """Check 3 — port-signature collision. The draft's port signature
    identical to an existing archetype with no distinguishing evidence
    -> auto-reject ("indistinguishable from relay")."""
    draft_signature = {(p.name, p.direction, p.domain) for p in draft.ports}
    for archetype in ARCHETYPES.values():
        existing = {(p.name, p.direction, p.domain) for p in archetype.ports}
        if draft_signature and draft_signature == existing:
            # Distinguishing evidence: a parameter or failure mode the
            # existing archetype does not have.
            existing_params = {p.name for p in archetype.parameters}
            existing_modes = {m.name for m in archetype.failure_modes}
            draft_params = {p.name for p in draft.parameters}
            draft_modes = {m.name for m in draft.failure_modes}
            if draft_params - existing_params or draft_modes - existing_modes:
                continue
            return GateCheck(
                check="3-port-signature-collision", passed=False,
                outcome="reject",
                reasons=[f"port signature identical to existing archetype "
                         f"{archetype.archetype_id!r} with no distinguishing "
                         f"evidence (indistinguishable from "
                         f"{archetype.archetype_id})"])
    return GateCheck(check="3-port-signature-collision", passed=True)


def check_template_executability(draft: DraftArchetype) -> GateCheck:
    """Check 4 — template executability. Every row's condition references
    only declared ports/states/parameters; every effect is a declared
    state or port value in the domain's set."""
    declared_inputs = {p.name for p in draft.ports if p.direction == "in"} | \
        {p.name for p in draft.parameters}
    declared_outputs = {p.name for p in draft.ports if p.direction == "out"}
    declared_states = set(draft.states)
    problems: list[str] = []
    for row in draft.propagation_table:
        for field_name in row.inputs:
            if field_name not in declared_inputs and field_name != "fault":
                problems.append(f"row {row.row_id}: condition references "
                                f"undeclared field {field_name!r}")
        for field_name, value in row.outputs.items():
            if field_name not in declared_outputs:
                problems.append(f"row {row.row_id}: effect references "
                                f"undeclared output {field_name!r}")
                continue
            domain_name = next(
                (p.domain for p in draft.ports if p.name == field_name), "")
            domain = QUALITATIVE_DOMAINS.get(domain_name, [])
            if value not in domain and value != "unknown":
                problems.append(f"row {row.row_id}: effect {field_name}="
                                f"{value!r} is not a declared value of its "
                                f"domain")
        if row.next_state not in declared_states and row.next_state != "unknown":
            problems.append(f"row {row.row_id}: next_state "
                            f"{row.next_state!r} is not a declared state")
    if problems:
        return GateCheck(check="4-template-executability", passed=False,
                         outcome="reject", reasons=problems)
    return GateCheck(check="4-template-executability", passed=True)


def check_oracle(draft: DraftArchetype, brief: DraftingBrief,
                 vocabulary_path: str | Path | None = None) -> GateCheck:
    """Check 5 — oracle run (replaces the old trace test + binding test).
    The drafted instance is compiled into a sandbox test twin for its
    case, the oracle runs O1–O5 on that case's rows, and the pass/fail +
    vectors are attached. A draft that fails O2 on its own case is
    rejected with the vectors attached; a draft that passes O2 but fails
    O3 is flagged for the reviewer (not auto-rejected — O3 failures are
    often a missing test model, not a wrong behaviour)."""
    vocab_path = vocabulary_path or (
        Path.home() / "workspace" / "goals" /
        "crater-ai-diagnosis-and-machine-model-workflow-improvement" /
        "files" / "oracle_vocabulary_v1.json")
    vocab = load_vocabulary(str(vocab_path))
    case_rows = [r for r in vocab.get("rows", [])
                 if r.get("case_id") == brief.case.case_id]
    if not case_rows:
        return GateCheck(check="5-oracle", passed=False, outcome="reject",
                         reasons=[f"no oracle rows for case "
                                  f"{brief.case.case_id!r}"])
    # The sandbox test twin: the proof twin, with the drafted archetype
    # registered only inside the context manager. In-memory only, never
    # persisted, never pinned to an approved twin.
    from .omnipro_proof import observable_paths, build_test_models
    with sandbox_archetype_library(draft):
        model = build_omnipro_proof_model()
        definition, _warnings = compile_machine_model(
            model, name=f"sandbox test twin for draft {draft.archetype_id}")
        # The oracle needs the case's rows as OracleRow objects; reuse the
        # vocabulary loader's expansion by running the full oracle set and
        # filtering to this case.
        from .oracle import OracleRow
        rows = []
        for index, raw in enumerate(case_rows):
            rows.append(OracleRow(
                row_id=f"oracle-row-{index + 1:02d}",
                case_id=raw.get("case_id") or "",
                case_rank=0, symptom=brief.case.symptom,
                cause=raw.get("cause") or "",
                remedy=raw.get("remedy") or "",
                remedy_source="", source_pages=[],
                category=raw.get("category") or "",
                fault=raw.get("fault"),
                observables=dict(raw.get("observables") or {}),
                tests=dict(raw.get("tests") or {}),
                basis=raw.get("basis") or "",
                flags=list(raw.get("flags") or [])))
        report = run_oracle(
            definition.model_dump(mode="json"), rows, vocab.get("cases") or {},
            observable_paths=observable_paths(definition),
            test_models=build_test_models(definition))
    vectors = {
        "headline": report.headline,
        "per_case": report.per_case.get(brief.case.case_id),
        # Only scored rows carry O2/O3 checks; not-scored rows (settings,
        # technique, cross-reference) are not twin-expressible and do not
        # count against the draft.
        "o2_failures": [r["row_id"] for r in report.to_dict()["rows"]
                        if "O2" in r["checks"] and not r["checks"]["O2"]["passed"]],
        "o3_failures": [r["row_id"] for r in report.to_dict()["rows"]
                        if "O3" in r["checks"] and not r["checks"]["O3"]["passed"]],
    }
    o2_failed = vectors["o2_failures"]
    o3_failed = vectors["o3_failures"]
    if o2_failed:
        return GateCheck(check="5-oracle", passed=False, outcome="reject",
                         reasons=[f"O2 fails on its own case for rows "
                                  f"{o2_failed}; the draft's behaviour does "
                                  f"not reproduce the manual's rows"],
                         evidence={"oracle_vectors": vectors})
    if o3_failed:
        return GateCheck(check="5-oracle", passed=True, outcome="flag",
                         reasons=[f"O2 passes but O3 fails for rows "
                                  f"{o3_failed}; flagged for the reviewer "
                                  f"(the discriminating test needs work — "
                                  f"often a missing test model, not a wrong "
                                  f"behaviour)"],
                         evidence={"oracle_vectors": vectors})
    return GateCheck(check="5-oracle", passed=True,
                     evidence={"oracle_vectors": vectors})


def check_binding(draft: DraftArchetype, brief: DraftingBrief) -> GateCheck:
    """Check 6 — binding check. The §8 rule runs on the case's entities:
    the draft must bind its intended entity and must not bind entities it
    should not. A draft that also binds the relay when it should bind
    only the contactor is a reject, with the evidence attached."""
    if draft.intended_entity is None:
        return GateCheck(check="6-binding", passed=False, outcome="reject",
                         reasons=["the draft names no intended entity"])
    with sandbox_archetype_library(draft):
        model = build_omnipro_proof_model()
        bindings = bind_model(model)
    by_name = {rec["entity_name"]: rec for rec in bindings.values()}
    intended = by_name.get(draft.intended_entity)
    if intended is None or intended["archetype_id"] != draft.archetype_id:
        return GateCheck(
            check="6-binding", passed=False, outcome="reject",
            reasons=[f"the draft does not bind its intended entity "
                     f"{draft.intended_entity!r} (bound to "
                     f"{(intended or {}).get('archetype_id')!r})"],
            evidence={"bindings": {k: v["archetype_id"]
                                   for k, v in by_name.items()}})
    wrong = [name for name, rec in by_name.items()
             if rec["archetype_id"] == draft.archetype_id
             and name != draft.intended_entity]
    if wrong:
        return GateCheck(
            check="6-binding", passed=False, outcome="reject",
            reasons=[f"the draft also binds {wrong}, which it should not "
                     f"(it should bind only {draft.intended_entity!r})"],
            evidence={"bindings": {k: v["archetype_id"]
                                   for k, v in by_name.items()},
                      "wrongly_bound": wrong})
    return GateCheck(check="6-binding", passed=True,
                     evidence={"bindings": {k: v["archetype_id"]
                                            for k, v in by_name.items()}})


def run_pre_review_gate(draft_raw: str | dict[str, Any],
                        brief: DraftingBrief,
                        draft_id: str,
                        vocabulary_path: str | Path | None = None
                        ) -> tuple[DraftArchetype | None, PreReviewGateResult]:
    """Run the six pre-review checks in order. A draft that fails any
    check never reaches the reviewer. Returns (draft, result) — the draft
    is None when check 1 rejects it."""
    draft, schema_check = check_schema(draft_raw)
    checks: list[GateCheck] = [schema_check]
    if draft is None:
        return None, PreReviewGateResult(draft_id=draft_id, verdict="rejected",
                                         checks=checks)
    checks.append(check_value_provenance(draft, brief))
    checks.append(check_port_signature_collision(draft))
    checks.append(check_template_executability(draft))
    # A rejection on 3 or 4 stops the gate (the oracle and binding checks
    # need a well-formed, non-colliding draft to run against).
    if any(c.outcome == "reject" for c in checks):
        return draft, PreReviewGateResult(
            draft_id=draft_id, verdict="rejected", checks=checks,
            quarantined_fields=[f for c in checks
                                for f in c.evidence.get("quarantined_fields", [])])
    oracle_check = check_oracle(draft, brief, vocabulary_path)
    checks.append(oracle_check)
    binding_check = check_binding(draft, brief)
    checks.append(binding_check)
    rejected = any(c.outcome == "reject" for c in checks)
    return draft, PreReviewGateResult(
        draft_id=draft_id,
        verdict="rejected" if rejected else "candidate",
        checks=checks,
        quarantined_fields=[f for c in checks
                            for f in c.evidence.get("quarantined_fields", [])],
        oracle_vectors=next(
            (c.evidence.get("oracle_vectors", {}) for c in checks
             if c.check == "5-oracle"), {}))


# ---------------------------------------------------------------------------
# The bounded revision loop (v3 §7.5)
# ---------------------------------------------------------------------------


class DraftingLoop(BaseModel):
    """One draft; one repair retry for schema only; then review. A reviewer
    requesting revision gets a revision brief with the structured rejection
    reasons, and the re-draft covers only the flagged fields. Maximum two
    revision rounds; after that a human writes it (Layer 1 fallback)."""

    draft_id: str
    revision_rounds: int = 0
    max_revision_rounds: int = MAX_REVISION_ROUNDS
    state: DraftAuthorization = Field(default_factory=lambda:
                                      DraftAuthorization(draft_id=""))

    def request_revision(self, gate_result: PreReviewGateResult,
                         reviewer: str) -> dict[str, Any]:
        """Build the revision brief: the structured rejection reasons
        (which field, which check/reason). Raises when the revision cap
        is reached — after that a human writes it."""
        if self.revision_rounds >= self.max_revision_rounds:
            raise ValueError(
                f"draft {self.draft_id!r} has used {self.revision_rounds} "
                f"revision rounds (max {self.max_revision_rounds}); a human "
                f"writes it now (Layer 1 fallback).")
        flagged_fields: list[str] = []
        for check in gate_result.checks:
            flagged_fields.extend(
                check.evidence.get("quarantined_fields", []))
            if check.outcome in ("reject", "flag"):
                flagged_fields.extend(
                    [r for r in check.reasons])
        self.revision_rounds += 1
        return {
            "draft_id": self.draft_id,
            "revision_round": self.revision_rounds,
            "max_revision_rounds": self.max_revision_rounds,
            "flagged_fields": flagged_fields,
            "checks": [{"check": c.check, "outcome": c.outcome,
                        "reasons": c.reasons} for c in gate_result.checks],
            "instruction": ("Re-draft ONLY the flagged fields, not the whole "
                            "contract."),
            "reviewer": reviewer,
        }
