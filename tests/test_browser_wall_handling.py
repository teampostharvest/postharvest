"""Browser wall handling: anonymous retry, and feed-less partial surfacing."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import backend.scraper.browser_scraper as bs
from backend.scraper.parser import ParsedPage, ParsedPost


def _valid_html() -> str:
    return (
        '<html><body>'
        '<script type="application/json" data-fb-graphql-feed="1">'
        '{"data":{"nodes":[{"post_id":"123"}]}}'
        "</script>"
        '<div data-testid="post">' + "x" * 3000 + "</div>"
        "</body></html>"
    )


def _wall_html() -> str:
    return '<html><body><input name="email" id="email"></body></html>'


def _feed_missing_html() -> str:
    return (
        '<html><body><!-- fb-scrape-feed-missing -->'
        '<div data-testid="post">' + "x" * 3000 + "</div>"
        '"post_id":"987"'
        "</body></html>"
    )


def _parsed_page(n_posts: int = 3) -> ParsedPage:
    posts = [
        ParsedPost(
            post_id=f"p{i}",
            published_at=datetime.now(timezone.utc),
        )
        for i in range(n_posts)
    ]
    return ParsedPage(
        page_name="Test Page",
        page_id="123",
        posts=posts,
        fetched_url="https://www.facebook.com/test",
    )


def test_wall_first_attempt_retries_cookie_then_succeeds():
    seen = []

    def fake_fetch(*args, **kwargs):
        seen.append(kwargs.get("use_cookies"))
        if len(seen) == 1:
            return _wall_html(), {"login_wall": True, "posts_found": 0}
        return _valid_html(), {"login_wall": False, "posts_found": 3}

    with patch.object(bs, "fetch_with_browser", side_effect=fake_fetch), \
         patch.object(bs, "parse_browser_page", return_value=_parsed_page()), \
         patch.object(bs, "time"):
        result = bs.scrape_source_browser(
            "https://www.facebook.com/test",
            max_posts=10,
            account_name="default",
        )
    # attempt 1 and 2 both use cookies, then break on success
    assert seen == [True, True]
    assert result.errors == []
    assert len(result.posts) == 3


def test_two_cookie_attempts_then_anonymous():
    seen = []

    def fake_fetch(*args, **kwargs):
        seen.append(kwargs.get("use_cookies"))
        return _wall_html(), {"login_wall": True, "posts_found": 0}

    with patch.object(bs, "fetch_with_browser", side_effect=fake_fetch), \
         patch.object(bs, "parse_browser_page", return_value=_parsed_page()), \
         patch.object(bs, "time"):
        result = bs.scrape_source_browser(
            "https://www.facebook.com/test",
            max_posts=10,
            account_name="default",
        )
    # two cookie attempts, then anonymous
    assert seen == [True, True, False]


def test_feed_missing_partial_surfaces_error():
    with patch.object(bs, "fetch_with_browser",
                      return_value=(_feed_missing_html(), {"login_wall": False, "posts_found": 3})), \
         patch.object(bs, "parse_browser_page", return_value=_parsed_page()):
        result = bs.scrape_source_browser("https://www.facebook.com/test")
    codes = [e.get("code") for e in result.errors]
    assert "partial_feed" in codes
    # partial DOM posts are still kept so the caller can store what exists
    assert len(result.posts) == 3


def test_clean_feed_has_no_partial_error():
    with patch.object(bs, "fetch_with_browser",
                      return_value=(_valid_html(), {"login_wall": False, "posts_found": 3})), \
         patch.object(bs, "parse_browser_page", return_value=_parsed_page()):
        result = bs.scrape_source_browser("https://www.facebook.com/test")
    assert result.errors == []
    assert len(result.posts) == 3


def test_cookie_status_valid():
    xs = {"name": "xs", "expires": datetime.now(timezone.utc).timestamp() + 3600}
    with patch.object(bs, "load_cookies", return_value=[xs]):
        assert bs.get_cookie_status("default") == "VALID"


def test_cookie_status_expired():
    xs = {"name": "xs", "expires": datetime.now(timezone.utc).timestamp() - 3600}
    with patch.object(bs, "load_cookies", return_value=[xs]):
        assert bs.get_cookie_status("default") == "EXPIRED"


def test_cookie_status_missing_account():
    with patch.object(bs, "load_cookies", return_value=None):
        assert bs.get_cookie_status("ghost") == "EXPIRED"


def test_dom_duplicate_of_graphql_post_is_dropped():
    gql = ParsedPost(post_id="1836269811633835",
                     text="Karim Adeyemi: I have a number of goals to reach")
    dom_dup = ParsedPost(post_id=None,
                         text="Fabrizio Romano Verified account 36m Shared with "
                              "Public Karim Adeyemi: I have a number of goals to reach")

    kept = bs._drop_dom_duplicates([gql, dom_dup], bs._clean_dom_text)
    assert [p.post_id for p in kept] == ["1836269811633835"]
    assert len(kept) == 1


def test_unique_dom_post_kept_when_no_overlap():
    gql = ParsedPost(post_id="1836269811633835",
                     text="Karim Adeyemi: transfer update")
    dom_own = ParsedPost(post_id=None, text="A completely unrelated social post")

    kept = bs._drop_dom_duplicates([gql, dom_own], bs._clean_dom_text)
    assert len(kept) == 2


def test_cli_export_mkdir():
    # BUG-005: export into a nested directory that doesn't exist yet
    import tempfile
    from pathlib import Path
    from cli import export_posts
    import json as _json

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        deep = str(tmp_path / "a" / "b" / "out.json")
        result = export_posts([{"post_id": "1"}], fmt="json", output=deep)
        with open(result, "r", encoding="utf-8") as f:
            data = _json.load(f)
        assert data[0]["post_id"] == "1"


def test_cli_reactions_key():
    # BUG-001: verify the CLI reads "reactions", not the old "reactions_total"
    from backend.scraper.normalizer import normalize_post
    from backend.scraper.parser import ParsedPost
    post = normalize_post(ParsedPost(text="hi", reactions=5),
                          facebook_url="https://www.facebook.com/test")
    assert post["reactions"] == 5
    assert post.get("reactions_total") is None
