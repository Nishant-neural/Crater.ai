# Phases 4–6 follow-up build notes — Wingnut gate wording, upstream walk
# wired into the live loop, Tier 2 Option C approval

Built 14 Nov 2026. Tree: `~/workspace/crater-phase1/v12`. Package:
`~/workspace/crater-phase1/project-review-phase6-followup-patched.zip`
(NEW file; the Phase 6 package is untouched). No new runtime LLM calls
(diagnosis 2 calls/turn, tournament 0 — unchanged). No engine rewrite,
no solver, no OCR, no auth/multi-tenancy, no Phase 7/8, no Wingnut
archetype, no plugin, no production hardening.

## Scope (Nishant's three decisions, 14 Nov 2026)

1. **Wingnut gate wording (docs only):** amend the 3B gate wording as
   proposed in `files/phase3-wingnut-gate-decision-memo.md`. Do NOT build
   a fastener archetype.
2. **Wire the upstream walk into the live diagnosis loop (build):** the
   verifier proved `upstream_walk`, `apply_test_answer`,
   `generate_pair_hypotheses` were called only by
   `scripts/run_phase4_offtable.py` and the unit tests; the live loop's
   off-table behaviour was `_attempt_for_state` alone. Also enforce the
   half-enforced cap: `MAX_LIVE_HYPOTHESES = 5` was defined but never
   referenced.
3. **Tier 2 Option C approval (docs + verify):** record Nishant's
   approval of Option C. Do NOT flip the default — it stays ON per the
   registry.

## Item 1 — Wingnut gate wording (docs only, no code)

- `files/plan.md` — the Phase 3 result 3B bullet now records the gate
  as **MET AS AMENDED** (Nishant, 14 Nov 2026), quoting the amended
  wording verbatim:
  > 3B gate: all 4 case-1 causes VALIDATED through the RepairAction +
  > ExpectedTrace path on the proof twin — Feed Tensioner from the A4
  > archetype tables, Wingnut from the translated-seed rule-11 overrides
  > (p10), both with the required motor → feeder → wire trace. Fastener
  > behaviour stays rule-11 until a second machine family's top case
  > requires a generic clamp archetype.
  The old "not met as written" sentence is superseded in that bullet;
  nothing else in plan.md was rewritten.
- `files/phase3-design-v3-archetype-oracle.md` — a fourth Amendments
  section appended after the third (14 Nov 2026), recording the approval,
  the amended wording verbatim, the reasoning (rule-11 was already the
  design's assignment for this quirk; a fastener archetype would be a
  12th archetype for one part on one machine), the revisit trigger
  (a second family's top case needing "loose fastener → downstream
  free-spin / slip"), and that nothing verified changes.
- No code, no recompile, no oracle re-run for this item (per the memo's
  no-degradation case).

## Item 2 — Upstream walk wired into the live loop (build)

### `backend/diagnostics/backtracking.py` (one addition)

- `split_live_and_reserves(hypotheses, max_live=MAX_LIVE_HYPOTHESES)` —
  the one place the live cap is now enforced: top 5 live, the rest
  returned as ranked reserves in walk order. `upstream_walk` itself is
  unchanged (pool stays 6), so the harness keeps measuring the full chain.

### `backend/api/routes/diagnostics.py` (CRLF preserved)

- New endpoint `POST /diagnose/{session_id}/upstream-walk`
  (`UpstreamWalkRequest{force}`): the off-table path on demand.
  Idempotent while a walk is live (returns the live walk);
  `force=true` re-walks. Refuses with available=False + reason when the
  table is not exhausted yet.
- `POST /diagnose/{id}/respond` now:
  1. applies a live walk's answer deterministically BEFORE the LLM turn
     (`_apply_walk_answer`), so the model reasons over the backtracked
     set instead of resurrecting ruled-out causes; then
  2. after the early tournament, auto-runs the walk when the turn
     exhausted the table causes (or none matched) — but only when a
     registry case matches or the session has no hypotheses left, so an
     unmatched symptom with live LLM hypotheses stays the LLM's to
     reason over until the technician explicitly asks for the walk.
- `_run_upstream_walk` — off-table detection (`_is_off_table`: no live
  hypotheses, or no live hypothesis matches a registry case cause, or
  every live hypothesis is already eliminated); symptom component from
  the matched case's first twin-expressible vocabulary row's fault
  component (case 1: Wire Feed Mechanism), downstream exclusion from
  the case's required_chain (same exclusion as the harness, so the
  arc → welder feedback edge cannot name the arc as a producer);
  edges from the twin's own relations first, else the reviewed edge
  list (never invented); bindings from the approved twin's metadata
  (`archetype_bindings`, evidence-based) first, then the canonical
  snapshot's `library_type`, then a unique exact-alias match only
  (a component with no binding still walks with the archetype-shaped
  generic fault — never guessed into an archetype). Bindings are
  translated to the backtracking A-codes (A1 source … A11 interface);
  the archetype objects are passed keyed by A-code, which is the
  convention `backtracking.py`'s preferred-mode / generic-test tables
  use. Walk hypotheses replace the live set; pre-existing live
  hypotheses are demoted to reserves, never dropped. The walk's
  discriminating question (walk-generated, archetype-shaped, labelled
  as not a manual-cited test) becomes the next step. Every run —
  including a walk that cannot run — is recorded on the timeline, and
  a walk that cannot run returns unavailable-with-attempt, never a bare
  unavailable.
- `_apply_walk_answer` — yes/no/not-sure/sometimes advance the walk
  deterministically via `apply_test_answer` (an outcome value such as
  "stops" is left to the LLM turn, because walk causes carry no
  vocabulary signatures to match it against — `apply_test_answer`
  keeps such a hypothesis instead of ruling it out). Ruled-out live
  hypotheses move to `eliminated_hypotheses`; ranked reserves are
  promoted to refill the live cap. Once the singles are exhausted
  (≤ 1 single left, no pairs yet), pairs are generated from the top of
  the ranking (remaining singles + reserves + all walk-eliminated
  hypotheses so far) via `generate_pair_hypotheses`; pair hypotheses
  carry the walk marker so the walk stays live. Corrected loop order
  is preserved: the early tournament still runs after every turn and
  re-ranks; the walk never eliminates what the tournament validated
  (tournament REJECTED only demotes, per the existing rule).

### Bugs found and fixed during this build (do not reintroduce)

1. **Stale reserves duplicated a hypothesis.** The first version
   re-read reserves from the original `upstream_walk` timeline entry
   on every answer, so a promoted reserve was promoted again each
   turn and the live set never shrank to the pair stage. Fixed by
   `_latest_walk_entry` (most recent walk/answer entry) plus a
   never-promote-a-cause-already-live guard.
2. **Pair hypotheses lost the walk.** Pairs from
   `generate_pair_hypotheses` carry no walk marker, so after the
   singles were exhausted the walk looked dead and a fresh walk
   overwrote the pairs. Fixed: pairs get the walk-rationale prefix
   when appended.
3. **Loose-match dedup ate a real elimination.** The eliminated
   dedup used `_texts_match`, which matches "Input Power Cord:
   not_connected" against the already-eliminated "Welder:
   not_connected" (shared "not / connected" tokens), so ruling out
   the cord was silently dropped. Fixed: exact (case-insensitive)
   cause equality for dedup. The loose rule itself is unchanged
   elsewhere.

### Tests — `tests/test_phase456_followup_walk.py` (5 new, all pass)

- `test_split_live_and_reserves_enforces_cap` — 6 walk hypotheses →
  5 live + 1 reserve, order preserved, nothing dropped.
- `test_upstream_walk_endpoint_generates_live_plus_reserves_and_answers_advance_to_pairs`
  — HTTP-level: endpoint returns 5 live + 1 reserve covering Wire Feed
  Motor / Trigger / Input Power Cord, persists the walk + reserve on
  the timeline, is idempotent while live; then 6 × POST /respond "no"
  rules the singles out one by one (reserve promoted each time) and
  ends with "(two faults)" pair hypotheses live, live ≤ 5, session
  still active.
- `test_respond_auto_walks_when_turn_exhausts_table_causes` — a stubbed
  LLM turn that rules the table causes out is followed, in the same
  POST /respond, by 5 live walk hypotheses on the session.
- `test_upstream_walk_refuses_when_table_not_exhausted` — available=False
  with reason while a table cause is live; `force=true` runs it anyway.
- `test_tier2_default_stays_on_after_option_c_approval` — see Item 3.
- The LLM turn is stubbed at `gateway.complete_result` (no provider is
  configured in this environment): one stub returns a valid turn, the
  other unparseable output (degrades to keeping the walk-advanced
  hypotheses — the documented no-LLM behaviour). The tests assert the
  walk wiring, not the model. The respond-loop fixture starts at
  confidence 0.5 so the v12 forced-escalation guard (confidence < 0.35
  after 5 turns) does not fire — that guard is pre-existing v12
  behaviour, unchanged here.

## Item 3 — Tier 2 Option C approval (docs + verify, no default change)

- `files/tier2-flag-decision-v1.md` — new "Decision — Option C APPROVED
  by Nishant, 14 Nov 2026" section appended after "What remains
  Nishant's call" (A/B text kept intact): the flag + deferred-state
  machinery is ratified; the default stays ON per the registry; the
  eventual flip is per-product in the registry JSON when coverage data
  justifies it.
- `files/plan.md` — the Tier 2 item in the Phases 4–6 result section
  (verifier catch 3) is resolved as approved, with the default-stays-ON
  note.
- Verified, no code change: `repair_cases_omnipro.json`
  `"tier2_default": true`, `repair_cases.py` `tier2_default: bool = True`;
  Tier 2 tests re-run (see Measured results): the flag-off / flag-on /
  lazy-pages / lazy-summary tests in `test_phase4_backtracking.py` and
  the Mode B Tier 2 tests in `test_phase6_mode_b.py` all pass.

## Measured results

- Full suite: **398 passed, 2 failed, 0 collection errors** (398 = 393
  baseline + 5 new). The 2 failures are the same pre-existing
  `test_phase8a_integration.py` pair as the baseline
  (`test_pdf_chunk_modalities_reach_universal_persistence`,
  `test_schematic_projection_creates_entities_ports_and_relations`).
- 3C oracle re-run (`scripts/run_phase3c_oracle.py`): headline **20/35**,
  O1 pass, O3 **57/58**, Demo Motor Drive **7 transitions**,
  crater-10 **0/35**, O1 fail — identical to the filed 3C report.
- Off-table re-run (`scripts/run_phase4_offtable.py`): registry **9/9**,
  unseen **4/4**, intermittent True, pairs True, gate **PASS**.
- Tier 2 tests (`tests/test_phase4_backtracking.py` +
  `tests/test_phase6_mode_b.py`): all pass (included in the full-suite
  count above; the Tier 2 flag-off/on/lazy tests and the Mode B
  deferred/graduation tests).
- `engine.py`, `compiler.py`, `archetypes.py`, `oracle.py`,
  `tournament.py`, `verification.py` untouched (no diff vs the Phase 6
  package tree beyond the files listed below).

## Files changed (in `~/workspace/crater-phase1/v12`)

- `backend/diagnostics/backtracking.py` — `split_live_and_reserves`
  only (no change to `upstream_walk`, `apply_test_answer`,
  `generate_pair_hypotheses`, or any table).
- `backend/api/routes/diagnostics.py` — walk helpers, upstream-walk
  endpoint, respond wiring (CRLF preserved).
- `tests/test_phase456_followup_walk.py` — new, 5 tests.
- `PHASE456_FOLLOWUP_BUILD_NOTES.md` — this file.
- Goal files (docs only): `files/plan.md` (3B gate bullet + Tier 2
  resolution), `files/phase3-design-v3-archetype-oracle.md` (fourth
  Amendments section), `files/tier2-flag-decision-v1.md` (Option C
  decision section).

## Deviations (with reasons)

1. **No new `DiagnosticState` field for walk reserves.** Reserves live
   on the timeline (`upstream_walk` / `upstream_walk_answer` entries)
   and in API responses, not in `DiagnosticState` — the same choice
   Phase 4 made for citations (the `Hypothesis` model has no
   reserve/citation field, and timeline entries are already the
   session's audit trail). A future UI that needs reserves reads the
   latest walk timeline entry.
2. **Pairs pool includes walk-eliminated hypotheses, not only live
   ones.** A pair can explain what each single alone could not, so
   once singles are exhausted the pool is remaining singles +
   reserves + all walk-eliminated hypotheses so far (walk-marked
   only — pre-walk table causes are excluded). This is the scenario
   §4 "singles first, then pairs from the top of the ranking" rule.
3. **Auto-walk in respond() is gated on a matched registry case (or
   an empty hypothesis list).** An unmatched symptom with live LLM
   hypotheses stays the LLM's to reason over; the explicit endpoint
   (or `force=true`) is the way to walk there. This keeps the
   auto-trigger from firing an unavailable-with-attempt timeline entry
   on every turn of a non-registry product.

## Not run (live checks — need the user)

End-to-end ingestion with the user's LLM + Qdrant; any live app check
(fresh OMNIPRO upload/job, session restore, tournament, verify verdicts,
Machine Model view); a real repair outcome through the walk (field
YES/NO on a walk-generated hypothesis); a real Mode B cold start
end-to-end; first LLM draft + 15-minute review target. See the final
report's live-check list.
