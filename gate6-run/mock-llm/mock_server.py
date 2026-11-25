"""Mock LLM server for the Crater test path (stdlib only).

Crater's backend (~/workspace/crater-phase1/v12) points its
OpenRouterProvider at this server instead of a real LLM provider, so a
human/Muse agent can answer the backend's prompts by hand. The backend
needs NO code change: this server speaks exactly what
OpenRouterProvider.complete_result parses (see README.md).

Protocol: each POST /v1/chat/completions is assigned the next sequence
number n, its request JSON is written verbatim to queue/req-<n>.json,
and the handler polls for queue/resp-<n>.json (written by the answering
agent) for up to 170s. Queue files are the audit trail and are never
deleted or consumed twice: each request number is used once.

Run:  python3 mock_server.py
Listens on 127.0.0.1:18080.
"""
from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "127.0.0.1"
PORT = 18080
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
QUEUE_DIR = os.path.join(BASE_DIR, "queue")

# Backend's urlopen timeout is 180s; answer within 170s so the server,
# not the client, decides a timeout.
ANSWER_TIMEOUT_SECONDS = 170.0
POLL_INTERVAL_SECONDS = 0.5

_counter_lock = threading.Lock()
_next_number = 1


def _next_request_number() -> int:
    global _next_number
    with _counter_lock:
        n = _next_number
        _next_number += 1
    return n


def _req_path(n: int) -> str:
    return os.path.join(QUEUE_DIR, f"req-{n}.json")


def _resp_path(n: int) -> str:
    return os.path.join(QUEUE_DIR, f"resp-{n}.json")


def _first_user_text(messages) -> str:
    """First 80 chars of the first user message, for the log line.

    Message content may be a plain string or a list of parts
    (OpenRouterProvider sends both shapes); images carry no text.
    """
    if not isinstance(messages, list):
        return ""
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            return content[:80]
        if isinstance(content, list):
            text = "".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
            return text[:80]
    return ""


def _health() -> dict:
    received = 0
    answered = 0
    pending: list[int] = []
    if os.path.isdir(QUEUE_DIR):
        for name in os.listdir(QUEUE_DIR):
            if name.startswith("req-") and name.endswith(".json"):
                try:
                    n = int(name[4:-5])
                except ValueError:
                    continue
                received = max(received, n)
                if os.path.exists(_resp_path(n)):
                    answered += 1
                else:
                    pending.append(n)
    return {
        "status": "ok",
        "received": received,
        "answered": answered,
        "pending": sorted(pending),
    }


class MockLLMHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # --- helpers -----------------------------------------------------
    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, status: int, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        # Keep stdout for the request/response log lines only.
        pass

    # --- routes ------------------------------------------------------
    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._send_json(200, _health())
        elif self.path == "/":
            self._send_text(
                200,
                "Crater mock LLM server: POST /v1/chat/completions, GET /healthz\n",
            )
        else:
            self._send_json(404, {"error": {"message": "not found", "type": "not_found"}})

    def do_POST(self) -> None:
        if self.path != "/v1/chat/completions":
            self._send_json(404, {"error": {"message": "not found", "type": "not_found"}})
            return

        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            request_json = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._send_json(400, {"error": {"message": "invalid JSON body", "type": "bad_request"}})
            return
        if not isinstance(request_json, dict):
            self._send_json(400, {"error": {"message": "request body must be a JSON object", "type": "bad_request"}})
            return

        n = _next_request_number()
        os.makedirs(QUEUE_DIR, exist_ok=True)
        # Write the request VERBATIM (raw bytes as received, not re-serialized).
        with open(_req_path(n), "wb") as f:
            f.write(raw)

        model = request_json.get("model", "?")
        user_preview = _first_user_text(request_json.get("messages"))
        print(f"[req {n}] model={model} user={user_preview!r}", flush=True)

        # Poll for the answer file. Sleep between polls; never hold a lock
        # while waiting, so concurrent requests are not blocked.
        deadline = time.monotonic() + ANSWER_TIMEOUT_SECONDS
        resp_path = _resp_path(n)
        while True:
            if os.path.exists(resp_path):
                break
            if time.monotonic() >= deadline:
                print(f"[req {n}] TIMEOUT after {ANSWER_TIMEOUT_SECONDS:.0f}s, no resp file", flush=True)
                self._send_json(504, {"error": {
                    "message": f"mock answer timeout for request {n}",
                    "type": "mock_timeout",
                }})
                return
            time.sleep(POLL_INTERVAL_SECONDS)

        try:
            with open(resp_path, "r", encoding="utf-8") as f:
                resp_json = json.load(f)
            content = resp_json["content"]
            if not isinstance(content, str):
                raise ValueError("content is not a string")
        except (ValueError, KeyError, OSError) as exc:
            print(f"[req {n}] BAD resp file: {exc}", flush=True)
            self._send_json(500, {"error": {
                "message": f"bad mock response file for request {n}",
                "type": "mock_bad_response",
            }})
            return

        print(f"[req {n}] answered ({len(content)} chars)", flush=True)
        self._send_json(200, {
            "choices": [{
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
        })


def main() -> None:
    os.makedirs(QUEUE_DIR, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), MockLLMHandler)
    print(f"mock LLM server listening on http://{HOST}:{PORT} (queue: {QUEUE_DIR})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("shutting down", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
