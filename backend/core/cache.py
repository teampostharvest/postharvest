"""Redis-backed read cache — finalplanv2 §8.

Supabase Postgres is remote: a warm query costs ~120-300 ms and a cold connect
~1-2 s. Several dashboard reads (quota usage, active-job counts) are polled by
every open client, so the same rows are counted over and over. This module
caches those read responses in Redis for a short TTL.

Keyspace discipline (finalplanv2 §8)
------------------------------------
Every key lives under the ``cache:`` prefix, distinct from the ``job:`` job-state
keyspace and any future ``ratelimit:`` / ``idemp:`` keyspaces::

    cache:usage:{user_id} -> JSON of the quota-usage response

Durability (finalplanv2 §8)
---------------------------
Redis here is a cache, not a store. Postgres remains the source of truth, every
entry is short-lived, and a missing ``REDIS_URL`` or a Redis outage degrades to
a plain cache miss (the pre-cache DB path) rather than failing a request.
"""
from __future__ import annotations

import json
from typing import Any

import redis

from backend.core import job_state
from backend.core.logging import get_logger

logger = get_logger("core.cache")

_KEY_PREFIX = "cache:"

#: Default lifetime for cached read payloads (seconds). Kept short so a stale
#: quota readout self-heals within one poll interval.
DEFAULT_TTL_SECONDS = 15


def _key(*parts: Any) -> str:
    return _KEY_PREFIX + ":".join(str(p) for p in parts)


def _safe(fn, *args, **kwargs):
    """Run a Redis call; log and swallow any outage (cache miss semantics)."""
    try:
        return fn(*args, **kwargs)
    except redis.RedisError:
        logger.debug("Redis cache call failed (treating as miss)", exc_info=True)
        return None
    except Exception:  # noqa: BLE001 - same contract as RedisError
        logger.debug("Unexpected Redis cache failure", exc_info=True)
        return None


def get_json(key: str, client: "redis.Redis | None" = None) -> Any | None:
    """Return the cached JSON value for ``key``, or None on miss/outage."""
    c = client if client is not None else job_state.get_client()
    if c is None:
        return None
    raw = _safe(c.get, _key(key))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def set_json(
    key: str,
    value: Any,
    ttl: int = DEFAULT_TTL_SECONDS,
    client: "redis.Redis | None" = None,
) -> None:
    """Cache ``value`` as JSON under ``key`` for ``ttl`` seconds."""
    c = client if client is not None else job_state.get_client()
    if c is None:
        return
    try:
        payload = json.dumps(value, default=str)
    except (TypeError, ValueError):
        logger.debug("Could not serialize cache value for %s", key)
        return
    _safe(c.set, _key(key), payload, ex=ttl)


def delete(*keys: str, client: "redis.Redis | None" = None) -> None:
    """Drop one or more cached entries (best-effort)."""
    if not keys:
        return
    c = client if client is not None else job_state.get_client()
    if c is None:
        return
    _safe(c.delete, *(_key(k) for k in keys))


def invalidate_usage(user_id: int | None, client: "redis.Redis | None" = None) -> None:
    """Drop the cached quota readout for a user (called on job state changes)."""
    if user_id is None:
        return
    delete(f"usage:{user_id}", client=client)
