# Ingestion fixes — Manuals/schematics → ingestion + OCR → structured machine knowledge model

Patched copy of project-2.zip (Oct 29 code). Only the files below changed.

## What was fixed, gap by gap

1. **Schematic reuse / provenance** — `backend/ingestion/pdf_loader.py`
   - Image dedup is now per-page, not per-document. Same xref on a later page
     reuses the saved file but still gets a diagram chunk on that page.
   - Pipeline caches vision results by image_path, so the reused schematic is
     analysed once (no extra LLM cost) but persisted per page.

2. **Two-column reading order** — `pdf_loader.py`
   - `_column_aware_page_text` uses the block dict, clusters non-full-width
     blocks into columns, emits full-width headings as separators, then
     left column top-to-bottom, then right column. Falls back to sorted text
     when the layout is not two-column.

3. **Page classification** — `pdf_loader.py`
   - `classify_page` labels cover / toc / parts_list / specifications /
     troubleshooting / maintenance / safety / procedure / diagram / other.
   - Annotation only: it is stored on RawPage and on chunks, it never drops a page.

4. **Chunk overlap across flush** — `backend/ingestion/chunking.py`
   - `flush()` now keeps a sentence-boundary tail (up to chunk_overlap_chars)
     instead of clearing the buffer. A component introduced at the end of
     chunk N is still visible at the start of chunk N+1.

5. **Tables detached from section** — `chunking.py`
   - Table chunks now carry section + page_kind in extra, and their content
     is prefixed `[Section: X | Page kind: Y]`. `_page_section` falls back to
     a short first line, because real manual headings are often Title Case,
     not the ALL-CAPS pattern the old heading regex requires.
   - Diagram chunks carry the same section / page_kind context.

6. **Placeholder diagram vectors** — `backend/ingestion/pipeline.py`
   - Diagram chunks are no longer upserted to Qdrant with `[Diagram on page X]`.
   - After schematic extraction, each diagram with nodes is upserted as a real
     summary: `Schematic diagram on page N: nodes: …; connections: …`.

7. **Cross-chunk relationships dropped** — `component_extraction.py`, `pipeline.py`, `pending_links.py` (new)
   - `persist_extraction` no longer skips a relationship whose endpoints were
     not extracted in the same chunk. It queues it as an open
     `pending_relationship` UnresolvedFact.
   - After ALL chunks are extracted, `resolve_pending_relationships` resolves
     it against the revision-wide component index (by normalised name / part
     number) only when the match is unique. Ambiguous or missing endpoints
     stay open, never guessed.

8. **Cross-page schematic edges dropped** — `schematic/graph.py`, `pending_links.py`
   - Same pattern for schematic edges: queued as `pending_schematic_edge`,
     resolved after all diagrams for the document against the document-wide
     node label index, unique match only.

9. **Failure modes never extracted** — `knowledge/schema.py`, `component_extraction.py`
   - `MachineKnowledgeExtractionResult` now has `failure_modes` with symptom /
     possible_causes / diagnostic_test / expected_observation / repair procedure.
   - Single and batch prompts ask for them explicitly, only when stated.
   - `_universal_model_from_result` carries them into the universal model, and
     `persist_machine_knowledge` also writes legacy `FailureMode` rows so the
     diagnostic agent (which queries that table) can actually use them.

10. **Per-fact quarantine** — `knowledge/validation.py`, `pipeline.py`
    - New `quarantine_machine_model`: keeps every usable fact, quarantines
      only duplicate ids, relations with missing endpoints, entity attributes
      with missing entities, and conflicting state values. Invalid bboxes are
      stripped, the entity kept.
    - Deliberately NOT quarantined: duplicate names with different ids,
      low confidence, and behaviors/procedures/failure modes whose subject
      is not yet known (the fact is kept, the missing link is noted).
    - Pipeline chunk and schematic loops now use this instead of dropping the
      whole chunk model when it has ANY issue.

## Deliberately NOT done, per your instruction

- No deterministic entity register. You are right: it cannot account for all
  facts and names. The replacement is fact-centric: facts are kept
  independent of the chunk that produced them, pending links are resolved
  where they can be uniquely matched, and everything else stays open in the
  unresolved ledger instead of being forced into a register or dropped.
- No OCR engine change, no embedding change, no new LLM passes. The gaps
  above were structural, not model quality.

## Changed files

- backend/ingestion/pdf_loader.py
- backend/ingestion/chunking.py
- backend/ingestion/pipeline.py
- backend/ingestion/pending_links.py (new)
- backend/knowledge/schema.py
- backend/knowledge/component_extraction.py
- backend/knowledge/validation.py
- backend/schematic/graph.py

## Tests run here (no LLM / Qdrant in this environment)

- `python -m compileall backend` — passes.
- Per-fact quarantine unit test: 20 good entities + 1 bad relation + 2 fans
  with the same name → 22 entities kept, 1 relation kept, 1 quarantined.
  Duplicate id → second occurrence quarantined, first kept.
- Column-order test with a fake two-column page: left column fully before
  right column, heading first — passes.
- Page classification test: cover / toc / parts_list / specifications /
  diagram — passes.
- Chunk overlap test: a chunk spanning pages 1–2 contains the fan
  introduction from the end of page 1 — passes.
- Pending-link test (in-memory SQLite): unique Motor M1 → Relay K17
  resolves, ambiguous Fan → Motor M1 stays open, not guessed — passes.
- Failure-mode schema test: symptom → cause → test → repair round-trips
  into the universal model — passes.

## Not yet verified (needs your setup)

- End-to-end ingestion of the OMNIPRO 220 manual with LLM + Qdrant, and
  before/after counts: entities, relations, failure modes, unresolved open,
  pending resolved vs still open, and diagram summaries retrieved.
- Existing pytest suite (needs the project's full dependencies).

## Suggested test on the OMNIPRO manual

Ingest it, then compare: (a) parts-list entities survived, (b) cross-page
relations and schematic edges resolved vs still open, (c) failure modes
from the troubleshooting section appear in FailureMode rows, (d) no
`[Diagram on page X]` placeholder points remain in Qdrant.

## Chunk-size fix — PLC manual (M800V/M80V, 852 pages) — 31 Oct 2026

Symptom: 2662 chunks (reproduced here as 2411 with the pre-fix v3 code:
1729 table + 672 text + 10 diagram).

Root causes, both in backend/ingestion/chunking.py:
1. Overlap + section flush interaction. v3 kept a 1000-char overlap tail
   after every flush, but the section-change flush fired at chunk_min_chars
   (2500). The next chunk therefore hit 2500 after only ~1500 new chars,
   inflating text chunks 469 (v2, no overlap) -> 672 (v3 with overlap).
   Many text chunks were exactly 2500-2502 chars, never reaching the
   12000-char target.
2. Weak headings. The old _HEADING regex treated any all-caps/digit line
   as a heading, so signal codes (R10685, X18C8), document numbers
   (IB-1501804) and bit headers became section boundaries, firing
   section flushes constantly in a signal manual.
3. One chunk per pdfplumber table. A logical signal table is split by
   pdfplumber into many fragments. Table chunks: 1729, mean 651 chars,
   median 335, 1236 under 500 chars — the "extremely small pieces".

Fixes in chunking.py only:
- Strong headings only: a heading must be a numbered heading with words,
  a short ":" line with letters, or an ALL-CAPS heading with at least
  two alphabetic words (no digits in the words). Single-token codes and
  bit headers are now plain text.
- Section-change flush now fires at chunk_size_chars (12000, the target),
  not chunk_min_chars (2500). All sections in a chunk are preserved in
  extra["sections"]; extra["section"] is still the first one.
- Tables are grouped per page into chunks up to chunk_max_chars, with
  tables_count / table_indices in extra, instead of one chunk per table.
  No table content is dropped; fragments on the same page are joined.

Verified (text-only, same manual): text chunks 672 -> 143, mean
3521 -> 13257 chars, median 2938 -> 13277, minimum 1115 -> 3268, none
under 2500. OMNIPRO manual (48 pages) text-only: 8 chunks, mean 11611,
so normal manuals are not broken by the threshold change.

Verified (full, with tables + diagrams, same manual):
- Before fix (v3 as shipped): 2411 chunks = 1729 table (mean 651, median
  335, 1236 under 500 chars) + 672 text (mean 3521) + 10 diagram.
  User reported 2662 in their run; the difference is likely run-to-run
  counting (e.g. re-ingestion / diagram summaries), but the pattern is
  the same: thousands of tiny table fragments + ~2500-char text chunks.
- After fix: 949 chunks = 796 table (mean 1348, median 865, 201 under
  500 chars) + 143 text (mean 13257, median 13277, none under 2500)
  + 10 diagram (pipeline replaces these with real summaries, it does
  not vector-index the placeholder).
- A NameError in the diagram loop (leftover _sec/_kind after the table
  grouping refactor) was caught by the full run and fixed before shipping;
  it would have broken any PDF with embedded diagrams.

## Gap 6/7/8 + OMNIPRO DB fixes — 31 Oct 2026 (second v3 update)

Diagnosed from ~/workspace/user/files/crater.db (OMNIPRO 220, ingested 31 Oct):
59 chunks, only 4 produced any evidence, 0 relations / 0 procedures / 0 ports,
34 diagram chunks with 0 schematic nodes, snapshot v1 32 entities -> v2 36
with only 25 unique names, completeness claimed 76/76 while 14 unresolved
rows were open. The fixes below address those exact rows, not just the
abstract gaps.

### 6. Validation / persistence (integration level)
- `backend/knowledge/global_integration.py`: after the LLM model is built,
  `quarantine_machine_model` is applied at integration level too, and the
  kept model is what is snapshotted. Invalid bbox / missing-entity relations
  become quarantined ledger records, not a job abort.
- `backend/ingestion/pipeline.py` and `backend/api/routes/machine_knowledge.py`:
  verification errors no longer raise ValueError / 422. They are recorded
  as warnings and the kept model is still persisted. A job only fails if
  integration returns no model and no unresolved facts at all.
- Chunk-level quarantine (previous v3) is unchanged.

### 7. Schematics
- `backend/schematic/vision_extraction.py`: max_tokens 2000 -> 8000; large
  images (>1600px side) are tiled 2x2 with 10% overlap and tile bboxes are
  remapped to full-image coordinates; unparseable output gets one JSON-repair
  retry, and if it still fails the result carries `parse_error` instead of
  an empty result that looks like "no nodes".
- `backend/schematic/schema.py`: nodes now carry `part_number` (only when
  printed on the diagram) and `ports` (real pin labels + bboxes); edges
  carry `from_port` / `to_port`.
- `backend/schematic/graph.py`: `schematic_to_machine_model` no longer
  creates one synthetic "terminal" port per node. It creates only the real
  pins returned by vision; a node with no visible pins gets no port, and
  that absence is visible instead of hidden. Entities carry link fields
  (`link_label`, `link_normalized_label`, `link_part_number`).
- `backend/schematic/graph.py` `persist_schematic`: component linking is
  by normalized label, or by a part_number that is unique among the
  revision's components. No fuzzy link is guessed.
- `backend/ingestion/pipeline.py`: a diagram with `parse_error` gets a
  `schematic_extraction_failure` unresolved fact; a diagram with no nodes
  gets a `schematic_extraction_empty` fact. Neither is silent any more.

### 8. Global integration (code first, LLM last; no deterministic register)
- `backend/knowledge/global_integration.py`: for <=250 source rows
  (OMNIPRO had 76) integration is ONE call with entities, relations, facts,
  procedures, failure modes and schematics together. Larger manuals fall
  back to category batches, but non-entity batches now see the FULL entity
  list, not the first 150.
- `_merge_rows_by_identity` now also merges name-level duplicates, but
  only when properties do not conflict: Mig/Tig/Stick Welder emitted twice
  with identical/subset properties merges (source_ids unioned); two Fans
  with different part numbers do NOT merge. Failure modes merge on same
  name with non-conflicting causes. Relations merge on identical
  subject/object/type. Dropped entity ids are mapped to the unique kept
  row with the same name, and relations/ports/quantities are rewritten
  to the kept id before verification, so merging cannot create a
  missing-entity relation.
- `_snapshot_fingerprint` + `persist_integration_result`: re-running
  integration on unchanged source data returns the existing snapshot
  version instead of creating a new, larger one (OMNIPRO v1->v2 inflation).
- Coverage audit: `integrate_revision_knowledge` now also stores
  `coverage` (total chunks, chunks with/without facts, diagrams
  with/without nodes, unresolved open count) in the snapshot payload and
  in `source_counts` as `coverage_*`. Completeness still counts source
  records; coverage is what stops a thin model claiming to be complete.

### DB-ledger fixes (from the OMNIPRO rows)
- `backend/knowledge/normalization.py`: new `resolve_unresolved_fact`.
- `backend/ingestion/pipeline.py`: on batch failure, only the chunk named
  in the error ("Chunk <id> extraction failed") is blamed, not every chunk
  in the batch. On a chunk's later success, its open
  `chunk_extraction_failure` rows are marked resolved.
- `backend/ingestion/pipeline.py`: a chunk whose LLM returns no legacy
  and no universal facts gets a `chunk_extraction_empty` unresolved fact
  instead of vanishing silently.
- `backend/ingestion/pending_links.py`: pending links now have a
  unique-only fallback: strip generic words (mechanism, assembly, relay,
  motor, ...) and allow a unique prefix match. "Wire Feed mechanism" ->
  "Wire Feed Motor" and "Relay K17" -> "K17" resolve only when exactly
  one candidate matches; ambiguous cases stay open.

### Not fixed by code (do this on the existing DB)
- Product identity: the OMNIPRO DB has product mi / m80m80 / m80, but the
  manual is Harbor Freight OMNIPRO 220 (item 57812). Fix that product row
  in the DB, e.g.:
  `UPDATE products SET manufacturer='Harbor Freight', family='OMNIPRO', model='220' WHERE manufacturer='mi' AND family='m80m80' AND model='m80';`
  Do NOT re-ingest to fix this; it is a data-entry correction, and
  re-ingesting under the wrong product would merge it with a real M80.
- `part_number` on legacy components in that DB is the parts-list item
  index / quantity (e.g. Fan = "1", IGBT Radiator 1 = "50"), not a
  manufacturer part number. Treat it as an item index when linking; the
  new schematic part-number link requires a unique match for this reason.

### Tests added
- `tests/test_ingestion_fixes_v4.py` (9 tests, all passing here):
  name-level merge, two-Fans-not-merged, failure-mode merge, integration
  quarantine, real-ports-only schematic model, snapshot fingerprint,
  pending-link unique-prefix resolution, stale-unresolved clearing,
  vision parse-error carrying.
- Existing `test_global_integration.py`, `test_schematic_graph.py`,
  `test_normalization_ledger_pipeline.py` still pass (21 tests total
  with the new file).

### Still not verified here
- No LLM/Qdrant end-to-end ingestion was run in this environment.
  Re-ingest the OMNIPRO manual with this zip and check: chunks with facts
  should be far more than 4, diagram nodes > 0, snapshot entity count
  should not grow on a second integration run, and unresolved rows should
  name the actual failing chunk, not the whole batch.

## Malformed-JSON extraction fix — 31 Oct 2026

Error reported: `Chunk 1b40dd37-... extraction failed: LLM returned invalid JSON:
Expecting property name enclosed in double quotes`.

What that error means: Python's JSON parser found a property that is not a
double-quoted string. In LLM output that is almost always a trailing comma
(`{"a": 1,}`), single-quoted keys/strings (`{'a': 1}`), an unquoted key
(`{a: 1}`), or a comment inside the JSON. The old parser only stripped code
fences and brace-hunted, then gave up, and the pipeline then failed the
whole ingestion job for that one chunk.

Fix, in `backend/knowledge/component_extraction.py` only:
- New `_parse_lenient_json`, used by both single-chunk and batch extraction:
  strict JSON, then trailing-comma repair, then `ast.literal_eval` (single
  quotes + trailing commas, with true/false/null converted only outside
  quoted strings), then regex repair for unquoted keys and comments.
- If deterministic repair still fails, ONE LLM repair call: "return only
  the corrected valid JSON, without adding, removing, or inventing facts".
  Only if that also fails does extraction raise for that chunk.
- Single-chunk output cap 4000 -> 8000 tokens, batch floor 4000 -> 8000,
  so a long chunk is less likely to be cut off mid-object in the first place.
- No change to job-failure semantics in this patch: a chunk that is still
  unrecoverable after repair still fails the job, and the job retry endpoint
  resumes from the checkpoint (only the failed chunk and later chunks are
  reprocessed, not the whole manual). That is deliberate: silently skipping
  a chunk would hide exactly the loss this project is trying to stop.

Tests added to `tests/test_ingestion_fixes_v4.py`: trailing comma,
single quotes, unquoted keys, comments, code fences, leading prose, and a
mocked LLM repair retry. All 11 tests in that file pass here.

To apply: replace the files in the zip, restart FastAPI, then use Retry on
the failed job (do not re-upload). The new parser will be used for the
failed chunk on retry. If it still fails after this patch, send the
unresolved fact's `content_excerpt` for chunk 1b40dd37-... from the DB —
that excerpt is the chunk content, and the exact malformed output can then
be reproduced without guessing.

## Code-first + adjudication global integration redesign — 31 Oct 2026

Replaces the old LLM re-emission integration as the default. The old path
is kept as `_integrate_revision_knowledge_llm` in
`backend/knowledge/global_integration.py` and runs only when
`integrate_revision_knowledge(..., method="llm")` is passed explicitly.

Why: on the OMNIPRO manual the old path took ~10 minutes at 93%
("Building canonical machine knowledge") for 166 source rows: one LLM
call re-emitting the whole model at up to 16,000 output tokens, with
truncation/split risk, a fingerprint check only AFTER the LLM call (so
the frontend's second /integrate call paid full cost again), and no
per-batch progress or checkpoint. The new path is O(rows) in code.

New module `backend/knowledge/code_first_integration.py`:
- No deterministic register: grouping is derived from the rows observed
  in this revision only. A new name automatically becomes a new entity.
- Entities: universal entities + legacy components + schematic nodes are
  combined. Grouping key is (normalised name, entity_type). A row merges
  into a subgroup only when there is exactly one non-conflicting
  candidate. Merge vetoes: conflicting properties (function, location,
  voltage/current, size, material, parent), different non-empty
  part_numbers, and same-source-list items with different item numbers.
  Part-number rule: one side empty, one side specific merges ONLY when
  both share a known source chunk (same extraction record in universal
  + legacy tables); a prose "Fan" with no number never merges into
  Fan 44 / Fan 53 from another chunk. More than one candidate -> the row
  stays its own subgroup, never the first match.
- Stable canonical ids: `entity:<slug>` for a single subgroup, else
  `entity:<slug>-<part_number>`, `entity:<slug>-<location>`, or
  `entity:<slug>-<n>`. Conflicts are recorded explicitly as
  `conflict:<slug>-<property>` with values and source_ids, not hidden.
- Failure modes deduplicate by normalised name (legacy "Name: symptom"
  prefix stripped), causes unioned, diagnostic test filled from a row
  that has one. Procedures dedupe by (name, type) with subset steps only.
- Relations (universal, legacy, schematic edges) resolve in code only
  when the endpoint name/id maps to exactly one canonical entity;
  otherwise they stay unresolved with reason
  `relation_endpoint_not_uniquely_resolvable` /
  `schematic_edge_endpoint_not_uniquely_resolvable`, never guessed.
- Ports/quantities/states/events/constraints attach only when their
  entity resolves uniquely. Behaviours keep a canonical subject or an
  `unresolved:behavior-subject:<slug>` subject. Unknown fact types and
  expert-knowledge rows are recorded unresolved, not dropped.
- Completeness is computed by construction: every source row is either
  canonicalised or in unresolved_facts. An unattachable fact (e.g. a
  quantity with no entity) is unresolved and counts as missing.
- Fingerprint: `source_fingerprint(context)` is SHA-256 over all context
  rows, sorted. The new `integrate_revision_knowledge` checks the latest
  snapshot's stored `source_fingerprint` BEFORE any code build or LLM
  call, so a second /integrate on unchanged data returns the existing
  snapshot and `persist_integration_result` reports `unchanged: True`.
- Adjudication only: clusters are (a) conflicting same-name groups,
  (b) relation endpoints with multiple candidates, (c) synonym
  candidates (token subset, shared first token, or function overlap;
  never both sides with a non-empty part_number). At most 50 clusters,
  one `gateway.complete("integration", ..., max_tokens=2000)` call, and
  the LLM returns decisions only
  `{"decisions":[{"cluster_id","action":"merge|keep_separate|link",...}]}`.
  Any gateway exception, empty/unparseable response, or non-list
  decisions -> no decisions, code model stands. `apply_adjudication`
  re-applies the code vetoes: a merge of same-list distinct items or
  conflicting properties is rejected even if the LLM proposed it.

Context fix in the new path: lineage rows are concatenated, NOT passed
through `_merge_contexts` shadowing. That function keys universal
entities by (name, type) and let "pump p1" shadow "Pump P1", dropping a
source row before grouping could union its source_ids. The old LLM path
still uses `_merge_contexts` unchanged.

Legacy->universal loss fix, `backend/knowledge/component_extraction.py`:
`_universal_model_from_result` now merges legacy components, relations,
behaviours and procedures that universal extraction did not already
cover (by normalised name / subject-object-type / name). Previously a
chunk with any universal fact (including a failure mode) took the
universal path and its legacy components were dropped: in crater-2.db
pages 39/42/43 produced 7/6/64 legacy components but 0 universal
entities, while page 46 (no failure modes) took the legacy path and its
12 components became entities.

Pipeline progress, `backend/ingestion/pipeline.py`: the pipeline's
`progress` callback is now passed into integration, so the 93% stage
moves (code-first build / adjudication / verify) instead of sitting at
93% until 98%.

Verified here on a COPY of crater-2.db (original untouched, no LLM key,
adjudication stubbed to no decisions):
- 0.23 s first run, 0.06 s second run, for 166 source rows (was ~10 min).
- Snapshot v1: 96 entities, 1 relation (Screen Cover contains
  Screen Frame), 6 procedures, 17 failure modes (23 -> 17 deduped),
  18 conflicts recorded, 3 unresolved (all `invalid_bbox_stripped`
  schematic pixel bboxes — bbox normalisation is still a later step).
- Fans kept separate: `entity:fan-1`, `entity:fan-44`, `entity:fan-53`.
- Completeness 166/166, missing 0. Second run: no new snapshot version
  (`unchanged: True`).
- Coverage in snapshot: 59 chunks, 34 diagram, 25 text/table,
  8 chunks with facts, 51 empty, 4 diagrams with nodes, 30 empty.
  Extraction recall (21 empty text/table chunks) is NOT fixed by this
  redesign and is the next step in the crater-2 plan.

Tests: new `tests/test_integration_code_first.py` (7 tests: exact-duplicate
merge, conflicting parts stay separate, prose Fan, unique/ambiguous
relations, failure-mode dedupe, adjudication veto, fingerprint pre-check,
legacy survival with failure modes). Two existing tests updated for the
new contract: `test_global_integration_resolves_duplicate_entities`
(exact duplicates, code merge, id `entity:pump-p1`) and
`test_revision_inheritance_is_effective` (slug id, lineage by ids).
`test_completeness_retains_unaccounted_fact` now passes with code-first
completeness (it failed on the old path, which claimed complete=True
for an unattachable quantity fact).

To apply: replace the files listed in REPLACE_FILES.md for this
redesign, restart FastAPI, then use Retry on the crater-2 job (do NOT
re-upload). Watch 93% complete in seconds, confirm a snapshot exists,
and confirm a second /integrate creates no new snapshot version.

## Multi-chunk batch extraction fix — 5 Nov 2026

Diagnosed from the gold-set test (spec table p7, troubleshooting table
p42, procedure text p10, run on the crater-3 DB): single extraction
gave p7 8 items and p42 6 components + 5 failure modes, while a
small batch of the same 3 chunks at the same 8000-token cap gave
literally empty lists for all three (1328 chars, not truncated), and
full-batch batch 0 (12 text chunks) was also all empty. Full-batch
batch 1 was rich but schema-drifted (components as strings, procedures
with procedure_name and no procedure_type, quantities with parameter
instead of name). The batch prompt was therefore not a slower version
of the single prompt; it was a different, weaker contract.

Fixes, in backend/knowledge/component_extraction.py unless noted:
- Batch prompt now contains the full field shapes from the single
  prompt (components, relationships, procedures, entities, relations,
  ports, quantities, states, events, behaviors, constraints,
  failure_modes), with the procedure_type and relation_type enums, the
  failure-mode push, and explicit rules for the shapes previous runs
  got wrong: components are objects, never bare strings; procedures
  always have name + procedure_type; quantities have name/value/unit/
  entity_id, never parameter in place of name; failure_modes evidence
  is a list, never a single object. Spec tables get one quantity per
  numeric row instead of burying values in entity properties. Tables
  and mid-procedure chunks are named as extractable, not as empty.
- Single prompt gained the same evidence-shape clarification
  (components/relationships: single object; universal items including
  failure_modes: list), because p10's single output copied the
  component evidence shape onto failure modes and lost the chunk.
- backend/knowledge/normalization.py normalize_payload: a dict evidence
  field is wrapped to a list, a string evidence field becomes
  [{"fact": ...}], and string elements inside an evidence list become
  fact objects. This is the p10 fix: it preserves the evidence text
  and invents nothing, in both single and batch paths.
- component_extraction.py _normalize_extraction_item: a bare string in
  a name-only collection (components, entities, failure_modes) becomes
  {"name": ...}; a quantity's "parameter" field is copied to "name"
  (original field kept). No other conversions are done here.
- Dict collections (ports, quantities, states, events, behaviors,
  constraints) are list[dict] at extraction stage, so they were not
  validated until _universal_model_from_result. A truly unvalidatable
  item there (e.g. a quantity whose value is an object, not a
  string) used to raise an uncaught ValidationError that bypassed
  the pipeline's split recovery. That conversion is now wrapped as an
  ExtractionError in both single and batch paths, so a batch splits
  to singles instead of failing the job. Note: a quantity merely
  missing name/entity_id does NOT fail this way -- MachineModelBase
  already fills those with explicit unresolved placeholders
  ("Unresolved item" / "unresolved:entity") and keeps the value, so
  that case was never a job failure and is not changed here.
- backend/ingestion/pipeline.py: after a multi-chunk batch succeeds,
  each chunk whose batch result is empty is retried once with the
  single-chunk extractor, and the single result is kept if it yields
  anything. Bounded: at most one extra call per empty chunk, never for
  non-empty chunks, never when the batch was already a single chunk.
  If the retry fails, the chunk keeps its empty outcome and the
  failure is recorded in its chunk_extraction_empty reason; a retry
  never turns a successful empty batch into a job failure.

Deliberately NOT done, and why:
- No procedure_type, entity_id, or relation endpoint is invented.
  A procedure with no type still fails validation visibly and splits
  to a single, where the single prompt shows the enum; guessing a
  type would be a false fact, not a recovered one.
- No per-fact quarantine at extraction stage. The existing
  split-to-single path already recovers from batch validation
  failures, and a per-fact drop at this stage would need a new ledger
  channel; silently dropping the bad item would be a new loss mode.
- No batch-size or token-cap change. A smaller batch would triple
  call count and time without proof it helps once the prompt is
  fixed; the empty-retry is targeted at exactly the chunks that lose
  facts, and validation failures already split.
- No API-level structured-output change (response_schema / mime type).
  That would help, but it changes the provider contract for every
  task and needs live testing against the user's Gemini model; it is
  a follow-up, not part of this narrow fix.

Tests: new tests/test_batch_extraction_fix.py (9 tests, mocked
gateway, no LLM): dict evidence, string evidence, p10 dict-evidence
chunk survival, batch string components, batch quantity parameter,
quantity-without-name as unresolved placeholder (not a failure),
truly unusable quantity as ExtractionError (not uncaught), batch
prompt shape assertions, and procedure-without-type still failing
visibly. Plus tests/test_pipeline_empty_retry.py (2 tests, mocked
PDF/chunk/vector/gateway, in-memory SQLite, no LLM/Qdrant): an empty
batch chunk is rescued by a single retry and persisted with no
chunk_extraction_empty record, and a chunk whose single retry is
also empty is still recorded as empty and the job completes.

Still not verified here (needs your setup, no LLM key in this
environment): the live gold-set test with the new batch prompt.
Re-run gold_set_test.py (the fixed version, not the pre-fix copy in
project-3.zip) and compare: small batch should no longer be all
empty, and a fresh OMNIPRO ingestion should show chunks_with_facts
well above 2/25. If small batch is still empty with the new prompt,
the next step is batch size / structured output, not another prompt
tweak. CORRECTION (5 Nov 2026, after this section was written): do NOT
use Retry to test this fix. Retry on a completed job re-queues nothing
(`retry_ingestion_job` returns the job as reused), and even a failed-job
Retry skips chunks already in `knowledge_done`, including empty ones --
so the new empty-retry code only fires on a fresh job. Test with a fresh
OMNIPRO upload/job, not Retry on the crater-3 job. Re-run the gold
harness first if you want (it makes no DB writes), then the fresh job.

## Unresolved-entity quantities fix (5 Nov 2026)

Diagnosis (from crater-4.db, the fresh OMNIPRO run with the batch fix):
the p7 spec table extracted 29 quantities and the p29 duty-cycle table
8, but the snapshot had 0 quantities. They were not lost in extraction;
they were quarantined by validation and would have been dropped again
by code-first integration. A spec quantity is usually about a welding
mode or the whole machine ("Mig Power Input", 120 VAC), not about a
named component in the same chunk, so its entity_id is the explicit
placeholder "unresolved:entity". Across crater-4, 55 quantity rows were
quarantined this way (p7 29, p29 8, p5 6, p27 4, p17 2, p6 6); the p6
ones are a unit-abbreviation glossary with empty values, the other 49
are real measurements. The 3 fact-less text/table chunks in that run
were also explained from the same DB: p1 table is a genuinely empty
cover/legal table, p6 is the glossary, p29 is the quarantined duty
cycles.

- backend/knowledge/validation.py: quarantine_machine_model now keeps
  a quantity with a real name and a non-empty value even when its
  entity is not in the chunk's kept entities, with the placeholder
  entity_id left untouched and the subject_name preserved -- the same
  treatment behaviors/procedures/failure_modes already get for an
  unknown subject. A quantity with a specific (non-placeholder) entity
  id not in the kept set is kept the same way and gets a
  missing_link_note, since a revision-wide pass can still resolve it.
  Quantities with an empty value or a placeholder name
  ("Unresolved item") are still quarantined, so the p6 glossary stays
  out. Ports, states and events are unchanged: an unresolvable one is
  still quarantined.
- backend/knowledge/code_first_integration.py: build_code_first_payload
  now applies the same rule, because validation alone would not have
  been enough -- the old code dropped any quantity whose entity did
  not resolve to a canonical entity
  (fact_entity_not_uniquely_resolvable) before it could reach the
  snapshot. A kept quantity goes into payload["quantities"] with
  entity_id "unresolved:entity" (or the original id if present) and
  its subject_name, so completeness counts it as accounted. Ports,
  states, events and constraints keep the old behaviour.

Deliberately NOT done, and why:
- No entity is invented for a quantity. "Mig Power Input" is not
  attached to the Spool Gun, the welder, or any other entity; it stays
  a machine-level spec with an explicit unresolved marker, retrievable
  by name/value in the snapshot and in retrieval. Guessing an entity
  would be a false fact, not a recovered one.
- No change to relations, ports, states or events. Relations were
  also rare in crater-4 (2 in the snapshot), but that is a separate
  extraction gap (few relations are produced at all), not this
  quarantine rule, and it is not fixed here.
- No change to the p6 glossary handling beyond the empty-value rule:
  those entries are not measurements and are still quarantined.

Tests: new tests/test_quantity_unresolved_entity.py (3 tests, mocked
gateway, no LLM): unlinked real quantities kept and glossary /
placeholder-name quantities quarantined (with ports/states/events
still quarantined), code-first payload keeps the p7/p29 quantities
with subject_name preserved and counts them as accounted in
completeness, and a quantity that does resolve to a canonical entity
still gets that entity_id. Relevant suite: 58 passed (the 3 new tests
plus the batch-fix, pipeline-empty-retry, ingestion-fixes-v4,
global-integration, phase8c, code-first, schematic-graph,
extraction-hardening, large-manual and normalization suites). Full
backend compileall passed.

Offline verification on a copy of crater-4.db (no LLM key here, so
adjudication was stubbed to no decisions, as in the earlier offline
runs): the 55 quarantined quantity payloads were run through the new
quarantine as extraction would have done, the 49 kept ones persisted
as machine_knowledge_facts, then code-first integration ran on the
copy. Snapshot quantities 0 -> 49 (p7 29, p29 8, p5 6, p27 4, p17 2;
the 6 p6 glossary entries stayed quarantined), completeness 519/519
with 0 missing, unresolved_count unchanged at 12, and coverage
text/table chunks with facts 22 -> 23 (p29 rescued; p7 was already
counted via Spool Gun and went 2 -> 31 summed facts). One honest
caveat: that offline run's snapshot had 143 entities vs the live v1's
141, because the stubbed adjudication does not merge "Gun" and "LCD
SCREEN" the way the live LLM adjudication did; that difference is the
stub, not this fix, and a live fresh run would adjudicate as before.

Still not verified here (needs your setup, no LLM key in this
environment): a live fresh OMNIPRO ingestion with this fix. Expect
snapshot quantities to go from 0 to roughly 49 on the same manual,
p29 table to show facts, and p7 table to show ~31 summed facts in the
coverage script. As with the batch fix, test with a fresh upload/job,
not Retry on the completed job: Retry re-queues nothing on a completed
job and skips knowledge_done chunks, so it cannot exercise this fix.

## Same-chunk relation disambiguation (5 Nov 2026)

Problem: in the crater-5 live run, snapshot relations were 0 despite 20
source relations (10 universal + 10 legacy, all from the p10/p15 spool
assembly). Every relation had at least one endpoint with two canonical
candidates (Wingnut, Spool Spindle, Spool Knob, Wire Feed Mechanism,
Regulator, Feed Tensioner, MIG Gun duplicated with location suffixes),
so code-first refused to guess and completeness went False (22 missing).

Fix, in code only, no new LLM call:
- `backend/knowledge/global_integration.py`: `build_revision_knowledge_context`
  now passes `source_chunk_id` for universal entities and universal
  relations (legacy rows already carried it; the columns exist on both
  tables). Without this the rule below could not see the chunk.
- `backend/knowledge/code_first_integration.py`: when a relation endpoint
  name has several candidates, keep only the candidates whose source rows
  came from the relation's own source chunk; link only if exactly one
  remains. A relation and its endpoints were extracted from one chunk in
  one extraction output, so the names in that output co-refer to the
  entities in that same output. Zero or two-plus candidates from that
  chunk, or no chunk info, leaves the relation unresolved exactly as
  before. Applied to universal and legacy relations; schematic edges
  unchanged.
- Same file: payload relations are deduped by (subject_id, relation_type,
  object_id), unioning source_ids and evidence, as failure modes and
  procedures already are. A universal row and its legacy copy are one
  logical relation. Also, an adjudication `link` decision that would
  recreate a relation code already resolved now folds its source id into
  the existing relation instead of duplicating it.
- Observability only: `adjudicate_clusters` now logs a warning when the
  call fails or returns nothing usable, and an info line with the
  decision count; `integrate_revision_knowledge` logs clusters sent vs
  decisions returned. The crater-5 run gave no record of whether
  adjudication ran, so a silent [] could not be told apart from a model
  that declined to link. No behaviour change from the logging.

Deliberately not changed: no entity is merged or renamed by this fix;
extraction, validation, chunking and the quantity path are untouched;
relations whose endpoint does not exist at all (0 candidates) still go
unresolved with no cluster, as before; the `entity:welder` canonical-id
collision (equipment-type and component-type Welder subgroups both
getting id `entity:welder`) is a separate entity-id fix and is not in
this build.

Tests: 7 new tests in `tests/test_relation_same_chunk.py` (both-sides
ambiguous resolves by same chunk; one-sided resolves; same chunk with
two candidates stays open with a cluster; no chunk info stays open;
unique resolution unchanged without chunk info; universal+legacy
duplicate dedupes to one relation with both source ids; adjudication
link does not duplicate an already-resolved relation). Relevant suite:
65 passed (the 7 new plus the 58 from the quantity fix). Full backend
compileall passed.

Offline verification on disposable copies of crater-4.db and crater-5.db
(no LLM key here, so adjudication was stubbed to no decisions, as in the
earlier offline runs; the old snapshots were deleted from the copies so
the fingerprint pre-check could not return them):
- crater-5: relations 0 -> 10, each with 2 source ids (universal +
  legacy), all endpoints the correct p10/p15 candidates
  (wingnut-over-the-spool-spindle, spool-spindle-welder-wall,
  wire-feed-mechanism-inside-welder-front, regulator-gas-cylinder,
  feed-tensioner-feed-mechanism, mig-gun-end-of-gun-cable).
  Completeness 475/497 -> 495/497, missing 22 -> 2 (only the CTWD
  constraint with an empty entity_id and the legacy "Welder" component
  row lost to the duplicate entity:welder id collision -- both
  out-of-scope items named above). Entities 109, procedures 20,
  failure modes 37, quantities 74, conflicts 42 all unchanged from the
  live v1; unresolved_facts 35 -> 15.
- crater-4: relations 2 -> 1 (the two were the same Screen Frame
  contains Screen Cover relation twice, universal + legacy; now one
  relation with both source ids). Completeness 470/470, missing 0,
  procedures 35, failure modes 30, quantities 0, conflicts 40,
  unresolved 12 all unchanged. Entities 143 vs the live v1's 141 is
  the known stub artefact, not this fix: the stubbed adjudication does
  not merge "Gun" and "LCD SCREEN" the way the live LLM adjudication
  did (same caveat as the quantity-fix offline run); the only entities
  differing are entity:gun and entity:lcd-screen.

Still not verified here (needs your setup, no LLM key in this
environment): a live fresh OMNIPRO ingestion with this fix. Expect
snapshot relations to go from 0 to 10 on the same manual, with
entities/procedures/failure modes/quantities within normal run-to-run
variance, and completeness missing down to 2 (the CTWD constraint and
the Welder row). As before, test with a fresh upload/job, not Retry on
the completed job: Retry re-queues nothing on a completed job and
skips knowledge_done chunks, so it cannot exercise this fix.

## Relation endpoint recovery (6 Nov 2026)

Crater-6.db showed the next layer under the v6 fix. The v6 same-chunk
rule handles endpoints that exist but are ambiguous; crater-6 had the
opposite problem: extraction produced 11 relations, all of which died
at the chunk level -- 11 validation quarantines
(`relation_endpoint unresolved_entity_reference`) and 11
`pending_relationship` rows (`cross_chunk_component_not_in_chunk`) --
and the snapshot had 0 relations, with 0 source relation rows at all.
Their endpoints (Ground Clamp Cable, Wire Feed Power Cable, TIG Torch
Cable, Electrode Holder Cable, Negative/Positive Sockets, Workpiece,
circuit breaker) were never extracted as entities anywhere in the run,
even though the text names them in 4-5 chunks; only Fan, Welder,
Ground Clamp and Reset Button among the endpoints exist. The LLM names
cables and sockets only inside a "plug X into Y" step and never lists
them as components, and no prompt rule told it that a relation
endpoint must also be an entity. There were four gates dropping the
same fact, so the fix touches all four -- a fix in only one gate still
loses the relation, as the quantity fix already showed.

- Prompt (source): both the single and the batch prompt now carry an
  endpoint-closure rule: every name used as a relationship
  from/to component or a relation subject/object name must also appear
  as a component/entity in the same output. Cables, sockets,
  connectors, torches and holders that a setup step plugs, connects or
  inserts are components and must be listed even when the text only
  names them inside that step; the machine itself and the workpiece
  must be listed as entities too when a step connects something to
  them.
- Validation safety net (`backend/knowledge/validation.py`,
  `quarantine_machine_model`): a relation whose endpoint is missing
  from the chunk's kept entities is now kept, and a minimal stub entity
  is recorded for each missing endpoint. The stub's id and name are
  taken verbatim from the relation, no function, location or property
  is invented, it is marked `endpoint_derived: true` in properties,
  and a `missing_link_note` records that it was derived from the
  relation rather than directly extracted. The stub is per chunk, like
  every other entity here; integration groups it with same-name
  entities from other chunks only under its usual no-conflict rules,
  so a stub for the whole machine stays separate from the parts-list
  Welder when their part numbers differ, exactly as prose Fan stays
  separate from Fan 44. A relation is still quarantined when an
  endpoint has no usable name (including the normalizer's "Unresolved
  subject/object" placeholders) or its id belongs to an entity already
  quarantined as a duplicate. This is not inventing an entity from
  nothing: the endpoint name was explicitly stated, by the extractor,
  as a party to a stated connection, with evidence; the stub adds no
  new name and no property. It is the same principle as the quantity
  fix (keep the fact, keep the link visibly open), applied to the
  other side of the link.
- Legacy persist (`backend/knowledge/component_extraction.py`,
  `persist_extraction`): a legacy relationship whose endpoint is not
  among the chunk's components now records a minimal stub component
  for the missing endpoint (name verbatim, function/location/part
  number empty) and persists the relationship against it, instead of
  queueing it as `pending_relationship` forever. The stub is
  registered in the chunk's name map, so a second relationship in the
  same chunk reuses it. An invalid relation_type still goes to pending
  with reason `invalid_relation_type` (the type check now runs before
  the endpoint check, so that corner is recorded under the more
  accurate reason). The universal model gets the matching
  endpoint-derived entity from validation, and code-first groups the
  two by name and chunk.
- Code-first (`backend/knowledge/code_first_integration.py`,
  `resolve_entity_id`): when the extractor's id slug does not resolve
  (e.g. an invented id such as `entity:p3` for a named endpoint), it
  now falls back to the endpoint name, which is the grounded part of
  the row. This only changes outcomes for relations that currently
  fail to resolve at all; a relation that already resolves is
  untouched.

Deliberately not changed: no entity is merged or renamed by this fix;
no deterministic register; chunking, schematics, quantities, ports,
states and events are untouched; relations whose endpoint has no
usable name still go unresolved, as before; the `entity:welder`
canonical-id collision and the Fan `entity:fan-internal` duplicate-id
suffix (two no-part Fan subgroups both getting the same distinguishing
suffix) are separate entity-id fixes and are not in this build. The
pending-link resolver itself is unchanged: with stub components
persisted, pending rows are simply no longer created for these
relations. Simulation rules are not in this build either: relations
give the machine model the topology the simulation compiler needs as
input, but the compiler still only creates transitions from explicit
`simulation_rules`, which extraction does not yet produce.

Tests: 12 new tests in `tests/test_relation_endpoint_recovery.py`
(both endpoints missing -> relation kept with 2 stubs, nothing
invented, missing_link_notes recorded; one endpoint missing -> one
stub; shared endpoint -> one stub for two relations; placeholder
endpoint id rewritten to the name slug; no usable endpoint name ->
still quarantined; both endpoints present -> unchanged, no stubs;
both prompts contain the closure rule; persist creates stub
components and the relationship with no pending row, stub reused by a
second relationship; invalid relation_type still goes to pending;
code-first falls back to the endpoint name when the id slug fails;
same relation from two chunks dedupes to one with both source ids).
One existing test updated deliberately for the new contract:
`tests/test_ingestion_fixes_v4.py::test_integration_quarantine_keeps_good_entities`
(a relation to a named-but-unlisted endpoint is now kept with a stub
entity instead of quarantined). Relevant suite: 77 passed (the 12 new
plus the 65 from the relations fix), plus 6 chunking tests. Full
backend compileall passed.

Offline verification on the crater-6 data (no LLM key here, so this
replays what a fresh ingestion with the fix would do at the validation
and integration gates, using the 11 relations crater-6 actually
extracted; adjudication stubbed to no decisions, as in the earlier
offline runs; script `~/workspace/verify-relation-fix-v7.py`):
- Validation gate, per affected chunk, rebuilt from the chunk's
  persisted entities plus its quarantined relations: 11/11 relations
  kept (p15 2, p20 1, p24 4, p27 1, p42 2, p43 1), with 15 stub entity
  records across the 6 chunks.
- Integration gate, real code-first context from the crater-6 copy
  plus the rescued relation rows and stub entity rows: baseline
  reproduces the live v1 (0 relations, 167 entities, completeness
  missing 0); with the fix, relations 0 -> 9, entities 167 -> 178
  (+11 distinct stub canonical entities), completeness missing 0,
  complete True, 0 new unresolved facts. The 9 are: Electrode Holder
  Cable -> Positive Socket; Fan -> Welder (2 source ids, the p20 and
  p27 statements correctly becoming one relation); Ground Clamp ->
  Workpiece; Ground Clamp Cable -> Negative Socket (2 source ids,
  "Negative (-) Socket" and "Negative Socket" normalising to one);
  Ground Clamp Cable -> Positive Socket; MIG Gun Cable Connector ->
  Wire Feed mechanism; Reset Button -> circuit breaker; TIG Torch
  Cable -> Negative Socket; Wire Feed Power Cable -> Positive Socket.
  The Welder stub stays separate from the parts-list Welder (part
  57812) under the part-number rule, as designed; the relation links
  to the in-chunk Welder node.

Still not verified here (needs your setup, no LLM key in this
environment): a live fresh OMNIPRO ingestion with this fix. Expect
snapshot relations to go from 0 to about 9 on the same manual (the
exact count moves with LLM-run variance: whether the run extracts the
crater-5 spool relations, the crater-6 cable relations, or both),
with entities/procedures/failure modes/quantities within normal
run-to-run variance, and completeness missing 0. As before, test with
a fresh upload/job, not Retry on the completed job: Retry re-queues
nothing on a completed job and skips knowledge_done chunks, so it
cannot exercise this fix.

# Verified-repair loop (v8, 6 Nov 2026) -- diagnosis -> twin experiment -> VALIDATED / REJECTED

The flagship feature: from a diagnostic conclusion, "Verify this repair in
the twin" runs the leading repair as an isolated twin experiment and returns
a verdict -- VALIDATED, REJECTED, or unavailable with the reason -- before
anyone touches the machine. Every step is derived from the twin's own
explicit simulation rules, never from free text, and never by guessing a link.

## What was built

1. Extraction emits states, simulation rules and failure-mode entity links
   (`backend/knowledge/component_extraction.py`): both prompts gain a states
   push and a simulation-rules contract (rule-11: a rule ONLY when the text
   explicitly defines a machine-state transition; never infer from a
   component type or a vague behaviour description; both directions when the
   text states them; paths are "<Entity Name>.<state name>" using names from
   the same output). Failure modes gain entity_ids guidance (the ids of the
   entities named in their symptoms/causes). `"simulation_rules"` is added to
   the normalize key tuples in `_extract_result` and the batch loop, and to
   `_has_universal_facts`. `_universal_model_from_result` carries rules into
   `model.simulation_rules` and translates failure-mode entity_ids (chunk-local
   ids like `entity:p1`) to `entity_names` via the same-output id->name map.
   It also stamps `properties.entity_name` on ports/quantities/states/events/
   constraints whose entity_id maps to a known entity in the same output (see
   "two real bugs" below). `MachineEvent` and `MachineConstraint` gain a
   `properties` field (`backend/knowledge/machine_model.py`) so the stamp has
   somewhere to live.
2. Schema (`backend/knowledge/schema.py`, `backend/knowledge/machine_model.py`):
   `UniversalFailureMode.entity_names` and `MachineFailureMode.entity_names`;
   `MachineKnowledgeExtractionResult.simulation_rules: list[dict]`;
   `simulation_rule` tuples in `MachineModelBase.all_facts()`, so rules persist
   as `MachineKnowledgeFact` rows.
3. Chunk quarantine veto (`backend/knowledge/validation.py`): a rule is kept
   only when it has non-empty conditions+effects and every path names an
   entity and a state field the chunk actually extracted; otherwise that rule
   alone is quarantined (`simulation_rule`), never the chunk. The veto accepts
   both name paths ("<Entity Name>.<state>") and canonical-id paths
   ("<canonical id>.<state>") -- see "two real bugs".
4. Code-first integration (`backend/knowledge/code_first_integration.py`,
   `backend/knowledge/global_integration.py`): the context builder now passes
   `source_chunk_id` for universal facts (v6 added it for entities/relations
   only); `_model_from_payload` passes `entity_names` through on failure modes;
   the simulation-rule pass (after states are built) rewrites each path via
   `resolve_entity_id` (unique-only, same-chunk preferred) and requires the
   field to be a state the resolved entity actually has, else the rule stays
   unresolved (`simulation_rule_path_not_uniquely_resolvable` /
   `simulation_rule_unknown_state`); identical (conditions, effects) rules
   dedupe with unioned source_ids/evidence. Failure-mode `entity_names`
   resolve to canonical `entity_ids` (unique match only, same-chunk preferred).
   The facts-loop fallback name now reads `properties.entity_name` before
   `subject_name` (for states, the normalizer maps the state's own name to
   `subject_name`, so that was never the entity name), and resolution passes
   the row's `source_chunk_id` for same-chunk preference, as for relations.
5. Compiler (`backend/simulation/compiler.py`, `backend/simulation/schema.py`):
   `_coerce_state_value` ("true"->True, "120"->120, non-numeric strings pass
   through) applied in `_safe_initial_state`, because `MachineState.value` is
   a string while rule conditions carry JSON values; without it no transition
   could ever match. `TwinTransition` gains optional `source_ids` + `evidence`,
   passed through by the compiler, so verdicts can cite manual pages.
6. Verification (`backend/simulation/verification.py`, new):
   `build_verification_experiment` + `run_verified_repair`. The builder finds
   the twin's candidate rules (conditions mention the faulty component), takes
   the symptom from their downstream effects, the healthy value from the
   symptom component's initial state, fault rules = candidates whose effect on
   the symptom path differs from healthy, recovery rules = transitions whose
   effect restores it. Fault commands set the fault rules' conditions;
   intervention commands set the recovery rules' conditions; the assertion is
   the symptom path == healthy value, always >= 1, so the vacuous path in
   `run_experiment` (no assertions -> validated on any state change) is never
   reachable. Pre-flight gates, in order: the twin must be able to express the
   fault and the repair; the fault state must reproduce the symptom (else
   unavailable: "fault did not reproduce the symptom"); the intervention must
   fire at least one real transition (else unavailable: "no recovery transition
   fired"). Only then does the validated/rejected verdict stand. Unavailable
   is not a rejection: the model did not disprove the repair, it cannot
   evaluate it.
7. Expert seed (`backend/simulation/expert_rules.py` new,
   `backend/simulation/golden_rules/omnipro_wire_feed.json` new, merge in
   `backend/api/routes/simulation.py` compile route): hand-checked rules from
   the OMNIPRO 220 manual, every quote verbatim (p10 spool setup, p42
   MIG/Flux-Cored troubleshooting). Resolved by entity NAME at compile time
   into a copy of the model, unique match only; an ambiguous or unknown name
   is skipped and named in the compile warnings, never guessed. The applied
   seed is flagged in the twin's warnings and `metadata["expert_seed"]`. The
   seed carries two rule pairs: Feed Tensioner too tight -> Welding Wire not
   feeding (and the recovery), and Wingnut too loose -> Wire Spool spinning
   freely (and the recovery), plus the four healthy states.
8. Endpoint (`backend/api/routes/diagnostics.py`):
   `POST /diagnose/{session_id}/verify-repair` -- leading hypothesis (or a
   named cause). Resolves the cause via the matched snapshot failure mode's
   `entity_ids` first, then a unique name match against twin components;
   ambiguity stays open. Uses the latest ACTIVE twin only (draft -> approve
   gate preserved). Unavailable outcomes return HTTP 200 with
   `available: false` + reason, not errors. Appends a `verify_repair` timeline
   entry to the session state. Returns verdict + checks + rules used + fault /
   intervention traces + procedure citation (matched snapshot procedure, else
   the conclusion's `procedure_steps` as given). Works on concluded sessions.
9. Frontend (`frontend/src/components/DiagnosticWorkspace.jsx`,
   `frontend/src/api/client.js`, `frontend/src/api/diagnostics.js`): on a
   conclusion step, a "Verify this repair in the twin" button calls the
   endpoint and shows a verdict panel (VALIDATED / REJECTED / "Cannot verify
   yet" + reason, checks with expected vs actual, rules used, procedure steps,
   intervention trace, re-run button). The existing simulation-contract block
   stays for non-conclusion steps. `vite build` passes.

## Two real bugs the offline verification caught (both fixed in this build)

- Opaque chunk-local entity ids for attached facts. The mocked pipeline leg
  used ids like "entity:p1" (the "entity:p3" class of failure behind the v7
  relation fallback -- the LLM does emit such ids). States referencing them
  died at integration with `fact_entity_not_uniquely_resolvable`, because the
  only fallback name was `subject_name`, which for states is the state's own
  name, not the entity's -- and then the rule veto lost its known fields and
  the rule died too. Fixed by stamping `properties.entity_name` at extraction
  (above) and reading it first at integration, with same-chunk preference.
  Regression test: `test_state_with_opaque_chunk_local_id_resolves_via_stamped_entity_name`.
- Id-pathed rules re-quarantined at integration. The code-first rewrite passed
  (rules with canonical ids), but `integrate_revision_knowledge` re-runs
  `quarantine_machine_model` on the integrated model, and the veto only
  matched paths by entity NAME -- a path like
  "entity:feed-tensioner.tension_ok" quarantined as unknown entity. Fixed:
  the veto now also matches the head against kept entity ids. Regression
  test: `test_quarantine_accepts_canonical_id_paths_after_integration`.

## What the offline verification proved (script `~/workspace/verify-verified-repair.py`, 15/15)

On a COPY of crater-8.db (original untouched; no LLM key here):
- Compile + seed: 2 transitions (both Wingnut rules). The Feed Tensioner
  rules were SKIPPED with warnings naming the 5 duplicate "Feed Tensioner"
  entities in the crater-8 snapshot (all component type, functions "Applies
  tension to the feed roller/wire" / "Applies pressure to wire feed") -- the
  seed cannot pick one, and neither can the verify endpoint: a cause of
  "Feed Tensioner is too tight" matches 5 twin components and returns
  unavailable. That is the known duplicate-entity problem, not a seed bug;
  when the duplicate-merge fix lands, the same seed applies fully unchanged.
- Wingnut fault on the real twin -> VALIDATED: fault trace "Loose wingnut
  lets the spool spin freely: ... spinning_freely False -> True", intervention
  trace "Tightening the wingnut stops the spool spinning freely: ...
  True -> False", check `components.entity:wire-spool.spinning_freely`
  expected False, actual False, passed.
- Wire Feed Motor (no fault rule) -> unavailable, "No simulation rule in
  this twin describes a fault in entity:wire-feed-motor".
- Synthetic twin with a second fault holding the symptom -> REJECTED (the
  model propagated the repair and the symptom still did not recover).
- Mocked-gateway extraction -> persist -> integrate: the rule survives the
  whole pipeline and the snapshot carries it with canonical ids
  (`entity:feed-tensioner.tension_ok: false` -> `entity:welding-wire.feeding:
  false`, evidence `[{"fact": "Feed Tensioner is too tight.", "page": 42}]`);
  the failure mode links `entity_ids: ["entity:feed-tensioner"]`;
  completeness 7/7, 0 missing, 0 unresolved.

Tests: 16 new in `tests/test_verified_repair.py` (coercion, prompt contract,
quarantine veto, code-first veto + rewrite, failure-mode linking, builder
validated/rejected/3x unavailable, seed resolution). Relevant suite: 133
passed; the only failures are the 5 pre-existing ones that fail identically
on the pristine v7 tree (3 llm_provider: missing `anthropic` / `google.genai`
SDKs in the minimal venv; 2 phase8a_integration: `page_to_chunks` no longer
exists in pipeline.py, schematic projection creates 0 ports). 3 more files
(test_diagnostics, test_phase1_hardening, test_phase8a_hardening) fail at
collection on both trees: they import `invalid_fact_keys` / `validate_facts`,
which exist in neither tree. Full backend compileall passed; `vite build`
passes.

## Live check (needs your setup; fresh OMNIPRO upload/job, NOT Retry)

Two prerequisites before the seed can apply live. First, the product-row SQL
fix (still outstanding since 3 Nov): the crater-8 product row is
`tets/tets/tets`, and the seed matches on manufacturer "Harbor Freight" /
model "OMNIPRO 220", so until the guarded UPDATE runs, compile finds no seed
and the twin has 0 transitions. Run it (backup first), then a fresh
upload/job (not Retry), integrate, compile, approve the twin, then start a
diagnosis with symptom "Wire Unspooling and Tangling" and verify the repair
when it concludes -- expect VALIDATED via the Wingnut rules, with the
intervention trace in the panel. The p42 symptom "Wire Feed Motor Runs but
Wire Does Not Feed Properly" will return unavailable until the
duplicate-entity fix lands, with the reason naming the 5 Feed Tensioners --
that is the honest answer, and the demo should show it as such rather than
hiding it. States/simulation_rules in a fresh run depend on the new prompts;
expect the snapshot to carry some rules (count moves with LLM variance) and
failure modes to carry entity_ids where the text names the component.

# Narrow adjudication veto + visual twin (v9, added 6 Nov 2026)

Two pieces, one release. First, the duplicate-entity fix that unblocks the
p42 wire-feed demo. Second, the visual twin: the compiled twin drawn from
its real relations, animated from the experiment trace.

## 1. Narrow veto: descriptive prose no longer vetoes an LLM merge

Root cause, traced in `backend/knowledge/code_first_integration.py`:
`_group_entities` splits same-name entities on any differing non-empty
property via `_props_conflict`, a `conflicting_same_name` cluster is formed,
but `apply_adjudication` vetoed any merge where `_props_conflict` held --
and a differing `function` string counted. The five crater-8 Feed Tensioners
split solely on two paraphrased function wordings, so a merge was
unenforceable whatever the LLM decided.

Change (narrow only): the veto now checks `_hard_identity_conflict` --
differing non-empty `part_number` / `location_description` / `voltage` --
plus the existing same-list-distinct check. That matches the adjudication
prompt, which already told the LLM those three signals. `function` is
LLM-paraphrased prose and never vetoes on its own. Grouping is untouched
(code never merges on its own), the LLM stays judge, the 50-cluster cap
stays. On merge the kept entity's function wins via `setdefault`; the other
wording stays in source rows and evidence, not canonical properties.

A real bug the offline verification caught, fixed in the same change:
merges resolved candidates by canonical id alone, and two distinct entities
can share an id (the known Welder collision -- universal and legacy
groupings assign the same suffix independently, e.g. `entity:welder-57812`
twice). `canonical_by_id` collapses them, and the old removal dropped
payload entities whose source rows were never unioned -- the first
verification run lost 5 Welder rows that way (completeness 937/955 vs live
942/955). `apply_adjudication` now resolves every candidate by id AND
source rows (the cluster carries each candidate's `source_ids`), removes
exactly the merged payload entities (by identity), and only drops an id
lookup when it actually points at the merged entity. A candidate that
cannot be resolved to exactly one entity is skipped. The state dict now
also carries the full `canonical` list for this resolution.

## 2. Visual twin

Backend: new `backend/simulation/visual.py` -- `layout_twin(definition,
state)` computes a deterministic layered layout (BFS from roots, alphabetical
within a layer; unconnected components in the last layer, never hidden) with
nodes = twin components carrying their current state, edges = the canonical
relations from `metadata["topology"]` (endpoints that are not twin components
are dropped, never invented), transitions with evidence, plus warnings and
the expert-seed flag. New endpoint `GET /simulation/twins/{twin_id}/visual`
in `backend/api/routes/simulation.py`. The verify-repair endpoint now also
returns `baseline_state` / `fault_state` / `final_state`, which the playback
needs.

Frontend: new `frontend/src/components/VisualTwin.jsx` (plain SVG, no new
dependency). Nodes show name, type and live state lines; edges show the
relation type; clicking a component shows its state, the rules involving it
(id- or name-pathed) and their evidence. Trace playback steps through the
three states an experiment already returns -- Baseline, Fault injected,
Repair applied -- with a Play button, ringing in red the components that
changed when the fault hit and in green the ones that changed when the
repair was applied, and showing the fault / intervention trace per phase.
Wired as a "Visual Twin" tab in `App.jsx` (opened by the
`crater-open-visual-twin` event, mirroring `crater-open-simulation`) and an
"Open visual twin" button in the DiagnosticWorkspace verification panel,
which passes the verified-repair result as playback.

Tests: 3 new in `tests/test_integration_code_first.py` (function-only
difference merges; location/voltage still veto; duplicate-id collision
merges the cluster while the unrelated entity sharing the id is untouched),
6 new in `tests/test_visual_twin.py` (layout determinism, roots layer 0 /
unconnected last, edges only from documented topology, live state +
transitions carried, endpoint shape, verified-repair result carries the
three playback states). Relevant suite: 142 passed (133 in v8 + 9 new); the
only failures are the same 5 pre-existing ones (3 llm_provider missing SDKs,
2 phase8a_integration). Full backend compileall passed; `vite build` passes.

Offline verification (`~/workspace/verify-v9.py`, 17/17 on a copy of
crater-8.db, original untouched): re-integrating crater-8's rows with an
adjudicator that merges wherever candidates differ only in prose gives
Feed Tensioner 5 -> 1 (kept `entity:feed-tensioner-interior-controls`, 7
source ids), Wire Feed Mechanism 4 -> 1, fans stay separate, completeness
942/955 -- exactly the live snapshot's figure, the same 13 relation rows
left open. Compile + expert seed applies all 4 wire-feed rules (2 Wingnut
+ 2 Feed Tensioner, no ambiguity warning). The p42 symptom now returns
VALIDATED (Welding Wire.feeding False in the fault state, True after the
repair); Wingnut still VALIDATES. Visual layout: one node per twin
component, one edge per canonical relation, every edge endpoint a real
node, deterministic.

## Live check (needs your setup; fresh OMNIPRO upload/job, NOT Retry)

Run the guarded product-row SQL fix first (the fresh DB's row will have its
own placeholder typo -- guard SELECT first, then UPDATE that row's actual
values to Harbor Freight / OMNIPRO / 220; the crater-8 `tets/tets/tets`
UPDATE from my earlier message does NOT apply to a fresh DB). Then a fresh
upload/job, integrate, compile, approve the twin. Expect: snapshot has
exactly one Feed Tensioner; compile applies 4 seed transitions and the
"5 entities named 'Feed Tensioner'" warning is gone; diagnose symptom
"Wire Feed Motor Runs but Wire Does Not Feed Properly" and verify at
conclusion -- expect VALIDATED, then "Open visual twin" to watch the fault
and the repair play across the diagram. The Wingnut/spool demo ("Wire
Unspooling and Tangling") should still VALIDATE.

# Hypothesis tournament (v10, added 9 Nov 2026)

The next ranked feature (item #2 from the 6 Nov ranking, approved 9 Nov):
instead of verifying one repair, run the session's hypotheses through the
same verified-repair loop and rank them -- VALIDATED first, REJECTED
second, unavailable last. One call, one timeline entry, no new LLM calls.

## What was built

Backend: new `backend/simulation/tournament.py` -- `verify_single_cause`
is the per-cause machinery of `POST /diagnose/{id}/verify-repair`,
extracted unchanged (snapshot failure-mode match, then failure-mode
entity_ids, then a unique name match against twin components with
ambiguity left open; `run_verified_repair` with its pre-flight gates;
procedure citation; the full result dict with rules used, checks, traces
and the baseline / fault / final states for playback). `run_tournament`
loops `state.hypotheses[:5]` through it, `rank_tournament_rows` sorts
stably (VALIDATED 0, REJECTED 1, unavailable 2), `tournament_status`
derives the row status from the verdict alone -- an unavailable row is
one with no verdict, never a rejection.

`backend/api/routes/diagnostics.py`: `verify_repair` now calls
`verify_single_cause` and only adds its timeline entry and commit -- the
same response keys and the same timeline entries as v9 (proven by the
endpoint test below). New `POST /diagnose/{session_id}/verify-hypotheses`:
loads the session and the latest ACTIVE twin, runs the tournament,
appends exactly one timeline entry (`kind: verify_hypotheses`, with the
counts and a per-row cause/status/verdict summary), and returns the
ranked leaderboard with counts, the `unavailable_label` ("the twin
cannot evaluate this cause") and the isolated-twin warning. No twin or
no hypotheses returns HTTP 200 `available: false` (or 409 when the
session has no hypotheses at all), mirroring verify-repair. The raw
batch endpoint `POST /simulation/twins/{id}/hypotheses` is NOT wired --
it takes hand-built payloads nothing in diagnosis fills in.

Deliberately unchanged: no extraction, compiler, engine or integration
change; no new LLM calls (deterministic twin runs on isolated copies);
capped at 5; the single-cause verify endpoint and button are untouched.
Rejected: auto-eliminating hypotheses from tournament results -- a
REJECTED verdict is evidence for the technician, not a silent state
mutation. The tournament only reads the session state.

Frontend: `frontend/src/api/client.js` gains `verifyHypotheses`
(re-exported from `frontend/src/api/diagnostics.js`). In
`DiagnosticWorkspace.jsx` a "Run hypothesis tournament" button sits
beside the "Verify this repair in the twin" button and next to
"Re-run verification", and a HYPOTHESIS TOURNAMENT card lists the
ranked rows: rank, cause, status pill (VALIDATED / REJECTED /
UNAVAILABLE), fault component, symptom path, checks, rules used with
their evidence (manual quote + page) and source chunk ids, procedure
steps, and per-row "Open visual twin" playback from that row's
baseline / fault / final states. Unavailable rows show the label plus
the specific reason.

Honest limits, preserved: on the OMNIPRO seed twin only 2 causes are
evaluable (Wingnut + Feed Tensioner, the 4 seed rules); the rest come
back unavailable, which is the correct answer. Two real data facts the
offline run surfaced rather than hid: "Wire Feed Motor is overheating"
is ambiguous in crater-8 because a "Wire Feed" component also matches
the substring name rule, and crater-8's failure modes carry no
entity_ids, so resolution runs on name matching alone. The p42 symptom
string itself ("Wire Feed Motor Runs but Wire Does Not Feed Properly")
name-matches the Wire Feed Motor, which is why the tournament resolves
by hypothesis cause, not by symptom text.

Tests: 14 new in `tests/test_hypothesis_tournament.py` (per-cause
validated / rejected / unavailable incl. no-fault-rule, no-match and
ambiguous; leaderboard ordering with stability inside one status;
unavailable is not a rejection and no hypothesis is mutated; the 5-cap;
the v9 single-cause response keys; per-row evidence with manual page;
endpoint tests with an in-memory DB: ranked leaderboard, exactly one
timeline entry, verify-repair response unchanged). The routes import
needs a `rank_bm25` stub in that file only -- the test venv has no
rank_bm25, and the stub exists solely so the module can import.
Relevant suite: 156 passed (142 in v9 + 14 new); the only failures are
the same 5 pre-existing ones (3 llm_provider missing SDKs,
2 phase8a_integration) and the same 3 files fail collection. Full
backend compileall passed; `vite build` passes.

Offline verification (`~/workspace/verify-tournament-v10.py`, 27/27 on a
copy of crater-8.db, original untouched): re-integration reproduces the
v9 baseline (Feed Tensioner 5 -> 1, fans separate, completeness 942/955,
same 13 relation rows open); compile + expert seed applies all 4
wire-feed rules (4 transitions). The tournament on the compiled twin
returns Feed Tensioner VALIDATED and Wingnut VALIDATED, with Welding
Wire unavailable (no fault rule for it), Wire Feed Motor unavailable
(ambiguous -- Wire Feed also matches) and a ghost cause unavailable (no
component match); leaderboard order VALIDATED first, unavailable last;
tensioner playback feeding True -> False -> True; the session state
unmutated. The HTTP route returns the same leaderboard and persists
exactly one timeline entry; single-cause verify still returns VALIDATED
with the v9 response keys.

## Live check (needs your setup; fresh OMNIPRO upload/job, NOT Retry)

On the fresh v9 run (product-row SQL first, fresh upload/job, integrate,
compile, approve), diagnose the p42 symptom "Wire Feed Motor Runs but
Wire Does Not Feed Properly" and, at the conclusion, click "Run
hypothesis tournament". Expect Feed Tensioner VALIDATED at the top of
the leaderboard and the unevaluable causes unavailable with reasons --
not rejected. Open the visual twin from a validated row to watch that
cause's fault and repair play across the diagram.

# States-schema fix (v10.1, added 9 Nov 2026)

Live bug found on re-ingestion of the OMNIPRO manual (fresh DB, 9 Nov):
chunks failed with "LLM extraction schema validation failed" on
`entities.N.states.0` -- the LLM returns states as name/value objects
(`{'name': 'tightness', 'value': 'loose'}`, `{'name': 'spinning',
'value': 'free'}`, `{'name': 'inserted', 'value': False}`), exactly as
the v8 prompts ask, but `MachineEntity.states` was declared `list[str]`
and `MachineState.value` was `str | None` (Pydantic v2 rejects booleans
and numbers for a `str` field). The failure is deterministic, not flaky:
retrying the same chunk fails the same way, and the chunk produces zero
facts (recorded as a `chunk_extraction_failure` unresolved fact) because
the failure happens at parse time, before per-fact quarantine runs.

Root cause of the miss: the v8 tests and offline verification built
extraction results in code with empty entity states and string-valued
top-level states, so real LLM output shape never passed through the real
Pydantic schema. The prompt and the schema were written in the same
change and never cross-checked. Lesson recorded in the test file: any
extraction-schema change must be tested with a real LLM-shaped JSON
payload through `model_validate`, not hand-built fixtures that conform
by construction.

## What was fixed

`backend/knowledge/machine_model.py`: new `EntityState` model -- `name:
str`, `value: str | bool | int | float | None`, with a before-validator
that turns a bare string into a named state with no value and keeps any
other shape as a name rather than failing the chunk. `MachineEntity.states`
is now `list[EntityState]`; `MachineState.value` widens to
`str | bool | int | float | None`. The prompts are unchanged -- they were
correct; only the schema was wrong.

`backend/knowledge/schema.py`: `UniversalEntity.states` is now
`list[EntityState]` (imported from `machine_model`); this is the first
gate the live error hit (`MachineKnowledgeExtractionResult.model_validate`).

`backend/knowledge/component_extraction.py`: persist dumps entity states
to plain dicts (`model_dump(mode="json")`) for the JSON `states` column;
Pydantic objects would not serialize there.

`backend/knowledge/validation.py`: the simulation-rule veto's known-fields
now registers entity-embedded states under their enclosing entity's id.
The loop already iterated entity states for this purpose, but a state
object carries no `entity_id` of its own, so embedded states contributed
nothing and a rule naming only an embedded state was quarantined even
though the chunk extracted that state. Top-level states are unchanged.

`backend/simulation/compiler.py`: docstring only -- `_coerce_state_value`
already returns non-string values unchanged, so native bool/int state
values are a no-op for it and string values ("true", "120") are still
coerced. No behavior change in the compiler, engine, or integration;
code-first integration already handled dict states embedded in entities.

Tests: 8 new in `tests/test_states_schema.py` (no LLM). Every test feeds
raw dicts -- the exact live payload, including a boolean `False` and a
numeric `120` -- through `MachineKnowledgeExtractionResult.model_validate`,
then through `_universal_model_from_result`, quarantine, persist (in-memory
SQLite, asserting plain JSON dicts in the column) and
`build_code_first_payload` (asserting embedded states land in
`payload["states"]` with the canonical entity id); plus the pre-v8
bare-string shape, a malformed state that must not kill its chunk, and a
compile check that native bool/int values survive into the twin's initial
state. Relevant suite: 167 passed (159 in the pristine v10 tree + 8 new);
the only failures are the same 5 pre-existing ones (3 llm_provider missing
SDKs, 2 phase8a_integration) and the same 2 collection errors
(`rank_bm25` not installed in the test venv, verified identical on the
pristine v10 zip). Full backend compileall passed; `vite build` passes.

Offline verification (all three scripts re-run, each on a copy of
crater-8.db, original untouched): `verify-verified-repair.py` 15/15,
`verify-v9.py` 17/17, `verify-tournament-v10.py` 27/27 -- same results as
before, including completeness 942/955, Feed Tensioner 5 -> 1, p42
VALIDATED, Wingnut VALIDATED, and tournament counts 2 validated /
0 rejected / 3 unavailable.

## Live check (needs your setup)

Deploy v10.1, then resume/retry the failed ingestion job -- failed chunks
are recorded as `chunk_extraction_failure` and are not `knowledge_done`,
so a resume re-extracts them -- or a fresh upload if the job completed
(Retry on a completed job re-queues nothing). Expect zero "LLM extraction
schema validation failed" errors on states, and the previously failing
chunks to produce facts.

# Frontend rebuild (v11, added 9 Nov 2026)

The old frontend was eight flat tabs with no hierarchy: Diagnose, Machine
Model, Component Explorer, Procedure Viewer, Capture Rajesh, Digital Twin,
Simulation Agent, Visual Twin. Compile lived under Simulation Agent, the
twin lived under Visual Twin, and both asked for raw UUIDs (Revision ID,
Procedure ID, "Load twin"), so after ingestion the user had to know where
to go next. v11 rebuilds the navigation around the job being done, with one
machine context carried across every view. Frontend only: no backend
changes, no new dependencies.

Four destinations, one machine context:

- **Machines** (home): machine list + onboarding, reusing the existing
  `MachineOnboarding` flow. Each machine card shows name, revision,
  entity/relation/conflict counts, and one clear pipeline -- Upload →
  Extracting → Integrate → Ready. The primary action follows the state:
  Add manual, Retry ingestion, Build machine knowledge, Compile twin, or
  Open twin. Secondary quiet actions: Diagnose, View model.
- **Diagnose**: the existing `DiagnosticWorkspace`, unchanged in function.
  Verify-this-repair and Run-hypothesis-tournament stay at the conclusion.
  "Add expert note" is a secondary action here (overlay); expert capture
  is no longer a top-level tab.
- **Machine Model**: the existing `MachineModelViewer`. Component Explorer
  and Procedure Viewer are now sections/panels inside this view -- a
  "Component explorer" section (component list + diagram side by side) and
  a procedure viewer opened by clicking a procedure in the Procedures list
  -- driven by clicks, never by pasted ids. The viewer is now controlled
  by the app's machine context instead of its own selects.
- **Twin**: one workspace merging Digital Twin, Simulation Agent and
  Visual Twin as sub-views (Overview / Simulate / Visual / Rules), reusing
  those three components. Overview shows twin status (draft/active),
  component/relation/transition counts, compiler warnings, expert-seed
  details, the safety disclaimer ("Simulation is not proof of physical
  safety."), and the live twin state. Simulate is the workbench (experiment
  plus a new batch-comparison panel that runs several hypotheses against
  the approved twin in one call). Visual is the visual twin with trace
  playback (its SVG now scales to the container). Rules lists every
  explicit simulation rule with conditions, effects, safety notes and
  manual evidence, plus the expert seed. Compile and Approve live in the
  workspace header as the single primary action; a disabled Compile says
  why ("Compile is disabled until the machine model is integrated").
  The approve gate calls the existing POST /simulation/twins/{id}/approve
  (new `approveTwin` client call, no backend change).

Machine context bar: a thin bar under the header on every view shows the
current machine (manufacturer · family · model — revision label) and twin
status, with a "Switch machine" action. No view asks for a UUID any more:
every Revision ID / Procedure ID / Product ID text input and the "Load
twin" button are gone; selection is by machine, revision and twin
dropdowns, or by clicking.

Guided post-ingestion flow: after ingestion + integration finish, the
machine card shows one primary action, "Compile twin". Clicking it opens
the Twin workspace and compiles; when compile finishes, the Visual
sub-view opens with the twin already loaded. The old custom events still
work and now route into the workspace: `crater-open-simulation` →
Simulate, `crater-open-visual-twin` → Visual (with playback context).

Adaptive layout: desktop keeps left nav + main content; the header wraps
below 1200px; the nav becomes a horizontally scrolling bar, cards and
grids stack to one column, and buttons get 44px touch targets below
768px; the visual-twin SVG scales to its container; tables become stacked
cards on small screens.

Verification: `vite build` passes; `oxlint` reports 32 warnings and 0
errors (pristine v10.1: 23 warnings, 0 errors -- the +9 are the same
setState-in-effect data-loading pattern the existing components already
use, in the two new files). Headless render check: the app and all four
destinations were server-rendered through vite's SSR build and asserted
on their key strings (nav, Machines home, Diagnose setup, Machine Model,
Twin Overview/Simulate/Visual/Rules, Compile twin, safety disclaimer).
No live backend was available here, so click-through against the deployed
backend is the live check below. Backend untouched.

## Live check (needs your setup)

Deploy v11, open Machines, pick the OMNIPRO machine, and confirm: the
card shows its pipeline stage and counts with no UUID input anywhere;
"Compile twin" opens the Twin workspace, compiles, and lands on the Visual
sub-view with the twin loaded; the context bar shows the machine and twin
status on every view; Diagnose → verify → "Open visual twin" still plays
the fault → repair trace; the hypothesis tournament still runs from the
diagnosis conclusion.

## v11.1 — machine selection fix (9 Nov 2026)

Bug found on first live use of v11: clicking "Open twin" / "Diagnose" / "View model" / "Compile twin" on a machine card did not select the machine. The card buttons passed `(product, revision)` positionally, but App.jsx's handlers expect one object `{ productId, revisionId }` — so both ids arrived as `undefined`, nothing was selected, and the Twin/Diagnose/Model views showed "No machine selected". The onboarding `onReady` path already passed the object form, so only the four card buttons were wrong.

Fix: the four card buttons now pass `{ productId: product.id, revisionId: revision.id }`. Clicking a machine card now also selects it (highlighted border + "Selected" pill, context bar updates). The context bar no longer says "No machine selected" on the Machines view — there it now says how to select ("Pick a machine below — click its card to select it, or use Open twin / Diagnose / View model"). No backend change. Frontend only: App.jsx, Machines.jsx, App.css.

Verification: `vite build` passes. Live check: deploy v11.1, open Machines, click the OMNIPRO card — it highlights as Selected and the context bar shows the machine; "Open twin" opens the Twin workspace with the twin loaded instead of "pick a machine".

## v12 — diagnosis robustness (9 Nov 2026)

Why: the LLM frequently returned invalid JSON during diagnosis and every failure escalated the session, so Nishant could not reach a grounded conclusion to test verify-repair / tournament. Root causes, all in the diagnose path (ingestion already had the defences): `backend/diagnostics/agent.py` did a bare `json.loads(raw)` with no fence strip, brace hunt, lenient repair or retry — the only JSON consumer in the backend without one; no provider enforced JSON (only `max_output_tokens`, no `response_mime_type`/`response_schema`, no tool use, no temperature); the agent hardcoded `max_tokens=2000`, so a full turn could truncate mid-JSON; the prompt was ~103k chars on crater-8 (all 294 components + 54 failure modes unfiltered, full evidence chunk text inside `state_json`); one parse failure escalated the session, which then 400s on `/respond` — the session was dead and the hypotheses lost; and a parseable-but-wrong-shape reply raised uncaught from `model_validate` (500, turn lost). Each turn also made 3 LLM calls because `hybrid_retrieve` ran twice.

On the output cap: a literal "no cap" is not something the APIs accept — Anthropic requires `max_tokens`, Gemini clamps to the model maximum. The diagnosis ceiling is now 8192 tokens (route default, no per-call cap in the agent). A real turn is ~1k tokens of JSON, so 8192 is a safety net the model never approaches and costs nothing extra (the model stops at the end of the JSON object); truncation is now detected off the provider's finish reason and gets one repair attempt instead of surfacing as a mystery parse failure.

Fix: provider-level structured output (`provider.py`/`gateway.py`: new `complete_result` returning `(text, truncated)`; Gemini JSON mode, Claude forced tool use, temperature 0.2 for diagnosis); lenient parse ladder + one repair call; per-hypothesis quarantine (bad hypothesis dropped, good ones kept; percent confidences rescaled 45 -> 0.45); malformed `next_step` defaults to a question; unparseable output or provider failure degrades to a question step with previous hypotheses intact and the session ACTIVE — escalation remains only for the real guards (turn limit, sustained low confidence, no provider configured). Prompt slimmed ~103k -> ~13k chars on crater-8 (evidence ids only in `state_json`, relevance-filtered knowledge block capped 25 components / 12 failure modes, canonical payloads stripped of evidence and capped at 1200 chars); retrieval deduped to one call per turn (3 LLM calls -> 2). No extraction, compiler, engine, integration, twin or frontend change.

Verification: 24 new tests in `tests/test_diagnosis_robustness.py`, all passing (fenced/preamble/trailing-comma/single-quote/unquoted outputs, truncation + repair, degrade-to-question, wrong shape, prompt slimming, single retrieval, `run_turn` surviving a bad turn with the session still active). Full suite: 195 passed, same 5 pre-existing failures as v10.1 (3 llm_provider missing SDKs in this venv, 2 phase8a_integration) + 1 pre-existing collection error in test_phase8a_hardening.py. Offline re-runs: tournament 27/27, v9 17/17. Live check: deploy v12, run the p42 diagnosis for several turns — the session must stay active and reach a conclusion; then verify-repair + tournament as before.
