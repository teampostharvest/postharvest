"""FastAPI application entry point.

Run with::

    uvicorn backend.main:app --reload          # development
    uvicorn backend.main:app --host 0.0.0.0 --port 8000   # production-ish

The application factory (:func:`create_app`) keeps the app importable for
tests::

    from backend.main import create_app
    app = create_app()
    client = TestClient(app)

Error contract: every failure renders as
``{"error": {"code": <str>, "message": <str>}}`` with the appropriate HTTP
status (400 validation/input, 404 unknown job, 409 job running, 500 internal,
503 dependency unavailable). Starlette HTTPExceptions (e.g. 404 for unknown
routes) are converted to the same envelope for consistency.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.api import accounts, admin, exports, health, jobs, scrape, usage
from backend.core import job_state
from backend.core.config import get_settings
from backend.core.database import init_db
from backend.core.exceptions import AppError
from backend.core.job_manager import JobManager
from backend.core.logging import get_logger, setup_logging

logger = get_logger("main")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Startup: logging config, runtime dirs, DB schema. Shutdown: executor."""
    settings = get_settings()
    setup_logging(logging.DEBUG if settings.debug else logging.INFO)
    settings.ensure_dirs()
    init_db()
    # Reconcile persisted job rows with the (empty) fresh worker pool:
    # orphaned `running` jobs fail with an audit row, `queued` jobs resume.
    # Best-effort — the sweep never raises, so boot cannot block on it.
    from backend.services.job_service import sweep_orphaned_jobs

    sweep_orphaned_jobs()
    # Log the URL the engine actually opened (database.py prefers
    # SUPABASE_DB_URL over DATABASE_URL) rather than the raw setting.
    from backend.core import database as _db

    effective_url = str(_db.engine.url).replace("******", "***")
    logger.info("Application started (db=%s)", effective_url)
    yield
    JobManager.get().shutdown()
    job_state.close()  # drop the Redis client (best-effort, degraded when unset)
    logger.info("Application shutdown complete")


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    _register_exception_handlers(app)

    from backend.api import auth, plans

    app.include_router(auth.router, prefix=settings.api_prefix)
    app.include_router(plans.router, prefix=settings.api_prefix)
    app.include_router(usage.router, prefix=settings.api_prefix)
    app.include_router(scrape.router, prefix=settings.api_prefix)
    app.include_router(jobs.router, prefix=settings.api_prefix)
    app.include_router(accounts.router, prefix=settings.api_prefix)
    app.include_router(admin.router, prefix=settings.api_prefix)
    app.include_router(exports.router, prefix=settings.api_prefix)
    app.include_router(health.router, prefix=settings.api_prefix)

    return app


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        location = ".".join(
            str(part)
            for part in first.get("loc", [])
            if part not in ("body", "query", "path")
        )
        message = first.get("msg", "Invalid request")
        full = f"{location}: {message}" if location else message
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "validation_error", "message": full}},
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(
        _request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": "http_error", "message": str(exc.detail)}},
        )

    @app.exception_handler(Exception)
    async def handle_unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "Internal server error",
                }
            },
        )


app = create_app()