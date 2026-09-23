"""Hermetic tests for the FastAPI -> go compute seam (finalplanv2 §5 "Go
(Compute)" / §14 strangler-fig).

M7 flagged client: ``USE_GO_WORKER`` routes the compute slice (parse ->
normalize -> dedup) of HTTP-mode scrapes through the go worker's Phase-1
Parse RPC (``POST /v1/parse``) instead of in-process Python.  Everything
here stays off the network:

* unit tests drive the client through an ``httpx.MockTransport``;
* the pipe/parity tests run the REAL ``scrape_source`` pipeline with
  ``USE_GO_WORKER`` on, against a loopback stand-in that answers with the
  same bytes the real go worker is proven to emit (the committed
  ``golang/httpapi/testdata/parse_response_*.json`` goldens for the
  dom_sample fixture; real-pipeline generation, mirroring
  ``gen_goldens.py``, for the other fixtures).

Honesty rules enforced here:

* The flag-off path bytes are NEVER touched: every legacy ``scrape_source``
  run in this file happens with ``use_go_worker == False``.
* ``scraped_at`` is the one field the two paths can never agree on
  byte-for-byte (go stamps its own process clock inside the worker, Python
  stamps its own), so parity comparisons mask it and then require it to be a
  well-formed aware ISO timestamp on both sides.  Every parse-derived field
  is compared byte-exact.
* The dedup-count caveat is asserted, not hidden: go's ParseResponse is
  contract-fixed and carries no dedup count, so the flagged path reports
  ``duplicates_removed == 0`` while its posts stay identical to the legacy
  path's — and the stats invariant holds on both sides.
"""

from __future__ import annotations

import base64
import json
import socket
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import List, Optional

import httpx
import pytest

from backend.core.config import get_settings
from backend.scraper import scrape_source
from backend.scraper.dedup import dedup_posts
from backend.scraper.errors import (
    ExtractionFailure,
    OperationCancelled,
    Timeout,
)
from backend.scraper.normalizer import normalize_post
from backend.scraper.parser import parse_page
from backend.services.go_worker import (
    GO_PARSE_HTTP_TIMEOUT,
    GoWorkerClient,
    ParseResult,
    parse_posts_via_go,
)

TARGET = "https://www.facebook.com/acmewidgets"
JOB_KEY = "job_7:acmewidgets_1"

#: Fixed clock — must match golang/httpapi/testdata/gen_goldens.py NOW so the
#: dom_sample response produced in-test equals the committed golden bytes.
NOW = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)

REPO = Path(__file__).resolve().parent.parent
DOM_HTML = (REPO / "golang" / "parser" / "testdata" / "dom_sample.html") \
    .read_text(encoding="utf-8")

#: The committed byte-truth artifact the real go worker is proven to emit
#: for the dom_sample fixture (golang/httpapi testdata).  The stand-in serves
#: these exact bytes so the flagged FastAPI path consumes precisely what the
#: real worker returns.
DOM_GOLDEN = (REPO / "golang" / "httpapi" / "testdata"
              / "parse_response_dom_sample.json").read_text(encoding="utf-8")

#: Script-fallback page (like the node seam's) — parses to two posts.
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

#: Two *identical* DOM posts with NO permalink (so ``post_id`` is None and
#: dedup falls back to the ``fp:`` fingerprint over page|timestamp|text).
#: The legacy path reports discovered=2 / duplicates_removed=1; go pre-dedups
#: to one post before the response leaves the worker.
DUP_HTML = """
<html><body>
<div role="article">
  <abbr data-utime="1700000960">Sep 14 at 2:29 PM</abbr>
  <div data-ad-preview="message">Same old news #acme</div>
</div>
<div role="article">
  <abbr data-utime="1700000960">Sep 14 at 2:29 PM</abbr>
  <div data-ad-preview="message">Same old news #acme</div>
</div>
</body></html>
"""


def _python_response(html: str, target_url: str, handle: str,
                     now: datetime = NOW) -> str:
    """The ParseResponse bytes the real pipeline produces — mirrors
    golang/httpapi/testdata/gen_goldens.py::main exactly (parse_page ->
    normalize_post -> dedup_posts -> json.dumps, dedup keeps first)."""
    page = parse_page(html, page_url=target_url, now=now, handle=handle)
    posts = [
        normalize_post(p, page_name=page.page_name, page_id=page.page_id,
                       facebook_url=target_url, now=now)
        for p in page.posts
    ]
    kept, _duplicates = dedup_posts(posts)
    errors = [json.dumps(e, ensure_ascii=False) for e in page.post_errors]
    return json.dumps({"posts": kept, "errors": errors}, ensure_ascii=False)


def _json_response(html: str, target_url: str, handle: str) -> dict:
    return json.loads(_python_response(html, target_url, handle, NOW))


def _mask_scraped_at(posts: List[dict]) -> List[dict]:
    """Drop the cross-process clock field; assert the rest is untouched."""
    return [{k: v for k, v in p.items() if k != "scraped_at"} for p in posts]


def _mock_client(handler) -> GoWorkerClient:
    return GoWorkerClient(
        "http://127.0.0.1:8080", transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# FastAPI fetch stand-in (the scraper's other seam stays sealed)
# ---------------------------------------------------------------------------


class _FakeFetch:
    html = DOM_HTML
    final_url: Optional[str] = None
    variant = "www"
    status_code = 200


class _FakeFetcher:
    """Seals the HTTP fetch: delivers a fixed HTML payload, no network."""

    html = DOM_HTML
    final_url: Optional[str] = None

    def __init__(self, **kwargs):  # swallows delay=/proxy_url=...
        pass

    def fetch_page(self, normalized_url: str):
        fetch = _FakeFetch()
        fetch.html = type(self).html
        fetch.final_url = type(self).final_url or normalized_url
        return fetch

    def close(self):
        pass


# ---------------------------------------------------------------------------
# Go worker stand-in (the REAL python pipeline answering /v1/parse)
# ---------------------------------------------------------------------------


class _GoServer(BaseHTTPRequestHandler):
    served: List[dict] = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(length).decode("utf-8"))
        type(self).served.append(req)
        html = base64.b64decode(req["raw_payload"]).decode("utf-8")
        if html == DOM_HTML:
            # byte-truth: the exact stream the real go worker is proven to
            # emit for this fixture + clock (committed golden).
            payload = DOM_GOLDEN
        else:
            payload = _python_response(html, req["target_url"],
                                       req["handle"], NOW)
        raw = payload.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("X-PostHarvest-Cache", "miss")
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):  # silence request logging
        pass


@pytest.fixture
def fake_go():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _GoServer)
    _GoServer.served = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        yield base_url, _GoServer.served
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


# ---------------------------------------------------------------------------
# Feature flag hygiene
# ---------------------------------------------------------------------------


class TestSeamConfig:
    def test_flag_off_by_default(self):
        s = get_settings()
        assert s.use_go_worker is False
        assert s.go_worker_base_url == "http://127.0.0.1:8080"


# ---------------------------------------------------------------------------
# Client contract (MockTransport, no I/O)
# ---------------------------------------------------------------------------


class TestGoWorkerClient:
    def test_request_shape(self):
        seen = {}

        def handler(req: httpx.Request):
            seen["method"] = req.method
            seen["url"] = str(req.url)
            seen["body"] = json.loads(req.content.decode())
            return httpx.Response(
                200, json=_json_response(DOM_HTML, TARGET, "acmewidgets"),
                headers={"X-PostHarvest-Cache": "miss"})

        client = _mock_client(handler)
        result = client.parse(
            target_url=TARGET, handle="acmewidgets", raw_html=DOM_HTML,
            idempotency_key=JOB_KEY)

        assert seen["method"] == "POST"
        assert seen["url"] == "http://127.0.0.1:8080/v1/parse"
        body = seen["body"]
        assert set(body) == {
            "raw_payload", "content_type", "idempotency_key",
            "target_url", "handle",
        }
        assert body["content_type"] == "html"
        assert body["target_url"] == TARGET
        assert body["handle"] == "acmewidgets"
        assert body["idempotency_key"] == JOB_KEY
        assert base64.b64decode(body["raw_payload"]).decode("utf-8") == DOM_HTML

        assert len(result.posts) == 1
        assert result.page_name == "Acme Widgets"
        assert result.page_id == "424242"
        assert result.errors == []
        assert result.cache_hit is False

    def test_cache_hit_header_absent(self):
        def handler(req: httpx.Request):
            return httpx.Response(
                200, json=_json_response(DOM_HTML, TARGET, "acmewidgets"))

        result = _mock_client(handler).parse(
            target_url=TARGET, raw_html=DOM_HTML)
        assert result.cache_hit is None

    def test_cache_hit_header_hit(self):
        def handler(req: httpx.Request):
            return httpx.Response(
                200, json=_json_response(DOM_HTML, TARGET, "acmewidgets"),
                headers={"X-PostHarvest-Cache": "hit"})

        result = _mock_client(handler).parse(
            target_url=TARGET, raw_html=DOM_HTML)
        assert result.cache_hit is True

    def test_errors_are_decoded_back_to_dicts(self):
        err = {"post_url": "https://www.facebook.com/1",
               "code": "post_parse_failed", "message": "boom"}
        payload = {"posts": [], "errors": [json.dumps(err)]}

        def handler(req: httpx.Request):
            return httpx.Response(200, json=payload)

        result = _mock_client(handler).parse(
            target_url=TARGET, raw_html=DOM_HTML)
        assert result.posts == []
        assert result.errors == [err]
        assert result.page_name is None and result.page_id is None

    def test_malformed_success_body_is_network_error(self):
        def handler(req: httpx.Request):
            return httpx.Response(200, text="not json at all")

        with pytest.raises(ExtractionFailure) as ei:
            _mock_client(handler).parse(target_url=TARGET, raw_html=DOM_HTML)
        assert ei.value.code == "network_error"

        def bad_shape(req: httpx.Request):
            return httpx.Response(200, json={"posts": {}, "errors": []})

        with pytest.raises(ExtractionFailure) as ei:
            _mock_client(bad_shape).parse(target_url=TARGET, raw_html=DOM_HTML)
        assert ei.value.code == "network_error"

    def test_non_200_envelope(self):
        def handler(req: httpx.Request):
            return httpx.Response(503, json={"error": "circuit tripped"})

        with pytest.raises(ExtractionFailure) as ei:
            _mock_client(handler).parse(target_url=TARGET, raw_html=DOM_HTML)
        assert ei.value.code == "network_error"
        assert "HTTP 503" in str(ei.value)
        assert "circuit tripped" in str(ei.value)

    def test_non_json_error_body(self):
        def handler(req: httpx.Request):
            return httpx.Response(500, text="<html>oops</html>")

        with pytest.raises(ExtractionFailure) as ei:
            _mock_client(handler).parse(target_url=TARGET, raw_html=DOM_HTML)
        assert ei.value.code == "network_error"

    def test_connect_error_retries_then_network_error(self):
        attempts: List[str] = []

        def handler(req: httpx.Request):
            attempts.append(req.url.path)
            raise httpx.ConnectError("connection refused", request=req)

        with pytest.raises(ExtractionFailure) as ei:
            _mock_client(handler).parse(target_url=TARGET, raw_html=DOM_HTML)
        assert ei.value.code == "network_error"
        # initial attempt + max_retries(2) retries
        assert len(attempts) == 3

    def test_connect_error_recovers_on_retry(self):
        calls = {"n": 0}

        def handler(req: httpx.Request):
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ConnectError("boom", request=req)
            return httpx.Response(
                200, json=_json_response(FAKE_PAGE_HTML, TARGET, "acmewidgets"))

        result = _mock_client(handler).parse(
            target_url=TARGET, raw_html=FAKE_PAGE_HTML)
        assert len(result.posts) == 2
        assert calls["n"] == 2

    def test_timeout_is_not_retried(self):
        attempts: List[str] = []

        def handler(req: httpx.Request):
            attempts.append(req.url.path)
            raise httpx.TimeoutException("read timed out", request=req)

        with pytest.raises(Timeout):
            _mock_client(handler).parse(target_url=TARGET, raw_html=DOM_HTML)
        assert len(attempts) == 1

    def test_timeout_constant_is_generous_but_bounded(self):
        assert GO_PARSE_HTTP_TIMEOUT == 60.0

    def test_cancel_before_request_raises_cancelled(self):
        seen = {"n": 0}

        def handler(req: httpx.Request):
            seen["n"] += 1
            return httpx.Response(200, json={"posts": [], "errors": []})

        client = _mock_client(handler)
        cancelled = threading.Event()
        cancelled.set()
        with pytest.raises(OperationCancelled):
            parse_posts_via_go(
                TARGET, "acmewidgets", DOM_HTML,
                idempotency_key=JOB_KEY, cancel_event=cancelled)
        # cancelled before any network I/O
        assert seen["n"] == 0


# ---------------------------------------------------------------------------
# Seam function (parse_posts_via_go)
# ---------------------------------------------------------------------------


class TestParsePostsViaGo:
    def test_injected_client_and_key_passthrough(self):
        seen = {}

        def handler(req: httpx.Request):
            seen["body"] = json.loads(req.content.decode())
            return httpx.Response(200, json={"posts": [], "errors": []})

        result = parse_posts_via_go(
            TARGET, "acmewidgets", FAKE_PAGE_HTML,
            idempotency_key="job_9:src_2",
            client=_mock_client(handler))
        assert isinstance(result, ParseResult)
        assert result.posts == []
        assert seen["body"]["idempotency_key"] == "job_9:src_2"

    def test_default_client_uses_settings_base_url(self, monkeypatch):
        # A dead-port default URL proves the client is actually built from
        # settings (and the seam surfaced the outage as a per-source error).
        s = get_settings()
        monkeypatch.setattr(s, "go_worker_base_url",
                            f"http://127.0.0.1:{_dead_port()}")
        with pytest.raises(ExtractionFailure) as ei:
            parse_posts_via_go(TARGET, "acmewidgets", FAKE_PAGE_HTML)
        assert ei.value.code == "network_error"


# ---------------------------------------------------------------------------
# Pipe parity: REAL scrape_source, flag on vs flag off, no network
# ---------------------------------------------------------------------------


class TestScrapeSourceParity:
    def test_flagged_matches_legacy_posts_and_stats(self, fake_go, monkeypatch):
        base_url, served = fake_go
        s = get_settings()
        monkeypatch.setattr("backend.scraper.Fetcher", _FakeFetcher)

        # flag-off first: byte-for-byte the legacy pipeline
        legacy = scrape_source(TARGET)

        monkeypatch.setattr(s, "use_go_worker", True)
        monkeypatch.setattr(s, "go_worker_base_url", base_url)
        flagged = scrape_source(TARGET, idempotency_key=JOB_KEY)

        # exactly one Parse RPC with a contract-correct request
        assert len(served) == 1
        req = served[0]
        assert req["content_type"] == "html"
        assert req["target_url"] == TARGET
        assert req["handle"] == "acmewidgets"
        assert req["idempotency_key"] == JOB_KEY
        assert base64.b64decode(req["raw_payload"]).decode("utf-8") == DOM_HTML

        # every parse-derived byte equal; only the cross-process scrape clock
        # differs, and it stays a well-formed aware ISO timestamp on both.
        assert _mask_scraped_at(flagged.posts) == _mask_scraped_at(legacy.posts)
        for post in flagged.posts + legacy.posts:
            assert post["scraped_at"].endswith("+00:00")
        assert flagged.page_name == legacy.page_name == "Acme Widgets"
        assert flagged.page_id == legacy.page_id == "424242"
        assert flagged.errors == legacy.errors == []
        assert flagged.stats == legacy.stats == {
            "posts_discovered": 1, "posts_extracted": 1,
            "duplicates_removed": 0, "posts_skipped": 0, "posts_failed": 0,
        }

    def test_post_type_filter_parity(self, fake_go, monkeypatch):
        base_url, _served = fake_go
        s = get_settings()
        monkeypatch.setattr("backend.scraper.Fetcher", _FakeFetcher)

        options = {"urls": [TARGET], "post_type": "text"}
        legacy = scrape_source(TARGET, options)

        monkeypatch.setattr(s, "use_go_worker", True)
        monkeypatch.setattr(s, "go_worker_base_url", base_url)
        flagged = scrape_source(TARGET, options, idempotency_key=JOB_KEY)

        # the single dom post is type "link" -> everything filtered on both
        assert flagged.posts == legacy.posts == []
        assert flagged.stats == legacy.stats == {
            "posts_discovered": 1, "posts_extracted": 0,
            "duplicates_removed": 0, "posts_skipped": 1, "posts_failed": 0,
        }

    def test_max_posts_cap_parity(self, fake_go, monkeypatch):
        base_url, _served = fake_go
        s = get_settings()
        monkeypatch.setattr("backend.scraper.Fetcher", _FakeFetcher)

        options = {"urls": [TARGET], "max_posts": 0}
        legacy = scrape_source(TARGET, options)

        monkeypatch.setattr(s, "use_go_worker", True)
        monkeypatch.setattr(s, "go_worker_base_url", base_url)
        flagged = scrape_source(TARGET, options, idempotency_key=JOB_KEY)

        assert flagged.posts == legacy.posts == []
        assert flagged.stats == legacy.stats == {
            "posts_discovered": 1, "posts_extracted": 0,
            "duplicates_removed": 0, "posts_skipped": 1, "posts_failed": 0,
        }

    def test_duplicate_posts_flag_stats_caveat(self, fake_go, monkeypatch):
        """Go pre-dedups, so the flagged path can't name the dedup share.

        The posts stay identical between paths; only the bookkeeping differs
        (``duplicates_removed`` is contract-invisible on the flagged path) and
        the stats invariant holds on BOTH sides.
        """
        base_url, _served = fake_go
        s = get_settings()
        monkeypatch.setattr("backend.scraper.Fetcher", _FakeFetcher)
        monkeypatch.setattr(_FakeFetcher, "html", DUP_HTML)

        legacy = scrape_source(TARGET)
        assert legacy.stats["posts_discovered"] == 2
        assert legacy.stats["duplicates_removed"] == 1

        monkeypatch.setattr(s, "use_go_worker", True)
        monkeypatch.setattr(s, "go_worker_base_url", base_url)
        flagged = scrape_source(TARGET, idempotency_key=JOB_KEY)

        assert _mask_scraped_at(flagged.posts) == _mask_scraped_at(legacy.posts)
        # contract-invisible dedup share
        assert flagged.stats["duplicates_removed"] == 0
        # invariant (stats.py): discovered == extracted + skipped + failed
        for stats in (flagged.stats, legacy.stats):
            assert stats["posts_discovered"] == (
                stats["posts_extracted"] + stats["posts_skipped"]
                + stats["posts_failed"])


# ---------------------------------------------------------------------------
# Fault isolation: go outage is a per-source error, never a job crash
# ---------------------------------------------------------------------------


class TestScrapeSourceOutage:
    def test_go_unreachable_is_per_source_error(self, monkeypatch):
        s = get_settings()
        monkeypatch.setattr("backend.scraper.Fetcher", _FakeFetcher)
        monkeypatch.setattr(s, "use_go_worker", True)
        monkeypatch.setattr(s, "go_worker_base_url",
                            f"http://127.0.0.1:{_dead_port()}")

        result = scrape_source(TARGET, idempotency_key="job_1:source_1")

        # SourceResult, NOT an exception: the go outage downgrades to a
        # per-source failure like any network error on the legacy path.
        assert result.posts == []
        assert result.page_name is None and result.page_id is None
        assert len(result.errors) == 1
        assert result.errors[0]["code"] == "network_error"
        assert "go worker" in result.errors[0]["message"]
        assert result.stats["posts_extracted"] == 0
        assert result.stats["posts_discovered"] == 0