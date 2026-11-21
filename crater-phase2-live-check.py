#!/usr/bin/env python3
"""Read-only Phase 2 live check for a Crater.ai ingestion DB.

Usage:  python3 crater-phase2-live-check.py <path-to-crater.db>
It never writes to the DB (sqlite URI mode=ro).

Run it AFTER a fresh OMNIPRO upload/job + /integrate + compile on the
Phase 2 package (~/workspace/crater-phase1/project-review-phase2-patched.zip),
NOT on a Retry job and NOT on a v12 DB. Then diagnose the p42 symptom,
navigate Diagnose -> Twin -> Diagnose, run the tournament, and send
this output plus what you saw in the app.

What Phase 2 should show (verified here: 243 passed, same 2 pre-existing
failures + 1 collection error as Phase 1; 26 new tests; 0 new LLM calls).
The Phase 1 checks are folded in at the top because the Phase 2 package
is built on the Phase 1 tree -- one fresh run confirms both:
  - Phase 1: p42/p43 chunks carry source_type="troubleshooting_table",
    p7 "spec_table"; deterministic_table_parse evidence exists; Tier-3
    pages (35-40) contribute ZERO evidence rows; case 1's failure mode
    has exactly 4 possible_causes
  - products: Harbor Freight / OMNIPRO / 220 (guard SELECT first on a
    fresh DB -- the placeholder typo varies per run; do not run a
    borrowed UPDATE blind)
  - snapshot: exactly 1 entity named "Feed Tensioner" (crater-10 v12 DB
    had 7), with merged_from recording the merged source ids and
    all source_ids kept; "Wire Feed Mechanism" 1 (case-insensitive);
    fans stay separate (>=2, distinct part numbers)
  - snapshot entities carry the new Phase 2 fields: typed_ports
    (model-level ports list is also reported), library_type bindings
    (unique-match only; ambiguous stays None, never guessed), and
    quantities split bound (value present) vs unbound (named gaps,
    no invented values)
  - digital_twins: the compiled twin's definition carries 4 transitions
    (the expert seed now resolves on the unique Feed Tensioner) --
    this script reads the stored definition, so compile + approve
    before running it
  - diagnostic_sessions: the session rows are listed so you can confirm
    the backend kept them; the actual restore is a frontend check
    (Diagnose -> Twin -> Diagnose, session still there)
"""
import json
import sqlite3
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "crater.db"
con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
con.row_factory = sqlite3.Row

print("== products ==")
for r in con.execute("SELECT manufacturer, family, model FROM products"):
    print(f"  {r['manufacturer']} / {r['family']} / {r['model']}")

print("\n== jobs ==")
for r in con.execute("SELECT status, attempt_count FROM ingestion_jobs"):
    print(f"  {dict(r)}")

# --- Phase 1 regression checks (same fresh run; Phase 2 builds on the
# Phase 1 tree, so one run should confirm both) ---------------------------
def load_extra(raw):
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return {}

print("\n== Phase 1: chunk source_type by page (p7, p42, p43) ==")
found = {7: False, 42: False, 43: False}
for r in con.execute("SELECT page_number, extra FROM chunks"):
    extra = load_extra(r["extra"])
    pages = extra.get("pages") or [r["page_number"]]
    types = set()
    if extra.get("source_type"):
        types.add(extra["source_type"])
    types.update(extra.get("source_types") or [])
    for p in pages:
        if p in found and types:
            found[p] = True
            print(f"  page {p}: {sorted(types)}")
for p, ok in found.items():
    print(f"  page {p}: {'OK source_type present' if ok else 'MISSING -- no source_type on any chunk covering this page'}")

print("\n== Phase 1: evidence by extraction_method / source_type ==")
has_evidence = con.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name='machine_knowledge_evidence'"
).fetchone()
if not has_evidence:
    print("  (no machine_knowledge_evidence table in this DB)")
else:
    for r in con.execute(
        "SELECT extraction_method, source_type, count(*) AS c FROM machine_knowledge_evidence "
        "GROUP BY extraction_method, source_type ORDER BY c DESC"
    ):
        print(f"  {dict(r)}")
    tier3 = con.execute(
        "SELECT count(*) AS c FROM machine_knowledge_evidence WHERE page IN (35,36,37,38,39,40)"
    ).fetchone()["c"]
    print(f"  Tier-3 (pp35-40) evidence rows: {tier3} (expect 0)")
    det = con.execute(
        "SELECT count(*) AS c FROM machine_knowledge_evidence "
        "WHERE extraction_method='deterministic_table_parse'"
    ).fetchone()["c"]
    print(f"  deterministic_table_parse evidence rows: {det} (expect > 0)")

row = con.execute(
    "SELECT version, created_at, source_counts FROM "
    "machine_knowledge_model_snapshots ORDER BY version DESC LIMIT 1"
).fetchone()
if not row:
    print("\nNo snapshot yet -- run /integrate first.")
    sys.exit(0)
print(f"\n== latest snapshot v{row['version']} ({row['created_at']}) ==")
print(f"  source_counts: {row['source_counts']}")
model_row = con.execute(
    "SELECT model FROM machine_knowledge_model_snapshots WHERE version=?",
    (row["version"],)).fetchone()
model = json.loads(model_row[0]) if isinstance(model_row[0], str) else model_row[0]
for key in ("entities", "relations", "procedures", "failure_modes",
            "quantities", "behaviors", "states", "ports", "simulation_rules",
            "conflicts", "unresolved_facts"):
    print(f"  {key}: {len(model.get(key) or [])}")
comp = model.get("completeness") or {}
print(f"  completeness: {comp.get('accounted_total')}/{comp.get('source_total')} "
      f"missing={comp.get('missing_count')}")
cov = model.get("coverage") or {}
if cov:
    print(f"  coverage keys: {sorted(cov.keys())}")

entities = model.get("entities") or []

def named(name):
    return [e for e in entities if e.get("name") == name]

print("\n== Phase 2 checks ==")
tensioners = named("Feed Tensioner")
print(f"  Feed Tensioner entities: {len(tensioners)} (expect 1)")
for e in tensioners:
    merged = e.get("merged_from") or []
    print(f"    {e.get('id')} source_ids={len(e.get('source_ids') or [])} "
          f"merged_from={merged if merged else '(field absent -- pre-Phase-2 DB?)'}")
mech = [e for e in entities if (e.get("name") or "").casefold() == "wire feed mechanism"]
print(f"  'Wire Feed Mechanism' entities (case-insensitive): {len(mech)} (expect 1)")
fans = named("Fan")
print(f"  Fan entities: {len(fans)} (expect >=2, separate part numbers)")
for e in fans:
    props = e.get("properties") or {}
    print(f"    {e.get('id')} part_number={props.get('part_number')!r}")

# Typed ports + library_type: distinguish field-absent (old DB) from
# field-present-but-empty (new DB, nothing bound yet).
def field_state(field):
    present = [e for e in entities if field in e]
    if not present:
        return "FIELD ABSENT on all entities -- this DB predates Phase 2"
    filled = [e for e in present if e.get(field)]
    return f"field present on {len(present)}/{len(entities)}, filled on {len(filled)}"

print(f"  typed_ports: {field_state('typed_ports')}")
typed = [e for e in entities if e.get("typed_ports")]
for e in typed[:10]:
    print(f"    {e.get('id')}: {[p.get('name') for p in e['typed_ports']]}")
print(f"  library_type: {field_state('library_type')}")
bound = [e for e in entities if e.get("library_type")]
for e in bound[:10]:
    print(f"    {e.get('id')} ({e.get('name')}) -> {e['library_type']}")

quantities = model.get("quantities") or []
q_bound = [q for q in quantities if str(q.get("value") or "").strip()]
q_unbound = [q for q in quantities if not str(q.get("value") or "").strip()]
print(f"  quantities: {len(quantities)} total, {len(q_bound)} bound "
      f"(value present), {len(q_unbound)} unbound (named gaps, expect some)")
for q in q_unbound[:10]:
    print(f"    unbound: {q.get('name')!r} source_kind={q.get('source_kind')!r}")

# Evidence precedence (MTB > OEM > generic) lives in evidence_metadata
# JSON on this schema; count it in Python so older DBs degrade gracefully.
ev_table = con.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name='machine_knowledge_evidence'"
).fetchone()
if ev_table:
    from collections import Counter
    prec = Counter()
    for (meta,) in con.execute("SELECT evidence_metadata FROM machine_knowledge_evidence"):
        try:
            m = json.loads(meta) if isinstance(meta, str) else (meta or {})
        except Exception:
            m = {}
        prec[m.get("precedence") or "(none)"] += 1
    print("  evidence precedence:", dict(prec))

CASE1_SYMPTOM = "Wire Feed Motor Runs but Wire Does Not Feed Properly"

def norm(s):
    return " ".join((s or "").lower().split())

case1 = None
for fm in model.get("failure_modes") or []:
    names = [fm.get("name") or ""] + list(fm.get("symptoms") or [])
    if any(norm(CASE1_SYMPTOM) in norm(n) or norm(n) == norm(CASE1_SYMPTOM) for n in names):
        case1 = fm
        break
if case1 is None:
    for fm in model.get("failure_modes") or []:
        blob = norm(" ".join([fm.get("name") or ""] + list(fm.get("symptoms") or [])))
        if "wire" in blob and "feed" in blob and "not feed" in blob:
            case1 = fm
            break
if case1 is None:
    print(f"  case 1 failure mode NOT FOUND for symptom: {CASE1_SYMPTOM}")
else:
    causes = case1.get("possible_causes") or []
    print(f"  case 1 failure mode: {case1.get('name')!r} id={case1.get('id')}")
    print(f"  possible_causes: {len(causes)} (expect 4)")
    for c in causes:
        print(f"    - {c}")
    ev = case1.get("evidence") or []
    det_ev = [e for e in ev if e.get("extraction_method") == "deterministic_table_parse"]
    print(f"  evidence on case 1: {len(ev)} rows, {len(det_ev)} deterministic_table_parse")

print("\n== twins (compile + approve before expecting transitions) ==")
twins = con.execute(
    "SELECT id, name, status, definition FROM digital_twins ORDER BY created_at DESC"
).fetchall()
if not twins:
    print("  no twins compiled yet")
for t in twins:
    try:
        definition = json.loads(t["definition"]) if isinstance(t["definition"], str) else t["definition"]
        n_trans = len(definition.get("transitions") or [])
        n_comp = len(definition.get("components") or [])
    except Exception as ex:
        n_trans, n_comp = f"unreadable ({ex})", "?"
    print(f"  {t['id'][:8]} {t['name']!r} status={t['status']} "
          f"components={n_comp} transitions={n_trans} (expect 4 from the seed)")

print("\n== diagnostic sessions (backend; restore itself is a frontend check) ==")
sessions = con.execute(
    "SELECT id, status, state, updated_at FROM diagnostic_sessions ORDER BY updated_at DESC"
).fetchall()
if not sessions:
    print("  no sessions yet -- diagnose the p42 symptom first")
for s in sessions:
    try:
        state = json.loads(s["state"]) if isinstance(s["state"], str) else s["state"]
        symptom = (state.get("symptoms") or ["?"])[0]
        print(f"  {s['id'][:8]} status={s['status']} turns={state.get('turns_taken')} "
              f"confidence={state.get('confidence')} updated={s['updated_at']} symptom={symptom!r}")
    except Exception as ex:
        print(f"  {s['id'][:8]} status={s['status']} (state unreadable: {ex})")

print("\nDone. Still yours to check in the app: compile (expect 1 Feed "
      "Tensioner, 4 transitions), diagnose p42 (same hypotheses as v12, "
      "per-case coverage showing all 4 causes bound), navigate Diagnose "
      "-> Twin -> Diagnose (session still there with timeline + "
      "conclusion), run the tournament (Feed Tensioner resolves, no "
      "'matches 7 components'), inspect a p42 component in Machine Model "
      "(typed ports, bound parameters, source page + precedence), and "
      "compare the extraction call count with your v12 baseline "
      "(expect fewer -- this script cannot see call counts).")
