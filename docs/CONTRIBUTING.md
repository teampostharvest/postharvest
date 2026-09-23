# Contributing

How to build, test and ship changes to PostHarvest. Short version: **everything
goes through branches `fix/bugs → enhance → testing → main`, nothing lands
straight on a long-lived branch, and changes only merge after the CI checks
pass.**

## Setup

```bash
# backend (Python 3.11+)
make backend-install        # pip install -r backend/requirements.txt
# tests deps
python -m pip install -r tests/requirements.txt

# frontend (Node 22)
make frontend-install       # npm ci in frontend/

# full Docker stack (dev)
make dev
```

Secrets/env: `cp .env.example .env` for CLI/tests only. The Docker stack uses
`docker/.env` — see [DEPLOYMENT.md](./DEPLOYMENT.md) for the split.

> Host interpreters can't run the browser mode without Playwright's browser
> binaries. In Docker it's baked in. On the host:
> `python -m playwright install --with-deps chromium`.

## Verifying a change — always run these

```bash
make test              # backend pytest (uses hermetic mocks; never the network)
make test-frontend     # frontend vitest
make test-node         # node fetcher vitest (node/)
make test-go           # go worker, offline + vendored (never runs go get)
make typecheck         # tsc --noEmit
make lint              # eslint (frontend)
make lint-ff           # oxlint (fast frontend lint)
```

### Test suite facts

- Backend: 297 `test_*` functions across 28 files in `tests/` (~309 collected
  cases: API, infra, e2e, normalization, browser wall-handling, dedup/stats,
  exporters, error handling, job state machine, URL validation, GraphQL
  extractor, plus the node/go seam suites).
- Hermetically sealed: `conftest.py` neutralises every seam flag (node, go),
  installs a fake scraper, points the DB at a temp path, and no test ever
  touches the network.
- Frontend has vitest tests for `components/ui/badge` and `lib/utils`.
- `node/` (fetch service) and `golang/` (compute worker) each ship their own
  hermetic suites — vitest and `GOPROXY=off go test`. The Go tests never
  resolve a module (everything is vendored) and never open a socket.

## CI

Seven GitHub Actions workflows (`.github/workflows/`), all triggered on push +
PR to branches `[main, master, testing]`:

| Workflow | Runs |
|---|---|
| `pytest.yml` | `pytest tests/ -v --tb=short` (Python 3.11 + 3.12 matrix) |
| `vitest.yml` | `npm test` (Node 22) |
| `typecheck.yml` | `tsc --noEmit` |
| `eslint.yml` | `npm run lint` |
| `lighthouse.yml` | build + start + `lhci autorun`, uploads `.lighthouseci/` artifact |
| `node-test.yml` | `node/`: typecheck + vitest (Node 22) |
| `go-test.yml` | `golang/`: gofmt + `GOPROXY=off go test -count=1 ./...` (vendored, offline) |

`pytest.yml` paths-filter to `backend/**` + `tests/**`; `vitest/typecheck/eslint`
to `frontend/**`.

## Branch flow

```
fix/bugs ──────────► enhance ──────────► testing ──► main
   (bug fixes,        (features / new     (release        (stable)
    small, focused)     work)              candidates)
```

- Branch per change off the current integration branch; commit small and
  focused; push as soon as tracking exists so CI runs on it.
- Bug fixes land on `fix/bugs`; feature work lands on `enhance`. Nothing is
  committed to `main` except from `testing`.
- Always verify on `testing` (full CI + a real Docker prod run) before merging
  to `main`.

## Demo flows to sanity-check before merging (Docker)

```bash
make dev                     # stack up
# 1. scrape a public page
curl -X POST http://localhost:8000/api/scrape \
  -H 'Content-Type: application/json' \
  -d '{"urls":["https://www.facebook.com/<public-page>"],"use_browser":true,"max_posts":5}'
# 2. watch progress
curl -s http://localhost:8000/api/jobs/<job_id>
# 3. export
curl -J -O http://localhost:8000/api/jobs/<job_id>/export/json
make dev-down
```

## Style & conventions

- Python: FastAPI + pydantic v2; route handlers thin, services own work;
  errors always via the `{"error":{code,message}}` contract
  (`backend/core/exceptions.py`). No test hits the live network.
- TypeScript: strict mode; `frontend/lib/api.ts` is the typed API client — go
  through it, don't hand-roll `fetch`.
- No emojis in code/docs unless explicitly requested. No new dependencies
  without updating `backend/requirements.txt` / `frontend/package.json` and
  the README stack table.

## Decision record

Product and infra decisions (incl. the cookie-model and Docker-layout choices
currently driving the project) are locked in [DECISIONS.md](../DECISIONS.md);
the phased plan is in [ROADMAP.md](../ROADMAP.md). Read both before starting
anything that touches tenancy, auth, cookies, or the Docker topology — those
areas are deliberately planned, not improvised. When in doubt, raise a
question rather than guessing at an architecture.

## Docs

Long-form guides live in `docs/` (see [README](./README.md)). Update the
relevant doc alongside any change that affects the API surface, the job model,
deployment, or the scraper contract.