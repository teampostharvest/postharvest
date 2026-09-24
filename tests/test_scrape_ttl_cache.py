"""Hermetic TTL-cache proof for the snappy-UI seam (plan §8 runtime cache).

C's byte-paper today is genuinely sealed-blank: ``backend/core/`` has **zero**
cache seam — no ``cache.py``, no ``__init__``-level cache attribute — and the
node-browser suite (``tests/test_node_browser_seam.py``) proves the real
runtime *node-fetch* seam (:316/:449) is visited ``== 1`` across identical
scrapes **only because the seam itself is hermetically counted**, not
because a runtime cache exists.  C's byte-shape = the *cache leg* in front
of that same seam: a TTL cache whose byte is "two identical scrapes within
the TTL window → node seam visited EXACTLY ONCE (the 2nd identical scrape is
served from the hermetic cache, byte-equal, zero re-scrape)."

Hermeticity contract (byte-exact, same as the rest of the hermetic suite):

* zero network — the node fetch client is a counting hermetic double
* zero real browser — no playwright, no node, no :9332/:9334 listeners;
  the node fetch seam's lazy import is not even touched (the hermetic
  double IS the seam, byte-for-byte)
* zero env — nothing touches ``REDIS_URL`` / ``settings``; the cache is a
  plain in-process TTL dict with no external arming
* add-don't-replace — the pre-existing node-browser suite keeps its byte
  assertions untouched; C only *adds* hermetic coverage in front of the
  same real seam name it ships (``backend.core.cache.TTLCache`` + the
  ``with_ttl_cache`` wrapper that reads/writes it)
"""

from __future__ import annotations

import threading
from typing import Callable, List, Optional
from unittest.mock import patch

import pytest

from backend.core.cache import TTLCache

_URL_ALPHA = "https://www.facebook.com/hermetic-ttl-cache-alpha"
_FAKE_HTML = "<html><body>hermetic TTL cache byte — no node, no network</body></html>"


class _FakeFetch:
    """Hermetic double for the node seam's ``FetchResult``.

    Only JSON-roundtrip-safe fields (``.html``, ``.status_code``,
    ``.final_url``, ``.variant``) — the exact bytes a TTL cache can store
    and replay without holding a file handle or a live browser client.
    """

    def __init__(self, html: str, final_url: str) -> None:
        self.html = html
        self.status_code = 200
        self.final_url = final_url
        self.variant = "ttl-cache-double"


def _counted_node_double(calls: dict[str, int]) -> Callable:
    """Return a hermetic node seam double that counts its invocations."""

    def _double(normalized_url: str, cancel_event) -> _FakeFetch:
        calls["n"] += 1
        return _FakeFetch(_FAKE_HTML, final_url=normalized_url)

    return _double


def with_hermetic_ttl_cache(func, url, *, options, cancel_event, cache):
    """Run ``func`` with a hermetic cache mounted on the real consult slot.

    Mounts ``cache`` onto ``backend.scraper._SCRAPE_TTL_CONSULT`` — the
    consult byte the real node leg consults first (``_ttl_consult``) — arms
    it, calls ``func(url, ...)``, then restores the consult slot.  Byte-true
    to C's sealed consult seam, no env / no settings touched (hermetic).
    """
    import backend.scraper as scraper

    cache.arm(True)
    original = scraper._SCRAPE_TTL_CONSULT
    original_enabled = scraper._node_enabled
    scraper._SCRAPE_TTL_CONSULT = cache
    scraper._node_enabled = lambda: True  # hermetic: force the node leg
    try:
        return func(url, options=options, cancel_event=cancel_event)
    finally:
        scraper._SCRAPE_TTL_CONSULT = original
        scraper._node_enabled = original_enabled


def _scrape_twice_identical(
    scraper_module,
    *,
    is_second: bool = False,
) -> List:
    cancel_event = threading.Event()
    results = []
    for _ in ("first", "second") if is_second else ("first",):
        results.append(
            scraper_module.scrape_source(
                _URL_ALPHA,
                options={"urls": [_URL_ALPHA], "max_posts": 0},
                cancel_event=cancel_event,
            )
        )
    return results


def test_two_identical_scrapes_within_ttl_visit_node_once(monkeypatch):
    """Byte-seal: two identical scrapes within the TTL → node visited ONCE.

    This is C's proof-mold, byte-identical to how the node-browser suite
    proves its own seam (``test_node_browser_seam.py:671`` asserts
    ``len(served) == 1``): we reuse the exact hermetic-mold — a counting
    double on the node seam, two identical calls, and the byte is the
    *same* as it always was, just served from cache on the 2nd call.
    """
    import backend.scraper as scraper

    calls: dict[str, int] = {"n": 0}
    node_double = _counted_node_double(calls)
    cache = TTLCache(ttl_seconds=60)

    with patch(
        "backend.scraper._node_fetch_page",
        new=node_double,
    ):
        with_hermetic_ttl_cache(
            scraper.scrape_source,
            _URL_ALPHA,
            options={"urls": [_URL_ALPHA], "max_posts": 0},
            cancel_event=threading.Event(),
            cache=cache,
        )
        with_hermetic_ttl_cache(
            scraper.scrape_source,
            _URL_ALPHA,
            options={"urls": [_URL_ALPHA], "max_posts": 0},
            cancel_event=threading.Event(),
            cache=cache,
        )

    # THE BYTE: two identical scrapes, ONE node visit
    assert calls["n"] == 1


def test_two_identical_scrapes_after_ttl_expiry_visit_node_again(monkeypatch):
    """After the TTL window closes, the 2nd identical scrape re-visits node.

    Byte that keeps C honest: it is a TTL cache, *not* a permanent memoize
    — a genuinely-repeated page check after expiry is scraped afresh.
    """
    import backend.scraper as scraper

    calls: dict[str, int] = {"n": 0}
    node_double = _counted_node_double(calls)

    t = {"now": 0.0}

    def _clock() -> float:
        return t["now"]

    cache = TTLCache(ttl_seconds=60, clock=_clock)  # deterministic expiry

    with patch(
        "backend.scraper._node_fetch_page",
        new=node_double,
    ):
        with_hermetic_ttl_cache(
            scraper.scrape_source,
            _URL_ALPHA,
            options={"urls": [_URL_ALPHA], "max_posts": 0},
            cancel_event=threading.Event(),
            cache=cache,
        )
        t["now"] = 61.0  # the TTL window closes
        with_hermetic_ttl_cache(
            scraper.scrape_source,
            _URL_ALPHA,
            options={"urls": [_URL_ALPHA], "max_posts": 0},
            cancel_event=threading.Event(),
            cache=cache,
        )

    # zero TTL → the 2nd identical scrape is NOT served from cache →
    # node hit twice
    assert calls["n"] == 2
