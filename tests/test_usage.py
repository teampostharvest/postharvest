"""GET /api/usage — per-user quota readout tests.

Covers:
* auth required (401 + error envelope)
* default Basic readout (0/1 jobs, 0/1 accounts, 5 urls / 500 max_posts, 0 posts today)
* active-job counting matches the enforcement predicate (queued + running
  only; completed/failed/paused excluded; other tenants excluded)
* limits follow the caller's current tier
* posts_today counts only the caller's posts scraped since UTC midnight
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.core.database import SessionLocal
from backend.models import User
from backend.models.posts import Post
from backend.models.scrape_jobs import ScrapeJob
from backend.models.sources import ScrapeSource
from helpers import error_envelope


def _user_id(firebase_uid: str) -> int:
    with SessionLocal() as db:
        user = db.query(User).filter_by(firebase_uid=firebase_uid).first()
        assert user is not None, f"user {firebase_uid} was not provisioned"
        return user.id


def _add_job(job_id: str, owner_id: int, status: str) -> None:
    with SessionLocal() as db:
        db.add(ScrapeJob(id=job_id, owner_id=owner_id, status=status))
        db.commit()


_post_seq = 0


def _add_post(job_id: str, owner_id: int, scraped_at: datetime) -> None:
    global _post_seq
    _post_seq += 1
    with SessionLocal() as db:
        source = ScrapeSource(
            job_id=job_id,
            url=f"https://www.facebook.com/p{_post_seq}",
            normalized_url=f"https://www.facebook.com/p{_post_seq}",
        )
        db.add(source)
        db.flush()
        db.add(
            Post(
                job_id=job_id,
                source_id=source.id,
                dedup_key=f"fp:{job_id}:{scraped_at.isoformat()}",
                scraped_at=scraped_at,
            )
        )
        db.commit()


def test_usage_requires_auth(client):
    resp = client.get("/api/usage")
    assert resp.status_code == 401
    error_envelope(resp.json())


def test_usage_defaults_for_basic_user(authed_client):
    authed_client.get("/api/auth/me")  # provision user A (basic)
    resp = authed_client.get("/api/usage")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["plan"] == "basic"
    assert body["jobs_running"] == {"used": 0, "limit": 1}
    assert body["personal_accounts"] == {"used": 0, "limit": 1}
    assert body["per_job"] == {"urls": 5, "max_posts": 500}
    assert body["posts_today"] == 0
    assert body["jobs_today"] == 0


def test_usage_counts_active_jobs_like_enforcement(authed_client, client, auth_headers_b):
    authed_client.get("/api/auth/me")  # user A
    client.get("/api/auth/me", headers=auth_headers_b)  # user B
    uid_a = _user_id("test_firebase_uid_user_a")
    uid_b = _user_id("test_firebase_uid_user_b")

    _add_job("usage-job-q1", uid_a, "queued")
    _add_job("usage-job-r1", uid_a, "running")
    _add_job("usage-job-done", uid_a, "completed")
    _add_job("usage-job-fail", uid_a, "failed")
    _add_job("usage-job-paused", uid_a, "paused")
    _add_job("usage-job-other", uid_b, "running")

    body = authed_client.get("/api/usage").json()
    assert body["jobs_running"] == {"used": 2, "limit": 1}


def _set_plan(db_plan: str) -> None:
    with SessionLocal() as db:
        user = db.query(User).filter_by(firebase_uid="test_firebase_uid_user_a").one()
        user.plan = db_plan
        db.commit()


def test_usage_limits_follow_current_tier(authed_client):
    authed_client.get("/api/auth/me")
    _set_plan("team")

    body = authed_client.get("/api/usage").json()
    assert body["plan"] == "team"
    assert body["jobs_running"]["limit"] == 10
    assert body["personal_accounts"]["limit"] == 25
    assert body["per_job"] == {"urls": 150, "max_posts": 100000}


def test_usage_posts_today_counts_own_posts_since_midnight(
    authed_client, client, auth_headers_b
):
    authed_client.get("/api/auth/me")  # user A
    client.get("/api/auth/me", headers=auth_headers_b)  # user B
    uid_a = _user_id("test_firebase_uid_user_a")
    uid_b = _user_id("test_firebase_uid_user_b")

    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1, hours=2)
    _add_job("usage-posts-a", uid_a, "completed")
    _add_job("usage-posts-b", uid_b, "completed")
    _add_post("usage-posts-a", uid_a, now)
    _add_post("usage-posts-a", uid_a, now)
    _add_post("usage-posts-a", uid_a, yesterday)
    _add_post("usage-posts-b", uid_b, now)

    body = authed_client.get("/api/usage").json()
    assert body["posts_today"] == 2
    # jobs_today counts jobs created since UTC midnight (both test jobs are new).
    assert body["jobs_today"] == 1
