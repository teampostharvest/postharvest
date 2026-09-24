"""Group 5 — API endpoint contracts (POST /api/scrape, GET /api/jobs/...).

Request/response shape tests: 201 {job_id, status:queued}, 400 invalid URL
envelope, 422 validation errors (spec §17), 404 unknown job, DELETE 204 ->
404, GET /api/health.  Pagination is tested against jobs seeded directly
through the models; "full mocked scrape" correctness lives in
test_job_state_machine.py.
"""
from __future__ import annotations

import time

from helpers import (
    PAGE_URL,
    error_envelope,
    insert_completed_job,
    install_fake_scraper,
    make_source_result,
    sample_posts,
    wait_for_job,
)

VALID_URLS = ["https://www.facebook.com/example"]


def test_health_ok(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"


def test_scrape_valid_returns_201_queued(authed_client, monkeypatch):
    install_fake_scraper(
        monkeypatch,
        results=[make_source_result(PAGE_URL, posts=sample_posts(1))],
    )
    resp = authed_client.post(
        "/api/scrape", json={"urls": VALID_URLS, "post_type": "text"}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "queued"
    assert isinstance(body["job_id"], str) and body["job_id"]
    # let the worker finish so nothing leaks past the monkeypatch
    wait_for_job(authed_client, body["job_id"])


def test_scrape_invalid_urls_400_envelope(authed_client):
    resp = authed_client.post("/api/scrape", json={"urls": ["not a url"]})
    assert resp.status_code == 400
    err = error_envelope(resp.json())
    assert err["code"] == "invalid_input"
    assert err["message"]


def test_scrape_empty_urls_rejected(authed_client):
    for payload in ({"urls": []}, {"urls": ["   "]}):
        resp = authed_client.post("/api/scrape", json=payload)
        assert resp.status_code == 400, f"payload {payload!r} accepted?!"
        error_envelope(resp.json())


def test_scrape_partially_invalid_records_error(authed_client, monkeypatch):
    install_fake_scraper(
        monkeypatch,
        results=[make_source_result(PAGE_URL, posts=sample_posts(1))],
    )
    resp = authed_client.post(
        "/api/scrape",
        json={
            "urls": [PAGE_URL, "https://example.com/not-facebook"],
            "post_type": "text",
        },
    )
    assert resp.status_code == 201, resp.text
    job_id = resp.json()["job_id"]
    final = wait_for_job(authed_client, job_id)
    assert final["errors"] >= 1
    codes = {e["code"] for e in final["error_details"]}
    assert "invalid_url" in codes
    assert final["pages_total"] == 1


def test_scrape_bad_date_returns_400(authed_client):
    """Spec §17: bad dates -> 400 (app error envelope convention)."""
    resp = authed_client.post(
        "/api/scrape",
        json={"urls": VALID_URLS, "start_date": "01/08/2026"},
    )
    assert resp.status_code == 400, (
        f"expected 400 for malformed ISO date, got {resp.status_code}: {resp.text}"
    )
    error_envelope(resp.json())


def test_scrape_bad_end_date_order_returns_400(authed_client):
    resp = authed_client.post(
        "/api/scrape",
        json={"urls": VALID_URLS, "start_date": "2026-09-01", "end_date": "2026-01-01"},
    )
    assert resp.status_code == 400, (
        f"expected 400 for start>end, got {resp.status_code}: {resp.text}"
    )
    error_envelope(resp.json())


def test_scrape_bad_post_type_returns_400(authed_client):
    resp = authed_client.post(
        "/api/scrape",
        json={"urls": VALID_URLS, "post_type": "gif"},
    )
    assert resp.status_code == 400, (
        f"expected 400 for unknown post_type, got {resp.status_code}: {resp.text}"
    )
    error_envelope(resp.json())


def test_scrape_max_posts_bounds_400(authed_client):
    resp = authed_client.post("/api/scrape", json={"urls": VALID_URLS, "max_posts": 0})
    assert resp.status_code == 400, (
        f"expected 400 for max_posts=0, got {resp.status_code}: {resp.text}"
    )


def test_scrape_missing_urls_field_400(authed_client):
    resp = authed_client.post("/api/scrape", json={})
    assert resp.status_code == 400
    err = error_envelope(resp.json())
    assert err["code"] == "validation_error"


def test_get_unknown_job_404(authed_client):
    resp = authed_client.get("/api/jobs/definitely-not-a-job")
    assert resp.status_code == 404
    err = error_envelope(resp.json())
    assert err["code"] == "not_found"


def test_job_status_shape_when_inserted_directly(authed_client):
    job_id = insert_completed_job(sample_posts(2), pages_total=1, pages_completed=1)
    resp = authed_client.get(f"/api/jobs/{job_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == job_id
    assert body["status"] == "completed"
    assert body["pages_total"] == 1
    assert body["pages_completed"] == 1
    assert body["posts_processed"] == 2
    assert body["posts_found"] == 2
    assert body["duplicates"] == 0
    assert "error_details" in body
    assert body["started_at"] is not None
    assert body["completed_at"] is None  # test helper doesn't set this
    assert body["max_posts"] is None
    assert len(body["sources"]) == 1
    src = body["sources"][0]
    assert src["status"] == "completed"
    assert src["url"] == "https://www.facebook.com/example"
    assert src["posts_found"] == 2
    assert src["posts_processed"] == 2


def test_job_status_sources_reflect_running_sources(authed_client):
    from helpers import PAGE_URL

    job_id = insert_completed_job(sample_posts(1), status="running")
    resp = authed_client.get(f"/api/jobs/{job_id}")
    assert resp.status_code == 200
    src = resp.json()["sources"][0]
    assert src["status"] == "running"
    assert src["posts_found"] == 1
    assert src["posts_processed"] == 0
    assert src["url"] == PAGE_URL


def test_posts_pagination(authed_client):
    posts = sample_posts(5)
    job_id = insert_completed_job(posts)

    r1 = authed_client.get(f"/api/jobs/{job_id}/posts?page=1&page_size=2")
    assert r1.status_code == 200, r1.text
    b1 = r1.json()
    assert b1["total"] == 5
    assert b1["page"] == 1
    assert b1["page_size"] == 2
    assert len(b1["items"]) == 2

    r2 = authed_client.get(f"/api/jobs/{job_id}/posts?page=3&page_size=2")
    b2 = r2.json()
    assert len(b2["items"]) == 1
    assert b2["total"] == 5

    # newest first by published_at (2026-08-03 > 08-02 > 08-01 ...)
    assert b1["items"][0]["published_at"] > b1["items"][1]["published_at"]

    r3 = authed_client.get(f"/api/jobs/{job_id}/posts?page=99&page_size=2")
    assert r3.json()["items"] == []


def test_posts_pagination_defaults_and_caps(authed_client):
    job_id = insert_completed_job(sample_posts(3))
    resp = authed_client.get(f"/api/jobs/{job_id}/posts")
    body = resp.json()
    assert body["page"] == 1 and body["page_size"] == 50
    assert len(body["items"]) == 3

    # page_size > 200 is clamped/rejected by the API (max 200)
    resp200 = authed_client.get(f"/api/jobs/{job_id}/posts?page_size=500")
    assert resp200.status_code == 400  # FastAPI Query le=200 -> validation error
    error_envelope(resp200.json())


def test_posts_unknown_job_404(authed_client):
    resp = authed_client.get("/api/jobs/nope/posts")
    assert resp.status_code == 404
    error_envelope(resp.json())


def test_stats_bonus_endpoint(authed_client):
    job_id = insert_completed_job(sample_posts(3))
    resp = authed_client.get(f"/api/jobs/{job_id}/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_posts"] == 3
    assert body["total_likes"] == 36  # 12 per post
    assert body["post_type_counts"]["text"] == 1
    assert body["post_type_counts"]["image"] == 1
    assert body["post_type_counts"]["video"] == 1


def test_delete_job_204_then_404(authed_client, monkeypatch):
    install_fake_scraper(
        monkeypatch,
        results=[make_source_result(PAGE_URL, posts=sample_posts(1))],
    )
    resp = authed_client.post(
        "/api/scrape", json={"urls": VALID_URLS, "post_type": "text"}
    )
    job_id = resp.json()["job_id"]
    wait_for_job(authed_client, job_id)  # finish first -> deterministic delete

    resp_delete = authed_client.delete(f"/api/jobs/{job_id}")
    assert resp_delete.status_code == 204, resp_delete.text
    assert authed_client.get(f"/api/jobs/{job_id}").status_code == 404
    assert authed_client.get(f"/api/jobs/{job_id}/posts").status_code == 404


def test_list_jobs_empty(authed_client):
    resp = authed_client.get("/api/jobs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0


def test_list_jobs_returns_history_newest_first(authed_client):
    job_a = insert_completed_job(
        sample_posts(1),
        options={"urls": [PAGE_URL], "max_posts": 10, "post_type": "text"},
    )
    job_b = insert_completed_job(
        sample_posts(1),
        options={"urls": [PAGE_URL], "max_posts": 5, "post_type": "all"},
    )
    resp = authed_client.get("/api/jobs")
    assert resp.status_code == 200
    body = resp.json()
    ids = [item["job_id"] for item in body["items"]]
    assert job_b in ids and job_a in ids
    assert len(body["items"]) == body["total"]

    summary = body["items"][0]
    assert set(summary) >= {
        "job_id",
        "status",
        "pages_total",
        "pages_completed",
        "posts_found",
        "posts_processed",
        "duplicates",
        "errors",
        "urls",
        "max_posts",
        "post_type",
        "created_at",
        "completed_at",
    }
    assert summary["status"] == "completed"
    assert summary["urls"] == [PAGE_URL]


def test_list_jobs_pagination(authed_client):
    for _ in range(3):
        insert_completed_job(sample_posts(1))
    resp = authed_client.get("/api/jobs?page=1&page_size=2")
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2
    assert resp.json()["page_size"] == 2

    resp2 = authed_client.get("/api/jobs?page=2&page_size=2")
    assert len(resp2.json()["items"]) == 1


def test_list_jobs_status_filter(authed_client):
    running = insert_completed_job(sample_posts(1), status="running")
    insert_completed_job(sample_posts(1), status="completed")

    resp = authed_client.get("/api/jobs?status=running")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert [item["job_id"] for item in body["items"]] == [running]


def test_list_jobs_status_filter_multi_and_invalid(authed_client):
    insert_completed_job(sample_posts(1), status="queued")
    insert_completed_job(sample_posts(1), status="completed")

    resp = authed_client.get("/api/jobs?status=queued,running")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1

    bad = authed_client.get("/api/jobs?status=bogus")
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "invalid_input"


def test_list_accounts_empty_and_404_delete(authed_client, client):
    resp = client.get("/api/accounts")  # unauthenticated -> 401
    assert resp.status_code == 401

    resp = authed_client.get("/api/accounts")
    assert resp.status_code == 200
    body = resp.json()
    assert "ops" in body and "mine" in body

    resp_delete = authed_client.delete("/api/accounts/me/ghost")
    assert resp_delete.status_code == 404
    error_envelope(resp_delete.json())


def test_delete_unknown_job_404(authed_client):
    resp = authed_client.delete("/api/jobs/ghost")
    assert resp.status_code == 404
    error_envelope(resp.json())