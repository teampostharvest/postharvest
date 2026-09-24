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
from backend.core.metrics import REDIS_ERRORS

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
        REDIS_ERRORS.inc()
        logger.debug("Redis cache call failed (treating as miss)", exc_info=True)
        return None
    except Exception:  # noqa: BLE001 - same contract as RedisError
        REDIS_ERRORS.inc()
        logger.debug("Unexpected Redis cache failure", exc_info=True)
        return None


def _read(key: str, client: "redis.Redis | None") -> Any | None:
    """Shared primitive: JSON-decode one entry, or None on miss/outage."""
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


def _write(
    key: str, value: Any, ttl: int, client: "redis.Redis | None"
) -> None:
    """Shared primitive: JSON-encode and store one entry for ``ttl`` seconds."""
    c = client if client is not None else job_state.get_client()
    if c is None:
        return
    try:
        payload = json.dumps(value, default=str)
    except (TypeError, ValueError):
        logger.debug("Could not serialize cache value for %s", key)
        return
    _safe(c.set, _key(key), payload, ex=ttl)


def _drop(keys: tuple[str, ...], client: "redis.Redis | None") -> None:
    """Shared primitive: best-effort deletion of one or more entries."""
    if not keys:
        return
    c = client if client is not None else job_state.get_client()
    if c is None:
        return
    _safe(c.delete, *(_key(k) for k in keys))


def get_json(key: str, client: "redis.Redis | None" = None) -> Any | None:
    """Return the cached JSON value for ``key``, or None on miss/outage."""
    return _read(key, client)


def set_json(
    key: str,
    value: Any,
    ttl: int = DEFAULT_TTL_SECONDS,
    client: "redis.Redis | None" = None,
) -> None:
    """Cache ``value`` as JSON under ``key`` for ``ttl`` seconds."""
    _write(key, value, ttl, client)


def delete(*keys: str, client: "redis.Redis | None" = None) -> None:
    """Drop one or more cached entries (best-effort)."""
    _drop(keys, client)


def invalidate_usage(user_id: int | None, client: "redis.Redis | None" = None) -> None:
    """Drop the cached quota readout for a user (called on job state changes)."""
    if user_id is None:
        return
    _drop((f"usage:{user_id}",), client)


# ---------------------------------------------------------------------------
# Authenticated-user identity (auth hot path)
# ---------------------------------------------------------------------------
#: Lifetime for the identity cache (seconds). Every authenticated request used
#: to pay a Postgres round-trip just to resolve the caller
#: (``get_or_create_user``); caching the identity row removes that query from
#: the hot path so a burst of dashboard polls does not each hold a pooled
#: connection. Kept short because role/plan/is_active changes are
#: security-relevant: admin mutations invalidate explicitly, and this TTL is
#: the backstop for changes made outside the API.
USER_TTL_SECONDS = 30


def get_user(firebase_uid: str, client: "redis.Redis | None" = None) -> Any | None:
    """Return the cached identity payload for a Firebase UID, or None."""
    if not firebase_uid:
        return None
    return _read(f"user:{firebase_uid}", client)


def set_user(
    firebase_uid: str,
    payload: Any,
    ttl: int = USER_TTL_SECONDS,
    client: "redis.Redis | None" = None,
) -> None:
    """Cache an identity payload for a Firebase UID."""
    if not firebase_uid:
        return
    _write(f"user:{firebase_uid}", payload, ttl, client)


def invalidate_user(firebase_uid: str, client: "redis.Redis | None" = None) -> None:
    """Drop the cached identity for a Firebase UID (role/plan/is_active change)."""
    if not firebase_uid:
        return
    _drop((f"user:{firebase_uid}",), client)


# ---------------------------------------------------------------------------
# Saved-accounts list
# ---------------------------------------------------------------------------
#: Lifetime for the saved-accounts list (seconds). The list is derived from the
#: on-disk credentials index plus a ``saved_accounts`` scan, neither of which
#: changes except on explicit account mutations (which invalidate) or an async
#: capture completing (covered by this TTL).
ACCOUNTS_TTL_SECONDS = 30


def get_accounts(user_id: int | None, client: "redis.Redis | None" = None) -> Any | None:
    """Return the cached saved-accounts payload for a user, or None."""
    if user_id is None:
        return None
    return _read(f"accounts:{user_id}", client)


def set_accounts(
    user_id: int | None,
    payload: Any,
    ttl: int = ACCOUNTS_TTL_SECONDS,
    client: "redis.Redis | None" = None,
) -> None:
    """Cache the saved-accounts payload for a user."""
    if user_id is None:
        return
    _write(f"accounts:{user_id}", payload, ttl, client)


def invalidate_accounts(user_id: int | None, client: "redis.Redis | None" = None) -> None:
    """Drop the cached saved-accounts payload for a user (account mutation)."""
    if user_id is None:
        return
    _drop((f"accounts:{user_id}",), client)


# ---------------------------------------------------------------------------
# Merge re-export source note: this module is one half of the merged
# `backend.core.cache` import path — `cache.py` (the hermetic TTLCache half)
# re-exports everything above so both branches' call sites keep working
# through `from backend.core import cache`.
# ---------------------------------------------------------------------------