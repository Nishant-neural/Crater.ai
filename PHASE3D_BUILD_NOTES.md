# Phase 3D build notes — Layer 2 drafting, Layer 3 field promotion, review view

## Scope

- Layer 2 LLM slot-filling per v3 §7: drafting brief, six pre-review checks
  with the oracle as check 5, review view, bounded loop (max two revision
  rounds), authorization states draft → candidate → in review →
  approved/rejected/needs revision. Candidate 0.x versions compile ONLY in
  sandbox test twins, never an approved one.
- Layer 3 field-promotion scaffolding: `validation_history` wiring +
  promotion gate with human approval. NOTHING auto-promotes, not even after
  many validations. Promotion counts field outcomes only, never simulator
  verdicts alone.
- Binding points table: decided on measured 3B/3C data — NOT built (see
  below).
- Review-view UI: §7.4 five-part view as a self-contained HTML report
  generator (no React build step, no external dependencies).
- Twin VALIDATED and field SUCCESSFUL kept as separate statuses throughout.

## Modules

- `backend/simulation/drafting.py` — Layer 2.
  - `DraftLLM` Protocol: the LLM's slot — given a drafting brief (dict),
    return a draft archetype (dict). NOT implemented in this build (no LLM
    call anywhere). `draft_archetype(brief, llm=None)` raises
    `NotImplementedError` when no LLM is supplied. The gate, review view
    and loop all take a draft that is already produced (in tests,
    hand-written drafts stand in for LLM output).
  - `build_drafting_brief(case_id, drafting_mode="B")`: one case per brief;
    the case from the registry; structured facts from the canonical model
    (quantities with page citations, failure modes, topology around the
    symptom component one hop, procedures empty); the archetype schema as
    JSON schema; nearest archetypes top 2 by port/parameter Jaccard
    similarity; the 3 hand-written archetype instances (A2/A3/A4 on
    OMNIPRO) as gold examples; the hard constraints list (6 items, v3
    §7.2(f)). Mode B default (start Layer 2 with B for the first two
    instances to calibrate, then D).
  - Draft schema (Pydantic): `DraftParameter` (value + source_page/
    source_field, or unbound + reason; `domain` and `nominal_default`
    carried explicitly — see deviation 4), `DraftPort` (name, direction,
    domain, unit, source_page, or `inferred_from_template` mark;
    `required` flag — see deviation 3), `DraftRow` (row_id, inputs, fault,
    outputs, next_state, source_pages), `DraftFailureMode` (name,
    fault_state, parameter, discriminating_test — mandatory, expected_
    trace, source_pages), `DraftArchetype` (archetype_id, human_name,
    aliases, ports, states, parameters, propagation_table, failure_modes,
    version "0.1.0", authoring_layer "layer2-llm-drafted", intended_entity,
    `supersedes` — see deviation 3).
  - `DraftAuthorization`: states draft, candidate, in review, approved,
    rejected, needs revision; `ALLOWED_TRANSITIONS` enforced in code
    (illegal transition raises); approving requires a reviewer + checklist
    hash (raises otherwise); `ApprovalRecord` (reviewer, timestamp,
    checklist_hash, version, approvers list).
  - `sandbox_archetype_library(draft)`: registers the drafted archetype in
    the global `ARCHETYPES` dict ONLY inside the context, then removes it
    (restores the previous entry if one existed). The test twin compiled
    inside is in-memory only, never persisted, never pinned to an approved
    twin. Not thread-safe; only for the offline drafting gate, never a
    runtime path. The compiler was deliberately NOT modified to take an
    archetype-library override (the 3B gate depends on it).
  - Six pre-review checks (`GateCheck` with check/passed/outcome
    "pass"|"flag"|"reject"/reasons/evidence; `PreReviewGateResult` with
    verdict "candidate"|"rejected", checks, quarantined_fields,
    oracle_vectors):
    1. schema — Pydantic validation; malformed → one repair retry
       (`_repair_json_once` strips trailing commas) → then reject.
    2. value provenance — every parameter value must be in the allow-list
       (quantities extracted from the cited pages, from the brief's
       structured facts) or marked unbound. A non-allow-list value
       quarantines that field (flagged, visible, never silently dropped)
       and the draft continues to review WITH the flag. Never rejects.
    3. port-signature collision — draft's port signature identical to an
       existing archetype with no distinguishing evidence (a parameter or
       failure mode the existing archetype does not have) → auto-reject
       ("indistinguishable from <archetype>").
    4. template executability — every row's condition references only
       declared ports/states/parameters; every effect is a declared output
       port with a value in its domain (or "unknown"); every next_state is
       a declared state (or "unknown").
    5. oracle — the drafted instance is compiled into a sandbox test twin
       for its case (proof twin + drafted archetype registered only inside
       the sandbox), the oracle runs O1–O5 on that case's rows. O2 fail on
       its own case → reject with the vectors attached; O2 pass but O3
       fail → flag for the reviewer (not auto-rejected — often a missing
       test model, not a wrong behaviour). Only scored rows are checked
       (not-scored settings/technique/cross-reference rows carry no O2/O3
       checks and do not count against the draft).
    6. binding — the §8 rule runs on the case's entities (inside the
       sandbox): the draft must bind its intended entity and must not bind
       entities it should not. No intended entity → reject.
    `run_pre_review_gate(draft_raw, brief, draft_id)`: runs the six checks
    in order; a rejection on check 3 or 4 stops the gate (the oracle and
    binding checks need a well-formed, non-colliding draft).
  - `DraftingLoop`: max two revision rounds (`MAX_REVISION_ROUNDS = 2`).
    `request_revision(gate_result, reviewer)` builds the revision brief
    (structured rejection reasons: which field, which check/reason; flagged
    fields from quarantined_fields + reject/flag reasons; instruction
    "Re-draft ONLY the flagged fields, not the whole contract"). Raises
    ValueError when the cap is reached — after that a human writes it
    (Layer 1 fallback).
  - `archetype_to_draft(archetype, intended_entity, version)`: converts a
    hand-written archetype to a draft (for calibrating Layer 2: the gold
    examples round-trip through the draft schema). Parameters are marked
    unbound but keep their `domain` and `nominal_default` (see deviation
    4).
- `backend/simulation/field_promotion.py` — Layer 3.
  - `FieldOutcome` (archetype_id, row_id, machine_variant, technician,
    outcome "SUCCESSFUL"|"FAILED", discriminating_test, test_outcome,
    timestamp, notes). Entered by a human — the simulator never writes a
    field outcome.
  - `record_field_outcome(archetype, outcome)`: appends to the archetype's
    `validation_history` (validated_count, rejected_count, field_instances,
    plus a `field_outcomes` list of the outcome dicts). Counts FIELD
    outcomes only — simulator verdicts are never written here.
    (`validation_history` already existed on the Archetype model; this
    module wires it — it was previously only declared, never used.)
  - `PromotionApproval` (approver, timestamp, reason, field_successful,
    field_failed) + `PromotionDecision` (archetype_id, promoted, reason,
    field_evidence, approval).
  - `evaluate_promotion(archetype, approval=None)`: NOTHING auto-promotes
    — with no approval record the answer is always "not promoted", no
    matter how many field validations the type has. With an approval
    record, "promoted" with the FIELD counts attached as evidence.
    Simulator verdicts (oracle pass rates, twin VALIDATED counts) are
    never counted — the decision record deliberately does not include them.
  - `RepairRecord` (repair_id, case_id, cause, twin_verdict, field_outcome,
    field_technician, field_timestamp, learning_signal): twin_verdict
    (VALIDATED/REJECTED/unavailable) and field_outcome (SUCCESSFUL/FAILED)
    are separate fields. `record_twin_verdict` / `record_field_outcome`
    validate against `TWIN_VERDICTS` / `FIELD_OUTCOMES`.
    `_refresh_learning_signal` sets `learning_signal` when twin VALIDATED +
    field FAILED ("the twin's model of this repair is wrong… the archetype
    rows for this repair need review against the field outcome").
- `backend/simulation/review_view.py` — review view (§7.4, five parts).
  - `REVIEW_CHECKLIST` (8 items, from v2 §5.1): every parameter value has a
    source page or is explicitly marked unbound; every row's condition
    references only declared ports/states/parameters; every row's effect is
    a declared state or a port value in its domain; the oracle O2 passes on
    the draft's own case; the binding binds the intended entity and no
    entity it should not; every failure mode names a discriminating test;
    the diff against the nearest archetype is real (not a duplicate); no
    invented values.
  - `FieldReview` (field, action "approve"|"edit"|"reject", reason,
    edited_value) + `ReviewRecord` (draft_id, reviewer, field_reviews,
    checklist_ticked, checklist_total=8, decision "approved"|"rejected"|
    "needs revision"; `checklist_hash` = sha256 of the sorted ticked items,
    first 16 hex chars; `checklist_complete`).
  - `record_review(review)`: approval requires the full checklist ticked —
    raises ValueError otherwise. Returns the record dict for the
    authorization transition.
  - `render_review_view(draft, gate_result, *, brief, nearest_archetype_id,
    trace_messages, oracle_rows, binding_result, reviewer="Nishant") -> str`:
    self-contained HTML (inline CSS, no external scripts/stylesheets, no
    React build step). Five parts: (1) one-page summary (archetype,
    intended entity, cases served; ports/states/parameters/rows/failure-
    modes tables, each value with its source page); (2) diff vs nearest
    archetype (added/removed ports, parameters, states, rows, failure
    modes); (3) trace playback (ordered list of trace messages from the
    oracle run); (4) oracle result (per-row O2 pass/fail with reasons) +
    binding result (entity → archetype); (5) per-field actions (radio
    approve/edit/reject + reason text input per field) + checklist
    checkboxes + Approve / Request revision / Reject buttons. Notes that
    candidate 0.x versions compile only in sandbox test twins, and the
    15-minute review target.

## The binding points table — decided on measured 3B/3C data (NOT built)

v3 §8 defers the points table until the oracle gives data to tune it
against. The measured 3B/3C data:

- Proof twin: the §8 unique-match rule binds **15/18 (83.3%)** with **0
  ambiguous and 0 wrong bindings**. No O2 failure was caused by a wrong
  archetype binding.
- The 3 unresolved (Wingnut, Feed Tensioner, Welding Wire) have **no
  signature at all** (no ports recorded, no bound parameters). A points
  table ranks candidates, and with no signature there are no candidates to
  rank, so it could not bind them either.
- crater-10: 0 bound because the snapshot has 0 ports — the rule correctly
  says "no match" rather than guessing.

The rule is sufficient on the measured data; no points table is built. The
decision is pinned by a test (`test_points_table_not_needed_on_measured_
data`: 15/18 bound, 0 ambiguous, 3 unresolved with no signature). If
coverage ever falls below this rate or an ambiguous binding appears, the
question reopens.

## Tests (40 new, all passing)

`tests/test_phase3d_layer2_layer3.py` — **40 passed in 188 s** (slow because
the oracle-check and full-gate tests compile a sandbox proof twin at ~40 s
CPU each).

- LLM slot: `draft_archetype` without an LLM raises NotImplementedError; a
  stub LLM returns its output.
- Brief: case/facts/schema/nearest (top 2)/gold (A2/A3/A4)/constraints;
  mode B; unknown case raises.
- Check 1: valid draft passes; trailing comma repaired by the one retry;
  malformed output rejected.
- Check 2: unbound passes; a value outside the allow-list is quarantined
  (flagged, not rejected).
- Check 3: identical signature + no distinguishing evidence rejects;
  distinct signature passes; identical signature + a distinguishing failure
  mode passes.
- Check 4: valid rows pass; undeclared condition field / undeclared effect
  reject.
- Check 5: the A4 transmission draft on case 1 passes O2 (4/4); the same
  draft with the tension-low row's output broken fails O2 and is rejected.
- Check 6: the gas-regulator draft binds the Gas Valve and no other;
  a draft with no intended entity is rejected.
- Full gate: the gas-regulator draft on case 8 is a candidate (all six
  checks pass); a colliding draft is rejected at check 3 and the oracle /
  binding checks never run.
- Sandbox: the draft is registered only inside the context; a superseded
  archetype is removed during the evaluation and restored on exit; a
  supersede of an unknown archetype raises.
- Authorization: happy path records the approval (reviewer, checklist
  hash, version, approvers); illegal transition raises; approving without
  a record raises; the six states are the design's six.
- Bounded loop: a revision brief names only the flagged fields; the cap
  (2 rounds) raises ValueError (Layer 1 fallback).
- Field promotion: `record_field_outcome` wires `validation_history`;
  50 successful validations with no approval → not promoted; with a human
  approval → promoted on field evidence only (the decision carries no
  simulator verdicts).
- RepairRecord: twin VALIDATED + field FAILED sets the learning signal;
  twin VALIDATED + field SUCCESSFUL does not; the two statuses are
  separate fields.
- Review view: all five parts present; the diff names the nearest archetype
  and the delta; per-field actions + 8 checklist checkboxes are form
  controls; self-contained (no external script/stylesheet); approving with
  an incomplete checklist raises; a full checklist returns the record with
  a stable 16-char checklist hash.
- Points-table decision: the measured-evidence test above.
- `archetype_to_draft` preserves parameter domains and nominal defaults
  (regression test for deviation 4).

## Deviations from the design (3D)

1. **Review-view form: self-contained HTML report generator, not React.**
   The Amendments said review view first as a markdown report from the
   oracle run, UI in 3D. The HTML generator is the 3D form — no React build
   step, no external dependencies, one file the reviewer can open. The
   five parts (§7.4) are unchanged in shape.
2. **LLM slot, not an LLM call.** The drafting module defines the
   `DraftLLM` Protocol and raises `NotImplementedError` when no LLM is
   supplied. No LLM is wired in this build; tests use hand-written drafts.
   The six pre-review checks, the review view and the loop all take a draft
   that is already produced, so wiring a real LLM later is a one-function
   change (`draft_archetype` with an LLM that returns a draft dict).
3. **Sandbox supersede: a draft that refines an existing archetype declares
   what it supersedes.** The §8 unique-match rule cannot evaluate a draft
   that shares its intended entity's ports with an existing archetype — the
   binding would be ambiguous and the sandbox twin would break (the valve
   also wins the name hint for "Gas Valve"). So `DraftArchetype.supersedes`
   lists the archetype(s) the draft replaces for its intended entity, and
   `sandbox_archetype_library` removes them for the duration of the
   evaluation only, restoring them on exit. The approved library is never
   changed by an evaluation. A supersede of an unknown archetype raises.
   `DraftPort.required` was added for the same reason: a draft may offer an
   optional port (the gas regulator's `pressure_out`) that the intended
   entity does not carry — it is part of the signature for the collision
   check, but it does not block binding.
4. **Draft parameters carry `domain` and `nominal_default` explicitly.**
   The draft schema as first written had neither, and the sandbox inferred
   the domain from the value (or "qualitative" when unbound) and the
   nominal default from the bound value (or None when unbound). Two oracle
   failures exposed this:
   - A boolean parameter (`groove_matches`) inferred as qualitative: its
     rows (`groove_matches: True/False`) never matched any enumerated
     combo, so the feeder read unknown and the oracle failed O2 on the
     groove row and the downstream gun row.
   - An unbound parameter with no nominal default: the compiler could not
     seed the nominal state, so a fault on a downstream component (the
     gun, case 1(c)) left the feeder's tension/groove unset and the feeder
     read unknown instead of normal.
   The domain and nominal_default are part of the archetype definition, not
   bound model values — an unbound parameter still has both. The LLM fills
   them from the schema in the brief.
5. **Check 5 counts only scored rows.** Not-scored rows (settings,
   technique, cross-reference) carry no O2/O3 checks in the oracle report
   (`checks={}`), so `check_oracle` skips them instead of raising
   KeyError. They are not twin-expressible and do not count against the
   draft.
6. **Brief-builder facts read the model's typed fields, not
   `all_facts()`.** `all_facts()` returns (kind, id, payload) tuples, not
   objects with a `kind` attribute, so the quantities loop found nothing;
   relations use `subject_id`/`object_id` (not `source_id`/`target_id`);
   entity ports are plain port-name strings. Fixed to read
   `model.quantities`, `model.failure_modes` and `model.relations`
   directly.
7. **Vocabulary path uses `Path.home() / "workspace" / ...`** (the same
   pattern as the 3B/3C runners), not a `parents[N]` relative path — the
   vocabulary lives outside the v12 tree, in the goal files folder.

## Full suite (final, Phase 3 complete)

- **346 passed, 2 failed, 0 collection errors** (348 tests total, 697 s).
- The 2 failures are the pre-existing `test_phase8a_integration.py` ones
  (`test_pdf_chunk_modalities_reach_universal_persistence`,
  `test_schematic_projection_creates_entities_ports_and_relations`) — the
  same 2 that failed at the 272 baseline. No new failures.
- Count reconciliation: 272 baseline + 22 (3B) + 12 (3C) + 40 (3D) = 346.
- Demo Motor Drive compiles to exactly **7 transitions** (3B regression
  test, passing).

## Things NOT verified (need the user / a live run)

- End-to-end ingestion with the user's LLM + Qdrant (no live run was done;
  the proof twin is constructed, not ingested).
- Any live app check. The consolidated Phase 1+2 live check (fresh OMNIPRO
  upload/job) is still outstanding and is the precondition for a 3B/3C
  proof on a fresh live snapshot; nothing here replaces it.
- The M800V rates are lookup/discriminability rates on a 12-alarm sample,
  not twin-verified rates (no M800V twin exists).
- No real LLM has been wired to the `DraftLLM` slot. The drafting
  machinery (brief, six checks, review view, bounded loop, authorization)
  is tested with hand-written drafts; the first real LLM draft, and the
  15-minute review target, are untested.
- The 15-minute review target is stated in the review view but not
  measured (no reviewer has used it yet).
