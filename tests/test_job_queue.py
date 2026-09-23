"""Hermetic tests for the job-execution seam (``inline`` vs ``arq``).

No Redis and no worker process: the arq path is exercised against an injected
fake pool and a monkeypatched Redis mirror, so the suite stays network-free.
The inline path is the default the rest of the suite depends on, so these
tests also pin that default.
"""
from __future__ import annotations

import asyncio
import threading

import pytest
from pydantic import ValidationError

from backend.core import job_queue as queue_mod
from backend.core.config import Settings, get_settings
from backend.core.job_manager import CancelToken, JobManager
from backend.core.job_queue import (
    ARQ_TASK_NAME,
    ArqJobQueue,
    InlineJobQueue,
    get_job_queue,
    reset_job_queue,
)


@pytest.fixture(autouse=True)
def _restore_queue_singleton():
    reset_job_queue(None)
    yield
    reset_job_queue(None)


class _FakeArqPool:
    """Minimal stand-in for ``arq.connections.ArqRedis`` (async surface only)."""

    def __init__(self) -> None:
        self.enqueued: list[tuple] = []
        self.closed = False

    async def enqueue_job(self, name, *args, **kwargs):  # noqa: ANN001, ANN201
        self.enqueued.append((name, args, kwargs))
        return object()

    async def aclose(self) -> None:
        self.closed = True


def _arq_queue(pool: _FakeArqPool, name: str = "arq:test") -> ArqJobQueue:
    async def factory():
        return pool

    return ArqJobQueue("redis://unused:6379/0", name, pool_factory=factory)


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def test_default_execution_backend_is_inline():
    assert isinstance(get_job_queue(), InlineJobQueue)


def test_get_job_queue_is_a_singleton():
    assert get_job_queue() is get_job_queue()


def test_arq_backend_is_selected_when_configured(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "job_execution", "arq")
    monkeypatch.setattr(settings, "redis_url", "redis://example:6379/0")
    monkeypatch.setattr(settings, "arq_queue_name", "arq:custom")

    created: dict = {}

    class _Recorder:
        def __init__(self, redis_url, queue_name):
            created["redis_url"] = redis_url
            created["queue_name"] = queue_name

    monkeypatch.setattr(queue_mod, "ArqJobQueue", _Recorder)

    queue = queue_mod._build_job_queue()
    assert isinstance(queue, _Recorder)
    assert created == {"redis_url": "redis://example:6379/0", "queue_name": "arq:custom"}


def test_arq_without_redis_url_falls_back_to_inline(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "job_execution", "arq")
    monkeypatch.setattr(settings, "redis_url", None)
    assert isinstance(queue_mod._build_job_queue(), InlineJobQueue)


# ---------------------------------------------------------------------------
# Inline implementation (the pre-arq behaviour must be untouched)
# ---------------------------------------------------------------------------


def test_inline_queue_delegates_submit_to_job_manager(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        JobManager,
        "submit",
        lambda self, job_id, runner: calls.append((job_id, runner)),
    )
    runner = lambda _job_id: None  # noqa: E731
    InlineJobQueue().submit("job-1", runner)
    assert calls == [("job-1", runner)]


def test_inline_queue_delegates_cancel_to_job_manager(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        JobManager,
        "cancel",
        lambda self, job_id, wait_seconds=None: calls.append((job_id, wait_seconds)) or True,
    )
    assert InlineJobQueue().cancel("job-1", wait_seconds=3.0) is True
    assert calls == [("job-1", 3.0)]


# ---------------------------------------------------------------------------
# arq implementation (fake pool, no Redis)
# ---------------------------------------------------------------------------


def test_arq_queue_enqueues_the_task_by_name():
    pool = _FakeArqPool()
    queue = _arq_queue(pool)
    try:
        queue.submit("job-1", runner=None)
        assert pool.enqueued == [(ARQ_TASK_NAME, ("job-1",), {})]
    finally:
        queue.shutdown()


def test_arq_queue_reuses_one_pool_across_submits():
    pool = _FakeArqPool()
    queue = _arq_queue(pool)
    try:
        queue.submit("job-1", runner=None)
        queue.submit("job-2", runner=None)
        assert [call[1][0] for call in pool.enqueued] == ["job-1", "job-2"]
    finally:
        queue.shutdown()


def test_arq_queue_cancel_writes_the_redis_mirror(monkeypatch):
    from backend.core import job_state

    cancelled: list = []
    monkeypatch.setattr(job_state, "request_cancel", lambda job_id: cancelled.append(job_id))

    queue = _arq_queue(_FakeArqPool())
    try:
        assert queue.cancel("job-1") is True
        assert cancelled == ["job-1"]
    finally:
        queue.shutdown()


def test_arq_queue_token_honours_a_remote_cancel(monkeypatch):
    from backend.core import job_state

    monkeypatch.setattr(job_state, "is_cancelled", lambda job_id: job_id == "job-1")

    queue = _arq_queue(_FakeArqPool())
    try:
        assert isinstance(queue.token("job-1"), CancelToken)
        assert queue.token("job-1").cancelled is True
        assert queue.token("job-2").cancelled is False
    finally:
        queue.shutdown()


def test_arq_queue_shutdown_closes_the_pool_and_is_idempotent():
    pool = _FakeArqPool()
    queue = _arq_queue(pool)
    queue.submit("job-1", runner=None)
    queue.shutdown()
    assert pool.closed is True
    queue.shutdown()  # second call must be a no-op, not an error


# ---------------------------------------------------------------------------
# Worker entry point + settings
# ---------------------------------------------------------------------------


def test_worker_task_runs_the_sync_job_off_the_event_loop(monkeypatch):
    import backend.services.job_service as job_service
    from backend import worker

    seen: dict = {}

    def fake_run(job_id: str) -> None:
        seen["job_id"] = job_id
        seen["thread"] = threading.current_thread().name

    monkeypatch.setattr(job_service, "run_scrape_job", fake_run)

    asyncio.run(worker.run_scrape_job({}, "job-42"))

    assert seen["job_id"] == "job-42"
    # asyncio.to_thread must move the blocking scraper off the loop's thread.
    assert seen["thread"] != threading.current_thread().name


def test_worker_settings_are_wired_to_config():
    from backend import worker

    settings = get_settings()
    assert [fn.__name__ for fn in worker.WorkerSettings.functions] == [ARQ_TASK_NAME]
    assert worker.WorkerSettings.queue_name == settings.arq_queue_name
    assert worker.WorkerSettings.max_jobs == settings.worker_threads
    assert worker.WorkerSettings.max_tries == 1
    # arq treats 0 as "expire immediately"; the ceiling must be positive.
    assert worker.WorkerSettings.job_timeout == settings.arq_job_timeout_seconds
    assert worker.WorkerSettings.job_timeout > 0


def test_job_execution_setting_is_normalized_and_validated():
    assert Settings(job_execution="ARQ").job_execution == "arq"
    assert Settings(job_execution=" Inline ").job_execution == "inline"
    with pytest.raises(ValidationError):
        Settings(job_execution="celery")


def test_process_role_setting_is_normalized_and_validated():
    assert Settings(process_role="WORKER").process_role == "worker"
    assert Settings(process_role="api").process_role == "api"
    with pytest.raises(ValidationError):
        Settings(process_role="scheduler")
