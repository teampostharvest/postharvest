"""Shared builders/utilities for the test suite (no pytest fixtures here).

The canonical post dict built by :func:`canonical_post` mirrors the scraper
layer's ``backend.scraper.normalizer.NORMALIZED_KEYS`` contract exactly (33
keys; scalars ``None`` when absent, lists ``[]`` when empty) — same source of
truth the API/export layers consume.

Fake scraper results are REAL :class:`backend.scraper.SourceResult` instances
(``page_name``/``page_id``/``posts``/``stats``/``errors`` attributes), because
that is exactly what the real ``scrape_source`` returns (see
``backend/scraper/__init__.py``).  Using the real return type is what lets the
suite act as the first integration check of the API layer against the scraper
layer's actual contract.
"""
from __future__ import annotations

import json
import secrets
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from backend.scraper import SourceResult

#: The five per-source stat keys shared by the scraper/stats and the API.
STATS_KEYS = (
    "posts_discovered",
    "posts_extracted",
    "duplicates_removed",
    "posts_skipped",
    "posts_failed",
)

ZERO_STATS: dict[str, int] = {k: 0 for k in STATS_KEYS}

ENGAGEMENT_FIELDS = (
    "likes",
    "reactions",
    "comments_count",
    "shares",
    "views_count",
    "reaction_like_count",
    "reaction_love_count",
    "reaction_care_count",
    "reaction_haha_count",
    "reaction_wow_count",
    "reaction_sad_count",
    "reaction_angry_count",
)

PAGE_URL = "https://www.facebook.com/example"
PAGE_NAME = "Example Page"
PAGE_ID = "123456789"


# ---------------------------------------------------------------------------
# Canonical post fixtures
# ---------------------------------------------------------------------------


def canonical_post(
    post_id: str | None = "1001",
    *,
    post_type: str = "text",
    published_at: str = "2026-08-01T12:00:00+00:00",
    **overrides: Any,
) -> dict:
    """Build a fully-populated canonical normalized post dict.

    All 33 normalizer keys are present.  ``post_type`` picks sensible media
    defaults; pass explicit overrides to deviate.
    """
    ts = int(
        datetime.fromisoformat(published_at.replace("Z", "+00:00")).timestamp()
    )
    post: dict[str, Any] = {
        "post_id": post_id,
        "facebook_url": PAGE_URL,
        "post_url": f"https://www.facebook.com/example/posts/{post_id or 'x'}",
        "page_name": PAGE_NAME,
        "page_id": PAGE_ID,
        "profile_url": PAGE_URL,
        "post_type": post_type,
        "published_at": published_at,
        "timestamp": ts,
        "text": "Hello world #LaunchDay",
        "caption": None,
        "hashtags": ["#LaunchDay"],
        "mentions": ["@nasa"],
        "external_links": [],
        "likes": 12,
        "reactions": 14,
        "comments_count": 3,
        "shares": 1,
        "views_count": None,
        "reaction_like_count": 10,
        "reaction_love_count": 2,
        "reaction_care_count": 1,
        "reaction_haha_count": 1,
        "reaction_wow_count": 0,
        "reaction_sad_count": 0,
        "reaction_angry_count": 0,
        "media_type": None,
        "thumbnail_url": None,
        "media_url": None,
        "video_url": None,
        "transcript": None,
        "transcript_language": None,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }
    post.update(overrides)
    if post_type == "image" and not overrides.get("media_type"):
        post["media_type"] = "image"
        post["thumbnail_url"] = "https://scontent.example/img1.jpg"
        post["media_url"] = "https://scontent.example/img1_full.jpg"
    elif post_type == "video" and not overrides.get("media_type"):
        post["media_type"] = "video"
        post["thumbnail_url"] = "https://scontent.example/vid1_thumb.jpg"
        post["video_url"] = "https://video.example/vid1.mp4"
    elif post_type == "link" and not overrides.get("external_links"):
        post["external_links"] = ["https://news.example/article"]
    return post


def sample_posts(count: int = 3, **overrides: Any) -> list[dict]:
    """A deterministic set of canonical posts with distinct published_at."""
    _TYPES = ("text", "image", "video", "link")
    posts = []
    for i in range(count):
        day = max(1, 30 - i)
        posts.append(
            canonical_post(
                str(2000 + i),
                post_type=_TYPES[i % len(_TYPES)],
                published_at=f"2026-08-{day:02d}T09:00:00+00:00",
            )
        )
    if overrides:
        posts = [dict(p, **overrides) for p in posts]
    return posts


def post_with_text(text: str, **overrides: Any) -> dict:
    """Canonical post whose text exercises CSV/XLSX escaping paths."""
    return canonical_post(
        "9000",
        post_type="text",
        text=text,
        hashtags=["#a", "#b"],
        transcript=None,
        **overrides,
    )


# ---------------------------------------------------------------------------
# Fake scraper plumbing
# ---------------------------------------------------------------------------


def make_source_result(
    url: str,
    posts: list[dict] | None = None,
    *,
    page_name: str | None = PAGE_NAME,
    page_id: str | None = PAGE_ID,
    stats: dict[str, int] | None = None,
    errors: list[dict] | None = None,
) -> SourceResult:
    """Build the real SourceResult type the real scraper returns."""
    s = dict(stats or ZERO_STATS)
    return SourceResult(
        url=url,
        page_name=page_name,
        page_id=page_id,
        posts=list(posts or []),
        stats=s,
        errors=list(errors or []),
    )


def install_fake_scraper(
    monkeypatch: Any,
    *,
    results: list[SourceResult] | None = None,
    result_factory: Callable | None = None,
    progress_events: list[dict] | None = None,
    progress_gap: float = 0.0,
    gate: threading.Event | None = None,
    options_seen: list | None = None,
) -> list:
    """Replace ``backend.scraper.scrape_source`` (and only that symbol).

    The lazy-imported module attribute is patched, which is exactly the
    monkeypatch target the API layer documents.  ``validate_facebook_url`` is
    deliberately NOT patched — real validation is exercised.

    * ``results``       — SourceResult list, matched by ``url``.
    * ``result_factory``— ``fn(url, options, progress_cb, cancel_event) -> SourceResult``
    * ``progress_events`` — list of counter dicts sent via ``progress_cb(**ev)``
      (job_service's documented kwargs convention).  Events are sent
      ``progress_gap`` seconds apart so the ≥1s progress throttle persists
      each one.
    * ``gate``          — when provided the fake waits on it before returning
      (checks ``cancel_event`` while waiting so DELETE can unblock it).
    * ``options_seen``  — optional list to collect the ScrapeOptions objects.

    Returns the ``calls`` list: ``(url, options)`` per invocation.
    """
    calls: list = []

    def _fake_scrape_source(url, options=None, progress_cb=None, cancel_event=None,
                            idempotency_key=None):
        calls.append((url, options))
        if options_seen is not None:
            options_seen.append(options)
        for ev in progress_events or []:
            if progress_cb is not None:
                progress_cb(**dict(ev))
            if progress_gap:
                time.sleep(progress_gap)
        if gate is not None:
            while not gate.is_set():
                if cancel_event is not None and cancel_event.wait(0.05):
                    break
                time.sleep(0.05)
        if result_factory is not None:
            return result_factory(url, options, progress_cb, cancel_event)
        for r in results or []:
            if r.url == url:
                return r
        raise AssertionError(f"no fake result configured for url {url!r}")

    monkeypatch.setattr("backend.scraper.scrape_source", _fake_scrape_source)
    return calls


def blocking_scraper(cancel_event: threading.Event | None):
    """Result factory for the cancellation test: block until cancelled."""

    def _factory(url, options=None, progress_cb=None, ce=None):
        if ce is not None:
            ce.wait(timeout=30)
        return make_source_result(
            url,
            posts=[],
            stats=ZERO_STATS,
            errors=[{"url": url, "code": "cancelled", "message": "stopped by user"}],
        )

    return _factory


# ---------------------------------------------------------------------------
# Job polling / direct DB seeding
# ---------------------------------------------------------------------------


def wait_for_job(
    client: Any,
    job_id: str,
    timeout: float = 15.0,
    terminal: tuple[str, ...] = ("completed", "failed"),
) -> dict:
    """Poll GET /api/jobs/{id} until terminal; returns the final JSON body."""
    deadline = time.monotonic() + timeout
    last: dict | None = None
    while time.monotonic() < deadline:
        resp = client.get(f"/api/jobs/{job_id}")
        if resp.status_code == 200:
            last = resp.json()
            if last.get("status") in terminal:
                return last
        time.sleep(0.05)
    raise AssertionError(
        f"job {job_id} did not reach {terminal} within {timeout}s (last={last!r})"
    )


def insert_completed_job(
    posts: Iterable[dict],
    *,
    status: str = "completed",
    pages_total: int = 1,
    pages_completed: int = 1,
    options: dict | None = None,
    owner_id: int | None = None,
) -> str:
    """Seed a job + sources + posts directly through the models.

    Used by the export / pagination tests so they do not depend on the
    background worker.  Returns the job id.
    """
    from sqlalchemy.orm import Session

    from backend.core.database import SessionLocal
    from backend.models import EngagementMetric, Media, Post, ScrapeJob, ScrapeSource, User

    posts_list = list(posts)
    job_id = secrets.token_hex(8)
    with SessionLocal() as db:  # type: Session
        if owner_id is None:
            user = db.query(User).filter_by(firebase_uid="test_firebase_uid_user_a").first()
            if not user:
                user = User(
                    firebase_uid="test_firebase_uid_user_a",
                    email="user_a@example.com",
                    display_name="User A",
                )
                db.add(user)
                db.commit()
                db.refresh(user)
            owner_id = user.id

        job = ScrapeJob(
            id=job_id,
            owner_id=owner_id,
            status=status,
            pages_total=pages_total,
            pages_completed=pages_completed,
            posts_found=len(posts_list),
            posts_processed=len(posts_list) if status == "completed" else 0,
            started_at=_now_utc() if status == "completed" else None,
            options=options
            or {"urls": [PAGE_URL], "max_posts": None, "post_type": "text"},
        )
        db.add(job)
        src = ScrapeSource(
            job_id=job_id,
            url=PAGE_URL,
            normalized_url=PAGE_URL,
            status="completed" if status == "completed" else status,
            page_name=PAGE_NAME,
            page_id=PAGE_ID,
            posts_discovered=len(posts_list),
            posts_extracted=len(posts_list) if status == "completed" else 0,
        )
        db.add(src)
        db.flush()
        for i, p in enumerate(posts_list):
            post = Post(
                job_id=job_id,
                source_id=src.id,
                post_id=p.get("post_id"),
                dedup_key=f"test-{i}",
                facebook_url=p.get("facebook_url"),
                post_url=p.get("post_url"),
                page_name=p.get("page_name"),
                page_id=p.get("page_id"),
                profile_url=p.get("profile_url"),
                post_type=p.get("post_type"),
                published_at=_parse_iso(p.get("published_at")),
                timestamp=p.get("timestamp"),
                text=p.get("text"),
                caption=p.get("caption"),
                hashtags=p.get("hashtags"),
                mentions=p.get("mentions"),
                external_links=p.get("external_links"),
                media_type=p.get("media_type"),
                thumbnail_url=p.get("thumbnail_url"),
                media_url=p.get("media_url"),
                video_url=p.get("video_url"),
                transcript=p.get("transcript"),
                transcript_language=p.get("transcript_language"),
            )
            db.add(post)
            db.flush()
            eng = {k: p.get(k) for k in ENGAGEMENT_FIELDS if p.get(k) is not None}
            if eng:
                db.add(EngagementMetric(post_id=post.id, **eng))
            if p.get("media_url") or p.get("thumbnail_url") or p.get("video_url"):
                db.add(
                    Media(
                        post_id=post.id,
                        media_type=p.get("media_type"),
                        url=p.get("media_url"),
                        thumbnail_url=p.get("thumbnail_url"),
                        video_url=p.get("video_url"),
                    )
                )
        db.commit()
    return job_id


def _parse_iso(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def error_envelope(body: dict) -> dict:
    """Assert/return the documented error envelope shape."""
    assert isinstance(body, dict) and "error" in body, f"missing error envelope: {body!r}"
    assert isinstance(body["error"], dict)
    assert "code" in body["error"] and "message" in body["error"]
    return body["error"]


def json_body(resp: Any) -> dict:
    """Decode a JSON response body (handles empty bodies)."""
    if not resp.content:
        return {}
    return json.loads(resp.content)