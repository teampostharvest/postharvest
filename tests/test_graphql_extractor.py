"""GraphQL feed extractor: comments, media types, and second-generation shapes.

Covers the Comet ``/api/graphql/`` shapes where engagement is nested under
``adaptive_ufi_action_renderers`` / ``comment_rendering_instance.comments``
and media URIs live inside ``attachments[].styles.attachment.media`` rather
than on the shallow ``attachments[].media`` node.
"""
from __future__ import annotations

import json
from pathlib import Path

from backend.scraper.normalizer import normalize_post
from backend.scraper.parser import extract_posts_from_graphql

FIXTURES = Path(__file__).parent / "fixtures"


def _wrap(payload: dict) -> str:
    return (
        '<script type="application/json" data-fb-graphql-feed="1">'
        + json.dumps(payload)
        + "</script>"
    )


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_graphql_photo_extracts_comments_media_and_engagement():
    html = _wrap(_load("graphql_story_photo.json"))
    posts = extract_posts_from_graphql(html, "https://www.facebook.com/testpage")

    assert len(posts) == 1
    post = posts[0]
    assert post.post_id == "111"
    assert post.has_image is True
    assert post.has_video is False
    assert post.comments_count == 12
    assert post.likes == 1000
    assert post.shares == 33
    assert post.thumbnail_url and post.thumbnail_url.startswith("https://img/")

    normalized = normalize_post(post, facebook_url="https://www.facebook.com/testpage")
    assert normalized["post_type"] == "image"
    assert normalized["media_type"] == "image"
    assert normalized["comments_count"] == 12


def test_graphql_photo_nested_media_style():
    payload = _load("graphql_story_photo.json")
    payload["data"]["node"]["timeline_list_feed_units"]["edges"][0]["node"][
        "attachments"
    ][0] = {
        "media": {"__typename": "Photo", "id": "shallow-only"},
        "styles": {
            "__typename": "StoryAttachmentPhotoStyleRenderer",
            "attachment": {
                "media": {
                    "__typename": "Photo",
                    "photo_image": {"uri": "https://img/nested-photo.jpg"},
                }
            },
        },
    }
    posts = extract_posts_from_graphql(_wrap(payload), "https://www.facebook.com/testpage")
    assert len(posts) == 1
    assert posts[0].has_image is True
    assert posts[0].thumbnail_url == "https://img/nested-photo.jpg"


def test_graphql_story_without_renderer_stays_minimal():
    payload = _load("graphql_story_photo.json")
    node = payload["data"]["node"]["timeline_list_feed_units"]["edges"][0]["node"]
    node["attachments"] = None
    node["comet_sections"] = None
    posts = extract_posts_from_graphql(_wrap(payload), "https://www.facebook.com/testpage")

    assert len(posts) == 1
    post = posts[0]
    assert post.has_image is False
    assert post.has_video is False
    assert post.comments_count == 0
    # text-only stories never fabricate engagement/media signals
    assert post.has_image is False
    assert post.has_video is False
    assert post.comments_count == 0
    # text-only stories never fabricate engagement/media signals
    assert post.text or post.post_id


def test_graphql_user_timeline_feed_units_shape():
    """Test Layer 1 user.timeline_feed_units shape extraction."""
    payload = {
        "data": {
            "user": {
                "timeline_feed_units": {
                    "edges": [
                        {
                            "node": {
                                "__typename": "Story",
                                "post_id": "999888777",
                                "creation_time": 1700000000,
                                "message": {"text": "User profile timeline post"},
                            }
                        }
                    ]
                }
            }
        }
    }
    posts = extract_posts_from_graphql(_wrap(payload), "https://www.facebook.com/testuser")
    assert len(posts) == 1
    assert posts[0].post_id == "999888777"
    assert posts[0].text == "User profile timeline post"


def test_graphql_page_timeline_feed_units_shape():
    """Test Layer 1 page.timeline_feed_units shape extraction."""
    payload = {
        "data": {
            "page": {
                "timeline_feed_units": {
                    "edges": [
                        {
                            "node": {
                                "post_id": "555444333",
                                "creation_time": 1700000500,
                                "message": {"text": "Page announcement post"},
                                "feedback": {"reaction_count": {"count": 42}},
                            }
                        }
                    ]
                }
            }
        }
    }
    posts = extract_posts_from_graphql(_wrap(payload), "https://www.facebook.com/testpage")
    assert len(posts) == 1
    assert posts[0].post_id == "555444333"
    assert posts[0].text == "Page announcement post"
    assert posts[0].likes == 42


def test_graphql_deep_connection_discovery_fallback():
    """Test Layer 2 connection discovery fallback for arbitrary nested feed connections."""
    payload = {
        "data": {
            "serp_response": {
                "results": {
                    "edges": [
                        {
                            "node": {
                                "post_id": "777666555",
                                "creation_time": 1700001000,
                                "message": {"text": "Search result feed post"},
                            }
                        }
                    ]
                }
            }
        }
    }
    posts = extract_posts_from_graphql(_wrap(payload), "https://www.facebook.com/search")
    assert len(posts) == 1
    assert posts[0].post_id == "777666555"
    assert posts[0].text == "Search result feed post"


def test_graphql_ignores_generic_json_without_story_signals():
    """Test Layer 3 validation rejects non-story objects carrying post_id as a subfield."""
    payload = {
        "data": {
            "user_config": {
                "post_id": "123",
                "settings": {"theme": "dark"},
            }
        }
    }
    posts = extract_posts_from_graphql(_wrap(payload), "https://www.facebook.com/test")
    assert len(posts) == 0