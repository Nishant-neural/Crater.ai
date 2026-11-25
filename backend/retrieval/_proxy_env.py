"""Proxy-env workaround for huggingface_hub / httpx (Phase 1 Step 2).

This VM's NO_PROXY contains bracketed IPv6 literals (``[::1]``,
``[fd8b:4f84:7d32:99::1]`` ...). huggingface_hub builds a global
httpx client that parses every NO_PROXY entry as a URL pattern, and
httpx raises ``InvalidURL: Invalid port: ':1]'`` on the bracketed
entries -- even for a fully cached model load, and even with
HF_HUB_OFFLINE=1, because the client is constructed before the cache
is consulted. The result is that *any* sentence-transformers load
(reranker AND embeddings) fails in this environment.

Fix: drop the unparseable entries from NO_PROXY / no_proxy in-process
before the first hub call. Only bracketed / bare-IPv6 literals are
dropped -- every other entry (localhost, 127.0.0.1, the egress ranges)
is kept verbatim, and the HTTP(S)_PROXY settings are untouched, so
proxying behaviour for real hosts does not change. Nothing in this
codebase talks to a bracketed IPv6 loopback address.

This is an environment quirk, not a model problem. A deploy that
bakes the model files into the image and runs with a clean NO_PROXY
does not need it; the helper is a no-op there (nothing to drop).
"""
from __future__ import annotations

import os


def sanitize_proxy_env_for_httpx() -> None:
    """Remove NO_PROXY entries httpx cannot parse as URL patterns, in place."""
    for key in ("NO_PROXY", "no_proxy"):
        raw = os.environ.get(key)
        if not raw:
            continue
        kept = []
        for entry in raw.split(","):
            entry = entry.strip()
            if not entry:
                continue
            # Bracketed IPv6 ("[::1]") or bare IPv6 ("::1"): httpx's
            # URLPattern parser chokes on both. Hostnames, IPv4, and
            # host:port entries parse fine and are kept.
            if entry.startswith("[") or "::" in entry:
                continue
            kept.append(entry)
        cleaned = ",".join(kept)
        if cleaned != raw:
            os.environ[key] = cleaned
