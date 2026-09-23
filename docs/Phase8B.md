# Phase 8B — Global Machine Knowledge Integration

Phase 8A extracts machine facts one chunk at a time. Phase 8B adds the missing
revision-wide integration stage.

```text
Chunks / schematics / procedures / expert knowledge
                    ↓
          persisted extracted facts
                    ↓
       Global Integration LLM pass
                    ↓
       entity resolution + relation
       integration + conflict preservation
                    ↓
        canonical UniversalMachineModel
                    ↓
       revision-scoped model snapshot
```

The global LLM receives all persisted machine-knowledge records for one
revision, not the original document chunks. Each canonical object carries
`source_ids` during integration so the source records remain traceable.

API:
- `POST /knowledge/revisions/{revision_id}/integrate`
- `GET /knowledge/revisions/{revision_id}/model`

The integration is explicit because it is an LLM operation over the complete
revision knowledge set and may be expensive. Source extraction remains
chunk-local and idempotent.

## Phase 8C hardening: revision-aware retrieval, conflict resolution, verification
- Retrieval now combines source chunks with canonical UniversalMachineModel facts from the latest revision snapshot.
- Product + revision are hard retrieval boundaries; a mismatched product/revision returns no canonical knowledge.
- Global integration has an explicit `conflicts` output and must preserve conflicting evidence instead of silently overwriting it.
- `GET /knowledge/revisions/{revision_id}/verify` runs deterministic topology and available spatial-coherence checks.
- Spatial verification validates normalized schematic bounding boxes and flags implausibly distant `connected_to`/`adjacent_to` entities. It does not claim full 3D physical validation.
