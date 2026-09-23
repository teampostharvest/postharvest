# Deployment

How to run PostHarvest with Docker (dev and production), the env-file split,
the Makefile, and the path to a public deployment on a VPS.

Everything container-related lives in `docker/` — the compose **project
directory** (see [DECISIONS.md](../DECISIONS.md) D6–D12 for the rationale).

## Env files — the two-template split

| File | Consumer | Purpose |
|---|---|---|
| `docker/.env` (← copy of `docker/example.env`) | docker compose | postgres creds, `COOKIE_ENCRYPTION_KEY`, quotas, build-time frontend vars, prod caps |
| root `.env` (← copy of `.env.example`) | CLI / tests / local `next dev` | sqlite fallback DSN, data paths, scraper knobs |

Compose reads `docker/.env` because it runs from inside `docker/`. The root
`.env` is **not** consumed by compose. Both files are gitignored; the
`*.example` templates are tracked.

## Makefile

Everything is one command from the repo root (`make help` lists all targets).

### Dev stack (hot reload)

```bash
make dev        # foreground (backend :8000, frontend :3000, postgres, hot reload)
make dev-up     # background
make dev-down
make dev-build  # rebuild + start
make dev-logs   # follow logs
make dev-ps
```

Dev uses `docker/docker-compose.override.yml` (auto-loaded) with bind mounts +
writable rootfs; the frontend and backend reload on file changes.

### Prod stack (nginx + hardening)

```bash
make prod-up        # nginx :80/:443, hardened layer (read-only rootfs, caps dropped)
make prod-build     # rebuild + start
make prod-down
make prod-restart
make prod-logs
make prod-ps
make prod-health    # curl http://localhost/api/health
```

Prod = `docker-compose.yml` + `docker-compose.prod.yml` `--profile prod`.
The explicit `-f` disables the dev override automatically. Only nginx exposes
ports (`:80/:443`); backend/frontend/redis/node are internal to the Docker
networks.

### Whole-codebase status

```bash
make status     # one command: git branch/worktree + compose services + containers
make ps         # compose ps (base stack)
make redis-cli  # redis-cli shell inside the compose-managed redis
```

### Host tools

```bash
make backend-dev        # uvicorn --reload on :8000 (host, no Docker)
make backend-start
make frontend-dev       # next dev on :3000 (host)
make frontend-build / frontend-start
make test               # pytest (backend + CLI + scraper)
make test-frontend      # frontend vitest
make test-node          # node fetcher vitest
make test-go            # golang gofmt + go test (offline)
make test-all           # all four suites
make checks             # typecheck + lint
make cli-login ACCOUNT=myfb   # Python CLI Facebook login
make cli-scrape URL=... --browser MAXX=20 EXPORT=xlsx OUT=posts.xlsx
```

## Docker topology

```
Internet
  │  :80 / :443
  ▼
nginx              network: web (nginx + frontend + backend + node)
  ├── /api/*   → backend    (:8000, FastAPI)
  ├── /        → frontend   (:3000, Next.js standalone)
  └── /docs    → backend Swagger
backend            network: web + isolated
node               network: web + isolated   (facebook fetcher, :9334 internal)
redis              network: isolated         (shared rate-limit store, :6379 internal)
```

The database is **managed Supabase** (`SUPABASE_DB_URL`) — there is no
Postgres compose service anymore.

`docker/Dockerfile` is multi-target:
`frontend-build` → `frontend` / `frontend-dev`, and `backend` → `backend-dev`.
Playwright + Chromium are baked into the backend image; the frontend uses
Next.js `standalone` output.

> **Single replica only.** Job state lives in in-process worker threads — the
> backend must never be scaled horizontally. Scale nginx/frontend as needed;
> keep backend at one replica (see ROADMAP non-goals).

## Environment variables

Key compose-level vars (all have defaults baked into the compose files):

| Variable | Default | Meaning |
|---|---|---|
| `SUPABASE_DB_URL` | *(empty)* | managed Postgres DSN (no compose postgres service) |
| `REDIS_URL` | `redis://redis:6379/0` | shared rate-limit store (compose service name) |
| `USE_NODE` | 0 | delegate HTTP-mode fetches to the node service (finalplanv2 §14) |
| `NODE_BASE_URL` | `http://node:9334` | node service base URL on the stack network |
| `USE_GO_WORKER` | 0 | delegate the compute slice (parse -> normalize -> dedup) to the go worker's `POST /v1/parse` (finalplanv2 §5/§14; M7 flagged client) |
| `GO_WORKER_BASE_URL` | `http://127.0.0.1:8080` | go worker base URL; **M6** ships the container and the compose override to `http://go:8080` |
| `SCRAPE_TTL_SECONDS` | 0 | runtime TTL cache window per normalized URL; `0` = disarmed (backend's in-process consult seam, finalplanv2 §8) |
| `COOKIE_ENCRYPTION_KEY` | *(empty)* | Fernet key for encrypting user FB cookies at rest (future) |
| `WORKER_THREADS` | 4 | background scrape workers |
| `MAX_URLS_PER_JOB` | 300 | URL limit per scrape request (global cap above plan ceilings) |
| `SCRAPER_DELAY_SECONDS` | 2.5 | compliance throttle (do not lower in prod) |
| `CORS_ORIGINS` | `["http://localhost:3000","http://127.0.0.1:3000"]` | browser origins that may call the API directly |
| `NEXT_PUBLIC_API_URL` | *(empty)* | build-time backend origin from the *browser*'s view; **empty = same-origin via nginx** (the client already appends `/api` — never add it here) |
| `NEXT_PUBLIC_SITE_URL` | https://postharvest.space | canonical origin for sitemap/robots/OG |
| `BACKEND_MEM_LIMIT` etc. | 1g/1.5 … | prod-only resource caps |

## Database migrations

Alembic owns the deployed schema:

- The prod backend boots with `alembic upgrade head` before uvicorn
  (docker-compose.prod.yml) — every deploy applies pending migrations
  idempotently.
- A database that already has tables (created by the old `create_all` path —
  e.g. the current Supabase database) must be stamped **once** before the
  first upgrade-head boot, otherwise the initial migration fails with
  "relation already exists":
  `alembic -c backend/alembic.ini stamp head` (with `SUPABASE_DB_URL` set).
- Schema changes ship as new Alembic revisions only. `create_all` is kept
  solely as the dev/test convenience and never alters existing tables.

## Sharing cookies between CLI and container

The backend bind-mounts `../data` (repo `data/`), so `python cli.py login
--account NAME` on the host and the containerized backend read/write the same
`fb_cookies_*.json` / `fb_credentials.json` files. Saved sessions show up in
`/api/accounts` with no copying required.

## Production on a VPS (plan)

Current state: the prod stack is production-shaped but runs on your machine.
The path to public deployment (tracked in ROADMAP phases 3–5 and
DECISIONS.md O1/O4):

1. Install Docker + add your user to the `docker` group on the VPS.
2. Clone `https://github.com/teampostharvest/PostHarvest.git`, write `docker/.env`
   with real secrets.
3. Point `postharvest.space` DNS (A record) at the VPS public IP.
4. Wire Let's Encrypt at nginx (certbot on the host; scripts planned under
   `deploy/letsencrypt/`).
5. `make prod-up`; verify `make prod-health`, the `/docs` route, TLS.
6. Backup: `pg_dump` cron into a second volume + copy of `data/`
   (planned in `deploy/`).
7. Decide ops ergonomics (systemd unit vs Makefile-only) — **O1 in
   DECISIONS.md**.

Host-side artifacts live in `deploy/` (see `deploy/README.md`).