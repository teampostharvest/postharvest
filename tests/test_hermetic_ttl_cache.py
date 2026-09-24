"""Hermetic TTL-cache proof for C's byte: is the runtime cache RED or sealed?

Hermetic law — byte-exact, same as the sibling node-browser suite
(``tests/test_node_browser_seam.py:671`` asserts ``len(served) == 1`` for
two identical scrapes):

* **zero network** — the node seam is a hermetic counting double; nothing
  ever reaches ``:9332/:9334`` or any network socket
* **zero real browser** — no playwright, no node, no ``:9326`` listener;
  the *real* seam (``backend.scraper._node_fetch_page``, lazy-imports the
  node client at ``:457``) is what's patched, byte-true
* **zero env** — nothing touches ``REDIS_URL``/``settings``; the hermetic
  cache is a plain in-process TTL dict with a fake clock, byte-provable
* **add-don't-replace** — the sibling node suite keeps its assertions
  untouched; C only adds hermetic coverage

C's proof-mold: **two identical scrapes within TTL → node seam visited
EXACTLY ONCE** (the 2nd identical scrape is *hermetic-served* from the
runtime cache, the node-client seam is not re-visited).  This is the byte
that makes plan §8 "snappy UI" a hermetic fact: a second glance at the
*exact same URL* within the TTL window never takes the ~670ms node leg
again — the UI's profile re-render reads the cached bytes.
"""
from __future__ import annotations

import threading
from typing import Optional
from unittest.mock import patch

from backend.core.cache import TTLCache
from backend.scraper import scrape_source

_FAKE_HTML = "<html><body>hermetic TTL proof — no node, no browser</body></html>"
_FAKE_URL = "https://www.facebook.com/hermetic-ttl-snappy"


class _FakeFetch:
    """Hermetic double for the real node seam's return shape.

    Byte-true to what ``backend.scraper.parse_page`` consumes — only
    ``.html``/``.status_code``/``.final_url``/``.variant`` fields, exactly
    the bytes ``backend.services.node_fetch.fetch_page_via_node`` returns.
    """

    def __init__(self, html: str, final_url: str) -> None:
        self.html = html
        self.status_code = 200
        self.final_url = final_url
        self.variant = "hermetic-ttl-double"


def _arm_consult_slot(monkeypatch, cache) -> None:
    """Mount a hermetic TTL cache on the real consult slot.

    ``backend.scraper._SCRAPE_TTL_CONSULT`` is the consult byte the real
    node leg consults first (``_ttl_consult``); arming it hermetically (no
    env / no settings) is what turns the ``== 1`` byte-true.
    """
    cache.arm(True)
    monkeypatch.setattr("backend.scraper._SCRAPE_TTL_CONSULT", cache)
    monkeypatch.setattr("backend.scraper._node_enabled", lambda: True)


def test_identical_scrape_within_ttl_visits_node_seam_once(monkeypatch):
    """Two identical ``scrape_source`` calls within the TTL window span
    EXACTLY ONE node-seam visit.

    C's sealed byte: the 2nd identical scrape is served byte-equal from
    the hermetic runtime cache — the *real* node seam
    (``backend.scraper._node_fetch_page``) is visited ``== 1`` across a
    pair of identical scrapes, byte for byte the same proof-shape the
    node-browser suite already seals for its seam
    (``test_node_browser_seam.py:671``: ``assert len(served) == 1``).
    """
    calls: dict[str, int] = {"n": 0}

    def _node_double(normalized_url, cancel_event):  # hermetic double
        calls["n"] += 1
        return _FakeFetch(_FAKE_HTML, final_url=normalized_url)

    cache = TTLCache(ttl_seconds=60, clock=lambda: 0.0)

    with patch(
        "backend.scraper._node_fetch_page",
        new=_node_double,
    ):
        _arm_consult_slot(monkeypatch, cache)
        first = scrape_source(
            _FAKE_URL,
            options={"urls": [_FAKE_URL], "max_posts": 0},
            cancel_event=threading.Event(),
        )
        second = scrape_source(
            _FAKE_URL,
            options={"urls": [_FAKE_URL], "max_posts": 0},
            cancel_event=threading.Event(),
        )

    # THE BYTE: one node visit across two identical scrapes
    assert calls["n"] == 1
    # byte-equal results (2nd served from TTL cache, identical bytes)
    assert first.errors == second.errors
    assert first.stats == second.stats


def test_cache_expiry_rehits_node_seam(monkeypatch):
    """After the TTL window closes, an identical scrape re-visits the seam.

    The byte that keeps C from *over*-sealing: a real scrape after expiry
    MUST hit the node seam again — the cache decays, it does not memoize
    forever.
    """
    calls: dict[str, int] = {"n": 0}

    def _node_double(normalized_url, cancel_event):
        calls["n"] += 1
        return _FakeFetch(_FAKE_HTML, final_url=normalized_url)

    t = {"now": 0.0}

    def _clock():
        return t["now"]

    cache = TTLCache(ttl_seconds=60, clock=_clock)

    with patch(
        "backend.scraper._node_fetch_page",
        new=_node_double,
    ):
        _arm_consult_slot(monkeypatch, cache)
        scrape_source(
            _FAKE_URL,
            options={"urls": [_FAKE_URL], "max_posts": 0},
            cancel_event=threading.Event(),
        )
        t["now"] = 61.0  # TTL window closes
        scrape_source(
            _FAKE_URL,
            options={"urls": [_FAKE_URL], "max_posts": 0},
            cancel_event=threading.Event(),
        )

    # after expiry: 2nd identical scrape re-visits the seam → 2 calls
    assert calls["n"] == 2


def test_different_urls_never_share_a_cache_entry(monkeypatch):
    """Different URLs never share cache bytes — the multi-account safety byte.

    The byte that keeps C multi-VPS/output separable: a ``page-alpha``
    scrape and a ``page-beta`` scrape never exchange HTML even within the
    same TTL window — the cache key includes the *full normalized URL*.
    """
    calls: dict[str, int] = {"n": 0}

    def _node_double(normalized_url, cancel_event):
        calls["n"] += 1
        return _FakeFetch(f"<html>{normalized_url}</html>", final_url=normalized_url)

    cache = TTLCache(ttl_seconds=60, clock=lambda: 0.0)

    with patch(
        "backend.scraper._node_fetch_page",
        new=_node_double,
    ):
        _arm_consult_slot(monkeypatch, cache)
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

    # two distinct URLs, two distinct cache keys → 2 node visits
    assert calls["n"] == 2