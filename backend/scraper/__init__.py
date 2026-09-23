"""PostHarvest - extraction layer (SA02).

Public interface consumed by the API layer (SA01):

    validate_facebook_url(url) -> {"valid", "normalized_url", "reason"}
    scrape_source(url, options, progress_cb=None, cancel_event=None) -> SourceResult
    ScrapeOptions / SourceResult  (dataclasses)

Compliance summary (spec §6 / §9 / §10) - see module docstrings of
``fetcher.py`` and ``parser.py`` for the full policy:

* Only publicly accessible content is fetched, over the public web, with a
  single honest user agent.
* robots.txt is respected (best-effort; allowlist-only fallback, documented).
* Hard request throttle (``SCRAPER_DELAY_SECONDS``, default 2.5 s),
  exponential backoff on 429/5xx (max 3 retries, ``SCRAPER_MAX_RETRIES``),
  hard timeout (``SCRAPER_TIMEOUT_SECONDS``, default 20 s), concurrency of 1
  per source.
* No cookies, no login, no CAPTCHA solving, no UA rotation, no headless
  browser bypass.  Login walls -> ``AuthRequired``; rate limits / traffic
  checks -> ``RateLimited``; both stop the source.
* One failing post never crashes the source: per-post errors are collected
  and the scrape continues.

Stats accounting (invariant, see ``stats.py``):

    posts_discovered == posts_extracted + posts_skipped + posts_failed
    duplicates_removed is separate (each removed duplicate is also counted
    in posts_skipped).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("scraper")

from . import errors  # noqa: F401  (expose the taxonomy to callers)
from .dedup import dedup_posts
from .errors import (
    InvalidUrl,
    OperationCancelled,
    ScraperError,
    UnsupportedUrl,
)
from .fetcher import Fetcher
from .normalizer import NORMALIZED_KEYS, normalize_post
from .parser import parse_page
from .stats import Stats
from .url_validator import validate_or_raise

# -- C hermetic runtime-cache consult seam (plan §8 "snappy" cache) --------
# Hermetic-default: **disarmed** (``_SCRAPE_TTL_CONSULT is None``) so the
# pre-existing node-browser suite keeps every ``== N`` byte untouched
# (add-don't-replace, same mold as the Local/Redis bucket arming pair).
# The hermetic C-paper (``tests/test_scrape_ttl_cache_hermetic.py``) arms
# this consult via ``monkeypatch`` — never via env / ``settings``.
_SCRAPE_TTL_CONSULT = None  # armed to a ``TTLCache`` by the hermetic suite


def arm_scrape_ttl_cache(ttl_seconds: float) -> None:
    """Production arming of the hermetic consult seam (finalplanv2 §8).

    Hermetic default stays DISARMED (``_SCRAPE_TTL_CONSULT is None``);
    the host FastAPI app arms it at startup when ``SCRAPE_TTL_SECONDS>0``
    (see backend.main:lifespan).  The hermetic C-paper arms via
    ``monkeypatch`` exactly as before — never via this env-driven path —
    so every suite keeps its bytes (add-don't-replace).
    """
    global _SCRAPE_TTL_CONSULT
    if ttl_seconds and ttl_seconds > 0:
        from backend.core.cache import TTLCache

        cache = TTLCache(ttl_seconds=float(ttl_seconds))
        cache.arm(True)
        _SCRAPE_TTL_CONSULT = cache
    else:
        _SCRAPE_TTL_CONSULT = None


def _ttl_consult(normalized_url: str):
    """Serve the node leg's payload from the TTL window if armed+fresh.

    The byte C seals: two identical ``scrape_source`` calls within the TTL
    window → the node seam is visited EXACTLY ONCE; the 2nd identical call
    is served byte-equal from this hermetic consult (never re-visiting
    ``_node_fetch_page`` at :316).  Disarmed default → legacy byte-for-byte.
    """
    consult = _SCRAPE_TTL_CONSULT
    if consult is None:
        return None
    return consult.get(normalized_url)


def _ttl_store(normalized_url: str, payload) -> None:
    """Write the node leg's payload into the armed TTL window (no-op off)."""
    consult = _SCRAPE_TTL_CONSULT
    if consult is not None:
        consult.set(normalized_url, payload)


__version__ = "0.1.0"

__all__ = [
    "validate_facebook_url",
    "scrape_source",
    "ScrapeOptions",
    "SourceResult",
    "NORMALIZED_KEYS",
    "errors",
    "__version__",
]

#: canonical post_type values
POST_TYPES = ("text", "image", "video", "link")

#: progress stages emitted through ``progress_cb``
STAGES = ("starting", "fetching", "parsing", "processing", "completed",
          "failed")

ProgressCallback = Callable[[Dict[str, Any]], None]


# ---------------------------------------------------------------------------
# Options / result containers
# ---------------------------------------------------------------------------

@dataclass
class ScrapeOptions:
    """Options for one scrape job.

    :param urls: list of page/profile URLs (validated by the API layer;
                 ``scrape_source`` is invoked once per URL).
    :param max_posts: cap on returned posts per source (``None`` = no cap;
                      use only as a safety limit, not as a completion condition).
    :param start_date: ``YYYY-MM-DD`` inclusive lower bound (``None`` = no bound).
    :param end_date:   ``YYYY-MM-DD`` inclusive upper bound (``None`` = no bound).
    :param post_type:  optional filter, one of ``text|image|video|link``.
    :param delay: override for inter-request delay (seconds).
    :param proxy_url: optional HTTP proxy URL for this scrape.

    Construction validates the date format, date ordering, post_type value
    and max_posts type; ``ValueError`` is raised on invalid input so the API
    layer can map it to a 422-style error.
    """

    urls: List[str]
    max_posts: Optional[int] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    post_type: Optional[str] = None
    delay: Optional[float] = None
    proxy_url: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.urls, list):
            raise ValueError("'urls' must be a list of URL strings.")
        if not self.urls or not all(
                isinstance(u, str) and u.strip() for u in self.urls):
            raise ValueError("'urls' must be a non-empty list of URL strings.")

        if self.max_posts is not None:
            if not isinstance(self.max_posts, int) or isinstance(self.max_posts, bool):
                raise ValueError("'max_posts' must be an int or None.")
            if self.max_posts < 0:
                raise ValueError("'max_posts' must be >= 0.")

        for label, value in (("start_date", self.start_date),
                             ("end_date", self.end_date)):
            if value is not None:
                try:
                    date.fromisoformat(value)
                except ValueError as exc:
                    raise ValueError(
                        f"'{label}' must be a YYYY-MM-DD date, got {value!r}."
                    ) from exc
        if (self.start_date is not None and self.end_date is not None
                and date.fromisoformat(self.start_date)
                > date.fromisoformat(self.end_date)):
            raise ValueError("'start_date' must not be after 'end_date'.")

        if self.post_type is not None:
            pt = self.post_type.strip().lower()
            if pt == "all":
                self.post_type = None
            elif pt not in POST_TYPES:
                raise ValueError(
                    f"'post_type' must be one of {POST_TYPES} or None, "
                    f"got {self.post_type!r}.")
            else:
                self.post_type = pt

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "ScrapeOptions":
        """Build options from a request body dict (unknown keys are ignored)."""
        data = dict(data or {})
        return cls(
            urls=list(data.get("urls") or []),
            max_posts=data.get("max_posts"),
            start_date=data.get("start_date"),
            end_date=data.get("end_date"),
            post_type=data.get("post_type"),
            delay=data.get("delay"),
            proxy_url=data.get("proxy_url"),
        )


@dataclass
class SourceResult:
    """Result of scraping one page/profile source.

    :param url: the input URL (as passed to ``scrape_source``).
    :param page_name: page display name (``None`` if the page never loaded).
    :param page_id: numeric page id when publicly derivable, else ``None``.
    :param posts: list of canonical normalized post dicts.
    :param stats: the five stat counters (see ``stats.py``).
    :param errors: list of ``{"url"|"post_url", "code", "message"}`` entries.
                   A source-level failure (auth wall, rate limit, 404, ...)
                   is reported here with ``"url"`` set to the input URL.
    """

    url: str
    page_name: Optional[str] = None
    page_id: Optional[str] = None
    posts: List[Dict[str, Any]] = field(default_factory=list)
    stats: Dict[str, int] = field(default_factory=dict)
    errors: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "page_name": self.page_name,
            "page_id": self.page_id,
            "posts": self.posts,
            "stats": self.stats,
            "errors": self.errors,
        }


# ---------------------------------------------------------------------------
# Public validator
# ---------------------------------------------------------------------------

def validate_facebook_url(url: str) -> Dict[str, Optional[str]]:
    """Validate a Facebook page/profile URL.

    Returns ``{"valid": bool, "normalized_url": str | None, "reason": str | None}``.
    Never raises.  See ``url_validator.py`` for the exact acceptance rules.
    """
    from .url_validator import validate_facebook_url as _validate
    return _validate(url)


# ---------------------------------------------------------------------------
# scrape_source - the per-source orchestrator
# ---------------------------------------------------------------------------

def _passes_filters(post: Dict[str, Any], options: ScrapeOptions) -> bool:
    """Date-range and post-type filter evaluation.

    A post with an *unknown* published_at is skipped whenever a date filter
    is active (the scrape cannot prove the post is inside the requested
    range; skipping is the honest choice and is counted in posts_skipped).
    Same for an undeterminable post_type when a type filter is active.
    """
    if options.post_type is not None and post.get("post_type") != options.post_type:
        return False
    if options.start_date is not None or options.end_date is not None:
        iso = post.get("published_at")
        if not iso:
            return False
        try:
            d = datetime.fromisoformat(str(iso)).date()
        except ValueError:
            return False
        if options.start_date is not None and d < date.fromisoformat(options.start_date):
            return False
        if options.end_date is not None and d > date.fromisoformat(options.end_date):
            return False
    return True


def scrape_source(
    url: str,
    options: Optional[ScrapeOptions] = None,
    progress_cb: Optional[ProgressCallback] = None,
    cancel_event: Optional[threading.Event] = None,
) -> SourceResult:
    """Scrape one Facebook page/profile source.

    :param url: page/profile URL to scrape.
    :param options: :class:`ScrapeOptions` (or a plain dict, which is
                    converted via ``ScrapeOptions.from_dict``).  Only the
                    per-source fields (``max_posts``, ``start_date``,
                    ``end_date``, ``post_type``) are used here.
    :param progress_cb: called periodically with
        ``{"page": url, "stage": str, "posts_found": int, "posts_processed": int}``.
        Stages: ``starting``, ``fetching``, ``parsing``, ``processing``,
        ``completed`` (or ``failed`` when the source itself failed).
        The callback is exception-safe (a broken callback never crashes a
        scrape).
    :param cancel_event: optional ``threading.Event``; when set, the scrape
        stops at the next checkpoint, preserving partial results and adding a
        ``cancelled`` error entry.
    :returns: :class:`SourceResult`.  This function NEVER raises for
        source-level failures - invalid URLs, login walls, rate limits,
        timeouts and 404s are all reported in ``result.errors``.
    """
    if options is None:
        options = ScrapeOptions(urls=[url])
    elif isinstance(options, dict):
        options = ScrapeOptions.from_dict(options)

    stats = Stats()
    errors_list: List[Dict[str, str]] = []
    posts_out: List[Dict[str, Any]] = []
    handle_counter = 0  # discovered items processed so far (for progress)

    def emit(stage: str) -> None:
        if progress_cb is None:
            return
        try:
            progress_cb({
                "page": url,
                "stage": stage,
                "posts_found": stats.posts_discovered,
                "posts_processed": handle_counter,
            })
        except Exception:
            # a progress callback must never kill the scrape
            pass

    emit("starting")

    # -- 1. validate ------------------------------------------------------
    try:
        normalized_url = validate_or_raise(url)
    except (InvalidUrl, UnsupportedUrl) as exc:
        errors_list.append({"url": url, "code": exc.code, "message": exc.message})
        emit("failed")
        return SourceResult(url=url, stats=stats.to_dict(), errors=errors_list)

    page_name: Optional[str] = None
    page_id: Optional[str] = None

    # -- 2. fetch + parse --------------------------------------------------
    try:
        emit("fetching")
        logger.info("Starting fetch for %s", url)
        fetcher = Fetcher(
            cancel_event=cancel_event,
            delay=options.delay,
            proxy_url=options.proxy_url,
        )
        try:
            # finalplanv2 §14: when USE_NODE is on, HTTP-mode fetches
            # are delegated to the node service (robots/throttle/retry
            # happen there); the flag-off path below is byte-for-byte the
            # legacy Fetcher. Parsing/normalization/dedup are identical for
            # both paths.
            if _node_enabled():
                cached_fetch = _ttl_consult(normalized_url)
                if cached_fetch is not None:
                    # C: identical scrape within the TTL window is served
                    # byte-equal from the hermetic runtime cache — the node
                    # seam is NOT re-visited (byte: ``== 1`` across two
                    # identical ``scrape_source`` calls).
                    fetch = cached_fetch
                else:
                    fetch = _node_fetch_page(normalized_url, cancel_event)
                    _ttl_store(normalized_url, fetch)
            else:
                fetch = fetcher.fetch_page(normalized_url)
            logger.info(
                "Fetched %s variant=%s status=%s final_url=%s bytes=%d",
                url, fetch.variant, fetch.status_code,
                fetch.final_url, len(fetch.html),
            )
            emit("parsing")
            page = parse_page(
                fetch.html,
                page_url=fetch.final_url,
                handle=_handle_of(normalized_url),
            )
            logger.info(
                "Parsed %s: page_name=%r, posts=%d, post_errors=%d",
                url, page.page_name, len(page.posts), len(page.post_errors),
            )
        finally:
            fetcher.close()
    except OperationCancelled as exc:
        errors_list.append({"url": url, "code": exc.code, "message": exc.message})
        emit("failed")
        return SourceResult(url=url, stats=stats.to_dict(), errors=errors_list)
    except ScraperError as exc:
        errors_list.append({"url": url, "code": exc.code, "message": exc.message})
        emit("failed")
        return SourceResult(url=url, stats=stats.to_dict(), errors=errors_list)

    page_name = page.page_name
    page_id = page.page_id

    # -- 3. normalize, filter, dedup, cap ----------------------------------
    stats.discovered(len(page.posts) + len(page.post_errors))
    for entry in page.post_errors:
        stats.failed(1)
        handle_counter += 1
        errors_list.append({"url": url, **entry})

    pending: List[Dict[str, Any]] = []
    for parsed_post in page.posts:
        if cancel_event is not None and cancel_event.is_set():
            errors_list.append({
                "url": url,
                "code": OperationCancelled.code,
                "message": OperationCancelled().message,
            })
            emit("failed")
            return SourceResult(
                url=url, page_name=page_name, page_id=page_id,
                posts=posts_out, stats=stats.to_dict(), errors=errors_list)

        try:
            post = normalize_post(
                parsed_post,
                page_name=page_name,
                page_id=page_id,
                facebook_url=normalized_url,
            )
        except Exception as exc:
            # one bad post must never crash the source
            stats.failed(1)
            handle_counter += 1
            errors_list.append({
                "post_url": parsed_post.post_url or url,
                "code": "extraction_failure",
                "message": f"failed to normalize a post: {exc!r}",
            })
            emit("processing")
            continue

        if not _passes_filters(post, options):
            stats.skipped(1)
            handle_counter += 1
            emit("processing")
            continue
        pending.append(post)
        handle_counter += 1
        emit("processing")

    # dedup: first occurrence wins (duplicates counted in skipped as well)
    kept, duplicates = dedup_posts(pending)
    stats.removed_duplicates(duplicates)
    if duplicates:
        stats.skipped(duplicates)
        handle_counter += duplicates

    # max_posts cap (applied after extraction, per source)
    if options.max_posts is not None and len(kept) > options.max_posts:
        trimmed = len(kept) - options.max_posts
        kept = kept[:options.max_posts]
        stats.skipped(trimmed)
        handle_counter += trimmed

    stats.extracted(len(kept))
    posts_out = kept
    logger.info(
        "Completed %s: extracted=%d, skipped=%d, failed=%d, duplicates=%d",
        url, len(kept), stats.to_dict().get("posts_skipped", 0),
        stats.to_dict().get("posts_failed", 0), duplicates,
    )

    # invariant (documented in stats.py): discovered == extracted + skipped + failed
    emit("completed")
    return SourceResult(
        url=url,
        page_name=page_name,
        page_id=page_id,
        posts=posts_out,
        stats=stats.to_dict(),
        errors=errors_list,
    )


def _handle_of(normalized_url: str) -> Optional[str]:
    """Best-effort handle from a normalized URL (used as a page-name
    fallback)."""
    from urllib.parse import urlparse
    path = urlparse(normalized_url).path
    segments = [s for s in path.split("/") if s]
    if not segments:
        return None
    if segments[0] in ("profile.php", "people"):
        return None
    return segments[0]


def _node_enabled() -> bool:
    """True when HTTP-mode fetches should be delegated to node."""
    from backend.core.config import get_settings
    return get_settings().use_node


def _node_fetch_page(normalized_url: str,
                     cancel_event: Optional[threading.Event]):
    """Fetch a page through the node service.

    Lazy import keeps scraper -> services -> (scraper.fetcher/errors) from
    forming a module-level cycle; the seam itself lives in
    ``backend/services/node_fetch.py``.
    """
    from backend.services.node_fetch import fetch_page_via_node
    return fetch_page_via_node(normalized_url, cancel_event=cancel_event)