# VERIFY-BEFORE-CONCLUSION BUILD NOTES (fix-3)

Date: 21 Nov 2026. Work tree `~/workspace/crater-phase1/v12`, venv
`~/workspace/crater-phase1/.venv`. Built on the fix-2 tree (433 tests
passing, 0 failed). No LLM calls were used for the build itself; tests stub
the gateway the way earlier builds did.

Package: `~/workspace/crater-phase1/project-review-phase6-readiness-ingestion-gemini-fix3.zip`
(new; no delivered zip was overwritten). 276 files. (The zip was rebuilt
once to drop empty `__pycache__/` dir entries; the final md5 is recorded
in the delivery report, not here — embedding it here would change the zip
on every rebuild.)
No changes to `~/workspace/crater-benchmarks/`, any `.db` file, `~/workspace/user/files/`,
the benchmark spec, or any delivered zip.

This build carries THREE requirements in one zip, all from Nishant via the
coordinator: (1) the verify-before-conclusion gate, (2) no repeated
discriminating test for an already-checked cause, (3) removal of escalation
from the diagnosis loop entirely. Requirement (3) supersedes the first
brief's "do not weaken the forced-escalation safeguards" line for the
escalation parts only — the escalation behaviour is removed, the
non-escalation safeguards (turn bound) are kept in their new form.

## 1. The status-gate bug (re-verified in code before changing anything)

Nishant's live session supplied the confirming evidence: he HAD compiled
and verified the twin, and the tournament ran on turns #2–#7 — so the
no-twin theory is ruled out, and the missing tournament entry after the
final turn #8 is confirmed as the cause. The bug, re-verified at the
cited locations:

1. `run_turn` (`backend/diagnostics/agent.py`, then lines 628-634) set the
   session status to concluded/escalated the moment the LLM picked a
   conclusion/escalate step:
   `if state.current_step.type == NextStepType.conclusion: session.status =
   DiagnosticStatus.concluded` (likewise escalate).
2. `respond()` (`backend/api/routes/diagnostics.py`, then lines 748-749)
   ran `_apply_early_tournament` only `if session.status.value ==
   "active"`. So on the very turn the model concluded, the twin
   tournament never ran and the conclusion went out unverified.
3. The tournament also ran AFTER the LLM turn, so the model never saw the
   current turn's verdicts before concluding (verdicts reached it only via
   the timeline on the next turn, and there is no next turn once it
   concludes).
4. With no approved twin, `_apply_early_tournament` silently no-op'd
   (`_load_twin_for_session` returned None → `return None`, no timeline
   entry, no user-visible warning). The start endpoint does not require a
   twin, so a diagnosis could run with verification silently absent.

## 2. The fix — verify-before-conclusion gate

A conclusion step is only accepted when the tournament has run on the
current turn's hypotheses and the cause the conclusion is about carries a
VALIDATED verdict. Otherwise the conclusion is DEMOTED to a question step
(the session stays active). Escalation is NOT the alternative — it was
removed from the loop by requirement (3) below, so demotion-to-question
is the mechanism (with the unavailable-with-attempt as the turn-bound
ending where that fits).

New shared module `backend/diagnostics/twin_verification.py` — the single
place that loads the approved twin and runs the early tournament, used by
both the agent and the routes, so the verdict logic lives in exactly one
place. No DB writes and no timeline side effects there; callers own
persistence. It does NOT change the tournament's verdict logic
(VALIDATED / REJECTED / unavailable) or the oracle:

- `load_twin_for_session(db, session)` — (definition, twin_state,
  snapshot_model) or None. (The routes' `_load_twin_for_session` is now
  a thin wrapper over it.)
- `run_tournament_for_state(db, session, state, repair_name)` — runs
  `run_tournament` + `rerank_with_tournament` on the state's hypotheses,
  re-ranking in place; returns `{"available": True, "results", "counts",
  "reranked"}` or `{"available": False, "reason": ...}` (no hypotheses /
  no approved twin / tournament failure) instead of silently doing
  nothing. Never raises.
- `conclusion_cause(state, content)` — the hypothesis a conclusion is
  about: the first hypothesis (ranked order) whose cause the conclusion
  content mentions, else the top hypothesis (a repair-only conclusion
  such as "Adjust the Feed Tensioner" is read as being about the leading
  cause).
- `verdict_for_cause(results, cause)` — tournament status for one cause.
- `render_verification_block(summary, asked_causes, asked_tests)` — the
  prompt block the LLM reasons over.

`run_turn` (`backend/diagnostics/agent.py`) new order per turn:

1. Retrieval + evidence (unchanged).
2. **Pre-turn tournament** (`_verification_block_for_turn`): runs the
   tournament on the ranking carried into the turn, re-ranks the
   hypotheses the LLM is about to see, appends a pre-turn
   `early_tournament` timeline entry, and renders the verification block
   into the prompt (VALIDATED / REJECTED / unavailable rows, the
   already-asked causes/tests, and the rule that only a VALIDATED cause
   may be concluded). The model now reasons over verified rankings before
   it picks its next step. With no twin the block says so plainly.
3. LLM turn (`_run_llm_turn`, now taking the verification block; the
   prompt in `backend/diagnostics/prompts.py` gained a
   `{verification_block}` section and rule 7).
4. **Conclusion gate** (`_apply_conclusion_gate`): if the step is a
   conclusion, runs the tournament on THIS turn's hypotheses. VALIDATED
   → the conclusion stands, with a `conclusion_verified` timeline entry.
   Otherwise → demoted via `_demoted_conclusion_step` (question step,
   content labelled `UNVERIFIED — the conclusion "…" was held because …`,
   asking about the next untested cause), with a `conclusion_held`
   timeline entry carrying the verdict/reason. Non-conclusion steps pass
   through untouched. The gate runs after the LLM turn and before the
   turn-bound guard, so a validated conclusion at the turn limit still
   concludes.
5. **Forced safeguards** (`_apply_forced_safeguards`, repurposed — see §4):
   escalate-step mapping + turn-bound unavailable ending.
6. **No-repeat gate** (`_apply_no_repeat_gate`, see §3), skipped when the
   turn bound fired (the ending is not a question to vary).
7. Timeline entries (turn-bound `diagnosis_unavailable` entry when the
   bound fired, then the turn entry), persist. Status: concluded only for
   a (validated) conclusion step; otherwise active — there is NO escalated
   transition any more.

No-twin surfacing (requirement: never silently skip):

- Gate, no twin: the demoted step content carries `UNVERIFIED` and the
  reason ("no approved twin for this revision yet; compile and approve
  one first" / "this session has no revision, so there is no twin to
  verify against (no approved twin)"), and the `conclusion_held`
  timeline entry's step reads `Conclusion held as unverified: <reason>`.
- `_apply_early_tournament` (`backend/api/routes/diagnostics.py`): when
  the tournament cannot run for lack of a twin, it now appends a
  `twin_unavailable` timeline entry
  (`Twin verification unavailable: <reason>`) and commits it, instead of
  returning None silently. (The `verify_hypotheses` endpoint already
  surfaced the no-twin case with a timeline entry; it is unchanged.)
- The gate applies wherever `run_turn` can produce a conclusion,
  including the start-session first turn: `start_session` calls
  `run_turn`, so the first turn is gated with no extra code. Covered by
  `test_start_session_first_turn_conclusion_is_gated`.

Cost note: a turn now runs the tournament up to twice (pre-turn on the
incoming ranking, post-turn in `respond()` on the new ranking; the gate
runs it on a concluding turn instead of the post-turn run, which
`respond()` skips once the session is concluded). The tournament is
deterministic and bounded (max 5 hypotheses), so this is accepted.
Deliberately avoided: re-ranking the SAME hypothesis set twice in one
turn. The re-rank's confidence adjustments are not idempotent (VALIDATED
+0.1, REJECTED x0.5 per application), so the pre-turn run reports
verdicts without re-ranking (`rerank=False` in
`run_tournament_for_state` — the authoritative re-rank is the gate's /
the post-turn run's), and the post-turn `_apply_early_tournament`
reuses the gate's verdicts from the `conclusion_held` timeline entry when
the gate already ran on this turn's hypotheses (a demoted conclusion),
running only the discriminating-test selection itself.

## 3. No repeated discriminating test for an already-checked cause

Timeline evidence (Nishant's live session): the exact same question
("What do you observe when you check the leading cause — Insufficient
wire feed pressure.?") was asked on two consecutive turns (#3 and #4),
and the step flip-flopped between giving a fix ("Loosen the Feed
Tensioner...") and re-asking about the same cause ("Feed Tensioner is too
tight?").

Rule implemented: once a discriminating test for a cause has been asked
and answered, it is not asked again for that cause in the same session.
If the observation does not resolve the cause, the next step says what
changed (or did not change) instead of repeating the same question. The
timeline is the record — no new persistence layer was invented.

- `_asked_causes_and_tests(state)` (`backend/diagnostics/agent.py`):
  scans the timeline for previous turns' question/action steps mentioning
  a live/eliminated hypothesis cause (loose cause-text match, the same
  rule as the registry/tournament) and for earlier `early_tournament`
  entries' discriminating tests (and their `cause` field — the routes now
  record the top hypothesis cause on every `early_tournament` entry).
  "Asked-and-answered" holds for a previous turn because the technician's
  input this turn is the answer to it.
- `_default_question_step` now asks about the leading cause that has NOT
  been asked yet; when every live hypothesis was already checked it asks
  what changed since those checks.
- `_apply_no_repeat_gate` (in `run_turn`, after the safeguards): when the
  next step is a question/action whose every mentioned cause was already
  asked-and-answered, it is rewritten to the next untested cause, or —
  when every live cause was checked — to a "what changed since those
  checks?" question.
- `select_discriminating_test`
  (`backend/diagnostics/backtracking.py`): new keyword-only
  `exclude_tests` parameter — already-asked test names are skipped when
  collecting candidate tests, so the next-best unasked test is returned
  (or None when every splitting test was already asked).
- `_apply_early_tournament` (routes): passes the already-asked test names
  from earlier turns' timeline entries as `exclude_tests`.
- The pre-turn verification block also lists the already-asked
  causes/tests to the LLM (prompt rule 7), so the model itself is told
  not to re-ask them.

## 4. Escalation removed from the diagnosis loop

Nishant's direct instruction: remove the escalation feature — he calls it
useless; it cut his live session off instead of diagnosing.

(a) `_apply_forced_safeguards` no longer escalates. The turn-limit and
    low-confidence escalations are gone (constants
    `_MAX_TURNS_BEFORE_FORCED_ESCALATION`,
    `_LOW_CONFIDENCE_ESCALATION_THRESHOLD`,
    `_TURNS_BEFORE_LOW_CONFIDENCE_CHECK` removed; the bound survives as
    `_MAX_TURNS_BEFORE_UNAVAILABLE_ENDING = 8`). Low confidence alone now
    does nothing — the turn stays whatever the model picked.
(b) The LLM can no longer pick escalate: `NextStepType.escalate` was
    removed from the diagnosis output schema
    (`_DIAGNOSIS_OUTPUT_SCHEMA` enum is now question/action/conclusion)
    and from the prompt JSON shape and rules (prompt rules 1, 4, 7
    rewritten; the prompt docstring notes the removal). The escalated
    transition in `run_turn` is removed. `NextStepType.escalate` itself
    is KEPT as an enum member, marked LEGACY, so stored sessions that
    still carry an escalate step (Nishant's live session is one) still
    validate on load; it is never emitted. A model returning an escalate
    step anyway is degraded to a question in `_normalize_result` (and
    belt-and-braces in `_apply_forced_safeguards`).
(c) The session still ends for a loop that cannot conclude: at the turn
    bound the next step becomes a question carrying the
    unavailable-with-attempt (`backend/diagnostics/backtracking.py`:
    `unavailable_with_attempt`) — ranked best guesses, the
    discriminating test when known, and what is missing — plus a
    `diagnosis_unavailable` timeline entry. Never a bare stop, never an
    infinite loop, never an escalation. The session stays active so the
    technician can still answer the discriminating test; every later turn
    re-presents the updated attempt (the bound keeps firing). A validated
    conclusion at the turn limit still concludes (the gate runs first).
(d) The no-provider case in `_run_llm_turn` now returns a question step
    via `_degraded_result` (hypotheses intact, content says plainly no
    provider is configured) instead of an escalate step.
(e) Dead-reference cleanup (trivial only; no UI redesign):
    `DiagnosticStatus.escalated` is KEPT as a value for sessions already
    escalated in the DB — just never set from `run_turn` or the
    safeguards again (comment updated in `backend/db/models.py`).
    Frontend: no `escalat*` references existed, nothing changed.
    The routes module docstring ("until it concludes or escalates") now
    reads "until it concludes or ends with the ranked best guesses".
    The unused `rerank_with_tournament` import in the routes was removed
    (the shared helper now owns the re-rank).

Tests changed/removed (all recorded here, as required):

- `tests/test_diagnostics.py` (rewritten, 4 tests → 5):
  - `test_forced_escalation_on_sustained_low_confidence` REMOVED —
    low-confidence escalation no longer exists; replaced by
    `test_low_confidence_no_longer_escalates` (asserts the turn stays a
    question).
  - `test_forced_escalation_on_turn_limit_regardless_of_confidence`
    REMOVED — the turn limit no longer escalates; replaced by
    `test_turn_limit_ends_with_unavailable_attempt_not_escalation`
    (asserts a question step carrying the attempt, `turn_bound_reached`,
    "turn limit", the "unknown upstream fault" fallback guess, and
    "What is missing").
  - `test_no_forced_escalation_when_confidence_high_and_turns_low`
    and `test_high_confidence_conclusion_is_not_overridden_before_limits`
    kept unchanged.
  - Added `test_escalate_step_from_model_is_degraded_to_question`.
- `tests/test_diagnosis_robustness.py`:
  - `test_no_provider_still_escalates_honestly` RENAMED to
    `test_no_provider_returns_question_not_escalation` — asserts a
    question step mentioning the provider, hypotheses intact. (The other
    "never escalates" assertions in this file, e.g.
    `test_truncated_output_is_repaired_not_escalated`, still pass
    unchanged.)
- `tests/test_phase456_followup_walk.py`: the comment explaining the
  `confidence=0.5` fixture as a dodge around the forced-escalation guard
  updated — the guard is gone, the 0.5 is now just the walk's working
  confidence. No assertion changed.

## 5. Files changed

- `backend/diagnostics/twin_verification.py` (NEW) — shared twin loader +
  tournament runner + conclusion-cause / verdict / prompt-block helpers.
- `backend/diagnostics/agent.py` — pre-turn tournament + verification
  block; conclusion gate (`_apply_conclusion_gate`,
  `_demoted_conclusion_step`); no-repeat helpers
  (`_asked_causes_and_tests`, `_apply_no_repeat_gate`,
  `_LEADING_CAUSE_RE`) and no-repeat-aware `_default_question_step`;
  repurposed `_apply_forced_safeguards` (escalate→question, turn-bound
  unavailable ending, `_render_unavailable_attempt`); `_run_llm_turn`
  takes the verification block and the no-provider case returns a
  question; `_normalize_result` degrades a model-returned escalate step
  to a question; output schema without escalate; `run_turn` reordered
  (pre-turn tournament → LLM → gate → safeguards → no-repeat → persist)
  and the escalated status transition removed; docstring updated.
- `backend/diagnostics/prompts.py` — escalate removed from the JSON shape
  and rules; rule 7 (only conclude a VALIDATED cause; never repeat an
  already-asked question); `{verification_block}` section.
- `backend/diagnostics/schema.py` — `NextStepType.escalate` marked
  LEGACY (kept so stored states validate); `NextStep.content` comment.
- `backend/diagnostics/backtracking.py` — `select_discriminating_test`
  gains keyword-only `exclude_tests`.
- `backend/api/routes/diagnostics.py` — `_load_twin_for_session`
  delegates to the shared helper; `_apply_early_tournament` uses
  `run_tournament_for_state`, records `twin_unavailable` on the timeline
  when there is no twin, records the top-cause `cause` on
  `early_tournament` entries, and excludes already-asked discriminating
  tests; unused `rerank_with_tournament` import removed; module
  docstring updated.
- `backend/db/models.py` — `DiagnosticStatus.escalated` comment (legacy).
- `tests/test_diagnostics.py` — rewritten (see §4).
- `tests/test_diagnosis_robustness.py` — no-provider test renamed (see §4).
- `tests/test_phase456_followup_walk.py` — stale guard comment updated.
- `tests/test_verify_before_conclusion.py` (NEW, 12 tests) — (i) unvalidated
  conclusion demoted, session active; (ii) VALIDATED conclusion stands;
  (iii) no twin → UNVERIFIED label + "no approved twin" on the timeline;
  (iv) pre-turn verdicts visible in the LLM prompt before concluding;
  (v) gate on the start-session first turn; no-repeat: same cause not
  asked twice, all-causes-checked asks what changed,
  `select_discriminating_test` exclusion, `_apply_early_tournament`
  does not repeat a discriminating test; escalation removal: LLM
  escalate → question, turn limit → unavailable attempt, validated
  conclusion at the turn limit still concludes.
- `VERIFY_BEFORE_CONCLUSION_BUILD_NOTES.md` (NEW, this file).

## 6. Verification

Baseline (pre-change tree, full suite, run that started before any fix-3
edit): **433 passed, 0 failed** (24:18 under load from concurrent runs).

Targeted runs (post-change):
- New + directly-affected files
  (`tests/test_verify_before_conclusion.py`,
  `tests/test_diagnostics.py`, `tests/test_diagnosis_robustness.py`):
  **41 passed, 0 failed** (12 new gate/no-repeat/escalation tests, 5
  guardrail tests, 24 robustness tests).
- Diagnosis-loop neighbours (`test_hypothesis_tournament`,
  `test_phase4_backtracking`, `test_phase4_steals`,
  `test_phase456_followup_walk`, `test_verified_repair`,
  `test_phase456_readiness_repairs`, `test_phase456_readiness_drafting`,
  `test_phase6_mode_b`, plus robustness): **107 passed, 1 failed** — the
  one failure was a bug in the updated no-provider test itself (the
  provider explanation lands in the step's rationale, not its content);
  fixed by asserting on the rationale, and the 41-passed run above
  re-verifies it.
- Oracle / proof-twin re-measurement (after the full suite):
  `test_proof_twin_reproduces_known_3c_numbers` +
  `test_real_oracle_json_runs_on_synthetic_twin` (both params) +
  `test_demo_motor_drive_still_seven_transitions` + all of
  `test_phase3a_oracle.py` + `test_phase3c_oracle_fix.py`:
  **34 passed**. Proof-twin numbers re-measured directly:
  **oracle 20/35, 648 transitions, 18 components, Demo 7 transitions** —
  unchanged.

Full suite (post-change): **459 passed, 3 failed, 462 total** (40:05).
All 3 failures are in `tests/test_benchmark_oracle_runner.py` — a file
a sibling agent added to the working tree WHILE this suite was running
(file 13:29, plus `tests/test_pdf_loader_images.py` 13:33, and the
runner script `scripts/run_benchmark_oracle.py` edited 14:09, inside the
suite window). They are NOT fix-3 regressions:
- the failing tests do not import any fix-3 module (the runner only
  mentions `twin_verification.py` in a docstring; the tests exercise the
  simulation oracle + the runner script only);
- re-running the same file standalone immediately after the suite:
  **16 passed, 0 failed**, including the 3 that failed in-suite;
- the 3 failures are therefore attributed to the sibling editing shared
  files mid-run, not to this build. Every one of the 433 baseline tests
  passes in the post-change suite, as do all 13 fix-3 tests and 13 of the
  sibling's 16 (the other 3 pass standalone).

No LLM calls were used for the build itself; tests stub the gateway.

Note: the zip below is the whole v12 tree, per the previous builds'
convention — so it also carries the sibling's in-flight benchmark-runner
work (`scripts/run_benchmark_oracle.py`,
`tests/test_benchmark_oracle_runner.py`,
`tests/test_pdf_loader_images.py`), which landed in the tree during this
build. The tree is self-consistent with them included (the test file
imports the script); excluding them would have broken the suite.

## 7. The "twin cannot express this cause" path (fix-3 addition, 21 Nov 2026)

No new zip was packaged for this addition (the parent combines all fixes
into one zip); no delivered zip was overwritten. No LLM calls were used
for the build itself; tests stub the gateway the way earlier builds did.

### 7.1 The trap (confirmed missing before changing anything)

Nishant's live case-1 session: the leading cause, "Insufficient wire feed
pressure", is an adjustment-type cause, not a component. On 13 Nov,
verify-repair on that same cause returned available:false, "Could not
match the cause to a component in this twin; the twin cannot express this
failure". The failure mode carries no entity_ids, so even a twin with
transitions could not express it; adjustment-cause mapping was left for
Phases 4–5. A hard gate ("a conclusion stands only if the twin tests the
top theory and passes") therefore deadlocks on it: it waits forever for a
pass that cannot come.

Re-verified in the current tree before changing anything: `_apply_conclusion_gate`
(`backend/diagnostics/agent.py`) demoted any non-validated conclusion to a
question step via `_demoted_conclusion_step` — asking about the next
untested cause, and when every cause was checked asking "What is the single
most informative observation you can report next?" The session only ended
at the turn limit, with the generic turn-bound unavailable-with-attempt
message. There was NO path that said plainly "the twin cannot express this
cause", and NO test in `tests/test_verify_before_conclusion.py` covered the
adjustment-cause case (the 12 tests cover demotion, validated-stands,
no-twin, verdicts-visible, no-repeat, turn-limit, escalate-to-question).
The tournament returns `available: True` for the session with the cause's
status `"unavailable"` (`run_tournament_for_state` /
`verdict_for_cause` in `backend/diagnostics/twin_verification.py`) — and
the gate treated that the same as any other non-validated verdict.

### 7.2 The signal keyed on — and what the current signal did NOT distinguish

The current signal did NOT distinguish the two outcomes. Status
`"unavailable"` (decided in `tournament_status` only by the absence of a
verdict) conflates at least four different rows, all with
`available: False, verdict: None`:

1. No component match (`verify_single_cause`, no-match branch): "Could not
   match the cause ... to a component in this twin; the twin cannot
   express this failure" — the 13 Nov live shape; no entity_ids, no name
   match. The twin cannot express the cause.
2. Registry match whose reviewed RepairAction does not resolve in this
   twin (`run_verified_repair_action`, via the registry branch): "The
   RepairAction does not resolve in this twin: ..." — the reviewed
   fault/repair references components this twin does not have. The twin
   cannot express the cause either. **Measured, not assumed: in the
   test fixture this is the row the pressure cause actually returns.**
   The registry (`backend/knowledge/repair_cases_omnipro.json`, case 1)
   holds "Insufficient wire feed pressure." with fault_injection
   instance "Wire Feed Mechanism", parameter tension=low, and a reviewed
   RepairAction ("Increase wire feed pressure..."); the small test twin
   has no Wire Feed Mechanism / MIG Gun / Welder components, so the
   action does not resolve there. The parent's brief cited the no-match
   reason for this cause; the current tree returns the unresolved-action
   reason for the same cause string in the fixture, so the new path keys
   on both shapes.
3. Ambiguous component match: "matches N twin components ...; ambiguous,
   so no guess was made" — components exist; only the resolution is
   ambiguous. The twin is NOT unable to express the cause.
4. Registry fault_injection with no RepairAction: "the twin can express
   the fault but not the repair" — explicitly CAN express the fault.
5. Experiments the twin ran but could not complete (fault did not
   reproduce the symptom; no recovery transition fired; trace contract
   unsatisfied; unmodelled input): the twin expressed the fault, the
   experiment ran. Plus REJECTED verdicts: the twin evaluated the cause
   and the repair did not restore the machine.

New minimal distinguishing signal, in the tournament layer only
(`backend/simulation/tournament.py`): every tournament row now carries
`cannot_express: bool`, True only when no component in this twin can
carry the cause — shape (1) the no-match branch, and shape (2) the
registry branch when `run_verified_repair_action` returns
`available: False` with reason "The RepairAction does not resolve in
this twin: ..." (detected by that substring; the reason text is built in
`backend/simulation/verification.py`, `run_verified_repair_action`, and
the check is commented as such at the use site). Shapes (3), (4), (5)
and REJECTED rows carry `cannot_express: False`, explicitly. The verdict
logic for evaluated causes is unchanged (`tournament_status`,
`rank_tournament_rows`, and every verdict are untouched); `run_tournament`
normalises the key onto every row so callers never infer it from the
absence of a key. `row_cannot_express(row)` (`tournament.py`) and
`cannot_express_cause(results, cause)`
(`backend/diagnostics/twin_verification.py`, mirroring
`verdict_for_cause`) are the two readers.

Deliberately NOT covered, and why: a component that IS matched but has
no simulation rule describing a fault in it ("No simulation rule in this
twin describes a fault in entity:wire-feed-motor; the model cannot
express this failure" — the motor cause in the fixture) is the same
deadlock shape one step further down, but it has a matching component,
so it is outside the specified no-component signal; it still demotes
today. Flagged here as a follow-up decision, not silently folded in.
Likewise the fault-instance-resolves-to-zero-components sub-case inside
`run_verified_repair_action` (after the action resolves) is not covered;
distinguishing its zero-vs-many outcomes would need more plumbing.

### 7.3 The new path

`_apply_conclusion_gate` (`backend/diagnostics/agent.py`): after the
VALIDATED branch, when the concluded cause carries the
`cannot_express` signal, the gate does NOT demote-and-ask-the-next-cause,
does NOT ask for more observations, and does NOT wait for the turn limit.
It ends the session at once with the existing
`unavailable_with_attempt` outcome
(`backend/diagnostics/backtracking.py`): ranked best guesses (from the
re-ranked hypotheses), the discriminating test when known, and what is
missing — plus a plain statement. Never a bare stop, never an infinite
loop.

- `_render_cannot_express_attempt(attempt, cause, reason)` — opens with
  "The twin cannot express this cause — {cause}. {reason}", states that
  no further observation can make the twin express it, then the ranked
  guesses, the discriminating test when known, and "What is missing".
  It asks NO further question (no "?" anywhere in the text).
- `_cannot_express_ending(state, cause_text, row_reason)` — builds the
  attempt (missing: "a twin that models {cause}" and "a check of this
  cause outside the twin (the manual's own test for it)") and the ending
  step. The step keeps the loop's ending shape (a question-type step
  carrying the attempt, the same shape as the turn-bound ending): there
  is no unavailable status in `DiagnosticStatus`, and a conclusion step
  would falsely mark the session concluded. The content itself asks no
  question — the ending is the answer. The discriminating test is None
  here, as in the turn-bound ending: no signature source exists in the
  agent layer, so the test line appears only when one is known.
- The gate appends a `conclusion_unexpressible` timeline entry (cause,
  verdict "unavailable", reason, full results, counts) and sets
  `result["cannot_express_ending"] = True` plus
  `result["cannot_express_reason"]`, so `run_turn` records a
  `diagnosis_unavailable` timeline entry for the ending.
- Guards so the ending survives the rest of the turn:
  `_apply_forced_safeguards` returns early when the flag is set (the
  turn-bound guard must not overwrite the ending with the generic
  turn-limit text, not even on turn 8); `run_turn` skips the no-repeat
  rewrite for it, exactly as for the turn-bound ending.
- `respond()` path (`backend/api/routes/diagnostics.py`,
  `_apply_early_tournament`): the `_held` reuse lookup now also matches
  `conclusion_unexpressible` entries for this turn (the gate already
  re-ranked; re-running would apply the non-idempotent confidence
  adjustments a second time), and the discriminating-test rewrite is
  skipped when such an entry exists, so the post-turn tournament cannot
  turn the ending back into a "Discriminating test: ... What do you
  observe?" question. (The routes file keeps its CRLF line endings.)
- Audit rows carry the signal too: the pre-turn `early_tournament`
  entries in the agent and the post-turn ones in the routes now record
  `cannot_express` per row.

### 7.4 Files changed

- `backend/simulation/tournament.py` — `cannot_express` on the no-match
  (True), ambiguous (False), registry no-RepairAction (False) and
  registry evaluated (computed) returns; `row_cannot_express()` helper;
  `run_tournament` normalises the key onto every row; module docstring.
  No verdict logic changed.
- `backend/diagnostics/twin_verification.py` — `cannot_express_cause()`
  helper, mirroring `verdict_for_cause`.
- `backend/diagnostics/agent.py` — `_render_cannot_express_attempt`,
  `_cannot_express_ending`, the gate's cannot-express branch with the
  `conclusion_unexpressible` timeline entry, the
  `_apply_forced_safeguards` early return, the `run_turn` no-repeat skip
  and `diagnosis_unavailable` recording for the ending, the signal on
  pre-turn `early_tournament` rows, docstring.
- `backend/api/routes/diagnostics.py` — `_apply_early_tournament`
  reuses `conclusion_unexpressible` verdicts and never rewrites such an
  ending into a discriminating-test question; signal on its
  `early_tournament` rows.
- `tests/test_verify_before_conclusion.py` — new test
  `test_conclusion_about_cause_twin_cannot_express_ends_session_plainly`
  (see §7.5).
- `tests/test_hypothesis_tournament.py` — ONE existing assertion updated:
  `test_single_cause_result_keeps_the_v9_response_keys` pinned the exact
  key set of the no-match dict; the expected set now includes
  `cannot_express`, and asserts it is True for the no-match ghost cause.
  This is the deliberate consequence of adding the signal to that dict;
  no verdict or behaviour in that test changed.
- `VERIFY_BEFORE_CONCLUSION_BUILD_NOTES.md` (this file) — this section.

Not changed, as required: the oracle, the tournament's verdict logic for
evaluated causes, and the escalation-removal behaviour.

### 7.5 Verification

Baseline (pre-change tree, full suite, measured for this addition):
**491 passed, 0 failed** (24:03). (Fix-3's baseline was 433; the tree has
grown since, including the sibling's benchmark-oracle runner.)

New test
`test_conclusion_about_cause_twin_cannot_express_ends_session_plainly`:
the LLM concludes "Insufficient wire feed pressure — increase the feed
pressure properly" with hypotheses `[PRESSURE_CAUSE, TIGHT_CAUSE]` on
the fixture twin. Asserts the step content says "twin cannot express"
and names the cause, asks no further question (no "?" in the content, no
"What do you observe", no "next untested cause"), is not an UNVERIFIED
demotion, ends at turn 1 (not the turn limit), carries "What is missing",
has `conclusion_unexpressible` + `diagnosis_unavailable` timeline entries
and no `conclusion_held` entry, and the pressure row in the entry is
status "unavailable" with `cannot_express` True. A second turn concluding
the same cause ends the same way at turn 2 — the loop never runs to the
turn limit.

Targeted runs (post-change):
- `tests/test_verify_before_conclusion.py`: **13 passed** (12 existing + 1 new).
- Targeted + neighbours (`test_verify_before_conclusion`,
  `test_diagnostics`, `test_diagnosis_robustness`,
  `test_hypothesis_tournament`, `test_verified_repair`,
  `test_phase456_readiness_repairs`): **76 passed, 0 failed**.
- Proof-twin re-measurement (`test_proof_twin_reproduces_known_3c_numbers`
  + `test_real_oracle_json_runs_on_synthetic_twin` (both params) +
  `test_demo_motor_drive_still_seven_transitions` + all of
  `test_phase3a_oracle.py` + `test_phase3c_oracle_fix.py`): **34 passed**.
  Proof-twin numbers re-measured directly: **oracle 20/35, 648
  transitions, 18 components, Demo 7 transitions** — unchanged.

Full suite (post-change): **492 passed, 0 failed, 492 total** (17:00) —
baseline 491 passed, plus the 1 new test, with the updated
`test_single_cause_result_keeps_the_v9_response_keys` keeping its count.
Every baseline test passes; no regressions.

## 8. Extension: matched component with no fault rule (21 Nov 2026)

### 8.1 The gap (§7.2's flagged follow-up, now folded in)

§7.2 named this shape explicitly and left it demoting: a cause whose
component IS matched in the twin, but no simulation rule describes a
fault in it — `build_verification_experiment`
(`backend/simulation/verification.py`, the `not candidates` branch)
returns available False with reason "No simulation rule in this twin
describes a fault in entity:wire-feed-motor; the model cannot express
this failure." The tournament's legacy branch returned that dict with
no `cannot_express` key, so `run_tournament` normalised it to False
and the gate demoted the conclusion, asking for observations that
cannot help: no observation can make a rule exist. It is the same
deadlock as the no-match shape, one layer down — the tournament can
never VALIDATE a conclusion about a fault in a component with no fault
rule, so demoting loops until the turn limit.

### 8.2 The signal extension

`verify_single_cause` (`backend/simulation/tournament.py`, legacy
branch only) now computes the signal from the verified-repair outcome
and carries it on the returned row:

```python
cannot_express = (
    not outcome.available
    and (outcome.reason or "").startswith(
        f"No simulation rule in this twin describes a fault in {fault_component_id}")
)
```

The reason text is unchanged — it already names the component
(`entity:wire-feed-motor`, the twin id) and the missing rule, so the
gate's plain ending states both verbatim. String-keyed on the reason
prefix, the same way the registry shape keys on "does not resolve in
this twin" — the reason is the outcome's own statement of why the twin
cannot evaluate the cause. Scope is deliberately narrow: only this
reason flips the flag. Every other unavailable reason from the legacy
path (fault did not reproduce the symptom, no recovery rule, no
recovery transition fired, rules with no effects) still means the twin
expressed the fault but could not complete the experiment, so those
rows stay `cannot_express` False and still demote.

The module docstring now lists the three cannot-express shapes
(no-match / registry-does-not-resolve / matched-component-no-fault-rule).

### 8.3 The gate needs no change — confirmed, not assumed

The gate keys on `cannot_express_cause(summary["results"], cause.cause)`
(`backend/diagnostics/twin_verification.py`), which reads only
`row.get("cannot_express")`; `run_tournament` already normalises the
key onto every row. The new True value therefore flows through the
existing cannot-express branch untouched: no demotion, no next-cause
question, no waiting for the turn limit — the session ends at once
with the same `unavailable_with_attempt` ending (§7.3), whose "What
is missing" lines name "a twin that models {cause}" and "a check of
this cause outside the twin (the manual's own test for it)". The
`_apply_forced_safeguards` early return and the `run_turn` no-repeat
skip both key on `result["cannot_express_ending"]`, so they cover
this shape with no change either. `backend/diagnostics/agent.py` and
`backend/diagnostics/twin_verification.py` are unchanged by this
extension; the verification block already shows the row's reason, so
the LLM sees the missing-rule reason before it concludes.

### 8.4 Test changes and why

MOTOR_CAUSE ("Wire Feed Motor is overheating") IS this shape: its
component `entity:wire-feed-motor` ("Wire Feed Motor") is in the
fixture twin and matches by unique name, and no seed rule mentions
it — verified by reading the fixture and the reason the row carries.
So the tests that pinned its demotion changed; the demotion behaviour
itself is still pinned, by a different shape:

- `tests/test_verify_before_conclusion.py` —
  `test_conclusion_with_unvalidated_top_hypothesis_is_demoted` is
  REPLACED by
  `test_conclusion_about_matched_component_with_no_fault_rule_ends_session_plainly`:
  same setup (conclusion about MOTOR_CAUSE, hypotheses
  `[MOTOR_CAUSE, TENSIONER_CAUSE]`), now asserting the plain ending —
  content says "twin cannot express", names the cause and the reason
  "No simulation rule in this twin describes a fault in
  entity:wire-feed-motor", asks no question (no "?", no "What do you
  observe", no "next untested cause"), is not an UNVERIFIED demotion,
  ends at turn 1, carries "What is missing", has
  `conclusion_unexpressible` + `diagnosis_unavailable` entries and no
  `conclusion_held` entry, the motor row is status "unavailable" with
  `cannot_express` True and `fault_component_id`
  "entity:wire-feed-motor", and the re-ranked hypotheses still lead
  with the VALIDATED tensioner. The old test's assertions are
  preserved in shape; only the expected outcome changed, because the
  behaviour changed.
- Same file, NEW `test_conclusion_with_ambiguous_cause_is_still_demoted`:
  the demotion path still exists for a cause the twin CAN express but
  cannot resolve — AMBIGUOUS_CAUSE ("Feed Tensioner and Welding Wire
  fault", matches two components, `cannot_express` False) — concluding
  about it is still held as UNVERIFIED with a `conclusion_held`
  entry, the session stays active, and no `conclusion_unexpressible`
  entry appears.
- Same file, `test_start_session_first_turn_conclusion_is_gated`
  updated for the same reason: it also concluded about MOTOR_CAUSE, so
  on the start-session first turn it now asserts the cannot-express
  ending (no "?", no UNVERIFIED, `conclusion_unexpressible` entry)
  instead of the demotion. The test name is kept: the gate still
  applies on the first turn.
- The §7 comment block above
  `test_conclusion_about_cause_twin_cannot_express_ends_session_plainly`
  is corrected: it previously said the motor cause "still demotes";
  it now says the motor cause is the third shape and the still-demoting
  example is the ambiguous cause.
- `tests/test_hypothesis_tournament.py` — NEW
  `test_matched_component_with_no_fault_rule_carries_cannot_express`:
  the motor row from `verify_single_cause` is available False,
  verdict None, `fault_component_id` "entity:wire-feed-motor", the
  exact no-fault-rule reason, and `cannot_express` True; through
  `run_tournament` the motor row is "unavailable" with
  `cannot_express` True while the ambiguous row is "unavailable" with
  `cannot_express` False — pinning the boundary between the two.

### 8.5 Files changed

- `backend/simulation/tournament.py` — the `cannot_express`
  computation on the legacy-branch return, plus the module docstring.
  No verdict logic changed; no other file in `backend/` changed.
- `tests/test_verify_before_conclusion.py` — one test replaced, one
  test added, one test updated, one comment block corrected (see §8.4).
- `tests/test_hypothesis_tournament.py` — one test added (see §8.4).
- `VERIFY_BEFORE_CONCLUSION_BUILD_NOTES.md` (this file) — this section.

Not changed, as required: the gate logic, the twin-verification helper
logic, the verification reason text, the LLM/provider/config code, the
benchmark folder, and anything outside the listed files. (Two docstrings
were corrected after independent verification — see §8.7; no logic changed.)

### 8.6 Verification

Targeted runs (post-change):
- `tests/test_verify_before_conclusion.py` +
  `tests/test_hypothesis_tournament.py`: **29 passed** (baseline 27:
  +1 new agent test, +1 new tournament test; the replaced and updated
  tests keep their counts).
- No other test file references MOTOR_CAUSE or the old demotion
  behaviour (grep over `tests/`); the diagnostics route only forwards
  the `cannot_express` flag (`backend/api/routes/diagnostics.py:694`),
  so no other file needed a change.

Full suite (post-change, project venv, in-memory SQLite):
**494 passed, 0 failed, 494 total** (23:15) — baseline 492 passed,
plus the 2 new tests, with the replaced
`test_conclusion_about_matched_component_with_no_fault_rule_ends_session_plainly`
and the updated `test_start_session_first_turn_conclusion_is_gated`
keeping their counts. Every baseline test passes; no regressions.

(Caveat: the first full-suite run of this extension was killed
~60% through by a runtime restart with its `/tmp` log lost; the
count above is from a clean re-run afterwards, log kept in the
workspace until recorded here.)

### 8.7 Post-verification corrections (coordinator, 21 Nov 2026)

The independent verifier passed the build but flagged two
non-behavioral items:

1. **Stale docstrings — fixed.** The gate docstring in
   `backend/diagnostics/agent.py` (~lines 851–858) and the
   `cannot_express_cause` docstring in
   `backend/diagnostics/twin_verification.py` still described only the
   two fix3 shapes. Both now list the third shape as well ("the matched
   component has no simulation rule describing a fault in it").
   Docstring-only change; targeted re-run after the edit: 29 passed.
   The full-suite 494 count above predates this docstring-only edit;
   no logic changed, so no full re-run was done.
2. **Boundary coverage gap — noted, not fixed.** No test pins the other
   legacy-path unavailable reasons ("Fault did not reproduce…", "No
   recovery rule…", "Rules mentioning … have no effects…", etc.)
   staying `cannot_express` False; only the ambiguous-cause boundary
   is test-pinned. Code reading (verifier + builder) confirms the
   prefix check is narrow enough that none of them flips the flag, but
   a future regression there would not be caught by the suite. Left
   for the parent's call — adding such a test is a small, well-scoped
   follow-up if wanted.
