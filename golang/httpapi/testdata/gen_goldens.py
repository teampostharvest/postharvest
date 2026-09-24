#!/usr/bin/env python3
"""Regenerate golden ParseResponse bytes for golang/httpapi (_test.go).

Byte truth, not belief: each golden is produced by running the REAL Python
pipeline the worker mirrors — parser.parse_page / extract_posts_from_graphql
-> normalizer.normalize_post -> dedup.dedup_posts -> json.dumps.  The Go
handler must reproduce these outputs byte-for-byte over HTTP.

Usage:
    ./.venv/bin/python golang/httpapi/testdata/gen_goldens.py

Outputs (ParseResponse JSON, json.dumps ensure_ascii=False default
separators — the exact bytes golang/httpapi emits):
    parse_response_dom_sample.json              content_type "html"
    parse_response_browser_snapshot_html.json   content_type "html"
    parse_response_browser_snapshot_graphql.json content_type "graphql_json"

After changing parser.py/normalizer.py/dedup.py DO NOT hand-edit these
files: re-run this script, then make the Go HTTP layer match.
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
    extract_posts_from_graphql,
    parse_page,
)
from backend.scraper.normalizer import normalize_post  # noqa: E402
from backend.scraper.dedup import dedup_posts  # noqa: E402

# Fixed clock — HTTP-layer goldens are deterministic.  Must match the clock
# golang/httpapi/httpapi_test.go injects.
NOW = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)

FB = "https://www.facebook.com"


def dump_response(posts, post_errors) -> str:
    """All posts are already normalized; dedup keeps first occurrences."""
    kept, _duplicates = dedup_posts(posts)
    errors = [json.dumps(e, ensure_ascii=False) for e in post_errors]
    return json.dumps({"posts": kept, "errors": errors}, ensure_ascii=False)


def normalize_all(parsed_posts, page_name, page_id, facebook_url):
    return [
        normalize_post(p, page_name=page_name, page_id=page_id,
                       facebook_url=facebook_url, now=NOW)
        for p in parsed_posts
    ]


def main() -> None:
    dom_sample = (REPO / "golang" / "parser" / "testdata" / "dom_sample.html") \
        .read_text()
    snapshot = (REPO / "shared" / "fixtures" / "browser_snapshot.html") \
        .read_text()

    # 1. dom_sample via parse_page (html path), page context from
    #    shared/fixtures/parse_request.html.json.
    req = json.loads(
        (REPO / "shared" / "fixtures" / "parse_request.html.json").read_text())
    page = parse_page(dom_sample, page_url=req["target_url"],
                      now=NOW, handle=req["handle"])
    posts = normalize_all(page.posts, page.page_name, page.page_id,
                          req["target_url"])
    (HERE / "parse_response_dom_sample.json").write_text(
        dump_response(posts, page.post_errors))

    # 2. browser_snapshot via parse_page (html path) — script fallback.
    page = parse_page(snapshot, page_url=FB + "/NASA", now=NOW, handle="NASA")
    posts = normalize_all(page.posts, page.page_name, page.page_id,
                          FB + "/NASA")
    (HERE / "parse_response_browser_snapshot_html.json").write_text(
        dump_response(posts, page.post_errors))

    # 3. browser_snapshot via extract_posts_from_graphql (graphql_json
    #    path) — page_name/page_id are null, facebook_url = target_url.
    gql = extract_posts_from_graphql(snapshot, FB + "/NASA")
    posts = normalize_all(gql, None, None, FB + "/NASA")
    (HERE / "parse_response_browser_snapshot_graphql.json").write_text(
        dump_response(posts, []))

    print("httpapi goldens regenerated in", HERE)


if __name__ == "__main__":
    main()