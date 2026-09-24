"""Background job runner.

A process-wide :class:`JobManager` owns a small :class:`ThreadPoolExecutor`
plus one :class:`CancelToken` per live job. The API layer submits job IDs; the
worker entry point (``backend.services.job_service.run_scrape_job``) executes
in a pool thread so ``POST /api/scrape`` returns immediately.

Job state is persisted in the database by the worker; this module is purely
the scheduler/cancellation plumbing.

Redis coordination (finalplanv2 §8(b)/§11 Phase 1)
---------------------------------------------------
Cancellation signals are mirrored to Redis (``job:{job_id}:cancel``) as well
as the in-process event, so a cancel issued from any replica/process is seen
by the worker that owns the job — the Phase 1 prerequisite for running more
than one backend replica. When Redis is unavailable the token degrades to the
plain in-process event (pre-Redis behaviour); it never blocks or raises.
"""
from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable

from backend.core import job_state
from backend.core.config import get_settings
from backend.core.logging import get_logger

logger = get_logger("core.job_manager")


class CancelToken:
    """Shared cancellation signal between the API layer and a running job."""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        self._event = threading.Event()
        self.future: Future | None = None

    @property
    def event(self) -> threading.Event:
        """The underlying threading.Event for passing to workers."""
        return self._event

    @property
    def cancelled(self) -> bool:
        """True once cancellation has been requested.

        Checks the in-process event first, then the Redis mirror so a cancel
        requested by another replica/process is honoured. Folding the Redis
        result into the event keeps any ``event.wait(...)`` caller unblocked
        too.
        """
        if self._event.is_set():
            return True
        if job_state.is_cancelled(self.job_id):
            self._event.set()
            return True
        return False

    def request_cancel(self) -> None:
        """Flip the cancellation flag (thread-safe) and mirror it to Redis."""
        self._event.set()
        job_state.request_cancel(self.job_id)


class JobManager:
    """Owns the executor and the registry of live cancel tokens."""

    _instance: "JobManager | None" = None
    _instance_lock = threading.Lock()

    def __init__(self, max_workers: int | None = None) -> None:
        settings = get_settings()
        self._max_workers = max_workers or settings.worker_threads
        self._executor = ThreadPoolExecutor(
            max_workers=self._max_workers,
            thread_name_prefix="scrape-worker",
        )
        self._tokens: dict[str, CancelToken] = {}
        self._lock = threading.Lock()

    @classmethod
    def get(cls) -> "JobManager":
        """Return the process-wide singleton."""
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def submit(self, job_id: str, runner: Callable[[str], None]) -> CancelToken:
        """Run ``runner(job_id)`` in the background; returns its cancel token."""
        token = CancelToken(job_id)
        with self._lock:
            self._tokens[job_id] = token
        future = self._executor.submit(runner, job_id)
        token.future = future
        future.add_done_callback(lambda _f: self._forget(job_id))
        return token

    def token(self, job_id: str) -> CancelToken | None:
        """Return the live token for a job, if any."""
        with self._lock:
            return self._tokens.get(job_id)

    def _forget(self, job_id: str) -> None:
        with self._lock:
            self._tokens.pop(job_id, None)

    def cancel(self, job_id: str, wait_seconds: float | None = None) -> bool:
        """Request cancellation and wait up to ``wait_seconds`` for the worker.

        The cancel signal is mirrored to Redis *before* the wait, so a worker
        owning the job in any process/replica observes it even when no local
        token exists here. Returns True when the worker stopped within the
        window (or was already done / unknown). Best-effort by design: the
        DELETE route proceeds with the row deletion afterwards regardless.
        """
        if wait_seconds is None:
            wait_seconds = get_settings().cancel_wait_seconds
        # Mirrored first — must land even if `token` is None below.
        job_state.request_cancel(job_id)
        token = self.token(job_id)
        if token is None:
            return True
        token.request_cancel()
        future = token.future
        if future is None:
            return True
        try:
            future.result(timeout=max(0.0, wait_seconds))
            return True
        except TimeoutError:
            logger.warning("Cancel timed out for job %s; proceeding best-effort", job_id)
            return False
        except Exception:  # noqa: BLE001 - worker already failed; treat as stopped
            return True

    def shutdown(self) -> None:
        """Used on application shutdown."""
        self._executor.shutdown(wait=False, cancel_futures=True)