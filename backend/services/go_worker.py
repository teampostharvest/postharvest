"""FastAPI -> go compute seam (finalplanv2 §5 "Go (Compute)" / §14 strangler).

When ``USE_GO_WORKER`` is on, HTTP-mode scrapes send the fetched page bytes
to the go worker's Phase-1 Parse RPC (``POST /v1/parse``) instead of
running parse -> normalize -> dedup in-process.  FastAPI keeps all
*orchestration* (fetching, per-source filters, max_posts cap, job
bookkeeping); go owns the compute slice and returns canonical 33-key posts
plus isolated per-post parse failures byte-for-byte equal to the Python
pipeline (proven by the golang ``httpapi`` goldens, which this seam's
hermetic tests reuse).  Nothing in this module ever fabricates content:
every go verdict maps onto the same ``ScraperError`` taxonomy the Python
path uses.

Request contract (shared/proto/postharvest.proto ``ParseRequest``):

* ``raw_payload``    — UTF-8 page bytes, base64
* ``content_type``   — "html" (HTTP-mode only; browser-mode stays separate)
* ``idempotency_key``— ``"{job_id}:{source_id}"``; go's §8(c) cache makes
                       retried calls dedup-safe (M4, golang/idempotency)
* ``target_url``     — normalized source URL (lands in every post's
                       ``facebook_url`` — the byte that matters for storage)
* ``handle``         — page-handle fallback for ``page_name``

Known seam caveats (documented, not hidden):

1. **Dedup count is not observable.**  ``ParseResponse`` carries only
   ``posts`` + ``errors`` (contract-fixed), so how many posts go deduped
   inside go is invisible here; ``duplicates_removed`` therefore stays 0 on
   the flagged path and ``posts_discovered`` is derived from what FastAPI
   can count (extracted + skipped + failed).  The stats invariant always
   holds; the flagged path just cannot name the dedup share.
2. **Filter ordering.**  The Python path filters *before* dedup; go returns
   an already-deduped set, so filters run after here.  Results only diverge
   in the exotic case where two mutually-duplicate posts straddle a date/
   type filter predicate, and only on the *flagged* path — the flag-off
   bytes are untouched.
3. **Page metadata.**  ``ParseResponse`` has no page-context fields;
   ``page_name``/``page_id`` are recovered from the first post's normalized
   fields (identical values the Python parser would return) or ``None``
   when zero posts came back.
"""

from __future__ import annotations

import base64
import json
import threading
import time
from typing import List, Optional

import httpx

from backend.core.config import get_settings
from backend.scraper.errors import (
    ExtractionFailure,
    OperationCancelled,
    Timeout,
)

__all__ = ["GoWorkerClient", "parse_posts_via_go", "GO_PARSE_HTTP_TIMEOUT"]


#: Total HTTP budget for one Parse RPC round trip.  Go only computes
#: in-memory (no network), so this is generous but not a policy ceiling.
GO_PARSE_HTTP_TIMEOUT = 60.0

#: Fast transport failures that indicate go is briefly unreachable
#: (mid-deploy, restart, pod blip).  These retried with short backoff;
#: ``TimeoutException`` and definite HTTP answers are NOT — a stuck worker
#: must surface as a per-source error, not a retry storm (Critical Note 2).
_RETRYABLE_TRANSPORT_ERRORS = (
    httpx.ConnectError,
    httpx.ReadError,
    httpx.RemoteProtocolError,
)

#: Response header go sets on /v1/parse (non-contract observability, M4):
#: whether the ParseResponse came from the §8(c) idempotency cache.
CACHE_HEADER = "X-PostHarvest-Cache"


class ParseResult:
    """One ParseResponse mapped back into scrape_source-shaped values.

    :param posts: canonical 33-key normalized posts (already deduped by go).
    :param errors: per-post parse failures as ``{code, message, ...}`` dicts
        (the JSON-encoded strings go emits, decoded — byte-identical to
        what parser.py's ``post_errors`` produce).
    :param page_name / page_id: recovered from the first post's normalized
        fields (or ``None`` when zero posts) — see module caveat 3.
    :param cache_hit: whether go served this from its idempotency cache
        (``True``/``False``), or ``None`` when the header is absent.
    """

    __slots__ = ("posts", "errors", "page_name", "page_id", "cache_hit")

    def __init__(
        self,
        posts: List[dict],
        errors: List[dict],
        *,
        page_name: Optional[str] = None,
        page_id: Optional[str] = None,
        cache_hit: Optional[bool] = None,
    ) -> None:
        self.posts = posts
        self.errors = errors
        self.page_name = page_name
        self.page_id = page_id
        self.cache_hit = cache_hit


class GoWorkerClient:
    """Small HTTP client for the go worker ``POST /v1/parse`` endpoint.

    One instance per source scrape (built by :func:`parse_posts_via_go` or
    injected in tests).  Not thread-safe by design — concurrency of 1 per
    source mirrors every other per-source client.
    """

    def __init__(
        self,
        base_url: str,
        *,
        transport: Optional[httpx.BaseTransport] = None,
        timeout: float = GO_PARSE_HTTP_TIMEOUT,
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

    def __enter__(self) -> "GoWorkerClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- the RPC ----------------------------------------------------------
    def parse(
        self,
        *,
        target_url: str,
        handle: Optional[str] = None,
        raw_html: str = "",
        content_type: str = "html",
        idempotency_key: Optional[str] = None,
    ) -> ParseResult:
        """POST one source's page bytes to go and return the parsed result.

        Raises ``ScraperError`` subtypes for go being unreachable
        (``network_error`` as ``ExtractionFailure``) or too slow
        (``timeout``) — the same per-source failure language the Python
        path uses, so a go outage is just another source error, never a
        whole-job crash (fault-isolation invariant).
        """
        body = {
            "raw_payload": base64.b64encode(raw_html.encode("utf-8")).decode("ascii"),
            "content_type": content_type,
            "idempotency_key": idempotency_key or "",
            "target_url": target_url,
            "handle": handle or "",
        }
        response = self._post(body)
        if response.status_code == 200:
            try:
                data = response.json()
                posts = data["posts"]
                if not isinstance(posts, list):
                    raise KeyError("posts")
                errors = data.get("errors") or []
                if not isinstance(errors, list):
                    raise KeyError("errors")
            except (ValueError, KeyError, TypeError) as exc:
                raise ExtractionFailure(
                    f"go worker returned a malformed ParseResponse for {target_url}",
                    code="network_error",
                ) from exc

            decoded_errors: List[dict] = []
            for raw in errors:
                try:
                    entry = json.loads(raw) if isinstance(raw, str) else dict(raw)
                    decoded_errors.append(entry)
                except (ValueError, TypeError) as exc:
                    raise ExtractionFailure(
                        f"go worker returned an undecodable parse error for "
                        f"{target_url}: {raw!r}",
                        code="network_error",
                    ) from exc

            page_name: Optional[str] = None
            page_id: Optional[str] = None
            if posts:
                first = posts[0]
                if isinstance(first, dict):
                    page_name = first.get("page_name")
                    page_id = first.get("page_id")
            hit = response.headers.get(CACHE_HEADER)
            return ParseResult(
                posts,
                decoded_errors,
                page_name=page_name,
                page_id=page_id,
                cache_hit={"hit": True, "miss": False}.get(hit),
            )

        # Non-200: go returns {"error": "<message>"}.  Anything other than
        # 200 means the RPC did not complete — treated as a per-source
        # extraction failure.
        message = self._error_message(response)
        raise ExtractionFailure(
            f"go worker returned HTTP {response.status_code}: {message}",
            code="network_error",
        )

    def _post(self, body: dict) -> httpx.Response:
        """POST ``/v1/parse`` with limited retry+backoff on *fast* transport
        failures only (go mid-deploy / restart blips).

        ``TimeoutException`` is deliberately NOT retried: a stuck go worker
        must surface as a per-source ``timeout``, not a retry storm.
        """
        backoff = 0.0
        for attempt in range(self.max_retries + 1):
            if attempt:
                time.sleep(backoff)
                backoff = min(backoff + 0.25, 1.0)
            try:
                response = self._client.post(
                    f"{self.base_url}/v1/parse",
                    json=body,
                )
            except _RETRYABLE_TRANSPORT_ERRORS:
                if attempt < self.max_retries:
                    continue
                raise ExtractionFailure(
                    f"go worker unreachable ({self.base_url}) after "
                    f"{self.max_retries} retries",
                    code="network_error",
                ) from None
            except httpx.TimeoutException as exc:
                raise Timeout(
                    f"go worker request timed out after {self.timeout:.0f}s "
                    f"for {body.get('target_url', '')}"
                ) from exc
            except httpx.HTTPError as exc:
                raise ExtractionFailure(
                    f"go worker unreachable ({self.base_url}): {exc!r}",
                    code="network_error",
                ) from exc

            self.requests_made += 1
            return response
        raise AssertionError("unreachable")  # pragma: no cover

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        try:
            data = response.json()
            message = str(data["error"])
        except (ValueError, KeyError, TypeError):
            message = response.text[:200] or response.reason_phrase
        return message


def parse_posts_via_go(
    target_url: str,
    handle: Optional[str],
    raw_html: str,
    *,
    idempotency_key: Optional[str] = None,
    cancel_event: Optional[threading.Event] = None,
    client: Optional[GoWorkerClient] = None,
) -> ParseResult:
    """Parse one source's bytes through go (the ``scrape_source`` seam).

    Builds a client from settings unless one is injected (tests).  ``client``
    ownership: the caller owns any injected client; the client built here is
    closed before returning/raising.
    """
    if cancel_event is not None and cancel_event.is_set():
        raise OperationCancelled()
    owns_client = client is None
    client = client or GoWorkerClient(get_settings().go_worker_base_url)
    try:
        return client.parse(
            target_url=target_url,
            handle=handle,
            raw_html=raw_html,
            content_type="html",
            idempotency_key=idempotency_key,
        )
    finally:
        if owns_client:
            client.close()