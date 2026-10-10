# Phase 9 additions: outcome feedback, active tests, guided checks

## 1. Repair outcome feedback
- `POST /diagnose/{session_id}/outcome` stores `fixed`, `not_fixed`, or `inconclusive` plus confirmed cause, repair, and notes.
- Outcome is unique per diagnostic session; duplicate submissions return HTTP 409.
- If `submit_correction_for_review=true`, the submitted correction becomes a **draft** `KnowledgeVersion` + `ExpertKnowledge` item and a transcript under the existing Phase 5 expert-review system. It is not automatically approved or used by diagnosis.
- `GET /diagnose/{session_id}/outcome` reads the saved outcome.

## 2. Most informative next test
- `GET /diagnose/{session_id}/next-test` ranks stored `FailureMode.diagnostic_test` records against the current competing hypotheses.
- This is an explainable string-overlap heuristic, not true probabilistic information gain. It only works when structured failure modes/tests have been extracted and their cause names align with hypothesis labels. It deliberately does not invent tests.

## 3. Guided physical checks
- `GET /diagnose/{session_id}/guided-checks` returns stored diagnostic tests, expected observations, provenance chunk IDs, and possible schematic labels from the same revision.
- Schematic label matches are candidates, not verified pin mappings. It does not infer a pin number, voltage, or pass threshold.
- The UI includes a safety reminder to follow manufacturer/site isolation procedures.

## Limitations
- Outcome feedback is persisted and can enter review, but confidence calibration is not yet retrained automatically from outcomes.
- Next-test ranking is not a formal information-theoretic optimizer.
- Guided checks need richer structured terminal/pin evidence to safely produce exact instructions like “X12 pin 3, expect 24 V”; the current endpoint refuses to invent those details.
- The existing SQLite database will create the new table on normal `Base.metadata.create_all` startup. Deployments using external migration management should add an equivalent migration.
