"""FastAPI -> node fetch seam (finalplanv2 §4, §12, §14).

When ``USE_NODE`` is on, HTTP-mode scrapes get their page bytes from
the node service instead of the legacy Python ``Fetcher``.  FastAPI
keeps all *orchestration* (variant fallback order, page classification, job
bookkeeping); node owns everything that touches the network:

* fetching the page (single honest UA, redirects, retries, jittered backoff)
* robots.txt enforcement (allowlist-only on failure, RFC 9309 subset)
* the shared Redis token bucket (2.5 s politeness floor, cross-process)

node returns **raw bytes only** (``FetchResponse.raw_payload``);
parsing, normalization and dedup stay where they are (FastAPI today,
worker later).  Nothing in this module ever fabricates content: every
node verdict maps onto a ``ScraperError`` subclass with the same
stable ``.code`` the Python fetcher would have produced.

Parity contract
---------------
:func:`fetch_page_via_node` mirrors ``Fetcher.fetch_page``'s variant fallback
and failure aggregation exactly (``backend/scraper/fetcher.py``, the
www -> mbasic -> mobile ladder), so a source fails with the SAME error codes
whether it was fetched by Python or by node.  A node outage
is therefore just another per-source failure (fault-isolation invariant) and
never takes down a whole job.
"""

from __future__ import annotations

import base64
import threading
import time
from typing import Optional

import httpx

from backend.core.config import get_settings
from backend.scraper.errors import (
    AuthRequired,
    ExtractionFailure,
    InvalidUrl,
    OperationCancelled,
    PageUnavailable,
    RateLimited,
    ScraperError,
    Timeout,
    UnsupportedUrl,
)
from backend.scraper.fetcher import (
    FetchResult,
    build_variants,
    classify_page_html,
)

__all__ = ["NodeFetchClient", "fetch_page_via_node", "NODE_FETCH_HTTP_TIMEOUT"]

#: Total HTTP budget for one node round trip.  node itself may
#: spend up to ~robots + (retries * SCRAPER_TIMEOUT_SECONDS) + backoff on a
#: failing target; this must sit comfortably above that worst case while still
#: letting the FastAPI worker free the connection when node dies.
NODE_FETCH_HTTP_TIMEOUT = 150.0

#: node error codes a variant ladder may recover from (try the next
#: variant).  Everything else — timeout / network_error / body_too_large /
#: unknown — is a hard stop for the source, matching the Python fetcher's
#: "do not hammer further variants after a hard block" rule.
_NODE_RECOVERABLE_CODES = frozenset(
    ("robots_disallowed", "rate_limited", "page_unavailable")
)

#: Fast transport failures that indicate node is briefly unreachable
#: (mid-deploy, restart, pod blip).  These retried with short backoff; "slow"
#: failures (TimeoutException) and definitive envelope answers are NOT — a
#: stuck node must surface as a per-source error, not a retry storm.
_RETRYABLE_TRANSPORT_ERRORS = (
    httpx.ConnectError,
    httpx.ReadError,
    httpx.RemoteProtocolError,
)


class NodeFetchClient:
    """Small HTTP client for the node ``POST /fetch`` endpoint.

    One instance per source scrape (constructed by :func:`fetch_page_via_node`
    or injected in tests).  Not thread-safe by design — concurrency of 1 per
    source mirrors the Python fetcher.
    """

    def __init__(
        self,
        base_url: str,
        *,
        transport: Optional[httpx.BaseTransport] = None,
        timeout: float = NODE_FETCH_HTTP_TIMEOUT,
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

    def __enter__(self) -> "NodeFetchClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- low-level POST -----------------------------------------------------
    def fetch(self, url: str) -> FetchResult:
        """POST one URL to node and map the result into a FetchResult.

        Raises the same ``ScraperError`` subtypes the Python fetcher raises for
        the equivalent condition:
        ``rate_limited`` / ``page_unavailable`` / ``robots_disallowed`` /
        ``timeout`` / ``invalid_url`` and ``network_error``/``body_too_large``
        as ``ExtractionFailure``.
        """
        started = time.monotonic()
        response = self._post(url)
        if response.status_code == 200:
            try:
                data = response.json()
                status = int(data["status_code"])
                final_url = str(data.get("final_url") or url)
                raw = str(data["raw_payload"])
                html = _decode_payload(raw)
            except (ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
                raise ExtractionFailure(
                    f"node returned a malformed FetchResponse for {url}",
                    code="network_error",
                ) from exc
            return FetchResult(
                status_code=status,
                final_url=final_url,
                html=html,
                url_used=url,
                variant="",
                attempts=self.requests_made,
                elapsed=time.monotonic() - started,
            )

        # Non-200: node returns the unified error envelope
        # {"error": {"code", "message"}} — mirror of the API error contract.
        try:
            body = response.json()
            code = str(body["error"]["code"])
            message = str(body["error"]["message"])
        except (ValueError, KeyError, TypeError) as exc:
            raise ExtractionFailure(
                f"node returned HTTP {response.status_code} with no "
                f"error envelope for {url}",
                code="network_error",
            ) from exc

        raise _map_node_error(code, message)

    def _post(self, url: str) -> httpx.Response:
        """POST ``/fetch`` with limited retry+backoff on *fast* transport
        failures (node mid-deploy / restart blips).

        ``TimeoutException`` is deliberately NOT retried: a slow node
        must surface as a per-source ``timeout`` error, not a retry storm.
        Definitive envelope answers (rate_limited, page_unavailable, ...) are
        returned as-is and never retried either.
        """
        backoff = 0.0
        for attempt in range(self.max_retries + 1):
            if attempt:
                time.sleep(backoff)
                backoff = min(backoff + 0.25, 1.0)
            try:
                response = self._client.post(
                    f"{self.base_url}/fetch",
                    json={"target_url": url, "mode": "http"},
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
                    f"node request timed out after {self.timeout:.0f}s "
                    f"for {url}"
                ) from exc
            except httpx.HTTPError as exc:
                raise ExtractionFailure(
                    f"node unreachable ({self.base_url}): {exc!r}",
                    code="network_error",
                ) from exc

            self.requests_made += 1
            return response
        raise AssertionError("unreachable")  # pragma: no cover

    # -- page fetch (variant ladder) -----------------------------------------
    def fetch_page(self, normalized_url: str, *, cancel_event=None) -> FetchResult:
        """Mirror of ``Fetcher.fetch_page``: try www -> mbasic -> mobile.

        Robots enforcement happens inside node, so the Python
        robotparser is not consulted on this path (equivalent policy, enforced
        server-side).  Aggregation of wall/block/not-found verdicts and the
        raise order match ``backend/scraper/fetcher.py`` exactly.
        """
        started = time.monotonic()
        walled: list[str] = []
        blocked: list[str] = []
        not_found: list[str] = []
        last_verdict = "empty"

        for label, url in build_variants(normalized_url):
            if cancel_event is not None and cancel_event.is_set():
                raise OperationCancelled()
            try:
                result = self.fetch(url)
            except ScraperError as exc:
                if exc.code not in _NODE_RECOVERABLE_CODES:
                    raise  # timeout / network_error / body_too_large -> hard stop
                if exc.code == "robots_disallowed":
                    walled.append(f"{label}:robots_disallowed")
                elif exc.code == "rate_limited":
                    blocked.append(label)
                else:  # page_unavailable
                    not_found.append(label)
                continue

            verdict = classify_page_html(result.html)
            last_verdict = verdict
            if result.status_code == 200 and verdict == "ok":
                return FetchResult(
                    status_code=result.status_code,
                    final_url=result.final_url,
                    html=result.html,
                    url_used=url,
                    variant=label,
                    attempts=self.requests_made,
                    elapsed=time.monotonic() - started,
                )
            if result.status_code == 404 or verdict == "not_found":
                not_found.append(label)
            elif verdict == "wall":
                walled.append(label)
            elif verdict == "traffic" or result.status_code in (401, 403):
                blocked.append(label)
            else:
                walled.append(f"{label}:unusable({verdict})")

        if blocked:
            raise RateLimited(
                "Facebook served an automated-traffic / security check on "
                "every variant; proceeding would require anti-bot evasion, "
                "which is out of scope by design.")
        if walled:
            raise AuthRequired(
                f"Every fetched variant requires authentication "
                f"(login wall / refused): {', '.join(walled)}.")
        if not_found:
            raise PageUnavailable(
                f"The page is not publicly available "
                f"(variants: {', '.join(not_found)}).")
        raise ExtractionFailure(
            f"No usable public HTML variant was fetched "
            f"(last verdict: {last_verdict}).")


def fetch_page_via_node(
    normalized_url: str,
    *,
    cancel_event: Optional[threading.Event] = None,
    client: Optional[NodeFetchClient] = None,
) -> FetchResult:
    """Fetch one source through node (the ``scrape_source`` seam).

    Builds a client from settings unless one is injected (tests).  ``client``
    ownership: the caller owns any injected client; the client built here is
    closed before returning/raising.
    """
    owns_client = client is None
    client = client or NodeFetchClient(get_settings().node_base_url)
    try:
        return client.fetch_page(normalized_url, cancel_event=cancel_event)
    finally:
        if owns_client:
            client.close()


def _decode_payload(raw: str) -> str:
    """Decode ``FetchResponse.raw_payload`` (base64 bytes) into str UTF-8.

    ``errors="replace"`` is deliberate: page bytes are decoded for parsing,
    which is loss-tolerant, while the byte payload itself stays intact inside
    node (it never round-trips through a UTF-8 decode).
    """
    decoded = base64.b64decode(raw)
    return decoded.decode("utf-8", errors="replace")


def _map_node_error(code: str, message: str) -> ScraperError:
    """Map a node error code onto the scraper error taxonomy."""
    if code == "invalid_url":
        return InvalidUrl(message)
    if code == "robots_disallowed":
        return UnsupportedUrl(message, code="robots_disallowed")
    if code == "rate_limited":
        return RateLimited(message)
    if code == "page_unavailable":
        return PageUnavailable(message)
    if code == "timeout":
        return Timeout(message)
    if code == "body_too_large":
        return ExtractionFailure(message, code="body_too_large")
    # network_error and any unknown code -> per-source extraction failure
    return ExtractionFailure(message, code=code)