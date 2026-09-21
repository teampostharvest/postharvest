"""Shared pytest fixtures for the PostHarvest test suite.

ORDER MATTERS — read before editing:

1. ``sys.path`` is modified FIRST so ``backend`` is importable no matter what
   working directory pytest is launched from.
2. ``DATABASE_URL`` / ``EXPORT_BASE_DIR`` / ``DATA_DIR`` /
   ``CANCEL_WAIT_SECONDS`` are set as environment variables BEFORE any
   ``backend.*`` module is imported.  The SQLAlchemy engine
   (``backend.core.database``) and the pydantic-settings singleton
   (``backend.core.config.get_settings``) are built exactly once per process,
   at first import — pointing them at a throw-away temp directory keeps the
   suite hermetic (no writes into the repository, no ``data/`` pollution).
3. A SINGLE session-scoped :class:`fastapi.testclient.TestClient` is shared by
   every test.  A TestClient triggers the FastAPI lifespan (``init_db()`` on
   enter, ``JobManager.get().shutdown()`` on exit) every time it is used as a
   context manager, and a :class:`ThreadPoolExecutor` cannot be restarted
   after ``shutdown()`` — creating more than one TestClient per process would
   leave the job manager permanently unable to schedule workers.
4. Rows are wiped between tests by an autouse fixture (children before
   parents, FK-safe) so tests are isolated while sharing one file DB.

Networking rule: tests NEVER hit the real network.  Every test that starts a
background job installs a fake ``backend.scraper.scrape_source`` (see
``helpers.install_fake_scraper``) and waits for the job to reach a terminal
state before returning, so no worker thread can outlive the monkeypatch.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# 1. sys.path + hermetic environment (before ANY backend import)
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_SESSION_TMP = Path(tempfile.mkdtemp(prefix="fbscraper_tests_"))
_SESSION_TMP.mkdir(parents=True, exist_ok=True)

os.environ["DATABASE_URL"] = "sqlite:///" + (_SESSION_TMP / "test.db").as_posix()
# Neutralize production-ish settings from root `.env` (loaded by pydantic
# settings via config.py). The suite's assertions assume the code defaults:
# * SUPABASE_DB_URL   — database.py prefers it over DATABASE_URL; without
#   this the suite runs against the REAL Supabase instance (slow round-trips,
#   pool contention with production, flaky plan/job timeouts).
# * MAX_URLS_PER_JOB  — root `.env` sets 100, but plan-cap tests assume the
#   code default of 300 (enterprise sends 200 URLs, team ceiling is 150).
# * COOKIE_ENCRYPTION_KEY — enables at-rest Fernet encryption of personal
#   cookies; mirror tests assert the plaintext jar.
# CI is exempt only because it has no `.env` file.
os.environ["SUPABASE_DB_URL"] = ""
os.environ["MAX_URLS_PER_JOB"] = "300"
os.environ["COOKIE_ENCRYPTION_KEY"] = ""
os.environ["EXPORT_BASE_DIR"] = str(_SESSION_TMP / "exports")
os.environ["DATA_DIR"] = str(_SESSION_TMP / "data")
os.environ["CANCEL_WAIT_SECONDS"] = "2"  # keep DELETE/cancellation tests fast
# REDIS_URL — job-state mirrors degrade to DB-only when empty, which is
# exactly the pre-Redis behaviour the hermetic suite assumes (no network,
# no Redis daemon). The Redis-backed path is covered by dedicated fakeredis
# tests in tests/test_job_state_redis.py.
os.environ["REDIS_URL"] = ""

import pytest  # noqa: E402

# Imported here so the smoke-check below runs at collection time.
import backend.scraper  # noqa: E402, F401


def _check_environment() -> None:
    """Fail fast with a clear message when the suite is misconfigured."""
    from backend.core.config import get_settings

    s = get_settings()
    assert _SESSION_TMP.as_posix() in s.database_url, "DATABASE_URL did not take effect"
    assert not s.supabase_db_url, (
        "SUPABASE_DB_URL leaked from root .env into the test process; "
        "tests would hit the production database"
    )
    assert s.max_urls_per_job == 300, (
        f"MAX_URLS_PER_JOB leaked from root .env (got {s.max_urls_per_job}); "
        "plan-cap tests assume the 300 default"
    )
    assert not s.cookie_encryption_key, (
        "COOKIE_ENCRYPTION_KEY leaked from root .env; mirror tests assume "
        "plaintext cookie jars"
    )
    assert s.cancel_wait_seconds == 2.0, "CANCEL_WAIT_SECONDS did not take effect"
    assert not s.redis_url, (
        "REDIS_URL leaked from root .env into the test process; the hermetic "
        "suite must not open a real Redis connection. The Redis-backed job "
        "state path is covered by fakeredis tests instead."
    )


_check_environment()

# Ensure tables exist even when no test uses the `client` fixture (which
# triggers init_db() via the FastAPI lifespan).  Exporter unit tests, for
# example, never touch the TestClient, so without this the autouse cleanup
# fixture would try to DELETE from non-existent tables.
from backend.core.database import init_db  # noqa: E402
init_db()

# Deletion order: children before parents (SQLite FK pragma is ON).
_CLEANUP_MODELS = (
    "ExportJob",
    "ScrapeError",
    "SavedAccount",
    "CrawlState",
    "Media",
    "EngagementMetric",
    "Post",
    "ScrapeSource",
    "ScrapeJob",
    "User",
)


@pytest.fixture(scope="session")
def client():
    """One TestClient for the whole session (see module docstring, note 3)."""
    from backend.main import create_app
    from fastapi.testclient import TestClient

    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _patch_firebase_verify(monkeypatch):
    """Patch verify_id_token to accept test tokens without hitting Firebase network."""
    def mock_verify(token: str):
        if token in ("garbage", "invalid", "bad-token") or not token.startswith(("test_", "user_")):
            from backend.core.exceptions import AppError
            raise AppError("Invalid token", status_code=401, code="invalid_token")
        if token.startswith("user_b_token") or token == "test_firebase_token_b":
            return {
                "uid": "test_firebase_uid_user_b",
                "email": "user_b@example.com",
                "name": "User B",
            }
        return {
            "uid": "test_firebase_uid_user_a",
            "email": "user_a@example.com",
            "name": "User A",
        }

    monkeypatch.setattr("backend.auth.firebase.verify_id_token", mock_verify)
    monkeypatch.setattr("backend.auth.dependencies.verify_id_token", mock_verify)


@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer test_firebase_token_a"}


@pytest.fixture
def auth_headers_b():
    return {"Authorization": "Bearer user_b_token"}


@pytest.fixture
def authed_client(client, auth_headers):
    """TestClient wrapper that automatically includes Authorization header."""
    class AuthedClient:
        def __init__(self, inner):
            self.inner = inner

        def _merge_headers(self, headers):
            h = dict(auth_headers)
            if headers:
                h.update(headers)
            return h

        def get(self, url, **kwargs):
            kwargs["headers"] = self._merge_headers(kwargs.get("headers"))
            return self.inner.get(url, **kwargs)

        def post(self, url, **kwargs):
            kwargs["headers"] = self._merge_headers(kwargs.get("headers"))
            return self.inner.post(url, **kwargs)

        def delete(self, url, **kwargs):
            kwargs["headers"] = self._merge_headers(kwargs.get("headers"))
            return self.inner.delete(url, **kwargs)

        def put(self, url, **kwargs):
            kwargs["headers"] = self._merge_headers(kwargs.get("headers"))
            return self.inner.put(url, **kwargs)

        def patch(self, url, **kwargs):
            kwargs["headers"] = self._merge_headers(kwargs.get("headers"))
            return self.inner.patch(url, **kwargs)

    return AuthedClient(client)


@pytest.fixture(autouse=True)
def _clean_database():
    """Wipe every table between tests for deterministic isolation."""
    yield
    from sqlalchemy import delete

    from backend import models as m
    from backend.core.config import get_settings
    from backend.core.database import SessionLocal

    with SessionLocal() as db:
        for name in _CLEANUP_MODELS:
            db.execute(delete(getattr(m, name)))
        db.commit()

    # Cookie stores are filesystem state, not DB rows: SQLite reuses user ids
    # after a wipe, so stale data/personal/{id}/ dirs would leak personal
    # accounts (and the plan cap) into the next test. Clear them too.
    import shutil
    from pathlib import Path

    data_dir = Path(get_settings().data_dir)
    personal = data_dir / "personal"
    if personal.exists():
        shutil.rmtree(personal, ignore_errors=True)
    for pattern in ("fb_cookies*.json", "fb_credentials.json"):
        for p in data_dir.glob(pattern):
            p.unlink(missing_ok=True)