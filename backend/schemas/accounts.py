"""Request/response models for the saved-session accounts API."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AccountOut(BaseModel):
    """Metadata for one saved Facebook session (never cookie contents)."""

    name: str
    scope: Literal["ops", "me"]
    saved_at: str | None = None
    cookies_file: str | None = None
    status: Literal["VALID", "EXPIRED"] = "EXPIRED"


class AccountsResponse(BaseModel):
    """Saved sessions split by tier: ops pool + the caller's own sessions."""

    ops: list[AccountOut]
    mine: list[AccountOut]


class PersonalLoginRequest(BaseModel):
    """Credentials + label for a server-side personal Facebook login."""

    name: str = Field(..., min_length=1, max_length=64, description="Account label")
    email: str = Field(..., min_length=3, max_length=320, description="Facebook email")
    password: str = Field(..., min_length=1, description="Facebook password (never stored)")


class SessionCaptureRequest(BaseModel):
    """Start a live session capture (user logs into Facebook in a new tab)."""

    name: str = Field(..., min_length=1, max_length=64, description="Account label")
    scope: Literal["ops", "me"] = Field(
        "me",
        description="me = personal session; ops = shared operator pool (ops role only)",
    )


class CookiesTxtRequest(BaseModel):
    """Add a saved Facebook session by pasting an exported cookies.txt.

    The cookies.txt (Netscape HTTP Cookie File) content is parsed server-side
    into the same jar shape every other capture path produces; only the
    resulting cookies get stored, never the raw text.
    """

    name: str = Field(..., min_length=1, max_length=64, description="Account label")
    scope: Literal["ops", "me"] = Field(
        "me",
        description="me = personal session; ops = shared operator pool (ops role only)",
    )
    cookies_txt: str = Field(
        ...,
        min_length=1,
        max_length=1_000_000,
        description="Netscape HTTP Cookie File content (tab-separated rows)",
    )


class SessionCaptureOut(BaseModel):
    """Response with the pipe link the user opens to log into Facebook."""

    capture_id: str
    name: str
    scope: Literal["ops", "me"]
    url: str
    expires_at: str