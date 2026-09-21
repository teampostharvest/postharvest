"""PostgreSQL pool guardrails.

The shared Supabase session-mode pooler caps out at 15 clients TOTAL across
every backend using the DSN. A single process must never be able to exhaust
that on its own — when it did, pool checkouts blocked uvicorn threads until
the whole API (including /api/health) stopped responding and every UI
surface sat on infinite loading spinners.
"""
from __future__ import annotations

import backend.core.database as dbmod


def _settings_with_postgres(monkeypatch) -> None:
    class _Settings:
        supabase_db_url = "postgresql+psycopg://user:pass@localhost:5432/postgres"
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
    kwargs = dbmod.postgres_pool_kwargs()
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
