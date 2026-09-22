"""Application configuration loaded from environment variables.

Every field can be overridden with an environment variable of the same name
(case-insensitive). Examples:

    DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/postharvest
    CORS_ORIGINS='["http://localhost:3000","http://127.0.0.1:3000"]'   # JSON list
    DEBUG=true
    WORKER_THREADS=8
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the application."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- application ----------------------------------------------------------
    app_name: str = "PostHarvest API"
    version: str = "1.0.0"
    debug: bool = False
    api_prefix: str = "/api"

    # --- storage --------------------------------------------------------------
    # Defaults to a local SQLite file created under ./data.
    # For PostgreSQL, switch DATABASE_URL to a connection string such as
    # postgresql+psycopg://user:pass@host:5432/dbname (requires the
    # psycopg / psycopg[binary] package — see requirements.txt comments).
    # No code changes are needed: the engine is built from this value and
    # all models use portable SQLAlchemy types / JSON columns.
    database_url: str = "sqlite:///./data/postharvest.db"
    data_dir: str = "./data"
    export_base_dir: str = "./data/exports"

    # --- supabase --------------------------------------------------------------
    supabase_url: str | None = None
    supabase_db_url: str | None = None
    supabase_anon_key: str | None = None
    supabase_service_key: str | None = None

    # --- firebase authentication ------------------------------------------------
    firebase_project_id: str | None = "postharvest-firebase"
    firebase_client_email: str | None = None
    firebase_private_key: str | None = None
    firebase_credentials_path: str | None = None

    # --- accounts / personal cookies -------------------------------------------
    # Emails (comma-separated) auto-promoted to the "ops" role on first login.
    # NoDecode stops pydantic-settings from JSON-decoding the env value first;
    # the field validator below then accepts both JSON arrays and plain
    # comma-separated strings.
    ops_emails: Annotated[list[str], NoDecode] = []
    # Fernet key used to encrypt per-user cookie files at rest. When unset,
    # personal cookies are stored in plain JSON (dev only; set this in prod).
    cookie_encryption_key: str | None = None
    # Max seconds a server-side personal-cookie Facebook login may take.
    personal_login_timeout_seconds: float = 90.0
    # --- session capture (live browser login) ----------------------------------
    # The capture browser binds a Chromium remote-debugging (CDP) endpoint on
    # loopback inside the backend container. Nothing is published to the host:
    # the API proxies the DevTools frontend and bridges its websocket, so the
    # whole flow stays on the app's own origin (works from any device).
    session_capture_port: int = 9333
    # Max seconds to wait for the user to finish logging in before cleanup.
    session_capture_timeout_seconds: float = 240.0

    @field_validator("ops_emails", mode="before")
    @classmethod
    def _parse_ops_emails(cls, value):  # noqa: ANN001
        """Accept either a JSON array or a plain comma-separated string.

        pydantic-settings would otherwise demand JSON for list fields, which
        makes ``OPS_EMAILS=a@x.com,b@x.com`` crash the app at boot.
        """
        if isinstance(value, str):
            value = value.strip()
            if value.startswith("["):
                try:
                    parsed = json.loads(value)
                    return parsed if isinstance(parsed, list) else []
                except json.JSONDecodeError:
                    return []
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    # --- worker / job manager ---------------------------------------------------
    worker_threads: int = 4
    # Global hard cap per job, applied on top of per-plan URL limits. Must sit
    # above the highest plan ceiling (Team = 150) so plan numbers are reachable.
    max_urls_per_job: int = 300
    default_max_posts: int | None = None
    default_post_type: str = "all"
    # How long DELETE /api/jobs/{id} waits for the background worker to stop
    # before deleting the rows (best-effort cancellation).
    cancel_wait_seconds: float = 5.0

    # --- HTTP -------------------------------------------------------------------
    # Comma-free JSON array; e.g. CORS_ORIGINS='["http://localhost:3000"]'
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

    # --- pagination --------------------------------------------------------------
    page_size_default: int = 50
    page_size_max: int = 200

    # --- rate limiting / proxy (Phase 14-16) ------------------------------------
    scraper_delay_seconds: float = 2.5
    scraper_timeout_seconds: float = 20.0
    scraper_max_retries: int = 3
    scraper_robots: bool = True

    # --- node fetch seam (finalplanv2 §4/§14) ---------------------------
    # When ON, HTTP-mode page fetches are delegated to the node service
    # (the only service allowed to talk to Facebook). Feature-flagged so the
    # legacy Python Fetcher stays the default until parity is proven; node
    # owns robots.txt, the shared Redis token bucket and retries, while FastAPI
    # keeps orchestrating variants / parsing / job bookkeeping.
    use_node: bool = False
    # Browser-mode seam (finalplanv2 §4 browser-mode / §12): when ON,
    # browser-mode scrapes run through the node service's Playwright
    # capture (/fetch mode="browser") instead of the Python
    # browser_scraper. Independent of use_node — both must be verified
    # against the legacy path before any cutover (§14 parity gates).
    use_node_browser: bool = False
    # Base URL of the node service. Defaults to the host-dev location;
    # Docker Compose overrides it to http://node:9334 on the stack
    # network (see docker/docker-compose.yml backend environment).
    node_base_url: str = "http://127.0.0.1:9334"

    # --- proxy support (optional) -----------------------------------------------
    proxy_enabled: bool = False
    proxy_url: str | None = None
    proxy_urls: list[str] = []

    # --- logging ----------------------------------------------------------------
    log_level: str = "INFO"
    log_format: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    def ensure_dirs(self) -> None:
        """Create runtime directories (data/, exports/)."""
        Path(self.data_dir).mkdir(parents=True, exist_ok=True)
        Path(self.export_base_dir).mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton (cached)."""
    return Settings()