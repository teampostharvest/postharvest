"""Boot-time sweep + resume worker tests (ROADMAP job-state hygiene).

Covers:
* ``sweep_orphaned_jobs`` — orphaned ``running`` jobs/sources fail with a
  ``suspended_by_restart`` audit row (quota releases as a consequence);
  ``queued`` jobs resume through real workers; ``paused`` jobs are untouched.
* ``POST /api/jobs/{id}/resume`` — hands the job to a worker instead of
  stranding it in ``queued`` (previously a no-op status flip).
"""
from __future__ import annotations

from backend.core.database import SessionLocal
from backend.models import User
from backend.models.errors import ScrapeError
from backend.models.scrape_jobs import ScrapeJob
from backend.models.sources import ScrapeSource
from backend.services.job_service import (
    RESTART_SUSPENDED_CODE,
    sweep_orphaned_jobs,
)
from helpers import (
    error_envelope,
    install_fake_scraper,
    make_source_result,
    sample_posts,
    wait_for_job,
)

PAGE_URL = "https://www.facebook.com/sweeppage"


def _user_id(firebase_uid: str) -> int:
    with SessionLocal() as db:
        user = db.query(User).filter_by(firebase_uid=firebase_uid).first()
        assert user is not None, f"user {firebase_uid} was not provisioned"
        return user.id


def _seed_job(job_id: str, owner_id: int, status: str, source_status: str = "queued") -> None:
    with SessionLocal() as db:
        db.add(
            ScrapeJob(
                id=job_id,
                owner_id=owner_id,
                status=status,
                options={"urls": [PAGE_URL]},
            )
        )
        db.add(
            ScrapeSource(
                job_id=job_id,
                url=PAGE_URL,
                normalized_url=PAGE_URL,
                status=source_status,
            )
        )
        db.commit()


def _statuses(job_id: str) -> tuple[str, str | None, int]:
    with SessionLocal() as db:
        job = db.get(ScrapeJob, job_id)
        assert job is not None
        source = (
            db.query(ScrapeSource).filter_by(job_id=job_id).order_by(ScrapeSource.id).first()
        )
        errors = db.query(ScrapeError).filter_by(job_id=job_id).all()
        codes = [e.code for e in errors]
        return (
            job.status,
            source.status if source else None,
            job.errors_count,
            codes,
            job.completed_at,
        )


def test_sweep_fails_orphaned_running_jobs(authed_client):
    authed_client.get("/api/auth/me")  # provision user A
    uid = _user_id("test_firebase_uid_user_a")
    _seed_job("sweep-running-1", uid, "running", source_status="running")

    result = sweep_orphaned_jobs()

    assert "sweep-running-1" in result["failed"]
    status, source_status, errors_count, codes, completed_at = _statuses("sweep-running-1")
    assert status == "failed"
    assert source_status == "failed"
    assert RESTART_SUSPENDED_CODE in codes
    assert errors_count >= 1
    assert completed_at is not None


def test_sweep_leaves_paused_jobs_alone(authed_client):
    authed_client.get("/api/auth/me")
    uid = _user_id("test_firebase_uid_user_a")
    _seed_job("sweep-paused-1", uid, "paused", source_status="paused")

    result = sweep_orphaned_jobs()

    assert result["failed"] == []
    status, source_status, _, codes, _ = _statuses("sweep-paused-1")
    assert status == "paused"
    assert source_status == "paused"
    assert codes == []


def test_sweep_resumes_queued_jobs_through_workers(authed_client, monkeypatch):
    install_fake_scraper(
        monkeypatch,
        result_factory=lambda url, options=None, progress_cb=None, cancel_event=None: make_source_result(
            url, posts=sample_posts(1)
        ),
    )
    authed_client.get("/api/auth/me")
    uid = _user_id("test_firebase_uid_user_a")
    _seed_job("sweep-queued-1", uid, "queued")

    result = sweep_orphaned_jobs()

    assert "sweep-queued-1" in result["resumed"]
    final = wait_for_job(authed_client, "sweep-queued-1")
    assert final["status"] == "completed"


def test_resume_hands_job_to_worker(authed_client, monkeypatch):
    install_fake_scraper(
        monkeypatch,
        result_factory=lambda url, options=None, progress_cb=None, cancel_event=None: make_source_result(
            url, posts=sample_posts(2)
        ),
    )
    authed_client.get("/api/auth/me")
    uid = _user_id("test_firebase_uid_user_a")
    _seed_job("resume-worker-1", uid, "paused", source_status="paused")

    resp = authed_client.post("/api/jobs/resume-worker-1/resume")
    assert resp.status_code == 200, resp.text

    final = wait_for_job(authed_client, "resume-worker-1")
    assert final["status"] == "completed"
    assert final["posts_processed"] == 2


def test_resume_still_rejects_non_paused(authed_client):
    authed_client.get("/api/auth/me")
    uid = _user_id("test_firebase_uid_user_a")
    _seed_job("resume-reject-1", uid, "completed", source_status="completed")

    resp = authed_client.post("/api/jobs/resume-reject-1/resume")
    assert resp.status_code == 409
    error_envelope(resp.json())
