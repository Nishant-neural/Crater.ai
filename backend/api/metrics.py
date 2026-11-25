"""Prometheus metrics from the gateway shim (gateway audit §2/§7).

Request count, latency, and status by route and upstream; /ready
dependency state is exposed by /ready itself, not here. OpenTelemetry
tracing is DEFER to Phase 5 (contracts §1.5).
"""
from __future__ import annotations

from fastapi import Response
from prometheus_client import (
    CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest,
)

_REQUESTS = Counter(
    "crater_gateway_requests_total",
    "Requests seen by the gateway shim",
    ["route", "method", "status"],
)
_LATENCY = Histogram(
    "crater_gateway_request_seconds",
    "Request latency at the gateway shim",
    ["route", "method"],
)


def observe(route: str, method: str, status: int, seconds: float) -> None:
    _REQUESTS.labels(route=route, method=method, status=str(status)).inc()
    _LATENCY.labels(route=route, method=method).observe(seconds)


def metrics_response() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
