"""Unit tests for capture boundary metrics, early listener registration, and payload retention."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from backend.scraper import SourceResult
from backend.scraper.browser_scraper import scrape_source_browser


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


def test_boundary_metrics_tracked_in_stats():
    """Verify that boundary_metrics dictionary is attached to SourceResult.stats."""
    html = (
        '<html><body><script type="application/json" data-fb-graphql-feed="1">'
        + _make_gql_payload("901", "Boundary metrics test post content")
        + "</script></body></html>"
    )
    mock_stats = {
        "login_wall": False,
        "posts_found": 1,
        "stop_reason": "MAX_POSTS_REACHED",
        "boundary_metrics": {
            "responses_received": 15,
            "graphql_responses": 5,
            "responses_retained": 5,
            "graphql_post_ids_discovered": 1,
        },
    }

    with patch("backend.scraper.browser_scraper._browser_fetch", return_value=(html, mock_stats)):
        result = scrape_source_browser("https://www.facebook.com/testpage", max_posts=1)

    assert "boundary_metrics" in result.stats
    bm = result.stats["boundary_metrics"]
    assert bm["responses_received"] == 15
    assert bm["graphql_responses"] == 5
    assert bm["responses_retained"] == 5
    assert bm["graphql_post_ids_discovered"] == 1
    assert bm["posts_extracted"] == 1
    assert result.stats["completeness_status"] == "FULL"
