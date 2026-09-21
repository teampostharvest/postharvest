# AGENTS.md — PostHarvest

> **This is the canonical guide for AI coding agents working in this repository.**
> All agent instruction files (`GEMINI.md`, `CLAUDE.md`, etc.) defer to this document.

---

## Table of Contents

1. [What This Project Is](#1-what-this-project-is)
2. [Tech Stack](#2-tech-stack)
3. [Repository Layout](#3-repository-layout)
4. [Dev Environment Setup](#4-dev-environment-setup)
5. [Running the App](#5-running-the-app)
6. [Tests](#6-tests)
7. [Linting & Type Checking](#7-linting--type-checking)
8. [Git Workflow](#8-git-workflow)
9. [Commit Conventions](#9-commit-conventions)
10. [Architecture & Key Patterns](#10-architecture--key-patterns)
11. [Environment Variables](#11-environment-variables)
12. [CI/CD Pipeline](#12-cicd-pipeline)
13. [Agent Rules & Scope Guards](#13-agent-rules--scope-guards)
14. [Gotchas & Critical Conventions](#14-gotchas--critical-conventions)
15. [Domain Terminology](#15-domain-terminology)

---

## 1. What This Project Is

**PostHarvest** (`postharvest.space`) is a compliance-first Facebook public-post scraper and analytics platform.

### Core Philosophy & Crawl Behavior
- Fetches only **publicly accessible** posts from Facebook pages and profiles over the public web.
- Strictly respects `robots.txt` (`SCRAPER_ROBOTS=true`).
- Uses a **single honest Chrome user-agent** — never rotated, never spoofed.
- Throttles every request (2.5 s default floor via token-bucket rate limiter).
- Never bypasses authentication barriers, CAPTCHAs, or anti-bot defenses.
- Does not exploit private APIs or undocumented endpoints maliciously.

### Two Extraction Modes
| Mode | Speed | Mechanism | Posts Returned |
|---|---|---|---|
| **HTTP mode** (default) | Fast (~3 s) | `httpx` + `BeautifulSoup4` (`lxml`) | 1–2 posts from initial server-rendered HTML |
| **Browser mode** (`--browser`) | Slower, deep | Playwright Chromium (headless) + scroll | Deep history; supports authenticated scraping via saved session cookies |

### Interfaces
- **FastAPI REST backend**: Orchestrates asynchronous background jobs with live progress, pause/resume, and stats.
- **Next.js 16 Web Dashboard**: Interactive UI with URL submission, real-time progress indicators, results tables, and export triggers.
- **CLI** (`cli.py`): Standalone developer CLI for cookie capture, account management, scraping, and export generation.
- **Multi-format Exports**: JSON, CSV (UTF-8 BOM), Excel XLSX (4 workbook sheets), and streaming JSONL.

### Current Evolution
PostHarvest is actively transitioning from a single-operator local tool to a multi-tenant SaaS platform at `postharvest.space`. Core scraping and job lifecycle logic is complete; authentication seams (Firebase ID tokens) and ownership scoping (`owner_id`) are being rolled out across endpoints.

---

## 2. Tech Stack

| Layer | Technology | Details |
|---|---|---|
| **Backend Runtime** | Python 3.13+ | CI tests Python 3.11 & 3.12 |
| **Backend Framework** | FastAPI 0.141.1 + Uvicorn 0.52.4 | Async ASGI, Pydantic v2 validation |
| **Database & ORM** | SQLAlchemy 2.0 | SQLite (local dev & hermetic tests); PostgreSQL / Supabase (production) |
| **Database Migrations** | Alembic | Versioned migrations in `backend/alembic/versions/` |
| **HTTP Scraper** | `httpx` 0.28.1 + `beautifulsoup4` + `lxml` + `brotli` | Compliance fetcher with retry & circuit breaker |
| **Browser Scraper** | Playwright 1.62.0 (headless Chromium) | Virtual display / CDP session capture |
| **Session Security** | `cryptography` (Fernet) | At-rest encryption via `COOKIE_ENCRYPTION_KEY` |
| **Authentication** | Firebase Admin SDK + Firebase Client SDK | Server-side ID token verification (`Bearer <token>`) |
| **Job Execution** | In-process `ThreadPoolExecutor` | Singleton `JobManager` with per-job `CancelToken` |
| **Frontend Framework** | Next.js 16 (App Router) + React 19 | Standalone output mode (`output: "standalone"`) |
| **Language & Styling** | TypeScript (Strict) + Tailwind CSS 4 | Jost typography design system |
| **Frontend Testing** | Vitest + `@testing-library/react` | Configured in `frontend/vitest.config.mts` |
| **Backend Testing** | `pytest` + `pytest-asyncio` | 179+ hermetic unit & integration tests |
| **Linters & Formatters** | ESLint + `oxlint` 1.83.0 (pinned) | Fast linting + TypeScript verification (`tsc --noEmit`) |
| **Reverse Proxy** | Nginx 1.27-alpine | Prod routing, TLS termination, static asset cache |
| **Release Management** | `semantic-release` + Husky 9 + `commitlint` | Automated semantic versioning on push to `master` |

---

## 3. Repository Layout

```
postharvest/
├── AGENTS.md                  # Canonical guide for AI coding agents (this file)
├── CLAUDE.md                  # Claude instruction stub pointing to AGENTS.md
├── GEMINI.md                  # Gemini instruction stub pointing to AGENTS.md
├── Makefile                   # Primary entry point for dev, test, lint, and docker tasks
├── cli.py                     # Standalone CLI (login, accounts, scrape, export)
├── package.json               # Monorepo root scripts: husky, commitlint, semantic-release, promote
├── commitlint.config.mjs      # Conventional commit validator and length rules
├── .releaserc.json            # semantic-release plugin pipeline configuration
├── pytest.ini                 # Pytest configuration (restricts testpaths to tests/)
├── .env.example               # Template for root .env (CLI, unit tests, host dev)
│
├── .agents/                   # Specialized agent instructions and workflows
│   ├── POSTHARVEST-AUTH.md    # Multi-tenancy & Firebase auth implementation constraints
│   ├── hooks/                 # Formatter and linter hooks
│   └── skills/                # Agent skills (commit rituals, reviews, UI animations)
│
├── .github/
│   └── workflows/             # GitHub Actions CI/CD workflows
│       ├── pytest.yml         # Matrix pytest (Python 3.11, 3.12)
│       ├── eslint.yml         # Frontend ESLint check
│       ├── vitest.yml         # Frontend Vitest test runner
│       ├── typecheck.yml      # TypeScript compiler check (tsc --noEmit)
│       ├── lighthouse.yml     # Lighthouse CI performance & accessibility audits
│       └── release.yml        # Semantic-release execution (master branch only)
│
├── backend/                   # FastAPI backend application
│   ├── main.py                # App factory, lifespan hooks, CORS middleware, error handlers
│   ├── requirements.txt       # Production & framework dependencies
│   ├── alembic.ini            # Alembic configuration (script_location = backend/alembic)
│   ├── alembic/               # Database migration scripts
│   │   └── versions/          # Versioned schema migrations
│   ├── api/                   # Thin HTTP routers (delegate directly to services)
│   │   ├── health.py          # GET /api/health (liveness & DB latency)
│   │   ├── scrape.py          # POST /api/scrape (enqueue new scrape jobs)
│   │   ├── jobs.py            # GET/PATCH/DELETE /api/jobs/{id} (status, pause/resume, cancel)
│   │   ├── exports.py         # GET /api/jobs/{id}/export/{fmt} (download exports)
│   │   ├── accounts.py        # Facebook session cookie credentials management
│   │   ├── admin.py           # GET/PATCH /api/admin/users (role/plan assignments)
│   │   ├── auth.py            # GET /api/auth/me (current user identity)
│   │   └── capture_viewer.py  # WebSocket CDP bridge for interactive login capture
│   ├── auth/                  # Authentication & identity layer
│   │   ├── dependencies.py    # FastAPI Depends for token verification & current user
│   │   └── firebase.py        # Firebase Admin SDK initialization
│   ├── core/                  # Core infrastructure singletons
│   │   ├── config.py          # Pydantic Settings singleton (lru_cached get_settings())
│   │   ├── database.py        # SQLAlchemy engine, SessionLocal, Base, init_db()
│   │   ├── job_manager.py     # ThreadPoolExecutor job worker pool & CancelToken registry
│   │   ├── exceptions.py      # AppError hierarchy & normalized error response envelope
│   │   ├── logging.py         # Centralized stdout logging under postharvest.*
│   │   └── plans.py           # Subscription tier definitions & quota boundaries
│   ├── models/                # 11 SQLAlchemy ORM models
│   │   ├── user.py            # User: firebase_uid, email, role (user/ops), plan
│   │   ├── scrape_jobs.py     # ScrapeJob: status, counters, config snapshot, owner_id
│   │   ├── sources.py         # ScrapeSource: URL state, per-source counters
│   │   ├── posts.py           # Post: 33-key normalized post schema, deduplication keys
│   │   ├── engagement_metrics.py # Likes, comments, shares, reactions breakdown
│   │   ├── media.py           # Media: primary and secondary image/video attachments
│   │   ├── errors.py          # ScrapeError: per-source or per-post error audit logs
│   │   ├── crawl_state.py     # CrawlState: pagination resume checkpoints
│   │   ├── export_jobs.py     # ExportJob: audit record of exported artifacts
│   │   └── saved_account.py   # SavedAccount: Fernet-encrypted Facebook session cookies
│   ├── schemas/               # Pydantic request and response schemas
│   ├── services/              # Business logic layer
│   │   ├── job_service.py     # Job lifecycle coordination, queueing, thread worker runner
│   │   ├── export_service.py  # Export builders & file generation
│   │   ├── crawl_state_service.py # Checkpointing & pause/resume state management
│   │   ├── serialization.py   # ORM entity to canonical dictionary mapper
│   │   └── stats.py           # Aggregated statistics and KPI calculations
│   ├── scraper/               # Extraction engine (PROTECTED CORE — see Scope Guards)
│   │   ├── __init__.py        # Public interface: validate_facebook_url(), scrape_source()
│   │   ├── fetcher.py         # Throttled httpx HTTP client respecting robots.txt
│   │   ├── http_client.py     # Shared client factory with honest Chrome user-agent
│   │   ├── rate_limiter.py    # TokenBucketRateLimiter and circuit-breaker RetryManager
│   │   ├── proxy_manager.py   # Proxy rotation and failover management
│   │   ├── pagination.py      # Abstract PageFetcher loop and cancellation checks
│   │   ├── crawler.py         # Extraction coordinator: fetch -> parse -> normalize -> dedup
│   │   ├── adapters/          # FacebookHttpTransport and FacebookBrowserTransport
│   │   ├── parser.py          # HTML & GraphQL payload parser (resilient to FB layout shifts)
│   │   ├── normalizer.py      # Transforms raw extracts into the 33-key canonical post dict
│   │   ├── dedup.py           # Two-layer deduplication (post_id + SHA-256 fingerprint)
│   │   ├── browser_scraper.py # Headless Playwright runner for dynamic page loads
│   │   ├── errors.py          # Scraper-specific exception hierarchy
│   │   └── url_validator.py   # Strict Facebook profile/page URL validator
│   └── exporters/             # Multi-format serialization engines
│       ├── __init__.py        # Dispatcher function export_posts()
│       ├── json_exporter.py   # Streamed JSON serialization
│       ├── csv_exporter.py    # UTF-8 with BOM, pipe-joined list columns
│       ├── xlsx_exporter.py   # Multi-sheet Excel workbook (openpyxl)
│       ├── jsonl_exporter.py  # Memory-efficient JSON Lines streaming
│       └── safety.py          # Strict path traversal guards & filename allowlists
│
├── frontend/                  # Next.js 16 App Router application
│   ├── package.json           # Frontend dependencies and npm scripts
│   ├── tsconfig.json          # TypeScript strict configuration (paths: @/* -> ./*)
│   ├── next.config.mjs        # Next.js config (standalone build mode)
│   ├── vitest.config.mts      # Vitest test suite configuration
│   ├── vitest-setup.ts        # DOM assertions setup (@testing-library/jest-dom)
│   ├── app/                   # App Router pages and layouts
│   │   ├── layout.tsx         # Root layout with font definitions and providers
│   │   ├── globals.css        # Global CSS variables and Jost font styling
│   │   ├── login/             # Public user sign-in page
│   │   └── (app)/             # Protected route group (auth guard applied)
│   │       ├── layout.tsx     # App dashboard shell with sidebar navigation
│   │       ├── page.tsx       # Primary scrape console and live view
│   │       ├── accounts/      # Facebook session cookies manager
│   │       ├── history/       # Prior scraping jobs archive
│   │       ├── investigation/ # Drill-down inspection of extracted posts
│   │       ├── pricing/       # Subscription plans and tier limits
│   │       ├── settings/      # User profile and client settings
│   │       └── docs/          # In-app reference documentation
│   ├── components/            # React UI components
│   │   ├── ui/                # Base primitives (button, input, dialog, card, badge, etc.)
│   │   ├── common/            # Shared layout & global shells (AppSidebar, ThemeProvider, UserNav)
│   │   ├── features/          # Domain components (scraper/, posts/, metrics/)
│   │   └── views/             # Full page layout views (HomeView, SignInScreen, Display)
│   └── lib/                   # Frontend helpers and services
│       ├── api.ts             # Central API client injecting Firebase auth tokens
│       ├── hooks.ts           # Polling hooks (useJobProgress at 1500ms, useJobPosts)
│       ├── auth-context.tsx   # React context provider for Firebase auth state
│       ├── firebase.ts        # Firebase client SDK initialization
│       ├── types.ts           # Canonical TypeScript interfaces matching backend models
│       └── utils.ts           # CSS class mergers and formatting helpers
│
├── tests/                     # Backend test suite (179+ hermetic tests)
│   ├── conftest.py            # Global fixtures: SQLite temp DB, Firebase mock, TestClient
│   ├── helpers.py             # Shared utilities: install_fake_scraper(), wait_for_job_terminal()
│   ├── requirements.txt       # Test harness dependencies
│   └── fixtures/              # Mock HTML and GraphQL response fixtures
│
├── docker/                    # Docker containerization infrastructure
│   ├── Dockerfile             # Multi-stage build: frontend-dev, frontend, backend, backend-dev
│   ├── docker-compose.yml     # Base network and service definitions
│   ├── docker-compose.override.yml # Local development overrides (bind mounts, hot-reload)
│   ├── docker-compose.prod.yml     # Production hardening (read-only rootfs, dropped caps)
│   ├── example.env            # Environment variable template for Docker Compose
│   └── nginx/                 # Nginx configurations (prod reverse proxy)
│
├── deploy/                    # Host-side operations and runbooks (systemd, certbot)
├── scripts/                   # Utility automation scripts
│   ├── promote.sh             # Branch ladder promotion: testing -> next -> master
│   └── generate_schema_sql.py # DDL export script from SQLAlchemy models
├── data/                      # Local runtime data directory (gitignored: DB, exports, cookies)
├── examples/                  # Sample output exports for verification
├── certs/                     # TLS certificates (gitignored)
└── inspo/                     # Reference archives (NOT code; strictly excluded from pytest)
```

---

## 4. Dev Environment Setup

**Prerequisites:** Python 3.13+, Node.js 22+, Docker & Docker Compose (optional for Docker workflow).

### Environment File Separation
> [!IMPORTANT]
> The project uses **two separate** `.env` files for different contexts:
> - **`docker/.env`**: Used exclusively by Docker Compose (copied from `docker/example.env`).
> - **`.env` (root)**: Used by standalone Python runs, local CLI commands, unit tests, and host-run frontend dev (copied from `.env.example`).
> Never confuse or merge these two files.

### Workflow A: Full Docker Stack (Recommended)
```bash
# 1. Initialize Docker environment
cp docker/example.env docker/.env
cp .env.example .env

# 2. Boot the full containerized development environment
make dev
```
This runs PostgreSQL, FastAPI backend (:8000 with reload), and Next.js frontend (:3000 with hot-reload).

### Workflow B: Local Host Setup (No Docker)
```bash
# 1. Backend virtual environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
# If planning to run browser-based scraping locally:
python -m playwright install chromium

# 2. Frontend dependencies
cd frontend && npm ci && cd ..

# 3. Root environment configuration
cp .env.example .env

# 4. Run services concurrently or in separate terminals
make backend-dev   # uvicorn backend.main:app --reload --port 8000
make frontend-dev  # cd frontend && npm run dev (runs on :3000)
```

---

## 5. Running the App

### Make Targets Cheatsheet
The repository uses a root `Makefile` as the single operational entry point:

| Make Target | Description |
|---|---|
| `make dev` | Start full Docker stack in foreground (Ctrl+C to stop) |
| `make dev-up` | Start full Docker stack in background |
| `make dev-down` | Stop and teardown Docker stack |
| `make dev-logs` | Stream logs from all Docker containers |
| `make dev-build` | Force rebuild Docker images and restart |
| `make backend-dev` | Run FastAPI server locally on host with hot-reload (:8000) |
| `make frontend-dev` | Run Next.js server locally on host with hot-reload (:3000) |
| `make test` | Run backend pytest suite |
| `make test-frontend` | Run frontend Vitest suite |
| `make test-all` | Run both backend and frontend test suites |
| `make lint` | Run frontend ESLint check |
| `make lint-ff` | Run ultra-fast frontend linting via `oxlint` |
| `make typecheck` | Run frontend TypeScript type checking |
| `make prod-up` | Boot production-hardened Docker stack (read-only filesystem, Nginx) |
| `make prod-health` | Probe production health check endpoint |

### Development URLs
- **Web Dashboard**: [http://localhost:3000](http://localhost:3000)
- **API Base**: [http://localhost:8000/api](http://localhost:8000/api)
- **Interactive Swagger Docs**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **Health Check Endpoint**: [http://localhost:8000/api/health](http://localhost:8000/api/health)

### CLI Operations
The standalone `cli.py` utility can be executed directly from the virtual environment:
```bash
# Authenticate Facebook account visually via browser and store encrypted session
python cli.py login

# List stored Facebook session accounts
python cli.py accounts

# Run a scrape job directly
python cli.py scrape "https://www.facebook.com/TargetPage" --max-posts 25 --format json

# Export existing job results
python cli.py export <job-uuid> --format csv
```

---

## 6. Tests

### Backend Tests (`pytest`)
All backend tests are located in `tests/` and run against a temporary, hermetic SQLite database.
```bash
# Via Makefile
make test

# Direct invocation
pytest tests/ -v --tb=short
```

#### Critical Backend Testing Invariants
1. **Zero Real Network Calls**: Tests that trigger background scraping jobs **must** install a fake scraper using `helpers.install_fake_scraper()` and await job terminal state before returning.
2. **Mocked Firebase Authentication**: `tests/conftest.py` monkeypatches `verify_id_token`. Tokens prefixed with `test_` or `user_` resolve to mock users. Malformed tokens (`"garbage"`, `"invalid"`) trigger HTTP 401.
3. **Session-Scoped TestClient**: Exactly **one** `TestClient` is initialized for the test session. Creating a second `TestClient` instance permanently destroys the in-process `JobManager` worker pool.
4. **Hermetic Test Pathing**: `pytest.ini` strictly pins `testpaths = tests`. Under no circumstances should `inspo/` be evaluated by pytest.
5. **Baseline Test Count**: Maintain or expand beyond the **179** passing tests baseline. Never weaken or delete assertions.

### Frontend Tests (`Vitest`)
Frontend unit and component tests use Vitest and React Testing Library:
```bash
# Run full suite once
make test-frontend

# Run in interactive watch mode
cd frontend && npm run test:watch
```

---

## 7. Linting & Type Checking

### Frontend Quality Gates
```bash
# Run standard ESLint
make lint

# Run fast lint via oxlint (pinned to 1.83.0 — do NOT upgrade without verification)
make lint-ff

# Strict TypeScript type check
make typecheck
```

### Commit Message Validation
Verify your proposed or latest commit message against repository conventional commit rules:
```bash
npx commitlint --from HEAD~1 --to HEAD --verbose
```

---

## 8. Git Workflow

PostHarvest adheres to a strict promotion ladder. Direct pushes to release and integration branches are forbidden.

### Branch Ladder Structure
```
feature/* / fix/* / chore/*
            │
            ▼ (Pull Request & CI Verification)
         testing  (Active QA and development target)
            │
            ▼ (npm run promote -- next)
          next    (Integration and staging)
            │
            ▼ (npm run promote)
         master   (Production release — triggers semantic-release)
            │
            ▼ (Automated mirror tag)
       production (Stable deployment pointer)
```

### Branch Rules
- `feature/*`, `fix/*`, `chore/*`: Work branches. Must branch off `origin/testing` and open PRs targeting `testing`.
- `testing`: Primary collaboration branch. All PRs merge here.
- `next`: Staging integration branch. Merges only from `testing`.
- `master`: Shipped release branch. Merges only from `next`. Every push automatically generates a GitHub release and version tag.
- `production`: Read-only pointer tag mirroring the latest stable production release.

### Branch Promotion Routine
```bash
# Promote testing -> next -> master
npm run promote

# Promote only next -> master
npm run promote -- next

# Enforce local test gates prior to pushing master
GATE=1 npm run promote
```

---

## 9. Commit Conventions

Repository commits are validated by Husky pre-commit hooks and `commitlint.config.mjs`.

### Commit Structure
```
type(scope): subject
```

### Formatting Rules
- **type**: Lowercase string (`feat`, `fix`, `chore`, `docs`, `test`, `refactor`, `ci`, `style`, `perf`, `build`, `revert`).
- **scope**: Lowercase string indicating component (`scraper`, `accounts`, `api`, `auth`, `frontend`, `deps`, `export`).
- **subject**: Imperative mood, lowercase start, no period at end, minimum 8 characters, maximum 120 characters.
- **header**: Maximum total length 150 characters.

### Semantic Release Impact
| Type | Semantic Version Bump | Example |
|---|---|---|
| `feat` | Minor (`1.0.0` -> `1.1.0`) | `feat(accounts): add session cookie import` |
| `fix` | Patch (`1.0.0` -> `1.0.1`) | `fix(scraper): resolve reaction count parsing` |
| `feat!` or `BREAKING CHANGE:` | Major (`1.0.0` -> `2.0.0`) | `feat!(auth): enforce firebase token on all endpoints` |
| `chore`, `docs`, `test`, `refactor`, `ci` | None (no release triggered) | `chore(deps): bump httpx version` |

> [!WARNING]
> Never manually edit `CHANGELOG.md` or the `version` field in `frontend/package.json`. These are owned exclusively by `semantic-release`.

---

## 10. Architecture & Key Patterns

### Asynchronous Scraping Lifecycle
1. **Submission**: Client calls `POST /api/scrape` with target URLs and options.
2. **Immediate Acknowledgment**: Backend persists `ScrapeJob` (state `queued`), records child `ScrapeSource` entries, and immediately returns HTTP 201 with `job_id`.
3. **Execution**: The background `JobManager` (`ThreadPoolExecutor`, default 4 workers) picks up the job, switches state to `running`, and spawns scraper tasks.
4. **Transport Selection**: `make_facebook_transport()` selects either `FacebookHttpTransport` or `FacebookBrowserTransport` based on job parameters.
5. **Extraction Loop**: Fetcher -> HTML/GraphQL Parser -> Normalizer -> Deduplicator -> Database persistence.
6. **Polling**: Frontend polls `GET /api/jobs/{id}` at 1500ms intervals until reaching terminal state (`completed`, `failed`, or `cancelled`).

### Architecture Invariants (Non-Negotiable)
1. **Unified Error Envelope**: All API error responses must strictly follow the format:
   ```json
   {
     "error": {
       "code": "error_code_string",
       "message": "Human-readable explanation"
     }
   }
   ```
   Never return arbitrary or unnested error objects.
2. **Single Backend Replica**: The backend must run as exactly **one** process/replica. Scraping job state and worker cancellation tokens reside in-process in `JobManager`. Running multiple replicas or setting `uvicorn --workers > 1` causes divergent state and broken job controls.
3. **Fault Isolation**: Failure of a single source URL must **never** terminate the overall scrape job. Errors are isolated to the specific `ScrapeSource` or `Post`, logged in `ScrapeError`, and processing continues for remaining URLs.
4. **Identity Source of Truth**: User identity is derived **only** from cryptographically verified Firebase ID tokens via backend dependency injection (`get_current_user`). Never trust client-supplied identity parameters in headers, query strings, or request bodies.
5. **SQL WHERE Scoping**: Multi-tenant data isolation must be enforced directly in the database query's `WHERE` clause (e.g. `WHERE scrape_jobs.owner_id = current_user.id`). Never retrieve an entity and subsequently check permissions in Python. Cross-tenant access must return HTTP 404 (not 403) to prevent resource enumeration.
6. **Canonical 33-Key Post Schema**: Every extracted post must be normalized to the exact 33-key dictionary structure defined in `backend/scraper/normalizer.py`. Missing attributes must be `None` or `[]` — never fabricated or omitted.
7. **Export Path Allowlist**: Export generation uses a strict filename and path allowlist (`backend/exporters/safety.py`). User input must never be interpolated into filesystem paths.
8. **Frontend Safe URLs**: Always wrap URLs in `safeHttpUrl()` before rendering in HTML anchor tags (`<a href=...>`). Never bypass React sanitization with `dangerouslySetInnerHTML`.

---

## 11. Environment Variables

### Backend Configuration (`backend/core/config.py`)

| Environment Variable | Default Value | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./data/postharvest.db` | SQLAlchemy connection string |
| `SUPABASE_DB_URL` | *(None)* | Production PostgreSQL URL (takes precedence over `DATABASE_URL`) |
| `SUPABASE_URL` | *(None)* | Supabase project API endpoint |
| `SUPABASE_ANON_KEY` | *(None)* | Supabase anonymous API key |
| `SUPABASE_SERVICE_KEY` | *(None)* | Supabase service-role administrative key |
| `FIREBASE_PROJECT_ID` | `postharvest-firebase` | Firebase application project identifier |
| `FIREBASE_CLIENT_EMAIL` | *(None)* | Service account email for server-side token validation |
| `FIREBASE_PRIVATE_KEY` | *(None)* | Service account private key |
| `FIREBASE_CREDENTIALS_PATH` | *(None)* | Path to local Firebase service account JSON file |
| `OPS_EMAILS` | `[]` | Comma-separated or JSON list of emails auto-assigned `ops` role |
| `COOKIE_ENCRYPTION_KEY` | *(None)* | 32-byte url-safe base64 Fernet key for encrypting cookies |
| `DATA_DIR` | `./data` | Filesystem path for local database and cookie storage |
| `EXPORT_BASE_DIR` | `./data/exports` | Filesystem destination for generated export files |
| `WORKER_THREADS` | `4` | Concurrency limit for background scraping thread pool |
| `MAX_URLS_PER_JOB` | `100` | Maximum source URLs accepted in a single scrape request |
| `CANCEL_WAIT_SECONDS` | `5.0` | Grace period timeout when aborting a running job |
| `DEBUG` | `false` | Enable verbose debug logging |
| `CORS_ORIGINS` | `["http://localhost:3000"]` | Allowed CORS origins as a **valid JSON array string** |
| `PAGE_SIZE_DEFAULT` | `50` | Default record limit for paginated queries |
| `PAGE_SIZE_MAX` | `200` | Hard cap on page size for paginated queries |
| `SCRAPER_DELAY_SECONDS` | `2.5` | Politeness throttle delay between sequential Facebook requests |
| `SCRAPER_TIMEOUT_SECONDS`| `20` | Network request timeout for HTTP operations |
| `SCRAPER_MAX_RETRIES` | `3` | Exponential backoff retry threshold before tripping circuit breaker |
| `SCRAPER_ROBOTS` | `true` | Enforce compliance checking against `robots.txt` |
| `PROXY_URL` | *(None)* | Single outbound HTTP proxy string |
| `PROXY_URLS` | `[]` | Array of outbound proxy URLs for rotating proxy pool |
| `SESSION_CAPTURE_PORT` | `9333` | Chrome DevTools Protocol loopback port for interactive login |
| `SESSION_CAPTURE_TIMEOUT_SECONDS` | `240.0` | Maximum interactive window for user to complete login |

### Frontend Build Variables (`frontend/.env.local`)

| Environment Variable | Default Value | Notes |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Backend API URL. **Do NOT append `/api`**; baked at build time. In production behind Nginx, leave empty (`""`). |
| `NEXT_PUBLIC_SITE_URL` | `http://localhost:3000` | Canonical origin used for sitemap, robots, and OpenGraph tags. |
| `NEXT_PUBLIC_FIREBASE_API_KEY` | *(None)* | Client-side Firebase web API key. |
| `NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN` | *(None)* | Client-side Firebase authentication domain. |
| `NEXT_PUBLIC_FIREBASE_PROJECT_ID` | `postharvest-firebase` | Client-side Firebase project ID. |
| `NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET` | *(None)* | Client-side Firebase Cloud Storage bucket. |
| `NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID` | *(None)* | Client-side Firebase messaging sender ID. |
| `NEXT_PUBLIC_FIREBASE_APP_ID` | *(None)* | Client-side Firebase application ID. |

---

## 12. CI/CD Pipeline

The repository runs 6 GitHub Actions workflows on pushes and pull requests against `master`, `next`, and `testing`:

1. **`pytest.yml`**: Installs dependencies and runs the complete backend pytest suite across Python 3.11 and 3.12.
2. **`eslint.yml`**: Validates frontend source files against ESLint rules.
3. **`vitest.yml`**: Executes frontend React component and helper tests.
4. **`typecheck.yml`**: Runs `tsc --noEmit` in strict mode to guarantee zero TypeScript type errors.
5. **`lighthouse.yml`**: Builds the standalone Next.js site and executes automated Lighthouse CI audits for SEO, accessibility, and performance.
6. **`release.yml`**: Triggers exclusively on pushes to `master`. Runs `semantic-release` to generate version tags, compile changelogs, update `frontend/package.json`, and publish GitHub releases with `[skip ci]`.

---

## 13. Agent Rules & Scope Guards

> [!CAUTION]
> AI agents must strictly adhere to the scope boundaries outlined below.

### Hard Denylist — NEVER MODIFY Without Explicit User Instruction
- **`backend/scraper/**`**: The core scraping engine (`parser.py`, `normalizer.py`, `dedup.py`, `browser_scraper.py`, `pagination.py`, `rate_limiter.py`, `crawler.py`, `adapters/`, `fetcher.py`).
- **`backend/exporters/**`**: Core serialization engines.
- **Existing Test Assertions**: New tests are welcomed; modifying or deleting existing assertions to pass tests is forbidden.
- **Protected Git Branches**: Never push directly to `master`, `next`, `testing`, or `production`.
- **Release Metadata**: Never manually edit `CHANGELOG.md` or modify version numbers in `package.json`.

### Permitted Feature Scope (Auth & Multi-Tenancy Transition)
- `backend/auth/**` (New token verification dependencies)
- `backend/alembic/**` (New database schema migration scripts)
- `backend/models/**` (`User` and `ScrapeJob.owner_id` relationships only)
- `backend/api/**` and `backend/services/**` (Adding tenant filtering and ownership constraints)
- `backend/core/config.py` (Adding necessary environment flags)
- `frontend/lib/{firebase,auth-context,api}.ts` and frontend auth pages/components
- `tests/` (Adding new tenancy, auth, or endpoint tests)

### Mandatory Agent Protocol
1. **Inspect Before Acting**: Never assume a file structure or code convention exists. Always view the relevant file and confirm its contents before editing.
2. **Cite File and Line Numbers**: Clearly reference `path/to/file:line` when explaining changes or issues.
3. **Execute Test Suite**: Run `make test` after backend modifications and verify that the test count remains at or above the **179** baseline.
4. **Follow Commit Rituals**: Adhere to `.agents/skills/commit.md`. Check diffs, craft clean conventional commits, and verify git status.

---

## 14. Gotchas & Critical Conventions

1. **`NEXT_PUBLIC_API_URL` Baking**: Next.js bakes `NEXT_PUBLIC_*` variables at compile time. Changing this requires rebuilding the frontend image. Never add `/api` to this variable, as `frontend/lib/api.ts` automatically prepends `/api`.
2. **Single Process Backend**: `ThreadPoolExecutor` and job tracking exist entirely in memory. Never configure multiple Uvicorn workers (`--workers > 1`) or horizontal pod scaling without an external queue.
3. **Docker Compose Working Directory**: All compose operations execute from the `docker/` subfolder. Build contexts are relative to the repository root (`..`).
4. **Two Distinct `.env` Files**: `docker/.env` manages container configurations; root `.env` manages host CLI, unit tests, and host dev servers. Keep them synchronized where appropriate, but distinct.
5. **Mock Auth in Pytest**: Pytest automatically bypasses Firebase. Tokens must begin with `test_` or `user_` to simulate valid authenticated sessions.
6. **Singleton `TestClient`**: Instantiating multiple `TestClient` objects across test files breaks the `JobManager` singleton. Use the shared session fixture from `tests/conftest.py`.
7. **`pytest.ini` Isolation**: Keeps `inspo/` from being parsed by pytest. Do not alter `testpaths = tests`.
8. **Alembic in Production vs Dev**: Dev and tests use `Base.metadata.create_all()` via `init_db()`. Production uses `alembic upgrade head`. When pointing to an existing Supabase DB, run `alembic -c backend/alembic.ini stamp head` prior to the first upgrade.
9. **Oxlint Version Locking**: `oxlint` is explicitly pinned to `1.83.0` in `frontend/package.json`. Do not bump this version without verifying compatibility.
10. **CORS Syntax Requirement**: `CORS_ORIGINS` must be formatted as a valid JSON array string (e.g. `'["http://localhost:3000"]'`), not a comma-separated list.
11. **OPS Emails Parsing**: `OPS_EMAILS` supports both comma-separated strings and JSON arrays. Any user logging in with an email in this list is automatically assigned the `ops` administrative role.
12. **Facebook Markup Shifts**: Facebook changes HTML structures frequently. The parser in `parser.py` is heuristic and resilient; errors on individual post structures must not crash the entire source scraper.
13. **Export Path Sanitization**: `backend/exporters/safety.py` restricts download filenames to a fixed allowlist to prevent arbitrary file reading and path traversal attacks.
14. **Persistent Cookie Storage**: Facebook cookies are saved under `data/fb_cookies_<name>.json` and shared between host CLI and Docker containers via bind mounts.
15. **Inspo Folder Is Not Code**: The `inspo/` directory contains external reference archives and inspiration. Never import modules from `inspo/` into the application.
16. **Flaky WebSocket Bridge Test**: `test_capture_ws_bridge_forwards_frames` is known to occasionally raise `CancelledError` in CI. This is tracked in `TODO.md` and does not represent a code regression.
17. **Auto-Generated Changelogs**: `CHANGELOG.md` is managed by `semantic-release`. Manual edits will cause merge conflicts during release runs.
18. **Target Runtime Python 3.13**: The production Docker image runs on `python:3.13-slim`. CI tests ensure backwards compatibility with 3.11 and 3.12.
19. **Supabase DSN Priority**: In `backend/core/database.py`, if `SUPABASE_DB_URL` is set, it overrides `DATABASE_URL`.
20. **Naming Standards**: Always use lowercase `postharvest` for Python modules, Docker containers, and system identifiers. Use `PostHarvest` for UI branding.

---

## 15. Domain Terminology

| Term | Definition |
|---|---|
| **Job (`ScrapeJob`)** | A top-level scraping run. Tracks overall lifecycle status (`queued`, `running`, `completed`, `failed`, `cancelled`), options, and aggregate counters. |
| **Source (`ScrapeSource`)** | A single target URL within a job. Maintains independent status, page counts, and error tracking. |
| **CrawlState** | Checkpoint data allowing a paused or interrupted source crawl to resume from its last cursor position. |
| **33-Key Post Schema** | The standardized JSON structure containing 33 fields (IDs, text, timestamps, engagement, media) produced by `normalizer.py`. |
| **Dedup Key** | Primary identifier for posts: `post_id` if present, otherwise `fp:<sha256>` computed from page ID, timestamp, and text prefix. |
| **Token Bucket** | Rate-limiting algorithm used by `RateLimiter` (default 0.4 tokens/sec = 1 request every 2.5 seconds). |
| **Circuit Breaker** | Protection mechanism in `RetryManager` transitioning through `CLOSED`, `HALF_OPEN`, and `OPEN` states on upstream failures. |
| **Honest UA** | A fixed, transparent Chrome User-Agent header explicitly identifying the browser without deceptive rotation. |
| **Owner ID** | The Firebase UID (`sub`) assigned to a job, used to enforce tenant isolation across all queries. |
| **Ops Pool** | Shared pool of operator-managed Facebook session cookies used to power free-tier requests without requiring user credentials. |
| **Personal Session** | User-provided Facebook cookies, encrypted with Fernet and restricted to that user's jobs. |
| **Session Capture** | Interactive login system where Chromium runs on a loopback CDP port to allow users to log in directly while securely capturing cookies. |
| **CDP** | Chrome DevTools Protocol, used for headless browser automation and session capture. |
| **CancelToken** | Thread-safe `threading.Event` wrapper passed to scraper loops to trigger graceful aborts. |
| **JobManager** | Singleton process service managing worker threads and running jobs. |
| **Error Envelope** | The mandatory response format `{"error": {"code": "...", "message": "..."}}` returned on all API errors. |
| **Promote Ladder** | The automated git progression (`testing` -> `next` -> `master`) triggered via `npm run promote`. |
| **Jost** | The primary geometric sans-serif font family used throughout the frontend UI. |
| **JSONL** | JSON Lines format (newline-delimited JSON), used for memory-safe streaming of large export sets. |
