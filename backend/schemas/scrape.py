"""Request/response models for ``POST /api/scrape``.

Validation notes
----------------
* ``urls`` — 1..``max_urls_per_job`` entries; empty entries are stripped and
  rejected. Semantic validation (is this really a Facebook URL?) happens in
  the service layer via the scraper's ``validate_facebook_url`` so invalid
  URLs land in ``error_details`` instead of failing the whole request — unless
  *no* URL is valid, which returns 400.
* ``start_date`` / ``end_date`` — ISO ``YYYY-MM-DD`` strings (mirroring what
  the frontend date inputs send); passed through unchanged to the scraper.
* ``post_type`` — one of ``text | image | video | link | all`` (case-insensitive).
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from backend.core.config import get_settings

ALLOWED_POST_TYPES = ("text", "image", "video", "link", "all")

# Keep the request-level bound in lockstep with the global service cap
# (MAX_URLS_PER_JOB) so plan ceilings like Team's 150 are actually reachable.
_URLS_MAX = get_settings().max_urls_per_job


class ScrapeRequest(BaseModel):
    urls: list[str] = Field(
        ...,
        min_length=1,
        max_length=_URLS_MAX,
        description="Facebook page/profile URLs (one or more)",
    )
    max_posts: int | None = Field(
        default=None, ge=1, le=100_000, description="Maximum posts per source"
    )
    start_date: str | None = Field(
        default=None, description="ISO date (YYYY-MM-DD); inclusive lower bound"
    )
    end_date: str | None = Field(
        default=None, description="ISO date (YYYY-MM-DD); inclusive upper bound"
    )
    post_type: str | None = Field(
        default=None, description="text | image | video | link | all"
    )
    use_browser: bool = Field(
        default=False,
        description=(
            "Use the Playwright browser scraper (captures Facebook's own "
            "Comet /api/graphql/ feed; far more posts than the anonymous "
            "HTML path, which is capped at ~5 by Facebook itself)."
        ),
    )
    account: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "Saved account name (see 'python cli.py login --account NAME'); "
            "its cookies unlock the full logged-in feed. Leave empty to run "
            "without cookies."
        ),
    )
    scrolls: int | None = Field(
        default=None, ge=1, le=300, description="Browser scroll rounds (default 40)"
    )

    @field_validator("urls")
    @classmethod
    def _strip_and_check_urls(cls, value: list[str]) -> list[str]:
        cleaned = [u.strip() for u in value]
        if not any(cleaned):
            raise ValueError("at least one non-empty URL is required")
        return cleaned

    @field_validator("start_date", "end_date")
    @classmethod
    def _validate_iso_date(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("date must use the ISO format YYYY-MM-DD") from exc
        return value

    @field_validator("post_type")
    @classmethod
    def _validate_post_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        lowered = value.strip().lower()
        if lowered not in ALLOWED_POST_TYPES:
            raise ValueError(
                f"post_type must be one of: {', '.join(ALLOWED_POST_TYPES)}"
            )
        return lowered

    @field_validator("end_date")
    @classmethod
    def _validate_date_order(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        start = info.data.get("start_date")
        if start is not None:
            if date.fromisoformat(start) > date.fromisoformat(value):
                raise ValueError("start_date must not be after end_date")
        return value


class ScrapeOptionsOut(BaseModel):
    """Snapshot of the scrape options attached to a job."""

    urls: list[str]
    max_posts: int | None = None
    start_date: str | None = None
    end_date: str | None = None
    post_type: str | None = None
    use_browser: bool = False
    account: str | None = None
    scrolls: int | None = None


class ScrapeResponse(BaseModel):
    """201 response body for POST /api/scrape."""

    job_id: str
    status: Literal["queued"] = "queued"