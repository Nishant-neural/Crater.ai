"""Gate 6: OMNIPRO 35-row oracle run through the fixed harness scorer.

The harness only registers c002-lincoln / c002b-miller as oracle cases
(benchmark/harness.py ORACLE_FILES); the OMNIPRO oracle lives in
frozen/omnipro-oracle-35.json. This driver reuses harness.live_run and
harness scoring verbatim — same turn-1, symptom-only, fault-aware
scoring as the Miller run — with the frozen OMNIPRO oracle as the
entry. It does NOT modify harness.py or the scorer.

Usage (backend + mock server already running):
  ~/workspace/crater-phase1/.venv/bin/python gate6-run/run_omnipro.py
Writes frozen/omnipro-report-phase1-2026-11-23.json
"""
import json
import sys
from pathlib import Path

REFACTOR = Path("/home/hatch/workspace/crater-refactor")
sys.path.insert(0, str(REFACTOR / "benchmark"))

import harness  # noqa: E402

BASE_URL = "http://127.0.0.1:8000"
PRODUCT_ID = "ccc3bae7-edff-42ce-8366-900d4a6523d5"
REVISION_ID = "3c246c9a-3f0b-4760-9d9b-e5811751c49f"
ORACLE_PATH = REFACTOR / "frozen" / "omnipro-oracle-35.json"
OUT_PATH = REFACTOR / "frozen" / "omnipro-report-phase1-2026-11-23.json"


def main() -> int:
    oracle = json.loads(ORACLE_PATH.read_text(encoding="utf-8"))
    report = harness.live_run(
        BASE_URL,
        PRODUCT_ID,
        REVISION_ID,
        "c001-omnipro",
        {"kind": "oracle", "data": oracle},
    )
    OUT_PATH.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nwrote {OUT_PATH}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
