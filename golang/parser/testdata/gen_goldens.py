#!/usr/bin/env python3
"""Regenerate golden outputs for golang/parser (_test.go) from the REAL Python.

Byte truth, not belief: every golden in this directory is produced by running
backend/scraper/parser.py (the single source of truth) against a real HTML
sample.  The Go parser port must reproduce these outputs exactly.

Usage:
    ./.venv/bin/python golang/parser/testdata/gen_goldens.py

Outputs (compact JSON, ensure_ascii=False, one document per post):
    parse_page_browser_snapshot.json  parse_page() posts from
                                      shared/fixtures/browser_snapshot.html
    graphql_browser_snapshot.json     extract_posts_from_graphql() same HTML
    graphql_story_photo.json          extract_posts_from_graphql() wrapped
                                      tests/fixtures/graphql_story_photo.json
    parse_page_dom_sample.json        parse_page() on a crafted DOM sample
    script_fallback.json              _extract_posts_from_scripts() fallback
    timestamps.json                   parse_timestamp() battery (fixed `now`)
    counts.json                       parse_count() battery

After changing parser.py DO NOT hand-edit these files: re-run this script,
then make the Go port match.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]

sys.path.insert(0, str(REPO))

from backend.scraper.parser import (  # noqa: E402
    parse_count,
    parse_page,
    parse_timestamp,
)
from backend.scraper.parser import _extract_posts_from_scripts  # noqa: E402
from backend.scraper.parser import extract_posts_from_graphql  # noqa: E402

CLOCK = datetime(2026, 9, 21, 10, 0, 0, tzinfo=timezone.utc)

FB = "https://www.facebook.com"


def _dump(posts) -> str:
    out = []
    for p in posts:
        d = p.to_dict()
        d["published_at"] = (
            d["published_at"].isoformat() if d["published_at"] else None
        )
        out.append(json.dumps(d, ensure_ascii=False, separators=(",", ":")))
    return "\n".join(out) + ("\n" if out else "")


def _dump_meta(page) -> str:
    """Page-level fields only (key order mirrors ParsedPage with posts/errors dropped)."""
    meta = {
        "page_name": page.page_name,
        "page_id": page.page_id,
        "profile_url": page.profile_url,
        "og_image": page.og_image,
    }
    return json.dumps(meta, ensure_ascii=False, separators=(",", ":")) + "\n"


def main() -> None:
    # 1. browser_snapshot.html via parse_page (DOM path) and via graphql.
    snapshot = (REPO / "shared" / "fixtures" / "browser_snapshot.html").read_text()
    snap_page = parse_page(snapshot, FB + "/NASA", now=CLOCK, handle="NASA")
    (HERE / "parse_page_browser_snapshot.json").write_text(_dump(snap_page.posts))
    (HERE / "parse_page_browser_snapshot_meta.json").write_text(_dump_meta(snap_page))
    (HERE / "graphql_browser_snapshot.json").write_text(_dump(
        extract_posts_from_graphql(snapshot, FB + "/NASA")))

    # 2. graphql_story_photo.json wrapped in the marker script tag.
    story = json.loads(
        (REPO / "tests" / "fixtures" / "graphql_story_photo.json").read_text())
    wrapped = ('<script type="application/json" data-fb-graphql-feed="1">'
               + json.dumps(story) + "</script>")
    (HERE / "graphql_story_photo.json").write_text(_dump(
        extract_posts_from_graphql(wrapped, FB + "/testpage")))

    # 3. Crafted DOM sample (og meta, data-href post id, data-utime epoch,
    #    aria-label engagement, content image + oembed link card, external
    #    anchor, @mention text, ignorable "more" anchor).
    dom_sample = """<html><head>
<meta property="og:title" content="Acme Widgets | Facebook">
<meta property="og:image" content="https://imgur.acme/og.jpg">
<meta property="og:url" content="https://www.facebook.com/profile.php?id=424242">
<title>Acme Widgets | Facebook</title>
</head><body>
<div role="article" data-href="https://www.facebook.com/story.php?story_fbid=515151&amp;id=424242">
<abbr data-utime="1700000960">Sep 14 at 2:29 PM</abbr>
<div data-ad-preview="message">
Hello @acme_fans world from <a href="https://acme.example/official?src=1">acme.example</a> &mdash; big news #acme
<span aria-label="1.2K views"></span>
<span aria-label="All reactions: 15"></span>
<span aria-label="3 Comments"></span>
<span aria-label="1 Share"></span>
</div>
<div>
<img src="https://scontent.xx/fbcdn/photo1.jpg" width="640" height="480">
<a class="oembed" href="https://acme.example/"><img src="https://imgur.acme/thumb.jpg" width="120" height="90"></a>
</div>
<a href="/posts/515151">More</a>
</div>
</body></html>"""
    (HERE / "dom_sample.html").write_text(dom_sample)
    page = parse_page(dom_sample, FB + "/acmewidgets", now=CLOCK,
                      handle="acmewidgets")
    (HERE / "parse_page_dom_sample.json").write_text(_dump(page.posts))
    (HERE / "parse_page_dom_sample_meta.json").write_text(_dump_meta(page))

    # 4. Script fallback path (NO DOM roots — pure embedded JSON).
    script_html = """<html><body><script type="application/json">{"post_id":"999","creation_time":1700000000,"message":{"text":"Script fallback post"},"reaction_count":{"count":5},"comment_count":{"count":2},"share_count":{"count":1},"__typename":"Photo","uri":"https:\\/\\/scontent.xx\\/fbcdn\\/thumb2.jpg"}</script></body></html>"""
    (HERE / "script_fallback.html").write_text(script_html)
    (HERE / "script_fallback.json").write_text(_dump(
        _extract_posts_from_scripts(script_html, FB + "/acmewidgets",
                                    now=CLOCK)))

    # 5. parse_timestamp battery (fixed clock above).
    ts_inputs = [
        "just now", "Just Posted", "3 h", "2 days ago", "1 w ago",
        "August 2, 2024 at 8:00 AM", "Sep 14 at 2:29 PM",
        "December 25 at 8:00 AM", "Yesterday at 5:30 PM",
        "31 August at 21:29", "1700000000", "2024-08-02T08:11:22+05:30",
        "2024-08-02 08:11:22", "2 March 2025", "1.5 h", "3 months ago",
        "42 minutes", "12/25/2024", "Now", "just shared",
    ]
    ts_rows = []
    for s in ts_inputs:
        dt = parse_timestamp(s, now=CLOCK)
        ts_rows.append([s, dt.isoformat() if dt else None])
    (HERE / "timestamps.json").write_text(
        json.dumps(ts_rows, ensure_ascii=False) + "\n")

    # 6. parse_count battery.
    cnt_inputs = [
        "1.2K likes", "3.4M", "1,234", "42 Comments", "Share",
        "All reactions: 15", "1.2K views", "15 shares", "12k", "0.5K",
        "7", "a 2.5K b", "2.5Kb", "",
    ]
    cnt_rows = [[s, parse_count(s)] for s in cnt_inputs]
    (HERE / "counts.json").write_text(json.dumps(cnt_rows) + "\n")

    print("goldens regenerated in", HERE)


if __name__ == "__main__":
    main()