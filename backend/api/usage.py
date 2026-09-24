"""Per-user quota readout for the sidebar usage panel.

* ``GET /api/usage`` — ``used/limit`` pairs for everything the server
  enforces, plus denominator-less "today" counters (jobs started, posts
  fetched) for the ops panel and dashboard.

Every number here is derived from the same sources as enforcement
(:mod:`backend.core.plans`, the ``queued``/``running`` job predicate in
``job_service.start_scrape_job``, ``list_accounts`` in the accounts API), so
a displayed ``2/3`` can never disagree with the 429 the next request gets.
Tenant isolation is enforced in the SQL ``WHERE`` clause throughout.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_current_user
from backend.core import cache
from backend.core.database import get_db
from backend.core.logging import get_logger
from backend.core.plans import normalize_plan, plan_limits
from backend.models.posts import Post
from backend.models.scrape_jobs import ScrapeJob
from backend.models.user import User
from backend.scraper.browser_scraper import list_accounts

router = APIRouter(tags=["usage"])

logger = get_logger("api.usage")

ACTIVE_JOB_STATUSES = ("queued", "running")


class LimitOut(BaseModel):
    used: int
    limit: int | None = None


class PerJobOut(BaseModel):
    urls: int | None = None
    max_posts: int | None = None


class UsageOut(BaseModel):
    plan: str
    jobs_running: LimitOut
    personal_accounts: LimitOut
    per_job: PerJobOut
    posts_today: int
    jobs_today: int


@router.get("/usage", response_model=UsageOut, summary="Quota usage for the current user")
def get_usage(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UsageOut:
    """Return used/limit pairs for the caller's tier (``None`` = no ceiling)."""
    cache_key = f"usage:{current_user.id}"
    cached = cache.get_json(cache_key)
    if cached is not None:
        try:
            return UsageOut.model_validate(cached)
        except Exception:  # noqa: BLE001 - stale/corrupt entry, recompute below
            logger.debug("Discarding unreadable usage cache entry", exc_info=True)

    plan = normalize_plan(current_user.plan)
    limits = plan_limits(plan)

    active_jobs = int(
        db.scalar(
            select(func.count())
            .select_from(ScrapeJob)
            .where(
                ScrapeJob.owner_id == current_user.id,
                ScrapeJob.status.in_(ACTIVE_JOB_STATUSES),
            )
        )
        or 0
    )

    personal_used = len(list_accounts(owner_id=current_user.id))

    day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    posts_today = int(
        db.scalar(
            select(func.count())
            .select_from(Post)
            .join(ScrapeJob, Post.job_id == ScrapeJob.id)
            .where(
                ScrapeJob.owner_id == current_user.id,
                Post.scraped_at >= day_start,
            )
        )
        or 0
    )
    jobs_today = int(
        db.scalar(
            select(func.count())
            .select_from(ScrapeJob)
            .where(
                ScrapeJob.owner_id == current_user.id,
                ScrapeJob.created_at >= day_start,
            )
        )
        or 0
    )

    result = UsageOut(
        plan=plan,
        jobs_running=LimitOut(used=active_jobs, limit=limits["concurrent_jobs"]),
        personal_accounts=LimitOut(used=personal_used, limit=limits["personal_accounts"]),
        per_job=PerJobOut(urls=limits["urls"], max_posts=limits["max_posts"]),
        posts_today=posts_today,
        jobs_today=jobs_today,
    )
    cache.set_json(cache_key, result.model_dump(), ttl=cache.DEFAULT_TTL_SECONDS)
    return result
