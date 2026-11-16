"""Phase 3B compiler lint — the engine execution contract, enforced at
compile time (third Amendments, item 5; v3 §5.4).

The engine itself is untouched (``backend/simulation/engine.py``). The
lint checks, per compiled twin:

- **Conflicting effects** — two rows/transitions writing different
  values to the same path under simultaneously satisfiable conditions
  -> the row is quarantined, never resolved by silent last-writer-wins.
- **Non-convergence** — propagation not settled within the K-pass bound
  -> reported as did-not-settle (unavailable with reason), not silently
  truncated at the bound.
- **Deterministic ordering** — producers before consumers (topological
  order, ties broken by transition id), same twin + same fault -> same
  transition list. A plain id sort puts every archetype row before
  every link and the chain never settles in one step() call (the
  engine's streak bookkeeping freezes a condition's streak at its
  first-pass value within a step).
- **Invalid transitions** — a row whose condition references an
  undeclared port/state/parameter, or whose effect is not a declared
  state or domain value, is quarantined at compile time.

Plus the two table-level checks from the second Amendments (item 3):
rows must be **mutually exclusive** (no input combination matches two
rows with different outputs) and **total** (every reachable input
combination is covered, or the compiler emits an explicit row setting
the outputs to ``unknown`` — the engine cannot produce ``unknown`` on
its own). Nominal rows must fire when a fault is cleared, or clearing a
fault leaves the old values behind — which is why every row's fault
condition is explicit (a nominal row conditions on ``fault == "none"``,
never on the absence of a fault).

No solver, no new engine. If the lint cannot be made to pass on the
case-1 twin, the rows are wrong — fix the rows, do not weaken the lint.
"""
from __future__ import annotations

import itertools
from typing import Any

from backend.simulation.archetypes import (
    ARCHETYPE_LIBRARY_VERSION, Archetype, PropagationRow, QUALITATIVE_DOMAINS,
)
from backend.simulation.engine import DigitalTwinEngine, _matches
from backend.simulation.schema import DigitalTwinDefinition

# ---------------------------------------------------------------------------
# Row / combo matching (plain scalar values; the archetype row format does
# not emit dict specs — see archetypes.py).
# ---------------------------------------------------------------------------

# Fields are tried for dropping (generalisation) in this order when the
# compiler generates totality rows: least discriminating first, so the
# generated rows stay as general as the uncovered set allows.
_DROP_ORDER = ("fault", "groove_matches", "size_matches", "connector_seated",
               "tension", "level", "gas_flow", "plug_rating", "cord_gauge",
               "input_voltage", "power_in", "coil_in", "enable_in",
               "motion_in", "material_in")


def _combo_key(combo: dict[str, Any]) -> tuple:
    # Stringified: combo values mix bools (groove_matches) and strings
    # ("unknown"), and tuple comparison would raise TypeError on
    # bool-vs-str. The key is only an identity for set membership and
    # sorting, never a value — stringifying is safe here.
    return tuple(sorted((str(k), str(v)) for k, v in combo.items()))


def row_matches_combo(row: PropagationRow, combo: dict[str, Any]) -> bool:
    """True when the row fires for this input combination.

    A row matches if every input it constrains equals the combo's value,
    and its fault matches (``fault=None`` on a row means "don't care" —
    used only by compiler-generated totality rows).
    """
    for field, value in (row.inputs or {}).items():
        if field not in combo or combo[field] != value:
            return False
    fault = getattr(row, "fault", "none")
    if fault is not None and combo.get("fault") != fault:
        return False
    return True


def enumerate_combos(archetype: Archetype) -> list[dict[str, Any]]:
    """Every reachable input combination for the archetype's table.

    The domain per field is the archetype's declared input domain
    (ports + parameters + fault). Bounded: A4 is 5 x 4 x 3 x 4 = 240
    combos at most (fault enumerates the declared fault vocabulary).
    """
    domain = archetype.input_domain()
    fields = sorted(domain)
    combos: list[dict[str, Any]] = []
    for values in itertools.product(*(domain[f] for f in fields)):
        combos.append(dict(zip(fields, values)))
    return combos


def validate_row(archetype: Archetype, row: PropagationRow) -> list[str]:
    """Invalid-row check: every condition must reference a declared
    port/state/parameter, every effect a declared state or domain value.
    Returns the list of problems (empty = valid)."""
    problems: list[str] = []
    declared_inputs = set(archetype.input_fields) | {"fault"}
    for field in row.inputs:
        if field not in declared_inputs:
            problems.append(f"row {row.row_id}: condition references undeclared "
                            f"field {field!r}")
    for field, value in row.inputs.items():
        if field == "fault":
            continue
        domain = QUALITATIVE_DOMAINS.get(archetype.input_fields.get(field, ""), [])
        if value not in domain and value != "unknown":
            problems.append(f"row {row.row_id}: input {field}={value!r} is not a "
                            f"declared value of its domain")
    if row.fault is not None and row.fault not in archetype.fault_values:
        problems.append(f"row {row.row_id}: fault {row.fault!r} is not in the "
                        f"archetype's fault vocabulary")
    for field, value in row.outputs.items():
        if field not in archetype.output_ports:
            problems.append(f"row {row.row_id}: effect references undeclared "
                            f"output port {field!r}")
            continue
        domain = QUALITATIVE_DOMAINS.get(archetype.output_ports[field], [])
        if value not in domain and value != "unknown":
            problems.append(f"row {row.row_id}: output {field}={value!r} is not a "
                            f"declared value of its domain")
    if row.next_state not in archetype.states and row.next_state != "unknown":
        problems.append(f"row {row.row_id}: next_state {row.next_state!r} is not a "
                        f"declared state")
    return problems


def lint_archetype_table(archetype: Archetype,
                         rows: list[PropagationRow] | None = None
                         ) -> dict[str, Any]:
    """Exclusion + totality over the enumerated input domain.

    Returns {"conflicts": [...], "uncovered": [...], "invalid": [...],
    "combos": N}. A conflict names the input combination and the rows
    that disagree on it; uncovered names combinations no row covers.
    """
    rows = list(rows if rows is not None else archetype.propagation_table)
    invalid: list[dict[str, Any]] = []
    valid_rows: list[PropagationRow] = []
    for row in rows:
        problems = validate_row(archetype, row)
        if problems:
            invalid.append({"row_id": row.row_id, "problems": problems})
        else:
            valid_rows.append(row)
    combos = enumerate_combos(archetype)
    conflicts: list[dict[str, Any]] = []
    uncovered: list[dict[str, Any]] = []
    for combo in combos:
        matching = [r for r in valid_rows if row_matches_combo(r, combo)]
        if not matching:
            uncovered.append(dict(combo))
            continue
        signatures = {(tuple(sorted(r.outputs.items())), r.next_state) for r in matching}
        if len(signatures) > 1:
            conflicts.append({
                "combo": dict(combo),
                "rows": [r.row_id for r in matching],
                "outputs": sorted({str(s) for s in signatures}),
            })
    return {"archetype_id": archetype.archetype_id, "combos": len(combos),
            "conflicts": conflicts, "uncovered": uncovered, "invalid": invalid}


def generate_unknown_rows(archetype: Archetype) -> list[PropagationRow]:
    """Explicit unknown-setting rows for every uncovered input combination.

    Greedy minimal cover, deterministic: uncovered combos are visited in
    sorted order; each becomes a row generalised by dropping fields (in
    _DROP_ORDER) while the partial condition still matches ONLY uncovered
    combos. The result is total by construction (the lint re-verifies it)
    and every generated row sets all output ports + state to "unknown".
    A generated row never claims a behaviour — its source says so.
    """
    combos = enumerate_combos(archetype)
    declared = list(archetype.propagation_table)
    uncovered = [c for c in combos if not any(row_matches_combo(r, c) for r in declared)]
    uncovered_keys = {_combo_key(c) for c in uncovered}

    def matches_only_uncovered(condition: dict[str, Any]) -> bool:
        for combo in combos:
            if all(combo.get(f) == v for f, v in condition.items()):
                if _combo_key(combo) not in uncovered_keys:
                    return False
        return True

    generated: list[PropagationRow] = []
    covered_by_generated: set[tuple] = set()
    fields_in_order = sorted({f for c in uncovered for f in c})
    drop_order = [f for f in _DROP_ORDER if f in fields_in_order] + [
        f for f in fields_in_order if f not in _DROP_ORDER]
    for combo in sorted(uncovered, key=_combo_key):
        if _combo_key(combo) in covered_by_generated:
            continue
        condition = dict(combo)
        for field in drop_order:
            if field not in condition:
                continue
            trial = {k: v for k, v in condition.items() if k != field}
            if trial and matches_only_uncovered(trial):
                condition = trial
        fault = condition.pop("fault", None)
        row = PropagationRow(
            row_id=f"{archetype.archetype_id}.u-totality-{len(generated) + 1:02d}",
            inputs=dict(condition),
            # fault dropped from the condition during generalisation means
            # "don't care" (None); a fault value that survived means the
            # row constrains exactly that fault value.
            fault=fault,
            outputs={port: "unknown" for port in archetype.output_ports},
            next_state="unknown",
            sources=["compiler-generated totality row (Phase 3B lint): explicit "
                     "unknown, not a behaviour claim — the engine cannot produce "
                     "'unknown' on its own"],
            cases=[],
            note="Generated, not hand-written: covers input combinations no "
                 "declared row covers. If this row fires, the twin says it does "
                 "not know, which is the correct answer for an unmodelled input.",
        )
        generated.append(row)
        for other in combos:
            if row_matches_combo(row, other):
                covered_by_generated.add(_combo_key(other))
    return generated


# ---------------------------------------------------------------------------
# Whole-twin lint: conflicts across ALL transitions, invalid refs, ordering.
# ---------------------------------------------------------------------------

def _constraint_compatible(left: Any, right: Any) -> bool:
    """True when two condition specs on the same path can hold at once.

    Plain values must be equal. Dict specs are decided where decidable
    (equals / not_equals / in); anything else is treated as compatible
    (fail-closed toward reporting a possible conflict — the 3B data uses
    plain values only, so this branch never fires on archetype rows).
    """
    if left == right:
        return True
    if isinstance(left, dict) or isinstance(right, dict):
        def _as_set(spec: Any) -> set | None:
            if isinstance(spec, dict):
                if "equals" in spec:
                    return {spec["equals"]}
                if "in" in spec:
                    return set(spec["in"])
                return None
            return {spec}
        lset, rset = _as_set(left), _as_set(right)
        if lset is not None and rset is not None:
            return bool(lset & rset)
        for spec, plain in ((left, right), (right, left)):
            if isinstance(spec, dict) and "not_equals" in spec and not isinstance(plain, dict):
                return plain != spec["not_equals"]
        return True
    return False


def conditions_simultaneously_satisfiable(left: dict[str, Any],
                                          right: dict[str, Any]) -> bool:
    for path in set(left) | set(right):
        if path in left and path in right:
            if not _constraint_compatible(left[path], right[path]):
                return False
    return True


def order_transitions(transitions: list[Any]) -> tuple[list[Any], list[str]]:
    """Canonical deterministic order for a compiled transition list:
    producers before consumers (topological, ties broken by transition
    id), so the engine's fixpoint settles a chain in one step() call.

    Why this matters (found 13 Nov 2026, 3B): the engine's ``for_steps``
    streak bookkeeping updates a condition's streak only on its first
    evaluation within a step() call. A consumer whose condition first
    becomes true mid-step — because its producer fired later in the
    same pass — keeps a streak of 0 and cannot fire until the NEXT
    step() call. A plain id sort puts every archetype row before every
    link, so the motor -> feeder -> wire chain never settles in one
    step and the nominal fixpoint stops after the trigger. Ordering
    producers before consumers makes each consumer's conditions true
    by the time it is first evaluated, so the chain settles in one
    pass. The order is deterministic (Kahn's algorithm, id tie-break);
    a cycle falls back to id order for its members and is reported.
    """
    transitions = list(transitions)
    by_id = {t.id: t for t in transitions}
    reads = {t.id: set(getattr(t, "conditions", {}) or {}) for t in transitions}
    writes = {t.id: set(getattr(t, "effects", {}) or {}) for t in transitions}
    writers_of: dict[str, set[str]] = {}
    for tid, paths in writes.items():
        for path in paths:
            writers_of.setdefault(path, set()).add(tid)
    dependencies: dict[str, set[str]] = {}
    for tid in by_id:
        deps = set()
        for path in reads[tid]:
            deps |= writers_of.get(path, set())
        deps.discard(tid)
        dependencies[tid] = deps
    ordered: list[Any] = []
    remaining = set(by_id)
    cycles: list[str] = []
    while remaining:
        ready = sorted(tid for tid in remaining if dependencies[tid] <= set(by_id) - remaining)
        if not ready:
            # Cycle (or a dependency on a transition not in this list):
            # fall back to id order for the smallest remaining id and
            # report it — the settle lint will catch a real non-settling
            # twin, and the order stays deterministic.
            tid = sorted(remaining)[0]
            cycles.append(tid)
            ready = [tid]
        for tid in ready:
            ordered.append(by_id[tid])
            remaining.discard(tid)
    return ordered, cycles


def lint_transitions(definition: DigitalTwinDefinition) -> dict[str, Any]:
    """Lint the compiled transition list.

    Returns {"conflicts": [...], "invalid": [...], "ordered": bool,
    "transition_ids": [...]}. A conflict is two transitions writing
    different values to the same path under simultaneously satisfiable
    conditions; invalid is a condition referencing a path no component
    owns (or a signal that is never set) or an effect on an unknown
    path. Ordering is the sorted-id check (deterministic pass order).
    """
    transitions = list(definition.transitions or [])
    component_ids = {c.id for c in definition.components}
    invalid: list[dict[str, Any]] = []
    for t in transitions:
        for path in list(t.conditions) + list(t.effects):
            if "." in path:
                cid, _field = path.split(".", 1)
                if cid not in component_ids:
                    invalid.append({"transition_id": t.id, "path": path,
                                    "problem": f"references unknown component {cid!r}"})
    conflicts: list[dict[str, Any]] = []
    for i, left in enumerate(transitions):
        for right in transitions[i + 1:]:
            shared = set(left.effects) & set(right.effects)
            differing = [p for p in shared if left.effects[p] != right.effects[p]]
            if not differing:
                continue
            if conditions_simultaneously_satisfiable(left.conditions, right.conditions):
                conflicts.append({
                    "transitions": [left.id, right.id],
                    "paths": sorted(differing),
                    "left_effects": {p: left.effects[p] for p in differing},
                    "right_effects": {p: right.effects[p] for p in differing},
                })
    ids = [t.id for t in transitions]
    canonical, _cycles = order_transitions(transitions)
    canonical_ids = [t.id for t in canonical]
    return {"conflicts": conflicts, "invalid": invalid,
            "ordered": ids == canonical_ids, "transition_ids": ids,
            "transition_count": len(transitions)}


def check_settles(definition: DigitalTwinDefinition,
                  fault_injections: list[dict[str, Any]] | None = None,
                  rounds: int = 3) -> dict[str, Any]:
    """Non-convergence check, via the real engine (never a re-implementation).

    Runs step() ``rounds`` times from the nominal initial state (and once
    per fault injection) and reports whether any step after the first
    still changes state. At a true fixpoint a further step() changes
    nothing; a system that keeps changing across steps has not settled
    (oscillation / cycle) and the result is did-not-settle.
    """
    def _run(fault: dict[str, Any] | None) -> dict[str, Any]:
        engine = DigitalTwinEngine(definition)
        engine.step()
        settled = True
        changed_after_first: list[int] = []
        for round_no in range(2, rounds + 1):
            before = {cid: dict(fields)
                      for cid, fields in engine.state.get("components", {}).items()}
            changes = engine.step()
            after = engine.state.get("components", {})
            if changes and any(" → " in line for line in changes):
                settled = False
                changed_after_first.append(round_no)
            elif before != after:
                settled = False
                changed_after_first.append(round_no)
        return {"settled": settled, "changed_after_first": changed_after_first,
                "fault": fault}

    results = [_run(None)]
    for fault in fault_injections or []:
        engine = DigitalTwinEngine(definition)
        engine.command("inject_fault", target=fault["component_id"],
                       field=fault.get("field") or "fault",
                       value=fault.get("value"),
                       reason="lint settle check")
        # The first step() after the injection is the propagation of the
        # fault itself — it is expected to change state. Only changes in
        # the steps AFTER that first one mean the twin has not settled
        # (the nominal branch above does the same first step first).
        engine.step()
        settled = True
        changed_after_first: list[int] = []
        for round_no in range(2, rounds + 1):
            before = {cid: dict(fields)
                      for cid, fields in engine.state.get("components", {}).items()}
            changes = engine.step()
            after = engine.state.get("components", {})
            if changes and any(" → " in line for line in changes):
                settled = False
                changed_after_first.append(round_no)
            elif before != after:
                settled = False
                changed_after_first.append(round_no)
        results.append({"settled": settled, "changed_after_first": changed_after_first,
                        "fault": fault})
    return {"settled": all(r["settled"] for r in results), "runs": results}
