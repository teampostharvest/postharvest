"""Hermetic tests for the FastAPI -> node feed-walk seam.

Nothing here touches the network: client tests drive the seam through
``httpx.MockTransport``; the adapter tests stub the node frame callable with
the GraphQL frame fixtures below; the end-to-end seam test injects a scripted
client covering a two-frame walk.

Phase 2 covers the GraphQL pagination walk:
* frame 1 = GET page HTML that embeds the feed bootstrap (queryID +
  preloader variables + initial ``end_cursor``);
* frames 2+ = POST form bodies to ``/api/graphql/`` with the preloader
  variables and an advancing cursor; each response is a concatenated stream
  of JSON documents whose trailing document carries ``data.page_info``.
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
    FrameRequest,
    NodeFeedClient,
    extract_feed_bootstrap,
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

# Frame-1 HTML carrying the GraphQL feed bootstrap: a Relay preloader with the
# timeline-feed queryID + variables template, plus an embedded real
# "has_next_page":true and the initial end_cursor.
FRAME1_BOOTSTRAP_HTML = """
<html><body>
<script type="application/json">
{"preloaderID":"adp_ProfileCometTimelineFeedQueryRelayPreloader_6ab4bb8359e702165015452","queryID":"28338492715759825","variables":{"count":1,"feedbackSource":0,"feedLocation":"TIMELINE","omitPinnedPost":true,"scale":1,"stream_count":1,"userID":"100050429952420","__relay_internal__pv__SomeFeature":false}}
</script>
<script type="application/json">
{"data":{"node":{"__typename":"ProfileCometTimelineFeedQueryRelayPreloader","id":"x"}}, "extensions":{"is_final":false,"has_next_page":true,"end_cursor":"INITIAL_CURSOR"}}
</script>
</body></html>
"""


def _story(post_id: str, text: str, ts: int = 1700000000) -> dict:
    return {
        "__typename": "Story",
        "post_id": post_id,
        "creation_time": ts,
        "permalink_url": f"https://www.facebook.com/story.php?story_fbid={post_id}",
        "message": {"text": text},
    }


def _gql_body(
    stories: list[dict],
    *,
    has_next: bool,
    end_cursor: str,
    user_shape: bool = True,
) -> str:
    """Build a concatenated GraphQL response like the live /api/graphql/ one.

    The first document carries ``data.user.timeline_list_feed_units``
    (walk shape); subsequent documents carry ``data.node`` (deferred query
    slices); the trailing document carries ``data.page_info``.
    """
    docs: list[dict] = []
    first, rest = stories[:1], stories[1:]
    if first and user_shape:
        docs.append(
            {
                "data": {
                    "user": {
                        "timeline_list_feed_units": {
                            "edges": [{"node": first[0]}],
                        }
                    }
                },
                "extensions": {},
            }
        )
        docs.extend([{"label": "d", "path": [], "data": {"node": s}, "extensions": {}} for s in rest])
    else:
        docs.extend([{"data": {"node": s}, "extensions": {}} for s in stories])
    docs.append(
        {
            "data": {
                "page_info": {
                    "has_next_page": has_next,
                    "end_cursor": end_cursor,
                }
            },
            "extensions": {},
        }
    )
    return "".join(json.dumps(d) for d in docs)


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
    """A ready-made adapter fetch_frame stub (FrameRequest callable shape)."""

    def _fetch(req: FrameRequest) -> FeedFrame:
        return FeedFrame(
            status_code=status,
            final_url=req.url,
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


def test_fetch_frame_sends_method_form_referer_when_given():
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured.update(json.loads(req.content.decode()))
        return httpx.Response(200, json=_frame_success("https://www.facebook.com/api/graphql/"))

    client = _mock_client(handler)
    client.fetch_frame(
        "https://www.facebook.com/api/graphql/",
        cursor='{"kind":"gql"}',
        method="POST",
        form={"doc_id": "28338492715759825", 'variables': '{"count":3}'},
        referer="https://www.facebook.com/NASA/",
    )
    assert captured["cursor"] == '{"kind":"gql"}'
    assert captured["method"] == "POST"
    assert captured["form"] == {
        "doc_id": "28338492715759825",
        'variables': '{"count":3}',
    }
    assert captured["referer"] == "https://www.facebook.com/NASA/"
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
# Bootstrap extraction (frame 1 -> GraphQL cursor)
# ---------------------------------------------------------------------------

def test_extract_feed_bootstrap_parses_frame1():
    boot = extract_feed_bootstrap(FRAME1_BOOTSTRAP_HTML)
    assert boot is not None
    assert boot.doc_id == "28338492715759825"
    assert boot.variables["count"] == 1
    assert boot.variables["feedLocation"] == "TIMELINE"
    assert boot.variables["userID"] == "100050429952420"
    assert boot.end_cursor == "INITIAL_CURSOR"


def test_extract_feed_bootstrap_none_without_preloader():
    assert extract_feed_bootstrap(FAKE_FRAME_HTML) is None
    assert extract_feed_bootstrap("") is None


# ---------------------------------------------------------------------------
# FeedWalkAdapter: frame -> parsed posts -> PageResult
# ---------------------------------------------------------------------------

def test_adapter_returns_parsed_posts_and_stops_without_bootstrap():
    # No GraphQL preloader in the frame -> one-frame seam (no cursor).
    adapter = FeedWalkAdapter(
        target_url="https://www.facebook.com/NASA/",
        fetch_frame=fake_fetch_frame(),  # type: ignore[arg-type]
    )
    result = adapter.fetch_page()
    assert len(result.items) == 2
    assert {p.post_id for p in result.items} == {"1001", "1002"}
    assert result.next_cursor is None
    assert result.has_more is False
    assert result.meta["blocked"] is False
    assert result.meta["posts"] == 2


def test_adapter_frame1_bootstrap_sets_gql_cursor():
    adapter = FeedWalkAdapter(
        target_url="https://www.facebook.com/NASA/",
        fetch_frame=fake_fetch_frame(FRAME1_BOOTSTRAP_HTML),  # type: ignore[arg-type]
    )
    result = adapter.fetch_page()
    assert result.next_cursor is not None
    assert result.has_more is True
    spec = json.loads(result.next_cursor)
    assert spec["kind"] == "gql"
    assert spec["doc_id"] == "28338492715759825"
    assert spec["end_cursor"] == "INITIAL_CURSOR"


def test_adapter_uses_cursor_as_next_frame_url():
    seen: list[str] = []

    def fetch(req: FrameRequest) -> FeedFrame:
        seen.append(req.url)
        return FeedFrame(
            status_code=200,
            final_url=req.url,
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


def test_adapter_graphql_frame_posts_and_advances_cursor():
    """A GraphQL round parses story nodes and advances end_cursor."""
    body = _gql_body(
        [
            _story("2001", "GraphQL post A"),
            _story("2002", "GraphQL post B", ts=1700000100),
        ],
        has_next=True,
        end_cursor="NEXT_CURSOR",
    )
    calls: list[FrameRequest] = []

    def fetch(req: FrameRequest) -> FeedFrame:
        calls.append(req)
        return FeedFrame(status_code=200, final_url=req.url, html=body)

    adapter = FeedWalkAdapter(
        target_url="https://www.facebook.com/NASA/",
        fetch_frame=fetch,
    )
    spec = json.dumps(
        {
            "kind": "gql",
            "url": "https://www.facebook.com/api/graphql/",
            "doc_id": "28338492715759825",
            "count": 3,
            "end_cursor": "INITIAL_CURSOR",
            "variables": {"count": 1, "userID": "100050429952420"},
        }
    )
    result = adapter.fetch_page(cursor=spec)

    assert {p.post_id for p in result.items} == {"2001", "2002"}
    assert result.meta["posts"] == 2

    # POST mechanics: method, form fields, referer all on the wire.
    assert len(calls) == 1
    req = calls[0]
    assert req.method == "POST"
    assert req.referer == "https://www.facebook.com/NASA/"
    form = req.form or {}
    assert form["doc_id"] == "28338492715759825"
    assert form["fb_api_caller_class"] == "RelayModern"
    assert form["fb_api_req_friendly_name"] == "ProfileCometTimelineFeedQuery"
    sent_vars = json.loads(form["variables"])
    assert sent_vars["count"] == 3
    assert sent_vars["cursor"] == "INITIAL_CURSOR"
    assert sent_vars["userID"] == "100050429952420"

    next_spec = json.loads(result.next_cursor)
    assert next_spec["kind"] == "gql"
    assert next_spec["end_cursor"] == "NEXT_CURSOR"
    assert result.has_more is True


def test_adapter_graphql_frame_stops_when_page_info_no_next():
    body = _gql_body(
        [_story("2001", "Last post")],
        has_next=False,
        end_cursor="",
    )

    def fetch(req: FrameRequest) -> FeedFrame:
        return FeedFrame(status_code=200, final_url=req.url, html=body)

    adapter = FeedWalkAdapter(
        target_url="https://www.facebook.com/NASA/",
        fetch_frame=fetch,
    )
    spec = json.dumps(
        {
            "kind": "gql",
            "url": "https://www.facebook.com/api/graphql/",
            "doc_id": "28338492715759825",
            "count": 3,
            "end_cursor": "LAST_CURSOR",
            "variables": {},
        }
    )
    result = adapter.fetch_page(cursor=spec)
    assert len(result.items) == 1
    assert result.next_cursor is None
    assert result.has_more is False


# ---------------------------------------------------------------------------
# walk_feed_via_node: the seam (frame 1 GET + GraphQL POST walk)
# ---------------------------------------------------------------------------

def test_walk_feed_via_node_walks_graphql_to_exhaustion():
    """Full seam: frame 1 GET (bootstrap) then POST frames until exhausted."""

    class ScriptedClient:
        def __init__(self) -> None:
            self.calls: list[FrameRequest] = []

        def fetch_frame(
            self,
            frame_url: str,
            *,
            cursor: str | None = None,
            method: str = "GET",
            form: dict | None = None,
            referer: str | None = None,
        ) -> FeedFrame:
            req = FrameRequest(
                url=frame_url, cursor=cursor, method=method, form=form, referer=referer
            )
            self.calls.append(req)
            if method == "GET":
                return FeedFrame(
                    status_code=200,
                    final_url=frame_url,
                    html=FRAME1_BOOTSTRAP_HTML,
                )
            end = req.form.get("variables", "")
            vars_ = json.loads(end)
            if vars_.get("cursor") == "INITIAL_CURSOR":
                # First POST: two stories, more pages to come.
                return FeedFrame(
                    status_code=200,
                    final_url=req.url,
                    html=_gql_body(
                        [_story("3001", "Walk post one"), _story("3002", "Walk post two", ts=1700000100)],
                        has_next=True,
                        end_cursor="SECOND_CURSOR",
                    ),
                )
            # Second POST: one story, page_info has_next_page false -> stop.
            return FeedFrame(
                status_code=200,
                final_url=req.url,
                html=_gql_body(
                    [_story("3003", "Walk post three", ts=1700000200)],
                    has_next=False,
                    end_cursor="",
                ),
            )

    stub = ScriptedClient()
    result = walk_feed_via_node(
        "https://www.facebook.com/NASA/",
        client=stub,  # type: ignore[arg-type]
    )

    assert stub.calls[0].method == "GET"
    assert stub.calls[0].url == "https://www.facebook.com/NASA/"
    assert len(stub.calls) == 3  # frame 1 + two POST frames
    for req in stub.calls[1:]:
        assert req.method == "POST"
        assert req.url == "https://www.facebook.com/api/graphql/"
        assert req.referer == "https://www.facebook.com/NASA/"

    assert {p.post_id for p in result.items} == {"3001", "3002", "3003"}
    assert result.stop_reason == "no_more"
    assert result.total_rounds == 3


def test_walk_feed_via_node_surfaces_fetch_error():
    """A hard frame failure (blocked after rotation) raises, not a partial walk."""

    class FailingClient:
        def fetch_frame(
            self,
            frame_url: str,
            *,
            cursor: str | None = None,
            method: str = "GET",
            form: dict | None = None,
            referer: str | None = None,
        ) -> FeedFrame:
            if method == "GET":
                return FeedFrame(
                    status_code=200, final_url=frame_url, html=FRAME1_BOOTSTRAP_HTML
                )
            raise RateLimited("FB 429 after frame rotation")

    with pytest.raises(RateLimited):
        walk_feed_via_node(
            "https://www.facebook.com/NASA/",
            client=FailingClient(),  # type: ignore[arg-type]
        )


def test_walk_feed_via_node_respects_max_items():
    class StubClient:
        def fetch_frame(
            self,
            frame_url: str,
            *,
            cursor: str | None = None,
            method: str = "GET",
            form: dict | None = None,
            referer: str | None = None,
        ) -> FeedFrame:
            return FeedFrame(status_code=200, final_url=frame_url, html=FAKE_FRAME_HTML)

    result = walk_feed_via_node(
        "https://www.facebook.com/NASA/",
        max_items=1,
        client=StubClient(),  # type: ignore[arg-type]
    )
    assert len(result.items) == 1
    assert result.stop_reason == "item_cap"