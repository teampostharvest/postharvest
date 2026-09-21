"""Plan catalog + self-service tier selection tests.

Covers:
* ``GET /api/plans`` — the canonical catalog mirrors ``PLAN_LIMITS``.
* ``PATCH /api/auth/me/plan`` — users can select their own tier.
* ``PATCH /api/admin/users/{id}/plan`` — operators can assign the Team tier
  (previously rejected because the Pydantic literal omitted it).
"""
from __future__ import annotations

from backend.core.database import SessionLocal
from backend.core.plans import PLAN_LIMITS, PLANS
from backend.models import User
from helpers import (
    error_envelope,
    install_fake_scraper,
    make_source_result,
    sample_posts,
    wait_for_job,
)


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


# ---------------------------------------------------------------------------
# GET /api/plans — public catalog
# ---------------------------------------------------------------------------


def test_plans_catalog_is_public_and_ordered(client):
    resp = client.get("/api/plans")
    assert resp.status_code == 200
    catalog = resp.json()
    assert [entry["id"] for entry in catalog] == list(PLANS)
    assert [entry["name"] for entry in catalog] == ["Basic", "Pro", "Team", "Enterprise"]


def test_plans_catalog_mirrors_enforcement_table(client):
    catalog = {entry["id"]: entry["limits"] for entry in client.get("/api/plans").json()}
    assert set(catalog) == set(PLAN_LIMITS)
    for plan_id, limits in PLAN_LIMITS.items():
        assert catalog[plan_id] == limits

    # Spot-check the open-ended Enterprise bounds (None = no per-plan ceiling).
    assert catalog["enterprise"]["urls"] is None
    assert catalog["enterprise"]["max_posts"] is None
    assert catalog["enterprise"]["personal_accounts"] is None


# ---------------------------------------------------------------------------
# PATCH /api/auth/me/plan — REMOVED (tiers are operator-assigned only)
# ---------------------------------------------------------------------------


def test_self_service_plan_endpoint_is_gone(client):
    resp = client.patch("/api/auth/me/plan", json={"plan": "pro"})
    assert resp.status_code == 404
    error_envelope(resp.json())


def test_self_service_plan_gone_for_authed_users(authed_client):
    me = authed_client.get("/api/auth/me")
    assert me.status_code == 200

    resp = authed_client.patch("/api/auth/me/plan", json={"plan": "pro"})
    assert resp.status_code == 404
    error_envelope(resp.json())

    # The attempt changed nothing.
    assert authed_client.get("/api/auth/me").json()["plan"] == "basic"


def test_operator_assignment_lifts_url_cap(authed_client, client, auth_headers_b, monkeypatch):
    """A tier assigned by an operator immediately lifts the enforced caps."""
    install_fake_scraper(
        monkeypatch,
        result_factory=lambda url, options=None, progress_cb=None, cancel_event=None: make_source_result(
            url, posts=sample_posts(1)
        ),
    )
    authed_client.get("/api/auth/me")  # user A
    client.get("/api/auth/me", headers=auth_headers_b)  # user B (basic)
    _set_user("test_firebase_uid_user_a", role="ops")
    user_b_id = _user_id("test_firebase_uid_user_b")

    urls = [f"https://www.facebook.com/page{i}" for i in range(6)]
    blocked = client.post("/api/scrape", headers=auth_headers_b, json={"urls": urls})
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "plan_limit"

    changed = authed_client.patch(f"/api/admin/users/{user_b_id}/plan", json={"plan": "pro"})
    assert changed.status_code == 200
    assert changed.json()["plan"] == "pro"

    allowed = client.post("/api/scrape", headers=auth_headers_b, json={"urls": urls})
    assert allowed.status_code == 201, allowed.text

    class _BClient:
        def get(self, url, **kwargs):
            kwargs["headers"] = dict(auth_headers_b, **(kwargs.get("headers") or {}))
            return client.get(url, **kwargs)

    wait_for_job(_BClient(), allowed.json()["job_id"])


# ---------------------------------------------------------------------------
# admin can assign every tier, including Team
# ---------------------------------------------------------------------------


def test_admin_can_assign_team_plan(authed_client, client, auth_headers_b):
    authed_client.get("/api/auth/me")  # user A
    client.get("/api/auth/me", headers=auth_headers_b)  # user B
    _set_user("test_firebase_uid_user_a", role="ops")
    user_b_id = _user_id("test_firebase_uid_user_b")

    resp = authed_client.patch(f"/api/admin/users/{user_b_id}/plan", json={"plan": "team"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["plan"] == "team"
