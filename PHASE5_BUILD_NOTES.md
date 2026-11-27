# Phase 5 build notes — verify/repair/learn hardening (+ steals items 5–9)

Built 14 Nov 2026. Tree: `~/workspace/crater-phase1/v12`. No new runtime
LLM calls (diagnosis 2 calls/turn, tournament 0 — unchanged). No engine
rewrite, no solver, no OCR, no production hardening.

## Scope (plan.md §4 Phase 5)

- (a) Outcome recording: field outcome SUCCESSFUL/FAILED recorded by
  the technician, kept as a SEPARATE status from the twin verdict
  VALIDATED/REJECTED — never overwrites it; twin VALIDATED followed
  by field FAILED flags the implicated archetype row for review.
- (b) Learning gate: an unseen fault solved in the field becomes a
  registry case only after expert approval (append-only learning log;
  reviewer identity recorded); nothing auto-promotes, including Layer 3
  field promotion — promotion counts field outcomes only, never
  simulator verdicts alone.
- (c) Versioning: twin pins library version; recompilation against an
  older version is explicit; rollback is a real operation.
- (d) Full RepairAction schema: preconditions + safety_requirements
  added to the minimal 3B form, with procedure refs + page evidence.
- (e) `validation_history` per archetype row is WRITTEN from recorded
  field outcomes.
- Steals from Anthropic's Claude for Manufacturing
  `breakdown-diagnosis` skill (items 5–9): close-out-note mining +
  template check, safety-first RepairAction, PM-interval draft,
  downtime record, batch bad-actor ranking. See
  `PHASE45_STEALS_BUILD_NOTES.md`.

## What was built

### New tables (`backend/db/models.py`)

- `LearningLogEntry` — append-only learning log. The proposal
  (`proposed_case`) is immutable; only the review decision fields
  (status, reviewer, review_reason, reviewed_at) are ever set, exactly
  once. Statuses: pending / approved / rejected.
- `FieldValidationHistory` — per-(archetype, row) validation_history,
  unique on (archetype_id, row_id); `row_id` "" = archetype-level.
  Counts field outcomes only.
- `ArchetypeRowFlag` — flagged row for review (status flagged /
  cleared, cleared only by a named reviewer).
- `DowntimeRecord` — structured downtime per diagnosis; `duration_minutes`
  null while ongoing, never zero; session_id unique.
- `PMDraft` — PM-interval draft (status draft / approved / rejected).
- `DigitalTwin.library_version` (nullable) — the pin; the compiler
  already records `archetype_library_version` in the definition
  metadata, the column makes the pin queryable. `TwinEventType`
  gained `library_version` for rollback audit events.
- `backend/db/migrations.py`: `digital_twins.library_version` added
  to `_REQUIRED_COLUMNS` (same pattern as the Phase 8A columns) so
  existing databases gain the column.

### `backend/diagnostics/learning.py` (new)

- `FIELD_OUTCOME_MAP`: fixed → SUCCESSFUL, not_fixed → FAILED;
  inconclusive maps to nothing (an inconclusive outcome is not field
  evidence either way).
- `last_twin_verdict(state)` — reads the last verify_repair verdict
  from the session timeline (the record of what the twin said; never
  re-runs verify). Normalizes lowercase "validated" to "VALIDATED".
- `implicated_archetype_rows(state, confirmed_cause, twin_definition)`
  — registry cause → fault_injection → behaviour owner archetype +
  fault-template row ids (via `failure_to_behaviour`). Empty when no
  registry cause / twin / resolvable owner — the outcome is still
  recorded, but no row is implicated or flagged on a guess.
- `apply_field_outcome(db, outcome, session_state, twin_definition,
  twin_verdict=None)` — the end-to-end flow: maps the outcome, writes
  `FieldValidationHistory` for the implicated rows (reusing the 3D
  `record_field_outcome` counting rule — field outcomes only), flags
  each implicated row when twin VALIDATED + field FAILED (with the
  `RepairRecord` learning signal), proposes a learning-log entry when
  the confirmed cause has no registry match (pending, never
  auto-promoted). Returns everything the endpoint reports.
- `flag_row_for_review()` (deduped on archetype+row+source outcome),
  `clear_row_flag()` (named reviewer required; already-cleared → error).
- `propose_learning_entry()` (deduped on product+symptom+cause while
  pending), `review_learning_entry()` — the gate, enforced in code:
  decision approved/rejected, named reviewer required, decided exactly
  once, proposal never edited.
- `learned_cases(db)` — approved entries only. `load_registry_with_learned(db)`
  — file registry + approved learned cases as new registry cases
  (`learned-<id prefix>`, rank after the file cases, `table_page` 0,
  `tier1_pages` [] — field evidence, not manual pages, built with
  `model_construct` so the file-registry Tier-1 validator does not
  apply; a proposal with neither fix nor test is skipped, never
  promoted).

### `backend/simulation/library_versions.py` (new; 5c)

- `pin_library_version(twin, version=None)` — stamp the version a twin
  was compiled against; unknown version rejected.
- `recompile_with_version(twin, version, explicit=False)` — recompiling
  against anything other than the current library requires
  `explicit=True`; records the version decision, never silently runs a
  compile.
- `rollback_library_version(db, twin, target_version, actor, reason)` —
  a real operation: the pin moves and a `library_version` twin event
  records before/after, actor, reason. Named actor and reason required.

Deviation — only one known version: `KNOWN_LIBRARY_VERSIONS` is
`("3c-1.0.0",)` — the only version this tree has shipped. A rollback
target must be a version the tree has shipped; the registry grows as
versions ship. Rollback is tested with a monkeypatched known-versions
list (see tests).

### Full RepairAction (5d; `backend/simulation/repair_actions.py` + JSON)

- `RepairAction` gained `preconditions: list[str]` and
  `safety_requirements: list[str]`. All 4 case-1 actions in
  `repair_actions_omnipro.json` now carry both: preconditions cite the
  procedure-ref pages (p42 remedies, p17 step 27, p12 feed roller);
  safety_requirements are reviewer-authored (the lockout line first,
  plus one action-specific line); page evidence lives in
  `procedure_refs`, unchanged.
- `render_repair_action_safety_first(action)` — fixed order: Safety,
  Preconditions, Steps, Expected trace. The safety section is always
  present (lockout line even when an action has no
  safety_requirements), so its absence is visible rather than silent.

### Wiring (`backend/api/routes/diagnostics.py`, `backend/diagnostics/agent.py`)

- `POST /diagnose/{id}/outcome` — after the outcome is committed, the
  Phase 5 flow runs: twin definition loaded (active twin for the
  revision), `apply_field_outcome`, a PM draft for the failure mode,
  downtime close. The response gains a `"phase5"` block (field_outcome,
  twin_verdict, learning_signal, history_written, flags_created,
  learning_entry, pm_draft, downtime). Best-effort: a Phase 5 failure
  is reported in the response as `"phase5": {"error": ...}`, never
  silent, and never rolls back the recorded outcome.
- `POST /diagnose/{id}/verify-repair` — when the repair is a reviewed
  RepairAction, the response gains `"repair_action_safety_first"` (the
  safety-first rendering). Note: `repair_action_id` is the action's
  `id` ("repair:omni-01-a"), not its `fault_injection_ref` — the
  lookup is by id (found during the build; `find_repair_action`
  matches on the ref).
- `start_session` (`agent.py`) — opens the downtime record for the
  diagnosis (best-effort; a downtime failure never breaks a session
  start).

### Review view + gated endpoints (`backend/api/routes/learning.py`, new; registered in `backend/api/main.py`)

- `GET /learning/review` — the Phase 5 review view: flagged rows,
  pending learning entries, PM drafts, field-validation history,
  approved learned cases, counts.
- `POST /learning/entries/{id}/review` — approve/reject (reviewer
  required, decided once; 409 otherwise).
- `POST /learning/rows/{flag_id}/clear`, `POST /learning/pm-drafts/{id}/review`.
- `GET /learning/closeout` (steal 5), `GET /learning/bad-actors`
  (steal 9), `GET /learning/downtime/{session_id}` (steal 8).
- `GET /learning/library-versions`,
  `POST /learning/twins/{id}/pin-library-version`,
  `POST /learning/twins/{id}/recompile` (explicit flag),
  `POST /learning/twins/{id}/rollback-library-version`.

### Steals 5–9 (detail in `PHASE45_STEALS_BUILD_NOTES.md`)

- `backend/diagnostics/closeout.py` — `mine_closeout_notes()`,
  `propose_from_mining()`. Groups identical note text in code, counts
  distinct makes/models per group; ≥2 makes/models ⇒ template,
  labelled "same text on N assets — not specific to this machine",
  never cited as this machine's evidence. Same-model recurrence ⇒
  fleet pattern via `sibling_search`, citable with records named.
  Weight by symptom similarity, then recency. Signal words again /
  still / same as last time / temporary fix / needs proper repair ⇒
  unresolved. Coverage reported honestly (N of M usable; terse notes
  listed, never over-read). Usable = ≥10 non-space characters.
- `backend/diagnostics/pm.py` — `draft_pm_interval()` (with a current
  interval: propose halving it; without one: proposed_interval null
  and the draft asks what today's interval is), `review_pm_draft()`.
- `backend/diagnostics/downtime.py` — `start_downtime()`,
  `close_downtime()` (duration = elapsed minutes, minimum 1 on close;
  null while ongoing), `downtime_report()`, `bad_actor_ranking()`
  (by total downtime minutes AND by failure frequency, from recorded
  outcomes only; no data ⇒ "no outcome data"; assets with sessions
  but no outcomes listed separately as unreported; window + per-asset
  coverage stated).

## Measured results — Phase 5 gate: PASS

- `tests/test_phase5_learning.py`: **10 passed** (outcome flow
  VALIDATED→FAILED with flag+history visible in the review view;
  SUCCESSFUL without flag; inconclusive writes nothing; unseen cause
  proposes pending entry, gate needs a reviewer, approval enters the
  merged registry, second review 409; versioning pin/recompile/
  rollback; full RepairAction schema; close-out template + fleet
  pattern + unresolved; PM draft with/without interval; downtime
  null-while-ongoing + bad-actor ranking incl. "no outcome data").
- Off-table script to `/tmp/phase5`: **registry 9/9, unseen 4/4,
  intermittent True, pairs True, gate PASS** (unchanged).
- 3C oracle re-run to `/tmp/phase456-phase5`: proof twin **20/35
  headline, O1 pass, O3 57/58**; crater-10 **0/35**; Demo Motor Drive
  **7 transitions**. Reproduces the 3C baseline exactly.
- Full suite: **387 passed, 2 failed, 0 collection errors** (12 min 52 s).
  The 2 failures are the same pre-existing phase8a failures as the
  3C baseline (`test_pdf_chunk_modalities_reach_universal_persistence`,
  `test_schematic_projection_creates_entities_ports_and_relations`).
  387 = 377 after Phase 4 + 10 new Phase 5 tests.

## Deviations (with reasons)

1. **Only one known library version.** See library_versions section
   above — rollback targets must be versions this tree has shipped.
2. **Learned cases carry `tier1_pages=[]` / `table_page` 0.** Field
   evidence, not manual pages; built with `model_construct` so the
   file-registry Tier-1 validator does not apply. A proposal with
   neither fix nor test is skipped at merge time.
3. **Phase 5 endpoint flow is best-effort with a visible error.**
   The outcome is the recorded fact; a Phase 5 failure is reported in
   the response (`phase5.error`) and logged, never silent, and never
   rolls back the outcome. The gate that matters — nothing
   auto-promotes — is enforced in code in `review_learning_entry`,
   not in the endpoint wrapper.
4. **`reviewed_at` timestamps use naive `datetime.utcnow()`.** The
   codebase convention (all models use `utcnow()` defaults); mixing
   aware timestamps broke `close_downtime` arithmetic during the
   build (naive `started_at` vs aware `now`), fixed by using naive
   throughout the new modules.

## Bugs found and fixed during the build (do not reintroduce)

1. **Twin verdict casing.** Twin verdicts are lowercase
   ("validated"/"rejected") in the code; `RepairRecord`/`FieldOutcome`
   use uppercase (VALIDATED/REJECTED). `last_twin_verdict` now
   normalizes to upper case; without this the VALIDATED-then-FAILED
   learning signal never fired.
2. **`repair_action_id` is the action id, not the fault_injection_ref.**
   `find_repair_action` matches on the ref, so the verify-repair
   wiring looks the action up by id instead.
3. **Naive/aware datetime mix** in `close_downtime` (aware `now` minus
   naive `started_at`) — fixed by using naive `utcnow()` everywhere
   in the new modules.
4. **Test bug (not production):** the recompile-explicit test used an
   unknown version, so it raised "unknown version" before reaching the
   explicit check — the known-versions monkeypatch now comes first.

## Not run (live checks — need the user)

End-to-end ingestion with the user's LLM + Qdrant; any live app check
(fresh OMNIPRO upload/job, session restore, tournament, verify
verdicts, Machine Model view, first real repair outcome through the
review view). See the final report's live-check list.
