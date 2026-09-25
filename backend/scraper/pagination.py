"""Generic pagination engine — transport-agnostic infinite-scroll / cursor loop.

The engine drives a fetch→extract→paginate loop with:
  * Stale detection (no new items after N rounds)
  * Max-rounds safety fallback
  * Optional item-cap (use as safety limit, not completion condition)
  * Cancel-event support
  * Per-round checkpoint callbacks for resume

Transport adapters (browser, HTTP, GraphQL) implement the
:class:`PageFetcher` protocol and plug into the engine.  The engine
never knows whether pages come from a headless browser, an HTTP GET,
or a GraphQL cursor query.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol, Sequence

logger = logging.getLogger("scraper.pagination")


# ---------------------------------------------------------------------------
# Protocols & result types
# ---------------------------------------------------------------------------

class PageFetcher(Protocol):
    """Transport adapter contract — one method, one responsibility."""

    def fetch_page(
        self,
        *,
        cursor: Optional[str] = None,
        cancel_event: Optional[Any] = None,
    ) -> "PageResult":
        """Fetch one page of items.

        :param cursor: opaque pagination token (``None`` for the first page).
        :param cancel_event: optional threading.Event for cancellation.
        :returns: :class:`PageResult` with items, next cursor, and stats.
        """
        ...


@dataclass
class PageResult:
    """Single page returned by a :class:`PageFetcher`."""

    items: list[Any] = field(default_factory=list)
    next_cursor: Optional[str] = None
    has_more: bool = True
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class PaginationResult:
    """Aggregated output of the pagination engine."""

    items: list[Any] = field(default_factory=list)
    pages_fetched: int = 0
    total_rounds: int = 0
    stop_reason: str = "unknown"
    last_cursor: Optional[str] = None
    meta: dict[str, Any] = field(default_factory=dict)


# Callback type: fn(round_num, item_count, cursor) -> None
CheckpointCallback = Optional[Callable[[int, int, Optional[str]], None]]


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

def paginate(
    fetcher: PageFetcher,
    *,
    max_items: Optional[int] = None,
    max_rounds: int = 40,
    stale_rounds: int = 3,
    delay_seconds: float = 0.0,
    cancel_event: Optional[Any] = None,
    start_cursor: Optional[str] = None,
    on_checkpoint: CheckpointCallback = None,
) -> PaginationResult:
    """Drive a fetch→extract loop until a stop condition is met.

    Stop conditions (checked in order):
      1. ``cancel_event`` is set → ``stop_reason = "cancelled"``
      2. ``max_items`` reached (when set) → ``stop_reason = "item_cap"``
      3. ``fetcher`` returns ``has_more = False`` → ``stop_reason = "no_more"`
      4. No new items for ``stale_rounds`` consecutive rounds → ``stop_reason = "stale"``
      5. ``max_rounds`` safety fallback → ``stop_reason = "max_rounds"``

    :param fetcher: transport adapter implementing :class:`PageFetcher`.
    :param max_items: optional safety cap on total items (``None`` = unbounded).
    :param max_rounds: safety fallback for infinite loops.
    :param stale_rounds: consecutive rounds with no new items before stopping.
    :param delay_seconds: sleep between rounds (0 = no delay).
    :param cancel_event: optional threading.Event for cancellation.
    :param start_cursor: resume from this cursor (``None`` = from the beginning).
    :param on_checkpoint: called after each round with (round, total_items, cursor).
    :returns: :class:`PaginationResult` with all collected items.
    """
    import time

    cursor = start_cursor
    all_items: list[Any] = []
    pages_fetched = 0
    stale_count = 0
    prev_count = 0

    for round_num in range(1, max_rounds + 1):
        # 1. Cancel check
        if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
            logger.info("Pagination cancelled at round %d", round_num)
            return PaginationResult(
                items=all_items,
                pages_fetched=pages_fetched,
                total_rounds=round_num - 1,
                stop_reason="cancelled",
                last_cursor=cursor,
            )

        # 2. Fetch
        try:
            result = fetcher.fetch_page(cursor=cursor, cancel_event=cancel_event)
        except Exception as exc:
            logger.warning("Pagination fetch failed at round %d: %s", round_num, exc)
            return PaginationResult(
                items=all_items,
                pages_fetched=pages_fetched,
                total_rounds=round_num,
                stop_reason="fetch_error",
                last_cursor=cursor,
                # The raw exception is kept alongside the string so callers that
                # need the underlying taxonomy (the feed-walk seam re-raising a
                # ScraperError) can recover it; ``error`` stays a plain string
                # for callers that just report it.
                meta={"error": str(exc), "error_exc": exc},
            )

        pages_fetched += 1
        new_items = result.items or []
        all_items.extend(new_items)

        # 3. Stale detection
        if len(all_items) == prev_count:
            stale_count += 1
            if stale_count >= stale_rounds:
                logger.info(
                    "Pagination stale after %d rounds (%d items), stopping",
                    stale_count, len(all_items),
                )
                return PaginationResult(
                    items=all_items,
                    pages_fetched=pages_fetched,
                    total_rounds=round_num,
                    stop_reason="stale",
                    last_cursor=cursor,
                    meta=result.meta,
                )
        else:
            stale_count = 0
            prev_count = len(all_items)

        logger.info(
            "Pagination round %d: +%d items (total=%d, cursor=%s)",
            round_num, len(new_items), len(all_items), cursor,
        )

        # 4. Item cap
        if max_items is not None and len(all_items) >= max_items:
            logger.info("Pagination item cap reached: %d >= %d", len(all_items), max_items)
            return PaginationResult(
                items=all_items[:max_items],
                pages_fetched=pages_fetched,
                total_rounds=round_num,
                stop_reason="item_cap",
                last_cursor=cursor,
                meta=result.meta,
            )

        # 5. No more pages
        if not result.has_more:
            logger.info("Pagination no more pages at round %d", round_num)
            return PaginationResult(
                items=all_items,
                pages_fetched=pages_fetched,
                total_rounds=round_num,
                stop_reason="no_more",
                last_cursor=cursor,
                meta=result.meta,
            )

        # 6. Update cursor
        cursor = result.next_cursor

        # 7. Checkpoint
        if on_checkpoint is not None:
            try:
                on_checkpoint(round_num, len(all_items), cursor)
            except Exception:
                logger.debug("Checkpoint callback failed", exc_info=True)

        # 8. Delay between rounds
        if delay_seconds > 0:
            time.sleep(delay_seconds)

    # Max rounds safety fallback
    logger.info("Pagination max rounds reached: %d", max_rounds)
    return PaginationResult(
        items=all_items,
        pages_fetched=pages_fetched,
        total_rounds=max_rounds,
        stop_reason="max_rounds",
        last_cursor=cursor,
    )
