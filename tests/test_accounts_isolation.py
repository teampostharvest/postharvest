"""Accounts two-tier isolation, ops/admin gating, and tier-limit tests."""
from __future__ import annotations

import pytest

from backend.core.database import SessionLocal
from backend.models import ScrapeJob, User
from backend.scraper.browser_scraper import save_cookies
from helpers import (
    error_envelope,
    install_fake_scraper,
    insert_completed_job,
    make_source_result,
    PAGE_URL,
    sample_posts,
    wait_for_job,
)

# A session cookie set that status-checks as VALID (xs present, far future).
_VALID_COOKIES = [
    {
        "name": "c_user", "value": "1001", "domain": ".facebook.com", "path": "/",
        "expires": 4102444800, "secure": True, "httpOnly": True,
    },
    {
        "name": "xs", "value": "sess-token", "domain": ".facebook.com", "path": "/",
        "expires": 4102444800, "secure": True, "httpOnly": True,
    },
]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


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


def _seed_personal_accounts(owner_id: int, names: list[str]) -> None:
    for name in names:
        save_cookies(list(_VALID_COOKIES), account_name=name, owner_id=owner_id)


# ---------------------------------------------------------------------------
# accounts: auth requirement + two-tier isolation
# ---------------------------------------------------------------------------


def test_accounts_endpoints_require_auth(client):
    assert client.get("/api/accounts").status_code == 401
    assert client.post("/api/accounts/personal", json={"name": "x", "email": "a@b.c", "password": "p"}).status_code == 401


def test_personal_accounts_isolated_between_users(authed_client, client, auth_headers_b, monkeypatch):
    authed_client.get("/api/auth/me")  # provision user A
    user_a_id = _user_id("test_firebase_uid_user_a")

    # User A adds a personal account (login mocked — no real Facebook call).
    def fake_login(email, password, owner_id=None, account_name=None, timeout_seconds=None):
        save_cookies(list(_VALID_COOKIES), account_name=account_name, owner_id=owner_id)
        return True

    monkeypatch.setattr("backend.api.accounts.login_with_credentials", fake_login)

    created = authed_client.post(
        "/api/accounts/personal",
        json={"name": "myfb", "email": "a@face.test", "password": "secret"},
    )
    assert created.status_code == 201
    assert created.json()["name"] == "myfb"
    assert created.json()["scope"] == "me"
    assert created.json()["status"] == "VALID"

    # User A sees it in their own list only.
    mine_a = authed_client.get("/api/accounts").json()["mine"]
    assert [a["name"] for a in mine_a] == ["myfb"]
    ops_a = authed_client.get("/api/accounts").json()["ops"]
    assert all(a["scope"] == "ops" for a in ops_a)

    # User B does NOT see and cannot delete User A's personal account.
    client.get("/api/auth/me", headers=auth_headers_b)  # provision user B
    mine_b = client.get("/api/accounts", headers=auth_headers_b).json()["mine"]
    assert all(a["scope"] == "me" for a in mine_b)
    assert "myfb" not in [a["name"] for a in mine_b]

    resp = client.delete("/api/accounts/me/myfb", headers=auth_headers_b)
    assert resp.status_code == 404
    error_envelope(resp.json())

    # Owner can delete their own.
    assert authed_client.delete("/api/accounts/me/myfb").status_code == 204


def test_regular_user_cannot_delete_ops_pool(authed_client):
    resp = authed_client.delete("/api/accounts/ops/some-ops-account")
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "admin_required"


def test_personal_account_plan_cap(authed_client, monkeypatch):
    authed_client.get("/api/auth/me")  # provision user A (basic plan, cap 1)
    user_a_id = _user_id("test_firebase_uid_user_a")
    _seed_personal_accounts(user_a_id, ["one"])

    calls = []

    def fake_login(email, password, owner_id=None, account_name=None, timeout_seconds=None):
        calls.append(account_name)
        save_cookies(list(_VALID_COOKIES), account_name=account_name, owner_id=owner_id)
        return True

    monkeypatch.setattr("backend.api.accounts.login_with_credentials", fake_login)
    resp = authed_client.post(
        "/api/accounts/personal",
        json={"name": "two", "email": "a@face.test", "password": "secret"},
    )
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "plan_limit"
    assert calls == []  # login must not run when over the cap

    # Pro plan lifts the cap: seed to the pro limit (5) then expect a rejection.
    _set_user("test_firebase_uid_user_a", plan="pro")
    _seed_personal_accounts(user_a_id, ["two", "three", "four", "five"])
    resp = authed_client.post(
        "/api/accounts/personal",
        json={"name": "six", "email": "a@face.test", "password": "secret"},
    )
    assert resp.status_code == 429


def test_personal_login_failure_maps_to_502(authed_client, monkeypatch):
    authed_client.get("/api/auth/me")
    monkeypatch.setattr(
        "backend.api.accounts.login_with_credentials",
        lambda *a, **k: False,
    )
    resp = authed_client.post(
        "/api/accounts/personal",
        json={"name": "bad", "email": "a@face.test", "password": "wrong"},
    )
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "facebook_login_failed"


# ---------------------------------------------------------------------------
# admin: ops role gating + plan/role management
# ---------------------------------------------------------------------------


def test_admin_users_requires_ops(authed_client, client, auth_headers_b):
    authed_client.get("/api/auth/me")  # user A (role=user)
    client.get("/api/auth/me", headers=auth_headers_b)  # user B

    resp = authed_client.get("/api/admin/users")
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "admin_required"

    # Promote A to ops through the DB, then the endpoint opens up.
    _set_user("test_firebase_uid_user_a", role="ops")
    resp = authed_client.get("/api/admin/users")
    assert resp.status_code == 200
    emails = {u["email"] for u in resp.json()}
    assert "user_a@example.com" in emails and "user_b@example.com" in emails


def test_admin_can_change_role_and_plan(authed_client, client, auth_headers_b):
    authed_client.get("/api/auth/me")
    client.get("/api/auth/me", headers=auth_headers_b)
    _set_user("test_firebase_uid_user_a", role="ops")
    user_b_id = _user_id("test_firebase_uid_user_b")

    promoted = authed_client.patch(f"/api/admin/users/{user_b_id}/role", json={"role": "ops"})
    assert promoted.status_code == 200
    assert promoted.json()["role"] == "ops"

    upgraded = authed_client.patch(f"/api/admin/users/{user_b_id}/plan", json={"plan": "pro"})
    assert upgraded.status_code == 200
    assert upgraded.json()["plan"] == "pro"

    bad = authed_client.patch(f"/api/admin/users/{user_b_id}/plan", json={"plan": "fancy"})
    # Pydantic Literal rejections are folded into the project's 400 envelope.
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "validation_error"


def test_admin_cannot_demote_self(authed_client):
    authed_client.get("/api/auth/me")
    _set_user("test_firebase_uid_user_a", role="ops")
    user_a_id = _user_id("test_firebase_uid_user_a")

    resp = authed_client.patch(f"/api/admin/users/{user_a_id}/role", json={"role": "user"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_role_change"


def test_admin_unknown_user_404(authed_client):
    authed_client.get("/api/auth/me")
    _set_user("test_firebase_uid_user_a", role="ops")
    resp = authed_client.patch("/api/admin/users/999999/plan", json={"plan": "pro"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# tier limits: backend-enforced scrape caps
# ---------------------------------------------------------------------------


def test_plan_url_limit_enforced(authed_client):
    authed_client.get("/api/auth/me")  # basic -> 5 URLs
    urls = [f"https://facebook.com/page{i}" for i in range(6)]
    resp = authed_client.post("/api/scrape", json={"urls": urls})
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "plan_limit"


def test_team_plan_url_limit_enforced(authed_client):
    """Team allows 150 URLs; a 151st is rejected (global cap is 300)."""
    authed_client.get("/api/auth/me")
    _set_user("test_firebase_uid_user_a", plan="team")
    urls = [f"https://facebook.com/page{i}" for i in range(151)]
    resp = authed_client.post("/api/scrape", json={"urls": urls})
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "plan_limit"


def test_pro_plan_url_limit_raised_to_50(authed_client):
    """Pro's per-job URL cap moved from 20 to 50 with the tier rework."""
    authed_client.get("/api/auth/me")
    _set_user("test_firebase_uid_user_a", plan="pro")
    urls = [f"https://facebook.com/page{i}" for i in range(51)]
    resp = authed_client.post("/api/scrape", json={"urls": urls})
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "plan_limit"


def test_enterprise_plan_has_no_url_ceiling(authed_client, monkeypatch):
    """Enterprise: no per-plan URL cap — only the global cap applies."""
    install_fake_scraper(
        monkeypatch,
        result_factory=lambda url, options=None, progress_cb=None, cancel_event=None: make_source_result(
            url, posts=sample_posts(1)
        ),
    )
    authed_client.get("/api/auth/me")
    _set_user("test_firebase_uid_user_a", plan="enterprise")
    # 200 URLs is below the global cap (300) and above any old plan cap,
    # so it must succeed for Enterprise.
    urls = [f"https://www.facebook.com/page{i}" for i in range(200)]
    resp = authed_client.post("/api/scrape", json={"urls": urls})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "queued"
    wait_for_job(authed_client, body["job_id"])


def test_plan_max_posts_limit_enforced(authed_client):
    authed_client.get("/api/auth/me")  # basic -> max 500 posts
    resp = authed_client.post(
        "/api/scrape",
        json={"urls": ["https://facebook.com/example"], "max_posts": 501},
    )
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "plan_limit"


def test_plan_concurrent_jobs_limit_enforced(authed_client):
    authed_client.get("/api/auth/me")  # progressive: user A exists
    user_a_id = _user_id("test_firebase_uid_user_a")
    insert_completed_job([], status="queued", options={}, owner_id=user_a_id)

    resp = authed_client.post(
        "/api/scrape",
        json={"urls": ["https://facebook.com/example"]},
    )
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "plan_limit"


def test_me_account_spec_must_exist(authed_client):
    authed_client.get("/api/auth/me")
    resp = authed_client.post(
        "/api/scrape",
        json={
            "urls": ["https://facebook.com/example"],
            "use_browser": True,
            "account": "me:ghost",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "account_not_found"
    # No job rows may have been created for the failed request.
    with SessionLocal() as db:
        assert db.query(ScrapeJob).count() == 0