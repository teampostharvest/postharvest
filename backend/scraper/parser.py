"""Public-HTML parser for Facebook pages (BeautifulSoup + lxml).

The fetcher hands this module the raw HTML of a *public* page variant
(``www.facebook.com/<handle>``, ``mbasic.facebook.com/<handle>`` or
``m.facebook.com/<handle>``) and this module extracts whatever Facebook
publicly renders - nothing more.

Honesty over completeness
-------------------------
Facebook's server-rendered, unauthenticated HTML does NOT expose every field
of the normalized post schema.  The rule of the whole project is: *a field
that is not present in the public markup stays ``None`` - it is never
fabricated, guessed or fetched from a private API.*  Concretely, on public
HTML you should typically expect:

* **post_id / post_url** - usually available (permalink anchors/URLs).
* **text**               - usually available on ``mbasic``; on modern ``www``
  pages post bodies are frequently JS-rendered and therefore absent.
* **published_at**        - exact when Facebook renders an epoch
  ``data-utime`` attribute (mbasic); otherwise parsed from a localized
  human string (e.g. "August 2 at 8:00 AM").  Naive (timezone-less) strings
  are recorded as-is and treated as UTC by convention (see ``parse_timestamp``).
* **engagement counts**   - best-effort.  Comments/shares/views are parsed
  from rendered text/aria-labels; precise split between *likes* and the
  other reactions is frequently unavailable.
* **reaction breakdown**  - **typically unavailable** on public markup
  Facebook renders the top reactions as images without per-reaction counts;
  a full breakdown requires an authenticated session.  Left ``None``.
* **media**               - image thumbnail/URL usually available; the raw
  ``.mp4`` ``video_url`` is **typically unavailable** on public HTML (it is
  only handed to authenticated JS), so ``video_url`` is usually ``None``.
* **transcript / transcript_language** - **never** on public HTML; always
  ``None``.

Parser policy
-------------
* Parse only what is publicly rendered; do not follow "load more" JS.
* Per-post-container failures never crash the page: they are collected in
  ``ParsedPage.post_errors`` (and the orchestrator counts them as failed).
* Heuristics are clearly marked; selector lists are intentionally tolerant
  because Facebook changes markup.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup, Tag

__all__ = [
    "ParsedPage",
    "ParsedPost",
    "extract_posts_from_graphql",
    "extract_posts_from_graphql_body",
    "parse_page",
    "parse_timestamp",
    "parse_count",
    "find_post_roots",
]

# ===========================================================================
# Timestamp / count helpers (used by the parser and re-used by the normalizer)
# ===========================================================================

#: Relative time units -> seconds (Facebook renders e.g. "3 h", "2 d").
_RELATIVE_UNIT_SECONDS = {
    "s": 1, "sec": 1, "second": 1,
    "m": 60, "min": 60, "minute": 60,
    "h": 3600, "hr": 3600, "hour": 3600,
    "d": 86400, "day": 86400,
    "w": 604800, "wk": 604800, "week": 604800,
    "mo": 2592000, "mon": 2592000, "month": 2592000,  # 30-day approximation
    "y": 31536000, "yr": 31536000, "year": 31536000,  # 365-day approximation
}

#: Absolute formats tried in order.  Formats without a year get the implied
#: year handled specially (past-year correction when the result is in the
#: future).  ``%z``-formats produce timezone-aware datetimes.
_ABS_FORMATS = [
    "%B %d, %Y at %I:%M %p",
    "%b %d, %Y at %I:%M %p",
    "%B %d, %Y at %H:%M",
    "%B %d at %I:%M %p",
    "%b %d at %I:%M %p",
    "%B %d, %Y",
    "%b %d, %Y",
    "%d %B %Y at %H:%M",
    "%d %b %Y at %H:%M",
    "%d %B at %H:%M",
    "%d %b at %H:%M",
    "%d %B at %I:%M %p",
    "%d %b at %I:%M %p",
    "%d %B %Y",
    "%d %b %Y",
    "%d %B",
    "%d %b",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
]
_CLOCK_FORMATS = ["%I:%M %p", "%H:%M"]


def parse_count(text: Optional[str]) -> Optional[int]:
    """Parse a Facebook count string into an int.

    Handles ``"1.2K likes"`` -> ``1200``, ``"3.4M"`` -> ``3400000``,
    ``"1,234"`` -> ``1234``, ``"42 Comments"`` -> ``42``.
    Returns ``None`` when no number is present (e.g. a bare "Share" button).
    """
    if not text:
        return None
    s = " ".join(str(text).split())
    multiplier = 1
    m = re.search(r"(?<![\d.])(\d[\d.,]*)\s*([km])\b", s, re.I)
    if m is None:
        m = re.search(r"(?<![\d.])(\d[\d.,]*)\b", s)
    if m is None:
        return None
    try:
        num = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    if m.lastindex and m.lastindex >= 2:
        multiplier = {"k": 1000, "m": 1000000}.get(m.group(2).lower(), 1)
    return int(num * multiplier)


def _count_near(text: str, keywords: Tuple[str, ...]) -> Optional[int]:
    """Look for a count within ~12 chars of any of ``keywords``.

    Used for per-metric extraction ("3 Comments", "1.2K views",
    "All reactions: 15", ...) so that unrelated numbers elsewhere in a post
    body do not leak into the wrong metric.
    """
    for kw in keywords:
        for m in re.finditer(re.escape(kw), text, re.I):
            window = text[max(0, m.start() - 12): m.end() + 12]
            count = parse_count(window)
            if count is not None:
                return count
    return None


def _parse_clock(s: str) -> Optional[datetime]:
    """Parse a bare clock string ("8:05 AM") into a datetime on epoch 0."""
    for fmt in _CLOCK_FORMATS:
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    return None


def _parse_abs(s: str, now: datetime) -> Optional[datetime]:
    """Parse an absolute date/time string.

    Year-implied formats ("August 2 at 8:00 AM") assume the current year and
    roll back one year if the result is in the future.  Naive results are
    treated as UTC by convention (see module docstring); ``%z`` results keep
    their offset.
    """
    raw = s.strip().replace("Z", "+00:00")
    for fmt in _ABS_FORMATS:
        try:
            dt = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        if "%Y" not in fmt:
            dt = dt.replace(year=now.year)
            if dt.replace(tzinfo=timezone.utc) > (now + timedelta(days=1)):
                dt = dt.replace(year=now.year - 1)
        if dt.tzinfo is None:
            # convention: naive Facebook strings are stored as UTC
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    return None


def parse_timestamp(raw: Optional[str], now: Optional[datetime] = None) -> Optional[datetime]:
    """Parse a Facebook timestamp string into an aware UTC datetime.

    Handles, in priority order:
    1. ``data-utime`` epoch values (exact) - caller converts with their own
       ``datetime.fromtimestamp``; a plain numeric string is accepted here too.
    2. Relative strings: "Just now", "3 h", "2 d", "1 w ago".
    3. Absolute strings: "August 2, 2024 at 8:00 AM", "August 2 at 8:00 AM",
       "Yesterday at 5:30 PM", ISO 8601.

    Naive (timezone-less) strings are stored as UTC by convention because the
    public HTML does not reveal the viewer's timezone; when Facebook provides
    an epoch the result is exact.
    """
    now = now or datetime.now(timezone.utc)
    if not raw:
        return None
    s = " ".join(str(raw).split())
    if not s:
        return None
    low = s.lower()

    if low in {"just now", "now", "just posted", "recently", "just shared"}:
        return now

    # plain epoch (from data-utime / data-utime int-like strings)
    if re.fullmatch(r"\d{9,13}", s):
        try:
            return datetime.fromtimestamp(float(s), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    # relative: "<n> <unit> [ago]"
    m = re.match(r"^(\d+)\s*([a-z]+)\s*(ago)?$", low)
    if m:
        amount, unit = int(m.group(1)), m.group(2).rstrip("s")
        seconds = _RELATIVE_UNIT_SECONDS.get(unit)
        if seconds is not None and 0 < amount < 100000:
            return now - timedelta(seconds=amount * seconds)

    # "Yesterday at ..."
    m = re.match(r"^yesterday\s+at\s+(.+)$", low)
    if m:
        clock = _parse_clock(m.group(1))
        if clock:
            yesterday = now - timedelta(days=1)
            return yesterday.replace(hour=clock.hour, minute=clock.minute,
                                     second=0, microsecond=0)

    # absolute formats
    return _parse_abs(s, now)


# ===========================================================================
# Post ID / URL patterns
# ===========================================================================

#: href patterns identifying a *specific post*, in priority order.  The first
#: capture group is the numeric post id.
_POST_ID_PATTERNS = (
    re.compile(r"/story\.php\?[^\"'\s]*story_fbid=(\d+)"),
    re.compile(r"/permalink\.php\?[^\"'\s]*story_fbid=(\d+)"),
    re.compile(r"/photo\.php\?[^\"'\s]*fbid=(\d+)"),
    re.compile(r"/watch/\?[^\"'\s]*v=(\d+)"),
    re.compile(r"/reel/(\d+)"),
    re.compile(r"/posts/(\d+)"),
    re.compile(r"/photos/(?:[^/\"'\s]*/)*?(\d+)(?:[?/]|$)"),
    re.compile(r"/videos/(?:[^/\"'\s]*/)*?(\d+)(?:[?/]|$)"),
)

#: selectors that identify a link-preview / shared-URL attachment card
_LINK_CARD_SELECTORS = (
    "div[data-testid='story-attachment']",
    "a[class*='oembed']",
    "div[class*='share_wrapper']",
    "div[class*='shareWrapper']",
    "div[class*='attachment']",
)

_IGNORED_ANCHOR_LABELS = {
    "see more", "see more...", "read more", "continue reading", "full story",
    "read full story", "more", "translate", "see translation", "view more",
    "show more", "see all", "learn more", "details",
}


# ===========================================================================
# Parsed data containers
# ===========================================================================

@dataclass
class ParsedPost:
    """One extracted post, pre-normalization.

    Values are exactly what the public HTML contained (or ``None``).  The
    normalizer layer converts this into the project's canonical post dict.
    """

    post_id: Optional[str] = None
    post_url: Optional[str] = None
    text: Optional[str] = None
    published_at: Optional[datetime] = None
    published_at_raw: Optional[str] = None
    likes: Optional[int] = None
    reactions: Optional[int] = None
    likes_total: Optional[int] = None
    comments_count: Optional[int] = None
    shares: Optional[int] = None
    views_count: Optional[int] = None
    reaction_like_count: Optional[int] = None
    reaction_love_count: Optional[int] = None
    reaction_care_count: Optional[int] = None
    reaction_haha_count: Optional[int] = None
    reaction_wow_count: Optional[int] = None
    reaction_sad_count: Optional[int] = None
    reaction_angry_count: Optional[int] = None
    has_image: bool = False
    has_video: bool = False
    has_link_preview: bool = False
    thumbnail_url: Optional[str] = None
    media_url: Optional[str] = None
    video_url: Optional[str] = None
    external_links: List[str] = field(default_factory=list)
    mentions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass
class ParsedPage:
    """Page-level extraction result."""

    page_name: Optional[str] = None
    page_id: Optional[str] = None
    profile_url: Optional[str] = None
    og_image: Optional[str] = None
    posts: List[ParsedPost] = field(default_factory=list)
    post_errors: List[Dict[str, str]] = field(default_factory=list)
    fetched_url: str = ""


# ===========================================================================
# Page-level extraction
# ===========================================================================

def _meta_content(soup: BeautifulSoup, *attributes: str) -> Optional[str]:
    """Read a ``meta`` tag by any of several (key, value) attribute pairs."""
    for key, value in attributes:
        tag = soup.find("meta", attrs={key: value})
        if tag and tag.get("content"):
            return str(tag["content"]).strip()
    return None


def _clean_name(raw: Optional[str]) -> Optional[str]:
    """Strip Facebook chrome from page titles ("Name | Facebook" / "Name - Facebook")."""
    if not raw:
        return None
    name = " ".join(str(raw).split())
    name = re.sub(r"\s+[|\-–]\s+Facebook\s*$", "", name).strip()
    return name or None


def _page_id_from_url(url: str) -> Optional[str]:
    """Numeric page/profile id from canonical URL shapes."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    m = re.search(r"/profile\.php\?[^\"'\s]*id=(\d+)", url)
    if m:
        return m.group(1)
    m = re.search(r"/people/[^\"'\s/]+/(\d+)", url)
    if m:
        return m.group(1)
    return None


def parse_page(
    html: str,
    page_url: str,
    *,
    now: Optional[datetime] = None,
    handle: Optional[str] = None,
) -> ParsedPage:
    """Parse public Facebook page HTML into :class:`ParsedPage`.

    :param html: raw HTML fetched by the fetcher (any public variant).
    :param page_url: the page URL used for resolving relative links
                     (pass the *final* URL after redirects).
    :param now: injectable clock for deterministic tests.
    :param handle: page handle (used as a page_name fallback).
    """
    if not html or not html.strip():
        return ParsedPage(page_name=handle, fetched_url=page_url)
    try:
        soup = BeautifulSoup(html, "lxml")
    except ImportError as exc:  # pragma: no cover - dependency hint
        raise ImportError(
            "The scraper parser requires 'beautifulsoup4' and 'lxml'. "
            "Install them (see backend/requirements.txt)."
        ) from exc

    # og:* metadata (usually present even on JS-rendered www pages)
    og_title = _clean_name(_meta_content(soup, ("property", "og:title"),
                                         ("name", "og:title")))
    og_image = _meta_content(soup, ("property", "og:image"),
                             ("name", "og:image"))
    og_url = _meta_content(soup, ("property", "og:url"), ("name", "og:url"))
    title = _clean_name(soup.title.get_text(strip=True) if soup.title else None)

    # page_name: og:title > <title> > header profile link > handle
    page_name = og_title or title
    if not page_name:
        header_link = soup.select_one("h3 a[href]") or soup.select_one("header a[href]")
        if header_link:
            page_name = _clean_name(header_link.get_text(" ", strip=True)) or handle
        else:
            page_name = handle

    profile_url = og_url or page_url
    page_id = _page_id_from_url(profile_url) or _page_id_from_url(page_url)

    posts: List[ParsedPost] = []
    post_errors: List[Dict[str, str]] = []

    # Try DOM-based extraction first (mbasic / older www / mobile)
    roots = find_post_roots(soup)
    if roots:
        for root in roots:
            try:
                posts.append(_parse_post_root(root, page_url, now=now))
            except Exception as exc:  # one bad container must never kill the page
                post_errors.append({
                    "post_url": "",
                    "code": "extraction_failure",
                    "message": f"failed to parse a post container: {exc!r}",
                })
    else:
        # Fallback: extract posts from embedded JSON in <script> tags.
        # Modern Facebook (2024+) embeds post data as JSON inside Relay/Comet
        # <script> tags rather than as DOM elements.
        try:
            posts = _extract_posts_from_scripts(html, page_url, now=now)
        except Exception as exc:
            post_errors.append({
                "post_url": "",
                "code": "extraction_failure",
                "message": f"script-based extraction failed: {exc!r}",
            })

    return ParsedPage(
        page_name=page_name,
        page_id=page_id,
        profile_url=profile_url,
        og_image=og_image,
        posts=posts,
        post_errors=post_errors,
        fetched_url=page_url,
    )


def find_post_roots(soup: BeautifulSoup) -> List[Tag]:
    """Locate post container elements, tolerantly.

    Priority order (Facebook changes markup frequently):

    1. ``<article>`` tags (mbasic / older www)
    2. ``div[role="article"]`` (modern Facebook desktop, 2024+)
    3. ``div[data-ad-preview="message"]`` (modern Facebook post body)
    4. Elements carrying a ``data-ft`` payload (mbasic / older www)
    5. Divs whose class mentions story/userContent (legacy)

    Nested candidates are dropped so a post is never parsed twice.
    """
    roots: List[Tag] = []

    # 1. <article> tags
    articles = soup.find_all("article")
    if articles:
        candidates = list(articles)
    else:
        # 2. Modern Facebook: div[role="article"] is the primary post container
        role_articles = soup.find_all(attrs={"role": "article"})
        if role_articles:
            candidates = list(role_articles)
        else:
            # 3. data-ad-preview marks the post text container on modern pages
            ad_preview = soup.find_all(attrs={"data-ad-preview": "message"})
            if ad_preview:
                # Walk up to find the nearest post-level container
                candidates = []
                for tag in ad_preview:
                    parent = tag.parent
                    for _ in range(8):
                        if parent is None or parent.name is None:
                            break
                        # Prefer a parent that looks like a post container
                        if parent.get("role") == "article" or parent.name == "article":
                            candidates.append(parent)
                            break
                        parent = parent.parent
                    else:
                        # fallback: use the ad-preview element itself
                        candidates.append(tag)
            else:
                # 4. data-ft (mbasic / older www)
                data_ft = soup.find_all(attrs={"data-ft": True})
                if data_ft:
                    candidates = list(data_ft)
                else:
                    # 5. Legacy class-based fallback
                    classy = soup.find_all("div", class_=re.compile(
                        r"(story_body_container|userContent|fbUserPost|story)", re.I))
                    candidates = [c for c in classy if len(c.get_text(strip=True)) > 10]

    # drop elements nested inside another candidate (same order preserved)
    result: List[Tag] = []
    for tag in candidates:
        if not any(_is_ancestor(anc, tag) for anc in result):
            result.append(tag)
    return result


def _is_ancestor(ancestor: Tag, tag: Tag) -> bool:
    for _ in range(64):
        parent = tag.parent if tag.parent is not None else None
        if parent is None:
            return False
        if parent is ancestor:
            return True
        tag = parent  # type: ignore[assignment]
    return False


# ===========================================================================
# Script-based extraction (fallback for modern Facebook www pages)
# ===========================================================================

def _extract_posts_from_scripts(
    html: str,
    page_url: str,
    *,
    now: Optional[datetime] = None,
) -> List[ParsedPost]:
    """Extract posts from embedded JSON in <script> tags (Relay/Comet format).

    Modern Facebook (2024+) embeds server-rendered post data as JSON inside
    ``<script>`` tags using the Relay/Comet framework.  When no DOM-based post
    containers are found (``find_post_roots`` returns empty), this function
    extracts post data from the raw HTML string.

    Returns a list of :class:`ParsedPost` objects.
    """
    if not html:
        return []

    now = now or datetime.now(timezone.utc)
    posts: List[ParsedPost] = []
    seen_ids: set = set()

    # Strategy: find "post_id" values, then search the entire HTML for
    # associated data (creation_time, message, engagement) near each post_id.

    # Collect all post_id occurrences with their positions
    post_id_positions: List[Tuple[str, int]] = [
        (m.group(1), m.start()) for m in re.finditer(
            r'"post_id"\s*:\s*"(\d+)"', html
        )
    ]

    # Group by post_id to get unique posts
    unique_posts: Dict[str, List[int]] = {}
    for pid, pos in post_id_positions:
        unique_posts.setdefault(pid, []).append(pos)

    for post_id, positions in unique_posts.items():
        if post_id in seen_ids:
            continue
        seen_ids.add(post_id)

        # Use the first occurrence as the anchor; search a wide window around it
        anchor = positions[0]
        start = max(0, anchor - 3000)
        end = min(len(html), anchor + 15000)
        block = html[start:end]

        # Extract creation_time (nearest to the post_id anchor)
        published_at = None
        ct_m = re.search(r'"creation_time"\s*:\s*(\d{10})', block)
        if ct_m:
            ts = int(ct_m.group(1))
            try:
                published_at = datetime.fromtimestamp(ts, tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                pass

        # Extract message text — try multiple patterns in priority order
        text_val = None

        # Pattern 1: find "delight_ranges" or "inline_style_ranges", then nearby "text"
        for ranges_key in ("delight_ranges", "inline_style_ranges"):
            ranges_m = re.search(
                rf'"{ranges_key}"\s*:\s*\[[^\]]*\]', block
            )
            if ranges_m:
                nearby = block[ranges_m.end():ranges_m.end() + 2000]
                t_m = re.search(
                    r'"text"\s*:\s*"((?:[^"\\]|\\.)*)"',
                    nearby,
                )
                if t_m and len(t_m.group(1)) > 2:
                    text_val = _decode_fb_text(t_m.group(1))
                    break

        # Pattern 2: "message":{"text":"..."}
        if not text_val:
            for msg_m in re.finditer(r'"message"\s*:\s*\{', block):
                window = block[msg_m.start():msg_m.start() + 3000]
                t_m = re.search(
                    r'"text"\s*:\s*"((?:[^"\\]|\\.)*)"',
                    window,
                )
                if t_m and len(t_m.group(1)) > 5:
                    text_val = _decode_fb_text(t_m.group(1))
                    break

        # Pattern 3: "message_text":"..." (alternative encoding used by some
        # Facebook page variants)
        if not text_val:
            mt_m = re.search(
                r'"message_text"\s*:\s*"((?:[^"\\]|\\.)*)"',
                block,
            )
            if mt_m and len(mt_m.group(1)) > 5:
                text_val = _decode_fb_text(mt_m.group(1))

        # Pattern 4: "text_value":"..." (used in some Comet payloads)
        if not text_val:
            tv_m = re.search(
                r'"text_value"\s*:\s*"((?:[^"\\]|\\.)*)"',
                block,
            )
            if tv_m and len(tv_m.group(1)) > 5:
                text_val = _decode_fb_text(tv_m.group(1))

        # Pattern 5: "content":{"text":"..."} (shared content blocks)
        if not text_val:
            for ct_m in re.finditer(r'"content"\s*:\s*\{', block):
                window = block[ct_m.start():ct_m.start() + 3000]
                t_m = re.search(
                    r'"text"\s*:\s*"((?:[^"\\]|\\.)*)"',
                    window,
                )
                if t_m and len(t_m.group(1)) > 5:
                    text_val = _decode_fb_text(t_m.group(1))
                    break

        # Extract engagement counts — search the entire HTML for these,
        # associated by post_id or nearby context
        reactions = None
        comments_count = None
        shares = None

        # Search for feedback target associated with this post_id
        for search_start in positions:
            search_end = min(len(html), search_start + 20000)
            search_block = html[search_start:search_end]

            if reactions is None:
                rc_m = re.search(
                    r'"reaction_count"\s*:\s*\{[^}]*?"count"\s*:\s*(\d+)',
                    search_block,
                )
                if rc_m:
                    reactions = int(rc_m.group(1))

            if comments_count is None:
                cc_m = re.search(
                    r'"comment_count"\s*:\s*\{[^}]*?"count"\s*:\s*(\d+)',
                    search_block,
                )
                if cc_m:
                    comments_count = int(cc_m.group(1))

            if shares is None:
                sc_m = re.search(
                    r'"share_count"\s*:\s*\{[^}]*?"count"\s*:\s*(\d+)',
                    search_block,
                )
                if sc_m:
                    shares = int(sc_m.group(1))

        # Extract attachment type and media signals
        att_m = re.search(r'"story_attachment_style"\s*:\s*"(\w+)"', block)
        attachment_style = att_m.group(1) if att_m else None

        has_image = '"__typename":"Photo"' in block
        has_video = '"__typename":"Video"' in block
        has_link = attachment_style in ("share", "link") if attachment_style else False

        # Extract thumbnail/media URL
        thumbnail_url = None
        media_url = None
        uri_m = re.search(r'"uri"\s*:\s*"(https?://scontent[^"]+)"', block)
        if uri_m:
            thumb = uri_m.group(1).replace("\\/", "/")
            thumbnail_url = thumb
            media_url = thumb

        # Build post URL
        post_url = None
        owner_m = re.search(r'"owner_id"\s*:\s*"(\d+)"', block)
        owner_id = owner_m.group(1) if owner_m else None
        if owner_id:
            post_url = f"https://www.facebook.com/permalink.php?story_fbid={post_id}&id={owner_id}"
        else:
            post_url = f"https://www.facebook.com/permalink.php?story_fbid={post_id}"

        # Extract mentions and external links from text
        mentions = re.findall(r"@([A-Za-z0-9_.\-]+)", text_val or "")
        external_links = [
            l for l in re.findall(r"(https?://[^\s\"'<>]+)", text_val or "")
            if "facebook.com" not in l.lower()
        ]

        post = ParsedPost(
            post_id=post_id,
            post_url=post_url,
            text=text_val,
            published_at=published_at,
            published_at_raw=str(int(published_at.timestamp())) if published_at else None,
            reactions=reactions,
            likes=reactions,
            comments_count=comments_count,
            shares=shares,
            has_image=has_image,
            has_video=has_video,
            has_link_preview=has_link,
            thumbnail_url=thumbnail_url,
            media_url=media_url,
            external_links=external_links,
            mentions=mentions,
        )
        posts.append(post)

    return posts


#: marker attribute (full name incl. ``data-`` prefix) used on <script>
#: blocks that carry raw Comet GraphQL /api/graphql/ feed payloads captured
#: from the browser network.
_GRAPHQL_SCRIPT_MARKER = "data-fb-graphql-feed"


def _iter_json_objects(text: str):
    """Yield top-level JSON values from a concatenated JSON body.

    Facebook's ``/api/graphql/`` responses are frequently several JSON
    documents serially concatenated (one per query/mutation result).
    """
    decoder = json.JSONDecoder()
    i = 0
    while i < len(text):
        while i < len(text) and text[i] in " \n\t\r":
            i += 1
        if i >= len(text):
            break
        try:
            obj, end = decoder.raw_decode(text, i)
        except (ValueError, json.JSONDecodeError):
            break
        yield obj
        i = end


def _timeline_story_nodes(tl) -> List[dict]:
    """Flatten ``{edges: [{node: Story}]}`` (or a Story node itself) into dicts."""
    found: List[dict] = []
    if not isinstance(tl, dict):
        return found
    # A bare Story node directly under the parent (data.user / data.node).
    if tl.get("__typename") == "Story" and tl.get("post_id"):
        found.append(tl)
    for edge in tl.get("edges") or []:
        child = edge.get("node") if isinstance(edge, dict) else None
        if isinstance(child, dict) and child.get("post_id"):
            found.append(child)
    return found


def _graphql_story_nodes(payload) -> List[dict]:
    """Flatten ``/api/graphql/`` response objects into candidate story dicts.

    Looks for ``data.node`` entries of ``__typename`` ``Story`` plus the
    timeline edge lists under both ``data.node.timeline_list_feed_units``
    (browser-snapshot shape) and ``data.user.timeline_list_feed_units``
    (guest feed-walk shape — the walk's GraphQL frames carry their story
    nodes under ``data.user`` on the first query document).
    """
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, dict):
        return []
    found: List[dict] = []
    node = data.get("node")
    if isinstance(node, dict):
        if node.get("__typename") == "Story" and node.get("post_id"):
            found.append(node)
        found.extend(_timeline_story_nodes(node.get("timeline_list_feed_units")))
    user = data.get("user")
    if isinstance(user, dict):
        found.extend(_timeline_story_nodes(user.get("timeline_list_feed_units")))
    return found


def _graphql_page_info(payload) -> Optional[dict]:
    """Extract ``data.page_info`` (the pagination cursor batch) if present.

    The feed walk's GraphQL responses end with a tail document whose
    ``data.page_info`` carries ``has_next_page`` + the next ``end_cursor``.
    """
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    pi = data.get("page_info")
    if isinstance(pi, dict) and ("has_next_page" in pi or "end_cursor" in pi):
        return pi
    return None


def extract_posts_from_graphql_body(
    body: str,
    page_url: str,
) -> Tuple[List[ParsedPost], Optional[dict]]:
    """Parse a raw concatenated ``/api/graphql/`` feed body into posts + cursor.

    The guest feed walk (``backend/services/node_feed.py``) POSTs ``doc_id`` +
    preloader variables to ``/api/graphql/`` and receives several JSON
    documents back-to-back (one per query slice).  Each document may carry a
    story under ``data.node`` or under ``data.user.timeline_list_feed_units``
    (first document); the tail document carries ``data.page_info`` with
    ``has_next_page`` + the next ``end_cursor``.

    :returns: ``(posts, page_info)`` — deduped :class:`ParsedPost` list and the
        page_info dict (``None`` when the body carries none).
    """
    if not body or '"post_id"' not in body:
        return [], None
    posts: List[ParsedPost] = []
    seen: set = set()
    page_info: Optional[dict] = None
    for obj in _iter_json_objects(body):
        for story in _graphql_story_nodes(obj):
            pid = str(story.get("post_id") or "")
            if pid and pid in seen:
                continue
            if pid:
                seen.add(pid)
            try:
                post = _graphql_story_to_post(story, page_url)
            except Exception:  # one bad node must not kill the batch
                continue
            if post and (post.post_id or post.text):
                posts.append(post)
        pi = _graphql_page_info(obj)
        if pi is not None:
            page_info = pi
    return posts, page_info


def extract_posts_from_graphql(html: str, page_url: str) -> List[ParsedPost]:
    """Extract posts from embedded ``/api/graphql/`` feed payloads.

    ``fetch_with_browser`` captures the browser's own Comet feed API responses
    and stores each one inside a ``<script type="application/json"
    data-fb-graphql-feed="1">…</script>`` block of the returned snapshot.
    Each response is a sequence of JSON documents; story nodes carry
    ``post_id``, ``creation_time``, the rendered ``message.text``, media
    attachments and engagement counts.  This intentionally complements (not
    replaces) the DOM/script heuristics used for anonymous public pages.
    """
    if not html or '"post_id"' not in html:
        return []
    soup = BeautifulSoup(html, "lxml")
    posts: List[ParsedPost] = []
    seen: set = set()
    for tag in soup.find_all("script", attrs={_GRAPHQL_SCRIPT_MARKER: True}):
        raw = tag.get_text()
        if not raw or '"post_id"' not in raw:
            continue
        for obj in _iter_json_objects(raw):
            for story in _graphql_story_nodes(obj):
                pid = str(story.get("post_id") or "")
                if pid and pid in seen:
                    continue
                if pid:
                    seen.add(pid)
                try:
                    post = _graphql_story_to_post(story, page_url)
                except Exception:  # one bad node must not kill the batch
                    continue
                if post and (post.post_id or post.text):
                    posts.append(post)
    return posts


def _graphql_story_to_post(story: dict, page_url: str) -> Optional[ParsedPost]:
    """Build a :class:`ParsedPost` from one Comet feed story node."""
    post_id = str(story.get("post_id") or "").strip()
    if not post_id:
        return None

    published_at: Optional[datetime] = None
    ct = story.get("creation_time")
    if isinstance(ct, (int, float)):
        published_at = datetime.fromtimestamp(int(ct), tz=timezone.utc)
    published_raw = str(int(ct)) if isinstance(ct, (int, float)) else None

    permalink = story.get("permalink_url")
    post_url = None
    if permalink:
        abs_url = urljoin(page_url, str(permalink))
        if abs_url.startswith("http"):
            post_url = abs_url

    text_val: Optional[str] = None
    story_render = story
    if isinstance(story_render.get("comet_sections"), dict):
        content = story_render["comet_sections"].get("content")
        if isinstance(content, dict):
            inner = content.get("story")
            if isinstance(inner, dict):
                story_render = inner
    msg = story_render.get("message")
    if isinstance(msg, dict):
        text_val = msg.get("text") or None
    elif isinstance(msg, str) and msg.strip():
        text_val = msg
    if not text_val:
        alt = story_render.get("text")
        if isinstance(alt, str) and alt.strip():
            text_val = alt

    reactions = comments_count = shares = 0
    for node in (story, story_render):
        fb = node.get("feedback") if isinstance(node, dict) else None
        if not isinstance(fb, dict):
            continue
        def _grab_int(key: str) -> Optional[int]:
            v = fb.get(key)
            if isinstance(v, dict) and isinstance(v.get("count"), (int, float)):
                return int(v["count"])
            if v is None:
                v = fb.get(key.replace("_count", ""))
            if isinstance(v, dict) and isinstance(v.get("total_count"), (int, float)):
                return int(v["total_count"])
            return int(v) if isinstance(v, (int, float)) else None

        rc = _grab_int("reaction_count")
        if rc is not None:
            reactions = rc
        cc = _grab_int("comment_total_count")
        if cc is None:
            cc = _grab_int("comment_widget_total_comment_count")
        if cc is not None:
            comments_count = cc
        sc = _grab_int("share_count")
        if sc is not None:
            shares = sc

    # Counts may sit deeper inside the story renderer (e.g. comet_sections).
    def _deep_counts(obj: Any, key_prefix: str) -> List[int]:
        hits = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k.startswith(key_prefix) and isinstance(v, dict) \
                        and isinstance(v.get("count"), (int, float)):
                    hits.append(int(v["count"]))
                hits.extend(_deep_counts(v, key_prefix))
        elif isinstance(obj, list):
            for it in obj:
                hits.extend(_deep_counts(it, key_prefix))
        return hits

    def _deep_total_comment_counts(obj: Any) -> List[int]:
        """Collect ``{"comments": {"total_count": N}}`` nodes anywhere."""
        hits = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "comments" and isinstance(v, dict) \
                        and isinstance(v.get("total_count"), (int, float)):
                    hits.append(int(v["total_count"]))
                hits.extend(_deep_total_comment_counts(v))
        elif isinstance(obj, list):
            for it in obj:
                hits.extend(_deep_total_comment_counts(it))
        return hits

    if reactions == 0:
        rc = _deep_counts(story, "reaction_count")
        if rc:
            reactions = rc[0]
    if comments_count == 0:
        cc = _deep_counts(story, "comment_total_count") \
            or _deep_counts(story, "comment_widget_total_comment_count")

        # Comet newer shape: the total lives on a ``comments`` renderer node as
        # ``comments.total_count`` (no dedicated *_count key).  Collect every
        # ``{"comments": {"total_count": N}}`` and take the largest.
        if not cc:
            cc = _deep_total_comment_counts(story)
        if cc:
            comments_count = cc[0]
    if shares == 0:
        sc = _deep_counts(story, "share_count")
        if sc:
            shares = sc[0]

    likes = reactions or 0

    has_image = has_video = False
    thumbnail_url = media_url = video_url = None
    for att in story.get("attachments") or []:
        if not isinstance(att, dict):
            continue
        media = att.get("media")
        if not isinstance(media, dict):
            continue
        typename = media.get("__typename") or ""
        img = media.get("image") or media.get("target_image") or {}
        uri = (img.get("uri") if isinstance(img, dict) else None) or media.get("uri")
        if uri:
            if "Video" in typename or media.get("playable_url"):
                has_video = True
                video_url = media.get("playable_url") or uri
                thumbnail_url = uri
            else:
                has_image = True
                if not thumbnail_url:
                    thumbnail_url = uri
                if not media_url:
                    media_url = uri
        if not uri and isinstance(media.get("image"), dict):
            uri2 = media.get("image", {}).get("uri")
            if uri2:
                has_image = True
                if not thumbnail_url:
                    thumbnail_url = uri2

        # Comet newer shape: the shallow ``attachments[].media`` node only
        # carries ``__typename`` + id; the real renderer (photo URI, video
        # frames) is nested under ``attachments[].styles.attachment.media``.
        renderer = att.get("styles")
        if not uri and isinstance(renderer, dict):
            nested = renderer.get("attachment")
            if isinstance(nested, dict):
                # Albums: styles.attachment.all_subattachments.nodes[].media
                for sub in nested.get("all_subattachments", {}).get("nodes") or []:
                    smedia = sub.get("media") if isinstance(sub, dict) else None
                    if not isinstance(smedia, dict):
                        continue
                    simg = smedia.get("image") or smedia.get("target_image") \
                        or smedia.get("photo_image") or smedia.get("viewer_image") or {}
                    suri = (simg.get("uri") if isinstance(simg, dict) else None) \
                        or smedia.get("uri") or smedia.get("first_frame_thumbnail")
                    if isinstance(suri, dict):
                        suri = suri.get("uri")
                    if not suri:
                        continue
                    if "Video" in (smedia.get("__typename") or ""):
                        has_video = True
                        if not video_url:
                            video_url = smedia.get("playable_url") or smedia.get("url")
                        if not thumbnail_url:
                            thumbnail_url = suri
                    else:
                        has_image = True
                        if not thumbnail_url:
                            thumbnail_url = suri
                        if not media_url:
                            media_url = suri
                nmedia = nested.get("media")
                if isinstance(nmedia, dict):
                    ntyp = nmedia.get("__typename") or typename
                    nimg = nmedia.get("image") or nmedia.get("target_image") \
                        or nmedia.get("preferred_thumbnail") or nmedia.get("photo_image") \
                        or nmedia.get("viewer_image") or {}
                    if isinstance(nimg, dict) and not nimg.get("uri"):
                        nimg = nimg.get("image") or {}
                    nuri = (nimg.get("uri") if isinstance(nimg, dict) else None) \
                        or nmedia.get("uri") or nmedia.get("first_frame_thumbnail")
                    if isinstance(nuri, dict):
                        nuri = nuri.get("uri")
                    if nuri and "Video" in ntyp:
                        has_video = True
                        if not video_url:
                            video_url = nmedia.get("playable_url") or nmedia.get("url")
                        if not thumbnail_url:
                            thumbnail_url = nuri
                    elif nuri:
                        has_image = True
                        if not thumbnail_url:
                            thumbnail_url = nuri
                        if not media_url:
                            media_url = nuri

    external_links = [
        l for l in re.findall(r"(https?://[^\s\"'<>]+)", text_val or "")
        if "facebook.com" not in l.lower()
    ]
    mentions = re.findall(r"@([A-Za-z0-9_.\-]+)", text_val or "")

    return ParsedPost(
        post_id=post_id,
        post_url=post_url,
        text=text_val,
        published_at=published_at,
        published_at_raw=published_raw,
        reactions=reactions,
        likes=likes,
        comments_count=comments_count,
        shares=shares,
        has_image=has_image,
        has_video=has_video,
        has_link_preview=False,
        thumbnail_url=thumbnail_url,
        media_url=media_url,
        video_url=video_url,
        external_links=external_links,
        mentions=mentions,
    )


def extract_posts_from_graphql(html: str, page_url: str) -> List[ParsedPost]:
    """Extract posts from embedded ``/api/graphql/`` feed payloads.

    ``fetch_with_browser`` captures the browser's own Comet feed API responses
    and stores each one inside a ``<script type="application/json"
    data-fb-graphql-feed="1">…</script>`` block of the returned snapshot.
    Each response is a sequence of JSON documents; story nodes carry
    ``post_id``, ``creation_time``, the rendered ``message.text``, media
    attachments and engagement counts.  This intentionally complements (not
    replaces) the DOM/script heuristics used for anonymous public pages.
    """
    if not html or '"post_id"' not in html:
        return []
    soup = BeautifulSoup(html, "lxml")
    posts: List[ParsedPost] = []
    seen: set = set()
    for tag in soup.find_all("script", attrs={_GRAPHQL_SCRIPT_MARKER: True}):
        raw = tag.get_text()
        if not raw or '"post_id"' not in raw:
            continue
        for obj in _iter_json_objects(raw):
            for story in _graphql_story_nodes(obj):
                pid = str(story.get("post_id") or "")
                if pid and pid in seen:
                    continue
                if pid:
                    seen.add(pid)
                try:
                    post = _graphql_story_to_post(story, page_url)
                except Exception:  # one bad node must not kill the batch
                    continue
                if post and (post.post_id or post.text):
                    posts.append(post)
    return posts


def _decode_fb_text(raw: str) -> Optional[str]:
    """Decode a Facebook JSON-escaped text string.

    Handles ``\\uXXXX`` Bengali/Arabic/other unicode escapes and standard
    JSON escape sequences.  Returns ``None`` when the decoded result is
    empty or whitespace-only.
    """
    try:
        decoded = raw.encode("utf-8").decode("unicode_escape")
    except (UnicodeDecodeError, ValueError):
        # Fallback: manually decode common sequences
        decoded = (
            raw.replace("\\n", "\n")
            .replace("\\r", "\r")
            .replace("\\t", "\t")
            .replace('\\"', '"')
            .replace("\\\\", "\\")
        )
    return decoded.strip() or None


# ===========================================================================
# Per-post extraction
# ===========================================================================

def _clean_href(href: str) -> str:
    return href.replace("&amp;", "&").strip()


def _post_url_and_id(root: Tag, page_url: str) -> Tuple[Optional[str], Optional[str]]:
    """Find the post permalink + numeric id (best effort, first match wins)."""
    # Modern Facebook: the container itself may carry data-href with the permalink
    data_href = root.get("data-href")
    if data_href:
        for pattern in _POST_ID_PATTERNS:
            m = pattern.search(str(data_href))
            if m:
                return urljoin(page_url, str(data_href)), m.group(1)

    for anchor in root.find_all("a", href=True):
        href = _clean_href(str(anchor["href"]))
        for pattern in _POST_ID_PATTERNS:
            m = pattern.search(href)
            if m:
                post_id = m.group(1)
                # story.php / permalink.php hrefs may point to the *page*
                # timeline; they are still unique per post via story_fbid.
                abs_url = urljoin(page_url, href)
                return abs_url, post_id
    return None, None


def _post_text(root: Tag) -> Optional[str]:
    """Extract the visible body text of a post container.

    Removes scripts/styles, header elements, "See more / Full Story" chrome
    and like/comment/share control anchors before joining the remaining text.
    mbasic renders the full text; www frequently renders nothing (JS).
    """
    for tag in root.find_all(["script", "style", "noscript"]):
        tag.decompose()
    for el in root.find_all(["h3", "h4", "header"]):
        el.decompose()
    for a in list(root.find_all("a")):
        label = " ".join(a.get_text(" ", strip=True).split()).lower()
        if label in _IGNORED_ANCHOR_LABELS:
            a.decompose()
            continue
        href = (a.get("href") or "").lower()
        if re.search(r"/(comment|share|like|reaction|react|report)", href) \
                and len(a.get_text(strip=True)) < 40:
            a.decompose()

    text = root.get_text(" ", strip=True)
    return text or None


def _post_time(root: Tag, now: datetime) -> Tuple[Optional[datetime], Optional[str]]:
    """Extract post publish time.

    1. ``abbr[data-utime]`` - exact epoch (mbasic).
    2. ``abbr`` text or ``span.timestamp`` - parsed relative/absolute.
    3. Modern Facebook: ``span[data-utime]``, ``div[data-testid]`` with time info.
    4. A date-like sub-string found in the visible text (strict scan).
    """
    # 1. data-utime epoch (exact) — on any element, not just abbr
    for el in root.find_all(attrs={"data-utime": True}):
        utime = el.get("data-utime")
        if utime and re.fullmatch(r"\d{9,13}", str(utime)):
            try:
                dt = datetime.fromtimestamp(float(utime), tz=timezone.utc)
                return dt, str(el.get_text(strip=True)) or None
            except (ValueError, OverflowError, OSError):
                pass

    # 2. abbr text
    for abbr in root.find_all("abbr"):
        label = " ".join(abbr.get_text(" ", strip=True).split())
        if label:
            dt = parse_timestamp(label, now=now)
            if dt:
                return dt, label

    # 3. span.timestamp / modern timestamp classes
    for span in root.select("span.timestamp, span[class*='timestamp']"):
        label = " ".join(span.get_text(" ", strip=True).split())
        if label:
            dt = parse_timestamp(label, now=now)
            if dt:
                return dt, label

    # 4. Modern Facebook: look for aria-label time on anchor/div elements
    #    e.g. <a aria-label="August 15, 2024 at 3:00 PM">
    for el in root.find_all(["a", "span", "div"], attrs={"aria-label": True}):
        label = str(el["aria-label"]).strip()
        # Only try to parse if it looks like a date/time
        if re.search(r"\b(january|february|march|april|may|june|july|august|"
                     r"september|october|november|december|jan|feb|mar|apr|"
                     r"jun|jul|aug|sep|oct|nov|dec)\b", label, re.I):
            dt = parse_timestamp(label, now=now)
            if dt:
                return dt, label

    # 5. strict text scan: month names / 4-digit year / relative units / "ago"
    text = root.get_text(" ", strip=True)
    strict = re.compile(
        r"\b(january|february|march|april|may|june|july|august|september|"
        r"october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|"
        r"oct|nov|dec|20\d{2}|\d{1,2}\s+ago|ago|yesterday|just now|now|"
        r"\d{1,3}\s*[smhdw]\s*(ago)?)\b",
        re.I,
    )
    for m in strict.finditer(text):
        token = m.group(0)
        if re.fullmatch(r"\d{1,3}\s*[smhdw]\s*(ago)?", token, re.I):
            dt = parse_timestamp(token, now=now)
            if dt:
                return dt, token.strip()
        # absolute dates ("31 August at 21:29"): parse_timestamp needs a
        # fullmatch, so wide snippets clip at the real date boundary by
        # trying shrinking prefixes of the date region.
        if re.match(r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*",
                    token, re.I):
            region = text[max(0, m.start() - 8): m.start() + 36]
            for end in range(len(region), 6, -1):
                dt = parse_timestamp(region[:end].strip(), now=now)
                if dt:
                    return dt, region[:end].strip()
        snippet = text[max(0, m.start() - 12): m.end() + 24]
        dt = parse_timestamp(snippet, now=now)
        if dt:
            return dt, snippet.strip()
    return None, None


def _is_in_link_card(img: Tag) -> bool:
    """True if the image belongs to an attachment/link-preview card
    (those images are *preview thumbnails*, not post media)."""
    for ancestor in img.parents:
        if not isinstance(ancestor, Tag) or ancestor.name is None:
            continue
        cls = " ".join(ancestor.get("class") or [])
        if re.search(r"(share|attachment|oembed)", cls, re.I):
            return True
        if ancestor.name in ("h3", "header"):
            return True
    return False


def _img_src(img: Tag) -> Optional[str]:
    """Best src for an image: explicit ``src`` when real, else ``data-src``
    (used by Facebook for lazy-loaded images)."""
    src = str(img.get("src") or "").strip()
    if not src or "/rsrc.php/" in src and src.endswith(".png"):
        # empty or a placeholder sprite -> prefer the lazy-loaded data-src
        src = str(img.get("data-src") or "").strip()
    return src or None


def _dim(img: Tag, attr: str) -> Optional[int]:
    value = img.get(attr)
    if value is None:
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _iter_content_images(root: Tag):
    """Yield (img, src, area) for candidate *content* images only.

    Skips: placeholders, emoji/icon assets, avatar/profile thumbnails, tiny
    images (< 60x60 when dimensions are known) and link-card preview images.
    """
    for img in root.find_all("img"):
        src = _img_src(img)
        if not src:
            continue
        low_src = src.lower()
        if re.search(r"/(icons?|emojis?)/|emoji", low_src):
            continue
        if re.search(r"(avatar|profilepic|profile_pic)", low_src):
            continue
        w, h = _dim(img, "width"), _dim(img, "height")
        area = (w or 0) * (h or 0)
        if w is not None and h is not None and area < 60 * 60:
            continue
        if _is_in_link_card(img):
            continue
        yield img, src, area


def _post_media(root: Tag, page_url: str) -> Dict[str, object]:
    """Extract image / video media signals from the post container."""
    out: Dict[str, object] = {
        "has_image": False, "has_video": False,
        "has_link_preview": False,
        "thumbnail_url": None, "media_url": None, "video_url": None,
    }

    # video?  (anchors to /videos/|/reel/|/watch/ or a <video> element)
    for anchor in root.find_all("a", href=True):
        href = _clean_href(str(anchor["href"]))
        if re.search(r"/(videos?|reel|watch)/", href) \
                or re.search(r"[?&](?:v|video_id|fbid)=\d+", href):
            out["has_video"] = True
            break
    if root.find("video") is not None:
        out["has_video"] = True
        for video in root.find_all("video"):
            src = video.get("src")
            if src:
                abs_url = urljoin(page_url, str(src))
                if abs_url.startswith(("http://", "https://")):
                    out["video_url"] = abs_url
                    break

    # direct mp4 metadata (rare on public HTML - usually None, kept honest)
    if not out["video_url"]:
        for meta in root.find_all("meta"):
            prop = (meta.get("property") or meta.get("itemprop") or "")
            content = meta.get("content")
            if content and prop.lower() in ("og:video", "og:video:url",
                                            "twitter:player:stream"):
                abs_url = urljoin(page_url, str(content))
                if abs_url.startswith(("http://", "https://")) \
                        and abs_url.split("?")[0].endswith((".mp4", ".m4v", ".webm")):
                    out["video_url"] = abs_url
                    break

    # content images (excludes link-card previews)
    images = sorted(_iter_content_images(root), key=lambda t: t[2],
                    reverse=True)
    if images:
        best, src, _area = images[0]
        abs_url = urljoin(page_url, src)
        out["has_image"] = True
        out["media_url"] = abs_url
        out["thumbnail_url"] = abs_url

    # link-card preview image (thumbnail for link posts)
    for sel in _LINK_CARD_SELECTORS:
        card = root.select_one(sel)
        if card is not None:
            out["has_link_preview"] = True
            img = card.find("img")
            src = _img_src(img) if img else None
            if src and not out["thumbnail_url"]:
                out["thumbnail_url"] = urljoin(page_url, src)
            break

    # og:image fallback when the post itself carries no image
    if not out["thumbnail_url"]:
        og_img = root.find("meta", attrs={"property": "og:image"})
        if og_img and og_img.get("content"):
            out["thumbnail_url"] = urljoin(page_url, str(og_img["content"]))
            if not out["has_image"]:
                out["has_image"] = True

    return out


_REACTION_KEYS = {
    "like": "reaction_like_count",
    "love": "reaction_love_count",
    "care": "reaction_care_count",
    "haha": "reaction_haha_count",
    "wow": "reaction_wow_count",
    "sad": "reaction_sad_count",
    "angry": "reaction_angry_count",
}


def _post_engagement(root: Tag) -> Dict[str, Optional[int]]:
    """Extract engagement numbers rendered in the public markup.

    Works from visible text ("3 Comments", "1.2K views", "All reactions: 15")
    and from ``aria-label`` attributes.  Per-reaction breakdown is frequently
    NOT rendered publicly (Facebook shows reaction images without counts);
    such fields stay ``None``.  This is documented best-effort behavior.
    """
    text = root.get_text(" ", strip=True)
    counts: Dict[str, Optional[int]] = {
        "likes": None,
        "reactions": None,
        "comments_count": None,
        "shares": None,
        "views_count": None,
        "reaction_like_count": None,
        "reaction_love_count": None,
        "reaction_care_count": None,
        "reaction_haha_count": None,
        "reaction_wow_count": None,
        "reaction_sad_count": None,
        "reaction_angry_count": None,
    }

    counts["comments_count"] = _count_near(text, ("comment",))
    # "X shares" — use a precise pattern so "Shared with Public 13m" doesn't
    # read the relative time (13m) as 13,000,000 shares.
    share_m = re.search(r"(\d[\d.,]*\s*[km]?)\s*shares?\b", text, re.I)
    counts["shares"] = parse_count(share_m.group(1)) if share_m else None
    counts["views_count"] = _count_near(text, ("view",))
    counts["reactions"] = _count_near(
        text, ("all reactions", "reacted", "reactions"))
    counts["likes"] = _count_near(text, ("like",))

    # aria-label driven counts (most reliable on modern markup)
    for el in root.find_all(attrs={"aria-label": True}):
        label = str(el["aria-label"])
        count = parse_count(label)
        if count is None:
            continue
        low = label.lower()
        if "comment" in low:
            counts["comments_count"] = count
        elif "share" in low:
            counts["shares"] = count
        elif "view" in low:
            counts["views_count"] = count
        elif "react" in low:
            counts["reactions"] = count

        for name, key in _REACTION_KEYS.items():
            if name in low:
                counts[key] = count
                break

    # reaction emoji images with an own count in their parent text
    for img in root.find_all("img", alt=True):
        alt = str(img["alt"]).strip().lower()
        key = _REACTION_KEYS.get(alt)
        if not key:
            continue
        parent_text = " ".join(
            (img.parent.get_text(" ", strip=True) if img.parent else "")).split()
        count = parse_count(" ".join(parent_text))
        if count is not None:
            counts[key] = count

    return counts


_FB_SHIM_HOSTS = ("l.facebook.com", "lm.facebook.com", "l.messenger.com")


def _decode_facebook_shim(href: str) -> str:
    """Resolve the obvious ``u=`` parameter of Facebook's link shim
    (l.facebook.com/l.php?u=...) to the real external URL.  Pure URL
    parsing - not an evasion technique."""
    parsed = urlparse(href)
    if parsed.hostname in _FB_SHIM_HOSTS and parsed.path.startswith("/l.php"):
        target = (parse_qs(parsed.query).get("u") or [None])[0]
        if target:
            return target
    return href


def _post_links_and_mentions(
    root: Tag, text: str, page_url: str
) -> Tuple[List[str], List[str]]:
    """External links + mentioned names, from anchors in the post body."""
    page_hosts = {"facebook.com", "www.facebook.com", "m.facebook.com",
                  "mbasic.facebook.com", "touch.facebook.com"}
    external: List[str] = []
    mentions: List[str] = []

    for anchor in root.find_all("a", href=True):
        try:
            parsed = urlparse(anchor["href"])
        except ValueError:
            continue
        label = " ".join(anchor.get_text(" ", strip=True).split())
        host = (parsed.hostname or "").lower()
        if parsed.scheme in ("http", "https") and host and host not in page_hosts:
            href = _decode_facebook_shim(_clean_href(str(anchor["href"])))
            abs_url = urljoin(page_url, href)
            if abs_url.startswith(("http://", "https://")):
                external.append(abs_url)
        elif host and host.endswith(".facebook.com") and not host in page_hosts:
            pass  # other facebook subdomains: not "external"
        # profile mention: link into a facebook profile with a real name label
        href = _clean_href(str(anchor.get("href", "")))
        if label and len(label) <= 60 and re.search(
                r"/(?:profile\.php\?[^\"'\s]*id=\d+|people/[^/]+/\d+)", href):
            mentions.append(label)

    # @handle style mentions from the post text
    mentions.extend(re.findall(r"@([A-Za-z0-9_.\-\u0080-\uFFFF]+)", text or ""))

    # de-duplicate preserving order
    def _uniq(seq: List[str]) -> List[str]:
        seen = set()
        out = []
        for item in seq:
            if item not in seen:
                seen.add(item)
                out.append(item)
        return out

    return _uniq(external), _uniq(mentions)


def _parse_post_root(root: Tag, page_url: str, *, now: Optional[datetime] = None) -> ParsedPost:
    """Parse one post container into a :class:`ParsedPost`."""
    now = now or datetime.now(timezone.utc)

    post_url, post_id = _post_url_and_id(root, page_url)
    published_at, published_raw = _post_time(root, now)
    text = _post_text(root)
    media = _post_media(root, page_url)
    engagement = _post_engagement(root)
    external, mentions = _post_links_and_mentions(root, text or "", page_url)

    return ParsedPost(
        post_id=post_id,
        post_url=post_url,
        text=text,
        published_at=published_at,
        published_at_raw=published_raw,
        likes=engagement["likes"],
        reactions=engagement["reactions"],
        comments_count=engagement["comments_count"],
        shares=engagement["shares"],
        views_count=engagement["views_count"],
        reaction_like_count=engagement["reaction_like_count"],
        reaction_love_count=engagement["reaction_love_count"],
        reaction_care_count=engagement["reaction_care_count"],
        reaction_haha_count=engagement["reaction_haha_count"],
        reaction_wow_count=engagement["reaction_wow_count"],
        reaction_sad_count=engagement["reaction_sad_count"],
        reaction_angry_count=engagement["reaction_angry_count"],
        has_image=bool(media["has_image"]),
        has_video=bool(media["has_video"]),
        has_link_preview=bool(media["has_link_preview"]),
        thumbnail_url=media["thumbnail_url"],  # type: ignore[arg-type]
        media_url=media["media_url"],          # type: ignore[arg-type]
        video_url=media["video_url"],          # type: ignore[arg-type]
        external_links=external,
        mentions=mentions,
    )