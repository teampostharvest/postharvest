"""arq worker entry point for out-of-process scrape execution.

Run with::

    arq backend.worker.WorkerSettings

Only used when ``JOB_EXECUTION=arq``. The API enqueues job ids to Redis; this
worker consumes them and runs the existing **synchronous**
``backend.services.job_service.run_scrape_job`` inside a thread (the scraper
stack — httpx + Playwright — is sync, so it must not block the event loop).

Job state already lives in Redis + Postgres (§8b), so the API and the worker
share all of it: the API can restart or scale without stranding a running job,
and cancellation travels over the Redis cancel mirror.
"""
from __future__ import annotations

import asyncio
import logging

from arq.connections import RedisSettings

from backend.core.config import get_settings
from backend.core.database import init_db
from backend.core.logging import get_logger, setup_logging

logger = get_logger("worker")


async def run_scrape_job(ctx, job_id: str) -> None:  # noqa: ANN001 - arq ctx
    """arq task: run the sync scrape job off the event loop.

    The name (``run_scrape_job``) is the dispatch key the API enqueues
    (``backend.core.job_queue.ARQ_TASK_NAME``); do not rename one without the
    other.
    """
    # Imported lazily: importing the service pulls in the scraper package, and
    # a worker should fail at task time with a clear error, not at import.
    from backend.services.job_service import run_scrape_job as _run

    await asyncio.to_thread(_run, job_id)


async def startup(ctx) -> None:  # noqa: ANN001 - arq ctx
    """Boot the worker: logging, dirs, schema, restart reconciliation."""
    settings = get_settings()
    setup_logging(logging.DEBUG if settings.debug else logging.INFO)
    settings.ensure_dirs()
    init_db()
    # A worker owns execution, so it owns restart reconciliation too: fail
    # orphaned `running` rows from a previous crash and re-enqueue `queued`
    # ones (which now land back on this queue via the arq job queue).
    from backend.services.job_service import sweep_orphaned_jobs

    await asyncio.to_thread(sweep_orphaned_jobs)
    logger.info("arq worker ready (queue=%s)", settings.arq_queue_name)


async def shutdown(ctx) -> None:  # noqa: ANN001 - arq ctx
    """Drop the shared Redis client on the way out (best-effort)."""
    from backend.core import job_state

    job_state.close()
    logger.info("arq worker stopped")


def _redis_settings() -> RedisSettings:
    # The worker is only launched when Redis is configured; the fallback keeps
    # the module importable (and the settings object constructible) otherwise.
    return RedisSettings.from_dsn(
        get_settings().redis_url or "redis://localhost:6379/0"
    )


class WorkerSettings:
    """arq reads this class to configure the worker process."""

    functions = [run_scrape_job]
    redis_settings = _redis_settings()
    queue_name = get_settings().arq_queue_name
    on_startup = startup
    on_shutdown = shutdown
    # One job per configured worker thread, mirroring the inline pool size.
    max_jobs = get_settings().worker_threads
    # Jobs catch their own errors and finalize in the DB; a retry would
    # re-scrape a job that already made progress — exactly once, not twice.
    max_tries = 1
    # Deep scrapes legitimately run for hours (throttled page fetches); the
    # inline path has no such ceiling, so make it effectively unbounded but
    # explicit. NOTE: 0 would mean "time out immediately" in arq, never use it.
    job_timeout = get_settings().arq_job_timeout_seconds
    keep_result = 60
