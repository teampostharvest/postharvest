"""Job orchestration service.

Responsibilities
----------------
* ``start_scrape_job`` — validate submitted URLs with the scraper's
  ``validate_facebook_url``, persist the job + sources + validation errors,
  and hand the job to the background :class:`JobManager` so POST /api/scrape
  returns immediately with ``{"job_id", "status": "queued"}``.
* ``run_scrape_job`` — worker entry point (executes in a pool thread):
  queued -> running -> completed/failed; one source never fails the whole job;
  per-source progress is persisted to the DB so GET /api/jobs/{id} reflects
  live counters; cancellation is honoured between sources and via the
  ``cancel_event`` handed to ``scrape_source``.

Dependency injection / testability
----------------------------------
``backend.scraper`` and ``backend.exporters`` are imported lazily inside
functions through ``_scraper_module()``. Tests can monkeypatch
``backend.scraper.validate_facebook_url`` / ``backend.scraper.scrape_source``
(they resolve at call time) and the whole app still imports cleanly even if
the scraper package is temporarily absent — at runtime that yields a clear
503 ``scraper_unavailable`` instead of a crash.

Progress callback contract (documented to SA02)
-----------------------------------------------
``progress_cb`` may be called as ``progress_cb(**counters)`` or
``progress_cb({...})`` or with positional ints following the stats key order
``(posts_found, posts_extracted, duplicates_removed, posts_skipped,
posts_failed)``. Values are treated as ABSOLUTE cumulative counters for the
current source. The callback is deliberately lenient: unknown payload shapes
are ignored without raising.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from backend.core.config import get_settings
from backend.core.database import SessionLocal
from backend.core.exceptions import AppError, InvalidInputError
from backend.core import cache, job_state
from backend.core.job_manager import CancelToken, JobManager
from backend.core.logging import get_logger
from backend.models.engagement_metrics import EngagementMetric
from backend.models.errors import ScrapeError
from backend.models.media import Media
from backend.models.posts import Post
from backend.models.scrape_jobs import ScrapeJob
from backend.models.sources import ScrapeSource
from backend.services import crawl_state_service
from backend.schemas.scrape import ScrapeRequest

logger = get_logger("services.job_service")

# Process-wide background runner.
job_manager = JobManager.get()

# Scraper exception class name -> persisted error code. Kept as a plain name
# mapping so the core app never hard-depends on the scraper's errors module.
SCRAPER_ERROR_CODES = {
    "InvalidUrl": "invalid_url",
    "UnsupportedUrl": "unsupported_url",
    "PageUnavailable": "page_unavailable",
    "AuthRequired": "auth_required",
    "RateLimited": "rate_limited",
    "Timeout": "timeout",
    "ExtractionFailure": "extraction_failure",
}

_PROGRESS_CACHE: dict[str, tuple[float, tuple[int, ...]]] = {}
_PROGRESS_LOCK = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Lazy access to the scraper engine (owned by SA02)
# ---------------------------------------------------------------------------


def _scraper_module() -> Any:
    """Import ``backend.scraper`` lazily with a friendly 503 when missing.

    Required public symbols per the shared contract:
        validate_facebook_url, scrape_source, ScrapeOptions
    """
    try:
        import backend.scraper as scraper  # noqa: PLC0415 - intentional lazy import
    except ImportError as exc:
        raise AppError(
            "The scraping engine (backend.scraper) is not available. "
            "Check the backend deployment.",
            status_code=503,
            code="scraper_unavailable",
        ) from exc

    missing = [
        name
        for name in ("validate_facebook_url", "scrape_source", "ScrapeOptions")
        if not hasattr(scraper, name)
    ]
    if missing:
        raise AppError(
            "The scraping engine (backend.scraper) is incomplete; missing "
            f"required symbols: {', '.join(missing)}.",
            status_code=503,
            code="scraper_unavailable",
        )
    return scraper


def _build_scrape_options(urls: list[str], snapshot: dict) -> Any:
    """Build a ScrapeOptions object from stored job options.

    Falls back to a plain dict (same keys) if the scraper's options type is
    not kwargs-compatible — the scraper layer is the owner of that class.
    Pulls proxy and delay settings from the global config as defaults.
    """
    scraper = _scraper_module()
    settings = get_settings()
    kwargs: dict[str, Any] = {
        "urls": urls,
        "max_posts": snapshot.get("max_posts"),
        "start_date": snapshot.get("start_date"),
        "end_date": snapshot.get("end_date"),
        "post_type": snapshot.get("post_type"),
        "delay": snapshot.get("delay") or settings.scraper_delay_seconds,
        "proxy_url": snapshot.get("proxy_url") or settings.proxy_url,
    }
    try:
        return scraper.ScrapeOptions(**kwargs)
    except TypeError:
        logger.warning(
            "ScrapeOptions(**kwargs) failed for %s; falling back to plain dict",
            url_list_summary(urls),
        )
        return kwargs


def url_list_summary(urls: list[str], limit: int = 2) -> str:
    """Short human-readable summary of a URL list (for logs)."""
    shown = ", ".join(urls[:limit])
    more = f" (+{len(urls) - limit} more)" if len(urls) > limit else ""
    return f"{shown}{more}"


# ---------------------------------------------------------------------------
# URL validation & job creation
# ---------------------------------------------------------------------------


def validate_and_collect_urls(raw_urls: list[str]) -> tuple[list[dict], list[dict]]:
    """Validate raw URLs through the scraper's ``validate_facebook_url``.

    Returns ``(valid_entries, invalid_entries)`` where each entry is a dict:
    ``{url, normalized_url|None, code|None, message|None}``. Duplicate
    normalized URLs are reported as invalid with code ``duplicate_url`` so no
    duplicate requests are ever made.
    """
    scraper = _scraper_module()

    valid: list[dict] = []
    invalid: list[dict] = []
    seen: set[str] = set()

    for raw in raw_urls:
        raw = (raw or "").strip()
        if not raw:
            invalid.append(
                {
                    "url": "",
                    "normalized_url": None,
                    "code": "invalid_url",
                    "message": "URL is empty",
                }
            )
            continue
        try:
            verdict = scraper.validate_facebook_url(raw)
        except Exception as exc:  # noqa: BLE001 - scraper throw-safety
            invalid.append(
                {
                    "url": raw,
                    "normalized_url": None,
                    "code": "validation_error",
                    "message": f"URL validation failed: {exc}",
                }
            )
            continue

        if not verdict or not verdict.get("valid"):
            reason = (verdict or {}).get("reason") or "Invalid Facebook URL"
            reason_lower = reason.lower()
            code = "unsupported_url" if "unsupported" in reason_lower else "invalid_url"
            invalid.append(
                {
                    "url": raw,
                    "normalized_url": None,
                    "code": code,
                    "message": reason,
                }
            )
            continue

        normalized = (verdict.get("normalized_url") or raw).strip()
        if normalized in seen:
            invalid.append(
                {
                    "url": raw,
                    "normalized_url": normalized,
                    "code": "duplicate_url",
                    "message": "URL was already provided; duplicate ignored",
                }
            )
            continue
        seen.add(normalized)
        valid.append(
            {"url": raw, "normalized_url": normalized, "code": None, "message": None}
        )

    return valid, invalid


def start_scrape_job(db, request: ScrapeRequest, owner_id: int | None = None) -> ScrapeJob:
    """Validate URLs, persist job + sources + validation errors, queue worker.

    Raises :class:`InvalidInputError` (400) when no valid URL remains after
    validation, or :class:`~backend.core.exceptions.AppError` 503 when the
    scraper engine cannot be imported.
    """
    settings = get_settings()
    if len(request.urls) > settings.max_urls_per_job:
        raise InvalidInputError(
            f"Too many URLs: {len(request.urls)} (max {settings.max_urls_per_job})"
        )

    # Tier limits (Basic / Pro / Team / Enterprise) — enforced server-side,
    # on top of the global hard caps above. Unauthenticated/CLI jobs pass
    # through.
    if owner_id is not None:
        from backend.core.plans import plan_limits
        from backend.models.user import User

        user = db.get(User, owner_id)
        if user is not None:
            limits = plan_limits(user.plan)
            url_cap = limits["urls"]
            if url_cap is not None and len(request.urls) > url_cap:
                raise AppError(
                    f"Your {user.plan} plan allows up to {limits['urls']} "
                    "URL(s) per job",
                    status_code=429,
                    code="plan_limit",
                )
            if (
                request.max_posts is not None
                and limits["max_posts"]
                and request.max_posts > limits["max_posts"]
            ):
                raise AppError(
                    f"Your {user.plan} plan allows up to {limits['max_posts']} "
                    "posts per source",
                    status_code=429,
                    code="plan_limit",
                )
            running = db.scalar(
                select(func.count())
                .select_from(ScrapeJob)
                .where(
                    ScrapeJob.owner_id == owner_id,
                    ScrapeJob.status.in_(("queued", "running")),
                )
            )
            if running >= limits["concurrent_jobs"]:
                raise AppError(
                    f"Your {user.plan} plan allows {limits['concurrent_jobs']} "
                    "concurrent job(s); wait for the current job to finish",
                    status_code=429,
                    code="plan_limit",
                )
            # `me:<name>` must exist in the caller's own store; `ops:*` and
            # bare names pass through (ops sessions may be added later).
            if request.account:
                from backend.scraper.browser_scraper import load_cookies, parse_account_spec

                scope, name = parse_account_spec(request.account)
                if scope == "me" and name and not load_cookies(name, owner_id=owner_id):
                    raise AppError(
                        f"Personal account '{name}' not found",
                        status_code=400,
                        code="account_not_found",
                    )

    valid, invalid = validate_and_collect_urls(request.urls)
    if not valid:
        details = "; ".join(
            f"{e['url'] or '<empty>'}: {e['message']}" for e in invalid[:5]
        )
        raise InvalidInputError(
            f"No valid Facebook URLs were provided. {details}",
            code="invalid_input",
        )

    job_id = secrets.token_hex(8)
    max_posts = request.max_posts if request.max_posts is not None else settings.default_max_posts
    post_type = request.post_type or settings.default_post_type
    options = {
        "urls": [e["normalized_url"] for e in valid],
        "max_posts": max_posts,
        "start_date": request.start_date,
        "end_date": request.end_date,
        "post_type": post_type,
        "use_browser": request.use_browser,
        "account": request.account,
        "scrolls": request.scrolls,
    }

    job = ScrapeJob(
        id=job_id,
        owner_id=owner_id,
        status="queued",
        pages_total=len(valid),
        options=options,
        errors_count=len(invalid),
    )
    db.add(job)
    for entry in valid:
        db.add(
            ScrapeSource(
                job_id=job_id,
                url=entry["url"],
                normalized_url=entry["normalized_url"],
                status="queued",
            )
        )
    for entry in invalid:
        db.add(
            ScrapeError(
                job_id=job_id,
                source_url=entry["url"] or entry["normalized_url"] or "",
                code=entry["code"] or "invalid_url",
                message=entry["message"] or "Invalid Facebook URL",
            )
        )
    db.commit()
    db.refresh(job)

    job_manager.submit(job_id, run_scrape_job)
    job_state.set_status(job_id, "queued")  # Redis mirror (finalplanv2 §8b)
    cache.invalidate_usage(owner_id)  # quota readout changed (active jobs +1)
    logger.info(
        "Job %s queued: %d source(s) valid (%s), %d invalid URL(s)",
        job_id,
        len(valid),
        url_list_summary([e["normalized_url"] for e in valid]),
        len(invalid),
    )
    return job


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------


def run_scrape_job(job_id: str) -> None:
    """Worker entry point (runs inside a JobManager pool thread)."""
    token = job_manager.token(job_id)
    try:
        _run_job_inner(job_id, token)
    except AppError as exc:
        _finalize_failure(job_id, code=exc.code, message=exc.message)
    except Exception:  # noqa: BLE001 - last-resort barrier
        logger.exception("Unhandled error while running job %s", job_id)
        _finalize_failure(
            job_id,
            code="internal_error",
            message="Unexpected internal error while running the job",
        )


def _run_job_inner(job_id: str, token: CancelToken | None) -> None:
    with SessionLocal() as db:
        job = db.get(ScrapeJob, job_id)
        if job is None or job.cancel_requested:
            return  # deleted or cancelled before the worker started
        job.status = "running"
        job.updated_at = _now()
        if job.started_at is None:
            job.started_at = _now()
        options_snapshot: dict = job.options or {}
        owner_id: int | None = job.owner_id
        db.commit()
    job_state.set_status(job_id, "running")  # Redis mirror (finalplanv2 §8b)

    with SessionLocal() as db:
        source_ids = list(
            db.scalars(
                select(ScrapeSource.id)
                .where(ScrapeSource.job_id == job_id)
                .order_by(ScrapeSource.id)
            )
        )
    if not source_ids:
        logger.warning("Job %s has no sources; nothing to scrape", job_id)
        return

    for source_id in source_ids:
        if token is not None and token.cancelled:
            _mark_source_cancelled(job_id, source_id)
            break
        _process_source(job_id, source_id, options_snapshot, token, owner_id=owner_id)

    _finalize(job_id, token)


def _refresh_source_derived_counters(db, job: ScrapeJob) -> None:
    """Recompute job counters that derive from the sources rows (absolute).

    Used on every progress ping and after each source completes so live
    counters are internally consistent (mixing incremental += and absolute
    recomputes would double-count a source that reported progress).
    """
    sums = db.execute(
        select(
            func.coalesce(func.sum(ScrapeSource.posts_discovered), 0),
            func.coalesce(func.sum(ScrapeSource.posts_extracted), 0),
            func.coalesce(func.sum(ScrapeSource.duplicates_removed), 0),
            func.coalesce(func.sum(ScrapeSource.posts_skipped), 0),
            func.coalesce(func.sum(ScrapeSource.posts_failed), 0),
        ).where(ScrapeSource.job_id == job.id)
    ).one()
    job.posts_found = int(sums[0])
    job.posts_processed = int(sums[1])
    job.duplicates = int(sums[2]) + int(job.storage_duplicates or 0)
    job.posts_skipped = int(sums[3])
    job.posts_failed = int(sums[4])


def _process_source(
    job_id: str,
    source_id: int,
    options_snapshot: dict,
    token: CancelToken | None,
    owner_id: int | None = None,
) -> None:
    """Scrape one source, persist posts/errors, update job counters.

    ``owner_id`` is the job owner — used to resolve ``me:<name>`` personal
    cookie accounts against that user's store at scrape time.
    """
    scraper = _scraper_module()

    with SessionLocal() as db:
        source = db.get(ScrapeSource, source_id)
        if source is None:
            return  # job was deleted mid-run
        source.status = "running"
        source.started_at = _now()
        db.commit()
        source_url = source.normalized_url

        # Create or get crawl state for resume tracking
        crawl_state = crawl_state_service.get_resume_state(
            db, source_id=source_id, job_id=job_id
        )
        if crawl_state is None:
            crawl_state = crawl_state_service.create(
                db, source_id=source_id, job_id=job_id
            )
        crawl_state_service.mark_running(db, crawl_state)
        crawl_state_id = crawl_state.id
        db.commit()

    try:
        if options_snapshot.get("use_browser"):
            from backend.scraper.browser_scraper import (
                parse_account_spec,
                scrape_source_browser,
            )

            scope, account_name = parse_account_spec(
                options_snapshot.get("account") or None
            )
            # `me:<name>` resolves against the job owner's personal store;
            # `ops:<name>` / bare names use the shared ops pool.
            scrape_owner = owner_id if scope == "me" else None
            result = scrape_source_browser(
                source_url,
                max_posts=options_snapshot.get("max_posts"),
                scroll_rounds=options_snapshot.get("scrolls") or None,
                account_name=account_name,
                owner_id=scrape_owner,
                start_date=options_snapshot.get("start_date"),
                end_date=options_snapshot.get("end_date"),
                post_type=options_snapshot.get("post_type"),
                cancel_event=token.event if token else None,
                progress_callback=_make_progress_callback(job_id, source_id),
            )
        else:
            options = _build_scrape_options([source_url], options_snapshot)
            result = scraper.scrape_source(
                source_url,
                options,
                _make_progress_callback(job_id, source_id),
                token.event if token else None,
            )
    except Exception as exc:  # noqa: BLE001 - typed scraper errors map to codes
        if isinstance(exc, AppError):
            code, message = exc.code, exc.message
        else:
            # ScraperError exposes .code (may carry custom codes like
            # network_error / robots_disallowed); fall back to the class name.
            code = getattr(exc, "code", None) or SCRAPER_ERROR_CODES.get(
                type(exc).__name__, "scrape_failed"
            )
            message = str(exc) or code
        logger.warning(
            "Source %s (job %s) failed: %s %s", source_id, job_id, code, message
        )
        _persist_source_failure(job_id, source_id, code, message)
        return

    stats: dict = getattr(result, "stats", None) or {}
    posts: list = getattr(result, "posts", None) or []
    errors: list = getattr(result, "errors", None) or []

    with SessionLocal() as db:
        source = db.get(ScrapeSource, source_id)
        job = db.get(ScrapeJob, job_id)
        if source is None or job is None:
            return  # job deleted mid-run (best-effort cancel)

        stored, extra_duplicates = _store_posts(db, job_id, source_id, posts)

        source.status = "completed"
        source.page_name = getattr(result, "page_name", None)
        source.page_id = getattr(result, "page_id", None)
        source.posts_discovered = int(stats.get("posts_discovered") or 0)
        source.posts_extracted = stored
        source.duplicates_removed = int(stats.get("duplicates_removed") or 0)
        source.posts_skipped = int(stats.get("posts_skipped") or 0)
        source.posts_failed = int(stats.get("posts_failed") or 0)
        source.finished_at = _now()

        _record_errors(db, job_id, source_id, source_url, errors)

        job.pages_completed += 1
        job.errors_count += len(errors)
        job.storage_duplicates += extra_duplicates
        _refresh_source_derived_counters(db, job)
        job.updated_at = _now()

        try:
            db.commit()
        except Exception:  # noqa: BLE001 - job may have been deleted concurrently
            db.rollback()
            logger.exception("Could not persist source %s result (job %s)", source_id, job_id)

    # Checkpoint crawl state after successful storage
    try:
        with SessionLocal() as db:
            cs = db.get(crawl_state_service.CrawlState, crawl_state_id)
            if cs is not None:
                crawl_state_service.checkpoint(
                    db,
                    cs,
                    pages_fetched=1,
                    posts_extracted=len(posts),
                    posts_stored=stored,
                )
                crawl_state_service.mark_completed(db, cs)
                db.commit()
    except Exception:
        logger.debug("CrawlState checkpoint failed for source %s", source_id, exc_info=True)

    logger.info(
        "Source %s (job %s) completed: %d post(s) stored, %d duplicate(s), %d error(s)",
        source_id,
        job_id,
        stored,
        extra_duplicates,
        len(errors),
    )


def _finalize(job_id: str, token: CancelToken | None) -> None:
    """Recompute truthful counters, then complete or fail the job."""
    with SessionLocal() as db:
        job = db.get(ScrapeJob, job_id)
        if job is None:
            return

        owner_id = job.owner_id
        cancelled = (token is not None and token.cancelled) or bool(
            job.cancel_requested
        )

        # Truth-check the aggregated counters from the actual rows. The
        # sum-based refresh runs first; the COUNT(*) values are authoritative
        # and therefore applied last.
        _refresh_source_derived_counters(db, job)
        job.posts_processed = int(
            db.scalar(select(func.count(Post.id)).where(Post.job_id == job_id)) or 0
        )
        job.errors_count = int(
            db.scalar(
                select(func.count(ScrapeError.id)).where(ScrapeError.job_id == job_id)
            )
            or 0
        )
        job.pages_completed = int(
            db.scalar(
                select(func.count(ScrapeSource.id)).where(
                    ScrapeSource.job_id == job_id,
                    ScrapeSource.status.in_(("completed", "failed", "cancelled")),
                )
            )
            or 0
        )

        if cancelled:
            job.status = "failed"
            db.add(
                ScrapeError(
                    job_id=job_id,
                    source_url="",
                    code="cancelled",
                    message="Job was cancelled by the user",
                )
            )
            job.errors_count += 1
            logger.info("Job %s cancelled by user", job_id)
        else:
            job.status = "completed"
            logger.info(
                "Job %s completed: %d post(s), %d duplicate(s), %d error(s)",
                job_id,
                job.posts_processed,
                job.duplicates,
                job.errors_count,
            )
        job.completed_at = _now()
        job.updated_at = _now()
        db.commit()
    # Redis mirror for the terminal status (finalplanv2 §8b). The mirror key
    # stays put so replicas can read a terminal job without hitting the DB.
    job_state.set_status(job_id, "failed" if cancelled else "completed")
    cache.invalidate_usage(owner_id)  # quota readout changed (job terminal)


def _finalize_failure(job_id: str, code: str, message: str) -> None:
    with SessionLocal() as db:
        job = db.get(ScrapeJob, job_id)
        if job is None:
            return
        owner_id = job.owner_id
        job.status = "failed"
        job.errors_count += 1
        job.completed_at = _now()
        job.updated_at = _now()
        db.add(
            ScrapeError(job_id=job_id, source_url="", code=code, message=message)
        )
        db.commit()
    job_state.set_status(job_id, "failed")  # Redis mirror (finalplanv2 §8b)
    cache.invalidate_usage(owner_id)  # quota readout changed (job terminal)


# ---------------------------------------------------------------------------
# Boot-time sweep (ROADMAP job-state hygiene)
# ---------------------------------------------------------------------------

RESTART_SUSPENDED_CODE = "suspended_by_restart"
RESTART_SUSPENDED_MESSAGE = (
    "Job did not finish before the server restarted; "
    "no worker exists for it anymore. Re-submit to run it again."
)


def sweep_orphaned_jobs() -> dict:
    """Reconcile job rows with the (empty) in-process worker pool on boot.

    Worker state lives in :class:`JobManager` threads, so after any restart:

    * ``running`` jobs/sources have no worker and can never finish — mark
      them ``failed`` with a ``suspended_by_restart`` error row. Their quota
      slots release as a consequence (the concurrency cap counts
      queued+running only).
    * ``queued`` jobs never started — re-submit their workers so they run.
    * ``paused`` jobs are user intent — leave them; ``POST /resume`` restarts
      them on demand.

    Returns ``{"failed": [...], "resumed": [...]}`` job ids. Never raises:
    callers run this before serving traffic, and a sweep failure must not
    block boot (it is retried on the next boot).
    """
    failed: list[str] = []
    resumed: list[str] = []
    try:
        with SessionLocal() as db:
            orphaned = db.scalars(
                select(ScrapeJob).where(ScrapeJob.status == "running")
            ).all()
            for job in orphaned:
                job.status = "failed"
                job.errors_count += 1
                job.completed_at = _now()
                job.updated_at = _now()
                db.add(
                    ScrapeError(
                        job_id=job.id,
                        source_url="",
                        code=RESTART_SUSPENDED_CODE,
                        message=RESTART_SUSPENDED_MESSAGE,
                    )
                )
                failed.append(job.id)
                db.execute(
                    ScrapeSource.__table__.update()
                    .where(
                        ScrapeSource.job_id == job.id,
                        ScrapeSource.status == "running",
                    )
                    .values(status="failed")
                )
                # No worker exists for these rows anymore: drop any lingering
                # Redis job-state keys so a later replica never reads stale
                # "running" state for a job that can not progress (§8b).
                job_state.delete_state(job.id)
            queued = db.scalars(
                select(ScrapeJob.id).where(ScrapeJob.status == "queued")
            ).all()
            resumed.extend(str(job_id) for job_id in queued)
            db.commit()
    except Exception:  # noqa: BLE001 - sweep must never block boot
        logger.exception("Startup sweep failed; will retry on next boot")
        return {"failed": [], "resumed": []}

    manager = JobManager.get()
    for job_id in resumed:
        try:
            manager.submit(job_id, run_scrape_job)
        except Exception:  # noqa: BLE001 - one bad job must not stop the sweep
            logger.exception("Startup sweep could not resume job %s", job_id)
    if failed or resumed:
        logger.info(
            "Startup sweep: %d orphaned job(s) failed, %d queued job(s) resumed",
            len(failed),
            len(resumed),
        )
    return {"failed": failed, "resumed": resumed}


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------


def _dedup_key(raw: dict) -> str:
    """Storage-level dedup identity: post_id, or a fallback fingerprint.

    Fingerprint covers stable public fields (URL, page, date, text head) via
    SHA-256 — per spec §7. Missing post_id fields are normalized to None uses.
    """
    post_id = raw.get("post_id")
    if post_id:
        return str(post_id)
    payload = json.dumps(
        {
            "post_url": raw.get("post_url") or raw.get("facebook_url"),
            "page_id": raw.get("page_id"),
            "published_at": raw.get("published_at"),
            "text": (raw.get("text") or "").strip()[:500],
        },
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return f"fp:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_dt(value: Any) -> datetime | None:
    """Parse the canonical ``published_at`` (ISO string) into a datetime."""
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _build_rows(job_id: str, source_id: int, dedup_key: str, raw: dict):
    """Build (Post, EngagementMetric|None, [Media]) from a canonical dict."""
    post = Post(
        job_id=job_id,
        source_id=source_id,
        post_id=raw.get("post_id") or None,
        dedup_key=dedup_key,
        facebook_url=raw.get("facebook_url"),
        post_url=raw.get("post_url"),
        page_name=raw.get("page_name"),
        page_id=raw.get("page_id"),
        profile_url=raw.get("profile_url"),
        post_type=raw.get("post_type") or None,
        published_at=_parse_dt(raw.get("published_at")),
        timestamp=_int_or_none(raw.get("timestamp")),
        text=raw.get("text"),
        caption=raw.get("caption"),
        hashtags=raw.get("hashtags") or None,
        mentions=raw.get("mentions") or None,
        external_links=raw.get("external_links") or None,
        media_type=raw.get("media_type"),
        thumbnail_url=raw.get("thumbnail_url"),
        media_url=raw.get("media_url"),
        video_url=raw.get("video_url"),
        transcript=raw.get("transcript"),
        transcript_language=raw.get("transcript_language"),
    )

    engagement_values = {
        "likes": _int_or_none(raw.get("likes")),
        "reactions": _int_or_none(raw.get("reactions")),
        "comments_count": _int_or_none(raw.get("comments_count")),
        "shares": _int_or_none(raw.get("shares")),
        "views_count": _int_or_none(raw.get("views_count")),
        "reaction_like_count": _int_or_none(raw.get("reaction_like_count")),
        "reaction_love_count": _int_or_none(raw.get("reaction_love_count")),
        "reaction_care_count": _int_or_none(raw.get("reaction_care_count")),
        "reaction_haha_count": _int_or_none(raw.get("reaction_haha_count")),
        "reaction_wow_count": _int_or_none(raw.get("reaction_wow_count")),
        "reaction_sad_count": _int_or_none(raw.get("reaction_sad_count")),
        "reaction_angry_count": _int_or_none(raw.get("reaction_angry_count")),
    }
    metric = (
        EngagementMetric(**engagement_values)
        if any(v is not None for v in engagement_values.values())
        else None
    )

    media_rows: list[Media] = []
    primary = Media(
        media_type=raw.get("media_type"),
        url=raw.get("media_url"),
        thumbnail_url=raw.get("thumbnail_url"),
        video_url=raw.get("video_url"),
    )
    if any(
        (primary.media_type, primary.url, primary.thumbnail_url, primary.video_url)
    ):
        media_rows.append(primary)
    for extra in raw.get("media") or []:
        if isinstance(extra, dict):
            media_rows.append(
                Media(
                    media_type=extra.get("media_type"),
                    url=extra.get("media_url") or extra.get("url"),
                    thumbnail_url=extra.get("thumbnail_url"),
                    video_url=extra.get("video_url"),
                )
            )

    return post, metric, media_rows


def _store_posts(db, job_id: str, source_id: int, posts: list[dict]) -> tuple[int, int]:
    """Insert posts for one source; returns ``(stored, duplicates)``.

    The scraper already dedups within its result; the per-source unique
    constraint acts as a second line of defence. On an unexpected uniqueness
    violation the batch falls back to savepoint-per-post insertion so one bad
    row never poisons the rest.
    """
    if not posts:
        return 0, 0

    existing = set(
        db.scalars(select(Post.dedup_key).where(Post.source_id == source_id))
    )
    entries: list[tuple[str, dict]] = []
    for raw in posts:
        key = _dedup_key(raw)
        if key in existing:
            continue
        entries.append((key, raw))

    duplicates = len(posts) - len(entries)
    if not entries:
        return 0, duplicates

    built = [_build_rows(job_id, source_id, key, raw) for key, raw in entries]

    try:
        # Phase 1: posts (id assigned on flush).
        for post, _metric, _media_rows in built:
            db.add(post)
        db.flush()
        # Phase 2: media + engagement rows (need post.id).
        for post, metric, media_rows in built:
            for media_row in media_rows:
                media_row.post_id = post.id
                db.add(media_row)
            if metric is not None:
                metric.post_id = post.id
                db.add(metric)
        db.flush()
        return len(built), duplicates
    except IntegrityError:
        db.rollback()
        logger.warning(
            "Bulk insert hit a uniqueness violation (job %s, source %s); "
            "falling back to savepoint-per-post inserts",
            job_id,
            source_id,
        )
        return _store_posts_savepoint(db, job_id, source_id, posts, existing)


def _store_posts_savepoint(
    db, job_id: str, source_id: int, posts: list[dict], existing: set[str]
) -> tuple[int, int]:
    stored = 0
    duplicates = 0
    for raw in posts:
        key = _dedup_key(raw)
        if key in existing:
            duplicates += 1
            continue
        try:
            with db.begin_nested():
                post, metric, media_rows = _build_rows(job_id, source_id, key, raw)
                db.add(post)
                db.flush()
                for media_row in media_rows:
                    media_row.post_id = post.id
                    db.add(media_row)
                if metric is not None:
                    metric.post_id = post.id
                    db.add(metric)
                db.flush()
            existing.add(key)
            stored += 1
        except IntegrityError:
            duplicates += 1
    return stored, duplicates


def _record_errors(
    db, job_id: str, source_id: int, source_url: str, errors: list
) -> None:
    """Persist per-post/source errors reported inside the scraper result."""
    for entry in errors or []:
        if not isinstance(entry, dict):
            continue
        db.add(
            ScrapeError(
                job_id=job_id,
                source_id=source_id,
                source_url=entry.get("url") or source_url,
                post_url=entry.get("post_url"),
                code=entry.get("code") or "scrape_error",
                message=entry.get("message") or "",
            )
        )


def _persist_source_failure(
    job_id: str, source_id: int, code: str, message: str
) -> None:
    with SessionLocal() as db:
        source = db.get(ScrapeSource, source_id)
        job = db.get(ScrapeJob, job_id)
        if source is None or job is None:
            return
        source.status = "failed"
        source.error_code = code
        source.error_message = message
        source.finished_at = _now()
        job.errors_count += 1
        job.updated_at = _now()
        db.add(
            ScrapeError(
                job_id=job_id,
                source_id=source_id,
                source_url=source.normalized_url,
                code=code,
                message=message,
            )
        )
        # Mark crawl state as failed
        cs = crawl_state_service.get_resume_state(
            db, source_id=source_id, job_id=job_id
        )
        if cs is not None:
            crawl_state_service.mark_failed(
                db, cs, error_code=code, error_message=message
            )
        db.commit()


def _mark_source_cancelled(job_id: str, source_id: int) -> None:
    with SessionLocal() as db:
        source = db.get(ScrapeSource, source_id)
        if source is None:
            return
        source.status = "cancelled"
        source.finished_at = _now()
        db.commit()


# ---------------------------------------------------------------------------
# Live progress reporting
# ---------------------------------------------------------------------------


def _make_progress_callback(job_id: str, source_id: int) -> Callable:
    """Build the lenient progress callback handed to ``scrape_source``.

    Accepted payloads (values = ABSOLUTE cumulative counters for the source):
        progress_cb(posts_found=N, posts_extracted=M, ...)   # kwargs
        progress_cb({"posts_found": N, ...})                 # dict
        progress_cb(a, b, c, d, e)  # positional (found, extracted, dups, skipped, failed)
    """

    def _on_progress(*args: Any, **kwargs: Any) -> None:
        counters: dict[str, Any] = {}
        if args and isinstance(args[0], dict):
            counters.update(args[0])
        elif kwargs:
            counters.update(kwargs)
        elif args:
            names = (
                "posts_found",
                "posts_extracted",
                "duplicates_removed",
                "posts_skipped",
                "posts_failed",
            )
            counters.update(
                {names[i]: args[i] for i in range(min(len(args), len(names)))}
            )

        mapped = {
            "posts_found": _int_or_none(
                counters.get("posts_found", counters.get("posts_discovered"))
            ),
            "posts_extracted": _int_or_none(
                counters.get("posts_extracted", counters.get("posts_processed"))
            ),
            "duplicates_removed": _int_or_none(
                counters.get("duplicates", counters.get("duplicates_removed"))
            ),
            "posts_skipped": _int_or_none(counters.get("posts_skipped")),
            "posts_failed": _int_or_none(counters.get("posts_failed")),
        }
        if all(v is None for v in mapped.values()):
            return  # unknown payload shape; ignore silently
        if _progress_throttled(job_id, mapped):
            _persist_progress(job_id, source_id, mapped)

    return _on_progress


def _progress_throttled(job_id: str, counters: dict[str, int | None]) -> bool:
    """True when this progress ping should be persisted (>=1s since last)."""
    key = tuple(int(counters.get(k) or 0) for k in counters)
    now = time.monotonic()
    with _PROGRESS_LOCK:
        last_ts, last_key = _PROGRESS_CACHE.get(job_id, (0.0, ()))
        if key == last_key:
            return False
        if last_key and now - last_ts < 1.0:
            return False
        _PROGRESS_CACHE[job_id] = (now, key)
    return True


def _persist_progress(job_id: str, source_id: int, counters: dict) -> None:
    """Write live source counters + recomputed job aggregates to the DB.

    Wrapped in try/except: progress reporting must never crash the worker.
    """
    try:
        with SessionLocal() as db:
            source = db.get(ScrapeSource, source_id)
            job = db.get(ScrapeJob, job_id)
            if source is None or job is None:
                return  # job deleted (probably by DELETE endpoint)

            if counters.get("posts_found") is not None:
                source.posts_discovered = int(counters["posts_found"])
            if counters.get("posts_extracted") is not None:
                source.posts_extracted = int(counters["posts_extracted"])
            if counters.get("duplicates_removed") is not None:
                source.duplicates_removed = int(counters["duplicates_removed"])
            if counters.get("posts_skipped") is not None:
                source.posts_skipped = int(counters["posts_skipped"])
            if counters.get("posts_failed") is not None:
                source.posts_failed = int(counters["posts_failed"])

            db.flush()  # push source changes so the SUM query sees them
            _refresh_source_derived_counters(db, job)
            job.updated_at = _now()
            db.commit()
    except Exception:  # noqa: BLE001 - never crash the worker over progress
        logger.debug("Progress persistence failed for job %s", job_id, exc_info=True)
        return
    # Redis mirror: same live counters, so any replica can render progress
    # without touching Postgres (finalplanv2 §8b).
    job_state.set_progress(job_id, counters)
