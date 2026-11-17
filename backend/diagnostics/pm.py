"""Phase 5 (steal item 7) — PM-interval draft.

When a diagnosis lands, draft the interval or procedure change for
that asset and failure mode, for the planner. It stays a draft until
expert approval.

Crater holds no PM plan of its own, so a draft without a known current
interval does not invent one: it degrades to a recommendation asking
what today's interval is, with the proposed interval left null. A draft
with a known current interval proposes a concrete change (halve the
interval when the failure recurred, per the skill's "same failure
recurring → shorten the interval" shape) — still a draft, never applied.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.db.models import PMDraft


def draft_pm_interval(db: Any, product_id: str, failure_mode: str,
                      current_interval: str | None = None,
                      evidence: str | None = None) -> PMDraft:
    """Create a PM-interval draft for one asset + failure mode.

    With a current interval, propose halving it (a recurring failure
    shortens the interval); without one, ask what today's interval is.
    Either way the status is "draft" — approval is a separate, explicit
    step by a named reviewer."""
    if current_interval:
        proposed = f"half the current interval ({current_interval})"
        change = (f"Shorten the PM interval for {failure_mode!r} to half "
                  f"the current interval ({current_interval}): the failure "
                  f"recurred, so the current interval is not catching it. "
                  f"Evidence: {evidence or 'diagnosis outcome'}.")
    else:
        proposed = None
        change = (f"What is today's PM interval for {failure_mode!r}? No PM "
                  f"plan is recorded in Crater for this asset, so no new "
                  f"interval can be proposed until the current one is known. "
                  f"Evidence: {evidence or 'diagnosis outcome'}.")
    draft = PMDraft(product_id=product_id, failure_mode=failure_mode,
                    current_interval=current_interval,
                    proposed_interval=proposed, proposed_change=change,
                    status="draft")
    db.add(draft)
    db.flush()
    return draft


def review_pm_draft(db: Any, draft_id: str, decision: str,
                    reviewer: str) -> PMDraft:
    """Approve or reject a PM draft. A named reviewer is required; a
    draft is decided exactly once."""
    if decision not in ("approved", "rejected"):
        raise ValueError(f"decision must be 'approved' or 'rejected', got {decision!r}")
    if not reviewer or not reviewer.strip():
        raise ValueError("reviewing a PM draft requires a named reviewer")
    draft = db.get(PMDraft, draft_id)
    if draft is None:
        raise ValueError(f"no PM draft with id {draft_id!r}")
    if draft.status != "draft":
        raise ValueError(f"PM draft {draft_id!r} was already {draft.status}")
    draft.status = decision
    draft.reviewer = reviewer.strip()
    draft.reviewed_at = datetime.utcnow()
    db.commit()
    return draft
