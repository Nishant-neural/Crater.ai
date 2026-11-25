"""Shared oracle helpers — the pre-step for moving ``oracle.py`` to harness-scorer.

Twin-verifier Recheck (23 Nov 2026): ``oracle.py`` is NOT imported only by
benchmark scripts — kept production code ``backend/simulation/repair_actions.py:31``
imports ``load_fault_injections`` and ``normalize_text`` from it, so the
oracle cannot move until those two are extracted into a small shared module
inside twin-verifier. This module is that extraction: the two functions
moved verbatim (``normalize_text`` is self-contained; ``load_fault_injections``
uses only ``normalize_text`` + stdlib json). ``oracle.py`` re-exports them
so existing importers keep working, and ``repair_actions.py`` now imports
from here. The rest of ``oracle.py`` still lives in twin-verifier; its move
to harness-scorer is Step 6 prep, not this step.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def normalize_text(value: Any) -> str:
    """Join-key normalisation: lowercase, whitespace collapsed, trailing
    period stripped, curly apostrophe folded. Used for (symptom, cause)
    matching between parser output and the vocabulary."""
    text = re.sub(r"\s+", " ", str(value or "").replace("’", "'").replace("‘", "'"))
    return text.strip().rstrip(".").strip().lower()


def load_fault_injections(path: str | Path) -> dict[tuple[str, str], dict[str, Any] | None]:
    """Load the registry's reviewed ``fault_injection`` field.

    Returns a mapping keyed by (case_id, normalized registry cause
    text) -> the injection dict, or None for causes with no injection
    (settings / technique / cross-reference). This is the oracle's
    cause-to-fault mapping (Amendments item 5): the binding, not the
    LLM, maps a cause row to its injection.
    """
    with open(path, encoding="utf-8") as handle:
        registry = json.load(handle)
    mapping: dict[tuple[str, str], dict[str, Any] | None] = {}
    for case in registry.get("cases") or []:
        for cause in case.get("causes") or []:
            mapping[(case.get("id") or "", normalize_text(cause.get("cause")))] = (
                cause.get("fault_injection")
            )
    return mapping
