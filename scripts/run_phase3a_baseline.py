"""Phase 3A baseline runner: compile a twin from a snapshot DB exactly
the way the app's compile route does, then run the oracle on it.

Usage (from the repo root, with the build venv)::

    .venv/bin/python scripts/run_phase3a_baseline.py \
        --db ~/workspace/user/files/crater-10.db \
        --out-dir ~/workspace/goals/crater-ai-diagnosis-and-machine-model-workflow-improvement/files

The DB is opened read-only; nothing is written to it. The twin is the
current v12 / Phase 2 twin: seed rules + explicit ``simulation_rules``
only, no archetypes, no propagation engine. The oracle runs with NO
observable mapping and NO test models — in 3A there is no archetype
binding to map observable ports to twin state, and no test model —
so every row is expected to fail as unmappable / unmapped. That is the
honest near-zero baseline; if the rate is not near zero, the report
says why and nothing is tuned to raise it.

Outputs (in --out-dir): ``phase3a-baseline-report.json`` (the full
oracle report) and ``phase3a-baseline-report.md`` (the human-readable
baseline: pass rate, per-case rates, O1 gate, discriminability, and
the RANKED gap list, topology gaps first).
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pdfplumber

from backend.ingestion.table_parser import parse_troubleshooting_tables
from backend.knowledge.global_integration import _model_from_payload
from backend.simulation.compiler import compile_machine_model
from backend.simulation.expert_rules import apply_expert_seed, load_seed_for_product
from backend.simulation.oracle import (
    build_oracle_set,
    expand_cause_rows,
    load_fault_injections,
    load_vocabulary,
    run_oracle,
    validate_vocabulary,
)

DEFAULT_DB = Path.home() / "workspace" / "user" / "files" / "crater-10.db"
DEFAULT_MANUAL = Path.home() / "workspace" / "project-review-v3" / "docs" / "owner-manual.pdf"
DEFAULT_VOCAB = (Path.home() / "workspace" / "goals" /
                 "crater-ai-diagnosis-and-machine-model-workflow-improvement" / "files" /
                 "oracle_vocabulary_v1.json")
DEFAULT_OUT = DEFAULT_VOCAB.parent


def _load_snapshot(db_path: Path) -> tuple[dict, dict, dict]:
    """Read the latest snapshot model + product row, read-only."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        product = conn.execute(
            "SELECT manufacturer, family, model FROM products LIMIT 1").fetchone()
        snap = conn.execute(
            "SELECT id, version, model FROM machine_knowledge_model_snapshots "
            "ORDER BY version DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    if product is None or snap is None:
        raise SystemExit(f"no product row or snapshot in {db_path}")
    return ({"manufacturer": product[0], "family": product[1], "model": product[2]},
            {"id": snap[0], "version": snap[1]},
            json.loads(snap[2]) if isinstance(snap[2], str) else snap[2])


def _load_oracle_set(manual_path: Path, vocab: dict, registry_path: Path):
    """Ground truth = the deterministic parser on p42/p43, joined to
    the vocabulary and the registry's fault_injection mapping."""
    expanded: list[dict] = []
    with pdfplumber.open(manual_path) as pdf:
        for page_no in (42, 43):
            model = parse_troubleshooting_tables(
                pdf.pages[page_no - 1].extract_tables(),
                source_document="owner-manual.pdf", page=page_no, chunk_id=f"p{page_no}")
            if model is None:
                raise SystemExit(f"deterministic parser returned None for p{page_no}")
            expanded.extend(expand_cause_rows(model.failure_modes, model.procedures))
    rows, problems = build_oracle_set(
        expanded, vocab, fault_injections=load_fault_injections(registry_path))
    return rows, problems, expanded


def _seed_mismatch_note(seed: dict | None, applied: dict | None) -> str:
    """Amendments item 6 (3B work, recorded here): the seed speaks a
    boolean vocabulary (``tension_ok``) while the oracle vocabulary
    speaks parameters (``tension: low/normal/high``) — and the seed's
    own ``tension_ok=false`` rule is labelled 'too tight' though false
    is ambiguous (too low *or* too high). 3B translates seed rules
    into the parameter vocabulary at compile time; 3A uses the seed
    as-is and records the mismatch as a gap, not a failure to fix in
    the harness."""
    if seed is None:
        return ("No expert seed matched this product row, so no seed "
                "rules were applied at all (see below). The boolean-vs-"
                "parameter mismatch therefore does not even arise on "
                "this run — the gap is the missing seed match itself.")
    return ("The seed speaks a boolean vocabulary (``tension_ok``) "
            "while the oracle vocabulary speaks parameters (``tension: "
            "low/normal/high``); the seed's ``tension_ok=false`` rule "
            "is labelled 'too tight' though false is ambiguous (too "
            "low *or* too high). 3B translates seed rules into the "
            "parameter vocabulary at compile time; 3A uses the seed "
            f"as-is ({len(applied['rules_applied'])} rule(s) applied, "
            f"{len(applied['skipped'])} skipped) and records the "
            "mismatch as a gap.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--manual", type=Path, default=DEFAULT_MANUAL)
    parser.add_argument("--vocabulary", type=Path, default=DEFAULT_VOCAB)
    parser.add_argument("--registry", type=Path,
                        default=Path(__file__).resolve().parents[1] /
                        "backend" / "knowledge" / "repair_cases_omnipro.json")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    vocab = load_vocabulary(args.vocabulary)
    vocab_errors = validate_vocabulary(vocab)
    if vocab_errors:
        raise SystemExit(f"vocabulary invalid: {vocab_errors}")

    product, snap_info, snapshot_model = _load_snapshot(args.db)
    model = _model_from_payload(snapshot_model)
    # Exactly the app compile route's seed step (simulation.py):
    # load by product manufacturer/family/model, unique-name resolution only.
    seed = load_seed_for_product(product["manufacturer"], product["model"], product["family"])
    seed_warnings: list[str] = []
    applied = {"rules_applied": [], "states_applied": [], "skipped": []}
    if seed is not None:
        model, seed_warnings, applied = apply_expert_seed(model, seed)
    definition, warnings = compile_machine_model(
        model, name=f"{product['manufacturer']} {product['model']} — Phase 3A baseline twin",
        description="Phase 3A baseline: seed + explicit simulation_rules only.",
    )
    warnings = list(warnings) + seed_warnings

    oracle_rows, problems, expanded = _load_oracle_set(args.manual, vocab, args.registry)
    report = run_oracle(definition.model_dump(mode="json"), oracle_rows,
                        vocab["cases"], problems=problems)

    # The crater-10 DB predates the Phase 2 dedupe fix, so the compiled
    # twin still carries the duplicate names that blocked the seed in
    # Phase 0. Count them for the fault instances the oracle needs:
    # an instance resolving to 0 or 2+ components is unmappable.
    from collections import Counter as _Counter
    _name_counts = _Counter(
        re.sub(r"\s+", " ", c.name.lower()).strip() for c in definition.components)
    _instances = sorted({(row.fault or {}).get("component") for row in oracle_rows
                         if row.fault})
    duplicate_instances = [
        (name, _name_counts[re.sub(r"\s+", " ", name.lower()).strip()])
        for name in _instances
        if _name_counts[re.sub(r"\s+", " ", name.lower()).strip()] != 1
    ]

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    summary = {
        "generated_at": stamp,
        "inputs": {
            "db": str(args.db), "snapshot_id": snap_info["id"],
            "snapshot_version": snap_info["version"],
            "product": product, "manual": str(args.manual),
            "vocabulary": str(args.vocabulary),
            "vocabulary_version": vocab.get("version"),
        },
        "snapshot_counts": {
            "entities": len(model.entities), "relations": len(model.relations),
            "failure_modes": len(model.failure_modes),
            "quantities": len(model.quantities),
            "simulation_rules_in_snapshot": len(snapshot_model.get("simulation_rules") or []),
        },
        "seed": {
            "matched": seed is not None,
            "seed_id": (seed or {}).get("id"),
            "states_applied": applied["states_applied"],
            "rules_applied": applied["rules_applied"],
            "skipped": applied["skipped"],
            "warnings": seed_warnings,
            "vocabulary_mismatch_note": _seed_mismatch_note(seed, applied),
        },
        "twin": {
            "components": len(definition.components),
            "transitions": len(definition.transitions),
            "transition_ids": [t.id for t in definition.transitions],
            "topology_edges": len((definition.metadata or {}).get("topology") or []),
            "compiler_warnings": warnings,
        },
        "oracle": report.to_dict(),
        "duplicate_fault_instances": [
            {"instance": name, "components_in_twin": count}
            for name, count in duplicate_instances
        ],
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "phase3a-baseline-report.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    headline = report.headline
    lines = [
        "# Phase 3A baseline report — oracle on the Phase 2 twin",
        "",
        f"Generated {stamp}. Twin compiled from `{args.db}` (snapshot v{snap_info['version']}), "
        f"product {product['manufacturer']} / {product['family']} / {product['model']}. "
        "Ground truth: the deterministic parser on p42/p43 (45 cause rows) joined to "
        "`oracle_vocabulary_v1.json` and the registry's `fault_injection` mapping. "
        "The oracle proves the twin matches the manual's claims, not that the machine "
        "behaves that way; field data is the only real ground truth.",
        "",
        "## Twin",
        "",
        f"- Components: **{len(definition.components)}**, transitions: "
        f"**{len(definition.transitions)}** "
        f"({', '.join(t.id for t in definition.transitions) or 'none'}), "
        f"topology edges: **{len((definition.metadata or {}).get('topology') or [])}**.",
        f"- Snapshot model: {len(model.entities)} entities, {len(model.relations)} relations, "
        f"{len(model.failure_modes)} failure modes, "
        f"{len(snapshot_model.get('simulation_rules') or [])} explicit simulation rules.",
        "",
        "## Duplicates in this (pre-Phase-2) twin",
        "",
        "This DB was integrated before the Phase 2 dedupe fix, so the compiled twin still "
        "carries the duplicate names that blocked the seed in Phase 0. A fault instance "
        "that resolves to 0 or 2+ components is unmappable — that is 29 of the 35 rows:",
        "",
    ] + [
        f"- {name}: **{count}** component(s) in the twin" for name, count in duplicate_instances
    ] + [
        "",
        "The consolidated Phase 1+2 live check (fresh upload on the Phase 2 package) is "
        "what removes these duplicates; this baseline measures the twin as it is, not as "
        "the fixed code would build it.",
        "",
        "## Seed",
        "",
    ]
    if seed is None:
        lines += [
            "- **No expert seed matched this product row.** `load_seed_for_product` looks "
            "for a seed whose product block is manufacturer `Harbor Freight`, model "
            "`OMNIPRO 220`; this DB's product row is model `220`, so no seed applied — "
            "the compiled twin has 0 transitions. (Separately, applying the seed file "
            "directly would still yield 0 rules on this snapshot: the snapshot has 7 "
            "Feed Tensioners, so the tensioner rules skip as ambiguous, and the Wingnut "
            "rules reference `Wire Spool`, which has no entity in this snapshot.)",
            "- This is a finding, not a harness defect: the seed never reaches this twin "
            "under the app's own compile route.",
        ]
    else:
        lines += [
            f"- Seed `{seed.get('id')}` matched: {len(applied['rules_applied'])} rule(s) applied, "
            f"{len(applied['states_applied'])} state(s) applied, {len(applied['skipped'])} skipped.",
        ]
        for skipped in applied["skipped"]:
            lines.append(f"  - skipped: {skipped}")
    lines += [
        "",
        "**Seed vocabulary mismatch (Amendments item 6 — 3B work, recorded here):** "
        + _seed_mismatch_note(seed, applied),
        "",
        "## Headline",
        "",
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
        "## Per-case rates (twin-expressible rows only)",
        "",
    ]
    for case_id in sorted(report.per_case):
        entry = report.per_case[case_id]
        rate = f" = {entry['rate']:.0%}" if entry["rate"] is not None else ""
        lines.append(f"- {case_id}: **{entry['passed']}/{entry['denominator']}**{rate} "
                     f"(categories in case: {entry['categories']})")
    lines += [
        "",
        "## Ranked gap list (topology gaps first, then by rows blocked)",
        "",
    ]
    for gap in report.gap_list:
        lines.append(f"- **{gap['reason']}** — blocks {gap['rows_blocked']} row(s): "
                     f"{', '.join(gap['row_ids'][:10])}"
                     + (" …" if len(gap['row_ids']) > 10 else ""))
    lines += [
        "",
        "## What this does not mean",
        "",
        "- A near-zero rate is the expected 3A baseline (v3 §6.6), not a regression: "
        "this twin has no archetypes, no propagation engine, no observable mapping "
        "and no test models, so no fault can be expressed and no symptom reproduced. "
        "Nothing was tuned to raise the rate.",
        "- The gap list above is the 3B work list, ordered by how many oracle rows "
        "each gap blocks. 3B is decided from it, not started by this report.",
        "- The live check (fresh OMNIPRO upload/job on the Phase 2 package) is still "
        "outstanding; no harness replaces it.",
        "",
    ]
    if problems:
        lines += ["## Oracle-set problems", ""] + [f"- {p}" for p in problems] + [""]
    (args.out_dir / "phase3a-baseline-report.md").write_text(
        "\n".join(lines), encoding="utf-8")

    print(f"headline: {headline['passed']}/{headline['denominator']} "
          f"(O1 gate: {headline['o1_gate']})")
    print(f"denominator: {report.denominator}")
    print(f"discriminability: {report.discriminability['pairs_separated']}/"
          f"{report.discriminability['pairs_total']}")
    for gap in report.gap_list[:5]:
        print(f"gap: {gap['reason']} blocks {gap['rows_blocked']} rows")
    print(f"wrote {args.out_dir / 'phase3a-baseline-report.json'}")
    print(f"wrote {args.out_dir / 'phase3a-baseline-report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
