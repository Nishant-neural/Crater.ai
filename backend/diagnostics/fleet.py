"""Phase 4 (steal item 1) — sibling / fleet search.

When this machine's history is thin, search same-family/model
machines' failure modes and recorded outcomes for the same symptom
and cite them as a fleet pattern with the machine identity named.

Crater's schema (``Product`` has a unique constraint on
manufacturer/family/model) means two physical machines of the same
model are the same Product row: sibling history is therefore the
other revisions of the same product and the other diagnostic
sessions on that product, plus — labelled as cousins, not siblings
— other models of the same family. A sibling record's machine
identity is the product identity plus its revision label, so a
fleet pattern is never an anonymous aggregate.

A new machine with no history is not a dead end: when this asset has
no failure modes or recorded outcomes, ``fallback_used`` states
plainly "manual + same-model siblings".

An outcome is matched to a symptom through its session's recorded
symptoms (outcomes do not store a symptom of their own); a failure
mode is matched on its own symptom text. No LLM calls. No live
signals (Crater has none): the sources are the canonical failure
modes, recorded outcomes, and manual tables.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from backend.db.models import (
    DiagnosticOutcome, DiagnosticSession, FailureMode, Product, Revision,
)
from backend.diagnostics.backtracking import _texts_match

FLEET_PATTERN_MIN_ASSETS = 2
FALLBACK_TEXT = ("manual + same-model siblings — this asset has no recorded "
                 "failure history of its own")


def _product_identity(product: Product) -> str:
    return f"{product.manufacturer} {product.family} {product.model}".strip()


def _session_symptoms(db: Session, session_id: str | None) -> list[str]:
    if not session_id:
        return []
    session = db.get(DiagnosticSession, session_id)
    if session is None:
        return []
    return list((session.state or {}).get("symptoms") or [])


def sibling_search(db: Session, product_id: str, symptom: str,
                   revision_id: str | None = None) -> dict[str, Any]:
    """Search this asset, its same-model siblings, and family cousins
    for the same symptom. Returns counts with the query behind each,
    the records found, and a fleet pattern when the same confirmed
    cause recurs on >= 2 distinct assets (revisions/sessions)."""
    product = db.get(Product, product_id)
    if product is None:
        return {"available": False,
                "reason": f"no product with id {product_id!r}",
                "asset_records": [], "sibling_records": [],
                "cousin_records": [], "fleet_pattern": None,
                "fallback_used": None, "counts": {}}

    revisions = db.query(Revision).filter(Revision.product_id == product.id).all()
    revision_labels = {r.id: (r.label or r.id) for r in revisions}
    asset_revision_ids = [revision_id] if revision_id else [r.id for r in revisions]
    sibling_revision_ids = [r.id for r in revisions if r.id not in asset_revision_ids]

    def _modes(revision_ids: list[str], identity: str) -> list[dict[str, Any]]:
        if not revision_ids:
            return []
        out = []
        for mode in db.query(FailureMode).filter(
                FailureMode.revision_id.in_(revision_ids)).all():
            if _texts_match(mode.symptom, symptom):
                out.append({
                    "kind": "failure_mode", "revision_id": mode.revision_id,
                    "product_id": product.id,
                    "machine_identity": identity.format(
                        rev=revision_labels.get(mode.revision_id, "")),
                    "symptom": mode.symptom,
                    "possible_causes": list(mode.possible_causes or []),
                    "diagnostic_test": mode.diagnostic_test})
        return out

    def _outcomes(revision_ids: list[str], identity: str,
                  product_filter: str | None = None) -> list[dict[str, Any]]:
        out = []
        query = db.query(DiagnosticOutcome).filter(
            DiagnosticOutcome.product_id == (product_filter or product.id))
        for row in query.all():
            if revision_ids and row.revision_id not in revision_ids:
                continue
            symptoms = _session_symptoms(db, row.session_id)
            if not any(_texts_match(s, symptom) for s in symptoms):
                continue
            out.append({
                "kind": "recorded_outcome", "outcome_id": row.id,
                "session_id": row.session_id, "product_id": row.product_id,
                "revision_id": row.revision_id,
                "machine_identity": identity.format(
                    rev=revision_labels.get(row.revision_id, "")),
                "outcome": row.outcome, "confirmed_cause": row.confirmed_cause,
                "technician_notes": row.technician_notes,
                "symptoms": symptoms})
        return out

    asset_records = (
        _modes(asset_revision_ids,
               f"{_product_identity(product)} (this asset, revision {{rev}})")
        + _outcomes(asset_revision_ids,
                    f"{_product_identity(product)} (this asset, revision {{rev}})"))
    sibling_records = (
        _modes(sibling_revision_ids,
               f"{_product_identity(product)} (sibling, revision {{rev}})")
        + _outcomes(sibling_revision_ids,
                    f"{_product_identity(product)} (sibling, revision {{rev}})"))

    # Family cousins: other models of the same family, labelled as such.
    cousin_records: list[dict[str, Any]] = []
    cousin_products = db.query(Product).filter(
        Product.manufacturer == product.manufacturer,
        Product.family == product.family,
        Product.id != product.id).all()
    for cousin in cousin_products:
        cousin_revision_ids = [r.id for r in cousin.revisions]
        if not cousin_revision_ids:
            continue
        for mode in db.query(FailureMode).filter(
                FailureMode.revision_id.in_(cousin_revision_ids)).all():
            if _texts_match(mode.symptom, symptom):
                cousin_records.append({
                    "kind": "failure_mode", "revision_id": mode.revision_id,
                    "product_id": cousin.id,
                    "machine_identity": (f"{_product_identity(cousin)} "
                                         f"(family cousin, other model)"),
                    "symptom": mode.symptom,
                    "possible_causes": list(mode.possible_causes or []),
                    "diagnostic_test": mode.diagnostic_test})

    counts = {
        "asset_failure_modes": sum(1 for r in asset_records if r["kind"] == "failure_mode"),
        "asset_recorded_outcomes": sum(1 for r in asset_records if r["kind"] == "recorded_outcome"),
        "sibling_failure_modes": sum(1 for r in sibling_records if r["kind"] == "failure_mode"),
        "sibling_recorded_outcomes": sum(1 for r in sibling_records if r["kind"] == "recorded_outcome"),
        "sibling_revisions": len(sibling_revision_ids),
        "cousin_records": len(cousin_records),
    }

    # Fleet pattern: the same confirmed cause on >= 2 distinct assets
    # (distinct revisions across asset + siblings).
    cause_assets: dict[str, set[str]] = {}
    cause_records: dict[str, list[str]] = {}
    for record in asset_records + sibling_records:
        if record["kind"] != "recorded_outcome":
            continue
        cause = (record.get("confirmed_cause") or "").strip()
        if not cause:
            continue
        asset_key = record.get("revision_id") or record.get("session_id")
        cause_assets.setdefault(cause, set()).add(asset_key)
        cause_records.setdefault(cause, []).append(record["outcome_id"])
    fleet_pattern = None
    for cause, assets in cause_assets.items():
        if len(assets) >= FLEET_PATTERN_MIN_ASSETS:
            fleet_pattern = {
                "cause": cause,
                "asset_count": len(assets),
                "assets": sorted(assets),
                "records": cause_records[cause],
                "label": (f"fleet pattern: {cause!r} confirmed on "
                          f"{len(assets)} distinct assets, "
                          f"records {', '.join(cause_records[cause])}"),
            }
            break

    return {"available": True,
            "asset_records": asset_records,
            "sibling_records": sibling_records,
            "cousin_records": cousin_records,
            "fleet_pattern": fleet_pattern,
            "fallback_used": FALLBACK_TEXT if not asset_records else None,
            "counts": counts}
