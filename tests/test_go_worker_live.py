"""Opt-in LIVE gate for the deployment go worker (finalplanv2 §5/§15).

Skipped by default — the hermetic suite must never touch the network.  Set
`GO_WORKER_LIVE_URL` to point at a *running* worker to fire the real gates:

    make go-server                       # or: cd golang && go run ./cmd/server
    GO_WORKER_LIVE_URL=http://127.0.0.1:8080 pytest tests/test_go_worker_live.py

It drives the production FastAPI client (`GoWorkerClient` — the exact module
the USE_GO_WORKER seam uses) against the real binary and asserts the parses
come back byte-identical to the golden the hermetic suite proved.  Anything
the hermetic tests assert with a stand-in is re-asserted here for real: the
health probes, the Parse RPC, and the clock-masked golden equality.

The one divergence is `scraped_at` (the worker stamps its own process clock;
the goldens freeze a fixed clock) — same masking convention as
`test_go_worker_seam.py`, everything else must be byte-exact.
"""
from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

from backend.services.go_worker import GoWorkerClient

LIVE_URL = os.environ.get("GO_WORKER_LIVE_URL", "")

pytestmark = pytest.mark.skipif(
    not LIVE_URL,
    reason="set GO_WORKER_LIVE_URL=http://127.0.0.1:8080 to run the live gate",
)

REPO = Path(__file__).resolve().parent.parent
DOM_HTML = (REPO / "golang" / "parser" / "testdata" / "dom_sample.html") \
    .read_text(encoding="utf-8")
GOLDEN = (REPO / "golang" / "httpapi" / "testdata"
          / "parse_response_dom_sample.json")
GOLDEN_PAYLOAD = __import__("json").loads(GOLDEN.read_text(encoding="utf-8"))

TARGET = "https://www.facebook.com/acmewidgets"


def _mask(posts):
    return [{k: v for k, v in p.items() if k != "scraped_at"} for p in posts]


class TestLiveWorker:
    def test_health_endpoints(self):
        for endpoint in ("/healthz", "/readyz"):
            resp = httpx.get(f"{LIVE_URL}{endpoint}", timeout=5)
            assert resp.status_code == 200
            assert resp.text.strip() == "ok"

    def test_parse_returns_golden_bytes(self):
        result = GoWorkerClient(LIVE_URL).parse(
            target_url=TARGET, handle="acmewidgets", raw_html=DOM_HTML,
            idempotency_key="job_7:acmewidgets_1")

        assert result.errors == GOLDEN_PAYLOAD["errors"] == []
        assert _mask(result.posts) == _mask(GOLDEN_PAYLOAD["posts"])
        assert result.page_name == "Acme Widgets"
        assert result.page_id == "424242"
        # the response was actually produced by a process clock
        assert result.posts[0]["scraped_at"].endswith("+00:00")

    def test_malformed_request_is_400_json(self):
        resp = httpx.post(f"{LIVE_URL}/v1/parse", content=b"not json",
                          timeout=5)
        assert resp.status_code == 400
        # the same error envelope every handler path emits
        assert "error" in resp.json()

    def test_method_not_allowed_is_405_json(self):
        resp = httpx.get(f"{LIVE_URL}/v1/parse", timeout=5)
        assert resp.status_code == 405
        assert "error" in resp.json()