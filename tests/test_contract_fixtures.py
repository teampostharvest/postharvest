"""Cross-language golden fixtures (finalplanv2.md §6, "Critical Note 8").

Hermetic, no network: FastAPI asserts the shared/fixtures payloads so the
proto, the node service and the backend cannot silently drift apart.  Any
change must follow the editing order in shared/fixtures/README.md
(proto -> node/src/types.ts -> fixtures).
"""

import base64
import json
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "shared" / "fixtures"

FETCH_RESPONSE_KEYS = {
    "status_code",
    "final_url",
    "content_type",
    "raw_payload",
    "fetched_at_ms",
    "updated_cookies",
    "browser_stats",
}
FETCH_REQUEST_KEYS = {
    "target_url",
    "mode",
    "account_id",
    "scroll_rounds",
    "max_posts",
    "cookies",
}
BROWSER_STATS_KEYS = {"login_wall", "feed_missing", "posts_found"}


def _load_json(name: str) -> dict:
    with (FIXTURES / name).open("r", encoding="utf-8") as fh:
        return json.load(fh)


def test_all_fixtures_are_valid_json():
    for path in sorted(FIXTURES.glob("*.json")):
        with path.open("r", encoding="utf-8") as fh:
            json.load(fh)


def test_http_fetch_response_is_the_canonical_shape():
    fixture = _load_json("fetch_response.html.json")
    assert set(fixture) == FETCH_RESPONSE_KEYS
    assert fixture["content_type"] == "text/html"
    # proto3 JSON defaults for the browser-only fields (§6): repeated -> [],
    # message -> null.
    assert fixture["updated_cookies"] == []
    assert fixture["browser_stats"] is None
    html = base64.b64decode(fixture["raw_payload"]).decode("utf-8")
    assert len(html) > 0


def test_browser_fetch_response_decodes_to_the_snapshot_asset():
    fixture = _load_json("fetch_response.browser.json")
    assert set(fixture) == FETCH_RESPONSE_KEYS
    assert fixture["content_type"] == "text/html"
    assert isinstance(fixture["updated_cookies"], list)
    assert len(fixture["updated_cookies"]) > 0
    assert all(isinstance(c, str) for c in fixture["updated_cookies"])

    stats = fixture["browser_stats"]
    assert set(stats) == BROWSER_STATS_KEYS
    assert isinstance(stats["login_wall"], bool)
    assert isinstance(stats["feed_missing"], bool)
    assert isinstance(stats["posts_found"], int)
    assert stats == {"login_wall": False, "feed_missing": False, "posts_found": 2}

    # raw_payload is base64 of browser_snapshot.html, byte-for-byte.
    snapshot = (FIXTURES / "browser_snapshot.html").read_bytes()
    decoded = base64.b64decode(fixture["raw_payload"])
    assert decoded == snapshot

    text = decoded.decode("utf-8")
    # Markers the Python parser consumes (extract_posts_from_graphql reads
    # these script blocks; the missing-feed marker flags a walled view).
    assert 'data-fb-graphql-feed="1"' in text
    assert '"post_id":"2001"' in text
    assert "<!-- fb-scrape-feed-missing -->" not in text


def test_browser_fetch_request_shape():
    fixture = _load_json("fetch_request.browser.json")
    assert set(fixture) == FETCH_REQUEST_KEYS
    assert fixture["mode"] == "browser"
    assert fixture["account_id"] == "ops:maverick"
    assert fixture["scroll_rounds"] == 12
    assert fixture["max_posts"] == 0
    assert fixture["cookies"] == [
        "c_user=100000000000001; Domain=.facebook.com; Path=/; HttpOnly",
        "xs=abc123def456; Domain=.facebook.com; Path=/; Secure; HttpOnly",
    ]