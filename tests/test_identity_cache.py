"""Redis identity cache in ``get_current_user`` (finalplanv2 §8/§11).

Every authenticated request used to pay a Postgres round-trip just to resolve
the caller. These tests cover the cache seam hermetically: Redis is disabled by
conftest, so the cache helpers are monkeypatched rather than networked.
"""
from __future__ import annotations

from backend.core import cache
from backend.core.database import SessionLocal
from backend.models import User


def _user_id(firebase_uid: str) -> int:
    with SessionLocal() as db:
        user = db.query(User).filter_by(firebase_uid=firebase_uid).first()
        assert user is not None, f"user {firebase_uid} was not provisioned"
        return user.id


def _set_user(firebase_uid: str, **attrs) -> None:
    with SessionLocal() as db:
        user = db.query(User).filter_by(firebase_uid=firebase_uid).one()
        for key, value in attrs.items():
            setattr(user, key, value)
        db.commit()


def _identity(**overrides) -> dict:
    payload = {
        "id": 424242,
        "firebase_uid": "test_firebase_uid_user_a",
        "email": "cached@example.com",
        "display_name": "Cached User",
        "photo_url": None,
        "plan": "pro",
        "role": "user",
        "is_active": True,
    }
    payload.update(overrides)
    return payload


def test_current_user_serves_cached_identity_without_db(authed_client, monkeypatch):
    """A cache hit must not touch the users table (no provisioning)."""
    monkeypatch.setattr(cache, "get_user", lambda *a, **k: _identity())

    response = authed_client.get("/api/auth/me")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 424242
    assert body["email"] == "cached@example.com"
    assert body["display_name"] == "Cached User"
    assert body["plan"] == "pro"
    # The fake id was never written to the DB.
    with SessionLocal() as db:
        assert db.query(User).filter_by(id=424242).first() is None


def test_current_user_rejects_inactive_cached_identity(authed_client, monkeypatch):
    monkeypatch.setattr(cache, "get_user", lambda *a, **k: _identity(is_active=False))

    response = authed_client.get("/api/auth/me")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "account_disabled"


def test_current_user_caches_identity_on_miss(authed_client, monkeypatch):
    writes: list[tuple] = []
    monkeypatch.setattr(cache, "get_user", lambda *a, **k: None)
    monkeypatch.setattr(
        cache, "set_user", lambda uid, payload, **k: writes.append((uid, payload))
    )

    response = authed_client.get("/api/auth/me")

    assert response.status_code == 200
    assert len(writes) == 1
    uid, payload = writes[0]
    assert uid == "test_firebase_uid_user_a"
    assert payload["firebase_uid"] == uid
    assert payload["is_active"] is True
    # Every UserOut field must be present so a later hit is fully populated.
    assert set(payload) >= {
        "id",
        "firebase_uid",
        "email",
        "display_name",
        "photo_url",
        "plan",
        "role",
        "is_active",
    }


def test_admin_role_change_invalidates_identity_cache(
    authed_client, client, auth_headers_b, monkeypatch
):
    authed_client.get("/api/auth/me")
    client.get("/api/auth/me", headers=auth_headers_b)
    _set_user("test_firebase_uid_user_a", role="ops")
    user_b_id = _user_id("test_firebase_uid_user_b")

    invalidated: list[str] = []
    monkeypatch.setattr(
        cache, "invalidate_user", lambda uid, **k: invalidated.append(uid)
    )

    response = authed_client.patch(
        f"/api/admin/users/{user_b_id}/role", json={"role": "ops"}
    )

    assert response.status_code == 200
    assert invalidated == ["test_firebase_uid_user_b"]


def test_admin_plan_change_invalidates_identity_cache(
    authed_client, client, auth_headers_b, monkeypatch
):
    authed_client.get("/api/auth/me")
    client.get("/api/auth/me", headers=auth_headers_b)
    _set_user("test_firebase_uid_user_a", role="ops")
    user_b_id = _user_id("test_firebase_uid_user_b")

    invalidated: list[str] = []
    monkeypatch.setattr(
        cache, "invalidate_user", lambda uid, **k: invalidated.append(uid)
    )

    response = authed_client.patch(
        f"/api/admin/users/{user_b_id}/plan", json={"plan": "pro"}
    )

    assert response.status_code == 200
    assert invalidated == ["test_firebase_uid_user_b"]
