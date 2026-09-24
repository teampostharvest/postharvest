"""Prometheus metrics for the backend (plans/monitoring.md milestone).

Metrics live on a private ``CollectorRegistry`` so tests never contend with
the global default registry. The HTTP surface is exported at ``GET /metrics``
— internal-only by construction: nginx never proxies it, and no compose
service publishes the backend port.

Process/system collectors are registered alongside the app metrics so a
scrape yields process memory/CPU too (equivalent of the default registry's
output, but isolated).
"""
from __future__ import annotations

import re
import time
from typing import Any

from prometheus_client import (
    GCCollector,
    CollectorRegistry,
    Counter,
    Histogram,
    PlatformCollector,
    ProcessCollector,
    generate_latest,
)

REGISTRY = CollectorRegistry()

ProcessCollector(registry=REGISTRY)
GCCollector(registry=REGISTRY)
PlatformCollector(registry=REGISTRY)

HTTP_REQUESTS = Counter(
    "postharvest_http_requests_total",
    "HTTP requests served, labelled by method / path family / status.",
    ("method", "path", "status"),
    registry=REGISTRY,
)
HTTP_REQUEST_DURATION = Histogram(
    "postharvest_http_request_duration_seconds",
    "HTTP request latency in seconds.",
    ("method", "path"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
    registry=REGISTRY,
)
SCRAPE_JOBS = Counter(
    "postharvest_scrape_jobs_total",
    "Scrape jobs reaching a terminal state (done | cancelled | failed).",
    ("result",),
    registry=REGISTRY,
)
REDIS_ERRORS = Counter(
    "postharvest_redis_errors_total",
    "Redis operation failures observed at the client boundary.",
    registry=REGISTRY,
)

_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
)
#: Bounded cardinality for the http path label. Distinct paths beyond this
#: ceiling collapse to "other" so an adversarial/unbounded path cannot blow
#: up label space (the dashboard only ever groups by path family).
_PATH_LABEL_CAP = 500
_path_labels: dict[str, str] = {}


def path_label(path: str) -> str:
    """Normalize a request path into a bounded label.

    UUID segments become ``{uuid}`` and integer segments ``{id}`` so job ids
    and capture ids never create per-instance label values.
    """
    key = _UUID_RE.sub("{uuid}", path)
    key = re.sub(r"/\d+", "/{id}", key)
    cached = _path_labels.get(key)
    if cached is not None:
        return cached
    if len(_path_labels) >= _PATH_LABEL_CAP:
        return "other"
    _path_labels[key] = key
    return key


class MetricsMiddleware:
    """ASGI middleware recording request count + latency per path family."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "GET")
        raw_path = scope.get("path", "/")
        started = time.perf_counter()
        status = 0
        first_send = send

        async def _send(message: dict) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message.get("status", 0)
            await first_send(message)

        try:
            await self.app(scope, receive, _send)
        finally:
            label = path_label(raw_path)
            HTTP_REQUESTS.labels(method=method, path=label, status=str(status)).inc()
            HTTP_REQUEST_DURATION.labels(method=method, path=label).observe(
                time.perf_counter() - started
            )


def render_metrics() -> bytes:
    """Serialized Prometheus text exposition for this process."""
    return generate_latest(REGISTRY)