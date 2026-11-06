#!/usr/bin/env python3
"""Read-only coverage check for a Crater.ai ingestion DB.

Usage:  python3 crater-coverage-check.py <path-to-crater.db>
It never writes to the DB (sqlite URI mode=ro).

Reports, for text/table chunks only:
  - how many have any persisted fact (entity/relation/procedure/
    component/failure_mode/behaviour/fact row pointing at the chunk)
  - the per-chunk fact counts, so thin-but-non-empty chunks are visible
  - the chunk_extraction_empty records, grouped by reason -- a v4
    empty-retry failure shows up as a reason containing
    "single_retry_failed"
  - the latest snapshot counts, for comparison with the crater-3
    baseline (snapshot v1: 41 entities, 2 relations, 2 procedures,
    9 failure modes; 2 of 25 text/table chunks with facts).
"""
import json
import sqlite3
import sys
from collections import Counter

path = sys.argv[1] if len(sys.argv) > 1 else "crater.db"
con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
con.row_factory = sqlite3.Row

FACT_TABLES = [
    "machine_knowledge_entities",
    "machine_knowledge_relations",
    "machine_knowledge_behaviors",
    "machine_knowledge_facts",
    "components",
    "component_relationships",
    "procedures",
    "failure_modes",
]

print("== job ==")
for r in con.execute(
        "SELECT status, stage, attempt_count FROM ingestion_jobs"):
    print(dict(r))

counts = Counter()
for t in FACT_TABLES:
    try:
        for r in con.execute(
                f"SELECT source_chunk_id, COUNT(*) c FROM {t} "
                f"GROUP BY source_chunk_id"):
            if r["source_chunk_id"]:
                counts[r["source_chunk_id"]] += r["c"]
    except sqlite3.Error as e:
        print(f"  (skipped {t}: {e})")

print("\n== text/table chunks ==")
rows = con.execute(
    "SELECT id, page_number, chunk_type FROM chunks "
    "WHERE chunk_type IN ('text', 'table') ORDER BY page_number, chunk_type"
).fetchall()
with_facts = [r for r in rows if counts.get(r["id"], 0) > 0]
print(f"text/table chunks: {len(rows)}; with facts: {len(with_facts)}; "
      f"empty: {len(rows) - len(with_facts)}")
for r in rows:
    print(f"  p{r['page_number']} {r['chunk_type']}: "
          f"{counts.get(r['id'], 0)} facts")

print("\n== chunk_extraction_empty records, by reason ==")
for r in con.execute(
        "SELECT reason, COUNT(*) c FROM unresolved_facts "
        "WHERE fact_type='chunk_extraction_empty' "
        "GROUP BY reason ORDER BY c DESC"):
    print(f"  {r['c']}x {r['reason']}")
n_retry_failed = con.execute(
    "SELECT COUNT(*) FROM unresolved_facts WHERE "
    "fact_type='chunk_extraction_empty' AND reason LIKE '%single_retry_failed%'"
).fetchone()[0]
print(f"  empty records where the v4 single retry itself failed: "
      f"{n_retry_failed}")

print("\n== latest snapshot ==")
snaps = con.execute(
    "SELECT version, created_at, source_counts FROM "
    "machine_knowledge_model_snapshots ORDER BY version").fetchall()
print(f"snapshot versions: {len(snaps)}")
if snaps:
    latest = snaps[-1]
    print(f"v{latest['version']} created {latest['created_at']}")
    print(f"  source_counts: {latest['source_counts']}")
    model_row = con.execute(
        "SELECT model FROM machine_knowledge_model_snapshots "
        "WHERE version=?", (latest["version"],)).fetchone()
    try:
        model = json.loads(model_row[0]) if isinstance(
            model_row[0], str) else model_row[0]
    except Exception:
        model = {}
    for key in ("entities", "relations", "procedures", "failure_modes",
                "conflicts", "unresolved_facts"):
        print(f"  {key}: {len(model.get(key) or [])}")
print("\nDone.")
