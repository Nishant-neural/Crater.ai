#!/usr/bin/env python3
"""Gold-set extraction test for Crater.ai (OMNIPRO 220).

Question it answers: why do spec p7, troubleshooting table p42, and
procedure p10 return zero facts in batch ingestion, when the same content
looks plainly extractable?

Three arms, same chunks, no database writes, no re-ingest, no persistence:

  A. single      -- extract_chunk_knowledge() per chunk (full prompt, 8000 tokens)
  B. small_batch -- extract_chunk_knowledge_batch() on just the 3 gold chunks
                    (batch prompt, 8000 tokens for 3 chunks: isolates prompt shape)
  C. full_batch  -- extract_chunk_knowledge_batch() on ALL text/table chunks,
                    grouped exactly the way backend/ingestion/pipeline.py
                    groups them (batch size 18, 80,000-char budget, rowid
                    order). Only the 3 gold chunks are reported, but the
                    model sees the real batch size and token budget.
                    This arm does NOT split on failure, unlike the pipeline:
                    a failed batch is recorded as a failure, because
                    splitting would hide the batch effect this test measures.

Interpretation (decided before running, so the result is not post-hoc):
  - A succeeds, B empty  -> batch prompt shape is the cause
                           (batch prompt shows only empty lists, single
                           prompt shows full field shapes).
  - A succeeds, B succeeds, C empty -> large-batch budget / attention
                           dilution is the cause (~1000 tokens/chunk in a
                           real OMNIPRO batch, vs 8000 for a single chunk).
  - A empty too           -> neither batching nor budget: the prompt's
                           entity-attachment / do-not-infer rules are the
                           cause (spec values have no named component to
                           attach to; procedure titles sit in earlier chunks).

Gold chunks are selected by (page_number, chunk_type), NOT by chunk id:
chunk ids change on every fresh ingest, but the content is byte-identical
across crater-2.db and crater-3.db (verified 2026-11-04):
  - spec table p7:            (7,  table), 1,373 chars
  - troubleshooting table p42: (42, table), 3,294 chars
  - procedure text p10:       (10, text),  6,082 chars

Usage (from the project root, where backend/ lives and .env is loaded):
    python gold_set_test.py --db crater.db
    python gold_set_test.py --db crater.db --arms single,small_batch
    python gold_set_test.py --db crater.db --dry-run   # no LLM calls

Outputs (next to the script invocation, unless --out/--raw given):
  - gold_set_test_result.json : per-arm, per-gold item counts and names
  - gold_set_test_raw.jsonl   : every raw LLM response, so an "empty"
    result can be checked against what the model actually returned
    (empty lists vs truncation vs repair) instead of guessed at.

Cost: A = 3 calls, B = 1 call, C = 2 calls (25 text/table chunks in
pipeline grouping) = 6 LLM calls total, plus at most one repair call per
malformed output. At the Gemini 10 RPM limiter in gateway.py that is
roughly a minute of spacing plus model time -- not a re-ingest.

This script never writes to the database (opened mode=ro) and never
calls persist_extraction / persist_machine_knowledge.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

# Gold chunks: (page_number, chunk_type) -> expected char count.
GOLD = {
    "spec_table_p7": (7, "table", 1373),
    "troubleshooting_table_p42": (42, "table", 3294),
    "procedure_text_p10": (10, "text", 6082),
}
GOLD_ORDER = ["spec_table_p7", "troubleshooting_table_p42", "procedure_text_p10"]

# Pipeline grouping, copied from backend/ingestion/pipeline.py.
BATCH_SIZE = 18
BATCH_MAX_CHARS = 80000


def load_chunks(db_path: str):
    """Read text/table chunks read-only, in pipeline (rowid) order."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT id, chunk_type, page_number, content FROM chunks "
            "WHERE chunk_type IN ('text', 'table') ORDER BY rowid"
        ).fetchall()
    finally:
        con.close()
    return [
        {"id": r[0], "chunk_type": r[1], "page_number": r[2], "content": r[3]}
        for r in rows
    ]


def find_gold(all_chunks):
    gold = {}
    for name, (page, ctype, expected_chars) in GOLD.items():
        matches = [
            c for c in all_chunks
            if c["page_number"] == page and c["chunk_type"] == ctype
        ]
        if not matches:
            raise SystemExit(f"Gold chunk not found: {name} (page {page}, {ctype})")
        if len(matches) > 1:
            print(f"WARNING: {name} has {len(matches)} matches; using the first.")
        chunk = matches[0]
        if len(chunk["content"]) != expected_chars:
            print(
                f"WARNING: {name} is {len(chunk['content'])} chars, "
                f"expected {expected_chars} -- content may have changed "
                f"since the gold set was frozen; result still comparable "
                f"within this run, but not against earlier runs."
            )
        gold[name] = chunk
    return gold


def count_items(legacy, model) -> dict:
    """Count every collection the pipeline checks for 'has facts'."""
    return {
        "legacy_components": len(legacy.components or []),
        "legacy_relationships": len(legacy.relationships or []),
        "legacy_procedures": len(legacy.procedures or []),
        "entities": len(model.entities or []),
        "relations": len(model.relations or []),
        "ports": len(model.ports or []),
        "quantities": len(model.quantities or []),
        "states": len(model.states or []),
        "events": len(model.events or []),
        "behaviors": len(model.behaviors or []),
        "constraints": len(model.constraints or []),
        "procedures": len(model.procedures or []),
        "failure_modes": len(model.failure_modes or []),
        "component_names": [c.name for c in (legacy.components or [])],
        "failure_mode_names": [f.name for f in (model.failure_modes or [])],
        "procedure_names": [p.name for p in (legacy.procedures or [])]
        or [p.name for p in (model.procedures or [])],
        "quantity_sample": [
            {"name": q.name, "value": q.value, "unit": q.unit}
            for q in (model.quantities or [])[:10]
        ],
        "entity_names": [e.name for e in (model.entities or [])],
    }


def total(counts: dict) -> int:
    keys = [
        "legacy_components", "legacy_relationships", "legacy_procedures",
        "entities", "relations", "ports", "quantities", "states", "events",
        "behaviors", "constraints", "procedures", "failure_modes",
    ]
    return sum(counts[k] for k in keys)


def group_like_pipeline(all_chunks):
    """Reproduce pipeline.py batch grouping (size + char budget)."""
    batches, batch, prompt_chars = [], [], 0
    for chunk in all_chunks:
        added = len(chunk["content"]) + len(chunk["id"]) * 2 + 100 + (2 if batch else 0)
        if batch and (len(batch) >= BATCH_SIZE or prompt_chars + added > BATCH_MAX_CHARS):
            batches.append(batch)
            batch, prompt_chars = [], 0
            added = len(chunk["content"]) + len(chunk["id"]) * 2 + 100
        batch.append(chunk)
        prompt_chars += added
    if batch:
        batches.append(batch)
    return batches


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="crater.db", help="SQLite DB, opened read-only")
    ap.add_argument("--out", default="gold_set_test_result.json")
    ap.add_argument("--raw", default="gold_set_test_raw.jsonl")
    ap.add_argument("--arms", default="single,small_batch,full_batch",
                    help="comma list of: single,small_batch,full_batch")
    ap.add_argument("--dry-run", action="store_true",
                    help="verify gold chunks only, make no LLM calls")
    args = ap.parse_args()

    db_path = str(Path(args.db).resolve())
    print(f"DB (read-only): {db_path}")
    all_chunks = load_chunks(db_path)
    print(f"text/table chunks in DB: {len(all_chunks)}")
    gold = find_gold(all_chunks)
    for name in GOLD_ORDER:
        c = gold[name]
        print(f"gold {name}: id={c['id']} page={c['page_number']} "
              f"type={c['chunk_type']} chars={len(c['content'])}")

    if args.dry_run:
        print("dry run: no LLM calls made.")
        return 0

    # Import here so --dry-run and --help work without the project deps.
    sys.path.insert(0, str(Path.cwd()))
    from backend.knowledge.component_extraction import (
        ExtractionError,
        extract_chunk_knowledge,
        extract_chunk_knowledge_batch,
    )
    from backend.llm import gateway as gateway_mod

    # Log every raw LLM response (including repair retries) so an empty
    # result can be audited instead of guessed at.
    raw_log = open(args.raw, "w", encoding="utf-8")
    current_arm = {"name": None}
    original_complete = gateway_mod.gateway.complete

    def logged_complete(task, messages, max_tokens=None):
        started = time.monotonic()
        try:
            result = original_complete(task, messages, max_tokens=max_tokens)
            err = None
        except Exception as exc:  # logged, then re-raised for the arm to record
            result, err = None, str(exc)
        raw_log.write(json.dumps({
            "arm": current_arm["name"],
            "task": task,
            "max_tokens": max_tokens,
            "elapsed_s": round(time.monotonic() - started, 1),
            "response_chars": len(result) if result else 0,
            "error": err,
            "response": result,
        }, ensure_ascii=False) + "\n")
        raw_log.flush()
        if err is not None:
            raise RuntimeError(err)
        return result

    gateway_mod.gateway.complete = logged_complete

    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    report = {"db": db_path, "gold": {}, "arms": {}}
    for name in GOLD_ORDER:
        c = gold[name]
        report["gold"][name] = {
            "chunk_id": c["id"], "page_number": c["page_number"],
            "chunk_type": c["chunk_type"], "chars": len(c["content"]),
        }

    try:
        if "single" in arms:
            current_arm["name"] = "single"
            report["arms"]["single"] = {}
            for name in GOLD_ORDER:
                c = gold[name]
                try:
                    legacy, model = extract_chunk_knowledge(c["content"])
                    counts = count_items(legacy, model)
                    counts["total"] = total(counts)
                    counts["error"] = None
                except Exception as exc:
                    counts = {"total": 0, "error": f"{type(exc).__name__}: {exc}"}
                report["arms"]["single"][name] = counts
                print(f"single {name}: total={counts['total']} error={counts['error']}")

        if "small_batch" in arms:
            current_arm["name"] = "small_batch"
            report["arms"]["small_batch"] = {}
            try:
                results = extract_chunk_knowledge_batch(
                    [(gold[n]["id"], gold[n]["content"]) for n in GOLD_ORDER]
                )
                for name in GOLD_ORDER:
                    legacy, model = results[gold[name]["id"]]
                    counts = count_items(legacy, model)
                    counts["total"] = total(counts)
                    counts["error"] = None
                    report["arms"]["small_batch"][name] = counts
                    print(f"small_batch {name}: total={counts['total']}")
            except Exception as exc:
                for name in GOLD_ORDER:
                    report["arms"]["small_batch"][name] = {
                        "total": 0,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                print(f"small_batch FAILED: {exc}")

        if "full_batch" in arms:
            current_arm["name"] = "full_batch"
            report["arms"]["full_batch"] = {}
            batches = group_like_pipeline(all_chunks)
            print(f"full_batch: {len(batches)} batches, "
                  f"sizes={[len(b) for b in batches]}")
            gold_ids = {gold[n]["id"]: n for n in GOLD_ORDER}
            found = {}
            for bi, batch in enumerate(batches):
                try:
                    results = extract_chunk_knowledge_batch(
                        [(c["id"], c["content"]) for c in batch]
                    )
                    for cid, name in gold_ids.items():
                        if cid in results:
                            legacy, model = results[cid]
                            counts = count_items(legacy, model)
                            counts["total"] = total(counts)
                            counts["error"] = None
                            counts["batch_index"] = bi
                            found[name] = counts
                except Exception as exc:
                    # No split: a failed full batch IS the result for this arm.
                    for c in batch:
                        if c["id"] in gold_ids:
                            found[gold_ids[c["id"]]] = {
                                "total": 0, "batch_index": bi,
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                    print(f"full_batch batch {bi} FAILED: {exc}")
            for name in GOLD_ORDER:
                report["arms"]["full_batch"][name] = found.get(
                    name, {"total": 0, "error": "gold chunk not in any batch"})
                c_ = report["arms"]["full_batch"][name]
                print(f"full_batch {name}: total={c_['total']} error={c_['error']}")
    finally:
        gateway_mod.gateway.complete = original_complete
        raw_log.close()

    # Pre-registered interpretation, printed with the numbers.
    print("\n--- interpretation ---")
    for name in GOLD_ORDER:
        s = report["arms"].get("single", {}).get(name, {}).get("total")
        b = report["arms"].get("small_batch", {}).get(name, {}).get("total")
        f = report["arms"].get("full_batch", {}).get(name, {}).get("total")
        if s and not b:
            verdict = "batch prompt shape"
        elif s and b and not f:
            verdict = "large-batch budget / attention dilution"
        elif not s:
            verdict = "prompt entity-attachment / do-not-infer (not batching)"
        else:
            verdict = "no batch effect on this chunk in this run"
        print(f"{name}: single={s} small_batch={b} full_batch={f} -> {verdict}")

    Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False),
                               encoding="utf-8")
    print(f"\nreport: {args.out}\nraw LLM responses: {args.raw}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
