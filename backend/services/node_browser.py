"""FastAPI -> node browser-mode capture seam (finalplanv2 §4 browser-mode, §12).

When ``USE_NODE_BROWSER`` is on, browser-mode scrapes send their capture work
to the node service (``/fetch`` ``mode="browser"``) instead of the Python
``fetch_with_browser`` in ``backend/scraper/browser_scraper.py``.  FastAPI
keeps all *orchestration* (the 3-attempt session/anonymous retry ladder,
``_is_wall`` classification, page parsing, job bookkeeping); node owns the
actual browser: launching headless Chromium, navigating, scrolling, capturing
the DOM/script/graphql pools and applying the session cookies.

node returns **raw bytes only** (``FetchResponse.raw_payload``, base64) plus
a small capture summary (``FetchResponse.browser_stats``).  Parsing,
normalization and dedup stay in FastAPI, unchanged.  Nothing here fabricates
content: every node verdict maps onto the same ``ScraperError`` taxonomy the
Python browser path would raise for the equivalent condition.

Cookie boundary (finalplanv2 §2/§7): DB access stays Python-only.  The bridge
loads the jar with ``load_cookies`` (the same function ``fetch_with_browser``
uses) and serializes it to RFC 6265 ``"name=value; ..."`` lines that ride on
``FetchRequest.cookies``; node applies them to the browser context and never
persists them — it only reports the post-capture session back on
``FetchResponse.updated_cookies``, which this bridge persists (Slice C
refresh, see :func:`fetch_browser_via_node`).

Tuple contract: :func:`fetch_browser_via_node` returns ``(html, stats)`` with
exactly the same shape as :func:`fetch_with_browser` — ``stats`` carries
``login_wall`` (bool) and ``posts_found`` (int) — so the retry ladder in
``scrape_source_browser`` stays byte-for-byte unchanged.

Parity: a node outage or a browser launch/capture failure is a per-source
``ExtractionFailure`` (``browser_launch_failed`` / ``browser_capture_failed``
/ ``network_error``), never a whole-job failure (fault-isolation invariant).
"""

from __future__ import annotations

import base64
import time
from typing import Dict, List, Optional, Tuple

import httpx

from backend.core.config import get_settings
from backend.core.logging import get_logger
from backend.scraper.errors import (
    ExtractionFailure,
    InvalidUrl,
    OperationCancelled,
    Timeout,
)

logger = get_logger("services.node_browser")

__all__ = [
    "NodeBrowserClient",
    "fetch_browser_via_node",
    "NODE_BROWSER_HTTP_TIMEOUT",
]

#: Total HTTP budget for one browser capture round trip.  The pipeline is
#: much slower than http-mode: headless launch + goto (30s) + 3s settle +
#: popup dismissal + up to MAX_SCROLL_ROUNDS (40) rounds of 1.5s scroll
#: waits, each with evaluate/content reads, plus final DOM serialization.
#: node marks its own hard ceiling well below this; the timeout is a belt
#: for a wedged browser, not the expected duration.
NODE_BROWSER_HTTP_TIMEOUT = 300.0

#: Fast transport failures that indicate node is briefly unreachable
#: (mid-deploy, restart, pod blip).  These are retried with short backoff;
#: "slow" failures (TimeoutException) and definitive envelope answers are
#: NOT — mirror of node_fetch.py's rule (a stuck node must surface as a
#: per-source error, not a retry storm).
_RETRYABLE_TRANSPORT_ERRORS = (
    httpx.ConnectError,
    httpx.ReadError,
    httpx.RemoteProtocolError,
)


def serialize_cookies(cookies: List[dict]) -> List[str]:
    """Serialize Playwright-style cookie dicts to RFC 6265 cookie lines.

    ``cookies`` are the internal jar shape shared by every capture path
    (``save_cookies`` / ``load_cookies`` / ``parse_cookies_txt``)::

        {"name": "c_user", "value": "123",
         "domain": ".facebook.com", "path": "/",
         "expires": -1, "httpOnly": True, "secure": True,
         "sameSite": "Lax"}

    Each comes out as a single ``"name=value; Domain=..; Path=..;
    [Expires=..] [Secure] [HttpOnly]"`` line — the exact wire format node's
    ``FetchRequest.cookies`` carries (finalplanv2 §12; golden fixture
    ``fetch_request.browser.json``).  Session cookies (``expires == -1`` or
    ``0``) omit the ``Expires=`` attribute; secure/httpOnly flags appear
    only when set.
    """
    lines: List[str] = []
    for c in cookies:
        name = c.get("name")
        value = c.get("value")
        if not name or value is None:
            continue
        parts = [f"{name}={value}"]
        if c.get("domain"):
            parts.append(f"Domain={c['domain']}")
        if c.get("path"):
            parts.append(f"Path={c['path']}")
        expires = c.get("expires")
        if isinstance(expires, (int, float)) and expires not in (-1, 0):
            parts.append(f"Expires={int(expires)}")
        if c.get("secure"):
            parts.append("Secure")
        if c.get("httpOnly"):
            parts.append("HttpOnly")
        lines.append("; ".join(parts))
    return lines


def _compose_account_id(account_name: Optional[str], owner_id: Optional[int]) -> str:
    """Compose the ``FetchRequest.account_id`` for a saved session.

    Mirrors the ops/me split in ``parse_account_spec``: ``owner_id=None`` is
    the global ops pool (``ops:<name>``), otherwise the caller's personal
    store (``me:<name>``).  Empty string = no session (section comments in
    the proto; node treats it as anonymous).
    """
    if not account_name:
        return ""
    scope = "me" if owner_id is not None else "ops"
    return f"{scope}:{account_name}"


def deserialize_cookies(lines: List[str]) -> List[dict]:
    """Deserialize RFC 6265 cookie lines back into the internal jar shape.

    The exact inverse of :func:`serialize_cookies` — node dumps the post-
    capture browser context on ``FetchResponse.updated_cookies`` (Slice C
    refresh) and this turns those lines back into the Playwright-style dicts
    ``save_cookies`` expects::

        {"name": "xs", "value": "...", "domain": ".facebook.com",
         "path": "/", "expires": -1, "httpOnly": True, "secure": True}

    ``Expires=`` present -> epoch int; absent (a session cookie) -> ``-1``
    (the internal sentinel ``serialize_cookies`` omits).  ``Secure`` /
    ``HttpOnly`` flags become booleans.  Malformed lines and entries without
    a ``name=value`` pair are dropped.
    """
    jar: List[dict] = []
    for line in lines:
        parts = line.split(";")
        nv = parts[0].strip()
        eq = nv.find("=")
        if eq <= 0:
            continue
        name = nv[:eq].strip()
        value = nv[eq + 1 :].strip()
        if not name:
            continue
        cookie: dict = {
            "name": name,
            "value": value,
            "domain": "",
            "path": "/",
            "expires": -1,
            "httpOnly": False,
            "secure": False,
        }
        for raw in parts[1:]:
            attr = raw.strip()
            if not attr:
                continue
            aeq = attr.find("=")
            key = (attr if aeq == -1 else attr[:aeq]).strip().lower()
            val = None if aeq == -1 else attr[aeq + 1 :].strip()
            if key == "domain" and val:
                cookie["domain"] = val
            elif key == "path" and val:
                cookie["path"] = val
            elif key == "expires":
                try:
                    ts = int(float(val))
                except (TypeError, ValueError):
                    continue
                if ts > 0:
                    cookie["expires"] = ts
            elif key == "secure":
                cookie["secure"] = True
            elif key == "httponly":
                cookie["httpOnly"] = True
        if not cookie["domain"] or not cookie["name"]:
            continue
        jar.append(cookie)
    return jar


def _persist_refreshed_cookies(
    lines: List[str],
    account_name: Optional[str],
    owner_id: Optional[int],
) -> None:
    """Persist node's post-capture session back into the saved cookie store.

    Slice C refresh (finalplanv2 §12): the browser the capture ran in ends
    with a fresher session than the jar we started from (Facebook rotates
    cookies across a live session).  ``lines`` are the RFC 6265
    ``updated_cookies`` node dumped from that context; they are deserialized
    to the internal jar shape and saved to the same scope (``account_name`` /
    ``owner_id``) the attempt loaded from, so the next job starts from the
    refreshed session instead of the stale one.

    DB access stays Python-only (finalplanv2 §2/§7); node never persists.  A
    refresh failure must never fail the scrape — the capture already
    succeeded, and a stale jar is recoverable — so ``save_cookies`` errors are
    logged and swallowed here.
    """
    from backend.scraper.browser_scraper import save_cookies

    jar = deserialize_cookies(lines)
    if not jar:
        return
    try:
        save_cookies(jar, account_name, owner_id)
        logger.info(
            "Refreshed saved session cookies for account=%r owner_id=%s "
            "(%d cookies)",
            account_name,
            owner_id,
            len(jar),
        )
    except Exception as exc:  # pragma: no cover - best-effort refresh
        logger.warning("Failed to persist refreshed cookies: %s", exc)


class NodeBrowserClient:
    """Small HTTP client for node ``POST /fetch`` ``mode="browser"``.

    One instance per source attempt (constructed by
    :func:`fetch_browser_via_node` or injected in tests).  Not thread-safe by
    design — concurrency of 1 per source mirrors the Python browser path.
    """

    def __init__(
        self,
        base_url: str,
        *,
        transport: Optional[httpx.BaseTransport] = None,
        timeout: float = NODE_BROWSER_HTTP_TIMEOUT,
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

    def __enter__(self) -> "NodeBrowserClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- low-level POST -----------------------------------------------------
    def capture(self, url: str, *, account_id: str = "", scroll_rounds: int = 0,
                max_posts: int = 0, cookies: Optional[List[str]] = None,
                progress_callback=None) -> Tuple[str, Dict[str, object], List[str]]:
        """POST one browser capture to node and map it into ``(html, stats)``.

        ``stats`` mirrors ``fetch_with_browser``'s return contract:
        ``login_wall`` (bool) and ``posts_found`` (int).  The broker also
        carries ``feed_missing`` through for diagnostics, exactly matching
        ``FetchResponse.browser_stats``.

        Also returns the RFC 6265 ``updated_cookies`` lines node dumped from
        the browser context after capture (Slice C refresh) as a third
        element — the caller decides whether to persist them; this client
        never writes anything.

        Raises the same ``ScraperError`` subtypes the Python browser path
        raises for the equivalent condition: ``browser_launch_failed`` /
        ``browser_capture_failed`` / ``network_error`` as ``ExtractionFailure``
        (stable node codes preserved), ``invalid_url`` -> ``InvalidUrl``,
        ``timeout`` -> ``Timeout``.
        """
        started = time.monotonic()
        response = self._post(
            url,
            {
                "target_url": url,
                "mode": "browser",
                "account_id": account_id,
                "scroll_rounds": scroll_rounds,
                "max_posts": max_posts,
                "cookies": list(cookies or []),
            },
        )
        if response.status_code == 200:
            return self._decode_success(url, response, progress_callback)

        # Non-200: node returns the unified error envelope
        # {"error": {"code", "message"}} — mirror of the fetch seam.
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
        raise _map_browser_error(code, message)

    def _decode_success(
        self, url: str, response: httpx.Response, progress_callback=None
    ) -> Tuple[str, Dict[str, object], List[str]]:
        try:
            data = response.json()
            status = int(data["status_code"])
            raw = str(data["raw_payload"])
            bstats = data.get("browser_stats") or {}
            stats = {
                "login_wall": bool(bstats.get("login_wall", False)),
                "feed_missing": bool(bstats.get("feed_missing", False)),
                "posts_found": int(bstats.get("posts_found", 0)),
            }
            updated = data.get("updated_cookies") or []
            if not isinstance(updated, list):
                updated = []
            # Malformed entries (non-str) must not poison the refresh path.
            refreshed = [str(line) for line in updated if isinstance(line, str)]
            html = _decode_payload(raw)
        except (ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
            raise ExtractionFailure(
                f"node returned a malformed FetchResponse for {url}",
                code="network_error",
            ) from exc
        if status != 200:
            # Browser mode has no variant ladder; anything else is a capture
            # failure even though the envelope came back with status 200.
            raise ExtractionFailure(
                f"browser capture for {url} reported status {status}",
                code="browser_capture_failed",
            )
        # Mirror fetch_with_browser's progress report (it reports per scroll
        # round; node reports once at the end with the final tally).
        if progress_callback is not None:
            try:
                progress_callback(posts_found=stats["posts_found"])
            except Exception:  # pragma: no cover - callback must never kill
                pass
        return html, stats, refreshed

    def _post(self, url: str, body: dict) -> httpx.Response:
        """POST ``/fetch`` with limited retry+backoff on *fast* transport
        failures (node mid-deploy / restart blips).

        ``TimeoutException`` is deliberately NOT retried: a wedged browser
        capture must surface as a per-source ``timeout`` error, not a retry
        storm.  Definitive envelope answers are returned as-is and never
        retried either.
        """
        backoff = 0.0
        for attempt in range(self.max_retries + 1):
            if attempt:
                time.sleep(backoff)
                backoff = min(backoff + 0.25, 1.0)
            try:
                response = self._client.post(
                    f"{self.base_url}/fetch", json=body
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


def fetch_browser_via_node(
    url: str,
    *,
    max_posts: Optional[int] = None,
    scroll_rounds: Optional[int] = None,
    account_name: Optional[str] = None,
    owner_id: Optional[int] = None,
    use_cookies: bool = True,
    cancel_event=None,
    client: Optional[NodeBrowserClient] = None,
    progress_callback=None,
) -> Tuple[str, Dict[str, object]]:
    """Capture one source's feed through node (the ``scrape_source_browser``
    browser-mode seam).

    Builds a client from settings unless one is injected (tests).  Cookies are
    loaded in Python (``load_cookies``) and serialized to RFC 6265 lines that
    ride on ``FetchRequest.cookies`` — DB access never crosses to node
    (finalplanv2 §2/§7).  ``client`` ownership: the caller owns any injected
    client; the client built here is closed before returning/raising.

    Slice C refresh: when this attempt actually used a saved session
    (``use_cookies`` and a non-empty jar was sent) and the capture did not end
    on a login wall, the ``updated_cookies`` lines node dumped from the
    post-capture browser context are persisted back to the same scope via
    :func:`save_cookies` — self-healing the saved session so the next
    job/attempt starts from fresher cookies.  Anonymous attempts (no jar) and
    wall captures never persist: node does not own the store, and we must not
    clobber a good jar with an expired/captcha cookie set.
    """
    from backend.scraper.browser_scraper import load_cookies

    if cancel_event is not None and cancel_event.is_set():
        raise OperationCancelled()

    cookies: List[str] = []
    account_id = ""
    if use_cookies:
        jar = load_cookies(account_name, owner_id)
        if jar:
            cookies = serialize_cookies(jar)
            account_id = _compose_account_id(account_name, owner_id)

    owns_client = client is None
    client = client or NodeBrowserClient(get_settings().node_base_url)
    try:
        html, stats, updated = client.capture(
            url,
            account_id=account_id,
            scroll_rounds=scroll_rounds or 0,
            max_posts=max_posts or 0,
            cookies=cookies,
            progress_callback=progress_callback,
        )
    finally:
        if owns_client:
            client.close()

    # Self-heal only for a genuine saved-session attempt that got past the
    # wall.  `cookies` non-empty proves a jar was actually sent (never a
    # clobber of the saved session by an anonymous run).
    if use_cookies and cookies and updated and not stats.get("login_wall"):
        _persist_refreshed_cookies(updated, account_name, owner_id)
    return html, stats


def _decode_payload(raw: str) -> str:
    """Decode ``FetchResponse.raw_payload`` (base64 bytes) into str UTF-8."""
    decoded = base64.b64decode(raw)
    return decoded.decode("utf-8", errors="replace")


def _map_browser_error(code: str, message: str) -> None:
    """Map a node browser-mode error code onto the scraper error taxonomy."""
    if code == "invalid_url":
        raise InvalidUrl(message)
    if code == "timeout":
        raise Timeout(message)
    # browser_launch_failed / browser_capture_failed / network_error /
    # internal_error and any unknown code -> per-source extraction failure,
    # preserving the stable node code (job_service persists `.code`).
    raise ExtractionFailure(message, code=code)