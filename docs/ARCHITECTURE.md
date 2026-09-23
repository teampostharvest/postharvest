# Architecture

How PostHarvest is put together: request flow, the background job model, the
database, and the scraper internals. The code-understanding companion to
[API.md](./API.md).

## Request flow at a glance

```
POST /api/scrape {urls: [...]}
   │  validated (pydantic) → URLs semantically validated by scraper
   ▼
start_scrape_job()
   │  invalid URLs → recorded as error_details (job still runs)
   ▼
ScrapeJob row (status=queued)  ──►  POST returns 201 {job_id, status:"queued"} (fast)
   │
   ▼
JobManager (process-wide ThreadPoolExecutor, default 4 workers)
   ▼
run_scrape_job(job_id)  ── per-source state machine ──►  CrawlState checkpoints
   ▼
scrape_source(url, options, progress_cb, cancel)       (backend/scraper/)
   ▼
fetch ──► [offload seams: USE_NODE fetch, USE_GO_WORKER compute]
   ▼
Parse → normalize → dedup → cap  ──►  Post rows + EngagementMetric + Media
   │
   ▼
job.status = completed | failed     GET /api/jobs/{job_id} reflects live counters
```

`POST /api/scrape` returns **immediately** (201 + `job_id`); all heavy work
happens on worker threads. Poll `GET /api/jobs/{job_id}` (frontend polls every
1500 ms) for progress.

## Backend layout

```
backend/
├── main.py            # create_app(): lifespan (logging→ensure_dirs→init_db),
│                      # exception handlers, CORSMiddleware, routers
├── api/               # route modules, one file per area:
│   ├── health.py      #   GET /api/health
│   ├── scrape.py      #   POST /api/scrape
│   ├── jobs.py        #   GET/PATCH/DELETE /api/jobs{/id}...
│   ├── exports.py     #   GET /api/jobs/{id}/export/{fmt}
│   └── accounts.py    #   GET/DELETE /api/accounts
├── core/
│   ├── config.py      # pydantic-settings Settings singleton (all env knobs)
│   ├── database.py    # engine + SessionLocal + Base + get_db; init_db → create_all
│   ├── job_manager.py # process-wide ThreadPoolExecutor + per-job CancelToken
│   ├── logging.py     # stdout logger under postharvest.* namespace
│   └── exceptions.py  # AppError hierarchy → {"error":{code,message}} envelope
├── models/            # 8 SQLAlchemy ORM models (see table below)
├── schemas/           # pydantic: health, scrape, jobs
├── services/
│   ├── job_service.py       # job orchestration + worker entry run_scrape_job
│   ├── crawl_state_service.py
│   ├── export_service.py    # builds exports, records export_jobs audit rows
│   ├── serialization.py     # ORM row ↔ canonical post dict
│   └── stats.py             # KPI aggregation for /stats
├── scraper/           # extraction layer (see below)
└── exporters/         # json / csv / jsonl / xlsx (+ safety.py path guards)
```

Streaming convention: route handlers stay thin; services own the work; the
error contract is centralized in `core/exceptions.py`.

## Job model

- **Job** = one scraping run. States: `queued → running → completed | failed`
  (plus `paused` while queued/running). Aggregated counters on the row:
  `pages_total/pages_completed`, `posts_found/processed/skipped/failed`,
  `duplicates`, `errors_count`; `options` is a JSON snapshot of the request.
- **Source** = one validated URL within a job. Own state machine
  (`queued|running|completed|failed|cancelled`); **one source failure never
  fails the whole job**.
- **Execution:** `run_scrape_job` runs in the pool thread; per-job
  `CancelToken` wraps a `threading.Event`, honored between sources and
  forwarded into the pagination loop.
- **Cancellation** (DELETE /api/jobs/{id}): sets `cancel_requested`, asks the
  manager to cancel with up to `CANCEL_WAIT_SECONDS` grace, then deletes the
  row + dependents (cascade). A still-running worker simply finds rows gone and
  stops writing.
- **Pause/resume:** pause only from `queued|running` (else 409), worker stops
  after the current source; resume re-queues from the `CrawlState` checkpoint.
  *Note: the worker does not actively poll `paused` — the status is
  API-layer driven.*
- **Startup sweep** for orphaned `running` jobs: **planned**, not yet
  implemented (see ROADMAP § 4).

## Database

Engine default: **SQLite** (`sqlite:///./data/postharvest.db`, WAL + foreign
keys + busy_timeout). PostgreSQL is a pure DSN switch
(`postgresql+psycopg://...`); all models use portable SQLAlchemy types.

Currently `init_db()` runs `Base.metadata.create_all` at startup — **Alembic
migrations are planned** to replace this (ROADMAP § 2).

| Model | Table | Purpose |
|---|---|---|
| `ScrapeJob` | `scrape_jobs` | one row per run; lifecycle + counters + options JSON |
| `ScrapeSource` | `sources` | per-URL status/counters; unique `(job_id, normalized_url)` |
| `Post` | `posts` | normalized post; `dedup_key` unique per source, index on `post_id`, `page_id`, `published_at` |
| `Media` | `media` | one primary media row per post (+ extras for lists) |
| `EngagementMetric` | `engagement_metrics` | 0..1 per post: likes/reactions/comments/shares/views + 7 reaction buckets |
| `ScrapeError` | `errors` | per-validation/source/post/cancel failure |
| `CrawlState` | `crawl_states` | per-source resume checkpoint (status, cursor, meta) |
| `ExportJob` | `export_jobs` | audit row per export (pending → completed/failed) |

## Scraper internals

Public contract (`backend/scraper/__init__.py`): `validate_facebook_url(url)` +
`scrape_source(url, options, progress_cb, cancel)` returning a `SourceResult`.

- **`fetcher.py`** — compliance-first httpx fetcher: robots.txt enforcement
  (`urllib.robotparser`; disallowed → `robots_disallowed`), hard throttle
  (default 2.5 s, floor 0.1), exponential backoff (≤3 retries, honors
  `Retry-After`), 20 s timeout, single honest Chrome UA, **never sends
  cookies**.
- **`browser_scraper.py`** — Playwright headless Chromium; scroll rounds
  (default 40, 1.5 s between), cookie injection from
  `data/fb_cookies{,_<account>}.json` (+ index `data/fb_credentials.json`),
  popup dismissal, login-event handling.
- **`crawler.py`** — transport-agnostic orchestrator over a `Transport`
  protocol: validate → fetch → parse → normalize → dedup → cap.
- **`adapters/facebook.py`** — the two transports:
  `FacebookHttpTransport` (single fetch) and `FacebookBrowserTransport`
  (scrolling + cookies). Factory: `make_facebook_transport()`.
- **`parser.py`** — HTML parsing incl. `extract_posts_from_graphql`
  (Comet `data-fb-graphql-feed="1"` script blocks), timestamp parsing.
- **`normalizer.py`** — `normalize_post` → the canonical **33-key** dict.
  Every key always present; unavailable fields are `None`/`[]`, never
  fabricated (see [API.md](./API.md) for the key list).
- **`dedup.py`** — primary key `post_id`; fallback `fp:<sha256>` of
  `page_id | published_at | text[:200]`; first occurrence wins;
  `normalize_post_url` strips tracking params.
- **`rate_limiter.py`** — token bucket (0.4 tok/s = 1 req/2.5 s, burst 1) +
  `RetryManager` circuit breaker (`CLOSED/HALF_OPEN/OPEN`).
- **`proxy_manager.py`** — optional rotation + health checks,
  `fallback_to_direct=True` by default.
- **`pagination.py`** — `PageFetcher` protocol; fetch→extract→paginate loop
  with stale/max-rounds/cancel handling.
- **`url_validator.py`** — accepts `fb.com` page/profile shapes only; rejects
  login/consent/watch/events/groups/post URLs; never raises.
- **`errors.py`** — `ScraperError` taxonomy: `InvalidUrl`, `UnsupportedUrl`,
  `PageUnavailable`, `AuthRequired`, `RateLimited`, `Timeout`,
  `ExtractionFailure`, `OperationCancelled`.

## Compute offload seams (node fetch / go compute)

The in-process Python pipeline above remains the **source of truth** and the
**default** — the "flag-off" path is byte-untouched by the offload work.
Two opt-in seams (both `false` by default, `backend/core/config.py`)
delegate slices of an HTTP-mode scrape to sibling services, behind the same
flag discipline as any other risky cutover:

| Flag | Default | Delegates | Sibling service |
|---|---|---|---|
| `USE_NODE` | `0` | the **fetch** slice (robots/throttle/retry) | `node/` — Fastify + undici + Playwright, `http://node:9334` |
| `USE_GO_WORKER` | `0` | the **compute** slice (parse → normalize → dedup) | `golang/` — Go, `POST /v1/parse`, `http://go:8080` |

Both seams keep **fault isolation**: a sibling outage (or timeout) is a
per-source error recorded on that source (`network_error`), never a job
crash — the Google-town error taxonomy (`ScraperError`) is mapped 1:1 by
each client (`backend/services/node_fetch.py`, `backend/services/go_worker.py`).

**Contract boundary.** The Go worker's wire contract lives in
`shared/proto/postharvest.proto` (ParseRequest/ParseResponse) with
`shared/fixtures/` as the byte fixtures, and the Go side is byte-proven
against the *real* Python pipeline: `gen_goldens.py` scripts run
`parse_page → normalize_post → dedup_posts → json.dumps` to produce the
exact golden `ParseResponse` bytes the Go handler must reproduce (and its
hermetic `httptest` proofs do). Editing order is contract → types → Go →
fixtures; the Go worker (`golang/`, M1–M6) is stateless compute — no
Facebook calls, no DB — with its own `idempotency/` Store seam for the
§8(c) retry cache (`idemp:` keyspace; in-memory for hermetic proofs,
Redis-backed via the vendored `go-redis` at deployment).

**Deployables** (M6) exist in code with **zero live exposure**: the §15
gate still holds for anything operational. `golang/cmd/server` is the one
binary (env: `PORT`, optional `REDIS_URL` for the idempotency cache),
`golang/Dockerfile` builds it statically (offline, `-mod=vendor`), and the
compose stack adds an internal `go:` service on the `isolated` network
(`http://go:8080`). The M7 flagged client (`backend/services/go_worker.py`)
plus its parity suite (`tests/test_go_worker_seam.py`) prove the flagged
path returns byte-identical posts/stats to the flag-off path before anyone
flips the switch.

## Exports

`export_posts(...)` supports `json | csv | excel | jsonl` (`excel`/`xlsx`
alias). Output: `<EXPORT_BASE_DIR>/<job_id>/<allowed-filename>` where
filenames come from a fixed allow-list (`safety.py`) and path traversal is
blocked. Formats:

- **JSON** — lossless nested array, streamed post-by-post.
- **CSV** — UTF-8 BOM, 28 flat columns, lists joined with `|`.
- **XLSX** — openpyxl workbook: Posts / Engagement / Media / Metadata sheets.
- **JSONL** — one normalized object per line.

The HTTP layer only whitelists `json | csv | excel` (see [API.md](./API.md)).

## Frontend

Next.js 16 (App Router) + React 18 + TypeScript + Tailwind 3.4, `output:
"standalone"`. Route group `app/(app)/`: `/` dashboard, `accounts/`,
`history/`, `investigation/`, `settings/`, `docs/`. Key lib files:
`lib/api.ts` (typed client; `API_BASE` from `NEXT_PUBLIC_API_URL`), `hooks.ts`
(1500 ms job polling), `settings.ts` (localStorage scrape defaults),
`docs-meta.ts` (docs registry). JWT/auth headers: not yet present (planned
with multitenancy).

## Multi-tenancy direction (planned)

Currently a single-operator app. ROADMAP/DECISIONS define the target:
`owner_id` on every row, Firebase ID-token verification on every `/api` route,
encrypted per-owner FB sessions, Alembic migrations, quotas via a `plan`
resolver. **None of this exists in the code yet** — today every request is
shared and unauthenticated.