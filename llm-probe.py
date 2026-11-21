"""Pinpoint why the Gemini model is unavailable in Crater.

Run from the Crater project root, with the project's venv active:
    python llm-probe.py

It uses the app's own config (backend.config, .env) and gateway, so it
tests exactly what the app sees. It NEVER prints your API key — only
whether one is present and how long it is.

What it prints:
  1. The resolved route for each task (provider + model).
  2. Whether each API key is present.
  3. One tiny real call to the diagnosis route, and either its reply
     or the exact API error (404 = wrong model id, 401/403 = bad key,
     429 = quota/rate limit).
"""
from __future__ import annotations

import sys

from backend.config import settings
from backend.llm.gateway import gateway


def _key_status(key: str) -> str:
    if not key:
        return "MISSING (empty)"
    return f"present, {len(key)} chars, starts {key[:4]}..."


def main() -> int:
    print("=== resolved routes (what the app will actually call) ===")
    for task in ("extraction", "integration", "diagnosis", "rerank"):
        route = gateway.route(task)
        print(f"{task:12s} provider={route.provider}  model={route.model}  max_tokens={route.max_tokens}")
    print()
    print("=== keys (never printed, only presence) ===")
    print(f"GEMINI_API_KEY:    {_key_status(settings.gemini_api_key)}")
    print(f"ANTHROPIC_API_KEY: {_key_status(settings.anthropic_api_key)}")
    print()
    print("=== one tiny call on the diagnosis route ===")
    try:
        text, truncated = gateway.complete_result(
            "diagnosis",
            [{"role": "user", "content": "Reply with exactly: OK"}],
            max_tokens=50,
        )
    except Exception as exc:  # noqa: BLE001 - the point is to show the error
        print(f"CALL RAISED: {type(exc).__name__}: {exc}")
        print()
        print("Reading the error above:")
        print("  404 / NOT_FOUND  -> the model id does not exist. Fix GEMINI_MODEL")
        print("                     (try gemini-2.5-flash-lite) or DIAGNOSIS_MODEL.")
        print("  401 / 403        -> bad or missing key. Fix GEMINI_API_KEY.")
        print("  429              -> quota / rate limit. Wait, or check the")
        print("                     Google Cloud quota page for this key.")
        return 1
    if text is None:
        print("CALL RETURNED None -> the provider reported unavailable WITHOUT an error.")
        print("That happens when the API key is empty (see above) or the provider")
        print("has no key configured for the resolved route's provider.")
        return 1
    print(f"CALL WORKED: reply={text!r} truncated={truncated}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
