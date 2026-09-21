"""PostgreSQL pool guardrails.

The shared Supabase session-mode pooler caps out at 15 clients TOTAL across
every backend using the DSN. A single process must never be able to exhaust
that on its own — when it did, pool checkouts blocked uvicorn threads until
the whole API (including /api/health) stopped responding and every UI
surface sat on infinite loading spinners.
"""
from __future__ import annotations

import backend.core.database as dbmod

SESSION_URL = "postgresql+psycopg://user:pass@localhost:5432/postgres"
TRANSACTION_URL = "postgresql+psycopg://user:pass@localhost:6543/postgres"


def _settings_with_postgres(monkeypatch) -> None:
    class _Settings:
        supabase_db_url = SESSION_URL
        database_url = "sqlite:///./data/postharvest.db"

    monkeypatch.setattr(dbmod, "get_settings", lambda: _Settings())


def _settings_with_transaction_pooler(monkeypatch) -> None:
    class _Settings:
        supabase_db_url = TRANSACTION_URL
        database_url = "sqlite:///./data/postharvest.db"

    monkeypatch.setattr(dbmod, "get_settings", lambda: _Settings())


def test_postgres_pool_capped_below_shared_pooler_ceiling(monkeypatch):
    _settings_with_postgres(monkeypatch)
    engine = dbmod._build_engine()
    try:
        total = engine.pool._pool.maxsize + engine.pool._max_overflow
        assert total <= 10, f"pool can hold {total} connections against a 15-client pooler"
    finally:
        engine.dispose()


def test_postgres_pool_waits_are_all_bounded(monkeypatch):
    _settings_with_postgres(monkeypatch)
    kwargs = dbmod.postgres_pool_kwargs(SESSION_URL)
    assert kwargs["pool_timeout"] <= 10
    assert kwargs["pool_recycle"] > 0
    # TCP connects must fail fast too — otherwise a stalled pooler hangs
    # checkout threads forever and the API wedges despite pool_timeout.
    assert kwargs["connect_args"]["connect_timeout"] <= 5
    assert "statement_timeout" in kwargs["connect_args"].get("options", "")

    engine = dbmod._build_engine()
    try:
        assert engine.pool._timeout <= 10
        assert engine.pool._recycle > 0
    finally:
        engine.dispose()


def test_transaction_pooler_detection_is_port_based():
    assert dbmod.is_transaction_pooler(TRANSACTION_URL) is True
    assert dbmod.is_transaction_pooler(SESSION_URL) is False
    assert dbmod.is_transaction_pooler("sqlite:///./data/postharvest.db") is False
    assert dbmod.is_transaction_pooler("not-a-url") is False


def test_transaction_pooler_disables_prepared_statements(monkeypatch):
    """Supavisor transaction mode swaps backend connections between statements.

    Server-side prepared statements cannot survive that, so psycopg must be
    told not to prepare (``prepare_threshold=None``); the client pool is larger
    than the session-mode one but still capped below the pooler's own
    server-side pool so bursts queue locally instead of erroring.
    """
    _settings_with_transaction_pooler(monkeypatch)
    kwargs = dbmod.postgres_pool_kwargs(TRANSACTION_URL)
    assert kwargs["connect_args"]["prepare_threshold"] is None
    # Client pool must stay below Supavisor's server-side pool (~15) or bursts
    # trip its ECHECKOUTTIMEOUT instead of queueing locally.
    total = kwargs["pool_size"] + kwargs["max_overflow"]
    assert 5 < total <= 15, f"transaction pool can hold {total} concurrent txns"
    assert kwargs["pool_timeout"] <= 10
    assert kwargs["connect_args"]["connect_timeout"] <= 5

    engine = dbmod._build_engine()
    try:
        assert engine.pool._pool.maxsize == kwargs["pool_size"]
        assert engine.pool._timeout <= 10
    finally:
        engine.dispose()


def test_migrations_run_on_session_mode_not_the_transaction_pooler():
    migrated = dbmod.session_url_for_migrations(TRANSACTION_URL)
    assert migrated == "postgresql+psycopg://user:pass@localhost:5432/postgres"

    # Session/direct and sqlite URLs are passed through untouched.
    assert dbmod.session_url_for_migrations(SESSION_URL) == SESSION_URL
    assert dbmod.session_url_for_migrations("sqlite:///x.db") == "sqlite:///x.db"


# ---------------------------------------------------------------------------
# Role-aware sizing: the API and an arq worker share ONE Supavisor pool, so
# their client pools must SUM to the budget, not each claim it.
# ---------------------------------------------------------------------------


def _pooler_settings(monkeypatch, *, job_execution: str, process_role: str) -> None:
    class _Settings:
        supabase_db_url = TRANSACTION_URL
        database_url = "sqlite:///./data/postharvest.db"

    settings = _Settings()
    settings.job_execution = job_execution
    settings.process_role = process_role
    monkeypatch.setattr(dbmod, "get_settings", lambda: settings)


def test_inline_process_owns_the_whole_pooler_budget(monkeypatch):
    _pooler_settings(monkeypatch, job_execution="inline", process_role="api")
    pool_size, max_overflow = dbmod.transaction_pool_sizes()
    assert (pool_size, max_overflow) == dbmod._POOL_TRANSACTION_INLINE
    assert pool_size + max_overflow == dbmod._POOLER_BUDGET


def test_api_and_worker_pools_sum_to_the_budget(monkeypatch):
    _pooler_settings(monkeypatch, job_execution="arq", process_role="api")
    api_total = sum(dbmod.transaction_pool_sizes())

    _pooler_settings(monkeypatch, job_execution="arq", process_role="worker")
    worker_total = sum(dbmod.transaction_pool_sizes())

    assert api_total + worker_total == dbmod._POOLER_BUDGET
    assert api_total + worker_total <= dbmod._POOLER_SERVER_POOL


def test_arq_api_pool_is_smaller_than_the_inline_pool(monkeypatch):
    _pooler_settings(monkeypatch, job_execution="inline", process_role="api")
    inline_total = sum(dbmod.transaction_pool_sizes())

    _pooler_settings(monkeypatch, job_execution="arq", process_role="api")
    arq_api_total = sum(dbmod.transaction_pool_sizes())

    assert arq_api_total < inline_total


def test_no_role_can_exceed_the_pooler_server_pool(monkeypatch):
    worst = 0
    for job_execution, process_role in (
        ("inline", "api"),
        ("arq", "api"),
        ("arq", "worker"),
    ):
        _pooler_settings(monkeypatch, job_execution=job_execution, process_role=process_role)
        worst = max(worst, sum(dbmod.transaction_pool_sizes()))
    assert worst <= dbmod._POOLER_SERVER_POOL


def test_transaction_pool_kwargs_reflect_the_worker_role(monkeypatch):
    _pooler_settings(monkeypatch, job_execution="arq", process_role="worker")
    kwargs = dbmod.postgres_pool_kwargs(TRANSACTION_URL)
    assert (kwargs["pool_size"], kwargs["max_overflow"]) == dbmod._POOL_TRANSACTION_WORKER
    assert kwargs["connect_args"]["prepare_threshold"] is None


def test_arq_api_pool_exceeds_the_worker_pool(monkeypatch):
    """The API keeps the larger share: it serves the dashboard's polls.

    A scrape-time burst of authenticated dashboard requests exhausted the
    API's pool while the worker held its own; giving the API more headroom
    than the (single-scrape) worker is the rebalance that prevents it.
    """
    _pooler_settings(monkeypatch, job_execution="arq", process_role="api")
    api_total = sum(dbmod.transaction_pool_sizes())

    _pooler_settings(monkeypatch, job_execution="arq", process_role="worker")
    worker_total = sum(dbmod.transaction_pool_sizes())

    assert api_total > worker_total
