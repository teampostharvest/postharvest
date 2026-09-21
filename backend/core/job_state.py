"""Redis-backed job state — finalplanv2 §8(b)/§11 Phase 1.

Moves the job lifecycle's live state (status, progress, cancel) out of the
in-process :class:`~backend.core.job_manager.JobManager` / ``CancelToken``
registry and into Redis keys, so any backend replica can observe and steer a
job. This is the Phase 1 prerequisite §11 names ("do this before anything
else") before a second FastAPI replica can exist.

Keyspace discipline (finalplanv2 §8)
------------------------------------
Every key this module writes lives under the ``job:`` prefix and embeds the
job id, so a ``KEYS job:*`` scan is unambiguous and no other subsystem's keys
(future ``ratelimit:``, ``idemp:`` keyspaces) can collide with it::

    job:{job_id}:status    -> status string (queued/running/paused/completed/failed)
    job:{job_id}:progress  -> JSON of the five per-source progress counters
    job:{job_id}:cancel    -> "1" once cancellation has been requested

Durability (finalplanv2 §8)
---------------------------
Redis here is *ephemeral coordination state*, not a database. Postgres remains
the durability store — every write in this module mirrors what the worker
already persists to the DB, and a Redis outage or unconfigured ``REDIS_URL``
degrades to DB-only behaviour (the pre-Redis semantics) rather than crashing a
request or a worker. Each helper accepts an optional ``client`` so the test
suite can inject :class:`fakeredis.FakeRedis` (hermetic, no network).
"""
from __future__ import annotations

import json

import redis

from backend.core.config import get_settings
from backend.core.logging import get_logger

logger = get_logger("core.job_state")

_KEY_PREFIX = "job:"
_SYNC_CLIENT: "redis.Redis | None" = None


# ---------------------------------------------------------------------------
# Client lifecycle
# ---------------------------------------------------------------------------


def _get_client() -> "redis.Redis | None":
    """Return the process-wide sync Redis client, or None when unavailable.

    ``None`` is the degraded mode: every helper becomes a safe no-op, which is
    the documented behaviour when ``REDIS_URL`` is unset or Redis is down.
    """
    global _SYNC_CLIENT
    if _SYNC_CLIENT is None:
        url = get_settings().redis_url
        if url:
            try:
                _SYNC_CLIENT = redis.Redis.from_url(
                    url,
                    decode_responses=True,
                    socket_connect_timeout=1.0,
                    socket_timeout=2.0,
                )
            except Exception:  # noqa: BLE001 - never let client setup crash boot
                logger.warning("Could not create Redis client; degrading to DB-only")
                _SYNC_CLIENT = None
    return _SYNC_CLIENT


def close() -> None:
    """Close the sync client at app shutdown (best-effort)."""
    global _SYNC_CLIENT
    if _SYNC_CLIENT is not None:
        try:
            _SYNC_CLIENT.close()
        except Exception:  # noqa: BLE001 - best-effort shutdown
            logger.debug("Redis client close failed", exc_info=True)
        _SYNC_CLIENT = None


def _client_or(client: "redis.Redis | None") -> "redis.Redis | None":
    return client if client is not None else _get_client()


def _key(job_id: str, suffix: str) -> str:
    return f"{_KEY_PREFIX}{job_id}:{suffix}"


def _safe(fn, *args, **kwargs):
    """Run a Redis call; log and swallow any outage (degraded mode)."""
    try:
        return fn(*args, **kwargs)
    except redis.RedisError:
        logger.debug("Redis job-state call failed (degraded mode)", exc_info=True)
        return None
    except Exception:  # noqa: BLE001 - same contract as RedisError
        logger.debug("Unexpected Redis job-state failure", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Cancel signal
# ---------------------------------------------------------------------------


def request_cancel(job_id: str, client: "redis.Redis | None" = None) -> None:
    """Persist the cancel request to Redis so every replica/worker sees it."""
    c = _client_or(client)
    if c is None:
        return
    _safe(c.set, _key(job_id, "cancel"), "1")


def is_cancelled(job_id: str, client: "redis.Redis | None" = None) -> bool:
    """True when a cancel was requested for this job (via Redis)."""
    c = _client_or(client)
    if c is None:
        return False
    value = _safe(c.get, _key(job_id, "cancel"))
    return value == "1"


def clear_cancel(job_id: str, client: "redis.Redis | None" = None) -> None:
    """Drop the cancel key (used on resume)."""
    c = _client_or(client)
    if c is None:
        return
    _safe(c.delete, _key(job_id, "cancel"))


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def set_status(job_id: str, status: str, client: "redis.Redis | None" = None) -> None:
    """Mirror the job's current status to Redis."""
    c = _client_or(client)
    if c is None:
        return
    _safe(c.set, _key(job_id, "status"), status)


def get_status(job_id: str, client: "redis.Redis | None" = None) -> str | None:
    """Return the Redis-mirrored status for a job (or None when absent)."""
    c = _client_or(client)
    if c is None:
        return None
    return _safe(c.get, _key(job_id, "status"))


# ---------------------------------------------------------------------------
# Progress (live counters)
# ---------------------------------------------------------------------------


def set_progress(
    job_id: str, counters: dict, client: "redis.Redis | None" = None
) -> None:
    """Mirror the job's live progress counters to Redis as JSON."""
    c = _client_or(client)
    if c is None:
        return
    try:
        payload = json.dumps(counters, sort_keys=True, default=str)
    except (TypeError, ValueError):
        logger.debug("Could not serialize progress counters for job %s", job_id)
        return
    _safe(c.set, _key(job_id, "progress"), payload)


def get_progress(job_id: str, client: "redis.Redis | None" = None) -> dict | None:
    """Return the Redis-mirrored progress counters (or None when absent)."""
    c = _client_or(client)
    if c is None:
        return None
    raw = _safe(c.get, _key(job_id, "progress"))
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


def delete_state(job_id: str, client: "redis.Redis | None" = None) -> None:
    """Remove all job-state keys for a job (DELETE endpoint, terminal sweep)."""
    c = _client_or(client)
    if c is None:
        return
    _safe(
        c.delete,
        _key(job_id, "status"),
        _key(job_id, "progress"),
        _key(job_id, "cancel"),
    )