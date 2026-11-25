"""Benchmark oracle runner (O1/O2/O3) for the Crater benchmark's Lincoln
and Miller cases.

Usage (from the repo root, with the build venv)::

    .venv/bin/python benchmark/run_benchmark_oracle.py \
        --db ~/workspace/user/files/crater-10.db \
        --oracle ~/workspace/crater-benchmarks/evaluations/c002-lincoln.json
    .venv/bin/python benchmark/run_benchmark_oracle.py \
        --db <db> --oracle <oracle-json> --revision-id <id>

The benchmark harness (``crater-benchmarks/harness.py``) scores only the
diagnosis level over HTTP and explicitly does NOT run O1/O2/O3
(``o1_o2_o3: "NOT_RUN_OVER_HTTP"``). This script is the other half: it
runs the Phase 3A oracle (``benchmark/oracle.py`` — pure, no
LLM, no network) against the ACTIVE compiled twin stored in a crater
DB, using a benchmark oracle JSON (``c002-lincoln.json`` /
``c002b-miller.json``) as ground truth.

What it does
------------
1. Loads the ACTIVE compiled twin for the revision, read-only, with the
   same query pattern as ``_load_twin_for_session``
   (``backend/api/routes/diagnostics.py`` via
   ``backend/diagnostics/twin_verification.py``: ``DigitalTwin`` by
   ``revision_id`` + status ``active``, latest first,
   ``DigitalTwinDefinition.model_validate``). With no ``--revision-id``
   it uses the most recent ACTIVE twin in the DB and prints which
   revision/twin it used. The DB is opened read-only
   (``sqlite:///...?mode=ro&uri=true``, the same read-only intent as
   ``run_phase3a_baseline._load_snapshot``'s ``mode=ro``) and never
   written to.
2. Loads the oracle JSON, validates its denominator against its rows,
   and builds ``OracleRow`` objects from the JSON rows.
3. Builds ``observable_paths`` from the twin's components by
   unique-name match only — ambiguous or missing components are
   unmapped and recorded, never guessed (the same binding discipline
   as the rest of Crater; ``run_oracle``'s own ``resolve_component``
   already does unique-name matching for faults).
4. Calls ``run_oracle`` and prints the report: O1 pass/fail + reasons,
   O2 count against the twin-expressible denominator, O3 pairs, O4/O5
   as reported, per-row results, and the denominator.

Exit code: 0 when a report was produced, even if O1 fails (a failing
oracle is a result, not a crash); 2 only when it cannot run (no twin,
bad oracle file, bad DB).

Oracle JSON shape (verified against the Lincoln/Miller files, not
assumed — see BENCHMARK_ORACLE_RUNNER_BUILD_NOTES.md)
------------------------------------------------------
Top keys: ``version, created, case_id, product, source,
scope_disclaimer, review, join_rule, categories, tests, cases, rows,
answer_key, denominator``. Each row carries ``row_id, case_id,
symptom, cause, category, fault, observables, tests, basis, flags,
source_pages, source_table`` — and NO ``remedy`` and NO ``case_rank``.
The remedy is held out in ``answer_key`` only, keyed by ``row_id``;
the join rule in the file says a row joins to a deterministic-parser
cause row on ``row_id``. Each case carries ``symptom, table_page,
observables, nominal, required_chain, tests``; ``required_chain`` is
empty in these files (the chain is defined at run prep against the
twin's topology, so O5's chain check is vacuous until a run defines
one). The ``tests`` dict maps ``test_id -> {description, outcomes,
source_page}``.

Decisions that follow from that shape
-------------------------------------
- ``remedy`` comes from ``answer_key[row_id]["corrective_action"]``
  (``remedy_source`` from the entry's ``remedy_source``). Verified on
  both shipped files: the answer key covers EVERY row (22/22 Lincoln,
  63/63 Miller) and its ``cause`` text matches the row's cause exactly.
  Where a future file has no answer-key entry for a row, the remedy is
  ``""`` with source ``"answer_key_missing"`` and the gap is recorded
  as a problem — never guessed. ``run_oracle`` never reads the remedy
  (it is carried on ``OracleRow`` only for the report), so this choice
  cannot change any score.
- ``case_rank`` is derived from row order within its case: the
  1-based index of the row among the file's rows for that ``case_id``,
  in file order. The JSON carries no rank, so file order is the only
  rank available; it is informational only (no oracle check reads it).
- Rows without a fault stay ``fault=None`` and are recorded as
  unmappable in the report, never guessed. (In the shipped files every
  ``twin_expressible`` row has a complete fault and no other row
  carries one; the validator checks that and records a problem if a
  future file breaks it.)
- ``test_models`` is NOT passed by default: no test model can be built
  generically from the oracle JSON's ``tests`` dict (it carries
  descriptions and allowed outcomes, not a mapping from twin state to
  an outcome — writing one per test would be inventing behaviour).
  Tests therefore read as ``unmodelled``, exactly as the 3A baseline
  did. A caller that HAS reviewed per-test models for a specific twin
  can pass them through ``run_benchmark_oracle(..., test_models=...)``;
  the CLI has no such option on purpose.

What this runner cannot do yet
------------------------------
- A Lincoln twin does not exist yet (Lincoln ingestion crashed on
  21 Nov 2026 on an image-format error; fix4 is building). Until it
  exists, any Lincoln run scores whatever twin IS in the DB against
  Lincoln rows and will honestly read near zero.
- No twin-specific observable mapping or test models exist for
  Lincoln/Miller. When their twins are compiled, someone must write
  the reviewed mapping (which port is which twin field, as
  `omnipro_proof.observable_paths` does for OMNIPRO) and the reviewed
  per-test models (as `omnipro_proof.build_test_models` does), plus
  the per-case `required_chain` against the twin's topology — until
  then O1 stays unmapped, tests stay unmodelled, and O5's chain check
  stays vacuous. The core accepts both as arguments; the CLI does
  not yet take them from a file (a `--mapping` option is the obvious
  next step once the first Lincoln/Miller mapping exists).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, load_only

from backend.db.models import DigitalTwin, TwinStatus
from benchmark.oracle import OracleRow, run_oracle
from backend.simulation.schema import DigitalTwinDefinition

# Categories the benchmark oracle JSONs use. ``oracle.py``'s CATEGORIES
# has no "unmappable" (the OMNIPRO vocabulary used "unmapped" for that);
# the benchmark files use "unmappable" as a row category, and run_oracle
# treats any non-"twin_expressible" category as not_scored, so an
# "unmappable" row lands in the denominator and is reported, never
# silently scored or dropped.
BENCHMARK_CATEGORIES = (
    "twin_expressible", "settings", "technique", "cross_reference", "unmappable",
)


class BenchmarkOracleError(Exception):
    """The runner cannot run: bad oracle file, bad DB, or no twin."""


class NoActiveTwinError(BenchmarkOracleError):
    """No ACTIVE twin exists for the requested revision (or at all)."""


# ---------------------------------------------------------------------------
# Oracle JSON: load + validate + build rows
# ---------------------------------------------------------------------------

def load_benchmark_oracle(path: str | Path) -> dict:
    """Load a benchmark oracle JSON. Raises BenchmarkOracleError on a
    missing, unreadable, or malformed file — never returns a partial
    document."""
    path = Path(path)
    if not path.is_file():
        raise BenchmarkOracleError(f"oracle file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BenchmarkOracleError(f"oracle file {path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise BenchmarkOracleError(f"oracle file {path} is not a JSON object")
    for key in ("rows", "cases", "denominator"):
        if key not in data:
            raise BenchmarkOracleError(f"oracle file {path} is missing top-level key {key!r}")
    if not isinstance(data["rows"], list) or not data["rows"]:
        raise BenchmarkOracleError(f"oracle file {path} has no rows")
    if not isinstance(data["cases"], dict) or not data["cases"]:
        raise BenchmarkOracleError(f"oracle file {path} has no cases")
    if not isinstance(data["denominator"], dict):
        raise BenchmarkOracleError(f"oracle file {path} has no denominator object")
    return data


def validate_benchmark_oracle(data: dict) -> list[str]:
    """Validate a loaded oracle JSON against its own rows. Returns the
    list of problems; an empty list means valid.

    Checked: the denominator's per-category counts and total match the
    rows exactly; row_ids are unique; every row's case_id is a known
    case; every row's observables/tests match its case's sets; every
    twin_expressible row carries a complete fault and no other row
    carries one; every answer-key entry (where present) names the same
    cause as its row. A denominator mismatch is fatal to the run (the
    caller raises); the rest are recorded as problems and the run
    proceeds — a row that breaks them fails honestly in the oracle as
    unmappable / not_scored instead of being silently repaired.
    """
    problems: list[str] = []
    rows = data.get("rows") or []
    cases = data.get("cases") or {}
    denominator = data.get("denominator") or {}
    answer_key = data.get("answer_key") or {}

    counted = Counter(row.get("category") for row in rows)
    counted["total"] = len(rows)
    for key in sorted(set(denominator) | set(counted)):
        if denominator.get(key, 0) != counted.get(key, 0):
            problems.append(
                f"denominator mismatch: {key} is {denominator.get(key, 0)} in the file "
                f"but {counted.get(key, 0)} rows carry it"
            )

    seen_ids: set[str] = set()
    for index, row in enumerate(rows):
        where = f"row #{index + 1} ({row.get('row_id') or 'no row_id'})"
        row_id = row.get("row_id")
        if not row_id:
            problems.append(f"{where}: missing row_id")
        elif row_id in seen_ids:
            problems.append(f"{where}: duplicate row_id {row_id!r}")
        else:
            seen_ids.add(row_id)
        case_id = row.get("case_id")
        case = cases.get(case_id) if isinstance(case_id, str) else None
        if case is None:
            problems.append(f"{where}: unknown case_id {case_id!r}")
            continue
        if set(row.get("observables") or {}) != set(case.get("observables") or []):
            problems.append(f"{where}: observables do not match case {case_id}'s observable set")
        if set(row.get("tests") or {}) != set(case.get("tests") or []):
            problems.append(f"{where}: tests do not match case {case_id}'s test set")
        if row.get("category") not in BENCHMARK_CATEGORIES:
            problems.append(f"{where}: bad category {row.get('category')!r}")
        fault = row.get("fault")
        if row.get("category") == "twin_expressible":
            if not isinstance(fault, dict) or not fault.get("component") \
                    or not fault.get("field") or "value" not in fault:
                problems.append(f"{where}: twin-expressible row without a complete fault")
        elif fault is not None:
            problems.append(f"{where}: non-twin-expressible row carries a fault")
        entry = answer_key.get(row_id) if row_id else None
        if isinstance(entry, dict) and entry.get("cause") not in (None, row.get("cause")):
            problems.append(f"{where}: answer-key cause does not match the row's cause")
    return problems


def build_oracle_rows(data: dict) -> tuple[list[OracleRow], list[str]]:
    """Build ``OracleRow`` objects from a loaded oracle JSON.

    Returns (rows, problems). Remedy comes from the answer key (see
    module docstring); case_rank is the row's 1-based order within its
    case, in file order; rows without a fault stay fault=None.
    """
    problems: list[str] = []
    answer_key = data.get("answer_key") or {}
    rank_within_case: Counter = Counter()
    rows: list[OracleRow] = []
    for raw in data.get("rows") or []:
        case_id = str(raw.get("case_id") or "")
        rank_within_case[case_id] += 1
        row_id = str(raw.get("row_id") or "")
        entry = answer_key.get(row_id) if row_id else None
        if isinstance(entry, dict):
            remedy = str(entry.get("corrective_action") or "")
            remedy_source = str(entry.get("remedy_source") or "answer_key")
        else:
            remedy, remedy_source = "", "answer_key_missing"
            problems.append(
                f"row {row_id or '?'} has no answer-key entry: remedy held out "
                "and not recorded; remedy left empty, never guessed"
            )
        fault = raw.get("fault")
        rows.append(OracleRow(
            row_id=row_id, case_id=case_id, case_rank=rank_within_case[case_id],
            symptom=str(raw.get("symptom") or ""), cause=str(raw.get("cause") or ""),
            remedy=remedy, remedy_source=remedy_source,
            source_pages=list(raw.get("source_pages") or []),
            category=str(raw.get("category") or ""),
            fault=dict(fault) if isinstance(fault, dict) else None,
            observables=dict(raw.get("observables") or {}),
            tests=dict(raw.get("tests") or {}),
            basis=str(raw.get("basis") or ""),
            flags=list(raw.get("flags") or []),
        ))
    return rows, problems


# ---------------------------------------------------------------------------
# Observable binding: unique-name match only, never guessed
# ---------------------------------------------------------------------------

def build_observable_paths(
    definition: DigitalTwinDefinition, cases: dict,
) -> tuple[dict[str, str], dict[str, str]]:
    """Map vocabulary observable ports to twin state paths.

    A port maps only when exactly one twin component carries a state
    field with the port's base name (the part before ``@``, e.g.
    ``wire_out@tip`` -> ``wire_out``; a bare port such as
    ``weld_output`` is its own base name), or when the port names a
    twin signal exactly (mapped to the bare signal key, which the
    oracle reads from ``state["signals"]``). Zero matches or more than
    one match -> unmapped, recorded in the returned dict with its
    reason. Nothing is guessed: a port whose field name differs from
    the port name (OMNIPRO's ``wire_out@tip`` -> ``material_out``)
    stays unmapped here and needs a twin-specific mapping passed as
    an override to ``run_benchmark_oracle``.
    """
    by_field: dict[str, list[tuple[str, str]]] = {}
    for component in definition.components:
        for field_name in (component.initial_state or {}):
            by_field.setdefault(field_name, []).append((component.id, field_name))
    signals = set((definition.initial_signals or {}))

    ports: list[str] = []
    for case in (cases or {}).values():
        for port in (case or {}).get("observables") or []:
            if port not in ports:
                ports.append(port)

    paths: dict[str, str] = {}
    unmapped: dict[str, str] = {}
    for port in ports:
        if port in signals:
            paths[port] = port
            continue
        base = port.split("@", 1)[0]
        candidates = by_field.get(base, [])
        if len(candidates) == 1:
            component_id, field_name = candidates[0]
            paths[port] = f"{component_id}.{field_name}"
        elif not candidates:
            unmapped[port] = f"no twin component carries a state field named {base!r}"
        else:
            names = ", ".join(component_id for component_id, _ in candidates)
            unmapped[port] = (
                f"{len(candidates)} components carry a state field named {base!r} "
                f"({names}); ambiguous, not guessed"
            )
    return paths, unmapped


def unmapped_fault_components(
    definition: DigitalTwinDefinition, oracle_rows: list[OracleRow],
) -> dict[str, str]:
    """Fault components the twin cannot resolve by unique name.

    Mirrors ``run_oracle``'s ``resolve_component`` discipline (unique
    normalised-name match): a fault component with zero or several
    twin components of that name is unmapped. Reported so the CLI can
    name the missing components; the oracle itself already fails those
    rows as ``unmappable_cause`` and keeps them in the denominator.
    """
    from benchmark.oracle import normalize_text

    by_name: Counter = Counter()
    for component in definition.components:
        by_name[normalize_text(component.name)] += 1
    unmapped: dict[str, str] = {}
    for row in oracle_rows:
        fault = row.fault or {}
        name = str(fault.get("component") or "")
        if not name or name in unmapped:
            continue
        count = by_name.get(normalize_text(name), 0)
        if count == 0:
            unmapped[name] = f"no component named {name!r} in this twin"
        elif count > 1:
            unmapped[name] = f"{count} components named {name!r} in this twin; ambiguous"
    return unmapped


# ---------------------------------------------------------------------------
# Testable core: oracle load + run_oracle call (no DB, no CLI, no I/O)
# ---------------------------------------------------------------------------

def run_benchmark_oracle(
    definition: DigitalTwinDefinition, oracle_data: dict, *,
    observable_paths: dict[str, str] | None = None,
    test_models: dict | None = None,
    problems: list[str] | None = None,
) -> dict:
    """Run the benchmark oracle against one compiled twin definition.

    ``oracle_data`` is a loaded benchmark oracle JSON (see
    ``load_benchmark_oracle``). When ``observable_paths`` is omitted
    the generic binding in ``build_observable_paths`` is used;
    pass a twin-specific mapping (e.g. the 3C proof twin's) to
    override it. ``test_models`` defaults to none (tests read as
    unmodelled — see module docstring). Returns a dict with the
    ``OracleReport`` under ``"report"`` plus the binding notes; the
    report itself is also under ``"report_dict"`` (JSON-ready).
    Raises ``BenchmarkOracleError`` on a denominator mismatch — the
    oracle file's own accounting is wrong, so the run cannot be
    trusted — and ``ValueError``-style failures from ``run_oracle``
    propagate unchanged.
    """
    validation_problems = validate_benchmark_oracle(oracle_data)
    denominator_problems = [p for p in validation_problems if p.startswith("denominator mismatch")]
    if denominator_problems:
        raise BenchmarkOracleError(
            "oracle file denominator does not match its rows: " + "; ".join(denominator_problems)
        )
    oracle_rows, build_problems = build_oracle_rows(oracle_data)
    all_problems = list(problems or []) + validation_problems + build_problems

    cases = oracle_data.get("cases") or {}
    if observable_paths is None:
        observable_paths, unmapped_observables = build_observable_paths(definition, cases)
    else:
        observable_paths = dict(observable_paths)
        unmapped_observables = {
            port: "not in the supplied observable mapping"
            for case in cases.values() for port in (case.get("observables") or [])
            if port not in observable_paths
        }
    unmapped_faults = unmapped_fault_components(definition, oracle_rows)

    report = run_oracle(
        definition.model_dump(mode="json"), oracle_rows, cases,
        observable_paths=observable_paths,
        test_models=test_models or {},
        problems=all_problems,
    )
    return {
        "report": report,
        "report_dict": report.to_dict(),
        "observable_paths": observable_paths,
        "unmapped_observables": unmapped_observables,
        "unmapped_faults": unmapped_faults,
        "problems": all_problems,
        "denominator": dict(report.denominator),
    }


# ---------------------------------------------------------------------------
# DB loading: ACTIVE twin, read-only, same query pattern as
# _load_twin_for_session
# ---------------------------------------------------------------------------

def load_active_twin(
    db_path: str | Path, revision_id: str | None = None,
) -> tuple[DigitalTwinDefinition, dict]:
    """(definition, twin_info) for the ACTIVE twin, read-only.

    Same query pattern as ``_load_twin_for_session``: ``DigitalTwin``
    by ``revision_id`` + status ``active``, latest first,
    ``DigitalTwinDefinition.model_validate``. With no ``revision_id``,
    the most recent ACTIVE twin in the DB, whatever its revision.
    Raises ``NoActiveTwinError`` when there is none and
    ``BenchmarkOracleError`` when the DB cannot be opened or the twin
    definition is invalid. Never writes to the DB.
    """
    db_path = Path(db_path)
    if not db_path.is_file():
        raise BenchmarkOracleError(f"DB file not found: {db_path}")
    try:
        engine = create_engine(f"sqlite:///file:{db_path}?mode=ro&uri=true")
        with Session(engine) as session:
            # Select only the columns this runner needs. Older DBs
            # (e.g. crater-10.db) predate the DigitalTwin.library_version
            # column, and a full-ORM select fails on them with "no such
            # column: digital_twins.library_version" — the query pattern
            # (revision_id + status active, latest first) is unchanged.
            query = session.query(DigitalTwin).options(load_only(
                DigitalTwin.id, DigitalTwin.revision_id, DigitalTwin.name,
                DigitalTwin.definition, DigitalTwin.model_version,
                DigitalTwin.status, DigitalTwin.created_at,
            )).filter_by(status=TwinStatus.active)
            if revision_id:
                query = query.filter_by(revision_id=revision_id)
            twin = query.order_by(DigitalTwin.created_at.desc()).first()
            if twin is None:
                if revision_id:
                    raise NoActiveTwinError(
                        f"no ACTIVE twin for revision {revision_id} in {db_path}")
                raise NoActiveTwinError(f"no ACTIVE twin in {db_path}")
            try:
                definition = DigitalTwinDefinition.model_validate(twin.definition)
            except Exception as exc:  # noqa: BLE001 - invalid stored definition is a bad DB
                raise BenchmarkOracleError(
                    f"twin {twin.id} in {db_path} has an invalid definition: {exc}"
                ) from exc
            info = {
                "twin_id": twin.id, "revision_id": twin.revision_id,
                "name": twin.name, "created_at": str(twin.created_at),
                "model_version": twin.model_version,
            }
    except BenchmarkOracleError:
        raise
    except Exception as exc:  # noqa: BLE001 - e.g. missing table, corrupt file
        raise BenchmarkOracleError(f"cannot read DB {db_path}: {exc}") from exc
    return definition, info


# ---------------------------------------------------------------------------
# CLI: DB load + print
# ---------------------------------------------------------------------------

def _print_report(result: dict, twin_info: dict, oracle_path: Path) -> None:
    report = result["report"]
    headline = report.headline
    print(f"twin: {twin_info['name']} (id {twin_info['twin_id']}, "
          f"revision {twin_info['revision_id']}, created {twin_info['created_at']})")
    print(f"oracle: {oracle_path}")
    print(f"components: {report.twin_components}, transitions: {report.twin_transitions}, "
          f"topology edges: {report.topology_edges}")
    print(f"denominator: {result['denominator']}")
    print(f"O1 nominal gate: {headline['o1_gate']} — {headline['o1_gate_note']}")
    for reason in report.o1.reasons:
        print(f"  O1 reason: {reason}")
    if report.o1.detail and "Unmapped nominal ports" in report.o1.detail:
        print(f"  O1 detail: {report.o1.detail}")
    print(f"O2 headline (twin-expressible rows passed): "
          f"{headline['passed']}/{headline['denominator']}"
          + (f" = {headline['rate']:.1%}" if headline["rate"] is not None else ""))
    disc = report.discriminability
    print(f"O3 discriminability (same-symptom pairs separated): "
          f"{disc['pairs_separated']}/{disc['pairs_total']}"
          + (f" = {disc['rate']:.1%}" if disc["rate"] is not None else ""))
    scored = [r for r in report.rows if r.row.category == "twin_expressible"]
    for check in ("O2", "O3", "O4", "O5"):
        passed = sum(1 for r in scored if r.checks.get(check) and r.checks[check].passed)
        print(f"{check}: {passed}/{len(scored)} twin-expressible rows pass")
    print("per-case:")
    for case_id in sorted(report.per_case):
        entry = report.per_case[case_id]
        rate = f" = {entry['rate']:.0%}" if entry["rate"] is not None else ""
        print(f"  {case_id}: {entry['passed']}/{entry['denominator']}{rate}")
    if result["unmapped_observables"]:
        print(f"unmapped observables ({len(result['unmapped_observables'])}):")
        for port, reason in sorted(result["unmapped_observables"].items()):
            print(f"  {port}: {reason}")
    if result["unmapped_faults"]:
        print(f"unmapped fault components ({len(result['unmapped_faults'])}):")
        for name, reason in sorted(result["unmapped_faults"].items()):
            print(f"  {name}: {reason}")
    if report.gap_list:
        print("ranked gaps:")
        for gap in report.gap_list:
            print(f"  {gap['reason']} — blocks {gap['rows_blocked']} row(s): "
                  f"{', '.join(gap['row_ids'][:10])}"
                  + (" …" if len(gap["row_ids"]) > 10 else ""))
    if result["problems"]:
        print("oracle-set problems:")
        for problem in result["problems"]:
            print(f"  - {problem}")
    print("per-row:")
    for row_result in report.rows:
        row = row_result.row
        reasons = "; ".join(row_result.reasons)
        print(f"  {row.row_id} [{row.case_id}] {row_result.outcome}: "
              f"{row.cause[:100]}"
              + (f" — {reasons}" if reasons else ""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True,
                        help="path to a crater .db file (opened read-only)")
    parser.add_argument("--oracle", type=Path, required=True,
                        help="path to a benchmark oracle JSON "
                             "(e.g. crater-benchmarks/evaluations/c002-lincoln.json)")
    parser.add_argument("--revision-id", default=None,
                        help="revision to load the ACTIVE twin for; "
                             "omit for the most recent ACTIVE twin in the DB")
    args = parser.parse_args(argv)
    try:
        definition, twin_info = load_active_twin(args.db, args.revision_id)
    except BenchmarkOracleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        oracle_data = load_benchmark_oracle(args.oracle)
    except BenchmarkOracleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        result = run_benchmark_oracle(definition, oracle_data)
    except BenchmarkOracleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    _print_report(result, twin_info, args.oracle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
