"""Hermetic tests for the Redis read cache (finalplanv2 §8).

No real Redis: conftest neutralises ``REDIS_URL`` and these tests inject
:class:`fakeredis.FakeRedis` (unit) or monkeypatch the cache seam (API), so the
cached read paths are covered without a daemon or network.
"""
from __future__ import annotations

import fakeredis
import pytest

from backend.core import cache


@pytest.fixture
def rclient():
    client = fakeredis.FakeRedis(decode_responses=True)
    yield client
    client.flushall()
    client.close()


# ---------------------------------------------------------------------------
# Cache primitives
# ---------------------------------------------------------------------------


def test_set_get_round_trip(rclient):
    cache.set_json("usage:1", {"a": 1, "b": [2, 3]}, ttl=30, client=rclient)
    assert cache.get_json("usage:1", client=rclient) == {"a": 1, "b": [2, 3]}


def test_keys_are_namespaced_under_cache_prefix(rclient):
    cache.set_json("usage:1", {"a": 1}, client=rclient)
    # Keyspace discipline (§8): the read cache must not collide with `job:`.
    assert rclient.get("cache:usage:1") is not None
    assert rclient.ttl("cache:usage:1") > 0


def test_miss_returns_none(rclient):
    assert cache.get_json("usage:404", client=rclient) is None


def test_corrupt_entry_is_treated_as_miss(rclient):
    rclient.set("cache:usage:9", "{not json")
    assert cache.get_json("usage:9", client=rclient) is None


def test_degraded_mode_without_client(monkeypatch):
    # No Redis configured: reads miss, writes are safe no-ops (never raise).
    monkeypatch.setattr(cache.job_state, "get_client", lambda: None)
    assert cache.get_json("usage:1") is None
    cache.set_json("usage:1", {"a": 1})
    cache.invalidate_usage(1)


def test_invalidate_usage_drops_entry(rclient):
    cache.set_json("usage:7", {"a": 1}, client=rclient)
    assert cache.get_json("usage:7", client=rclient) == {"a": 1}
    cache.invalidate_usage(7, client=rclient)
    assert cache.get_json("usage:7", client=rclient) is None


def test_invalidate_usage_none_user_is_noop(rclient):
    cache.set_json("usage:1", {"a": 1}, client=rclient)
    cache.invalidate_usage(None, client=rclient)
    assert cache.get_json("usage:1", client=rclient) == {"a": 1}


# ---------------------------------------------------------------------------
# Identity + accounts caches (same `cache:` keyspace, distinct sub-prefixes)
# ---------------------------------------------------------------------------


def test_user_cache_round_trip_and_namespacing(rclient):
    cache.set_user("uid-1", {"id": 7, "role": "user"}, client=rclient)
    assert cache.get_user("uid-1", client=rclient) == {"id": 7, "role": "user"}
    assert rclient.get("cache:user:uid-1") is not None
    assert rclient.ttl("cache:user:uid-1") > 0


def test_invalidate_user_drops_entry(rclient):
    cache.set_user("uid-1", {"id": 7}, client=rclient)
    cache.invalidate_user("uid-1", client=rclient)
    assert cache.get_user("uid-1", client=rclient) is None


def test_user_cache_blank_uid_is_noop(rclient):
    assert cache.get_user("", client=rclient) is None
    cache.set_user("", {"id": 1}, client=rclient)
    cache.invalidate_user("", client=rclient)
    assert rclient.keys("cache:user:*") == []


def test_accounts_cache_round_trip_and_namespacing(rclient):
    payload = {"ops": [{"name": "default"}], "mine": []}
    cache.set_accounts(3, payload, client=rclient)
    assert cache.get_accounts(3, client=rclient) == payload
    assert rclient.get("cache:accounts:3") is not None


def test_invalidate_accounts_drops_entry(rclient):
    cache.set_accounts(3, {"ops": [], "mine": []}, client=rclient)
    cache.invalidate_accounts(3, client=rclient)
    assert cache.get_accounts(3, client=rclient) is None


def test_accounts_cache_none_user_is_noop(rclient):
    assert cache.get_accounts(None, client=rclient) is None
    cache.set_accounts(None, {"ops": [], "mine": []}, client=rclient)
    cache.invalidate_accounts(None, client=rclient)
    assert rclient.keys("cache:accounts:*") == []


def test_degraded_identity_and_accounts_cache_without_client(monkeypatch):
    # No Redis configured: reads miss, writes/invalidations are safe no-ops.
    monkeypatch.setattr(cache.job_state, "get_client", lambda: None)
    assert cache.get_user("uid-1") is None
    assert cache.get_accounts(1) is None
    cache.set_user("uid-1", {"id": 1})
    cache.set_accounts(1, {"ops": [], "mine": []})
    cache.invalidate_user("uid-1")
    cache.invalidate_accounts(1)


# ---------------------------------------------------------------------------
# /api/usage integration — cached readout
# ---------------------------------------------------------------------------


def test_usage_endpoint_serves_cached_payload(authed_client, monkeypatch):
    from backend.api import usage as usage_api

    cached = {
        "plan": "free",
        "jobs_running": {"used": 9, "limit": 1},
        "personal_accounts": {"used": 3, "limit": 1},
        "per_job": {"urls": 7, "max_posts": 11},
        "posts_today": 42,
        "jobs_today": 5,
    }
    wrote: list[tuple] = []
    monkeypatch.setattr(usage_api.cache, "get_json", lambda *a, **k: cached)
    monkeypatch.setattr(
        usage_api.cache, "set_json", lambda *a, **k: wrote.append((a, k))
    )

    response = authed_client.get("/api/usage")

    assert response.status_code == 200
    body = response.json()
    assert body["posts_today"] == 42
    assert body["jobs_running"] == {"used": 9, "limit": 1}
    # Cache hit: the handler must not recompute (and therefore not write back).
    assert wrote == []


def test_usage_endpoint_writes_cache_on_miss(authed_client, monkeypatch):
    from backend.api import usage as usage_api

    writes: list[tuple[str, dict]] = []
    monkeypatch.setattr(usage_api.cache, "get_json", lambda *a, **k: None)
    monkeypatch.setattr(
        usage_api.cache, "set_json", lambda key, value, **k: writes.append((key, value))
    )

    response = authed_client.get("/api/usage")

    assert response.status_code == 200
    assert len(writes) == 1
    key, value = writes[0]
    assert key.startswith("usage:")
    assert "posts_today" in value
    assert "jobs_running" in value


# ---------------------------------------------------------------------------
# /api/accounts integration — cached readout
# ---------------------------------------------------------------------------


def test_accounts_endpoint_serves_cached_payload(authed_client, monkeypatch):
    from backend.api import accounts as accounts_api

    cached = {
        "ops": [
            {
                "name": "default",
                "scope": "ops",
                "saved_at": None,
                "cookies_file": "fb_cookies.json",
                "status": "VALID",
            }
        ],
        "mine": [],
    }
    rebuilt: list[int] = []
    monkeypatch.setattr(accounts_api.cache, "get_accounts", lambda *a, **k: cached)
    monkeypatch.setattr(
        accounts_api, "account_metadata", lambda *a, **k: rebuilt.append(1) or []
    )

    response = authed_client.get("/api/accounts")

    assert response.status_code == 200
    body = response.json()
    assert body["ops"][0]["name"] == "default"
    assert body["mine"] == []
    # Cache hit: the handler must not rebuild from the credentials index/DB.
    assert rebuilt == []


def test_accounts_endpoint_writes_cache_on_miss(authed_client, monkeypatch):
    from backend.api import accounts as accounts_api

    writes: list[tuple] = []
    monkeypatch.setattr(accounts_api.cache, "get_accounts", lambda *a, **k: None)
    monkeypatch.setattr(
        accounts_api.cache,
        "set_accounts",
        lambda user_id, payload, **k: writes.append((user_id, payload)),
    )

    response = authed_client.get("/api/accounts")

    assert response.status_code == 200
    assert len(writes) == 1
    user_id, payload = writes[0]
    assert isinstance(user_id, int)
    assert "ops" in payload and "mine" in payload
