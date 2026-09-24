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

This module exposes:

* :class:`NodeFeedClient` — thin HTTP client for ``POST /feed-fetch``,
  mirroring :class:`NodeFetchClient`'s transport/error handling.
* :class:`FeedWalkAdapter` — a :class:`PageFetcher`
  (``backend/scraper/pagination.py``) that turns frames into ``PageResult``
  batches of parsed posts — the feed-walk shape ``paginate()`` drives.
* :func:`walk_feed_via_node` — the end-to-end seam (Phase 3 wires this
  into the crawler path behind the ``GUEST_FEED_WALK`` flag).
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

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
from backend.scraper.parser import ParsedPage, parse_page

__all__ = [
    "FeedFrame",
    "FeedWalkAdapter",
    "NodeFeedClient",
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
    ) -> FeedFrame:
        """POST one frame URL to node and map the result into a FeedFrame.

        Raises ScraperError subtypes mirroring the unified node envelope:
        ``rate_limited`` / ``page_unavailable`` / ``timeout`` /
        ``invalid_url``; malformed payloads and unreachable node map onto
        ``ExtractionFailure(code="network_error")``.
        """
        started = time.monotonic()
        response = self._post(frame_url, cursor=cursor)
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

    def _post(self, frame_url: str, *, cursor: Optional[str]) -> httpx.Response:
        """POST ``/feed-fetch`` with limited retry+backoff on *fast*
        transport failures (node mid-deploy blips). Timeouts and definitive
        envelope answers are never retried (see module docstring).
        """
        payload: dict[str, Any] = {"target_url": frame_url}
        if cursor is not None:
            payload["cursor"] = cursor
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
# Feed-walk adapter (PageFetcher protocol, backend/scraper/pagination.py)
# ---------------------------------------------------------------------------

class FeedWalkAdapter:
    """Turn node feed frames into ``PageResult`` batches for ``paginate()``.

    Each ``fetch_page`` call fetches ONE frame via node (a bound
    ``fetch_frame`` callable), parses the posts in Python, and returns them
    with the next-frame cursor.

    ``next_cursor`` extraction is the Phase 2 piece — the FB-specific
    pagination token (mbasic ``?page=N`` / GraphQL cursor) — extracted from
    the frame in Python. Until it lands the walk stops after the first frame
    (``has_more=False``), making this adapter a one-frame seam exactly
    equivalent to today's single shot. Extraction stays in Python by design:
    node never parses frames.
    """

    def __init__(
        self,
        target_url: str,
        fetch_frame: Callable[[str, Optional[str]], FeedFrame],
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
            # cursor encodes the next frame URL (Phase 2: token/URL).
            frame_url: str = cursor
        else:
            frame_url = self._target_url

        frame = self._fetch_frame(frame_url, cursor)
        parsed = self._parse(frame.html, page_url=frame.final_url, handle=self._handle)
        next_cursor = self._extract_next_frame_url(frame, parsed)

        return PageResult(
            items=list(parsed.posts) if parsed.posts else [],
            next_cursor=next_cursor,
            has_more=next_cursor is not None,
            meta={
                "frame_status": frame.status_code,
                "final_url": frame.final_url,
                "blocked": frame.blocked,
                "session_id": frame.session_id,
                "posts": len(parsed.posts),
            },
        )

    def _extract_next_frame_url(
        self, frame: FeedFrame, parsed: ParsedPage
    ) -> Optional[str]:
        """Phase 2: extract the next-frame token from this frame.

        Returns ``None`` for now — the walk is a one-frame seam until the
        FB pagination cursor extraction lands.
        """
        return None


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
        return paginate(
            adapter,
            max_items=max_items,
            max_rounds=max_rounds,
            cancel_event=cancel_event,
        )
    finally:
        if owns_client:
            client.close()


def _bound_fetch_frame(client: NodeFeedClient) -> Callable[[str, Optional[str]], FeedFrame]:
    """Bind a client's fetch_frame to the adapter's 2-arg callable shape."""

    def _fetch(frame_url: str, cursor: Optional[str]) -> FeedFrame:
        return client.fetch_frame(frame_url, cursor=cursor)

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