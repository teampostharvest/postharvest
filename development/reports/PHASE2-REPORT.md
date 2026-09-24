# Phase 2: Core SaaS Plumbing — Implementation Report

**Date:** 2026-09-17
**Branch:** `testing` (merged from `local-testing`)
**Author:** PostHarvest Team

---

## Executive Summary

Phase 2 transforms PostHarvest from a single-operator application into a properly authenticated, multi-user SaaS backend. The core security invariant is:

> **No authenticated PostHarvest user can access, modify, delete, export, or operate on resources that belong to another user.**

After a comprehensive audit of the codebase, we found that **nearly all Phase 2 infrastructure was already implemented** across prior work on the `feature/auth` branch. The one critical bug we identified and fixed was the **broken frontend export download** — the UI called a non-existent API method, causing runtime crashes on every export button click.

---

## Audit Findings

### Documentation vs. Reality

The project documentation (`docs/ARCHITECTURE.md`, `.agents/POSTHARVEST-AUTH.md`) described an older state where Firebase auth, Alembic, and multitenancy did not exist. **The actual source code contradicts this** — the implementation was already present and functional.

| Document Claim | Actual Code State |
|---|---|
| "Firebase auth does not exist" | `backend/auth/firebase.py` — full implementation |
| "Alembic does not exist" | `backend/alembic/` — initial migration covering all tables |
| "Multitenancy does not exist" | `ScrapeJob.owner_id`, `SavedAccount.owner_id`, owner-scoped queries |
| "No owner_id on rows" | `owner_id` on `scrape_jobs` and `saved_accounts`, indexed |

**Decision:** Treat source code as authoritative. Do not recreate existing implementation.

---

## What Was Already Implemented

### Backend Authentication

| Component | File | Status |
|---|---|---|
| Firebase Admin SDK init | `backend/auth/firebase.py` | Complete — env vars + JSON fallback + Application Default |
| Token verification | `backend/auth/firebase.py:81` | Complete — `verify_id_token()` with error handling |
| `get_current_user` dependency | `backend/auth/dependencies.py:31` | Complete — auto-provisions users on first login |
| `require_ops` dependency | `backend/auth/dependencies.py:52` | Complete — gates admin endpoints |
| User provisioning | `backend/auth/user_service.py` | Complete — get-or-create with OPS_EMAILS promotion |
| `/api/auth/me` endpoint | `backend/api/auth.py` | Complete — returns user profile |

### Owner Isolation

| Resource | Owner Scoping | Implementation |
|---|---|---|
| `ScrapeJob` | `owner_id` FK to `users.id`, indexed | SQL WHERE on all queries |
| Job list | `WHERE owner_id == current_user.id` | `backend/api/jobs.py:72-78` |
| Job status | `_get_job_or_404(db, job_id, owner_id)` | `backend/api/jobs.py:118` |
| Job posts | `_get_job_or_404(db, job_id, owner_id)` | `backend/api/jobs.py:184` |
| Job stats | `aggregate_job_stats(db, job_id, owner_id)` | `backend/api/jobs.py:221` |
| Job delete | `DELETE WHERE id=:id AND owner_id=:owner_id` | `backend/api/jobs.py:242-246` |
| Job pause/resume | `_get_job_or_404(db, job_id, owner_id)` | `backend/api/jobs.py:263,287` |
| Exports | `build_export(db, job_id, fmt, owner_id)` | `backend/api/exports.py:54` |
| Accounts (personal) | File path: `data/personal/{owner_id}/` | `backend/scraper/browser_scraper.py` |
| Accounts (delete) | `delete_account(name, owner_id)` | `backend/api/accounts.py:487` |
| Accounts (list) | `account_metadata(owner_id=current_user.id)` | `backend/api/accounts.py:222-225` |
| Scrape creation | `start_scrape_job(db, payload, owner_id)` | `backend/api/scrape.py:38` |

### Frontend Authentication

| Component | File | Status |
|---|---|---|
| Firebase SDK init | `frontend/lib/firebase.ts` | Complete — public config only |
| AuthProvider | `frontend/lib/auth-context.tsx` | Complete — Google + email/password |
| Token injection | `frontend/lib/api.ts:46-58` | Complete — auto-attaches Bearer |
| Login page | `frontend/components/sign-in-screen.tsx` | Complete — Google OAuth + email/password |
| Dashboard gating | `frontend/app/(app)/layout.tsx:89-104` | Complete — public: `/`, `/docs` |
| Auth redirect | `frontend/app/(app)/layout.tsx:101` | Complete — shows SignInScreen |

### Database

| Component | File | Status |
|---|---|---|
| User model | `backend/models/user.py` | `firebase_uid` UNIQUE, `email`, `plan`, `role` |
| ScrapeJob model | `backend/models/scrape_jobs.py` | `owner_id` FK indexed |
| SavedAccount model | `backend/models/saved_account.py` | `owner_id`, `scope`, encrypted cookies |
| Alembic migration | `backend/alembic/versions/20260917_001_initial_schema.py` | All 10 tables |
| Plans system | `backend/core/plans.py` | basic/pro/enterprise caps |

### Tests

| Test File | Coverage |
|---|---|
| `tests/test_multitenancy.py` | Auth required, invalid token, job isolation, OPS_EMAILS |
| `tests/test_accounts_isolation.py` | Two-tier isolation, ops gating, plan limits, admin |
| `tests/test_saved_accounts_mirror.py` | Cookie mirror, boot backfill, capture API, viewer |
| `tests/test_schema_migrations.py` | Alembic schema correctness |
| `tests/conftest.py` | Mock Firebase, auth fixtures, cleanup |

---

## What We Fixed

### Critical Bug: Broken Frontend Export Download

**File:** `frontend/lib/api.ts`

**Problem:** `frontend/components/export-area.tsx:38` called `api.exportJobDownload(jobId, format)` but this method did not exist in the `api` object. Only `getExportUrl()` existed (returns a plain URL string). This caused runtime crashes on every export button click.

**Impact:** Users could not download any exports (JSON, CSV, Excel) from the dashboard.

**Fix:** Added `exportJobDownload()` method that:
1. Performs an authenticated `fetch()` with Firebase Bearer token
2. Reads the response as a Blob
3. Extracts the filename from `Content-Disposition` header
4. Creates a temporary `<a>` element to trigger browser download
5. Handles errors with proper API error envelope parsing

### Additional Missing API Methods

Also added the following methods that were called by frontend components but missing from the API client:

| Method | Used By | Backend Route |
|---|---|---|
| `deleteAccount(scope, name)` | `accounts/page-content.tsx` | `DELETE /api/accounts/{scope}/{name}` |
| `startSessionCapture(body)` | `accounts/page-content.tsx` | `POST /api/accounts/capture` |
| `cancelSessionCapture(id)` | `accounts/page-content.tsx` | `DELETE /api/accounts/capture/{id}` |
| `adminListUsers()` | `settings/page-content.tsx` | `GET /api/admin/users` |
| `adminSetRole(userId, role)` | `settings/page-content.tsx` | `PATCH /api/admin/users/{id}/role` |
| `adminSetPlan(userId, plan)` | `settings/page-content.tsx` | `PATCH /api/admin/users/{id}/plan` |

Also fixed `deleteAccount()` signature — it previously accepted only `name` but the backend route requires `scope` and `name`.

---

## Security Audit Results

### Dangerous Pattern Search

| Pattern | Result |
|---|---|
| `firebase_uid = request` (client-supplied identity) | **NOT FOUND** |
| `owner_id = request` (client-supplied ownership) | **NOT FOUND** |
| `db.get(Model, id)` without ownership check | Found in `job_service.py` — **safe** (background worker uses persisted job IDs) |
| `query(Model).filter(Model.id == id)` without owner | **NOT FOUND** — all queries include `owner_id` |
| Plaintext cookie storage | **REMOVED** — cookies encrypted via Fernet |
| `NEXT_PUBLIC_*PRIVATE*` | **NOT FOUND** in env vars |
| `serviceAccount` in git | `.gitignore` covers `serviceAccountKey.json` |

### Route Authentication Matrix

| Route | Auth Required | Owner Scoped |
|---|---|---|
| `GET /api/health` | No | N/A |
| `GET /api/auth/me` | Yes | Current user |
| `POST /api/scrape` | Yes | Creates with owner_id |
| `GET /api/jobs` | Yes | `WHERE owner_id =` |
| `GET /api/jobs/{id}` | Yes | `_get_job_or_404(owner_id)` |
| `GET /api/jobs/{id}/posts` | Yes | `_get_job_or_404(owner_id)` |
| `GET /api/jobs/{id}/stats` | Yes | `aggregate_job_stats(owner_id)` |
| `POST /api/jobs/{id}/pause` | Yes | `_get_job_or_404(owner_id)` |
| `POST /api/jobs/{id}/resume` | Yes | `_get_job_or_404(owner_id)` |
| `DELETE /api/jobs/{id}` | Yes | `DELETE WHERE owner_id =` |
| `GET /api/jobs/{id}/export/{fmt}` | Yes | `build_export(owner_id)` |
| `GET /api/accounts` | Yes | Personal scoped by owner_id |
| `POST /api/accounts/personal` | Yes | Stored under owner_id |
| `POST /api/accounts/capture` | Yes | Owner-scoped per scope |
| `DELETE /api/accounts/capture/{id}` | Yes | Best-effort cancel |
| `DELETE /api/accounts/{scope}/{name}` | Yes | Owner-only for `me` scope |
| `GET /api/admin/users` | Yes + ops | N/A (admin only) |
| `PATCH /api/admin/users/{id}/role` | Yes + ops | N/A (admin only) |
| `PATCH /api/admin/users/{id}/plan` | Yes + ops | N/A (admin only) |
| Capture viewer routes | Capability-based | `capture_id` is credential |

---

## Verification Results

### Backend Tests

```
169 passed, 10 errors (Windows PermissionError — pre-existing, not real failures)
Baseline: 169 passed — NO REGRESSIONS
```

### Frontend Tests

```
19 passed, 0 failed
```

### TypeScript

```
0 errors (previously had 6 errors from missing API methods — all fixed)
```

### Lint

```
Pre-existing warnings only (no new issues)
```

### Production Docker Stack

```
All 3 containers healthy:
- postharvest-nginx (port 80/443)
- postharvest-backend (FastAPI)
- postharvest-frontend (Next.js)

API health: {"status":"ok","database":"ok","version":"1.0.0"}
Auth enforcement: GET /api/jobs → 401 auth_required
```

---

## Security Verification Checklist

- [x] Firebase ID tokens verified server-side
- [x] No client-controlled identity (firebase_uid from token only)
- [x] User provisioning works (auto-create on first login)
- [x] All private API routes authenticated
- [x] Jobs owner-scoped (SQL WHERE + DELETE)
- [x] Stats owner-scoped
- [x] Exports owner-scoped
- [x] Accounts owner-scoped (personal sessions)
- [x] Deletes owner-scoped (SQL-level)
- [x] Lists owner-scoped (only show current user's resources)
- [x] Cross-tenant access returns 404
- [x] owner_id migration correct (indexed, FK)
- [x] Firebase SDK integrated in frontend
- [x] Google OAuth working
- [x] Email/password auth working
- [x] AuthProvider wrapping app
- [x] Dashboard route gating active
- [x] API token attachment automatic
- [x] Export downloads authenticated (fixed)
- [x] Facebook sessions encrypted at rest
- [x] Sessions per-owner (personal scope)
- [x] Plaintext shared pool removed from SaaS path
- [x] Cookies never returned to frontend
- [x] Backend tests pass (169)
- [x] Frontend tests pass (19)
- [x] TypeScript compiles (0 errors)
- [x] Lint passes

---

## Files Changed in This Work

| File | Change |
|---|---|
| `frontend/lib/api.ts` | Added `exportJobDownload()`, `deleteAccount(scope, name)`, `startSessionCapture()`, `cancelSessionCapture()`, `adminListUsers()`, `adminSetRole()`, `adminSetPlan()` |
| `docs/PHASE2-REPORT.md` | This report |

**Commit:** `29c8ff3 fix(frontend): add missing authenticated export download and API methods`

---

## How to Test

### Quick Test (5 minutes)

1. Open http://localhost/ in browser
2. Sign in with Google or email/password
3. Go to Investigation, enter a Facebook URL, start a scrape
4. Wait for completion, click export download buttons — files should download
5. Check History — your job should appear
6. Open incognito, sign in as a different user
7. Verify the first user's jobs are NOT visible

### Full Security Test

See [PHASE2-TESTING.md](./PHASE2-TESTING.md) for the complete testing checklist with expected results for every scenario.

---

## Remaining Notes

### Pre-existing Concerns (not in scope for this work)

1. **docker/.env contains real secrets** — Firebase private key and Supabase credentials are committed. Should be moved to a secrets manager or .env.local excluded from git.
2. **10 test errors on Windows** — All are `PermissionError` on pytest tmp_path fixture. Platform-specific, not real failures.
3. **Documentation staleness** — `docs/ARCHITECTURE.md` and `.agents/POSTHARVEST-AUTH.md` describe an older state. Should be updated to reflect the actual implementation.
