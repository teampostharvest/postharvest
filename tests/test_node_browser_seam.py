"""Hermetic tests for the FastAPI -> node browser-mode seam (finalplanv2
§4 browser-mode / §12, Slice D).

Everything here stays off the network and off a real browser:
* unit tests drive the seam client through an ``httpx.MockTransport``;
* job-level tests point ``NODE_BASE_URL`` at a loopback
  ``ThreadingHTTPServer`` standing in for the node service.

The job-level tests exercise the REAL scrape pipeline (scrape_source_browser
-> ``_browser_fetch`` dispatch -> node stand-in -> parse_browser_page ->
normalize -> dedup -> storage) with ``USE_NODE_BROWSER`` on, which is exactly
the parity mode the plan demands proven before the live flag flip.  The
golden ``fetch_response.browser.json`` fixture is served byte-for-byte, so the
hermetic suite doubles as a cross-language contract check.
"""

from __future__ import annotations

import base64
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List

import httpx
import pytest

from backend.core.config import get_settings
from backend.scraper.errors import (
    ExtractionFailure,
    InvalidUrl,
    OperationCancelled,
    Timeout,
)
from backend.services.node_browser import (
    NodeBrowserClient,
    fetch_browser_via_node,
    serialize_cookies,
    deserialize_cookies,
)

from helpers import wait_for_job

FIXTURES = Path(__file__).resolve().parent.parent / "shared" / "fixtures"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# The golden browser-mode response (FetchResponse with browser_stats).
BROWSER_RESPONSE = json.loads(
    (FIXTURES / "fetch_response.browser.json").read_text()
)

# Session jar shape (Playwright dicts) that serializes to exactly the two
# cookie lines in fetch_request.browser.json.
SESSION_JAR = [
    {
        "name": "c_user",
        "value": "100000000000001",
        "domain": ".facebook.com",
        "path": "/",
        "expires": -1,
        "httpOnly": True,
        "secure": False,
        "sameSite": "Lax",
    },
    {
        "name": "xs",
        "value": "abc123def456",
        "domain": ".facebook.com",
        "path": "/",
        "expires": -1,
        "httpOnly": True,
        "secure": True,
        "sameSite": "Lax",
    },
]

SESSION_LINES = [
    "c_user=100000000000001; Domain=.facebook.com; Path=/; HttpOnly",
    "xs=abc123def456; Domain=.facebook.com; Path=/; Secure; HttpOnly",
]


def _browser_success(url: str) -> dict:
    return {
        **BROWSER_RESPONSE,
        "final_url": url,
    }


def _browser_error(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def _mock_client(handler) -> NodeBrowserClient:
    return NodeBrowserClient(
        "http://node:9334",
        transport=httpx.MockTransport(handler),
    )


class _Server(BaseHTTPRequestHandler):
    """Minimal node stand-in serving POST /fetch browser responses.

    Responds with the golden ``fetch_response.browser.json`` regardless of
    payload, and records every request for assertion.
    """

    served: List[dict] = []
    daemon_threads = True

    def do_POST(self):  # noqa: N802 - http.server API
        length = int(self.headers.get("content-length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            body = {}
        type(self).served.append(dict(body))
        payload = json.dumps(_browser_success(body.get("target_url", ""))).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # silence request logging
        pass


class _FailingServer(_Server):
    """Node stand-in that always answers 500 browser_launch_failed."""

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("content-length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            body = {}
        type(self).served.append(dict(body))
        payload = json.dumps(
            _browser_error("browser_launch_failed", "chromium missing")
        ).encode()
        self.send_response(500)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture
def fake_node_browser():
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


@pytest.fixture
def fake_node_browser_failing():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FailingServer)
    _FailingServer.served = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        yield base_url, _FailingServer.served
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
def enable_node_browser(monkeypatch):
    """Turn the browser seam on (USE_NODE_BROWSER)."""
    s = get_settings()
    monkeypatch.setattr(s, "use_node_browser", True)
    return s


# ---------------------------------------------------------------------------
# serialize_cookies (RFC 6265 lines for FetchRequest.cookies)
# ---------------------------------------------------------------------------


class TestSerializeCookies:
    def test_jar_matches_golden_fixture_lines(self):
        assert serialize_cookies(SESSION_JAR) == SESSION_LINES

    def test_omits_absent_flags_and_session_expiry(self):
        lines = serialize_cookies(
            [{"name": "m", "value": "1", "domain": ".facebook.com",
              "path": "/", "expires": -1, "httpOnly": False, "secure": False}]
        )
        assert lines == ["m=1; Domain=.facebook.com; Path=/"]

    def test_non_session_expiry_is_serialized(self):
        lines = serialize_cookies(
            [{"name": "datr", "value": "abc", "domain": ".facebook.com",
              "path": "/", "expires": 1758432025, "secure": True}]
        )
        assert lines == [
            "datr=abc; Domain=.facebook.com; Path=/; Expires=1758432025; Secure"
        ]

    def test_skips_entries_without_name_or_value(self):
        lines = serialize_cookies(
            [
                {"name": "", "value": "x", "domain": ".facebook.com"},
                {"name": "y", "value": None, "domain": ".facebook.com"},
                {"name": "k", "value": "v", "domain": ".facebook.com", "path": "/"},
            ]
        )
        assert lines == ["k=v; Domain=.facebook.com; Path=/"]

    def test_deserialize_round_trips_golden_session_lines(self):
        jar = deserialize_cookies(SESSION_LINES)
        assert serialize_cookies(jar) == SESSION_LINES

    def test_deserialize_restores_jar_shape_with_flags(self):
        jar = deserialize_cookies(
            ["xs=abc123def456; Domain=.facebook.com; Path=/; Secure; HttpOnly"]
        )
        assert jar == [
            {
                "name": "xs",
                "value": "abc123def456",
                "domain": ".facebook.com",
                "path": "/",
                "expires": -1,
                "httpOnly": True,
                "secure": True,
            }
        ]

    def test_deserialize_keeps_persistent_expiry(self):
        jar = deserialize_cookies(
            ["datr=abc; Domain=.facebook.com; Path=/; Expires=1758432025; Secure"]
        )
        assert jar == [
            {
                "name": "datr",
                "value": "abc",
                "domain": ".facebook.com",
                "path": "/",
                "expires": 1758432025,
                "httpOnly": False,
                "secure": True,
            }
        ]

    def test_deserialize_drops_malformed_lines(self):
        assert deserialize_cookies(["garbage", "", "=novalue; Domain=.facebook.com"]) == []
        assert deserialize_cookies([]) == []

    def test_deserialize_flag_booleans_follow_wire_absence(self):
        jar = deserialize_cookies(["k=v; Domain=.facebook.com; Path=/"])
        assert jar[0]["secure"] is False
        assert jar[0]["httpOnly"] is False


# ---------------------------------------------------------------------------
# Client contract (MockTransport, no I/O)
# ---------------------------------------------------------------------------


class TestNodeBrowserClient:
    def test_request_shape_and_payload_decode(self):
        seen = {}

        def handler(req: httpx.Request):
            seen["method"] = req.method
            seen["url"] = str(req.url)
            seen["body"] = json.loads(req.content.decode())
            return httpx.Response(200, json=_browser_success(_target_of(req)))

        client = _mock_client(handler)
        html, stats, updated = client.capture(
            "https://www.facebook.com/NASA",
            account_id="ops:maverick",
            scroll_rounds=12,
            max_posts=60,
            cookies=SESSION_LINES,
        )

        assert seen["method"] == "POST"
        assert seen["url"].endswith("/fetch")
        assert seen["body"] == {
            "target_url": "https://www.facebook.com/NASA",
            "mode": "browser",
            "account_id": "ops:maverick",
            "scroll_rounds": 12,
            "max_posts": 60,
            "cookies": SESSION_LINES,
        }

        # raw_payload decodes to the snapshot; the snapshot parses to the
        # canonical golden posts.
        expected_html = base64.b64decode(
            BROWSER_RESPONSE["raw_payload"]
        ).decode("utf-8")
        assert html == expected_html
        assert stats == {
            "login_wall": False,
            "feed_missing": False,
            "posts_found": 2,
        }

    def test_stats_carries_wall_and_found_from_browser_stats(self):
        def handler(req: httpx.Request):
            resp = _browser_success(_target_of(req))
            resp["browser_stats"] = {
                "login_wall": True,
                "feed_missing": True,
                "posts_found": 0,
            }
            return httpx.Response(200, json=resp)

        client = _mock_client(handler)
        _html, stats, _updated = client.capture("https://www.facebook.com/X")
        assert stats == {"login_wall": True, "feed_missing": True, "posts_found": 0}

    def test_malformed_success_raises_network_error(self):
        def handler(req: httpx.Request):
            return httpx.Response(200, json={"status_code": 200, "no": "payload"})

        client = _mock_client(handler)
        with pytest.raises(ExtractionFailure) as ei:
            client.capture("https://www.facebook.com/X")
        assert ei.value.code == "network_error"

    @pytest.mark.parametrize(
        ("code", "status", "exc_type", "exc_code"),
        [
            ("invalid_url", 400, InvalidUrl, "invalid_url"),
            ("timeout", 504, Timeout, "timeout"),
            ("browser_launch_failed", 500, ExtractionFailure, "browser_launch_failed"),
            ("browser_capture_failed", 500, ExtractionFailure, "browser_capture_failed"),
            ("network_error", 502, ExtractionFailure, "network_error"),
            ("internal_error", 500, ExtractionFailure, "internal_error"),
        ],
    )
    def test_error_envelope_mapping(self, code, status, exc_type, exc_code):
        client = _mock_client(
            lambda _r: httpx.Response(status, json=_browser_error(code, "boom"))
        )
        with pytest.raises(exc_type) as ei:
            client.capture("https://www.facebook.com/X")
        assert ei.value.code == exc_code

    def test_missing_error_envelope_raises_network_error(self):
        client = _mock_client(
            lambda _r: httpx.Response(500, text="<html>gateway hiccup</html>")
        )
        with pytest.raises(ExtractionFailure) as ei:
            client.capture("https://www.facebook.com/X")
        assert ei.value.code == "network_error"

    def test_connect_error_retries_then_network_error(self):
        attempts: List[str] = []

        def handler(req: httpx.Request):
            attempts.append(req.url.path)
            raise httpx.ConnectError("connection refused", request=req)

        client = NodeBrowserClient(
            "http://node:9334",
            transport=httpx.MockTransport(handler),
        )
        with pytest.raises(ExtractionFailure) as ei:
            client.capture("https://www.facebook.com/X")
        assert ei.value.code == "network_error"
        # initial attempt + max_retries(2) retries
        assert len(attempts) == 3

    def test_connect_error_recovers_on_retry(self):
        calls = {"n": 0}

        def handler(req: httpx.Request):
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ConnectError("boom", request=req)
            return httpx.Response(200, json=_browser_success(_target_of(req)))

        client = _mock_client(handler)
        html, _stats, _updated = client.capture("https://www.facebook.com/X")
        expected_html = base64.b64decode(
            BROWSER_RESPONSE["raw_payload"]
        ).decode("utf-8")
        assert html == expected_html
        assert calls["n"] == 2

    def test_timeout_is_not_retried(self):
        attempts: List[str] = []

        def handler(req: httpx.Request):
            attempts.append(req.url.path)
            raise httpx.TimeoutException("read timed out", request=req)

        client = _mock_client(handler)
        with pytest.raises(Timeout):
            client.capture("https://www.facebook.com/X")
        assert len(attempts) == 1


# ---------------------------------------------------------------------------
# fetch_browser_via_node (the scrape_source_browser seam)
# ---------------------------------------------------------------------------


class TestFetchBrowserViaNode:
    def test_cancel_before_fetch_raises_operation_cancelled(self):
        cancelled = threading.Event()
        cancelled.set()
        with pytest.raises(OperationCancelled):
            fetch_browser_via_node(
                "https://www.facebook.com/TestPage",
                cancel_event=cancelled,
                client=_mock_client(lambda _r: httpx.Response(200, json={})),
            )

    def test_anonymous_when_use_cookies_false(self):
        def handler(req: httpx.Request):
            body = json.loads(req.content.decode())
            assert body["cookies"] == []
            assert body["account_id"] == ""
            return httpx.Response(200, json=_browser_success(_target_of(req)))

        client = _mock_client(handler)
        html, stats = fetch_browser_via_node(
            "https://www.facebook.com/TestPage",
            use_cookies=False,
            client=client,
        )
        assert html
        assert stats["posts_found"] == 2

    def test_cookies_loaded_and_serialized_when_use_cookies(
        self, monkeypatch
    ):
        loaded = {}

        def fake_load_cookies(account_name, owner_id=None):
            loaded["account_name"] = account_name
            loaded["owner_id"] = owner_id
            return list(SESSION_JAR)

        monkeypatch.setattr(
            "backend.scraper.browser_scraper.load_cookies", fake_load_cookies
        )

        def handler(req: httpx.Request):
            body = json.loads(req.content.decode())
            assert body["cookies"] == SESSION_LINES
            assert body["account_id"] == "ops:maverick"
            return httpx.Response(200, json=_browser_success(_target_of(req)))

        client = _mock_client(handler)
        fetch_browser_via_node(
            "https://www.facebook.com/TestPage",
            account_name="maverick",
            owner_id=None,
            use_cookies=True,
            client=client,
        )
        assert loaded == {"account_name": "maverick", "owner_id": None}

    def test_no_cookies_when_jar_missing(self, monkeypatch):
        monkeypatch.setattr(
            "backend.scraper.browser_scraper.load_cookies", lambda *a, **k: None
        )

        def handler(req: httpx.Request):
            body = json.loads(req.content.decode())
            assert body["cookies"] == []
            assert body["account_id"] == ""
            return httpx.Response(200, json=_browser_success(_target_of(req)))

        client = _mock_client(handler)
        fetch_browser_via_node(
            "https://www.facebook.com/TestPage",
            account_name="ghost",
            use_cookies=True,
            client=client,
        )


# ---------------------------------------------------------------------------
# Slice C self-heal persist gate (falsy branches must never touch the jar)
# ---------------------------------------------------------------------------


class TestBrowserPersistGate:
    """Hermetic proofs that ``_persist_refreshed_cookies`` only fires for a
    genuine saved-session capture that cleared the login wall.

    The gate (``backend/services/node_browser.py``) is::

        if use_cookies and cookies and updated and not stats.get("login_wall"):
            _persist_refreshed_cookies(updated, account_name, owner_id)

    Four branches make it falsy — anonymous run, no saved jar, no refreshed
    cookies in the node response, and a login wall — and in every one the
    saved jar MUST be left untouched (an anonymous/wall capture must never
    clobber a saved session).  ``_persist_refreshed_cookies`` is recorder-
    faked so no DB access ever happens here; only "was it called at all" and
    "with exactly which refreshed lines" are proven.
    """

    def test_persist_skipped_when_anonymous_use_cookies_false(
        self, monkeypatch
    ):
        recorder = {"calls": []}
        monkeypatch.setattr(
            "backend.services.node_browser._persist_refreshed_cookies",
            lambda *a, **k: recorder["calls"].append(a),
        )

        def handler(req: httpx.Request):
            body = json.loads(req.content.decode())
            assert body["cookies"] == []
            assert body["account_id"] == ""
            return httpx.Response(200, json=_browser_success(_target_of(req)))

        client = _mock_client(handler)
        html, _stats = fetch_browser_via_node(
            "https://www.facebook.com/TestPage",
            account_name="maverick",
            owner_id=None,
            use_cookies=False,
            client=client,
        )
        assert html
        assert recorder["calls"] == []

    def test_persist_skipped_when_jar_missing(self, monkeypatch):
        monkeypatch.setattr(
            "backend.scraper.browser_scraper.load_cookies", lambda *a, **k: None
        )
        recorder = {"calls": []}
        monkeypatch.setattr(
            "backend.services.node_browser._persist_refreshed_cookies",
            lambda *a, **k: recorder["calls"].append(a),
        )

        def handler(req: httpx.Request):
            body = json.loads(req.content.decode())
            assert body["cookies"] == []
            assert body["account_id"] == ""
            return httpx.Response(200, json=_browser_success(_target_of(req)))

        client = _mock_client(handler)
        fetch_browser_via_node(
            "https://www.facebook.com/TestPage",
            account_name="maverick",
            owner_id=None,
            use_cookies=True,
            client=client,
        )
        assert recorder["calls"] == []

    def test_persist_skipped_when_updated_cookies_empty(self, monkeypatch):
        monkeypatch.setattr(
            "backend.scraper.browser_scraper.load_cookies",
            lambda *a, **k: list(SESSION_JAR),
        )
        recorder = {"calls": []}
        monkeypatch.setattr(
            "backend.services.node_browser._persist_refreshed_cookies",
            lambda *a, **k: recorder["calls"].append(a),
        )

        def handler(req: httpx.Request):
            body = json.loads(req.content.decode())
            assert body["cookies"] == SESSION_LINES
            success = _browser_success(_target_of(req))
            # Genuine saved-session capture, but node reports no refreshed
            # cookies: must NOT overwrite the jar with nothing new.
            success["updated_cookies"] = []
            return httpx.Response(200, json=success)

        client = _mock_client(handler)
        fetch_browser_via_node(
            "https://www.facebook.com/TestPage",
            account_name="maverick",
            owner_id=None,
            use_cookies=True,
            client=client,
        )
        assert recorder["calls"] == []

    def test_persist_skipped_on_login_wall(self, monkeypatch):
        monkeypatch.setattr(
            "backend.scraper.browser_scraper.load_cookies",
            lambda *a, **k: list(SESSION_JAR),
        )
        recorder = {"calls": []}
        monkeypatch.setattr(
            "backend.services.node_browser._persist_refreshed_cookies",
            lambda *a, **k: recorder["calls"].append(a),
        )

        def handler(req: httpx.Request):
            body = json.loads(req.content.decode())
            assert body["cookies"] == SESSION_LINES
            success = _browser_success(_target_of(req))
            # Same capture as a positive refresh, but the wall stopped us:
            # never clobber a saved jar with an anonymous capture's state.
            success["browser_stats"] = {
                **success["browser_stats"],
                "login_wall": True,
            }
            return httpx.Response(200, json=success)

        client = _mock_client(handler)
        _html, stats = fetch_browser_via_node(
            "https://www.facebook.com/TestPage",
            account_name="maverick",
            owner_id=None,
            use_cookies=True,
            client=client,
        )
        assert stats["login_wall"] is True
        assert recorder["calls"] == []

    def test_persist_called_after_wall_free_refresh(self, monkeypatch):
        monkeypatch.setattr(
            "backend.scraper.browser_scraper.load_cookies",
            lambda *a, **k: list(SESSION_JAR),
        )
        recorder = {"calls": []}
        monkeypatch.setattr(
            "backend.services.node_browser._persist_refreshed_cookies",
            lambda *a, **k: recorder["calls"].append(a),
        )

        def handler(req: httpx.Request):
            body = json.loads(req.content.decode())
            assert body["cookies"] == SESSION_LINES
            return httpx.Response(200, json=_browser_success(_target_of(req)))

        client = _mock_client(handler)
        fetch_browser_via_node(
            "https://www.facebook.com/TestPage",
            account_name="maverick",
            owner_id=None,
            use_cookies=True,
            client=client,
        )
        assert len(recorder["calls"]) == 1
        updated, account_name, owner_id = recorder["calls"][0]
        # The refreshed cookie lines match the golden fixture's updated_cookies
        # exactly — the RFC 6265 wire lines the node response carried.
        assert updated == list(BROWSER_RESPONSE["updated_cookies"])
        assert account_name == "maverick"
        assert owner_id is None


# ---------------------------------------------------------------------------
# Feature flag hygiene
# ---------------------------------------------------------------------------


class TestSeamConfig:
    def test_flag_off_by_default(self):
        s = get_settings()
        assert s.use_node_browser is False
        assert s.use_node is False
        assert s.node_base_url == "http://127.0.0.1:9334"


# ---------------------------------------------------------------------------
# Job-level parity tests (real pipeline, stand-in node, no network)
# ---------------------------------------------------------------------------


class TestSeamJobIntegration:
    def test_job_fetches_browser_through_node(
        self, authed_client, fake_node_browser, enable_node_browser, monkeypatch
    ):
        import backend.scraper.browser_scraper as bs

        base_url, served = fake_node_browser
        monkeypatch.setattr(enable_node_browser, "node_base_url", base_url)

        # Keep the retry ladder's cookie path hermetic: no saved jar to load.
        monkeypatch.setattr(
            bs, "load_cookies", lambda *a, **k: None
        )

        resp = authed_client.post("/api/scrape", json={
            "urls": ["https://www.facebook.com/NASA"],
            "use_browser": True,
        })
        assert resp.status_code in (200, 201)
        job_id = resp.json()["job_id"]
        job = wait_for_job(authed_client, job_id)
        assert job["status"] == "completed"

        # Exactly ONE /fetch call, routed to the stand-in node in browser mode.
        assert len(served) == 1, f"expected 1 /fetch call, got {served}"
        assert served[0]["target_url"] == "https://www.facebook.com/NASA"
        assert served[0]["mode"] == "browser"

        posts = authed_client.get(f"/api/jobs/{job_id}/posts").json()
        # Golden snapshot parses to 3 canonical posts (2001, 2002, DOM snap).
        assert posts["total"] == 3

    def test_browser_launch_failed_is_per_source_error(
        self, authed_client, fake_node_browser_failing,
        enable_node_browser, monkeypatch,
    ):
        import backend.scraper.browser_scraper as bs

        base_url, served = fake_node_browser_failing
        monkeypatch.setattr(enable_node_browser, "node_base_url", base_url)
        monkeypatch.setattr(bs, "load_cookies", lambda *a, **k: None)

        resp = authed_client.post("/api/scrape", json={
            "urls": ["https://www.facebook.com/NASA"],
            "use_browser": True,
        })
        job_id = resp.json()["job_id"]
        job = wait_for_job(authed_client, job_id)
        # A browser launch failure is a per-source failure, never fatal.
        assert job["status"] == "completed"
        assert len(served) == 1
        assert served[0]["mode"] == "browser"

    def test_node_outage_is_per_source_error(
        self, authed_client, enable_node_browser, monkeypatch
    ):
        import backend.scraper.browser_scraper as bs

        monkeypatch.setattr(
            enable_node_browser, "node_base_url",
            f"http://127.0.0.1:{_dead_port()}",
        )
        monkeypatch.setattr(bs, "load_cookies", lambda *a, **k: None)

        resp = authed_client.post("/api/scrape", json={
            "urls": ["https://www.facebook.com/NASA"],
            "use_browser": True,
        })
        job_id = resp.json()["job_id"]
        job = wait_for_job(authed_client, job_id)
        assert job["status"] in ("completed", "failed")
        assert job.get("errors", 0) >= 1


def _target_of(req: httpx.Request) -> str:
    """The Facebook URL a /fetch request carries in its JSON body."""
    return str(json.loads(req.content.decode()).get("target_url", ""))