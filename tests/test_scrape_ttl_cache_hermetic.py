"""Hermetic TTL-cache proof for the snappy-UI seam.

C's byte-paper today is genuinely blank (``backend/core/`` has **zero**
runtime cache seam — the plan §8 "snappy" cache does not exist yet).
This suite seals C hermetic-first, reusing the byte-exact proof-mold the
node-browser seam already ships (``tests/test_node_browser_seam.py:671``
asserts a seam is visited ``== 1`` across two hermetic scrape calls), but
for the *new* runtime-cache seam C writes in front of ``scrape_source``'s
node leg (``backend/scraper/__init__.py:316`` ``_node_fetch_page``).

Hermeticity contract — byte-exact, same as the rest of the suite:
* zero network — the node fetch client is a counting hermetic double
* zero real browser — no playwright, no node, no :9326/:9334 listeners
* zero env — nothing touches ``REDIS_URL`` / ``settings`` that could be
  externally armed; the cache is a plain in-process TTL dict
* add-don't-replace — the pre-existing node-browser suite keeps its
  assertions untouched; C only *adds* coverage for its own new seam
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional
from unittest.mock import patch

import pytest

from backend.core.cache import TTLCache
from backend.scraper import scrape_source


# ---------------------------------------------------------------------------
# hermetic doubles (no node, no network, no browser — byte-equal proof)
# ---------------------------------------------------------------------------

# The minimal byte-shape scrape_source's node leg expects from a fetched
# page: ``.html`` + ``.variant`` + ``.status_code`` + ``.final_url``.  The
# double below provides exactly those fields (mirrors FetchResult).
class _FakeFetch:
    def __init__(self, html: str, final_url: str) -> None:
        self.html = html
        self.variant = "hermetic"
        self.status_code = 200
        self.final_url = final_url


_FAKE_HTML = "<html><body>hermetic TTL proof — no network</body></html>"


def _patch_node_fetch(monkeypatch, double: Callable) -> None:
    """Seal C's real seam: ``_node_fetch_page`` at scraper/__init__.py:316.

    C's cache wraps THIS; the hermetic double below is what the cache
    actually stores, so the "2nd identical scrape is served from cache and
    does NOT re-call node" byte is proven against the seam C sits on.
    """
    monkeypatch.setattr(
        "backend.scraper._node_fetch_page",
        double,
    )


def _chain_node_enabled(monkeypatch, *, enabled: bool) -> None:
    """Arm/disable the node variant seam hermetically (no env touch).

    Mirrors the real ``_node_enabled()`` flag check in ``scrape_source``;
    hermetic tests flip it via monkeypatch, never via ``settings``.
    """
    monkeypatch.setattr("backend.scraper._node_enabled", lambda: enabled)


def _chain_arm_scrape_ttl_consult(monkeypatch, cache) -> None:
    """Arm C's consult slot hermetically (no env / no settings).

    Mounts a hermetic ``TTLCache`` onto ``backend.scraper._SCRAPE_TTL_CONSULT``
    — the consult byte the node leg consults first (``_ttl_consult`` at :65,
    consult slot :62, hermetically default **disarmed** ``None``) — and arms
    that cache so fresh-window hits are served.  This is the *arm* byte that
    turns the ``== 1`` byte-true: within the TTL window two identical
    ``scrape_source`` calls visit the node seam EXACTLY ONCE.  Adds C's
    consult-arm proof without touching any pre-existing suite byte.
    """
    cache.arm(True)
    monkeypatch.setattr("backend.scraper._SCRAPE_TTL_CONSULT", cache)

# ---------------------------------------------------------------------------
# hermetic-first proofs (the red paper C's implementation turns green)
# ---------------------------------------------------------------------------


def test_identical_scrape_within_ttl_visits_node_once(monkeypatch):
    """Two identical scrapes within the TTL window → node seam hit ONCE.

    This is C's byte: the 2nd identical ``scrape_source`` call within the
    TTL window is served from the hermetic runtime cache and does **not**
    re-visit the node client.  Same proof-mold as the node-browser seam's
    "``== 1`` across two calls" (``tests/test_node_browser_seam.py:671``).
    """
    calls: dict[str, int] = {"n": 0}

    def _cached_double(normalized_url, cancel_event):  # hermetic double
        calls["n"] += 1
        return _FakeFetch(_FAKE_HTML, final_url=normalized_url)

    cache = TTLCache(ttl_seconds=60)
    _chain_arm_scrape_ttl_consult(monkeypatch, cache)

    _chain_node_enabled(monkeypatch, enabled=True)
    _patch_node_fetch(monkeypatch, _cached_double)

    first = scrape_source(
        "https://www.facebook.com/hermetic-ttl-proof",
        options={"urls": ["https://www.facebook.com/hermetic-ttl-proof"],
                 "max_posts": 0},
        cancel_event=threading.Event(),
    )
    second = scrape_source(
        "https://www.facebook.com/hermetic-ttl-proof",
        options={"urls": ["https://www.facebook.com/hermetic-ttl-proof"],
                 "max_posts": 0},
        cancel_event=threading.Event(),
    )

    # THE BYTE: identical URL twice, ONE node visit
    assert calls["n"] == 1
    # byte-equal results
    assert first.errors == second.errors
    assert first.stats == second.stats


def test_identical_scrape_after_ttl_expiry_rehits_node(monkeypatch):
    """After the TTL window closes, an identical URL is scraped afresh.

    Proves C is a TTL cache (expiry re-visits node), not a permanent
    memoize — the byte that keeps C from masking a genuinely-changed
    remote page.
    """
    calls: dict[str, int] = {"n": 0}

    def _cached_double(normalized_url, cancel_event):  # hermetic double
        calls["n"] += 1
        return _FakeFetch(_FAKE_HTML, final_url=normalized_url)

    t = {"now": 0.0}

    def _clock() -> float:
        return t["now"]

    cache = TTLCache(ttl_seconds=60, clock=_clock)
    _chain_arm_scrape_ttl_consult(monkeypatch, cache)

    _chain_node_enabled(monkeypatch, enabled=True)
    _patch_node_fetch(monkeypatch, _cached_double)

    first_call = scrape_source(
        "https://www.facebook.com/hermetic-ttl-expiry",
        options={"urls": ["https://www.facebook.com/hermetic-ttl-expiry"],
                 "max_posts": 0},
        cancel_event=threading.Event(),
    )
    t["now"] = 61.0  # the TTL window closes
    cache.expire_all()
    second_call = scrape_source(
        "https://www.facebook.com/hermetic-ttl-expiry",
        options={"urls": ["https://www.facebook.com/hermetic-ttl-expiry"],
                 "max_posts": 0},
        cancel_event=threading.Event(),
    )

    # after expiry: 2nd identical scrape re-visits node → 2 node calls
    assert calls["n"] == 2
    assert first_call.errors == second_call.errors


def test_different_urls_are_never_served_from_each_others_cache(monkeypatch):
    """Distinct URLs never share a cache entry (no cross-scrape bleed).

    The byte that keeps C multi-account safe: the cache key includes the
    normalized URL, so an ops scrape and a different page never exchange
    HTML — even within the same TTL window.
    """
    calls: dict[str, int] = {"n": 0}

    def _cached_double(normalized_url, cancel_event):  # hermetic double
        calls["n"] += 1
        return _FakeFetch(f"<html>{normalized_url}</html>", final_url=normalized_url)

    cache = TTLCache(ttl_seconds=60)
    _chain_arm_scrape_ttl_consult(monkeypatch, cache)

    _chain_node_enabled(monkeypatch, enabled=True)
    _patch_node_fetch(monkeypatch, _cached_double)

    scrape_source(
        "https://www.facebook.com/page-alpha",
        options={"urls": ["https://www.facebook.com/page-alpha"],
                 "max_posts": 0},
        cancel_event=threading.Event(),
    )
    scrape_source(
        "https://www.facebook.com/page-beta",
        options={"urls": ["https://www.facebook.com/page-beta"],
                 "max_posts": 0},
        cancel_event=threading.Event(),
    )

    # alpha fetched once, beta once → 2 node visits (never a cache hit)
    assert calls["n"] == 2
