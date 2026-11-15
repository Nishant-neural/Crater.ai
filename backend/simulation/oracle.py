"""Phase 3A oracle harness — the manual as the test suite (design v3 §6, §10 3A).

The oracle scores a compiled twin against the deterministic parser's
troubleshooting rows (p42/p43 → 45 cause rows), joined to the separately
reviewed observable/test vocabulary (``oracle_vocabulary_v1.json``).
It proves the twin matches the manual's claims, not that the machine
behaves that way; field data is the only real ground truth.

What this module is and is not
------------------------------
- Pure logic: no LLM, no network, no I/O except loading the vocabulary
  JSON the caller passes in. Deterministic: same twin + same oracle set
  -> same report, byte for byte (JSON dumped with sorted keys).
- Fault injection reuses the existing engine (``DigitalTwinEngine``,
  read-only use — ``engine.py`` is untouched) and the existing
  experiment idea (baseline -> fault -> read state) from
  ``verification.py``'s design, without importing its diagnosis paths.
- There is NO propagation engine here. That is 3B. What 3A has instead
  is a clearly labelled stub (``_PROPAGATION_STUB``): the twin's own
  explicit transitions are the only behaviour that can fire, and a
  fault the twin cannot express fails as unmappable instead of being
  bridged by invented edges, values, or verdicts.
- Two things the twin cannot do in 3A are reported, never worked
  around: (1) observable ports (``wire_out@feeder``) have no mapping to
  twin state fields until archetype binding exists in 3B — the caller
  supplies ``observable_paths`` (port -> ``component_id.field``) and
  every unmapped port fails its checks with reason
  ``observable_unmapped``; (2) discriminating tests have no model until
  3B — the caller supplies ``test_models`` (test id -> callable) and
  every unmodelled test reads ``unmodelled``.

Checks (v3 §6.3, with Amendments Fix 3 — case 1's required O5 chain
starts at the motor, ``motor -> feeder -> wire``):
- O1 nominal (machine-level gate): no fault injected, every observable
  must read its nominal value. A fail invalidates the headline rate.
- O2 fault -> symptom: with the fault injected, the observable vector
  must equal the documented vector for that cause row.
- O3 discriminability: for every pair of twin-expressible causes
  sharing a symptom, the combined actual vectors (observables +
  modelled test outcomes) must differ on at least one entry.
- O4 no spurious output: observables whose component is not the
  faulted component or downstream of it (by declared topology edges)
  must stay nominal.
- O5 fixpoint + trace richness: the engine step settles (its bounded
  fixed-point loop — the real propagation settle check arrives with
  the 3B propagation engine) and the fault-run trace contains at
  least as many state changes as the case's required chain is long;
  every consecutive pair of the required chain must be connected by
  a declared topology edge, else ``topology_gap``.

Cause -> fault mapping: the registry's reviewed ``fault_injection``
field (``repair_cases_omnipro.json``) is the mapping — the binding,
not the LLM, maps a cause row to its injection (Amendments item 5,
13 Nov 2026). ``build_oracle_set`` takes the registry mapping and
uses it as each row's fault; a disagreement with the vocabulary's
``fault`` field is reported as a problem, never silently resolved.
This is what resolves Phase 0 fix 2 (adjustment-cause mapping) — by
replacing the tournament's name matching for these causes.

Recorded here, NOT built in 3A (Amendments items 2–4, 6 — 3B work):
the engine-as-propagator revision (compiler emits one generic
conditional transition per row; the engine's fixpoint propagates);
the no-retraction/totality requirement on rows (the engine never
retracts effects, so rows must be mutually exclusive and total, and
the oracle lint checks exclusion/totality/settling); the verifier's
healthy-value fix (compiler v3 runs the nominal fixpoint once and
stores it as initial_state); and the seed boolean -> parameter
translation (``tension_ok=true`` -> ``tension=normal``). In 3A the
harness uses the twin and the seed as-is and records the seed
vocabulary mismatch (``tension_ok`` boolean vs ``tension``
low/normal/high) as a gap, not a failure to fix in the harness.


A row passes only if O2+O3+O4+O5 all pass. Rows in the settings /
technique / cross-reference categories are NOT scored (outcome
``not_scored``) — the headline rate is over the 35 twin-expressible
rows, with the denominator breakdown (35/5/4/1) always shown. An
unmappable twin-expressible cause fails with reason and stays in the
denominator — never skipped, never guessed.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from backend.simulation.engine import DigitalTwinEngine
from backend.simulation.schema import DigitalTwinDefinition

ORACLE_VERSION = "phase3a-v1"

CATEGORIES = ("twin_expressible", "settings", "technique", "cross_reference")

# Reason codes. A row may carry several; the gap list counts distinct rows
# per reason, topology gaps first.
UNMAPPABLE_CAUSE = "unmappable_cause"
NO_TRANSITION_EXPRESSES_FAULT = "no_transition_expresses_fault"
TOPOLOGY_GAP = "topology_gap"
OBSERVABLE_UNMAPPED = "observable_unmapped"
UNMODELLED_TEST = "unmodelled_test"
O1_NOMINAL_FAIL = "o1_nominal_fail"
O2_MISMATCH = "o2_mismatch"
O3_INDISTINGUISHABLE = "o3_indistinguishable"
O4_SPURIOUS_OUTPUT = "o4_spurious_output"
O5_THIN_TRACE = "o5_thin_trace"
O5_NO_SETTLE = "o5_no_settle"
NOT_SCORED = "not_scored"

# 3A propagation stub marker. The harness evaluates a fault by injecting
# it into the twin's own explicit transitions and reading state back.
# There is no qualitative propagation in 3A: no archetype tables, no
# fixpoint over topology, no inferred edges. Anything the twin's own
# transitions cannot express is reported as a gap, not simulated.
_PROPAGATION_STUB = "phase3a-stub: explicit transitions only; no propagation engine (3B)"


def normalize_text(value: Any) -> str:
    """Join-key normalisation: lowercase, whitespace collapsed, trailing
    period stripped, curly apostrophe folded. Used for (symptom, cause)
    matching between parser output and the vocabulary."""
    text = re.sub(r"\s+", " ", str(value or "").replace("’", "'").replace("‘", "'"))
    return text.strip().rstrip(".").strip().lower()


def _evidence_pages(evidence: Any) -> list[int]:
    pages: list[int] = []
    for item in evidence or []:
        page = item.page if hasattr(item, "page") else (item or {}).get("page")
        if isinstance(page, int) and page not in pages:
            pages.append(page)
    return sorted(pages)


def _failure_mode_fields(fm: Any) -> dict[str, Any]:
    if isinstance(fm, dict):
        return {
            "name": fm.get("name") or "",
            "possible_causes": list(fm.get("possible_causes") or []),
            "repair_procedure_id": fm.get("repair_procedure_id"),
            "evidence": fm.get("evidence") or [],
        }
    return {
        "name": getattr(fm, "name", "") or "",
        "possible_causes": list(getattr(fm, "possible_causes", []) or []),
        "repair_procedure_id": getattr(fm, "repair_procedure_id", None),
        "evidence": getattr(fm, "evidence", []) or [],
    }


def expand_cause_rows(failure_modes: Any, procedures: Any = None) -> list[dict[str, Any]]:
    """Expand deterministic-parser failure modes into one dict per cause.

    This is the parser-level expansion only: symptom, cause, source
    pages, and the linked repair procedure's steps as the remedy. The
    procedure steps on p42/p43 fragment across pdfplumber table rows,
    so the remedy here is informational and the vocabulary join
    replaces it with the case registry's remedy (see
    ``build_oracle_set``); no oracle check reads the remedy.
    """
    proc_by_id: dict[str, list[str]] = {}
    for proc in procedures or []:
        if isinstance(proc, dict):
            proc_by_id[str(proc.get("id") or "")] = list(proc.get("steps") or [])
        else:
            proc_by_id[str(getattr(proc, "id", "") or "")] = list(getattr(proc, "steps", []) or [])
    rows: list[dict[str, Any]] = []
    for fm in failure_modes or []:
        fields = _failure_mode_fields(fm)
        steps = proc_by_id.get(str(fields["repair_procedure_id"] or ""), [])
        for cause in fields["possible_causes"]:
            rows.append({
                "symptom": fields["name"],
                "cause": cause,
                "remedy": " ".join(s for s in steps if s),
                "remedy_source": "parser_procedure",
                "source_pages": _evidence_pages(fields["evidence"]),
            })
    return rows


def load_vocabulary(path: str | Path) -> dict[str, Any]:
    """Load the oracle vocabulary JSON. No defaults are invented here:
    a missing or malformed file raises, loudly."""
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def validate_vocabulary(vocab: dict[str, Any]) -> list[str]:
    """Validate the vocabulary's shape. Returns the list of errors;
    an empty list means valid. Pure check — never repairs or guesses."""
    errors: list[str] = []
    if not isinstance(vocab, dict):
        return ["vocabulary is not a JSON object"]
    cases = vocab.get("cases") or {}
    tests = vocab.get("tests") or {}
    rows = vocab.get("rows") or []
    if not vocab.get("version"):
        errors.append("vocabulary has no version")
    if not cases:
        errors.append("vocabulary has no cases")
    if not rows:
        errors.append("vocabulary has no rows")
    seen: set[tuple[str, str]] = set()
    for index, row in enumerate(rows):
        where = f"row #{index + 1} ({row.get('case_id')}/{str(row.get('cause'))[:40]})"
        case = cases.get(row.get("case_id") or "")
        if case is None or case == "":
            errors.append(f"{where}: unknown case_id")
            continue
        key = (row.get("case_id") or "", normalize_text(row.get("cause")))
        if key in seen:
            errors.append(f"{where}: duplicate (case, cause)")
        seen.add(key)
        if row.get("category") not in CATEGORIES:
            errors.append(f"{where}: bad category {row.get('category')!r}")
        if set(row.get("observables") or {}) != set(case.get("observables") or []):
            errors.append(f"{where}: observables do not match the case's observable set")
        if set(row.get("tests") or {}) != set(case.get("tests") or []):
            errors.append(f"{where}: tests do not match the case's test set")
        for test_id, outcome in (row.get("tests") or {}).items():
            allowed = (tests.get(test_id) or {}).get("outcomes") or []
            if outcome not in allowed and outcome != "unmodelled":
                errors.append(f"{where}: test {test_id} outcome {outcome!r} not in {allowed}")
        if row.get("category") == "twin_expressible":
            fault = row.get("fault") or {}
            if not fault.get("component") or not fault.get("field") or "value" not in fault:
                errors.append(f"{where}: twin-expressible row without a complete fault injection")
        elif row.get("fault") is not None:
            errors.append(f"{where}: non-twin-expressible row carries a fault injection")
    return errors


@dataclass
class OracleRow:
    row_id: str
    case_id: str
    case_rank: int
    symptom: str
    cause: str
    remedy: str
    remedy_source: str
    source_pages: list[int]
    category: str
    fault: dict[str, Any] | None
    observables: dict[str, Any]
    tests: dict[str, Any]
    basis: str = ""
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "row_id": self.row_id, "case_id": self.case_id, "case_rank": self.case_rank,
            "symptom": self.symptom, "cause": self.cause, "remedy": self.remedy,
            "remedy_source": self.remedy_source, "source_pages": list(self.source_pages),
            "category": self.category, "fault": self.fault,
            "observables": dict(self.observables), "tests": dict(self.tests),
            "basis": self.basis, "flags": list(self.flags),
        }


def load_fault_injections(path: str | Path) -> dict[tuple[str, str], dict[str, Any] | None]:
    """Load the registry's reviewed ``fault_injection`` field.

    Returns a mapping keyed by (case_id, normalized registry cause
    text) -> the injection dict, or None for causes with no injection
    (settings / technique / cross-reference). This is the oracle's
    cause-to-fault mapping (Amendments item 5): the binding, not the
    LLM, maps a cause row to its injection.
    """
    with open(path, encoding="utf-8") as handle:
        registry = json.load(handle)
    mapping: dict[tuple[str, str], dict[str, Any] | None] = {}
    for case in registry.get("cases") or []:
        for cause in case.get("causes") or []:
            mapping[(case.get("id") or "", normalize_text(cause.get("cause")))] = (
                cause.get("fault_injection")
            )
    return mapping


def fault_injection_to_fault(injection: dict[str, Any] | None) -> dict[str, Any] | None:
    """Translate a registry ``fault_injection`` into the harness's fault
    shape: ``{component, field, value, kind}``. ``{instance, parameter,
    value}`` -> a parameter fault on the instance; ``{instance,
    fault_state}`` -> a fault-state fault on the instance. None stays
    None (no injection for this cause)."""
    if injection is None:
        return None
    if "parameter" in injection:
        return {"component": injection.get("instance"), "field": injection.get("parameter"),
                "value": injection.get("value"), "kind": "parameter"}
    return {"component": injection.get("instance"), "field": "fault",
            "value": injection.get("fault_state"), "kind": "fault_state"}


def build_oracle_set(
    expanded_rows: list[dict[str, Any]], vocab: dict[str, Any],
    fault_injections: dict[tuple[str, str], dict[str, Any] | None] | None = None,
) -> tuple[list[OracleRow], list[str]]:
    """Join parser cause rows to the vocabulary.

    Returns (oracle rows, problems). Problems are never silent: a parser
    row with no vocabulary entry becomes a row in category ``unmapped``
    (it stays in the denominator and fails as unmappable), and a
    vocabulary row with no parser row is reported as a problem (the
    oracle's ground truth is the parser, never the vocabulary alone).
    The remedy is taken from the vocabulary (case registry), because
    the parser's solutions column fragments on p42/p43.
    When ``fault_injections`` (the registry mapping) is supplied, each
    row's fault comes from the registry; a disagreement with the
    vocabulary's ``fault`` field is a problem. Without it, the
    vocabulary's ``fault`` field is used (tests / standalone runs).
    """
    problems: list[str] = []
    cases = vocab.get("cases") or {}
    symptom_to_case = {
        normalize_text(case.get("symptom")): case_id for case_id, case in cases.items()
    }
    vocab_by_key = {
        (row.get("case_id") or "", normalize_text(row.get("cause"))): row
        for row in vocab.get("rows") or []
    }
    oracle_rows: list[OracleRow] = []
    matched_keys: set[tuple[str, str]] = set()
    for index, raw in enumerate(expanded_rows):
        case_id = symptom_to_case.get(normalize_text(raw.get("symptom")))
        key = (case_id or "", normalize_text(raw.get("cause")))
        vrow = vocab_by_key.get(key)
        if case_id is None or vrow is None:
            problems.append(
                f"parser row #{index + 1} has no vocabulary entry: "
                f"symptom={raw.get('symptom')!r} cause={raw.get('cause')!r}"
            )
            oracle_rows.append(OracleRow(
                row_id=f"oracle-row-{index + 1:02d}", case_id=case_id or "unknown",
                case_rank=0, symptom=str(raw.get("symptom") or ""),
                cause=str(raw.get("cause") or ""),
                remedy=str(raw.get("remedy") or ""), remedy_source="parser_procedure",
                source_pages=list(raw.get("source_pages") or []),
                category="unmapped", fault=None, observables={}, tests={},
                basis="no vocabulary entry; unmappable by construction",
            ))
            continue
        matched_keys.add(key)
        fault = vrow.get("fault")
        if fault_injections is not None:
            registry_key = (case_id, normalize_text(vrow.get("registry_cause") or vrow.get("cause")))
            if registry_key not in fault_injections:
                problems.append(
                    f"vocabulary row has no registry fault_injection entry: "
                    f"case={case_id} cause={vrow.get('cause')!r}"
                )
                fault = None
            else:
                registry_fault = fault_injection_to_fault(fault_injections[registry_key])
                if registry_fault != fault:
                    problems.append(
                        f"fault disagreement for case={case_id} "
                        f"cause={vrow.get('cause')!r}: registry "
                        f"{fault_injections[registry_key]!r} vs vocabulary {fault!r}"
                    )
                fault = registry_fault
        oracle_rows.append(OracleRow(
            row_id=f"oracle-row-{index + 1:02d}", case_id=case_id,
            case_rank=int(cases[case_id].get("case_rank") or 0),
            symptom=str(raw.get("symptom") or ""),
            cause=str(raw.get("cause") or ""),
            remedy=str(vrow.get("remedy") or ""), remedy_source="case_registry",
            source_pages=list(raw.get("source_pages") or []),
            category=str(vrow.get("category") or ""),
            fault=fault,
            observables=dict(vrow.get("observables") or {}),
            tests=dict(vrow.get("tests") or {}),
            basis=str(vrow.get("basis") or ""),
            flags=list(vrow.get("flags") or []),
        ))
    for key, vrow in vocab_by_key.items():
        if key not in matched_keys:
            problems.append(
                f"vocabulary row has no parser row (ground truth is the parser): "
                f"case={key[0]} cause={vrow.get('cause')!r}"
            )
    return oracle_rows, problems


@dataclass
class CheckResult:
    passed: bool
    reasons: list[str]
    expected: Any = None
    actual: Any = None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "reasons": list(self.reasons),
                "expected": self.expected, "actual": self.actual, "detail": self.detail}


@dataclass
class RowResult:
    row: OracleRow
    outcome: str  # "pass" | "fail" | "not_scored"
    checks: dict[str, CheckResult]
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {"row_id": self.row.row_id, "case_id": self.row.case_id,
                "cause": self.row.cause, "category": self.row.category,
                "outcome": self.outcome,
                "checks": {name: result.to_dict() for name, result in sorted(self.checks.items())},
                "reasons": list(self.reasons)}


@dataclass
class OracleReport:
    oracle_version: str
    twin_name: str
    twin_components: int
    twin_transitions: int
    topology_edges: int
    o1: CheckResult
    rows: list[RowResult]
    denominator: dict[str, int]
    headline: dict[str, Any]
    per_case: dict[str, dict[str, Any]]
    discriminability: dict[str, Any]
    gap_list: list[dict[str, Any]]
    problems: list[str]
    propagation: str = _PROPAGATION_STUB

    def to_dict(self) -> dict[str, Any]:
        return {
            "oracle_version": self.oracle_version, "twin_name": self.twin_name,
            "twin_components": self.twin_components,
            "twin_transitions": self.twin_transitions,
            "topology_edges": self.topology_edges,
            "propagation": self.propagation,
            "o1_nominal_gate": self.o1.to_dict(),
            "rows": [row.to_dict() for row in self.rows],
            "denominator": dict(sorted(self.denominator.items())),
            "headline": self.headline,
            "per_case": {key: self.per_case[key] for key in sorted(self.per_case)},
            "discriminability": self.discriminability,
            "gap_list": self.gap_list,
            "problems": list(self.problems),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)


def _read_path(state: dict[str, Any], path: str) -> Any:
    """Read ``component_id.field`` (or a bare signal key) from engine state."""
    if "." in path:
        component_id, field_name = path.split(".", 1)
        return (state.get("components") or {}).get(component_id, {}).get(field_name)
    return (state.get("signals") or {}).get(path)


def _trace_change_count(trace: list[str]) -> int:
    """State changes in an engine trace: lines recording old -> new."""
    return sum(1 for line in trace or [] if " → " in line)


def _topology_edges(definition: DigitalTwinDefinition) -> tuple[list[tuple[str, str]], list[str]]:
    """Declared topology edges as (subject component id, object component id).

    Resolves by component id first, then by unique component name. An
    edge that resolves neither way is reported, never invented.
    """
    component_ids = {c.id for c in definition.components}
    by_name: dict[str, list[str]] = defaultdict(list)
    for component in definition.components:
        by_name[normalize_text(component.name)].append(component.id)
    edges: list[tuple[str, str]] = []
    unresolved: list[str] = []
    for relation in (definition.metadata or {}).get("topology") or []:
        if not isinstance(relation, dict):
            continue
        subject = relation.get("subject_id") or relation.get("entity_id")
        obj = relation.get("object_id")
        if subject not in component_ids:
            candidates = by_name.get(normalize_text(relation.get("subject_name")))
            subject = candidates[0] if candidates and len(candidates) == 1 else None
        if obj not in component_ids:
            candidates = by_name.get(normalize_text(relation.get("object_name")))
            obj = candidates[0] if candidates and len(candidates) == 1 else None
        if subject is None or obj is None:
            unresolved.append(
                f"topology edge unresolvable: {relation.get('subject_name')} "
                f"-> {relation.get('object_name')} ({relation.get('relation_type')})"
            )
            continue
        edges.append((subject, obj))
    return edges, unresolved


def _downstream(start: str, edges: list[tuple[str, str]]) -> set[str]:
    adjacency: dict[str, list[str]] = defaultdict(list)
    for subject, obj in edges:
        adjacency[subject].append(obj)
    seen = {start}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        for nxt in adjacency.get(current, []):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def run_oracle(
    definition: DigitalTwinDefinition | dict[str, Any],
    oracle_rows: list[OracleRow],
    vocab_cases: dict[str, Any],
    *,
    observable_paths: dict[str, str] | None = None,
    test_models: dict[str, Callable[[dict[str, Any]], Any]] | None = None,
    problems: list[str] | None = None,
) -> OracleReport:
    """Run O1–O5 against one compiled twin. Pure: no LLM, no network.

    ``observable_paths`` maps vocabulary observable ports to twin state
    paths (``component_id.field``). ``test_models`` maps test ids to
    callables taking ``{"state", "trace", "fault", "row"}`` and returning
    the predicted outcome. Anything not supplied reads as unmapped /
    unmodelled — never guessed.
    """
    if isinstance(definition, dict):
        definition = DigitalTwinDefinition.model_validate(definition)
    observable_paths = dict(observable_paths or {})
    test_models = dict(test_models or {})

    by_name: dict[str, list[Any]] = defaultdict(list)
    for component in definition.components:
        by_name[normalize_text(component.name)].append(component)
    edges, unresolved_edges = _topology_edges(definition)
    edge_set = set(edges)

    def resolve_component(name: str) -> tuple[Any | None, str]:
        candidates = by_name.get(normalize_text(name), [])
        if len(candidates) == 1:
            return candidates[0], ""
        if not candidates:
            return None, f"no component named {name!r} in this twin"
        return None, f"{len(candidates)} components named {name!r} in this twin; ambiguous"

    # ---- O1: nominal gate (machine-level, one run, no fault) ----
    nominal_engine = DigitalTwinEngine(definition)
    nominal_engine.step()
    nominal_state = nominal_engine.state
    nominal_ports: dict[str, Any] = {}
    for case in vocab_cases.values():
        for port, value in (case.get("nominal") or {}).items():
            if port in nominal_ports and nominal_ports[port] != value:
                nominal_ports[port] = "CONFLICT"
            else:
                nominal_ports[port] = value
    o1_reasons: list[str] = []
    o1_actual: dict[str, Any] = {}
    o1_unmapped: list[str] = []
    for port in sorted(nominal_ports):
        path = observable_paths.get(port)
        if path is None:
            # 3B clarification of O1's semantics (design v3 §6.3: "Fail =
            # the twin predicts a symptom with no fault"): an unmapped
            # port is a coverage gap, not a hallucination — the twin
            # predicts nothing about a port it does not model. It is
            # recorded here as unmapped coverage, not an O1 failure.
            # The gap list already tracks observable_unmapped per row.
            o1_unmapped.append(port)
            o1_actual[port] = None
            continue
        value = _read_path(nominal_state, path)
        o1_actual[port] = value
        if value is None:
            o1_reasons.append(f"{OBSERVABLE_UNMAPPED}: {port} reads as no value at {path}")
        elif nominal_ports[port] == "CONFLICT":
            o1_reasons.append(f"nominal conflict for {port} across cases")
        elif value != nominal_ports[port]:
            o1_reasons.append(
                f"{O1_NOMINAL_FAIL}: {port} reads {value!r}, nominal is {nominal_ports[port]!r}"
            )
    if not any(port in observable_paths for port in nominal_ports):
        # Nothing mapped at all: the twin claims no observable to check,
        # so it cannot pass the nominal gate (this preserves the 3A
        # baseline exactly — crater-10 has no mapping, O1 fails).
        o1_reasons.append(
            f"{OBSERVABLE_UNMAPPED}: no nominal observable is mapped to this twin; "
            "the nominal gate cannot pass on a twin that claims nothing")
    o1_detail = "No fault injected. Every mapped observable must read its nominal value."
    if o1_unmapped:
        o1_detail += (" Unmapped nominal ports (coverage, not a hallucination): "
                      + ", ".join(sorted(o1_unmapped)) + ".")
    o1 = CheckResult(
        passed=not o1_reasons, reasons=o1_reasons,
        expected=nominal_ports, actual=o1_actual,
        detail=o1_detail,
    )

    # ---- Per-row fault runs (actual vectors first; O3 needs all of them) ----
    per_row: dict[str, dict[str, Any]] = {}
    for row in oracle_rows:
        if row.category != "twin_expressible":
            continue
        case = vocab_cases.get(row.case_id) or {}
        fault = row.fault or {}
        component, resolve_reason = resolve_component(str(fault.get("component") or ""))
        entry: dict[str, Any] = {"row": row, "case": case, "component": component,
                                 "evaluable": False, "reasons": [], "actual_obs": {},
                                 "actual_tests": {}, "trace_changes": 0, "trace": []}
        per_row[row.row_id] = entry
        if component is None:
            entry["reasons"].append(f"{UNMAPPABLE_CAUSE}: {resolve_reason}")
            continue
        expresses = any(
            any((path.split(".", 1)[0] if "." in path else "") == component.id
                for path in (transition.conditions or {}))
            for transition in definition.transitions
        )
        if not expresses:
            entry["reasons"].append(
                f"{NO_TRANSITION_EXPRESSES_FAULT}: no transition in this twin "
                f"responds to a fault in {component.name} ({component.id})"
            )
            continue
        fault_engine = DigitalTwinEngine(definition)
        fault_engine.command(
            "inject_fault", target=component.id,
            field=str(fault.get("field") or "fault"), value=fault.get("value"),
            reason=f"oracle row {row.row_id}: {row.cause}",
        )
        entry["evaluable"] = True
        entry["trace"] = list(fault_engine.trace)
        entry["trace_changes"] = _trace_change_count(fault_engine.trace)
        for port in row.observables:
            path = observable_paths.get(port)
            entry["actual_obs"][port] = _read_path(fault_engine.state, path) if path else None
        context = {"state": fault_engine.state, "trace": list(fault_engine.trace),
                   "fault": fault, "row": row}
        for test_id in row.tests:
            model = test_models.get(test_id)
            entry["actual_tests"][test_id] = model(context) if model is not None else "unmodelled"

    # ---- O3 pair separation (needs every row's actual combined vector) ----
    def combined_vector(entry: dict[str, Any]) -> dict[str, Any]:
        combined = dict(entry["actual_obs"])
        combined.update(entry["actual_tests"])
        return combined

    o3_failures: dict[str, list[str]] = defaultdict(list)
    scored_by_case: dict[str, list[OracleRow]] = defaultdict(list)
    for row in oracle_rows:
        if row.category == "twin_expressible":
            scored_by_case[row.case_id].append(row)
    pair_total = 0
    pair_separated = 0
    for case_id, case_rows in scored_by_case.items():
        for i in range(len(case_rows)):
            for j in range(i + 1, len(case_rows)):
                pair_total += 1
                left, right = per_row[case_rows[i].row_id], per_row[case_rows[j].row_id]
                if not left["evaluable"] or not right["evaluable"]:
                    continue
                vec_left, vec_right = combined_vector(left), combined_vector(right)
                separated = any(
                    key in vec_left and key in vec_right
                    and vec_left[key] not in (None, "unmodelled")
                    and vec_right[key] not in (None, "unmodelled")
                    and vec_left[key] != vec_right[key]
                    for key in set(vec_left) | set(vec_right)
                )
                if separated:
                    pair_separated += 1
                else:
                    o3_failures[case_rows[i].row_id].append(case_rows[j].row_id)
                    o3_failures[case_rows[j].row_id].append(case_rows[i].row_id)

    # ---- Per-row checks ----
    results: list[RowResult] = []
    for row in oracle_rows:
        case = vocab_cases.get(row.case_id) or {}
        if row.category != "twin_expressible":
            reason = f"{NOT_SCORED}: category {row.category} is not scored in 3A"
            if row.category == "unmapped":
                reason = f"{UNMAPPABLE_CAUSE}: no vocabulary entry for this parser row"
            results.append(RowResult(row=row, outcome="not_scored", checks={}, reasons=[reason]))
            continue
        entry = per_row[row.row_id]
        checks: dict[str, CheckResult] = {}

        # O2 — fault -> symptom.
        o2_reasons = list(entry["reasons"])
        o2_mismatch: dict[str, dict[str, Any]] = {}
        if entry["evaluable"]:
            for port, expected in row.observables.items():
                path = observable_paths.get(port)
                actual = entry["actual_obs"].get(port)
                if path is None or actual is None:
                    o2_reasons.append(f"{OBSERVABLE_UNMAPPED}: {port}")
                elif actual != expected:
                    o2_mismatch[port] = {"expected": expected, "actual": actual}
            if o2_mismatch:
                o2_reasons.append(
                    f"{O2_MISMATCH}: " + ", ".join(
                        f"{port} expected {diff['expected']!r}, got {diff['actual']!r}"
                        for port, diff in sorted(o2_mismatch.items())
                    )
                )
            # O2 checks the observable vector only (v3 §6.3). Test
            # outcomes are not O2 failures: a test the twin cannot model
            # is recorded as unmodelled on the row (see below) and bites
            # in O3, where separation needs modelled tests — never
            # guessed, never silently counted as a symptom match.
        checks["O2"] = CheckResult(
            passed=entry["evaluable"] and not o2_reasons, reasons=o2_reasons,
            expected={"observables": dict(row.observables), "tests": dict(row.tests)},
            actual={"observables": dict(entry["actual_obs"]),
                    "tests": dict(entry["actual_tests"])},
            detail="Injected fault must reproduce the documented symptom vector.",
        )

        # O3 — discriminability.
        o3_reasons = list(entry["reasons"])
        partners = o3_failures.get(row.row_id, [])
        if entry["evaluable"] and partners:
            o3_reasons.append(
                f"{O3_INDISTINGUISHABLE}: identical actual vectors to "
                + ", ".join(sorted(partners))
            )
        checks["O3"] = CheckResult(
            passed=entry["evaluable"] and not partners and not entry["reasons"],
            reasons=o3_reasons,
            expected="differs from every same-symptom cause on >= 1 modelled entry",
            actual={"indistinguishable_from": sorted(partners)},
            detail="Same-symptom causes must differ on at least one test outcome.",
        )

        # O4 — no spurious output.
        o4_reasons = list(entry["reasons"])
        if entry["evaluable"]:
            downstream = _downstream(entry["component"].id, edges)
            for port, expected_nominal in (case.get("nominal") or {}).items():
                if port not in row.observables:
                    continue
                port_component_name = (case.get("observable_components") or {}).get(port)
                # 3B: the Welder exemption is gone. The arc now has a proper
                # port owner (the gun->arc link), so O4 attributes it like
                # any other observable instead of exempting it.
                port_component, _ = resolve_component(str(port_component_name or ""))
                if port_component is None:
                    o4_reasons.append(
                        f"{TOPOLOGY_GAP}: observable {port} belongs to "
                        f"{port_component_name!r}, which is not a component of this twin"
                    )
                    continue
                if port_component.id in downstream:
                    continue
                path = observable_paths.get(port)
                actual = entry["actual_obs"].get(port)
                if path is None or actual is None:
                    o4_reasons.append(f"{OBSERVABLE_UNMAPPED}: {port}")
                elif actual != expected_nominal:
                    o4_reasons.append(
                        f"{O4_SPURIOUS_OUTPUT}: {port} reads {actual!r} with fault in "
                        f"{entry['component'].name}, but {port_component_name} is not "
                        f"downstream of it; nominal is {expected_nominal!r}"
                    )
        checks["O4"] = CheckResult(
            passed=entry["evaluable"] and not o4_reasons, reasons=o4_reasons,
            expected="non-downstream observables stay nominal",
            actual={"observables": dict(entry["actual_obs"])},
            detail="No output without input: observables outside the fault's downstream stay nominal. Since 3B every observable has a proper port owner (the arc is carried by the gun->arc link), so none is exempt.",
        )

        # O5 — fixpoint + trace richness (Amendments Fix 3: case 1's required
        # chain starts at the motor).
        o5_reasons = list(entry["reasons"])
        required_chain = list(case.get("required_chain") or [])
        missing_edges: list[str] = []
        if entry["evaluable"]:
            previous_id: str | None = None
            for link in required_chain:
                link_component, _ = resolve_component(str(link.get("component") or ""))
                if link_component is None:
                    missing_edges.append(
                        f"{TOPOLOGY_GAP}: required-chain component "
                        f"{link.get('component')!r} is not in this twin"
                    )
                    previous_id = None
                    continue
                if previous_id is not None and (previous_id, link_component.id) not in edge_set:
                    missing_edges.append(
                        f"{TOPOLOGY_GAP}: no declared edge "
                        f"{previous_id} -> {link_component.id}"
                    )
                previous_id = link_component.id
            o5_reasons.extend(missing_edges)
            # The engine's step() is a bounded fixed-point loop, so a run
            # that returns has settled by construction; the real
            # propagation settle check arrives with the 3B engine.
            if entry["trace_changes"] < len(required_chain):
                o5_reasons.append(
                    f"{O5_THIN_TRACE}: fault-run trace has {entry['trace_changes']} "
                    f"state change(s); case {row.case_id} requires >= {len(required_chain)} "
                    f"({ ' -> '.join(str(link.get('label')) for link in required_chain) })"
                )
        checks["O5"] = CheckResult(
            passed=entry["evaluable"] and not o5_reasons, reasons=o5_reasons,
            expected={"required_chain": required_chain,
                      "min_state_changes": len(required_chain)},
            actual={"trace_changes": entry["trace_changes"], "trace": entry["trace"]},
            detail="Propagation settles and the trace shows the required chain changing step by step.",
        )

        row_reasons = sorted({reason for check in checks.values() for reason in check.reasons})
        # Independently measured gaps (work-list items, not check
        # failures): a row can be blocked by several at once, and a gap
        # applies whether or not the row was evaluable — e.g. on the 3A
        # baseline every row's required-chain edges are missing from
        # the topology even though most rows are also unmappable. The
        # ranked gap list is built from these reasons.
        gap_reasons: list[str] = []
        previous_id: str | None = None
        for link in case.get("required_chain") or []:
            link_component, _ = resolve_component(str(link.get("component") or ""))
            if link_component is None:
                gap_reasons.append(
                    f"{TOPOLOGY_GAP}: required-chain component "
                    f"{link.get('component')!r} is not in this twin"
                )
                previous_id = None
                continue
            if previous_id is not None and (previous_id, link_component.id) not in edge_set:
                gap_reasons.append(
                    f"{TOPOLOGY_GAP}: no declared edge "
                    f"{previous_id} -> {link_component.id}"
                )
            previous_id = link_component.id
        for port in row.observables:
            if port not in observable_paths:
                gap_reasons.append(f"{OBSERVABLE_UNMAPPED}: {port}")
        for test_id, expected in row.tests.items():
            if test_id not in test_models and expected != "unmodelled":
                gap_reasons.append(f"{UNMODELLED_TEST}: {test_id}")
        row_reasons = sorted(set(row_reasons) | set(gap_reasons))
        if entry["evaluable"]:
            # Tests the twin cannot model are a gap in the test model,
            # recorded on the row (and therefore in the ranked gap list)
            # without failing O2: O2 is the observable vector, O3 is
            # where unmodelled tests bite. A test the vocabulary itself
            # records as unmodelled for this cause is not a gap.
            for test_id, expected in row.tests.items():
                if entry["actual_tests"].get(test_id) == "unmodelled" and expected != "unmodelled":
                    row_reasons.append(f"{UNMODELLED_TEST}: {test_id}")
            row_reasons = sorted(set(row_reasons))
        outcome = "pass" if all(check.passed for check in checks.values()) else "fail"
        results.append(RowResult(row=row, outcome=outcome, checks=checks, reasons=row_reasons))

    # ---- Scores ----
    denominator = dict(Counter(row.category for row in oracle_rows))
    denominator["total"] = len(oracle_rows)
    scored = [r for r in results if r.row.category == "twin_expressible"]
    passed = [r for r in scored if r.outcome == "pass"]
    headline = {
        "passed": len(passed), "denominator": len(scored),
        "rate": (len(passed) / len(scored)) if scored else None,
        "o1_gate": "pass" if o1.passed else "fail",
        "o1_gate_note": (
            "O1 fail invalidates the headline rate: a twin that predicts "
            "symptoms with no fault is not scored as correct."
            if not o1.passed else "O1 nominal gate passed."
        ),
    }
    per_case: dict[str, dict[str, Any]] = {}
    for case_id in sorted(scored_by_case):
        case_scored = [r for r in results if r.row.case_id == case_id and r.row.category == "twin_expressible"]
        case_passed = [r for r in case_scored if r.outcome == "pass"]
        per_case[case_id] = {
            "passed": len(case_passed), "denominator": len(case_scored),
            "rate": (len(case_passed) / len(case_scored)) if case_scored else None,
            "categories": dict(Counter(
                r.row.category for r in results if r.row.case_id == case_id)),
        }
    discriminability = {
        "pairs_separated": pair_separated, "pairs_total": pair_total,
        "rate": (pair_separated / pair_total) if pair_total else None,
        "note": "Pairs where either row is unmappable count as not separated.",
    }

    # ---- Ranked gap list: topology gaps first, then by rows blocked ----
    reason_rows: dict[str, set[str]] = defaultdict(set)
    for result in results:
        if result.row.category != "twin_expressible":
            continue
        for reason in result.reasons:
            code = reason.split(":", 1)[0]
            reason_rows[code].add(result.row.row_id)
    for reason in unresolved_edges:
        reason_rows[TOPOLOGY_GAP].add("twin-topology")
    gap_list: list[dict[str, Any]] = []
    for code in sorted(reason_rows, key=lambda c: (0 if c == TOPOLOGY_GAP else 1, -len(reason_rows[c]), c)):
        row_ids = sorted(reason_rows[code] - {"twin-topology"})
        gap_list.append({
            "reason": code, "rows_blocked": len(row_ids), "row_ids": row_ids,
            "twin_topology_note": "unresolved declared edge(s)" if "twin-topology" in reason_rows[code] else None,
        })

    return OracleReport(
        oracle_version=ORACLE_VERSION,
        twin_name=definition.name,
        twin_components=len(definition.components),
        twin_transitions=len(definition.transitions),
        topology_edges=len(edges),
        o1=o1, rows=results, denominator=denominator, headline=headline,
        per_case=per_case, discriminability=discriminability,
        gap_list=gap_list, problems=list(problems or []),
    )
