"""Database engine, session factory and declarative base.

Storage strategy
----------------
* SQLite by default (``sqlite:///./data/postharvest.db``) — the ``data/``
  directory is created automatically on startup.
* PostgreSQL is a runtime switch: set ``DATABASE_URL`` to a
  ``postgresql+psycopg://...`` DSN (install ``psycopg[binary]`` separately;
  see requirements.txt). No application code changes required.
* ``:memory:`` databases are given a static pool so background worker threads
  share one connection (useful for the test suite).
* For SQLite, ``PRAGMA foreign_keys=ON`` guarantees ON DELETE CASCADE works,
  and WAL mode allows reading while the worker thread writes.

Sessions
--------
Routes receive a request-scoped session via the ``get_db`` dependency; the
background job worker opens its own short-lived sessions (one per source and
per progress ping) so a long-scraping source never holds a transaction open.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.core.config import get_settings

logger = logging.getLogger("db")


class Base(DeclarativeBase):
    """Declarative base shared by all ORM models."""


def is_transaction_pooler(url: str) -> bool:
    """True when ``url`` points at Supabase's transaction-mode pooler.

    Supabase exposes its Supavisor pooler in two modes on the same host:
    session mode on 5432 and **transaction** mode on 6543. Port 6543 is the
    unambiguous marker — direct and session connections both use 5432.
    """
    try:
        return make_url(url).port == 6543
    except Exception:  # noqa: BLE001 - malformed URL falls back to session mode
        return False


def session_url_for_migrations(url: str) -> str:
    """Point migrations at a session connection when the app uses the pooler.

    The application DSN may target Supabase's transaction pooler (port 6543),
    but migrations are DDL + multi-statement transactions that need a stable
    backend session — running them through transaction pooling is unsafe.
    Swap to the session pooler (5432) for the migration run only.
    """
    try:
        parsed = make_url(url)
    except Exception:  # noqa: BLE001 - leave the DSN untouched if unparsable
        return url
    if parsed.port == 6543:
        return parsed.set(port=5432).render_as_string(hide_password=False)
    return url


# Supavisor's server-side pool (``default_pool_size`` on Supabase's shared
# pooler) is the real concurrency ceiling for the transaction pooler. Every
# process using the DSN draws from the SAME server connections, so the SUM of
# all client pools must stay below it — past that, bursts do not queue
# client-side, they fail with ``ECHECKOUTTIMEOUT: unable to check out
# connection`` (measured: 20 concurrent queries -> 4 hard errors).
_POOLER_SERVER_POOL = 15
# Left for the host CLI and the one-off Alembic migration run.
_POOLER_RESERVED = 2
_POOLER_BUDGET = _POOLER_SERVER_POOL - _POOLER_RESERVED  # 13

# Client-pool splits of that budget, as (pool_size, max_overflow).
_POOL_TRANSACTION_INLINE = (9, 4)  # one process owns the whole budget
# The API keeps the larger share: it serves the dashboard's concurrent polls
# (usage + active jobs + accounts) and every request used to need a connection
# just to resolve the caller. The worker runs one scrape at a time and needs
# only enough for its progress pings and post writes.
_POOL_TRANSACTION_API_WITH_WORKER = (8, 2)  # 10
_POOL_TRANSACTION_WORKER = (2, 1)  # 10 + 3 == 13, within budget


def transaction_pool_sizes() -> tuple[int, int]:
    """Client-pool size for this process under the shared pooler budget.

    With ``inline`` execution one process (the API) uses the whole budget.
    With an arq worker two processes share the pooler, so the API shrinks and
    the worker takes a small pool — their **sum** stays within budget, which is
    what keeps a burst from oversubscribing Supavisor's server pool.
    """
    settings = get_settings()
    role = getattr(settings, "process_role", "api")
    if role == "worker":
        return _POOL_TRANSACTION_WORKER
    if getattr(settings, "job_execution", "inline") == "arq":
        return _POOL_TRANSACTION_API_WITH_WORKER
    return _POOL_TRANSACTION_INLINE


def postgres_pool_kwargs(url: str) -> dict:
    """Connection-pool guardrails for PostgreSQL (Supabase pooler).

    Two pooler modes are supported, chosen from the DSN port:

    * **Transaction pooler (6543)** — Supavisor multiplexes many client
      connections onto a small number of server connections, so the 15-session
      ceiling that wedged the API on the session pooler no longer applies. The
      client pool is sized from the process's role (see
      :func:`transaction_pool_sizes`) so that when an arq worker shares the
      pooler the **sum** of both client pools still fits its server pool.
      Server-side prepared statements are unusable, though: the backend
      connection can change between statements, so ``prepare_threshold=None``
      disables them (psycopg3). This is the plan's §7 "PgBouncer in transaction
      mode" prerequisite, satisfied by Supabase's managed pooler instead of a
      self-hosted PgBouncer.

    * **Session/direct (5432)** — the original conservative pool. The shared
      session pooler caps at 15 clients TOTAL across every backend, so this
      stays small (3 pooled + 2 overflow = 5) and every wait is bounded: pool
      checkout fails after 10 s, TCP connects after 5 s. Without both bounds a
      saturated pooler wedged the whole API — uvicorn threads piled up behind
      unbounded checkouts until even /api/health stopped responding.
    """
    # Bound the wait we actually control: ``connect_timeout`` is client-side
    # and enforced. The ``statement_timeout`` startup option is best-effort —
    # Supavisor ignores it (both pooler modes report the server's 2 min
    # default), so a wedged statement is not killed by this. It is harmless to
    # pass and takes effect on a direct (non-pooler) connection.
    connect_args = {
        "connect_timeout": 5,
        "options": "-c statement_timeout=15000",
    }

    if is_transaction_pooler(url):
        connect_args["prepare_threshold"] = None
        pool_size, max_overflow = transaction_pool_sizes()
        # The client pool must stay BELOW Supavisor's server-side pool; keeping
        # the pooler's total budget intact across every process sharing the DSN
        # turns any burst beyond it into a bounded local wait instead of an
        # ``ECHECKOUTTIMEOUT`` error.
        return {
            "pool_size": pool_size,
            "max_overflow": max_overflow,
            "pool_timeout": 10,
            # Supavisor keeps the client connection alive; recycle rarely.
            "pool_recycle": 1800,
            "pool_use_lifo": True,
            "connect_args": connect_args,
        }

    return {
        "pool_size": 3,
        "max_overflow": 2,
        "pool_timeout": 10,
        "pool_recycle": 300,
        "connect_args": connect_args,
    }


def _build_engine() -> Engine:
    settings = get_settings()
    url = (settings.supabase_db_url or settings.database_url).strip()
    kwargs: dict = {"pool_pre_ping": True}

    if url.startswith("sqlite"):
        # check_same_thread=False: FastAPI's threadpool and the background
        # worker threads both open sessions.
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        if ":memory:" in url:
            # One shared connection across all threads so in-memory databases
            # behave consistently in the test suite.
            from sqlalchemy.pool import StaticPool

            kwargs["poolclass"] = StaticPool
        elif url.startswith("sqlite:///"):
            raw_path = url[len("sqlite:///") :]
            if raw_path:
                Path(raw_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
    elif url.startswith("postgresql"):
        kwargs.update(postgres_pool_kwargs(url))
    elif url.startswith("mysql"):
        kwargs.update({"pool_size": 10, "max_overflow": 20})

    return create_engine(url, **kwargs)


engine = _build_engine()

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
    """Per-connection pragmas for SQLite (foreign keys + WAL + busy timeout)."""
    if engine.dialect.name != "sqlite":
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


def init_db() -> None:
    """Create all tables if they do not exist.

    Alembic owns the schema on deployed environments: the prod backend entry
    point runs ``alembic upgrade head`` before uvicorn boots
    (docker-compose.prod.yml).  ``create_all`` remains here as the idempotent
    dev/test convenience (it never alters existing tables — new columns on a
    worked database MUST ship as regular Alembic migrations, not schema
    additions here).  Importing ``backend.models`` registers every model on
    ``Base.metadata``.  ``_migrate_additive_columns`` is the legacy shim that
    reconciled pre-Alembic dev databases; new columns go through Alembic.
    """
    from backend import models  # noqa: F401  (side effect: register models)

    Base.metadata.create_all(bind=engine)
    _migrate_additive_columns()

    # 2026-09-17: one-time mirror import — seed the saved_accounts table from
    # existing on-disk cookie jars (no-op once it has rows). Best-effort: a
    # failure must never block boot.
    try:
        from backend.scraper.browser_scraper import import_cookie_files_to_db

        import_cookie_files_to_db()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("saved_accounts import at boot failed: %s", exc)


def _migrate_additive_columns() -> None:
    """Additive, idempotent schema upgrades for shipped tables.

    ``create_all`` never alters existing tables, so columns introduced after
    a table first shipped on a worked database would silently be absent.
    Inspect each table and ``ALTER TABLE ... ADD COLUMN`` only what is missing.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    _dialect = engine.dialect.name

    # 2026-09-15: API supplies ETA — jobs carry a nullable started_at.
    existing_columns = {
        col["name"] for col in inspector.get_columns("scrape_jobs")
    }
    if "started_at" not in existing_columns:
        # SQLite has no ALTER with IF NOT EXISTS; PostgreSQL accepts plain
        # ADD COLUMN. Both are idempotent behind this existence check.
        with engine.begin() as conn:
            conn.execute(
                text(
                    "ALTER TABLE scrape_jobs "
                    "ADD COLUMN started_at TIMESTAMP NULL"
                )
            )

    # 2026-09-17: user roles (ops/user) for accounts + admin tiers.
    user_columns = {col["name"] for col in inspector.get_columns("users")}
    if "role" not in user_columns:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "ALTER TABLE users "
                    "ADD COLUMN role VARCHAR(32) NOT NULL DEFAULT 'user'"
                )
            )
    # The tier rollout renamed the effective default plan "free" -> "basic".
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE users SET plan = 'basic' WHERE plan = 'free' OR plan IS NULL")
        )


def get_db():
    """FastAPI dependency yielding a request-scoped session."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def db_health_check() -> dict:
    """Run a lightweight health check against the database.

    Returns a dict with ``status`` ("ok" | "error"), ``latency_ms``,
    ``pool_status``, and ``error`` if applicable.
    """
    import time as _time
    from sqlalchemy import text

    start = _time.monotonic()
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        latency = (_time.monotonic() - start) * 1000
        pool = engine.pool
        return {
            "status": "ok",
            "latency_ms": round(latency, 2),
            "pool_status": {
                "size": pool.size(),
                "checked_in": pool.checkedin(),
                "checked_out": pool.checkedout(),
                "overflow": pool.overflow(),
            },
        }
    except Exception as exc:
        latency = (_time.monotonic() - start) * 1000
        return {
            "status": "error",
            "latency_ms": round(latency, 2),
            "error": str(exc),
        }


@contextmanager
def get_session_context() -> Session:
    """Context manager for a standalone session (non-FastAPI usage).

    Usage::

        with get_session_context() as session:
            session.query(Post).all()
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()