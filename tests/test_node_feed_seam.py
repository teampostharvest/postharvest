"""Hermetic tests for the FastAPI -> node feed-walk seam.

Nothing here touches the network: client tests drive the seam through
``httpx.MockTransport``; the adapter tests use a real ``parse_page`` over a
parseable fixture; the end-to-end seam test injects a stub client.
"""

from __future__ import annotations

import base64
import json
import time

import httpx
import pytest

from backend.scraper.errors import (
    ExtractionFailure,
    InvalidUrl,
    PageUnavailable,
    RateLimited,
    Timeout,
)
from backend.services.node_feed import (
    FeedFrame,
    FeedWalkAdapter,
    NodeFeedClient,
    walk_feed_via_node,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Parseable by the real parser: script-based extraction yields two posts
# (post_id 1001 / 1002). Same shape as the node /fetch seam fixtures.
FAKE_FRAME_HTML = """
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

EMPTY_FRAME_HTML = "<html><body><div id='pagelet_bluebar'>empty</div></body></html>"


def _frame_success(
    url: str,
    html: str = FAKE_FRAME_HTML,
    *,
    blocked: bool = False,
    session_id: str | None = "session_1",
) -> dict:
    return {
        "status_code": 200,
        "final_url": url,
        "raw_payload": base64.b64encode(html.encode("utf-8")).decode("ascii"),
        "session_id": session_id,
        "blocked": blocked,
        "fetched_at_ms": int(time.time() * 1000),
    }


def _frame_error(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def _target_of(req: httpx.Request) -> str:
    """The Facebook URL a /feed-fetch request carries in its JSON body."""
    return str(json.loads(req.content.decode()).get("target_url", ""))


def _mock_client(handler) -> NodeFeedClient:
    return NodeFeedClient(
        "http://node:9334",
        transport=httpx.MockTransport(handler),
    )


def fake_fetch_frame(
    html: str = FAKE_FRAME_HTML,
    *,
    blocked: bool = False,
    status: int = 200,
) -> object:
    """A ready-made adapter fetch_frame stub (2-arg callable shape)."""

    def _fetch(frame_url: str, cursor: str | None = None) -> FeedFrame:
        return FeedFrame(
            status_code=status,
            final_url=frame_url,
            html=html,
            blocked=blocked,
            session_id="session_1",
        )

    return _fetch


# ---------------------------------------------------------------------------
# NodeFeedClient: transport / envelope mapping
# ---------------------------------------------------------------------------

def test_fetch_frame_success():
    client = _mock_client(
        lambda req: httpx.Response(
            200, json=_frame_success("https://www.facebook.com/NASA/")
        )
    )
    frame = client.fetch_frame("https://www.facebook.com/NASA/")
    assert frame.status_code == 200
    assert frame.final_url == "https://www.facebook.com/NASA/"
    assert "Test post from page A" in frame.html
    assert frame.session_id == "session_1"
    assert frame.blocked is False
    assert client.requests_made == 1
    client.close()


def test_fetch_frame_blocks_are_surfaced_not_thrown():
    client = _mock_client(
        lambda req: httpx.Response(
            200,
            json=_frame_success(
                "https://www.facebook.com/NASA/", EMPTY_FRAME_HTML, blocked=True
            ),
        )
    )
    frame = client.fetch_frame("https://www.facebook.com/NASA/")
    assert frame.blocked is True
    client.close()


def test_fetch_frame_sends_cursor_when_given():
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured.update(json.loads(req.content.decode()))
        return httpx.Response(200, json=_frame_success("https://www.facebook.com/NASA/"))

    client = _mock_client(handler)
    client.fetch_frame("https://www.facebook.com/NASA/", cursor="frame-2-url")
    assert captured["cursor"] == "frame-2-url"
    client.close()


def test_fetch_frame_malformed_envelope_is_network_error():
    client = _mock_client(lambda req: httpx.Response(200, json={"nope": True}))
    with pytest.raises(ExtractionFailure) as excinfo:
        client.fetch_frame("https://www.facebook.com/NASA/")
    assert excinfo.value.code == "network_error"
    client.close()


def test_fetch_frame_maps_error_envelope():
    client = _mock_client(
        lambda req: httpx.Response(429, json=_frame_error("rate_limited", "FB 429"))
    )
    with pytest.raises(RateLimited):
        client.fetch_frame("https://www.facebook.com/NASA/")
    client.close()


def test_fetch_frame_retries_fast_transport_failures():
    calls = {"n": 0}

    def flaky(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("refused", request=req)
        return httpx.Response(200, json=_frame_success("https://www.facebook.com/NASA/"))

    client = _mock_client(flaky)
    frame = client.fetch_frame("https://www.facebook.com/NASA/")
    assert frame.status_code == 200
    assert calls["n"] == 2
    assert client.requests_made == 1
    client.close()


def test_fetch_frame_timeout_is_not_retried():
    client = _mock_client(
        lambda req: (_ for _ in ()).throw(httpx.TimeoutException("slow"))
    )
    with pytest.raises(Timeout):
        client.fetch_frame("https://www.facebook.com/NASA/")
    client.close()


# ---------------------------------------------------------------------------
# FeedWalkAdapter: frame -> parsed posts -> PageResult
# ---------------------------------------------------------------------------

def test_adapter_returns_parsed_posts_and_no_cursor_until_phase2():
    adapter = FeedWalkAdapter(
        target_url="https://www.facebook.com/NASA/",
        fetch_frame=fake_fetch_frame(),  # type: ignore[arg-type]
    )
    result = adapter.fetch_page()
    assert len(result.items) == 2
    assert {p.post_id for p in result.items} == {"1001", "1002"}
    assert result.next_cursor is None  # Phase 2 stub — one-frame seam
    assert result.has_more is False
    assert result.meta["blocked"] is False
    assert result.meta["posts"] == 2


def test_adapter_uses_cursor_as_next_frame_url():
    seen: list[str] = []

    def fetch(frame_url: str, cursor: str | None = None) -> FeedFrame:
        seen.append(frame_url)
        return FeedFrame(
            status_code=200,
            final_url=frame_url,
            html=FAKE_FRAME_HTML,
        )

    adapter = FeedWalkAdapter(
        target_url="https://www.facebook.com/NASA/",
        fetch_frame=fetch,
    )
    result = adapter.fetch_page(cursor="https://www.facebook.com/NASA/?page=2")
    assert seen == ["https://www.facebook.com/NASA/?page=2"]
    assert result.items  # still parsed


def test_adapter_surfaces_blocked_frames_in_meta():
    adapter = FeedWalkAdapter(
        target_url="https://www.facebook.com/NASA/",
        fetch_frame=fake_fetch_frame(EMPTY_FRAME_HTML, blocked=True),  # type: ignore[arg-type]
    )
    result = adapter.fetch_page()
    assert result.meta["blocked"] is True
    assert result.meta["frame_status"] == 200


# ---------------------------------------------------------------------------
# walk_feed_via_node: the seam (single frame until Phase 2 cursor lands)
# ---------------------------------------------------------------------------

def test_walk_feed_via_node_is_one_frame_seam():
    class StubClient:
        def __init__(self) -> None:
            self.calls = 0

        def fetch_frame(
            self, frame_url: str, *, cursor: str | None = None
        ) -> FeedFrame:
            self.calls += 1
            return FeedFrame(
                status_code=200,
                final_url=frame_url,
                html=FAKE_FRAME_HTML,
            )

    stub = StubClient()
    result = walk_feed_via_node(
        "https://www.facebook.com/NASA/",
        client=stub,  # type: ignore[arg-type]
    )
    assert stub.calls == 1
    assert len(result.items) == 2
    assert result.stop_reason == "no_more"
    assert result.total_rounds == 1


def test_walk_feed_via_node_respects_max_items():
    class StubClient:
        def fetch_frame(
            self, frame_url: str, *, cursor: str | None = None
        ) -> FeedFrame:
            return FeedFrame(
                status_code=200,
                final_url=frame_url,
                html=FAKE_FRAME_HTML,
            )

    result = walk_feed_via_node(
        "https://www.facebook.com/NASA/",
        max_items=1,
        client=StubClient(),  # type: ignore[arg-type]
    )
    assert len(result.items) == 1
    assert result.stop_reason == "item_cap"