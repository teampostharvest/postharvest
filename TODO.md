# TODO — PostHarvest

Status snapshot: 2026-09-18. Legend: **✅** done · **⬜** pending · **🔶** decided-against / needs-product-call
(Roadmap items from [ROADMAP.md](./ROADMAP.md), reality-checked against the code.)

**Execution order (agreed 2026-09-18): hardening batch → Phase 4 → launch (Phase 3 + Phase 5) → Phase 6.**

---

## ✅ Done

### Core product — scraper + API
- [x] Job-based Facebook page/profile scraping with two modes: **HTTP** (fast, ~3 s) and **browser** (Playwright + Chromium, JS, scrolling, saved-cookie auth)
- [x] Job manager: live progress, best-effort cancellation, **pause/resume**, per-source lifecycles (`queued → running → completed | failed | cancelled`)
- [x] **CrawlState checkpointing** — resume paused/crashed jobs where they left off
- [x] Normalized **33-key post schema** (missing fields stay `null`/`[]`, never fabricated)
- [x] Two-layer dedup (post-id + SHA-256 fingerprint)
- [x] Filters: date range, post type (`text/image/video/link/all`), `max_posts`
- [x] Rate limiter (token bucket) + retry manager (circuit breaker, exponential backoff on 429/5xx)
- [x] Configurable proxy support (`ProxyManager`)
- [x] Exports: **JSON / CSV / XLSX / JSONL** (streaming)
- [x] Consistent error envelope `{"error": {"code", "message"}}`; `/api/health` with DB latency check
- [x] Standalone **CLI** (`python cli.py` — login, accounts, scrape, export)

### Database
- [x] SQLite out of the box; Postgres via `DATABASE_URL`
- [x] **Supabase** (managed Postgres) as the production database (`SUPABASE_DB_URL`) — schema stamped **once** with `alembic -c backend/alembic.ini stamp head`
- [x] **Alembic owns the schema**: repo has the initial migration `20260917_001_initial_schema.py`; backend runs `alembic upgrade head` on boot (no more `create_all` in prod)

### Containers & deploy tooling
- [x] Dev compose stack: backend :8000 + frontend :3000, hot reload (`make dev`/`dev-up`)
- [x] Prod compose layer `docker-compose.prod.yml`: **nginx** :80/:443 + backend + frontend + postgres, non-root, read-only rootfs, dropped caps, mem/CPU limits (`make prod-up`)
- [x] Playwright + Chromium baked into the backend image; browser mode verified inside Docker
- [x] Makefile (dev/prod/test/lint/backend/frontend targets)
- [x] Env split: `docker/.env` (compose) + root `.env` (CLI/tests), both gitignored; tracked templates `docker/example.env` + `.env.example`
- [x] Single-replica constraint documented (in-process job workers)

### Frontend
- [x] Next.js dashboard (Jost design system) with jobs/exports/accounts screens; public Home with login entry
- [x] WebSocket capture bridge for saved-account captures
- [x] **Pricing page + top-nav menu** (`df4861e`)

### Quality, docs & compliance
- [x] Backend pytest suite (**179 tests**) + frontend vitest
- [x] `tsc` typecheck, ESLint, **oxlint pinned 1.83.0**, Lighthouse CI
- [x] Docs: README, `docs/{API,ARCHITECTURE,DEPLOYMENT,README}`, `DECISIONS.md`, `COMPLIANCE.md`, `ROADMAP.md`
- [x] `.editorconfig`
- [x] **CONTRIBUTING.md** (branch model + commit conventions + promote flow), **CODE_OF_CONDUCT.md** (enforcement → `report@postharvest.space`), **issue templates** (bug + feature) + **pull request template**
- [x] Dependabot + Renovate configs

### Release engineering (this stage)
- [x] **semantic-release pipeline** wired (commit-analyzer, release-notes-generator, changelog, npm, git, github plugins) — versions from `frontend/package.json`
- [x] Branch ladder **`master` ← `next` ← `testing` ← `feature/*`**; `master` = default branch; releases from master only
- [x] **`production`** marker branch (mirrors master)
- [x] **Commitlint** + husky 9.1.7 gate on every commit (`feat|fix|chore…` convention)
- [x] Release scripts: `npm run release` / `release:dry` / **`promote`** + `scripts/promote.sh` (ladder runner, `GATE=1` local pre-push checks)
- [x] CI gates run on `master`/`next`/`testing` pushes + PRs; release workflow fires on `master` only
- [x] **v1.0.0 released in CI** (`e781e7a`, first release folding the 134-commit history)
- [x] **v1.0.1 released in CI** via full promote (`testing → next → master`, merge `55ec67a`; tag `cb00ce7`) — genuine end-to-end run caught and fixed a script bug (`91db989`), then released
- [x] `CHANGELOG.md` auto-generated; public GitHub releases (repo is public — flagged & accepted)

---

## ⬜ Pending — roadmap phases (from [ROADMAP.md](./ROADMAP.md))

### Phase 2 — Core SaaS plumbing
- [x] **Firebase server-side verification on every `/api` route** — `verify_id_token()` in `backend/auth/firebase.py`; `get_current_user` dependency on all protected routes; auto-provisions users on first login
- [x] **`owner_id` / tenant ownership and cross-user isolation** — `scrape_jobs.owner_id` FK indexed; all queries filtered by `owner_id`; SQL-level DELETE ownership; Alembic migration covers full schema
- [x] **Frontend Firebase integration** (Google OAuth + email/password, shared auth provider, dashboard gating) — `frontend/lib/firebase.ts`, `auth-context.tsx`, route gating in `(app)/layout.tsx`, Bearer token auto-injection in `api.ts`
- [x] **Encrypted per-owner FB session storage** + owner-scoped accounts API — Fernet encryption via `COOKIE_ENCRYPTION_KEY`; personal sessions under `data/personal/{owner_id}/`; `SavedAccount` model with `owner_id`; owner-scoped API endpoints

### Phase 3 — Production topology
- [ ] **Let's Encrypt / nginx TLS** + point `postharvest.space` DNS (public deploy still pending — stack currently runs on the laptop, LAN)
- [ ] **CORS audit** (restrict to public origin) + hide `/docs` in prod
- [x] nginx service + docker network layout already in place (roadmap's local postgres container is superseded by Supabase)

### Phase 4 — Hardening & abuse protection
- [x] **Quota enforcement** (concurrency + per-job caps) — `concurrent_jobs`, `urls`, `max_posts`, `personal_accounts` enforced in `job_service.py` + `accounts.py`; observable via `GET /api/usage` + `GET /api/plans` (2026-09-18)
- [x] **Tier gating** — self-service `PATCH /api/auth/me/plan` deleted; tiers are operator-assigned only via `PATCH /api/admin/users/{id}/plan` (2026-09-18)
- [x] **DB pool guardrails** — Postgres pool capped at 5 with 10 s checkout timeout + 5 s TCP connect timeout + regression tests in `tests/test_db_pool.py` (2026-09-18)
- [x] **API timeout + retry UI** — 20 s fetch ceiling, polling hooks expose `{error, reload}`, sidebar shows retry instead of permanent loaders (2026-09-18)
- [x] **`GET /api/jobs?status=` filter** powering the ops-panel active-jobs list (2026-09-18)
- [x] **Startup sweep** for orphaned `running` jobs + quota release — `sweep_orphaned_jobs()` in `job_service.py` runs in lifespan: `running` → `failed` with `suspended_by_restart` audit row, `queued` re-submitted to workers, `paused` untouched; also fixed `POST /resume` to actually hand the job to a worker (was a no-op status flip stranding jobs in `queued`) — `tests/test_startup_sweep.py` (2026-09-18)
- [ ] Outbound **proxy rotation** wiring exposed via env (`PROXY_URL(S)` flow per-job today; `ProxyManager` rotation never instantiated from env)
- [ ] **Structured logs + request IDs** + basic error telemetry (logs exist as `postharvest.*`; no request IDs yet)
- [ ] Daily post ceiling — 🔶 recommend against inventing one (per-source/per-job caps already bound spend); pricing decision first if billing needs it

### Phase 5 — Launch
- [ ] Seed admin account + full-funnel smoke test on prod Postgres over nginx/TLS
- [ ] Load test: N concurrent users/jobs/exports; tune worker threads
- [ ] Backup strategy (pg_dump cron into a second volume; note Supabase-hosted backups)

### Phase 6 — Monetization (seams only — no UI until billing ships)
- [ ] `Organization`/`plan` + subscription hooks behind the existing limits resolver
- [ ] Stripe checkout + webhooks + customer portal; plan gating by tier
- [ ] Plan-aware UI (strictly post-billing)
- [ ] 🔶 CONTRADICTION (2026-09-18): plan-aware UI already shipped (pricing catalog from `/api/plans`, tier badges, operator assignment in Settings) despite the "no UI" rule — bless it or gate it behind a billing flag

---

## ⬜ Pending — engineering follow-ups (hardening batch, agreed order)

- [ ] **Startup sweep** for orphaned `running` jobs on boot (see Phase 4)
- [ ] **Request IDs** in logs + error responses (see Phase 4)
- [ ] **Release → deployment/version consumption** — images still `:latest`, `backend settings.version` hardcoded `"1.0.0"` in `/api/health`; wire release versions through
- [ ] **Add Python 3.13 to the CI matrix** (currently runs 3.11, 3.12)
- [ ] **Flaky WS mirror test still flakes in CI** — `test_capture_ws_bridge_forwards_frames` → `CancelledError` (failed on the master push 2026-09-17: 178 passed / 1 failed). Earlier fix `91820b3` helped locally but is insufficient under CI load
- [ ] **GitHub branch protection** on `master` (require PR + CI checks, only `next → master`) — needs repo admin, do at launch
- [ ] 🔶 **Redis deferred to post-launch** (2026-09-21): single-replica backend in-process job/rate-limit state means Redis has no payoff today; re-evaluate only when ≥2 replicas / separate workers become real (then: PG `SKIP LOCKED` queue first, Redis second)

---

## Who did what

- Reza — reza1234khan1234@gmail.com — core development
- Claude — n/a (AI assistant) — release tooling, docs
- Kazi Samir — kzsamir849@gmail.com — earlier foundation work
- dependabot[bot] — 49699333+dependabot[bot]@users.noreply.github.com — dependency bumps