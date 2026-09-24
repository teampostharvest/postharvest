"""Hermetic tests for Phase 3: wiring the node feed-walk seam into
``scrape_source`` behind the ``GUEST_FEED_WALK`` flag.

Pattern: like the go-worker parity tests (``test_go_worker_seam.py``), the
REAL ``scrape_source`` pipeline runs with the flag ON; only the walk's
transport (the ``NodeFeedClient`` that talks to node over HTTP) is replaced
by a scripted stand-in answering the same frames the live walk produces
(frame-1 GET that embeds the GraphQL bootstrap, then guest POST form bodies
to ``/api/graphql/``).  Everything upstream of the client — paginate(), the
``FeedWalkAdapter``, frame parsing, ``_walk_source_page``'s ParsedPage
shaping, and the shared normalize/filter/dedup/cap loop — is the real code.

Honesty rules:

* The flag-off path is never touched here: ``guest_feed_walk`` defaults to
  False and the flag-off tests assert the walk seam was NOT invoked.
* ``scraped_at`` is stamped by the real equal-off logic, so the parity
  comparisons use the same masked-posts helper the go tests use.
"""

from __future__ import annotations

import json
import threading

import pytest

from backend.core.config import get_settings
from backend.scraper import scrape_source
from backend.services import node_feed as node_feed_mod
from backend.services.node_feed import FeedFrame, FrameRequest

TARGET = "https://www.facebook.com/acmewidgets"
HANDLE = "acmewidgets"

# ---------------------------------------------------------------------------
# Walk transport fixtures (same shapes the live seam produces)
# ---------------------------------------------------------------------------

# Frame 1: the page HTML that embeds the GraphQL bootstrap — og: metadata
# (so frame-1 parse yields a page name), the Relay preloader (queryID +
# variables template) and the initial cursor next to has_next_page.
FRAME1_HTML = """
<html><head>
<meta property="og:title" content="Acme Widgets | Facebook" />
<meta property="og:url" content="https://www.facebook.com/acmewidgets/" />
</head><body>
<script type="application/json">
{"preloaderID":"adp_ProfileCometTimelineFeedQueryRelayPreloader_6ab4bb8359e702165015452","queryID":"28338492715759825","variables":{"count":1,"feedbackSource":0,"feedLocation":"TIMELINE","omitPinnedPost":true,"scale":1,"stream_count":1,"userID":"424242"}}
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
) -> str:
    docs = []
    first, rest = stories[:1], stories[1:]
    if first:
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
        docs.extend(
            [{"label": "d", "path": [], "data": {"node": s}, "extensions": {}}
            for s in rest]
        )
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


class ScriptedNodeClient:
    """Answers the walk's frames like the live node transport.

    Frame 1 (GET) returns the bootstrap page; POST frames advance the cursor
    like the real GraphQL endpoint: first POST yields two stories + a next
    cursor, second POST yields one story + ``has_next_page: false``.
    """

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
            return FeedFrame(status_code=200, final_url=frame_url, html=FRAME1_HTML)
        vars_ = json.loads((form or {}).get("variables", "{}"))
        if vars_.get("cursor") == "INITIAL_CURSOR":
            html = _gql_body(
                [_story("3001", "Walk one"), _story("3002", "Walk two", ts=1700000100)],
                has_next=True,
                end_cursor="SECOND_CURSOR",
            )
        else:
            html = _gql_body(
                [_story("3003", "Walk three", ts=1700000200)],
                has_next=False,
                end_cursor="",
            )
        return FeedFrame(status_code=200, final_url=frame_url, html=html)

    def close(self) -> None:
        pass


@pytest.fixture
def scripted_node(monkeypatch) -> ScriptedNodeClient:
    """Flag GUEST_FEED_WALK on and stub the node transport with a script."""
    client = ScriptedNodeClient()
    from backend.services import node_feed as nf

    monkeypatch.setattr(nf, "NodeFeedClient", lambda *a, **k: client)
    monkeypatch.setattr(
        get_settings(), "guest_feed_walk", True
    )
    return client


# ---------------------------------------------------------------------------
# Flag-off: legacy path untouched, seam never invoked
# ---------------------------------------------------------------------------


class _FakeFetch:
    html = """
    <html><body>
    <div data-testid="test">
      <script>
        window.__additionalDataLoaded("12345", {"require": [
          {"__bbox": {"result": {"data": {"nodes": [
            {"__typename": "Post", "id": "post_1001", "post_id": "1001",
             "creation_time": 1700000000,
             "message": "Test post from page A",
             "from": {"name": "Test Page", "id": "page_001"}}
          ]}}}
        ]}]);
      </script>
    </div>
    </body></html>
    """
    final_url = TARGET
    variant = "www"
    status_code = 200


class _FakeFetcher:
    def __init__(self, **kwargs):
        pass

    def fetch_page(self, normalized_url: str) -> _FakeFetch:
        f = _FakeFetch()
        f.final_url = normalized_url
        return f

    def close(self):
        pass


def test_walk_flag_off_runs_legacy_fetch_parse(monkeypatch):
    """Flag off: the walk seam is never touched; the legacy pipe is used."""
    monkeypatch.setattr("backend.scraper.Fetcher", _FakeFetcher)

    calls = {"walk": 0}

    def _boom(*_args, **_kwargs):
        calls["walk"] += 1
        raise AssertionError("walk seam invoked while GUEST_FEED_WALK is off")

    monkeypatch.setattr(node_feed_mod, "walk_feed_via_node", _boom)

    result = scrape_source(TARGET)

    assert calls["walk"] == 0
    assert [p["post_id"] for p in result.posts] == ["1001"]
    assert result.stats == {
        "posts_discovered": 1, "posts_extracted": 1,
        "duplicates_removed": 0, "posts_skipped": 0, "posts_failed": 0,
    }
    assert result.errors == []


# ---------------------------------------------------------------------------
# Flag on: real walk seam drives the shared pipeline
# ---------------------------------------------------------------------------


def test_walk_flag_on_walks_graphql_frames_end_to_end(scripted_node):
    """Frame 1 GET + two POST frames; posts flow through the shared loop."""
    result = scrape_source(TARGET)

    # The walk really walked: GET frame 1, then two GraphQL POSTs.
    reqs = scripted_node.calls
    assert len(reqs) == 3
    assert reqs[0].method == "GET"
    assert reqs[0].url == TARGET
    for req in reqs[1:]:
        assert req.method == "POST"
        assert req.url == "https://www.facebook.com/api/graphql/"
        assert req.referer == TARGET
        assert req.form["doc_id"] == "28338492715759825"
        assert req.form["fb_api_caller_class"] == "RelayModern"
        assert req.form["fb_api_req_friendly_name"] == "ProfileCometTimelineFeedQuery"

    # Page identity from frame-1 metadata flows into the SourceResult.
    assert result.page_name == "Acme Widgets"
    assert result.page_id is None  # handle URL carries no numeric id

    assert {p["post_id"] for p in result.posts} == {"3001", "3002", "3003"}
    assert [p["text"] for p in result.posts] == [
        "Walk one", "Walk two", "Walk three",
    ]
    assert result.stats == {
        "posts_discovered": 3, "posts_extracted": 3,
        "duplicates_removed": 0, "posts_skipped": 0, "posts_failed": 0,
    }
    assert result.errors == []


def test_walk_flag_on_max_posts_cap_applies_on_shared_loop(scripted_node):
    """The walk yields 3 posts; the shared cap trims to max_posts."""
    result = scrape_source(TARGET, {"urls": [TARGET], "max_posts": 2})

    assert [p["post_id"] for p in result.posts] == ["3001", "3002"]
    assert result.stats == {
        "posts_discovered": 3, "posts_extracted": 2,
        "duplicates_removed": 0, "posts_skipped": 1, "posts_failed": 0,
    }


def test_walk_flag_on_post_type_filter_applies_on_shared_loop(scripted_node):
    """A text-only filter keeps the walk's text posts."""
    result = scrape_source(TARGET, {"urls": [TARGET], "post_type": "text"})
    assert len(result.posts) == 3
    assert result.stats["posts_skipped"] == 0


def test_walk_flag_on_fetch_error_surfaces_taxonomy(monkeypatch):
    """A hard frame failure surfaces as the taxonomy-preserving error entry."""
    from backend.scraper.errors import RateLimited

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
                return FeedFrame(status_code=200, final_url=frame_url, html=FRAME1_HTML)
            raise RateLimited("FB 429 after frame rotation")

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        node_feed_mod, "NodeFeedClient", lambda *a, **k: FailingClient()
    )
    monkeypatch.setattr(get_settings(), "guest_feed_walk", True)

    result = scrape_source(TARGET)

    assert result.posts == []
    assert result.errors == [{
        "url": TARGET,
        "code": "rate_limited",
        "message": "FB 429 after frame rotation",
    }]


def test_walk_flag_on_cancel_maps_to_operation_cancelled(monkeypatch):
    """A mid-walk cancel produces the same cancelled error entry as legacy."""
    cancel_event = threading.Event()
    cancel_event.set()

    client = ScriptedNodeClient()
    monkeypatch.setattr(node_feed_mod, "NodeFeedClient", lambda *a, **k: client)
    monkeypatch.setattr(get_settings(), "guest_feed_walk", True)

    result = scrape_source(TARGET, cancel_event=cancel_event)

    assert result.posts == []
    assert result.errors[0]["code"] == "cancelled"