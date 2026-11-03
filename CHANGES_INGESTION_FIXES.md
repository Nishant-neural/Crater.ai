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
