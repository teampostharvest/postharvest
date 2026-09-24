# Phase 2: Testing Checklist

**URL:** http://localhost/ (production Docker stack)
**Stack:** nginx :80 → backend :8000 + frontend :3000

---

## 1. Public Pages (no login required)

| # | Action | Expected Result | Pass |
|---|---|---|---|
| 1.1 | Visit `/` | Landing page loads with URL input hero | [ ] |
| 1.2 | Visit `/docs` | Documentation page loads | [ ] |
| 1.3 | Visit `/docs/getting-started` | Individual doc page loads | [ ] |
| 1.4 | `GET /api/health` | `{"status":"ok","database":"ok","version":"1.0.0"}` | [ ] |

## 2. Authentication Gate

| # | Action | Expected Result | Pass |
|---|---|---|---|
| 2.1 | Visit `/investigation` (not logged in) | Redirected to sign-in screen | [ ] |
| 2.2 | Visit `/history` (not logged in) | Redirected to sign-in screen | [ ] |
| 2.3 | Visit `/accounts` (not logged in) | Redirected to sign-in screen | [ ] |
| 2.4 | Visit `/settings` (not logged in) | Redirected to sign-in screen | [ ] |
| 2.5 | Visit `/pricing` (not logged in) | Redirected to sign-in screen | [ ] |
| 2.6 | `GET /api/jobs` (no token) | **401** `{"error":{"code":"auth_required"}}` | [ ] |
| 2.7 | `POST /api/scrape` (no token) | **401** | [ ] |

## 3. Login & Signup

| # | Action | Expected Result | Pass |
|---|---|---|---|
| 3.1 | Click "Sign In" → Google | Firebase Google OAuth popup opens | [ ] |
| 3.2 | Complete Google sign-in | Redirected to dashboard | [ ] |
| 3.3 | Click "Sign In" → email/password | Email/password form appears | [ ] |
| 3.4 | Enter valid credentials | Signed in, redirected to dashboard | [ ] |
| 3.5 | Enter wrong password | Error message shown, stays on login | [ ] |
| 3.6 | Switch to "Create account" | Form switches to signup mode | [ ] |
| 3.7 | Create new account with email/password | Account created, signed in | [ ] |
| 3.8 | First login auto-provisions user | `GET /api/auth/me` returns profile with `plan: "basic"` | [ ] |
| 3.9 | Click "Sign Out" | Signed out, redirected to home | [ ] |

## 4. Authenticated API

| # | Action | Expected Result | Pass |
|---|---|---|---|
| 4.1 | `GET /api/auth/me` | Returns your profile (`firebase_uid`, `email`, `plan`, `role`) | [ ] |
| 4.2 | `GET /api/jobs` | Returns `{"items":[], "total":0}` (empty for new user) | [ ] |

## 5. Scraping

| # | Action | Expected Result | Pass |
|---|---|---|---|
| 5.1 | Go to Investigation, enter valid Facebook URL | URL validated, form ready | [ ] |
| 5.2 | Click "Start Scrape" | **201** returned, progress section appears | [ ] |
| 5.3 | Watch progress update | Posts count increases, status shows "running" | [ ] |
| 5.4 | Job completes | Status shows "completed", posts table populates | [ ] |
| 5.5 | `GET /api/jobs` | New job appears in list | [ ] |
| 5.6 | `GET /api/jobs/{id}` | Returns full job status with sources | [ ] |
| 5.7 | `GET /api/jobs/{id}/posts` | Returns paginated posts | [ ] |
| 5.8 | `GET /api/jobs/{id}/stats` | Returns KPI aggregates | [ ] |

## 6. Export Downloads (FIXED)

| # | Action | Expected Result | Pass |
|---|---|---|---|
| 6.1 | Click "JSON" download button | File downloads as `facebook_posts.json` | [ ] |
| 6.2 | Click "CSV" download button | File downloads as `facebook_posts.csv` | [ ] |
| 6.3 | Click "Excel" download button | File downloads as `facebook_posts.xlsx` | [ ] |
| 6.4 | Download while logged out | Error shown, no file downloads | [ ] |
| 6.5 | Open downloaded JSON | Valid JSON with post data | [ ] |
| 6.6 | Open downloaded CSV | Opens in spreadsheet, columns correct | [ ] |
| 6.7 | Open downloaded Excel | Opens in Excel, multiple sheets visible | [ ] |

## 7. Job Management

| # | Action | Expected Result | Pass |
|---|---|---|---|
| 7.1 | Pause a running job | Status changes to "paused" | [ ] |
| 7.2 | Resume a paused job | Status changes to "queued", continues | [ ] |
| 7.3 | Delete a job | Job removed from list, returns 204 | [ ] |
| 7.4 | `DELETE /api/jobs/{id}` for unknown ID | **404** | [ ] |

## 8. Cross-User Isolation (CRITICAL)

**Setup:** Open two browsers (or incognito windows). Sign in as User A in one, User B in the other. Both create at least one scrape job.

| # | Action | Expected Result | Pass |
|---|---|---|---|
| 8.1 | User A: `GET /api/jobs` | Only shows User A's jobs, NOT User B's | [ ] |
| 8.2 | User B: `GET /api/jobs` | Only shows User B's jobs, NOT User A's | [ ] |
| 8.3 | User A: `GET /api/jobs/{user_b_job_id}` | **404** | [ ] |
| 8.4 | User A: `GET /api/jobs/{user_b_job_id}/posts` | **404** | [ ] |
| 8.5 | User A: `GET /api/jobs/{user_b_job_id}/stats` | **404** | [ ] |
| 8.6 | User A: `GET /api/jobs/{user_b_job_id}/export/json` | **404** | [ ] |
| 8.7 | User A: `DELETE /api/jobs/{user_b_job_id}` | **404** | [ ] |
| 8.8 | User A: `POST /api/jobs/{user_b_job_id}/pause` | **404** | [ ] |
| 8.9 | User A: `POST /api/jobs/{user_b_job_id}/resume` | **404** | [ ] |

## 9. Accounts Isolation

| # | Action | Expected Result | Pass |
|---|---|---|---|
| 9.1 | User A: `GET /api/accounts` | Shows `{ops: [...], mine: [...]}` | [ ] |
| 9.2 | User A adds personal session | Appears in `mine` list only | [ ] |
| 9.3 | User B: `GET /api/accounts` | Does NOT show User A's personal sessions | [ ] |
| 9.4 | User B: `DELETE /api/accounts/me/{user_a_session}` | **404** | [ ] |
| 9.5 | User A: `DELETE /api/accounts/me/{own_session}` | **204** | [ ] |
| 9.6 | Non-ops: `DELETE /api/accounts/ops/{session}` | **403** `admin_required` | [ ] |

## 10. Plan Limits (basic tier)

| # | Action | Expected Result | Pass |
|---|---|---|---|
| 10.1 | Scrape 6+ URLs | **429** `plan_limit` (basic: max 5 URLs) | [ ] |
| 10.2 | Set `max_posts > 500` | **429** `plan_limit` | [ ] |
| 10.3 | Have 2+ concurrent running jobs | **429** `plan_limit` | [ ] |
| 10.4 | Add 2+ personal accounts | **429** `plan_limit` (basic: max 1) | [ ] |

## 11. Admin (ops role)

| # | Action | Expected Result | Pass |
|---|---|---|---|
| 11.1 | Non-ops: `GET /api/admin/users` | **403** `admin_required` | [ ] |
| 11.2 | Ops: `GET /api/admin/users` | Returns all users | [ ] |
| 11.3 | Ops: `PATCH /api/admin/users/{id}/role` | Role updated | [ ] |
| 11.4 | Ops: `PATCH /api/admin/users/{id}/plan` | Plan updated | [ ] |
| 11.5 | Ops cannot demote self | **400** `invalid_role_change` | [ ] |

## 12. Identity Spoofing Resistance

| # | Action | Expected Result | Pass |
|---|---|---|---|
| 12.1 | Send request with `firebase_uid` in body | Body field ignored, identity from token | [ ] |
| 12.2 | Send request with `owner_id` in body | Body field ignored, identity from token | [ ] |
| 12.3 | Use expired Firebase token | **401** `token_expired` | [ ] |
| 12.4 | Use malformed Bearer token | **401** `invalid_token` | [ ] |
| 12.5 | Use empty Bearer token | **401** `invalid_token` | [ ] |

---

## Summary

| Category | Tests | Pass | Fail |
|---|---|---|---|
| Public pages | 4 | | |
| Auth gate | 7 | | |
| Login/signup | 9 | | |
| Authenticated API | 2 | | |
| Scraping | 8 | | |
| Export downloads | 7 | | |
| Job management | 4 | | |
| Cross-user isolation | 9 | | |
| Accounts isolation | 6 | | |
| Plan limits | 4 | | |
| Admin | 5 | | |
| Identity spoofing | 5 | | |
| **TOTAL** | **70** | | |
