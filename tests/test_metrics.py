"""Observability milestone (plans/monitoring.md) — /metrics surface + counters.

The metrics registry is process-global and shared with every other test in
the run, so assertions are DELTA-based: sample a collector before the action,
sample again after, and assert the difference. Never assert absolute totals.
"""
from __future__ import annotations

from backend.core.metrics import (
    HTTP_REQUESTS,
    REDIS_ERRORS,
    SCRAPE_JOBS,
    path_label,
)


def _sample_value(collector, labels: dict) -> float:
    """Current value of the sample matching ``labels`` (0.0 when absent)."""
    for metric in collector.collect():
        for sample in metric.samples:
            if sample.labels == labels:
                return sample.value
    return 0.0


def test_metrics_endpoint_serves_prometheus_text(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    body = resp.text
    assert "postharvest_http_requests_total" in body
    assert "postharvest_http_request_duration_seconds" in body
    assert "postharvest_scrape_jobs_total" in body
    assert "postharvest_redis_errors_total" in body
    # Process collector is registered alongside the app metrics.
    assert "process_resident_memory_bytes" in body


def test_http_counter_increments_by_request(client):
    before = _sample_value(
        HTTP_REQUESTS, {"method": "GET", "path": "/api/health", "status": "200"}
    )
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/health").status_code == 200
    after = _sample_value(
        HTTP_REQUESTS, {"method": "GET", "path": "/api/health", "status": "200"}
    )
    assert after - before == 2.0


def test_path_label_collapses_uuid_and_id_segments(client):
    # A 404 on a UUID-bearing path must still be counted under the {uuid}
    # family, never a per-instance label.
    uuid = "123e4567-e89b-12d3-a456-426614174000"
    client.get(f"/api/jobs/{uuid}")
    client.get(f"/api/jobs/42")
    label_uuid = path_label(f"/api/jobs/{uuid}")
    label_id = path_label("/api/jobs/42")
    assert label_uuid == "/api/jobs/{uuid}"
    assert label_id == "/api/jobs/{id}"
    for metric in HTTP_REQUESTS.collect():
        for sample in metric.samples:
            if sample.labels.get("path") == "/api/jobs/{uuid}":
                return
    raise AssertionError("expected a sample under the collapsed {uuid} family")


def test_scrape_and_redis_counters_exposed_and_deltaable():
    before_failed = _sample_value(SCRAPE_JOBS, {"result": "failed"})
    before_errors = _sample_value(REDIS_ERRORS, {})
    SCRAPE_JOBS.labels(result="failed").inc()
    REDIS_ERRORS.inc()
    assert _sample_value(SCRAPE_JOBS, {"result": "failed"}) - before_failed == 1.0
    assert _sample_value(REDIS_ERRORS, {}) - before_errors == 1.0