"""FastAPI -> node feed-walk seam (guest, cookieless public page feeds).

Companion to :mod:`backend.services.node_fetch`: instead of one-off page
fetches, this drives a walk across a page's feed frames. node owns the
network — Crawlee ``HttpCrawler`` + ``SessionPool`` (sessions reused across
frames, rotation on block, ``POST /feed-fetch``) — and returns each frame
as RAW bytes; FastAPI keeps the orchestration: the ``paginate()`` loop,
Python-side parsing (``backend/scraper/parser.py``), and job bookkeeping.

Transport-only contract
-----------------------
One ``POST /feed-fetch`` round trip == one raw frame:

    {status_code, final_url, raw_payload (base64), session_id, blocked}

The frame request is extended for the GraphQL pagination walk:

    {target_url, cursor, method, form, referer}

* ``target_url`` — the URL to fetch (the page for frame 1, the GraphQL
  endpoint for every frame after).
* ``cursor``    — the opaque frame spec (see below); node passes it through
  verbatim, never interprets it.
* ``method``    — ``GET`` (page frames) or ``POST`` (GraphQL frames).
* ``form``      — form-encoded body fields for POST frames (string->string).
* ``referer``   — the page being walked, sent as the Referer header on POST
  frames so the GraphQL endpoint sees the walk origin.

This module exposes:

* :class:`NodeFeedClient` — thin HTTP client for ``POST /feed-fetch``,
  mirroring :class:`NodeFetchClient`'s transport/error handling.
* :class:`FeedWalkAdapter` — a :class:`PageFetcher`
  (``backend/scraper/pagination.py``) that turns frames into ``PageResult``
  batches of parsed posts — the feed-walk shape ``paginate()`` drives.
* :func:`walk_feed_via_node` — the end-to-end seam (Phase 3 wires this
  into the crawler path behind the ``GUEST_FEED_WALK`` flag).

Feed-walk mechanism (proven live, Phase 2)
------------------------------------------
Frame 1 is a ``GET`` of the page: it embeds the GraphQL feed bootstrap — the
``queryID`` (``28338492715759825``) plus the Relay preloader variables
template and the initial pagination cursor.  Every later frame is a guest
``POST`` to ``https://www.facebook.com/api/graphql/`` with a form body of
``doc_id`` + the preloader variables (``count`` bumped to 3, ``cursor``
advanced) plus the two Relay bookkeeping fields.  Each response is a
concatenated stream of top-level JSON documents; the story nodes live under
``data.user.timeline_list_feed_units.edges[].node`` (first document) and
``data.node`` (later documents), and the trailing document carries the next
``data.page_info`` (``has_next_page`` + ``end_cursor``).

The opaque frame cursor is a JSON spec::

    {"kind": "gql",
     "url": "https://www.facebook.com/api/graphql/",
     "doc_id": "...", "count": 3, "end_cursor": "...",
     "variables": {...preloader template...}}

Parsing stays in Python by design: node never parses frames.
"""

from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

import httpx

from backend.core.config import get_settings
from backend.scraper.errors import (
    ExtractionFailure,
    InvalidUrl,
    PageUnavailable,
    RateLimited,
    ScraperError,
    Timeout,
)
from backend.scraper.pagination import PageResult, PaginationResult, paginate
from backend.scraper.parser import ParsedPage, extract_posts_from_graphql_body, parse_page

__all__ = [
    "FeedFrame",
    "FrameRequest",
    "FeedWalkAdapter",
    "NodeFeedClient",
    "extract_feed_bootstrap",
    "walk_feed_via_node",
]

#: Total HTTP budget for one node round trip (one frame). node may spend up
#: to maxRequestRetries * SCRAPER_TIMEOUT_SECONDS plus rotation on a failing
#: frame; this sits above that worst case (same rationale as
#: NODE_FETCH_HTTP_TIMEOUT in node_fetch.py).
NODE_FEED_HTTP_TIMEOUT = 150.0

#: Fast transport failures indicating node is briefly unreachable; retried
#: with short backoff. Timeouts and definitive envelope answers are NOT
#: retried (a stuck node must surface as a per-source error, not a storm).
_RETRYABLE_TRANSPORT_ERRORS = (
    httpx.ConnectError,
    httpx.ReadError,
    httpx.RemoteProtocolError,
)

#: GraphQL feed endpoint for frames 2+ (proven live against humansofnewyork).
GQL_ENDPOINT = "https://www.facebook.com/api/graphql/"

#: Stories requested per GraphQL frame (~3 observed live).
GQL_FRAME_COUNT = 3

#: Relay bookkeeping sent alongside doc_id + variables (proven live probe).
GQL_CALLER_CLASS = "RelayModern"
GQL_FRIENDLY_NAME = "ProfileCometTimelineFeedQuery"

#: Anchor that uniquely identifies the timeline-feed Relay preloader in the
#: frame-1 HTML (the block that carries queryID + variables template).
PRELOADER_ANCHOR = '"preloaderID":"adp_ProfileCometTimelineFeedQueryRelayPreloader_'


@dataclass
class FeedFrame:
    """One decoded feed frame returned by node (raw transport result)."""

    status_code: int
    final_url: str
    html: str
    blocked: bool = False
    session_id: Optional[str] = None
    attempts: int = 0
    elapsed: float = 0.0


@dataclass
class FrameRequest:
    """One node frame request — what to fetch and how.

    ``method`` is ``GET`` for page frames and ``POST`` for GraphQL frames;
    ``form`` carries the form-encoded body fields for POST frames (values
    are always strings); ``referer`` names the page being walked so POST
    frames reach the GraphQL endpoint with the walk origin.
    """

    url: str
    cursor: Optional[str] = None
    method: str = "GET"
    form: Optional[Dict[str, str]] = None
    referer: Optional[str] = None


@dataclass
class FeedBootstrap:
    """The feed-walk bootstrap extracted from frame 1 (the page HTML)."""

    doc_id: str
    variables: Dict[str, Any]
    end_cursor: str


class NodeFeedClient:
    """Small HTTP client for the node ``POST /feed-fetch`` endpoint.

    One instance per feed walk (constructed by :func:`walk_feed_via_node` or
    injected in tests). Not thread-safe by design — concurrency of 1 mirrors
    the walk discipline.
    """

    def __init__(
        self,
        base_url: str,
        *,
        transport: Optional[httpx.BaseTransport] = None,
        timeout: float = NODE_FEED_HTTP_TIMEOUT,
        max_retries: int = 2,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.requests_made = 0
        self._client = httpx.Client(timeout=timeout, transport=transport)

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:  # pragma: no cover - best effort
            pass

    def __enter__(self) -> "NodeFeedClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- low-level POST ----------------------------------------------------
    def fetch_frame(
        self,
        frame_url: str,
        *,
        cursor: Optional[str] = None,
        method: str = "GET",
        form: Optional[Dict[str, str]] = None,
        referer: Optional[str] = None,
    ) -> FeedFrame:
        """POST one frame request to node and map the result into a FeedFrame.

        The JSON payload mirrors ``FeedFrameRequest`` (node/src/feed/types.ts):
        ``target_url`` plus the optional ``cursor`` / ``method`` / ``form`` /
        ``referer`` frame fields.

        Raises ScraperError subtypes mirroring the unified node envelope:
        ``rate_limited`` / ``page_unavailable`` / ``timeout`` /
        ``invalid_url``; malformed payloads and unreachable node map onto
        ``ExtractionFailure(code="network_error")``.
        """
        started = time.monotonic()
        response = self._post(
            frame_url, cursor=cursor, method=method, form=form, referer=referer
        )
        if response.status_code == 200:
            try:
                data = response.json()
                status = int(data["status_code"])
                final_url = str(data.get("final_url") or frame_url)
                raw = str(data["raw_payload"])
                blocked = bool(data.get("blocked"))
                session_id = data.get("session_id")
            except (ValueError, KeyError, TypeError) as exc:
                raise ExtractionFailure(
                    f"node returned a malformed FeedFrameResponse for {frame_url}",
                    code="network_error",
                ) from exc
            return FeedFrame(
                status_code=status,
                final_url=final_url,
                html=_decode_payload(raw),
                blocked=blocked,
                session_id=session_id,
                attempts=self.requests_made,
                elapsed=time.monotonic() - started,
            )

        # Non-200: node returns the unified error envelope
        # {"error": {"code", "message"}}.
        try:
            body = response.json()
            code = str(body["error"]["code"])
            message = str(body["error"]["message"])
        except (ValueError, KeyError, TypeError) as exc:
            raise ExtractionFailure(
                f"node returned HTTP {response.status_code} with no "
                f"error envelope for {frame_url}",
                code="network_error",
            ) from exc

        raise _map_node_error(code, message)

    def _post(
        self,
        frame_url: str,
        *,
        cursor: Optional[str],
        method: str,
        form: Optional[Dict[str, str]],
        referer: Optional[str],
    ) -> httpx.Response:
        """POST ``/feed-fetch`` with limited retry+backoff on *fast*
        transport failures (node mid-deploy blips). Timeouts and definitive
        envelope answers are never retried (see module docstring).
        """
        payload: Dict[str, Any] = {"target_url": frame_url}
        if cursor is not None:
            payload["cursor"] = cursor
        if method and method != "GET":
            payload["method"] = method
        if form:
            payload["form"] = form
        if referer:
            payload["referer"] = referer
        backoff = 0.0
        for attempt in range(self.max_retries + 1):
            if attempt:
                time.sleep(backoff)
                backoff = min(backoff + 0.25, 1.0)
            try:
                response = self._client.post(
                    f"{self.base_url}/feed-fetch",
                    json=payload,
                )
            except _RETRYABLE_TRANSPORT_ERRORS:
                if attempt < self.max_retries:
                    continue
                raise ExtractionFailure(
                    f"node unreachable ({self.base_url}) after "
                    f"{self.max_retries} retries",
                    code="network_error",
                ) from None
            except httpx.TimeoutException as exc:
                raise Timeout(
                    f"node feed request timed out after {self.timeout:.0f}s "
                    f"for {frame_url}"
                ) from exc
            except httpx.HTTPError as exc:
                raise ExtractionFailure(
                    f"node unreachable ({self.base_url}): {exc!r}",
                    code="network_error",
                ) from exc

            self.requests_made += 1
            return response
        raise AssertionError("unreachable")  # pragma: no cover


# ---------------------------------------------------------------------------
# Bootstrap + frame-spec helpers (frame-1 HTML -> GraphQL cursor mechanics)
# ---------------------------------------------------------------------------


def extract_feed_bootstrap(html: str) -> Optional[FeedBootstrap]:
    """Extract the feed-walk bootstrap from frame 1 (the page HTML).

    Looks for the timeline-feed Relay preloader block and pulls out the
    ``queryID`` (doc_id), the preloader ``variables`` template, and the
    initial ``end_cursor`` (the first cursor Facebook deposits next to
    ``"has_next_page": true``).

    Returns ``None`` when the frame carries no such bootstrap (e.g. a block
    or login wall) — the caller then degrades to a one-frame seam.
    """
    if not html:
        return None
    start = html.find(PRELOADER_ANCHOR)
    if start < 0:
        return None

    # queryID right after the preloaderID anchor.
    qs = html.find('"queryID":"', start)
    if qs < 0:
        return None
    qs += len('"queryID":"')
    qe = html.find('"', qs)
    if qe < 0:
        return None
    doc_id = html[qs:qe]
    if not doc_id.isdigit():
        return None

    # balanced-brace variables object following "variables":.
    vs = html.find('"variables":', qs)
    if vs < 0:
        return None
    begin = html.find("{", vs)
    if begin < 0:
        return None
    depth = 0
    i = begin
    while i < len(html):
        if html[i] == "{":
            depth += 1
        elif html[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    try:
        variables = json.loads(html[begin : i + 1])
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(variables, dict):
        return None

    hp = html.find('"has_next_page":true')
    if hp < 0:
        return None
    m = re.search(r'"end_cursor":"([^"]+)"', html[hp : hp + 8000])
    if not m:
        return None

    return FeedBootstrap(doc_id=doc_id, variables=variables, end_cursor=m.group(1))


def _gql_spec(bootstrap: FeedBootstrap, end_cursor: str) -> str:
    """Encode a GraphQL frame spec (the opaque paginate() cursor)."""
    return json.dumps(
        {
            "kind": "gql",
            "url": GQL_ENDPOINT,
            "doc_id": bootstrap.doc_id,
            "count": GQL_FRAME_COUNT,
            "end_cursor": end_cursor,
            "variables": bootstrap.variables,
        }
    )


def _graphql_form(spec: Dict[str, Any]) -> Dict[str, str]:
    """Build the form-encoded body for one GraphQL frame from a spec.

    ``variables`` is the frame-1 preloader template mutated exactly like the
    validated live probe: ``count`` bumped to the per-frame fetch size and
    ``cursor`` set to the current page token.
    """
    variables = dict(spec.get("variables") or {})
    variables["count"] = int(spec.get("count") or GQL_FRAME_COUNT)
    variables["cursor"] = spec["end_cursor"]
    return {
        "doc_id": str(spec["doc_id"]),
        "variables": json.dumps(variables),
        "fb_api_caller_class": GQL_CALLER_CLASS,
        "fb_api_req_friendly_name": GQL_FRIENDLY_NAME,
    }


def _next_gql_spec(spec: Dict[str, Any], page_info: Optional[dict]) -> Optional[str]:
    """Next frame spec from a GraphQL response's ``data.page_info``.

    Stops the walk when the response carries no next cursor (the page not
    present, or ``has_next_page`` false).
    """
    if not page_info:
        return None
    if not page_info.get("has_next_page"):
        return None
    next_end = page_info.get("end_cursor")
    if not next_end:
        return None
    spec = dict(spec)
    spec["end_cursor"] = next_end
    return json.dumps(spec)


# ---------------------------------------------------------------------------
# Feed-walk adapter (PageFetcher protocol, backend/scraper/pagination.py)
# ---------------------------------------------------------------------------


class FeedWalkAdapter:
    """Turn node feed frames into ``PageResult`` batches for ``paginate()``.

    Frame 1 (``cursor=None``) GETs the page, parses the HTML for posts and
    page metadata, and extracts the GraphQL bootstrap; the next cursor is a
    ``{"kind": "gql", ...}`` frame spec.  Every later frame POSTs the spec to
    the GraphQL endpoint and parses the concatenated JSON response into
    ``ParsedPost``s, with the next cursor taken from the trailing
    ``data.page_info`` document.

    Parsing stays in Python by design: node never parses frames.
    """

    def __init__(
        self,
        target_url: str,
        fetch_frame: Callable[[FrameRequest], FeedFrame],
        *,
        parse: Callable[..., ParsedPage] = parse_page,
        handle: Optional[str] = None,
    ) -> None:
        self._target_url = target_url
        self._fetch_frame = fetch_frame
        self._parse = parse
        self._handle = handle

    def fetch_page(
        self,
        *,
        cursor: Optional[str] = None,
        cancel_event: Optional[Any] = None,
    ) -> PageResult:
        del cancel_event  # cancellation is checked by paginate() between rounds

        if cursor is not None:
            try:
                spec = json.loads(cursor)
            except ValueError:
                spec = None
        else:
            spec = None

        if spec and spec.get("kind") == "gql":
            return self._fetch_graphql_frame(spec)
        # Frame 1 (or a plain-URL cursor for forward compat): GET + parse.
        frame_url = cursor if (cursor and spec is None) else self._target_url
        frame = self._fetch_frame(
            FrameRequest(url=frame_url, cursor=cursor, method="GET")
        )
        parsed = self._parse(frame.html, page_url=frame.final_url, handle=self._handle)

        # Page identity comes from frame 1's parse (og:title/profile url);
        # it is stamped into every round's meta so the walk's final
        # PaginationResult.meta carries it (paginate keeps the last round's).
        self._page_name = parsed.page_name
        self._page_id = parsed.page_id
        self._profile_url = parsed.profile_url

        # GraphQL walk continues when frame 1 embeds a feed bootstrap.
        bootstrap = extract_feed_bootstrap(frame.html)
        next_cursor = _gql_spec(bootstrap, bootstrap.end_cursor) if bootstrap else None

        return PageResult(
            items=list(parsed.posts) if parsed.posts else [],
            next_cursor=next_cursor,
            has_more=next_cursor is not None,
            meta=self._round_meta(frame, len(parsed.posts)),
        )

    def _fetch_graphql_frame(self, spec: Dict[str, Any]) -> PageResult:
        """POST one GraphQL frame and parse the concatenated JSON response."""
        form = _graphql_form(spec)
        frame = self._fetch_frame(
            FrameRequest(
                url=str(spec["url"]),
                cursor=json.dumps(spec),
                method="POST",
                form=form,
                referer=self._target_url,
            )
        )
        posts, page_info = extract_posts_from_graphql_body(
            frame.html, frame.final_url
        )
        next_cursor = _next_gql_spec(spec, page_info)

        return PageResult(
            items=list(posts),
            next_cursor=next_cursor,
            has_more=next_cursor is not None,
            meta=self._round_meta(frame, len(posts)),
        )

    def _round_meta(self, frame: FeedFrame, posts: int) -> dict:
        """Meta stamped on every round's PageResult.

        ``page_name``/``page_id``/``profile_url`` are captured from frame 1
        and carried forward so the walk's final PaginationResult.meta keeps
        page identity even when the last round is a GraphQL frame.
        """
        return {
            "frame_status": frame.status_code,
            "final_url": frame.final_url,
            "blocked": frame.blocked,
            "session_id": frame.session_id,
            "posts": posts,
            "page_name": getattr(self, "_page_name", None),
            "page_id": getattr(self, "_page_id", None),
            "profile_url": getattr(self, "_profile_url", None),
        }


def walk_feed_via_node(
    target_url: str,
    *,
    handle: Optional[str] = None,
    max_items: Optional[int] = None,
    max_rounds: int = 40,
    client: Optional[NodeFeedClient] = None,
    cancel_event: Optional[Any] = None,
) -> PaginationResult:
    """Walk a public page feed via node (the scraper seam behind GUEST_FEED_WALK).

    Drives ``paginate()`` over a :class:`FeedWalkAdapter`; the client is
    built from settings unless injected (tests). Client ownership follows
    ``node_fetch.fetch_page_via_node``: the caller owns an injected client;
    a client built here is closed before returning/raising.
    """
    owns_client = client is None
    client = client or NodeFeedClient(get_settings().node_base_url)
    try:
        adapter = FeedWalkAdapter(
            target_url=target_url,
            fetch_frame=_bound_fetch_frame(client),
            handle=handle,
        )
        result = paginate(
            adapter,
            max_items=max_items,
            max_rounds=max_rounds,
            cancel_event=cancel_event,
        )
        if result.stop_reason == "fetch_error":
            # A hard frame failure (blocked after rotation, rate limit,
            # timeout) surfaces as a per-source error — same semantics as
            # node_fetch: never a silent partial walk. paginate keeps the
            # original exception next to its string form (meta["error_exc"]).
            exc = result.meta.get("error_exc")
            if isinstance(exc, ScraperError):
                raise exc
            raise ExtractionFailure(
                str(result.meta.get("error") or "feed walk failed"),
                code="network_error",
            )
        return result
    finally:
        if owns_client:
            client.close()


def _bound_fetch_frame(client: NodeFeedClient) -> Callable[[FrameRequest], FeedFrame]:
    """Bind a client's fetch_frame to the adapter's FrameRequest callable."""

    def _fetch(req: FrameRequest) -> FeedFrame:
        return client.fetch_frame(
            req.url,
            cursor=req.cursor,
            method=req.method,
            form=req.form,
            referer=req.referer,
        )

    return _fetch


def _decode_payload(raw: str) -> str:
    """Decode ``FeedFrameResponse.raw_payload`` (base64 bytes) into UTF-8 str.

    ``errors="replace"`` is deliberate: frame bytes are decoded for parsing,
    which is loss-tolerant (same rationale as node_fetch._decode_payload).
    """
    decoded = base64.b64decode(raw)
    return decoded.decode("utf-8", errors="replace")


def _map_node_error(code: str, message: str) -> ScraperError:
    """Map a node error code onto the scraper error taxonomy."""
    if code == "invalid_url":
        return InvalidUrl(message)
    if code == "rate_limited":
        return RateLimited(message)
    if code == "page_unavailable":
        return PageUnavailable(message)
    if code == "timeout":
        return Timeout(message)
    # network_error and any unknown code -> per-source extraction failure
    return ExtractionFailure(message, code=code)