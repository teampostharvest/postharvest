"""Hermetic tests for Redis-backed job state (finalplanv2 §8b/§11 Phase 1).

The suite never opens a real Redis connection: conftest neutralises
``REDIS_URL`` (empty → DB-only degraded mode) and these tests inject a
:class:`fakeredis.FakeRedis` client explicitly, so the Redis-backed code paths
are exercised without a daemon or network.
"""
from __future__ import annotations

import fakeredis
import pytest

from backend.core import job_state
from backend.core.job_manager import CancelToken

JOB_ID = "job-0abc1234"


@pytest.fixture
def rclient():
    """A fresh in-process FakeRedis client per test (no network)."""
    client = fakeredis.FakeRedis(decode_responses=True)
    yield client
    job_state.delete_state(JOB_ID, client=client)
    client.close()


# ---------------------------------------------------------------------------
# Cancel signal round-trip
# ---------------------------------------------------------------------------


def test_request_cancel_sets_redis_key(rclient):
    assert not job_state.is_cancelled(JOB_ID, client=rclient)
    job_state.request_cancel(JOB_ID, client=rclient)
    assert job_state.is_cancelled(JOB_ID, client=rclient)
    # Keyspace discipline (§8): every key lives under the `job:` prefix.
    assert job_state._key(JOB_ID, "cancel") == f"job:{JOB_ID}:cancel"


def test_clear_cancel_drops_key(rclient):
    job_state.request_cancel(JOB_ID, client=rclient)
    assert job_state.is_cancelled(JOB_ID, client=rclient)
    job_state.clear_cancel(JOB_ID, client=rclient)
    assert not job_state.is_cancelled(JOB_ID, client=rclient)


def test_is_cancelled_false_without_client():
    # Degraded mode (no Redis configured): cancel checks return False and
    # request_cancel is a safe no-op — never raises.
    assert not job_state.is_cancelled(JOB_ID)
    job_state.request_cancel(JOB_ID)


# ---------------------------------------------------------------------------
# Status mirror
# ---------------------------------------------------------------------------


def test_status_round_trip(rclient):
    assert job_state.get_status(JOB_ID, client=rclient) is None
    job_state.set_status(JOB_ID, "running", client=rclient)
    assert job_state.get_status(JOB_ID, client=rclient) == "running"
    job_state.set_status(JOB_ID, "completed", client=rclient)
    assert job_state.get_status(JOB_ID, client=rclient) == "completed"


# ---------------------------------------------------------------------------
# Progress mirror
# ---------------------------------------------------------------------------


def test_progress_round_trip(rclient):
    counters = {
        "posts_found": 10,
        "posts_extracted": 7,
        "duplicates_removed": 2,
        "posts_skipped": 1,
        "posts_failed": 0,
    }
    assert job_state.get_progress(JOB_ID, client=rclient) is None
    job_state.set_progress(JOB_ID, counters, client=rclient)
    assert job_state.get_progress(JOB_ID, client=rclient) == counters


def test_progress_requires_dict(rclient):
    job_state.set_progress(JOB_ID, ["not", "a", "dict"], client=rclient)
    assert job_state.get_progress(JOB_ID, client=rclient) is None


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


def test_delete_state_removes_all_keys(rclient):
    job_state.set_status(JOB_ID, "running", client=rclient)
    job_state.set_progress(JOB_ID, {"posts_found": 3}, client=rclient)
    job_state.request_cancel(JOB_ID, client=rclient)
    job_state.delete_state(JOB_ID, client=rclient)
    assert job_state.get_status(JOB_ID, client=rclient) is None
    assert job_state.get_progress(JOB_ID, client=rclient) is None
    assert not job_state.is_cancelled(JOB_ID, client=rclient)


# ---------------------------------------------------------------------------
# CancelToken + Redis integration (the §11 shim)
# ---------------------------------------------------------------------------


def test_cancel_token_honours_redis_cancel(rclient, monkeypatch):
    """A cancel requested through Redis (e.g. by another replica) is seen by
    a CancelToken that never received an in-process request_cancel call."""
    monkeypatch.setattr(job_state, "_get_client", lambda: rclient)
    token = CancelToken(JOB_ID)
    assert not token.cancelled
    job_state.request_cancel(JOB_ID, client=rclient)  # "other process" signal
    assert token.cancelled  # picked up via the Redis mirror


def test_cancel_token_request_cancel_mirrors_to_redis(rclient, monkeypatch):
    """request_cancel mirrors to Redis even though this token is local."""
    monkeypatch.setattr(job_state, "_get_client", lambda: rclient)
    token = CancelToken(JOB_ID)
    token.request_cancel()
    assert token.cancelled
    assert job_state.is_cancelled(JOB_ID, client=rclient)