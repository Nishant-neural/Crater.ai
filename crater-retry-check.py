#!/usr/bin/env python3
"""Read-only check for the crater-2 Retry (code-first integration).

Usage:  python3 crater-retry-check.py /path/to/crater-2.db
It never writes to the DB (sqlite URI mode=ro).

Expected after a successful Retry, from the offline run on a copy of
crater-2.db (31 Oct 2026, adjudication stubbed to no decisions):
  snapshot v1, 96 entities, 1 relation, 6 procedures, 17 failure modes,
  18 conflicts, 3 unresolved (schematic pixel bboxes), completeness 166/166.
Live run may differ slightly: adjudication can make 0-1 small calls, so
conflict/unresolved counts may move, but fans must stay separate and a
second /integrate must NOT create a new snapshot version.
"""
import json
import sqlite3
import sys
from collections import Counter

path = sys.argv[1] if len(sys.argv) > 1 else "crater.db"
con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
con.row_factory = sqlite3.Row


def q(sql, args=()):
    return con.execute(sql, args).fetchall()


print("== job ==")
for r in q("SELECT status, stage, attempt_count, error_message FROM ingestion_jobs"):
    print(dict(r))

print("\n== products (expect test/test/test BEFORE the guarded SQL fix) ==")
for r in q("SELECT manufacturer, family, model FROM products"):
    print(dict(r))

print("\n== snapshots ==")
snaps = q("SELECT version, created_at, source_counts FROM "
          "machine_knowledge_model_snapshots ORDER BY version")
print(f"snapshot versions: {len(snaps)}")
if not snaps:
    print("NO SNAPSHOT YET - Retry has not completed integration.")
else:
    for r in snaps:
        print(f"v{r['version']} created {r['created_at']}")
        try:
            sc = json.loads(r["source_counts"]) if isinstance(r["source_counts"], str) else r["source_counts"]
        except Exception:
            sc = {}
        print("  source_counts:", json.dumps(sc, sort_keys=True)[:600])
    latest = snaps[-1]
    # Re-read full model JSON for the latest snapshot only.
    model_row = con.execute(
        "SELECT model FROM machine_knowledge_model_snapshots "
        "WHERE version=? ORDER BY version DESC LIMIT 1",
        (latest["version"],)).fetchone()
    try:
        model = json.loads(model_row[0]) if isinstance(model_row[0], str) else model_row[0]
    except Exception:
        model = {}
    for key in ("entities", "relations", "procedures", "failure_modes",
                "conflicts", "unresolved_facts"):
        print(f"  {key}: {len(model.get(key) or [])}")
    if model.get("completeness"):
        print("  completeness:", json.dumps(model["completeness"])[:400])
    if model.get("coverage"):
        print("  coverage:", json.dumps(model["coverage"])[:400])
    fans = [e for e in (model.get("entities") or [])
            if "fan" in str(e.get("name", "")).lower()]
    print(f"  fan entities (must stay separate, e.g. fan-1 / fan-44 / fan-53): "
          f"{[(e.get('id'), e.get('name')) for e in fans]}")

print("\n== unresolved facts still open, by type ==")
for r in q("SELECT fact_type, COUNT(*) c FROM unresolved_facts "
           "WHERE status='open' GROUP BY fact_type ORDER BY c DESC"):
    print(f"  {r['fact_type']}: {r['c']}")

print("\n== chunks ==")
for r in q("SELECT chunk_type, COUNT(*) c FROM chunks GROUP BY chunk_type"):
    print(f"  {r['chunk_type']}: {r['c']}")
print("\nDone. If a second /integrate created a new version, that is a "
      "regression (fingerprint check failed) - report the version count.")
