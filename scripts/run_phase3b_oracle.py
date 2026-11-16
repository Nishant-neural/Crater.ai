"""Phase 3B oracle runner: the proof twin + crater-10, both with compiler v3.

Usage (from the repo root, with the build venv)::

    .venv/bin/python scripts/run_phase3b_oracle.py \
        --out-dir ~/workspace/goals/crater-ai-diagnosis-and-machine-model-workflow-improvement/files

Two oracle runs, both with the same ground truth (the deterministic
parser on p42/p43, 45 cause rows, joined to ``oracle_vocabulary_v1.json``
and the registry's ``fault_injection`` mapping):

1. The PROOF twin: the constructed OMNIPRO canonical model
   (``backend/simulation/omnipro_proof.py`` — unique entities, typed
   ports, bound parameters, human-confirmed edges), seed applied,
   compiled by compiler v3, run with the port-qualified observable
   mapping + the three case-1 test models. The 3B gate is case 1 =
   100% (4/4 cause rows) here.
2. The CRATER-10 twin: the read-only baseline snapshot compiled by
   compiler v3, run with NO observable mapping and NO test models
   (same as the 3A baseline — the snapshot has 0 ports, so nothing
   binds). Reported honestly; expected to stay near zero. The
   blockers are the snapshot's duplicates + 0 ports, not the
   archetype engine.

Then the tournament/verify path for the 4 case-1 causes (Feed Tensioner
VALIDATED from the A4 tables + RepairAction) and the Wingnut (VALIDATED
through the seed overrides on the verify path — A9 storage vocabulary
arrives in 3C, so the Wingnut is NOT validated from archetype tables
in 3B; see PHASE3B_BUILD_NOTES.md).

Outputs (in --out-dir): ``phase3b-oracle-report.json`` (both oracle
reports + the tournament/verify results) and ``phase3b-oracle-report.md``.
The DB is opened read-only; nothing is written to it.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend.knowledge.global_integration import _model_from_payload
from backend.simulation.compiler import compile_machine_model
from backend.simulation.engine import demo_definition
from backend.simulation.expert_rules import apply_expert_seed, load_seed_for_product
from backend.simulation.omnipro_proof import (
    build_omnipro_proof_model,
    initial_twin_state,
    observable_paths,
    snapshot_model_for_tournament,
    build_test_models,
)
from backend.simulation.oracle import load_vocabulary, run_oracle, validate_vocabulary
from backend.simulation.repair_actions import load_registry_causes
from backend.simulation.tournament import verify_single_cause
from backend.simulation.verification import run_verified_repair
from run_phase3a_baseline import _load_oracle_set, _load_snapshot

DEFAULT_MANUAL = Path.home() / "workspace" / "project-review-v3" / "docs" / "owner-manual.pdf"
DEFAULT_VOCAB = (Path.home() / "workspace" / "goals" /
                 "crater-ai-diagnosis-and-machine-model-workflow-improvement" / "files" /
                 "oracle_vocabulary_v1.json")
DEFAULT_OUT = DEFAULT_VOCAB.parent
DEFAULT_DB = Path.home() / "workspace" / "user" / "files" / "crater-10.db"
DEFAULT_REGISTRY = (Path(__file__).resolve().parents[1] / "backend" / "knowledge" /
                    "repair_cases_omnipro.json")


def _render(report, title: str, twin_note: str) -> list[str]:
    headline = report.headline
    lines = [
        f"## {title}",
        "",
        twin_note,
        "",
        f"- Components: **{report.twin_components}**, transitions: "
        f"**{report.twin_transitions}**, topology edges: **{report.topology_edges}**.",
        f"- **Oracle pass rate (headline, over the 35 twin-expressible rows): "
        f"{headline['passed']}/{headline['denominator']}**"
        + (f" = {headline['rate']:.1%}" if headline["rate"] is not None else ""),
        f"- O1 nominal gate: **{headline['o1_gate']}** — {headline['o1_gate_note']}",
        f"- Denominator breakdown: {report.denominator}",
        f"- Discriminability (O3, same-symptom pairs separated): "
        f"{report.discriminability['pairs_separated']}/{report.discriminability['pairs_total']}"
        + (f" = {report.discriminability['rate']:.1%}"
           if report.discriminability["rate"] is not None else ""),
        "",
        "### Per-case rates (twin-expressible rows only)",
        "",
    ]
    for case_id in sorted(report.per_case):
        entry = report.per_case[case_id]
        rate = f" = {entry['rate']:.0%}" if entry["rate"] is not None else ""
        lines.append(f"- {case_id}: **{entry['passed']}/{entry['denominator']}**{rate} "
                     f"(categories in case: {entry['categories']})")
    lines += ["", "### Ranked gap list (topology gaps first, then by rows blocked)", ""]
    for gap in report.gap_list:
        lines.append(f"- **{gap['reason']}** — blocks {gap['rows_blocked']} row(s): "
                     f"{', '.join(gap['row_ids'][:10])}"
                     + (" …" if len(gap['row_ids']) > 10 else ""))
    lines += [""]
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--manual", type=Path, default=DEFAULT_MANUAL)
    parser.add_argument("--vocabulary", type=Path, default=DEFAULT_VOCAB)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    vocab = load_vocabulary(args.vocabulary)
    vocab_errors = validate_vocabulary(vocab)
    if vocab_errors:
        raise SystemExit(f"vocabulary invalid: {vocab_errors}")
    oracle_rows, problems, expanded = _load_oracle_set(args.manual, vocab, args.registry)

    # ---- 1. Proof twin ----
    proof_model = build_omnipro_proof_model()
    seed = load_seed_for_product("Harbor Freight", "220", "OMNIPRO")
    seed_warnings: list[str] = []
    applied = {"rules_applied": [], "states_applied": [], "skipped": []}
    if seed is not None:
        proof_model, seed_warnings, applied = apply_expert_seed(proof_model, seed)
    proof_definition, proof_warnings = compile_machine_model(
        proof_model, name="OMNIPRO 220 — Phase 3B proof twin",
        description="Phase 3B proof twin: constructed canonical model, archetype tables + links.",
    )
    proof_warnings = list(proof_warnings) + seed_warnings
    proof_report = run_oracle(
        proof_definition.model_dump(mode="json"), oracle_rows, vocab["cases"],
        observable_paths=observable_paths(proof_definition),
        test_models=build_test_models(proof_definition),
        problems=problems)

    # ---- 2. crater-10 with compiler v3 (honest re-measure) ----
    product, snap_info, snapshot_model = _load_snapshot(args.db)
    crater_model = _model_from_payload(snapshot_model)
    crater_seed = load_seed_for_product(product["manufacturer"], product["model"], product["family"])
    crater_seed_warnings: list[str] = []
    crater_applied = {"rules_applied": [], "states_applied": [], "skipped": []}
    if crater_seed is not None:
        crater_model, crater_seed_warnings, crater_applied = apply_expert_seed(
            crater_model, crater_seed)
    crater_definition, crater_warnings = compile_machine_model(
        crater_model,
        name=f"{product['manufacturer']} {product['model']} — Phase 3B crater-10 twin",
        description="Phase 3B: crater-10 snapshot compiled by compiler v3.",
    )
    crater_warnings = list(crater_warnings) + crater_seed_warnings
    crater_report = run_oracle(
        crater_definition.model_dump(mode="json"), oracle_rows, vocab["cases"],
        problems=problems)

    # ---- 3. Tournament / verify path (case 1 + Wingnut) ----
    snapshot = snapshot_model_for_tournament()
    twin_state = initial_twin_state(proof_definition)
    verify_rows: list[dict[str, Any]] = []
    for cause in load_registry_causes(args.registry):
        if cause["case_id"] != "omni-01-wire-not-feeding":
            continue
        row = verify_single_cause(
            proof_definition, twin_state, snapshot,
            _FakeState(), cause["cause"], cause["fix"])
        verify_rows.append({
            "case_id": cause["case_id"], "cause_letter": cause["cause_letter"],
            "cause": cause["cause"], "available": row["available"],
            "verdict": row["verdict"], "reason": row["reason"],
            "repair_action_id": row.get("repair_action_id"),
            "trace_note": row.get("trace_note"),
            "failure_to_behaviour": row.get("failure_to_behaviour"),
            "fault_component_id": row.get("fault_component_id"),
            "fault_component_name": row.get("fault_component_name"),
        })
    wingnut = run_verified_repair(
        proof_definition, twin_state, fault_component_id="wingnut",
        repair_name="Tighten the Wingnut to secure the spool",
        hypothesis="Tightening the Wingnut stops the Wire Spool spinning freely.")
    wingnut_row = {
        "component": "Wingnut", "available": wingnut.available,
        "verdict": wingnut.verdict, "reason": wingnut.reason,
        "symptom_path": wingnut.symptom_path,
        "symptom_expected": wingnut.symptom_expected,
        "fault_rules": [r["id"] for r in wingnut.fault_rules],
        "recovery_rules": [r["id"] for r in wingnut.recovery_rules],
        "note": ("Wingnut validated through the seed's rule-11 overrides on "
                 "the verify path, NOT from archetype tables (A9 storage "
                 "vocabulary arrives in 3C). See PHASE3B_BUILD_NOTES.md."),
    }

    demo_transitions = len(demo_definition().transitions)

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    summary = {
        "generated_at": stamp,
        "inputs": {
            "manual": str(args.manual), "vocabulary": str(args.vocabulary),
            "vocabulary_version": vocab.get("version"), "db": str(args.db),
            "snapshot_id": snap_info["id"], "snapshot_version": snap_info["version"],
        },
        "demo_motor_drive_transitions": demo_transitions,
        "proof_twin": {
            "components": len(proof_definition.components),
            "transitions": len(proof_definition.transitions),
            "transition_ids": [t.id for t in proof_definition.transitions],
            "topology_edges": len((proof_definition.metadata or {}).get("topology") or []),
            "compiler_warnings": proof_warnings,
            "seed": {"matched": seed is not None, "seed_id": (seed or {}).get("id"),
                     "rules_applied": applied["rules_applied"],
                     "states_applied": applied["states_applied"],
                     "skipped": applied["skipped"], "warnings": seed_warnings},
            "metadata": proof_definition.metadata,
        },
        "crater10_twin": {
            "components": len(crater_definition.components),
            "transitions": len(crater_definition.transitions),
            "transition_ids": [t.id for t in crater_definition.transitions],
            "topology_edges": len((crater_definition.metadata or {}).get("topology") or []),
            "compiler_warnings": crater_warnings,
            "seed": {"matched": crater_seed is not None,
                     "seed_id": (crater_seed or {}).get("id"),
                     "rules_applied": crater_applied["rules_applied"],
                     "states_applied": crater_applied["states_applied"],
                     "skipped": crater_applied["skipped"],
                     "warnings": crater_seed_warnings},
            "metadata": crater_definition.metadata,
        },
        "proof_oracle": proof_report.to_dict(),
        "crater10_oracle": crater_report.to_dict(),
        "tournament_verify_case1": verify_rows,
        "wingnut_verify": wingnut_row,
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "phase3b-oracle-report.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    lines = [
        "# Phase 3B oracle report — proof twin + crater-10, compiler v3",
        "",
        f"Generated {stamp}. Ground truth: the deterministic parser on p42/p43 "
        "(45 cause rows) joined to `oracle_vocabulary_v1.json` and the registry's "
        "`fault_injection` mapping. The oracle proves the twin matches the manual's "
        "claims, not that the machine behaves that way; field data is the only real "
        "ground truth.",
        "",
        f"- Demo Motor Drive transitions: **{demo_transitions}** (gate: exactly 7).",
        "",
    ]
    lines += _render(
        proof_report, "Proof twin (constructed OMNIPRO canonical model, compiler v3)",
        "The 3B gate twin: unique entities, typed ports, bound parameters, "
        "human-confirmed edges, seed applied. Observable mapping + the three "
        "case-1 test models are supplied.")
    lines += _render(
        crater_report, "crater-10 twin (baseline snapshot, compiler v3)",
        "The read-only baseline snapshot compiled by compiler v3, run with NO "
        "observable mapping and NO test models (same as the 3A baseline — the "
        "snapshot has 0 ports, so nothing binds). Expected to stay near zero; "
        "the blockers are the snapshot's duplicates + 0 ports, not the "
        "archetype engine.")
    lines += [
        "## Tournament / verify path (case 1)",
        "",
        "Each case-1 cause run through `verify_single_cause` on the proof twin "
        "(fault_injection-first resolution, RepairAction path).",
        "",
    ]
    for row in verify_rows:
        lines.append(
            f"- ({row['cause_letter']}) {row['cause']} — available: **{row['available']}**, "
            f"verdict: **{row['verdict']}**, action: `{row['repair_action_id']}`, "
            f"trace: {row['trace_note']}"
            + (f", reason: {row['reason']}" if row["reason"] else ""))
    lines += [
        "",
        "## Wingnut verify",
        "",
        f"- Wingnut — available: **{wingnut_row['available']}**, verdict: "
        f"**{wingnut_row['verdict']}**, symptom: `{wingnut_row['symptom_path']}` "
        f"(expected {wingnut_row['symptom_expected']!r}), fault rules: "
        f"{wingnut_row['fault_rules']}, recovery rules: {wingnut_row['recovery_rules']}.",
        f"- {wingnut_row['note']}",
        "",
        "## What this does not mean",
        "",
        "- The proof twin is constructed, not ingested: the gate proves the "
        "archetype engine + compiler v3 reproduce the manual's case-1 "
        "relationships on a canonical model with unique entities and typed "
        "ports. It does not prove ingestion builds such a model — the "
        "consolidated Phase 1+2 live check (fresh OMNIPRO upload/job) is "
        "still outstanding and is the precondition for a 3B proof on a fresh "
        "live snapshot.",
        "- The crater-10 rate measures the old snapshot, not the 3B code: the "
        "snapshot predates the Phase 2 dedupe fix and carries 0 ports.",
        "",
    ]
    if problems:
        lines += ["## Oracle-set problems", ""] + [f"- {p}" for p in problems] + [""]
    (args.out_dir / "phase3b-oracle-report.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"proof headline: {proof_report.headline['passed']}/{proof_report.headline['denominator']} "
          f"(O1 gate: {proof_report.headline['o1_gate']})")
    print(f"proof per-case: { {k: (v['passed'], v['denominator']) for k, v in proof_report.per_case.items()} }")
    print(f"proof O3: {proof_report.discriminability['pairs_separated']}/{proof_report.discriminability['pairs_total']}")
    print(f"crater-10 headline: {crater_report.headline['passed']}/{crater_report.headline['denominator']} "
          f"(O1 gate: {crater_report.headline['o1_gate']})")
    print(f"demo transitions: {demo_transitions}")
    for row in verify_rows:
        print(f"verify ({row['cause_letter']}): available={row['available']} verdict={row['verdict']} "
              f"action={row['repair_action_id']}")
    print(f"wingnut: available={wingnut_row['available']} verdict={wingnut_row['verdict']}")
    print(f"wrote {args.out_dir / 'phase3b-oracle-report.json'}")
    print(f"wrote {args.out_dir / 'phase3b-oracle-report.md'}")
    return 0


class _FakeState:
    """Minimal DiagnosticState stand-in for the runner: the new
    fault_injection-first path does not read the session state (the
    legacy fallback reads current_step.procedure_steps only when no
    snapshot procedure matches)."""

    hypotheses: list = []
    current_step = None


if __name__ == "__main__":
    raise SystemExit(main())
