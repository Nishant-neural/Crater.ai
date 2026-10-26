# Schema normalization and trust boundaries

## What this changes

- `backend/knowledge/normalization.py` is the shared boundary adapter for LLM and legacy dictionaries.
- Common aliases are mapped into canonical field names; scalar list fields are normalized; missing IDs are deterministic.
- Unknown keys and the original input are preserved as `normalization_warnings` and `_original_payload` when normalization was needed. The machine-model Pydantic base allows extra fields so that normalization metadata is not silently discarded.
- Invalid evidence `claim_status` values remain hard validation errors. They are not silently converted to `uncertain`, because that would hide a schema defect.
- `validate_machine_model_with_ledger()` runs structural/semantic checks and returns explicit unresolved records for missing entity references, placeholder references, and evidence-free procedures/failure modes. The caller can persist those records for review.

## Important boundary

Pydantic validation only establishes that a payload can be represented by a schema. It does not prove that the claim is true, that the cited page supports it, that units are compatible, or that a simulation rule is safe.

Use the pipeline gates in this order:

1. Preserve raw model output.
2. Normalize known aliases.
3. Validate canonical structure.
4. Validate references, provenance, units and contradictions.
5. Persist unresolved records rather than dropping them.
6. Permit simulation only for explicitly grounded and reviewed executable rules.

## Integration note

`validate_machine_model_with_ledger()` is an opt-in API and does not write to the database by itself. The integration service must persist its returned ledger entries in the revision's unresolved-fact store. This keeps the validator side-effect free and avoids pretending that an in-memory warning is durable storage.
