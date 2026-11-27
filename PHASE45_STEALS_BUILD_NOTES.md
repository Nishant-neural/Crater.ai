# Steals build notes — Claude for Manufacturing breakdown-diagnosis skill

Source: `/tmp/claude-plugins-official/plugins/claude-for-manufacturing/skills/breakdown-diagnosis/SKILL.md`
(361 lines), `reference/evidence-sources.md` (146 lines),
`reference/bad-actor-mode.md` (90 lines) — read in full 14 Nov 2026.
The plugin is Apache 2.0, "not actively maintained or monitored", AS IS.
Folded into Phases 4 (items 1–4) and 5 (items 5–9) of the same gated
build; no separate phase, no parallel build.

## What was taken, per item

### Item 1 — Sibling/fleet search (Phase 4; `backend/diagnostics/fleet.py`)

`sibling_search(db, product_id, symptom, revision_id=None)`. This
asset's FailureMode + DiagnosticOutcome rows, then same-model sibling
history, matched on the symptom's own words; counts with the query
behind each; records with machine identity named; a fleet pattern when
the same confirmed cause recurs on ≥2 distinct assets. No history on
this asset ⇒ `fallback_used` states "manual + same-model siblings".
No sensor data in Crater — sources are canonical failure modes,
recorded outcomes, manual tables.
Wired into: `GET /diagnose/{id}/fleet-patterns` and the
early-tournament response (`fleet`, advisory — failures logged, never
break the response).

Measured: 2 tests — sibling failure mode + outcome found with fallback
stated; fleet pattern when the cause recurs on 2 revisions
(asset_count 2, records named). Unknown product ⇒ available=False.

Deviation — sibling identity: `Product` is unique on
(manufacturer, family, model), so same-model siblings are the other
revisions of the same product and the other diagnostic sessions on
that product. Other models of the same family are returned separately
as `cousin_records`, labelled "family cousin, other model", never as
siblings. Outcomes are matched to a symptom through their session's
recorded symptoms (outcomes store no symptom of their own).

### Item 2 — Citation rule (Phase 4; `backend/diagnostics/backtracking.py`)

`cite_hypotheses(hypotheses, signatures, fleet, tournament_rows)`.
Every hypothesis carries citations (manual case+page, twin trace,
prior outcome). No citation ⇒ labelled `"guess — reasoning, not
evidence"`, shown not omitted. Citations live in API responses
(early-tournament `hypotheses`, verify/unavailable `attempt`s), not in
`DiagnosticState` (the `Hypothesis` model has no citation field) —
re-derived per response from the same signatures so they cannot go stale.

Measured: 1 test — a case-1 table cause carries a manual citation and
is "grounded"; a made-up cause has no citations and is labelled a guess.

### Item 3 — Fixed-shape hypothesis presentation (Phase 4; `backtracking.py`)

`present_hypotheses()` / `render_hypothesis_markdown()`: every
hypothesis under the same three headings in order — Evidence / First
check / Expected fix. A missing expected fix shows as an empty heading.
Wired into the early-tournament response as `presentation`.

Measured: 1 test — headings in order for every hypothesis; the table
cause has a fix from the registry, the walk hypothesis has an empty
Expected fix, visible not filled in.

### Item 4 — Safety implication leads (Phase 4; `backend/diagnostics/safety.py`)

`check_safety(evidence)` + `safety_evidence_from_state(state)`. Triggers
(the skill's list): seized/locked rotating part; defeated/bypassed/
failed guard or interlock; stored energy; hydraulic/pneumatic pressure
to bleed; electrical fault; fault cleared by restart now repeating.
Flag only from sourced evidence (`manual_safety_note`,
`canonical_model_safety_fact`, `technician_observation`,
`diagnosis_evidence`) — unsourced text is ignored and reported, never
sets the flag. Uncertain stored energy still flags. Every response
carries the lockout line first plus the "this is not a safety
assessment" disclaimer. Wired into: `POST /diagnose/{id}/respond`
(lockout line prepended to the next step, `safety_check` timeline
entry), `GET /diagnose/{id}/safety`, early-tournament `safety`.

Measured: 6 tests — guard bypass flags; uncertain stored energy flags;
restart-cleared-now-repeating flags; unsourced text never flags;
no trigger ⇒ flag false with reason; safety endpoint flags on a live
session.

### Item 5 — Close-out-note mining + template check (Phase 5; `backend/diagnostics/closeout.py`)

`mine_closeout_notes(db, symptom, product_id)` + `propose_from_mining()`.
Groups identical note text in code; counts distinct makes/models per
group; ≥2 makes/models ⇒ template, labelled "same text on N assets —
not specific to this machine", never cited as this machine's evidence.
Same-model recurrence ⇒ fleet pattern via `sibling_search`, citable
with records named. Weight by symptom similarity, then recency. Signal
words again / still / same as last time / temporary fix / needs proper
repair ⇒ unresolved. Coverage honest: N of M usable (≥10 non-space
characters), terse notes listed, never over-read. Feeds the learning
gate: a non-template group with a confirmed cause proposes a pending
learning-log entry; expert approval still required.
Wired into: `GET /learning/closeout`.

Measured: 2 tests — template across 2 models labelled (coverage 2 of
3 usable, 1 terse); same-model recurrence is a fleet pattern
(asset_count 2) with unresolved signals "again" + "still".

### Item 6 — Safety-first RepairAction (Phase 5; `backend/simulation/repair_actions.py`)

`render_repair_action_safety_first(action)`: fixed order Safety,
Preconditions, Steps, Expected trace. The full RepairAction
(preconditions + safety_requirements, Phase 5d) presents the safety
line before the repair steps; a repair with a safety implication leads
with it. The safety section is always present (lockout line even with
no safety_requirements). Wired into: `POST /diagnose/{id}/verify-repair`
(`repair_action_safety_first` when the repair is a reviewed action).

Measured: 1 schema test (all 4 case-1 actions carry preconditions +
safety_requirements; render order Safety first) + the Phase 5 gate
test (verify-repair response carries the safety-first action with the
lockout line first).

### Item 7 — PM-interval draft (Phase 5; `backend/diagnostics/pm.py`)

`draft_pm_interval()` + `review_pm_draft()`. When a diagnosis lands,
draft the interval/procedure change for that asset and failure mode.
Stays a draft until expert approval (named reviewer, decided once).
Without a current PM plan in Crater, degrades to a recommendation
asking what today's interval is (proposed_interval null). With a
current interval, proposes halving it (recurring failure ⇒ shorten).
Wired into: `POST /diagnose/{id}/outcome` (a draft is created per
recorded outcome) and `POST /learning/pm-drafts/{id}/review`.

Measured: 1 test — draft with interval proposes halving; draft without
interval asks today's interval; approval sticks, second review rejected.

### Item 8 — Downtime record (Phase 5; `backend/diagnostics/downtime.py`)

`start_downtime()` (opened at session start), `close_downtime()`
(closed at outcome recording), `downtime_report()`. Structured record
per diagnosis/repair: asset, symptom, cause, confirmed/unconfirmed,
duration — null while ongoing, never zero (minimum 1 on close) —
safety_flagged. Twin VALIDATED vs field SUCCESSFUL stay separate
statuses; the record carries neither. Wired into: `GET
/learning/downtime/{session_id}`; the outcome response carries the
closed duration.

Measured: in the downtime/bad-actor test — duration null while ongoing,
≥1 after close; in the Phase 5 gate test — outcome response carries
`downtime.duration_minutes >= 1`.

### Item 9 — Batch bad-actor ranking (Phase 5; `backend/diagnostics/downtime.py`)

`bad_actor_ranking(db)`: rank by total downtime minutes AND by failure
frequency, computed ONLY from recorded outcomes. No outcome data ⇒
`{"available": false, "reason": "no outcome data"}` honestly, never an
invented ranking. Assets with sessions but no outcomes are listed
separately as unreported (not good actors). Window and per-asset
coverage stated; reason-code coverage is a finding of its own.
Wired into: `GET /learning/bad-actors`.

Measured: 1 test — no data ⇒ "no outcome data"; with 2 outcomes +
downtime on 2 assets ⇒ both rankings have 2 entries, window states
2 outcomes; a third asset with sessions but no outcomes is listed
unreported.

## What was deliberately NOT taken

- Their connectors (Fusion / Infor / NetSuite / Microsoft 365) — Crater
  has no such integrations; out of scope by the task.
- Quoting, CAPA writeups, morning production reports, onboarding —
  not Crater's product surface.
- Sensor windows — Crater has no live signals; the skill's
  sensor-window shapes were not adapted (fleet search uses recorded
  outcomes and manual tables instead).
- Design-space-explore — a design tool, not a diagnostic loop.
- Any Claude plugin build — a later channel after production
  hardening, not part of this build.
- No new runtime LLM calls beyond the v12 budget; no OCR.

## Totals

- Phase 4 steals tests: 12 (`tests/test_phase4_steals.py`).
- Phase 5 steals tests: 4 of the 10 in `tests/test_phase5_learning.py`
  (close-out ×2, PM draft ×1, downtime/bad-actor ×1) + the gate test
  covering safety-first RepairAction.
- No-degradation holds after both phases: off-table gate PASS (9/9,
  4/4), 3C oracle 20/35 / O1 pass / O3 57/58, Demo 7 transitions.
