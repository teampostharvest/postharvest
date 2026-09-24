"""Job execution seam — in-process threads or an out-of-process arq worker.

finalplanv2 §8(b)/§11 Phase 1 moves job *state* into Redis (done in
``backend.core.job_state``). This module is the second half: it decides
**where a job runs**.

Two implementations satisfy one small interface, selected by
``JOB_EXECUTION`` (see :class:`~backend.core.config.Settings`):

``inline`` (default)
    :class:`InlineJobQueue` — the historical behaviour: a process-wide
    :class:`~backend.core.job_manager.JobManager` runs the job in a
    ``ThreadPoolExecutor`` thread. This is what the hermetic test suite uses
    (the fake scraper is monkeypatched in-process, so no real worker could
    ever observe it) and what single-process dev runs.

``arq``
    :class:`ArqJobQueue` — enqueue the job over Redis; a separate
    ``arq`` worker process (``backend/worker.py``) executes it. This is the
    prerequisite for running more than one backend replica: the API no longer
    owns execution, so it can restart or scale without stranding jobs.

Cancellation stays cooperative either way: :meth:`cancel` writes the Redis
cancel mirror (``job:{id}:cancel``) that every worker consults, so it works
across processes without needing a handle on the running thread.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import Future
import threading
from typing import Callable

from backend.core import job_state
from backend.core.config import get_settings
from backend.core.job_manager import CancelToken, JobManager
from backend.core.logging import get_logger

logger = get_logger("core.job_queue")

#: Name of the arq task registered in ``backend/worker.py``. arq dispatches by
#: function name, so the worker and this constant must agree.
ARQ_TASK_NAME = "run_scrape_job"

#: How long a blocking enqueue/cancel call may take before we give up. arq's
#: Redis calls are fast; this only guards a wedged connection.
_ARQ_CALL_TIMEOUT_SECONDS = 10.0


class JobQueue:
    """Dispatch + cancellation seam between the API and job execution."""

    def submit(self, job_id: str, runner: Callable[[str], None]) -> Future | None:
        """Dispatch ``job_id`` to be executed by ``runner``."""
        raise NotImplementedError

    def token(self, job_id: str) -> CancelToken | None:
        """Return the live cancel token for ``job_id`` if one is tracked."""
        raise NotImplementedError

    def cancel(self, job_id: str, wait_seconds: float | None = None) -> bool:
        """Request cancellation, waiting up to ``wait_seconds`` for the worker."""
        raise NotImplementedError

    def shutdown(self) -> None:
        """Release execution resources at application shutdown."""
        raise NotImplementedError


class InlineJobQueue(JobQueue):
    """In-process execution via :class:`JobManager` (default + test path).

    Deliberately a thin delegation: the ``JobManager``/``CancelToken``
    semantics (and every existing test) are unchanged — only the call site
    moves behind the seam.
    """

    def submit(self, job_id: str, runner: Callable[[str], None]):
        return JobManager.get().submit(job_id, runner)

    def token(self, job_id: str) -> CancelToken | None:
        return JobManager.get().token(job_id)

    def cancel(self, job_id: str, wait_seconds: float | None = None) -> bool:
        return JobManager.get().cancel(job_id, wait_seconds)

    def shutdown(self) -> None:
        JobManager.get().shutdown()


class ArqJobQueue(JobQueue):
    """Redis-backed execution: enqueue jobs for an out-of-process arq worker.

    A dedicated asyncio loop runs on a daemon thread so the **synchronous**
    API/worker code can drive arq's async Redis client through
    :func:`asyncio.run_coroutine_threadsafe` without touching the FastAPI
    event loop. The arq pool is created lazily on first use so importing the
    app (or test collection) never opens a connection.

    ``pool_factory`` is an injectable async callable returning the arq pool;
    tests pass one to exercise this class with no Redis at all.
    """

    def __init__(
        self,
        redis_url: str,
        queue_name: str = "arq:queue",
        *,
        pool_factory: Callable[[], "asyncio.Future"] | None = None,
    ) -> None:
        self._redis_url = redis_url
        self._queue_name = queue_name
        self._pool_factory = pool_factory
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, name="arq-enqueue", daemon=True
        )
        self._thread.start()
        self._pool = None
        self._pool_lock = threading.Lock()
        self._closed = False

    # -- internals -----------------------------------------------------------

    async def _create_pool(self):
        if self._pool_factory is not None:
            return await self._pool_factory()
        from arq import create_pool
        from arq.connections import RedisSettings

        return await create_pool(RedisSettings.from_dsn(self._redis_url))

    def _run(self, coro, timeout: float = _ARQ_CALL_TIMEOUT_SECONDS):
        """Run ``coro`` on the queue loop and block for its result."""
        if self._loop.is_closed():
            raise RuntimeError("arq job queue loop is already closed")
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=timeout)

    def _ensure_pool(self):
        with self._pool_lock:
            if self._pool is None:
                self._pool = self._run(self._create_pool())
            return self._pool

    # -- JobQueue ------------------------------------------------------------

    def submit(self, job_id: str, runner: Callable[[str], None]):
        """Enqueue ``job_id`` for the arq worker (``runner`` is ignored).

        ``runner`` exists so the call site is identical to the inline path;
        the worker binds the real entry point (``run_scrape_job``) itself. No
        explicit arq job id is set: it would make re-enqueueing a resumed job
        a silent no-op while its previous result is still cached, and our
        cancellation is cooperative (Redis), not arq-abort based.
        """
        pool = self._ensure_pool()
        self._run(pool.enqueue_job(ARQ_TASK_NAME, job_id))
        return None

    def token(self, job_id: str) -> CancelToken:
        """A detached token that observes the Redis cancel mirror.

        There is no in-process registry in arq mode, but
        :class:`CancelToken.cancelled` already folds in
        ``job_state.is_cancelled`` — so a token constructed on demand still
        honours a cancel from any process.
        """
        return CancelToken(job_id)

    def cancel(self, job_id: str, wait_seconds: float | None = None) -> bool:
        """Write the Redis cancel mirror; the owning worker stops cooperatively."""
        job_state.request_cancel(job_id)
        return True

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._pool is not None:
                self._run(self._pool.aclose(), timeout=5.0)
        except Exception:  # noqa: BLE001 - best-effort shutdown
            logger.debug("Failed to close arq pool", exc_info=True)
        finally:
            self._pool = None
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
                self._thread.join(timeout=5)
            finally:
                self._loop.close()


# ---------------------------------------------------------------------------
# Process-wide selection
# ---------------------------------------------------------------------------

_instance: JobQueue | None = None
_instance_lock = threading.Lock()


def _build_job_queue() -> JobQueue:
    """Pick the configured execution backend, falling back safely.

    ``JOB_EXECUTION=arq`` without ``REDIS_URL`` cannot work (there is no queue
    to enqueue to), so it degrades to inline with a warning rather than
    silently dropping jobs.
    """
    settings = get_settings()
    if settings.job_execution == "arq":
        if settings.redis_url:
            logger.info(
                "Job execution backend: arq (redis=%s, queue=%s)",
                settings.redis_url,
                settings.arq_queue_name,
            )
            return ArqJobQueue(settings.redis_url, settings.arq_queue_name)
        logger.warning(
            "JOB_EXECUTION=arq but REDIS_URL is unset; falling back to inline execution"
        )
    return InlineJobQueue()


def get_job_queue() -> JobQueue:
    """Return the process-wide job queue singleton (lazily constructed)."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = _build_job_queue()
    return _instance


def reset_job_queue(queue: JobQueue | None = None) -> None:
    """Swap the singleton (test seam) without shutting the previous one down.

    Shutting down here would be unsafe: the inline implementation wraps the
    shared ``JobManager`` whose executor every test relies on for the whole
    session. Callers that own an :class:`ArqJobQueue` should close it directly.
    """
    global _instance
    with _instance_lock:
        _instance = queue
