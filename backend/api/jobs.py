"""Job status / posts / deletion endpoints.

* GET    /api/jobs/{job_id}          — live status + progress counters (spec §8)
* GET    /api/jobs/{job_id}/posts    — paginated normalized posts (spec §14)
* GET    /api/jobs/{job_id}/stats    — aggregated KPIs (bonus, for the dashboard)
* POST   /api/jobs/{job_id}/pause    — pause a running job
* POST   /api/jobs/{job_id}/resume   — resume a paused job
* DELETE /api/jobs/{job_id}          — best-effort cancel + delete rows (204)

All unknown jobs return 404 {"error": {"code": "not_found", ...}}.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from backend.auth.dependencies import get_current_user
from backend.core.config import get_settings
from backend.core.database import get_db
from backend.core.exceptions import AppError, InvalidInputError, NotFoundError
from backend.core.job_manager import JobManager
from backend.models.errors import ScrapeError
from backend.models.posts import Post
from backend.models.scrape_jobs import ScrapeJob
from backend.models.sources import ScrapeSource
from backend.models.user import User
from backend.schemas.jobs import (
    ErrorDetail,
    JobListResponse,
    JobStatsResponse,
    JobStatusResponse,
    JobSummary,
    PostOut,
    PostPageResponse,
    SourceStatus,
)
from backend.services import serialization, stats as stats_service

router = APIRouter(tags=["jobs"])


def _get_job_or_404(db: Session, job_id: str, owner_id: int | None = None) -> ScrapeJob:
    stmt = select(ScrapeJob).where(ScrapeJob.id == job_id)
    if owner_id is not None:
        stmt = stmt.where(ScrapeJob.owner_id == owner_id)
    job = db.scalar(stmt)
    if job is None:
        raise NotFoundError(f"Job {job_id} not found")
    return job


@router.get(
    "/jobs",
    response_model=JobListResponse,
    summary="List recent jobs (paginated)",
)
def list_jobs(
    page: int = Query(1, ge=1, description="1-based page number"),
    page_size: int = Query(25, ge=1, le=100, description="Items per page (max 100)"),
    status: str | None = Query(
        None,
        description="Comma-separated status filter (e.g. 'queued,running'). Powers the ops-panel active-jobs list.",
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JobListResponse:
    """Return job history for current user, newest first."""
    settings = get_settings()
    page_size = min(page_size, settings.page_size_max)

    statuses: list[str] | None = None
    if status is not None:
        statuses = [part.strip().lower() for part in status.split(",") if part.strip()]
        unknown = [s for s in statuses if s not in ("queued", "running", "paused", "completed", "failed")]
        if not statuses or unknown:
            raise InvalidInputError(
                f"Unknown job status filter: {', '.join(unknown) or status!r}. "
                "Use queued, running, paused, completed, failed."
            )

    base_where = [ScrapeJob.owner_id == current_user.id]
    if statuses is not None:
        base_where.append(ScrapeJob.status.in_(statuses))

    total = int(
        db.scalar(
            select(func.count())
            .select_from(ScrapeJob)
            .where(*base_where)
        )
        or 0
    )
    jobs = db.scalars(
        select(ScrapeJob)
        .where(*base_where)
        .order_by(ScrapeJob.created_at.desc(), ScrapeJob.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    items = [
        JobSummary(
            job_id=job.id,
            status=job.status
            if job.status in ("queued", "running", "completed", "failed")
            else "failed",
            pages_total=job.pages_total,
            pages_completed=job.pages_completed,
            posts_found=job.posts_found,
            posts_processed=job.posts_processed,
            duplicates=job.duplicates,
            errors=job.errors_count,
            urls=list(job.options.get("urls") or []) if job.options else [],
            max_posts=job.options.get("max_posts") if job.options else None,
            post_type=job.options.get("post_type") if job.options else None,
            created_at=serialization.iso_format(job.created_at),
            completed_at=serialization.iso_format(job.completed_at),
        )
        for job in jobs
    ]
    return JobListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get(
    "/jobs/{job_id}",
    response_model=JobStatusResponse,
    summary="Get job status & live progress",
)
def get_job_status(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JobStatusResponse:
    """Return the job's state machine position, counters and recent errors."""
    job = _get_job_or_404(db, job_id, owner_id=current_user.id)
    error_rows = db.scalars(
        select(ScrapeError)
        .where(ScrapeError.job_id == job_id)
        .order_by(ScrapeError.id.desc())
        .limit(50)
    ).all()
    error_details = [
        ErrorDetail(
            url=e.source_url or None,
            post_url=e.post_url,
            code=e.code,
            message=e.message,
        )
        for e in error_rows
    ]
    source_rows = db.scalars(
        select(ScrapeSource)
        .where(ScrapeSource.job_id == job_id)
        .order_by(ScrapeSource.id)
    ).all()
    sources = [
        SourceStatus(
            url=s.normalized_url or s.url,
            status=s.status,
            posts_found=s.posts_discovered,
            posts_processed=s.posts_extracted,
            error_code=s.error_code,
            error_message=s.error_message,
        )
        for s in source_rows
    ]
    return JobStatusResponse(
        job_id=job.id,
        status=job.status if job.status in ("queued", "running", "completed", "failed") else "failed",
        pages_total=job.pages_total,
        pages_completed=job.pages_completed,
        posts_found=job.posts_found,
        posts_processed=job.posts_processed,
        duplicates=job.duplicates,
        errors=job.errors_count,
        error_details=error_details,
        posts_skipped=job.posts_skipped,
        posts_failed=job.posts_failed,
        cancel_requested=job.cancel_requested,
        created_at=serialization.iso_format(job.created_at),
        started_at=serialization.iso_format(job.started_at),
        completed_at=serialization.iso_format(job.completed_at),
        max_posts=job.options.get("max_posts") if job.options else None,
        sources=sources,
    )


@router.get(
    "/jobs/{job_id}/posts",
    response_model=PostPageResponse,
    summary="List extracted posts (paginated)",
)
def list_posts(
    job_id: str,
    page: int = Query(1, ge=1, description="1-based page number"),
    page_size: int = Query(50, ge=1, le=200, description="Items per page (max 200)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PostPageResponse:
    """Return extracted posts for the job, newest first, paginated."""
    _get_job_or_404(db, job_id, owner_id=current_user.id)
    settings = get_settings()
    page_size = min(page_size, settings.page_size_max)

    total = int(
        db.scalar(select(func.count()).select_from(Post).where(Post.job_id == job_id))
        or 0
    )
    stmt = (
        select(Post)
        .where(Post.job_id == job_id)
        .options(selectinload(Post.engagement), selectinload(Post.media))
        .order_by(Post.published_at.desc().nulls_last(), Post.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    posts = db.scalars(stmt).all()
    return PostPageResponse(
        items=[PostOut(**serialization.post_to_dict(post)) for post in posts],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/jobs/{job_id}/stats",
    response_model=JobStatsResponse,
    summary="Aggregated KPIs for the dashboard (bonus endpoint)",
)
def get_job_stats(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JobStatsResponse:
    """Return KPI aggregates (totals by type and engagement) for the job."""
    _get_job_or_404(db, job_id, owner_id=current_user.id)
    return JobStatsResponse(**stats_service.aggregate_job_stats(db, job_id, owner_id=current_user.id))


@router.delete(
    "/jobs/{job_id}",
    status_code=204,
    summary="Cancel (best-effort) and delete a job",
)
def delete_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    """Request cancellation, wait briefly for the worker, then delete rows."""
    job = _get_job_or_404(db, job_id, owner_id=current_user.id)
    job.cancel_requested = True
    db.commit()

    manager = JobManager.get()
    manager.cancel(job.id, wait_seconds=get_settings().cancel_wait_seconds)

    db.execute(
        delete(ScrapeJob).where(
            ScrapeJob.id == job_id, ScrapeJob.owner_id == current_user.id
        )
    )
    db.commit()
    return Response(status_code=204)


@router.post(
    "/jobs/{job_id}/pause",
    status_code=200,
    summary="Pause a running job",
)
def pause_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Pause a running/queued job.  The worker will stop after the current
    source completes.  Returns the updated job status."""
    job = _get_job_or_404(db, job_id, owner_id=current_user.id)
    if job.status not in ("queued", "running"):
        raise AppError(
            f"Cannot pause job in status '{job.status}'",
            status_code=409,
            code="invalid_state",
        )
    job.status = "paused"
    db.commit()
    return {"job_id": job.id, "status": "paused"}


@router.post(
    "/jobs/{job_id}/resume",
    status_code=200,
    summary="Resume a paused job",
)
def resume_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Resume a paused job.  The worker will pick up where it left off
    using the CrawlState checkpoint.  Returns the updated job status."""
    job = _get_job_or_404(db, job_id, owner_id=current_user.id)
    if job.status != "paused":
        raise AppError(
            f"Cannot resume job in status '{job.status}'",
            status_code=409,
            code="invalid_state",
        )
    job.status = "queued"
    job.cancel_requested = False
    db.commit()
    # A bare status flip strands the job: no worker watches the table, so
    # hand it to the pool here (same entry point as a fresh submit).
    from backend.services.job_service import run_scrape_job

    JobManager.get().submit(job.id, run_scrape_job)
    return {"job_id": job.id, "status": "queued"}