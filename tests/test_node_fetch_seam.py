"""Hermetic tests for the FastAPI -> node fetch seam (finalplanv2 §14).

Everything here stays off the network:
* unit tests drive the seam client through an ``httpx.MockTransport``;
* job-level tests point ``NODE_BASE_URL`` at a loopback
  ``ThreadingHTTPServer`` standing in for the node service (or at a
  closed port for the outage/fault-isolation case).

The job-level tests exercise the REAL scrape pipeline (scrape_source -> seam
-> node stand-in -> parse -> normalize -> dedup -> storage) with
``USE_NODE`` on, which is exactly the parity mode the plan demands
proven before the live flag flip.
"""

from __future__ import annotations

import base64
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import List

import httpx
import pytest

from backend.core.config import get_settings
from backend.scraper.errors import (
    AuthRequired,
    ExtractionFailure,
    InvalidUrl,
    OperationCancelled,
    PageUnavailable,
    RateLimited,
    Timeout,
    UnsupportedUrl,
)
from backend.services.node_fetch import NodeFetchClient, fetch_page_via_node

from helpers import wait_for_job

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Parseable by the real parser: falls through to script-based extraction,
# which yields exactly two posts (post_id 1001 / 1002).
FAKE_PAGE_HTML = """
<html><body>
<div data-testid="test">
  <script>
    window.__additionalDataLoaded("12345", {"require": [
      {"__bbox": {"result": {"data": {"nodes": [
        {"__typename": "Post", "id": "post_1001", "post_id": "1001",
         "creation_time": 1700000000,
         "message": "Test post from page A",
         "from": {"name": "Test Page", "id": "page_001"}},
        {"__typename": "Post", "id": "post_1002", "post_id": "1002",
         "creation_time": 1700001000,
         "message": "Second test post",
         "from": {"name": "Test Page", "id": "page_001"}}
      ]}}}
    ]}]);
  </script>
</div>
</body></html>
"""

WALL_HTML = (
    "<html><head><title>Log In to Facebook</title></head><body>"
    "<p>You must log in to continue.</p>"
    "<p>" + ("Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 8)
    + "</p></body></html>"
)


def _node_success(url: str, html: str = FAKE_PAGE_HTML) -> dict:
    return {
        "status_code": 200,
        "final_url": url,
        "content_type": "text/html; charset=utf-8",
        "raw_payload": base64.b64encode(html.encode("utf-8")).decode("ascii"),
        "fetched_at_ms": int(time.time() * 1000),
    }


def _target_of(req: httpx.Request) -> str:
    """The Facebook URL a /fetch request carries in its JSON body."""
    return str(json.loads(req.content.decode()).get("target_url", ""))


def _node_error(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def _mock_client(handler) -> NodeFetchClient:
    return NodeFetchClient(
        "http://node:9334",
        transport=httpx.MockTransport(handler),
    )


class _Server(BaseHTTPRequestHandler):
    """Minimal node stand-in serving POST /fetch from FAKE_PAGE_HTML."""

    served: List[dict] = []
    daemon_threads = True

    def do_POST(self):  # noqa: N802 - http.server API
        length = int(self.headers.get("content-length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            body = {}
        type(self).served.append(dict(body))
        payload = json.dumps(_node_success(body.get("target_url", ""))).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # silence request logging
        pass


@pytest.fixture
def fake_node():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Server)
    _Server.served = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        yield base_url, _Server.served
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _dead_port() -> int:
    """Grab an ephemeral port and immediately close it (connect will refuse)."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def enable_node(monkeypatch):
    """Turn the seam on.  Call ``patch_base(url)`` inside the test to point the
    client at a stand-in server; defaults to the config default."""
    s = get_settings()
    monkeypatch.setattr(s, "use_node", True)
    return s


# ---------------------------------------------------------------------------
# Client contract (MockTransport, no I/O)
# ---------------------------------------------------------------------------


class TestNodeFetchClient:
    def test_request_shape_and_payload_decode(self):
        seen = {}

        def handler(req: httpx.Request):
            seen["method"] = req.method
            seen["url"] = str(req.url)
            seen["body"] = json.loads(req.content.decode())
            return httpx.Response(
                200, json=_node_success(_target_of(req))
            )

        client = _mock_client(handler)
        result = client.fetch("https://www.facebook.com/NASA")

        assert seen["method"] == "POST"
        assert seen["url"].endswith("/fetch")
        assert seen["body"] == {
            "target_url": "https://www.facebook.com/NASA",
            "mode": "http",
        }
        assert result.html == FAKE_PAGE_HTML
        assert result.status_code == 200
        assert result.final_url == "https://www.facebook.com/NASA"

    def test_malformed_success_raises_network_error(self):
        client = _mock_client(lambda _r: httpx.Response(200, json={"junk": 1}))
        with pytest.raises(ExtractionFailure) as ei:
            client.fetch("https://www.facebook.com/X")
        assert ei.value.code == "network_error"

    @pytest.mark.parametrize(
        ("code", "status", "exc_type", "exc_code"),
        [
            ("invalid_url", 400, InvalidUrl, "invalid_url"),
            ("robots_disallowed", 403, UnsupportedUrl, "robots_disallowed"),
            ("rate_limited", 429, RateLimited, "rate_limited"),
            ("page_unavailable", 502, PageUnavailable, "page_unavailable"),
            ("timeout", 504, Timeout, "timeout"),
            ("network_error", 502, ExtractionFailure, "network_error"),
            ("body_too_large", 413, ExtractionFailure, "body_too_large"),
            ("internal_error", 500, ExtractionFailure, "internal_error"),
        ],
    )
    def test_error_envelope_mapping(self, code, status, exc_type, exc_code):
        client = _mock_client(
            lambda _r: httpx.Response(status, json=_node_error(code, "boom"))
        )
        with pytest.raises(exc_type) as ei:
            client.fetch("https://www.facebook.com/X")
        assert ei.value.code == exc_code

    def test_missing_error_envelope_raises_network_error(self):
        client = _mock_client(
            lambda _r: httpx.Response(500, text="<html>gateway hiccup</html>")
        )
        with pytest.raises(ExtractionFailure) as ei:
            client.fetch("https://www.facebook.com/X")
        assert ei.value.code == "network_error"

    def test_connect_error_retries_then_network_error(self):
        attempts: List[str] = []

        def handler(req: httpx.Request):
            attempts.append(req.url.path)
            raise httpx.ConnectError("connection refused", request=req)

        client = NodeFetchClient(
            "http://node:9334",
            transport=httpx.MockTransport(handler),
        )
        with pytest.raises(ExtractionFailure) as ei:
            client.fetch("https://www.facebook.com/X")
        assert ei.value.code == "network_error"
        # initial attempt + max_retries(2) retries
        assert len(attempts) == 3

    def test_connect_error_recovers_on_retry(self):
        calls = {"n": 0}

        def handler(req: httpx.Request):
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ConnectError("boom", request=req)
            return httpx.Response(200, json=_node_success(_target_of(req)))

        client = _mock_client(handler)
        result = client.fetch("https://www.facebook.com/X")
        assert result.html == FAKE_PAGE_HTML
        assert calls["n"] == 2

    def test_timeout_is_not_retried(self):
        attempts: List[str] = []

        def handler(req: httpx.Request):
            attempts.append(req.url.path)
            raise httpx.TimeoutException("read timed out", request=req)

        client = _mock_client(handler)
        with pytest.raises(Timeout):
            client.fetch("https://www.facebook.com/X")
        assert len(attempts) == 1


# ---------------------------------------------------------------------------
# Variant ladder parity (mirrors Fetcher.fetch_page in fetcher.py)
# ---------------------------------------------------------------------------


class TestFetchPageViaNode:
    def test_first_variant_ok_returns_canonical(self):
        client = _mock_client(
            lambda req: httpx.Response(200, json=_node_success(_target_of(req)))
        )
        result = fetch_page_via_node(
            "https://www.facebook.com/TestPage", client=client
        )
        assert result.variant == "canonical"
        assert result.html == FAKE_PAGE_HTML
        assert client.requests_made == 1

    def test_wall_then_mbasic_ok_falls_through(self):
        def handler(req: httpx.Request):
            target = _target_of(req)
            if "mbasic" in target:
                return httpx.Response(200, json=_node_success(target))
            return httpx.Response(
                200,
                json=_node_success(target, html=WALL_HTML),
            )

        client = _mock_client(handler)
        result = fetch_page_via_node(
            "https://www.facebook.com/TestPage", client=client
        )
        assert result.variant == "mbasic"
        assert client.requests_made == 2

    def test_all_walls_raise_auth_required(self):
        client = _mock_client(
            lambda req: httpx.Response(
                200, json=_node_success(_target_of(req), html=WALL_HTML)
            )
        )
        with pytest.raises(AuthRequired) as ei:
            fetch_page_via_node("https://www.facebook.com/TestPage", client=client)
        assert ei.value.code == "auth_required"

    def test_robots_disallowed_all_raises_auth_required(self):
        client = _mock_client(
            lambda _r: httpx.Response(
                403, json=_node_error("robots_disallowed", "robots.txt")
            )
        )
        with pytest.raises(AuthRequired) as ei:
            fetch_page_via_node("https://www.facebook.com/TestPage", client=client)
        assert "robots_disallowed" in ei.value.message

    def test_rate_limited_raises_after_all_variants(self):
        # Python parity: a rate-limit on one variant does NOT stop the ladder;
        # every variant is probed and RateLimited is raised only once they all
        # failed (fetcher.py:420/426).
        client = _mock_client(
            lambda _r: httpx.Response(
                429, json=_node_error("rate_limited", "too fast")
            )
        )
        with pytest.raises(RateLimited):
            fetch_page_via_node("https://www.facebook.com/TestPage", client=client)
        assert client.requests_made == 3  # canonical + mbasic + mobile

    def test_not_found_all_raises_page_unavailable(self):
        client = _mock_client(
            lambda _r: httpx.Response(
                502, json=_node_error("page_unavailable", "gone")
            )
        )
        with pytest.raises(PageUnavailable):
            fetch_page_via_node("https://www.facebook.com/TestPage", client=client)

    def test_timeout_is_hard_stop(self):
        client = _mock_client(
            lambda _r: httpx.Response(504, json=_node_error("timeout", "slow"))
        )
        with pytest.raises(Timeout):
            fetch_page_via_node("https://www.facebook.com/TestPage", client=client)
        # canonical 504 -> re-raised; mbasic/mobile never attempted
        assert client.requests_made == 1

    def test_cancel_before_fetch_raises_operation_cancelled(self):
        client = _mock_client(
            lambda _r: httpx.Response(200, json=_node_success("x"))
        )
        cancelled = threading.Event()
        cancelled.set()
        with pytest.raises(OperationCancelled):
            fetch_page_via_node(
                "https://www.facebook.com/TestPage",
                client=client,
                cancel_event=cancelled,
            )
        assert client.requests_made == 0


# ---------------------------------------------------------------------------
# Feature flag hygiene
# ---------------------------------------------------------------------------


class TestSeamConfig:
    def test_flag_off_by_default(self):
        s = get_settings()
        assert s.use_node is False
        assert s.node_base_url == "http://127.0.0.1:9334"


# ---------------------------------------------------------------------------
# Job-level parity tests (real pipeline, stand-in node, no network)
# ---------------------------------------------------------------------------


class TestSeamJobIntegration:
    def test_job_fetches_through_node(
        self, authed_client, fake_node, enable_node, monkeypatch
    ):
        base_url, served = fake_node
        monkeypatch.setattr(enable_node, "node_base_url", base_url)

        resp = authed_client.post("/api/scrape", json={
            "urls": ["https://www.facebook.com/TestPage"],
            "use_browser": False,
        })
        assert resp.status_code in (200, 201)
        job_id = resp.json()["job_id"]
        job = wait_for_job(authed_client, job_id)
        assert job["status"] == "completed"

        # Exactly ONE /fetch call (canonical variant was usable), routed to the
        # stand-in node with the http mode marker.
        assert len(served) == 1, f"expected 1 /fetch call, got {served}"
        assert served[0]["target_url"] == "https://www.facebook.com/TestPage"
        assert served[0]["mode"] == "http"

        posts = authed_client.get(f"/api/jobs/{job_id}/posts").json()
        assert posts["total"] == 2

    def test_node_outage_is_per_source_error(
        self, authed_client, enable_node, monkeypatch
    ):
        monkeypatch.setattr(
            enable_node, "node_base_url",
            f"http://127.0.0.1:{_dead_port()}",
        )

        resp = authed_client.post("/api/scrape", json={
            "urls": ["https://www.facebook.com/TestPage"],
            "use_browser": False,
        })
        job_id = resp.json()["job_id"]
        job = wait_for_job(authed_client, job_id)
        # A node outage is a per-source failure, never a fatal job error.
        assert job["status"] in ("completed", "failed")
        assert job.get("errors", 0) >= 1