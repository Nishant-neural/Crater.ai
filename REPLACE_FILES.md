# Resumable ingestion replacement

Replace these files on top of the Phase 9 progress project:

- `backend/api/routes/ingestion.py`
- `backend/ingestion/pipeline.py`
- `backend/ingestion/checkpoints.py` (new)
- `backend/ingestion/ocr.py` (new)
- `frontend/src/components/MachineOnboarding.jsx`
- `frontend/src/api/client.js`
- `frontend/src/App.css`

Features:
1. Retry failed ingestion without re-uploading the PDF.
2. Resume from the last persisted stage/checkpoint.
3. Progress is persisted in the existing `IngestionJob.stage` plus a durable JSON checkpoint under `data/ingestion_checkpoints/`.
4. Completed pages, vector indexing, knowledge chunks, and schematics are skipped on retry.
5. Tesseract is optional; the pipeline attempts a native-text-only retry when the PDF loader reports a Tesseract error and sets `CRATER_DISABLE_OCR=1`.
6. OCR absence is reported as a warning rather than a job failure when the underlying PDF loader supports that flag.
7. UI clearly distinguishes processing, FAILED, and READY and provides a Retry button.

Restart FastAPI after replacing files. Existing databases do not require new columns.

# General schema-normalization hardening (added)

Files changed/added for this upgrade:
- `backend/knowledge/normalization.py` (new shared alias normalizer, original-payload retention, deterministic IDs, unresolved ledger helper)
- `backend/knowledge/machine_model.py` (uses shared normalizer and preserves extra normalization metadata)
- `backend/knowledge/evidence.py` (invalid claim statuses remain strict validation errors)
- `backend/knowledge/validation.py` (semantic validation plus `validate_machine_model_with_ledger`)
- `backend/knowledge/__init__.py` (exports the new API)
- `tests/knowledge/test_schema_normalization.py` (regression tests)
- `docs/SCHEMA_NORMALIZATION.md` (contract and integration guidance)

The unresolved ledger API currently returns records; it does not automatically persist them to the database. The integration layer must persist returned records before claiming the ledger is durable.

# Code-first + adjudication redesign (31 Oct 2026) — replace these too

- `backend/knowledge/code_first_integration.py` (new)
- `backend/knowledge/global_integration.py` (new default `integrate_revision_knowledge`; old LLM path kept as `_integrate_revision_knowledge_llm`, method="llm" only)
- `backend/knowledge/component_extraction.py` (legacy items merged into universal model)
- `backend/ingestion/pipeline.py` (progress callback into integration)
- `tests/test_integration_code_first.py` (new)
- `tests/test_global_integration.py` (duplicate test updated to code-first contract)
- `tests/test_phase8c_integrity.py` (inheritance test updated to slug ids)

# Multi-chunk batch extraction fix (5 Nov 2026) — replace these too
- `backend/knowledge/component_extraction.py` (full batch prompt shapes, string-component / parameter-quantity normalisation, universal-conversion error wrapping)
- `backend/knowledge/normalization.py` (dict/string evidence -> list)
- `backend/ingestion/pipeline.py` (empty batch result retried once as single before knowledge_done)
- `tests/test_batch_extraction_fix.py` (new, mocked gateway, no LLM)
- `gold_set_test.py` (fixed version — the copy in project-3.zip was the pre-fix one with gateway_mod.gateway.complete; replace it with this fixed copy before rerunning the gold test)
- `tests/test_pipeline_empty_retry.py` (new, mocked ingest, no LLM/Qdrant)

# Unresolved-entity quantities fix (5 Nov 2026) — replace these too
- `backend/knowledge/validation.py` (keep a real, non-empty quantity even when its entity is unresolved; empty-value / placeholder-name quantities still quarantined)
- `backend/knowledge/code_first_integration.py` (same keep rule in build_code_first_payload, so kept quantities reach the snapshot instead of being dropped as fact_entity_not_uniquely_resolvable)
- `tests/test_quantity_unresolved_entity.py` (new, 3 tests, mocked gateway, no LLM)

# Same-chunk relation disambiguation (5 Nov 2026) — replace these too
- `backend/knowledge/code_first_integration.py` (same-chunk endpoint disambiguation, relation dedupe, adjudication link dedupe guard, adjudication logging)
- `backend/knowledge/global_integration.py` (source_chunk_id for universal entities/relations in the integration context; adjudication decision-count logging)
- `tests/test_relation_same_chunk.py` (new, 7 tests, no LLM)

# Relation endpoint recovery (6 Nov 2026) — replace these too
- `backend/knowledge/component_extraction.py` (endpoint-closure rule in both prompts; persist_extraction records a stub component for a missing relation endpoint instead of queueing pending forever; invalid relation_type still goes to pending)
- `backend/knowledge/validation.py` (quarantine keeps a relation with a named-but-unlisted endpoint and records a minimal endpoint_derived stub entity; no usable endpoint name still quarantines)
- `backend/knowledge/code_first_integration.py` (resolve_entity_id falls back to the endpoint name when the id slug does not resolve)
- `tests/test_relation_endpoint_recovery.py` (new, 12 tests, no LLM)
- `tests/test_ingestion_fixes_v4.py` (quarantine test updated to the new contract: named-but-unlisted endpoint is kept with a stub)

# Verified-repair loop (v8, added 6 Nov 2026)

Files changed/added for this upgrade (replace on top of the v7 tree):
- `backend/knowledge/schema.py` (UniversalFailureMode.entity_names; MachineKnowledgeExtractionResult.simulation_rules)
- `backend/knowledge/machine_model.py` (MachineFailureMode.entity_names; simulation_rule tuples in all_facts(); properties on MachineEvent and MachineConstraint)
- `backend/knowledge/component_extraction.py` (states push + simulation-rules contract in both prompts; failure-mode entity_ids guidance; simulation_rules in the normalize key tuples and _has_universal_facts; rules + failure-mode entity_names translation + properties.entity_name stamping in _universal_model_from_result)
- `backend/knowledge/validation.py` (per-rule simulation-rule veto; accepts name paths and canonical-id paths)
- `backend/knowledge/global_integration.py` (source_chunk_id on universal_facts rows; entity_names passthrough on failure modes in _model_from_payload)
- `backend/knowledge/code_first_integration.py` (simulation-rule pass with unique-only, same-chunk-preferred rewrite and known-field veto; failure-mode entity_names -> canonical entity_ids; properties.entity_name fallback + same-chunk preference for attached facts)
- `backend/simulation/compiler.py` (_coerce_state_value in _safe_initial_state; TwinTransition source_ids + evidence pass-through)
- `backend/simulation/schema.py` (TwinTransition source_ids + evidence)
- `backend/simulation/verification.py` (new: build_verification_experiment + run_verified_repair with pre-flight gates)
- `backend/simulation/expert_rules.py` (new: seed loader + unique-name-only resolver)
- `backend/simulation/golden_rules/omnipro_wire_feed.json` (new: hand-checked OMNIPRO 220 wire-feed rules, verbatim manual quotes p10/p42)
- `backend/api/routes/simulation.py` (compile route merges the expert seed for the matching product; flags it in twin warnings + metadata)
- `backend/api/routes/diagnostics.py` (POST /diagnose/{session_id}/verify-repair; ACTIVE twin only; unavailable returns HTTP 200 with available:false; verify_repair timeline entry)
- `frontend/src/api/client.js` (verifyRepair)
- `frontend/src/api/diagnostics.js` (verifyRepair re-export)
- `frontend/src/components/DiagnosticWorkspace.jsx` ("Verify this repair in the twin" button + verdict panel on conclusion steps)
- `tests/test_verified_repair.py` (new, 16 tests, no LLM)

No database migration is needed. Existing databases do not require new columns. Restart FastAPI after replacing files. Live check: run the guarded product-row SQL fix first (the seed matches on Harbor Freight / OMNIPRO 220), then a fresh OMNIPRO upload/job (not Retry), integrate, compile, approve the twin, and verify a concluded repair from the Diagnostic workspace.

# Narrow adjudication veto + visual twin (v9, added 6 Nov 2026)

Files changed/added for this upgrade (replace on top of the v8 tree):
- `backend/knowledge/code_first_integration.py` (narrow veto: `_hard_identity_conflict` on part_number / location_description / voltage only; merge resolution by id + source rows with a skip on unresolvable candidates; `canonical` list carried in the state dict)
- `backend/simulation/visual.py` (new: deterministic layered layout of a compiled twin -- nodes, edges from documented topology, transitions, canvas)
- `backend/api/routes/simulation.py` (GET /simulation/twins/{twin_id}/visual)
- `backend/api/routes/diagnostics.py` (verify-repair response now returns baseline_state / fault_state / final_state for playback)
- `frontend/src/api/client.js` (getTwinVisual)
- `frontend/src/components/VisualTwin.jsx` (new: SVG visual twin with trace playback)
- `frontend/src/App.jsx` ("Visual Twin" tab + crater-open-visual-twin event)
- `frontend/src/components/DiagnosticWorkspace.jsx` ("Open visual twin" button in the verification panel)
- `tests/test_integration_code_first.py` (3 new veto/collision tests)
- `tests/test_visual_twin.py` (new, 6 tests, no LLM)

No database migration is needed. Existing databases do not require new columns. Restart FastAPI after replacing files. Live check: run the guarded product-row SQL fix first (fresh DB -- guard SELECT, then UPDATE that row's actual values), then a fresh OMNIPRO upload/job (not Retry), integrate, compile, approve the twin; expect one Feed Tensioner, 4 seed transitions, p42 verification VALIDATED, and the Visual Twin tab to play the fault and repair.
# Hypothesis tournament (v10, added 9 Nov 2026)

Files changed/added for this upgrade (replace on top of the v9 tree):
- `backend/simulation/tournament.py` (new: verify_single_cause, run_tournament, rank_tournament_rows, tournament_status -- the per-cause verify-repair machinery, unchanged)
- `backend/api/routes/diagnostics.py` (verify_repair refactored to call verify_single_cause, same response and timeline entries; new POST /diagnose/{session_id}/verify-hypotheses)
- `frontend/src/api/client.js` (verifyHypotheses)
- `frontend/src/api/diagnostics.js` (verifyHypotheses re-export)
- `frontend/src/components/DiagnosticWorkspace.jsx` ("Run hypothesis tournament" button beside the verify button; HYPOTHESIS TOURNAMENT card with ranked rows, evidence, per-row visual-twin playback)
- `tests/test_hypothesis_tournament.py` (new, 14 tests, no LLM)

No database migration is needed. Existing databases do not require new columns. Restart FastAPI after replacing files. Live check: on the fresh v9 run, diagnose the p42 symptom, run the tournament, expect Feed Tensioner VALIDATED and the unevaluable causes unavailable with reasons.

# States-schema fix (v10.1, added 9 Nov 2026)

Files changed/added for this upgrade (replace on top of the v10 tree):
- `backend/knowledge/machine_model.py` (new EntityState: name + value as str/bool/int/float/None, bare strings still accepted; MachineEntity.states is now list[EntityState]; MachineState.value widened to str | bool | int | float | None)
- `backend/knowledge/schema.py` (UniversalEntity.states is now list[EntityState])
- `backend/knowledge/component_extraction.py` (persist dumps entity states to plain dicts for the JSON states column)
- `backend/knowledge/validation.py` (rule veto's known-fields now registers entity-embedded states under their enclosing entity's id)
- `backend/simulation/compiler.py` (docstring only: MachineState.value may be a boolean or number; coercion already no-ops typed values)
- `tests/test_states_schema.py` (new, 8 tests, no LLM -- raw LLM-shaped dicts through the real schema)

No database migration is needed. Existing databases do not require new columns. Restart FastAPI after replacing files. Live check: deploy v10.1, then resume/retry the failed ingestion job (failed chunks are recorded as chunk_extraction_failure and are not knowledge_done, so a resume re-extracts them) -- or a fresh upload if the job completed. Expect zero "LLM extraction schema validation failed" errors on states.

# Frontend rebuild (v11, added 9 Nov 2026)

Files changed/added for this upgrade (replace on top of the v10.1 tree). Frontend only -- no backend changes, no new dependencies:

- `frontend/src/components/Machines.jsx` (new: Machines home -- machine list + onboarding, per-revision ingestion pipeline Upload → Extracting → Integrate → Ready, entity/relation/conflict counts, state-following primary action Add manual / Retry ingestion / Build machine knowledge / Compile twin / Open twin)
- `frontend/src/components/TwinWorkspace.jsx` (new: one Twin workspace -- Overview / Simulate / Visual / Rules sub-views, machine-context header with Compile/Approve as the single primary action, guided auto-compile → Visual flow, expert-seed + warnings + safety disclaimer on Overview, Rules sub-view with conditions/effects/evidence)
- `frontend/src/App.jsx` (rewritten: 4 destinations Machines / Diagnose / Machine Model / Twin, machine context bar on every view, crater-open-simulation/visual-twin events now route into the Twin workspace, no sidebar UUID inputs)
- `frontend/src/App.css` (new v11 styles: context bar, view headers, machine cards + pipeline, twin workspace/subnav/stats, explorer/procedure panels, expert overlay, adaptive breakpoints at 1200/900/768px; all existing styles kept)
- `frontend/src/api/client.js` (new `approveTwin` client call to the existing POST /simulation/twins/{id}/approve route)
- `frontend/src/components/SimulationAgent.jsx` (no UUID input or Load/Compile buttons -- the workspace owns context and Compile; auto-loads on revision/refresh; new batch-comparison panel using the existing runSimulationHypotheses endpoint; experiment UI unchanged)
- `frontend/src/components/VisualTwin.jsx` (no UUID input or Load twin button -- auto-loads from context; SVG gains a viewBox so it scales to the container)
- `frontend/src/components/DigitalTwin.jsx` (no UUID inputs -- twin picked from a dropdown of the revision's twins, demo creation uses context ids; live state + commands unchanged)
- `frontend/src/components/MachineModelViewer.jsx` (controlled by the app's machine context; new "Component explorer" section with ComponentExplorer + DiagramViewer; procedure viewer panel opened by clicking a procedure; all existing sections unchanged)
- `frontend/src/components/DiagnosticWorkspace.jsx` (accepts the machine context as initial selection; "Add expert note" secondary action in setup + session header, opening ExpertKnowledge as an overlay)
- `frontend/src/components/ExpertKnowledge.jsx` (accepts machine context props, shows the machine name instead of Product/Revision ID inputs; interview/review flow unchanged)

No database migration is needed. Existing databases do not require new columns. Restart FastAPI after replacing files and rebuild the frontend (`npm ci && npm run build`). Live check: deploy v11, open Machines, pick the OMNIPRO machine, confirm the guided Compile twin → Visual flow and that no UUID input appears anywhere; diagnose p42 and confirm verify + tournament + visual playback still work from the diagnosis conclusion.

# Machine selection fix (v11.1, added 9 Nov 2026)

- `frontend/src/components/Machines.jsx` (card buttons now pass { productId, revisionId } as App.jsx expects; clicking a card selects it; selected card shows a "Selected" pill)
- `frontend/src/App.jsx` (context bar on the Machines view explains how to select instead of saying "No machine selected"; selectedRevisionId passed to Machines)
- `frontend/src/App.css` (.machine-card.selected highlight)

No database migration is needed. Restart FastAPI after replacing files and rebuild the frontend (`npm ci && npm run build`). Live check: deploy v11.1, click the OMNIPRO card — it selects (highlight + context bar); "Open twin" opens the twin instead of asking to pick a machine.

# Diagnosis robustness fix (v12, added 9 Nov 2026)

Backend only -- no frontend change, no new dependencies, no database migration:

- `backend/llm/provider.py` (new `complete_result` returning (text, truncated); with a JSON schema Gemini sets response_mime_type="application/json" + response_schema and Claude is forced into a tool call whose input IS the JSON object; temperature only when passed; plain `complete()` behaviour unchanged)
- `backend/llm/gateway.py` (new `complete_result` passthrough; max_tokens defaults to the task route ceiling when the caller passes none)
- `backend/config.py` (diagnosis route + task ceiling 2500 -> 8192 tokens)
- `backend/diagnostics/agent.py` (structured-output diagnosis call at temperature 0.2 with no per-call cap; lenient JSON parse + one repair call; per-hypothesis quarantine with percent-confidence rescale; malformed next_step defaults to a question; unparseable output or provider failure degrades to a question step with hypotheses intact and the session staying ACTIVE -- a parse failure never escalates; prompt slimmed -- evidence ids only in state_json, relevance-filtered component/failure-mode block capped at 25/12, canonical payloads stripped of evidence and capped; retrieval runs once per turn instead of twice)
- `backend/diagnostics/prompts.py` (explicit no-fences / never-truncate instruction)
- `tests/test_diagnosis_robustness.py` (new: 24 tests -- provider JSON mode + truncation flags, lenient parse of fenced/preamble/trailing-comma/single-quote/unquoted outputs, repair after truncation, degrade-to-question, percent confidences, prompt slimming, single retrieval per turn, run_turn surviving a bad turn)

No database migration is needed. Restart FastAPI after replacing files. Live check: deploy v12, diagnose "Wire Feed Motor Runs but Wire Does Not Feed Properly" and answer the follow-up questions for several turns -- the session must stay active through every turn and reach a grounded conclusion instead of escalating on an unparseable response; then verify + tournament as before.
