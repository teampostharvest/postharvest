"""Hermetic TTL cache for the runtime-cache seam (plan §8, slice C).

C's byte-paper today is genuinely blank (``backend/core/`` has **zero**
cache module — byte-confirmed: only ``config/database/exceptions`` /
``job_manager/logging/plans`` live here; there is **no** runtime cache
seam in the tree).  This module is C's *author-ed* blank paper
(hermetic-first: author the seam, then prove it):

* **zero network** — no redis, no HTTP; the cache is a plain in-process
  TTL window keyed by the normalized URL
* **zero env** — nothing touches ``REDIS_URL`` / ``settings``; the clock
  is injectable so hermetic proofs move time without touching any env
* **zero browser** — no playwright, no node, no :9326/:9334 listeners;
  this cache only stores the node seam's *payload bytes* (``FetchResult``
  shape: ``html`` / ``status_code`` / ``final_url`` / ``variant``) and
  replays them byte-equal
* **add-don't-replace** — the pre-existing node-browser suite keeps its
  assertions untouched; C only *adds* a hermetic runtime-cache seam in
  front of the same real seam (``backend/scraper/__init__.py:316``
  ``_node_fetch_page``)
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional


class TTLCache:
    """In-process TTL window keyed by normalized URL (hermetic, thread-safe).

    The byte this module seals (hermetic-proof mold, byte-mirroring
    ``tests/test_node_browser_seam.py:424`` / ``:671``, which assert a seam
    was visited ``== 1`` across two identical calls): **two identical
    scrapes within the TTL window → the node seam is visited EXACTLY ONCE**
    — the 2nd identical scrape is served byte-equal from the hermetic TTL
    window, NOT re-fetched from node.
    """

    def __init__(
        self,
        ttl_seconds: float,
        *,
        clock: Optional[Callable[[], float]] = None,
        enabled: bool = False,
    ) -> None:
        self.ttl_seconds = float(ttl_seconds)
        self._clock = clock or time.monotonic
        self._enabled = enabled
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, Any]] = {}

    def arm(self, enabled: bool = True) -> None:
        """Arm (or disarm) the cache.  Hermetic default is **disarmed**."""
        with self._lock:
            self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    def get(self, key: str) -> Optional[Any]:
        """Return the cached payload if the key is fresh within the TTL."""
        if not self._enabled:
            return None
        now = self._clock()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            first_seen, payload = entry
            if now - first_seen > self.ttl_seconds:
                # expired -> evict and miss
                del self._entries[key]
                return None
            return payload

    def set(self, key: str, payload: Any) -> None:
        """Store ``payload`` under ``key`` with this call as the TTL origin."""
        if not self._enabled:
            return
        now = self._clock()
        with self._lock:
            # store the *first* write time so the window stays open for the
            # full TTL from the first byte, not a rolling window
            current = self._entries.get(key)
            if current is not None:
                first_seen, _ = current
            else:
                first_seen = now
            self._entries[key] = (first_seen, payload)

    def expire_all(self) -> None:
        """Hermetic test seam: force every TTL window shut at once."""
        with self._lock:
            now = self._clock()
            for key in [k for k, (t0, _) in self._entries.items() if now - t0 > self.ttl_seconds]:
                del self._entries[key]

    def __contains__(self, key: str) -> bool:
        return self.get(key) is not None
