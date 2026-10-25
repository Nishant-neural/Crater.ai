# Machine knowledge extraction performance fix

## Why extraction was slow

- Gemini usage was close to the requests-per-minute and requests-per-day ceilings, while reported token use was much lower than the TPM ceiling.
- Extraction batches defaulted to 12 chunks / 72,000 characters. The pipeline budget counted chunk text but not source-ID delimiters, so a nominally full batch could exceed the extractor's real prompt budget.
- The retry loop repeated malformed/schema-invalid batches up to three times before splitting them, consuming request quota even when repeating the same prompt was unlikely to help.
- A rate-limited batch could be recursively split into more requests, amplifying the quota problem.

## Changes

- Fixed a worker-stopping `NameError`: the extraction progress message referenced undefined `chars`; it now reports the batch-local `prompt_chars` value.
- Increased text chunk defaults to 10,000 target characters / 12,000 maximum characters and raised overlap to 800 characters.
- Set extraction batch budget to 70,000 characters. At an approximate 4 characters per input token, this is about 17,500 input tokens/request; at 12 RPM it targets about 210k input TPM, leaving headroom under a 250k TPM quota for generated tokens and tokenizer variation. This is an estimate, not an exact tokenizer measurement.

- Default extraction batch size increased from 12 to 18 chunks, and the character budget from 72,000 to 70,000. Actual prompt wrapper overhead is now included when assembling batches.
- Malformed/truncated responses are split immediately rather than retried with the same prompt.
- Transient provider errors get at most one short retry before a batch is split; rate-limited errors get at most one quota-aware retry and are never recursively split.
- Progress now reports chunk count, approximate source characters, and elapsed time for each successful extraction batch.
- Schema normalization, semantic validation, unresolved-fact persistence, and provenance behavior remain in place.

## Quota note

The code intentionally does not bypass Gemini RPM/RPD limits. At 456/500 RPD, an already-running job may still run out of daily requests if its remaining extraction and schematic/integration work exceeds the remaining quota. Larger batches reduce future request consumption; they do not restore quota already consumed. If `.env` explicitly sets `EXTRACTION_BATCH_SIZE` or `EXTRACTION_BATCH_MAX_CHARS`, update `CHUNK_SIZE_CHARS=10000`, `CHUNK_OVERLAP_CHARS=800`, `CHUNK_MIN_CHARS=2000`, `CHUNK_MAX_CHARS=12000`, and `EXTRACTION_BATCH_MAX_CHARS=70000`, or remove those overrides to use the defaults.

## Verification

- Python syntax compilation passed.
- Focused extraction, schema-normalization, and unresolved-ledger tests: 13 passed.
- Global integration tests: 4 passed.
- Full phase integration tests could not be collected in this environment because `qdrant_client` is missing; another existing hardening test imports `invalid_fact_keys` and `validate_facts`, which are not defined in the supplied validation module.
