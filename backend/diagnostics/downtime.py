"""Phase 5 (steals 8–9) — downtime record + batch bad-actor ranking.

Item 8 — downtime record: a structured record per diagnosis/repair
(asset, symptom, cause, confirmed/unconfirmed state, duration — null
while the outage is ongoing, never zero — safety_flagged) in a shape a
report can consume. Twin VALIDATED vs field SUCCESSFUL stay separate
statuses; this record carries neither, only the diagnosis state.

Item 9 — batch bad-actor ranking: across machines in the DB, rank by
total downtime minutes AND by failure frequency, computed ONLY from
recorded outcomes. With no outcome data, report "no outcome data"
honestly rather than inventing a ranking. An asset with no history is
not a good actor: assets with sessions but no outcomes are listed
separately as unreported. The window and per-asset coverage are stated;
reason-code coverage is a finding of its own.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.db.models import (
    DiagnosticOutcome, DiagnosticSession, DowntimeRecord, Product,
)


def start_downtime(db: Any, session: DiagnosticSession) -> DowntimeRecord:
    """Open a downtime record when a diagnosis starts. The outage clock
    starts at the session (the honest recorded start — the machine may
    have failed earlier, and this record does not claim otherwise).
    Duration is null while the outage is ongoing, never zero."""
    existing = db.query(DowntimeRecord).filter_by(session_id=session.id).first()
    if existing is not None:
        return existing
    state = session.state or {}
    record = DowntimeRecord(
        session_id=session.id, product_id=session.product_id,
        symptom="; ".join(state.get("symptoms") or []) or "(no symptom recorded)",
        cause=None, confirmed=False,
        started_at=getattr(session, "created_at", None) or datetime.utcnow(),
        ended_at=None, duration_minutes=None, safety_flagged=False)
    db.add(record)
    db.flush()
    return record


def close_downtime(db: Any, session_id: str, cause: str | None = None,
                   confirmed: bool = False, safety_flagged: bool = False,
                   now: datetime | None = None) -> DowntimeRecord | None:
    """Close the downtime record for a session (called when the outcome
    is recorded). Duration is the elapsed minutes, minimum 1 — a closed
    outage lasted some positive time. If no record was opened (sessions
    started before Phase 5), one is created from the session row."""
    record = db.query(DowntimeRecord).filter_by(session_id=session_id).first()
    if record is None:
        session = db.get(DiagnosticSession, session_id)
        if session is None:
            return None
        record = start_downtime(db, session)
    end = now or datetime.utcnow()
    start = record.started_at or end
    elapsed_minutes = max(1, int(round((end - start).total_seconds() / 60.0)))
    record.ended_at = end
    record.duration_minutes = elapsed_minutes
    record.cause = cause if cause is not None else record.cause
    record.confirmed = bool(confirmed)
    record.safety_flagged = bool(safety_flagged)
    db.commit()
    return record


def downtime_report(db: Any, session_id: str) -> dict[str, Any] | None:
    record = db.query(DowntimeRecord).filter_by(session_id=session_id).first()
    if record is None:
        return None
    return {"session_id": record.session_id, "product_id": record.product_id,
            "symptom": record.symptom, "cause": record.cause,
            "confirmed": bool(record.confirmed),
            "started_at": record.started_at.isoformat() if record.started_at else None,
            "ended_at": record.ended_at.isoformat() if record.ended_at else None,
            "duration_minutes": record.duration_minutes,
            "ongoing": record.ended_at is None,
            "safety_flagged": bool(record.safety_flagged)}


def bad_actor_ranking(db: Any) -> dict[str, Any]:
    """Rank machines by total downtime minutes AND by failure frequency,
    from recorded outcomes only. No outcome data -> "no outcome data",
    never an invented ranking."""
    outcomes = db.query(DiagnosticOutcome).all()
    if not outcomes:
        return {"available": False,
                "reason": "no outcome data — no recorded repair outcomes "
                          "exist, so no ranking can be computed",
                "by_downtime_minutes": [], "by_failure_frequency": [],
                "unreported_assets": [], "window": None, "coverage": {}}
    products = {p.id: p for p in db.query(Product).all()}
    downtime_by_product: dict[str, int] = {}
    for record in db.query(DowntimeRecord).filter(
            DowntimeRecord.duration_minutes.isnot(None)).all():
        downtime_by_product[record.product_id] = (
            downtime_by_product.get(record.product_id, 0)
            + int(record.duration_minutes or 0))
    frequency: dict[str, int] = {}
    cause_coverage: dict[str, dict[str, int]] = {}
    for outcome in outcomes:
        frequency[outcome.product_id] = frequency.get(outcome.product_id, 0) + 1
        entry = cause_coverage.setdefault(outcome.product_id,
                                          {"with_cause": 0, "without_cause": 0})
        if outcome.confirmed_cause:
            entry["with_cause"] += 1
        else:
            entry["without_cause"] += 1
    created = [o.created_at for o in outcomes if o.created_at is not None]
    window = None
    if created:
        window = {"from": min(created).isoformat(), "to": max(created).isoformat(),
                  "outcomes": len(outcomes)}

    def _identity(product_id: str) -> str:
        product = products.get(product_id)
        return (f"{product.manufacturer} {product.family} {product.model}".strip()
                if product is not None else product_id)

    by_downtime = sorted(
        ({"product_id": pid, "machine_identity": _identity(pid),
          "total_downtime_minutes": minutes}
         for pid, minutes in downtime_by_product.items()),
        key=lambda r: -r["total_downtime_minutes"])
    by_frequency = sorted(
        ({"product_id": pid, "machine_identity": _identity(pid),
          "failure_count": count,
          "reason_code_coverage": cause_coverage.get(pid, {})}
         for pid, count in frequency.items()),
        key=lambda r: -r["failure_count"])
    # Assets with sessions but no outcomes are unreported, not good actors.
    session_products = {s.product_id for s in db.query(DiagnosticSession).all()}
    unreported = sorted(
        {"product_id": pid, "machine_identity": _identity(pid)}
        for pid in session_products if pid not in frequency)
    return {"available": True,
            "by_downtime_minutes": by_downtime,
            "by_failure_frequency": by_frequency,
            "unreported_assets": unreported,
            "window": window,
            "coverage": {"products_with_outcomes": len(frequency),
                         "products_with_downtime": len(downtime_by_product),
                         "reason_code_coverage": cause_coverage,
                         "note": ("reason-code coverage is a finding of its "
                                  "own: outcomes without a confirmed cause "
                                  "cannot be ranked by cause")}}
