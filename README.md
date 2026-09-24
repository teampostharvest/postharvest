# PostHarvest

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](#license)

A job-based web service that extracts publicly accessible posts from Facebook
pages and profiles — an honest scraper that fetches public HTML over the public
web with a **single, honest user agent**, **respects `robots.txt`**, and
**throttles every request** (2.5 s minimum by default).

Two extraction modes:

- **HTTP mode (default)** — Fast (~3 s), no browser needed, gets 1–2 posts per
  page directly from the initial HTML.
- **Browser mode (`--browser`)** — Uses Playwright headless Chromium to
  execute JavaScript, scroll the page, and load more posts. Supports
  authenticated scraping via saved cookies for full content access.

The project is a **FastAPI backend** with a background job manager, an optional
**Next.js dashboard**, a standalone **CLI**, and **JSON / CSV / XLSX / JSONL**
exports.

> **Compliance framing — read this first.** This tool exists to collect data
> Facebook already publishes to the world. It deliberately **does not** bypass
> authentication, consent screens, rate limits, or anti-bot protections. Using
> it still binds you to Facebook/Meta's Terms of Service, applicable laws, and
> the `robots.txt` / scraping policies of the sites you target. See
> **[COMPLIANCE.md](./COMPLIANCE.md)** for the full permitted-use statement.

---

## Table of contents

- [Features](#features)
- [Quick start](#quick-start)
- [CLI usage](#cli-usage)
- [Tech stack](#tech-stack)
- [Repository structure](#repository-structure)
- [Configuration](#configuration)
- [API reference](#api-reference)
- [Exports](#exports)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [Limitations](#limitations)
- [Compliance summary](#compliance-summary)
- [Documentation](#documentation)

---

## Features

- Scrape multiple Facebook **page / profile URLs** per job
- **Two extraction modes:** fast HTTP scraping, or full browser scraping with
  Playwright + saved-cookie authentication
- Job-based async model with **live progress** (API + dashboard) and
  best-effort cancellation, plus **pause / resume**
- Background worker pool with per-source lifecycles (`queued → running →
  completed | failed | cancelled`)
- **CrawlState checkpointing** — resume a paused or crashed job where it left off
- Normalized **33-key post schema** — missing fields are `null`/`[]`, never
  fabricated
- Date-range, post-type (`text/image/video/link/all`) and `max_posts` filters
- **Two-layer deduplication** (post-id + SHA-256 fingerprint)
- **Configurable proxy support** for all HTTP requests
- Built-in **rate limiter** (token bucket) and **retry manager** (circuit
  breaker, exponential backoff on 429/5xx)
- Exports: **JSON**, **CSV**, **XLSX**, **JSONL** (streaming)
- SQLite out of the box; PostgreSQL via `DATABASE_URL`
- Consistent error envelope `{"error": {"code", "message"}}` on every failure
- Health endpoint with database latency check

## Quick start

The fastest path is the **Docker stack** (one command, everything included).
Prefer running pieces on the host? Paths B–D below.

### Path A — Docker Compose (recommended)

All container files live in `docker/` — compose's project directory. From the
repo root, use the Makefile (or `cd docker` for the raw compose commands):

```bash
make dev        # foreground: backend :8000 + frontend :3000 + postgres, hot reload
```

| URL | What |
|---|---|
| http://localhost:3000 | Dashboard (Next.js) |
| http://localhost:8000/api | API root |
| http://localhost:8000/docs | Swagger UI |
| http://localhost:8000/api/health | Health check |

Production topology (nginx :80/:443 + read-only rootfs hardened layer):

```bash
make prod-up    # or: cd docker && docker compose -f docker-compose.yml \
                #          -f docker-compose.prod.yml --profile prod up -d --build
```

> **Docker notes**
> - **Env split:** compose reads `docker/.env` (template `docker/example.env`);
>   the root `.env.example` is only for the CLI / tests / host apps.
> - **Dev** = writable rootfs + bind mounts + hot reload (auto-loaded override).
>   **Prod** = `docker-compose.prod.yml` layer: read-only rootfs, dropped
>   capabilities, mem/CPU caps. See DECISIONS.md D12.
> - The backend bind-mounts `../data` — the **host CLI and container share one
>   cookie/export store**, so `python cli.py login` sessions appear in the API
>   immediately.
> - The scraper keeps job state in in-process worker threads: the backend must
>   run as a **single replica** behind any reverse proxy.
> - `NEXT_PUBLIC_API_URL` is baked into the frontend JS at build time. Default
>   is empty (= same-origin via nginx `/api/*`); override in `docker/.env`,
>   then `make prod-build`.
> - Linux: add your user to the `docker` group and re-login so `docker compose`
>   works without `sudo` (`sudo usermod -aG docker $USER`).

### Path B — CLI only (simplest)

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt
python -m playwright install chromium               # only for --browser mode

# HTTP mode (fast, 1-2 posts)
python cli.py scrape https://www.facebook.com/<public-page> --export json

# Browser mode (more posts, slower)
python cli.py scrape https://www.facebook.com/<public-page> --browser --max-posts 20 --export xlsx

# Authenticated browser mode (full content)
python cli.py login                          # opens browser, saves cookies
python cli.py scrape <url> --browser         # uses saved cookies
```

### Path C — Backend + frontend on the host

```bash
# Terminal 1 — backend
uvicorn backend.main:app --reload --reload-dir backend --port 8000

# Terminal 2 — frontend (http://localhost:3000)
cd frontend && npm install && npm run dev
```

### Where do the files go?

Runtime state (SQLite DB, exports, saved cookies) lands in `data/` at the repo
root — in Docker this is bind-mounted to the backend, on the host it's the
working directory's `data/`.

## CLI usage

```
python cli.py login                          # Open browser to log into Facebook
python cli.py scrape <url> [url ...] [options]
python cli.py accounts                       # List saved Facebook sessions
```

### Scrape options

| Flag | Default | Description |
|---|---|---|
| `--browser` | off | Use Playwright headless browser (slower, more posts) |
| `--max-posts N` | *(none)* | Max posts per URL (no default cap) |
| `--scrolls N` | 40 | Max scroll rounds in browser mode |
| `--export csv\|json\|jsonl\|xlsx` | *(none)* | Export results to file |
| `--output FILE` | auto | Output file path |
| `--account name` | *(none)* | Session name to use; comma-separated for rotation |
| `--accounts-all` | off | Auto-rotate across all saved sessions |

### Examples

```bash
# Quick HTTP scrape → terminal output
python cli.py scrape https://www.facebook.com/kzsamir849

# Browser scrape with export
python cli.py scrape https://www.facebook.com/ashraful.islam333 \
    --browser --max-posts 30 --scrolls 15 --export xlsx --output results.xlsx

# Scrape multiple pages
python cli.py scrape https://www.facebook.com/page1 https://www.facebook.com/page2 \
    --browser --export json

# Use two saved sessions for rotation (see `python cli.py login --account name`)
python cli.py scrape https://www.facebook.com/page --browser --account acc1,acc2
```

## Tech stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11+ · FastAPI 0.115 · Uvicorn 0.34 · Pydantic v2 |
| Data | SQLAlchemy 2.0 · SQLite WAL (default) / PostgreSQL (optional) |
| Scraper (HTTP) | httpx · BeautifulSoup4 (lxml) · brotli · stdlib `urllib.robotparser` |
| Scraper (Browser) | Playwright 1.62 (Chromium headless) |
| Resilience | RateLimiter (token-bucket) · RetryManager (circuit breaker) · ProxyManager |
| Exports | stdlib `json`/`csv` · openpyxl (XLSX) · streaming JSONL |
| Frontend | Next.js 16 (App Router) · React 18 · TypeScript · Tailwind CSS 3 |
| Ops | Docker (multi-stage) · docker compose · Makefile |

## Repository structure

```
postharvest/
├── cli.py                     # Standalone CLI (login + scrape + accounts)
├── Makefile                   # One command for everything (dev/prod/tests/CLI)
├── docker/                    # ALL container stuff (compose project dir)
│   ├── Dockerfile             # multi-target build (backend, backend-dev, frontend, …)
│   ├── docker-compose.yml     # base topology (dev + prod share this)
│   ├── docker-compose.override.yml  # dev only: hot reload + bind mounts
│   ├── docker-compose.prod.yml      # prod hardening layer (read-only, caps)
│   ├── example.env            # → cp to docker/.env
│   └── nginx/                 # nginx.conf + default.conf
├── deploy/                    # host-side ops: certbot, systemd, runbooks
├── docs/                      # long-form guides (architecture, API, deploy, contributing)
├── examples/                  # Shipped example exports (json, csv, xlsx)
├── .env.example               # CLI / tests / local `next dev` env template
├── DECISIONS.md               # Locked product & infra decisions (D1–D12)
├── COMPLIANCE.md              # Permitted-use + Meta compliance statement
├── README.md
│
├── backend/
│   ├── requirements.txt       # Pinned deps (incl. brotli, playwright)
│   ├── main.py                # FastAPI app factory, CORS, error handlers
│   ├── api/                   # scrape, jobs, exports, accounts, health routers
│   ├── core/                  # config, database, exceptions, job_manager, logging
│   ├── models/                # SQLAlchemy: jobs, sources, posts, engagement, media, errors, crawl_state
│   ├── schemas/               # Pydantic request/response models
│   ├── services/              # job_service, export_service, crawl_state_service, serialization, stats
│   ├── scraper/
│   │   ├── __init__.py        # public contract: validate_facebook_url, scrape_source
│   │   ├── fetcher.py         # compliance-first httpx fetcher (robots, throttle, retries)
│   │   ├── http_client.py     # shared httpx factory, honest UA
│   │   ├── rate_limiter.py    # token-bucket RateLimiter + RetryManager (circuit breaker)
│   │   ├── proxy_manager.py   # optional proxy rotation + health checks
│   │   ├── pagination.py      # generic pagination engine
│   │   ├── crawler.py         # transport-agnostic orchestrator
│   │   ├── adapters/          # Facebook HTTP + Browser transport adapters
│   │   ├── parser.py          # Facebook HTML/GraphQL post parser
│   │   ├── normalizer.py      # 33-key post schema normalization
│   │   ├── dedup.py           # post-id + SHA-256 dedup
│   │   ├── browser_scraper.py # Playwright browser scraper + login walls
│   │   ├── stats.py           # scrape statistics counters
│   │   ├── errors.py          # ScraperError taxonomy
│   │   └── url_validator.py   # Facebook URL validation + normalization
│   └── exporters/
│       ├── __init__.py        # export_posts() dispatcher
│       ├── json_exporter.py   # nested JSON export
│       ├── csv_exporter.py    # flat CSV (UTF-8 BOM)
│       ├── xlsx_exporter.py   # styled 4-sheet XLSX workbook
│       ├── jsonl_exporter.py  # streaming JSONL
│       └── safety.py          # filename allowlist, path-traversal guards
│
├── frontend/                  # Next.js 16 dashboard
│   ├── app/                   # (app)/ routes, layout, sitemap, robots, OG images
│   ├── components/            # sidebar, url-input, progress, KPI cards, posts table, …
│   └── lib/                   # api.ts (typed client), hooks.ts, settings.ts, docs-meta.ts
│
├── data/                      # Runtime (gitignored): SQLite DB + exports + fb_cookies*.json
└── tests/                     # 133 tests (hermetic mocks, no network)
    ├── test_api_endpoints.py   # scrape, jobs, exports, accounts, health
    ├── test_browser_wall_handling.py
    ├── test_dedup_stats.py
    ├── test_e2e_integration.py  # full pipeline + proxy wiring
    ├── test_error_handling.py
    ├── test_export_api.py / test_exporters.py
    ├── test_graphql_extractor.py
    ├── test_infrastructure.py   # RateLimiter, RetryManager, ProxyManager
    ├── test_job_state_machine.py
    ├── test_normalization.py
    └── test_url_validation.py
```

## Configuration

All backend variables are read by pydantic-settings — env vars **or** a `.env`
file. In Docker, set them in `docker/.env`; on the host, in the root `.env`.

### General

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./data/postharvest.db` | SQLAlchemy DSN |
| `DATA_DIR` | `./data` | Runtime data directory |
| `EXPORT_BASE_DIR` | `./data/exports` | Export output root |
| `WORKER_THREADS` | `4` | Parallel scrape workers |
| `MAX_URLS_PER_JOB` | `300` | Max URLs per request (global cap above plan ceilings) |
| `DEFAULT_MAX_POSTS` | *(none)* | Per-source post cap |
| `DEFAULT_POST_TYPE` | `all` | Default type filter |
| `DEBUG` | `false` | Verbose logging |
| `CORS_ORIGINS` | `["http://localhost:3000","http://127.0.0.1:3000"]` | Allowed browser origins |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Backend URL (build-time for Next.js) |

### Scraper (compliance knobs)

| Variable | Default | Description |
|---|---|---|
| `SCRAPER_DELAY_SECONDS` | `2.5` | Min delay between requests per source (floor 0.1; do not lower in production) |
| `SCRAPER_TIMEOUT_SECONDS` | `20` | Per-request timeout |
| `SCRAPER_MAX_RETRIES` | `3` | Retries with exponential backoff |
| `SCRAPER_ROBOTS` | `1` | Enforce robots.txt |

### Proxy

| Variable | Default | Description |
|---|---|---|
| `PROXY_URL` | *(none)* | Single HTTP proxy URL (e.g. `http://127.0.0.1:8080`) |
| `PROXY_URLS` | `[]` | List of proxies for rotation |

## API reference

> **Full reference** — every endpoint, payload, the normalized 33-key post
> schema, error contract and pagination rules — lives in
> **[docs/API.md](./docs/API.md)**. Interactive docs: http://localhost:8000/docs

Quick orientation — base URL `http://localhost:8000/api`:

**Error envelope:** `{"error": {"code": "<code>", "message": "<human>"}}`

| HTTP | Codes | Meaning |
|---|---|---|
| 400 | `invalid_input`, `validation_error` | Bad request |
| 404 | `not_found` | Unknown job |
| 409 | `job_running`, `invalid_state`, `conflict` | Illegal state / job busy |
| 500 | `internal_error` | Unhandled error |
| 503 | `service_unavailable`, `database_unavailable` | Dependency down |

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/scrape` | Start a job (201 → `job_id`, `status: queued`) |
| `GET` | `/api/jobs` | List recent jobs (paginated) |
| `GET` | `/api/jobs/{id}` | Job status + live progress |
| `GET` | `/api/jobs/{id}/posts` | Paginated posts (`?page=&page_size=`) |
| `GET` | `/api/jobs/{id}/stats` | Aggregated dashboard KPIs |
| `POST` | `/api/jobs/{id}/pause` · `/resume` | Pause / resume a running job |
| `DELETE` | `/api/jobs/{id}` | Best-effort cancel + delete |
| `GET` | `/api/jobs/{id}/export/{json\|csv\|excel}` | Download export file |
| `GET` | `/api/accounts` · `DELETE` `/api/accounts/{name}` | Manage saved FB sessions |
| `GET` | `/api/health` | Liveness probe (`{"status":"ok"}`) |

## Exports

| Format | Shape |
|---|---|
| **JSON** | Nested, pretty-printed, full 33-key schema per post |
| **CSV** | Flattened, UTF-8 BOM, Excel-friendly |
| **XLSX** | Styled 4-sheet workbook (Posts, Engagement, Media, Metadata) |
| **JSONL** | One JSON object per line, streaming (memory-safe for large datasets) |

## Testing

```bash
make test            # backend: python -m pytest tests/ -v --tb=short (133 tests)
make test-frontend   # frontend: vitest
make test-all        # both
```

Tests use **hermetic mocked scraper responses — no live network access**. The
suite covers API endpoints, the scraper pipeline (fetcher/parser/normalizer/
dedup/browser wall-handling), job state machine (cancel/pause/resume), exports,
infrastructure (RateLimiter/RetryManager/ProxyManager), and end-to-end flow.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "API unreachable" banner | Backend not running; rebuild frontend if `NEXT_PUBLIC_API_URL` changed |
| CORS error | `CORS_ORIGINS` doesn't include your frontend origin |
| 0 posts, no errors | Modern Facebook `www` is JS-rendered; try `--browser` or check the login wall |
| `auth_required` in errors | Page requires login; run `python cli.py login` then use `--browser` |
| `rate_limited` | Too many requests; raise `SCRAPER_DELAY_SECONDS` |
| `database is locked` (SQLite) | Reduce `WORKER_THREADS` or use PostgreSQL |
| Browser login not detected | Cookies expired; re-run `python cli.py login` |
| Proxy not working | Check `PROXY_URL` in config; verify the proxy is running |
| Job stuck in `running` | `POST /api/jobs/{id}/pause` then `/resume` to unstick |

## Limitations

1. **HTTP mode gets 1–2 posts.** Modern Facebook `www` pages embed minimal data
   in the initial HTML — use `--browser` for more.
2. **Browser mode without login gets ~3 posts.** Facebook limits
   unauthenticated viewing; `python cli.py login` opens the full feed.
3. **Browser mode with login gets more, but not unlimited.** Facebook's React
   pagination still throttles scroll depth; scraping stops when no new content
   loads.
4. **Post text may be `null`** even in browser mode — pure-image and shared-link
   posts often carry no text in Facebook's embedded JSON.
5. **Reaction breakdown, `video_url`, `transcript`** are usually unavailable on
   public pages — kept in the schema for forward-compatibility.
6. **Heuristic parsing.** Facebook changes markup frequently; one malformed post
   never crashes a source (per-post error collection).
7. **Rate-limiting risk.** Aggressive use can trigger Meta's rate limits.
   Respect the defaults.
8. **Cookies expire.** Facebook session cookies typically last 1–2 weeks —
   re-run `login` when they do.

## Compliance summary

- **Permitted:** public pages/profiles only; respects `robots.txt`; throttled;
  single honest UA; exponential backoff. Browser mode uses saved cookies for
  authentication — you are responsible for how you use that capability.
- **Deliberately excluded:** CAPTCHA/anti-bot bypass, UA rotation, private or
  restricted content, groups/events/watch sources.
- **Your responsibility:** obey Facebook/Meta's Terms of Service, applicable
  law (e.g. GDPR), and the target site's `robots.txt`.

**Full detail: [COMPLIANCE.md](./COMPLIANCE.md)**

## Documentation

Long-form, single-topic guides live in [docs/](./docs/README.md):

| Guide | Covers |
|---|---|
| [ARCHITECTURE.md](./docs/ARCHITECTURE.md) | Backend layout, job model, database, scraper internals |
| [API.md](./docs/API.md) | Full HTTP API reference |
| [DEPLOYMENT.md](./docs/DEPLOYMENT.md) | Docker dev+prod, Makefile, env files, VPS/TLS plan |
| [CONTRIBUTING.md](./docs/CONTRIBUTING.md) | Setup, tests, CI, branch flow |

Decision log: [DECISIONS.md](./DECISIONS.md) · Roadmap: [ROADMAP.md](./ROADMAP.md)

## License

MIT (placeholder). Add a license file before public distribution.