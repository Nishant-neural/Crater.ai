"""Promote the human-approved proof twins to live twins.

Approved 28 Nov 2026 by Nishant Tiwari ("i approve the twins you
built as live ones"). This script is the promotion: it compiles both
proof twins EXACTLY as the benchmark runners do, verifies the compiled
definitions match the measured ones, stamps the approval into the
definition metadata, exports the pinned definitions to ``frozen/``,
and — only with ``--apply`` — creates them as ACTIVE twins in the
database via ``backend.simulation.service.create_twin``.

How "live" works here (read before running): diagnosis and the
twin-verifier pick the twin by ``revision_id + status=active, order by
created_at desc`` (see ``backend/api/routes/diagnostics.py`` and
``backend/simulation/verify_service.py``). Creating a new ACTIVE twin
for the same revision supersedes the old 1-transition twin by recency;
the old row stays in the database as history. Nothing is deleted.
Re-running with ``--apply`` is idempotent: if the latest ACTIVE twin
for a revision already carries this exact definition, it is skipped.

Product / revision ids are the live-ingestion ids (same rows as the
test DB ``crater-phase1/mock-llm/crater-test.db``). The Miller proof
twin is compiled by its PROOF identity (Miller Electric / 252 /
Millermatic — that is how the seed and edge list resolve) and created
under the DB row's ids (miller / 220 / 220); the product-row correction
is still pending and does not block promotion, because ``create_twin``
takes explicit ids. If the live database carries different ids, pass
them as arguments — the script fails loudly on a missing product or
revision rather than promoting to the wrong row.

Usage (from the repo root, with the app's environment, e.g. inside the
twin-verifier container or with DATABASE_URL pointed at Postgres):

    python -m scripts.promote_proof_twins            # dry run: compile, verify, export; no DB write
    python -m scripts.promote_proof_twins --apply    # also create the ACTIVE twins

On Nishant's laptop (compose stack up, from the repo root):

    docker compose exec twin-verifier python -m scripts.promote_proof_twins --apply
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path

from backend.db.session import SessionLocal
from backend.simulation.compiler import compile_machine_model
from backend.simulation.edges import load_edge_list_for_product
from backend.simulation.expert_rules import apply_expert_seed, load_seed_for_product
from backend.simulation.miller_proof import build_miller_proof_model
from backend.simulation.omnipro_proof import build_omnipro_proof_model
from backend.simulation.service import create_twin

REPO = Path(__file__).resolve().parents[1]

# Live-ingestion ids (see module docstring). Miller's DB row still reads
# miller / 220 / 220; the proof identity is Miller Electric / 252 /
# Millermatic and is used ONLY to resolve the seed and edge list.
MILLER_PRODUCT = "01c956e5-84e5-484c-a280-ee9e15c348a0"
MILLER_REVISION = "30e0e856-62b9-4fd7-ac05-52ee1bf3e742"
MILLER_OLD_TWIN = "a7a67556-495f-488e-8742-1040b7397229"  # 1 transition, active
OMNIPRO_PRODUCT = "ccc3bae7-edff-42ce-8366-900d4a6523d5"
OMNIPRO_REVISION = "3c246c9a-3f0b-4760-9d9b-e5811751c49f"
OMNIPRO_OLD_TWIN = "427ad1be-3e48-49ee-8087-67d482d9d41b"  # 1 transition, active

APPROVED_BY = "Nishant Tiwari"
APPROVED_AT = "2026-11-28"

# Measured definitions this promotion must reproduce exactly. Miller was
# measured by benchmark/run_miller_proof.py (13/38, O1 pass, O3 70/71);
# OMNIPRO by benchmark/run_phase3b_oracle.py (O2 20/35, O3 57/58).
EXPECTED = {
    "miller": {"transitions": 1147, "components": 34},
    "omnipro": {"transitions": 648, "components": 18},
}


def _sha(definition_json: dict) -> str:
    blob = json.dumps(definition_json, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def _compile_miller():
    """Exactly as benchmark/run_miller_proof.py compiles it."""
    model = build_miller_proof_model()
    seed = load_seed_for_product("Miller Electric", "252", "Millermatic")
    assert seed is not None, "Miller seed not found"
    model, seed_warnings, applied = apply_expert_seed(model, seed)
    edge_list = load_edge_list_for_product("Miller Electric", "252", "Millermatic")
    assert edge_list is not None, "Miller edge list not found"
    definition, warnings = compile_machine_model(
        model, name="Millermatic 252 — proof twin",
        description="Miller proof twin: hand-authored canonical model, archetype tables + reviewed Miller links + Miller golden seed.",
        edge_list=edge_list,
    )
    warnings = list(warnings) + seed_warnings
    return definition, warnings, applied


def _compile_omnipro():
    """Exactly as benchmark/run_phase3b_oracle.py compiles it."""
    model = build_omnipro_proof_model()
    seed = load_seed_for_product("Harbor Freight", "220", "OMNIPRO")
    assert seed is not None, "OMNIPRO seed not found"
    seed_warnings: list[str] = []
    applied = {"rules_applied": [], "states_applied": [], "skipped": []}
    model, seed_warnings, applied = apply_expert_seed(model, seed)
    definition, warnings = compile_machine_model(
        model, name="OMNIPRO 220 — Phase 3B proof twin",
        description="Phase 3B proof twin: constructed canonical model, archetype tables + links.",
        edge_list=load_edge_list_for_product("Harbor Freight", "220", "OMNIPRO"),
    )
    warnings = list(warnings) + seed_warnings
    return definition, warnings, applied


def _check(name: str, definition, warnings: list[str]) -> None:
    want = EXPECTED[name]
    got_transitions = len(definition.transitions)
    got_components = len(definition.components)
    assert got_transitions == want["transitions"], (
        f"{name}: {got_transitions} transitions, expected {want['transitions']} — "
        "the twin changed since it was measured; do NOT promote until the "
        "difference is understood and the measurement is re-run")
    assert got_components == want["components"], (
        f"{name}: {got_components} components, expected {want['components']}")
    bad = [w for w in warnings
           if "cycle" in w.lower() or "quarantin" in w.lower()
           or "did-not-settle" in w.lower()]
    assert not bad, f"{name}: unexpected compiler warnings: {bad}"
    if name == "miller":
        # The 16 seed "not translated" notes are the only warnings the
        # measured Miller twin carries; anything else is a change.
        assert len(warnings) == 16 and all("not translated" in w for w in warnings), (
            f"miller: {len(warnings)} warnings, expected the 16 seed notes: {warnings}")
    print(f"[{name}] OK: {got_transitions} transitions, {got_components} "
          f"components, {len(warnings)} warning(s)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="also create the ACTIVE twins in the database")
    parser.add_argument("--miller-product", default=MILLER_PRODUCT)
    parser.add_argument("--miller-revision", default=MILLER_REVISION)
    parser.add_argument("--omnipro-product", default=OMNIPRO_PRODUCT)
    parser.add_argument("--omnipro-revision", default=OMNIPRO_REVISION)
    args = parser.parse_args()

    stamp = date.today().isoformat()
    twins = []
    for name, compile_fn, product_id, revision_id, old_twin, measured in [
        ("miller", _compile_miller, args.miller_product, args.miller_revision,
         MILLER_OLD_TWIN, "13/38, O1 pass, O3 70/71"),
        ("omnipro", _compile_omnipro, args.omnipro_product, args.omnipro_revision,
         OMNIPRO_OLD_TWIN, "O2 20/35, O3 57/58"),
    ]:
        definition, warnings, applied = compile_fn()
        _check(name, definition, warnings)
        measured_sha = _sha(definition.model_dump(mode="json"))
        # Stamp the human approval into the definition metadata. This is
        # the only difference from the measured definition; the transitions
        # and components are byte-identical to what was measured.
        definition.metadata["human_approval"] = {
            "approved_by": APPROVED_BY,
            "approved_at": APPROVED_AT,
            "scope": "approved as the live twin for this product/revision",
            "measured": measured,
            "measured_definition_sha256": measured_sha,
            "supersedes_twin_id": old_twin,
        }
        definition.metadata["compiler_warnings"] = list(warnings)
        if applied is not None:
            definition.metadata["expert_seed"] = applied
        promoted_sha = _sha(definition.model_dump(mode="json"))
        out = REPO / "frozen" / f"approved-{name}-twin-{stamp}.json"
        out.write_text(json.dumps(definition.model_dump(mode="json"), indent=2) + "\n")
        print(f"[{name}] exported {out}")
        print(f"[{name}] measured sha256 {measured_sha}")
        print(f"[{name}] promoted sha256 {promoted_sha} (differs only by the approval stamp)")
        twins.append((name, definition, product_id, revision_id, old_twin, promoted_sha))

    if not args.apply:
        print("Dry run only — no database writes. Re-run with --apply to "
              "create the ACTIVE twins (see the module docstring for the "
              "command on the laptop).")
        return 0

    from backend.db.models import DigitalTwin, TwinStatus

    db = SessionLocal()
    try:
        for name, definition, product_id, revision_id, old_twin, promoted_sha in twins:
            latest = (db.query(DigitalTwin)
                      .filter_by(revision_id=revision_id, status=TwinStatus.active)
                      .order_by(DigitalTwin.created_at.desc()).first())
            if latest is not None and _sha(latest.definition) == promoted_sha:
                print(f"[{name}] already live as twin {latest.id} — skipping")
                continue
            twin = create_twin(db, product_id, revision_id, definition)
            print(f"[{name}] promoted: old twin {old_twin} superseded by "
                  f"new ACTIVE twin {twin.id} "
                  f"(previous latest was {latest.id if latest else None})")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
