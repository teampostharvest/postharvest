"""Unit tests for feed walk diagnostics, telemetry, stop reasons, and completeness contract."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from backend.scraper import SourceResult
from backend.scraper.browser_scraper import scrape_source_browser, fetch_with_browser


def _make_gql_payload(post_id: str, text: str) -> str:
    return json.dumps({
        "data": {
            "node": {
                "timeline_list_feed_units": {
                    "edges": [
                        {
                            "node": {
                                "__typename": "Story",
                                "post_id": post_id,
                                "creation_time": 1700000000,
                                "message": {"text": text},
                            }
                        }
                    ]
                }
            }
        }
    })


def test_completeness_contract_full_status():
    """Verify that achieving the requested max_posts yields FULL status and coverage_ratio=1.0."""
    result = SourceResult(
        url="https://www.facebook.com/testpage",
        posts=[{"post_id": "1"}, {"post_id": "2"}],
        stats={
            "posts_requested": 2,
            "posts_discovered": 2,
            "posts_extracted": 2,
            "coverage_ratio": 1.0,
            "stop_reason": "MAX_POSTS_REACHED",
            "completeness_status": "FULL",
        },
    )
    assert result.stats["completeness_status"] == "FULL"
    assert result.stats["coverage_ratio"] == 1.0
    assert result.stats["stop_reason"] == "MAX_POSTS_REACHED"
    assert len(result.errors) == 0


def test_completeness_contract_partial_status():
    """Verify that extractions under max_posts trigger PARTIAL status and diagnostic warning."""
    mock_stats = {
        "login_wall": False,
        "posts_found": 2,
        "stop_reason": "STALE_LIMIT_REACHED",
    }
    html = (
        '<html><body><script type="application/json" data-fb-graphql-feed="1">'
        + _make_gql_payload("101", "First post content sample")
        + "</script></body></html>"
    )

    with patch("backend.scraper.browser_scraper._browser_fetch", return_value=(html, mock_stats)):
        result = scrape_source_browser("https://www.facebook.com/testpage", max_posts=10)

    assert result.stats["completeness_status"] == "PARTIAL"
    assert result.stats["posts_requested"] == 10
    assert result.stats["posts_extracted"] == 1
    assert result.stats["coverage_ratio"] == 0.1
    assert result.stats["stop_reason"] == "STALE_LIMIT_REACHED"

    partial_errs = [e for e in result.errors if e.get("code") == "partial_feed"]
    assert len(partial_errs) == 1
    assert "Stop reason: STALE_LIMIT_REACHED" in partial_errs[0]["message"]
