# node

The only service in PostHarvest that talks to Facebook (finalplanv2.md §4).

It is **not** part of the public surface — FastAPI proxies fetch work to it.
Phase 1 transport is HTTP + JSON with shapes defined in
`shared/proto/postharvest.proto` (see `src/types.ts` for the TS mirror).

## What it does today

- `GET /healthz` — liveness.
- `GET /readyz` — readiness; pings Redis when `REDIS_URL` is configured
  (reports `503` when unreachable, `200 ready` when unconfigured).
- `POST /fetch` — HTTP-mode fetch of one `https://www.facebook.com/...` URL,
  returning a base64 `FetchResponse` (`status_code`, `final_url`,
  `content_type`, `raw_payload`, `fetched_at_ms`). Browser mode returns
  `501 browser_mode_not_implemented` until the Playwright transport lands.

## Compliance invariants (mirrored from `backend/scraper/`)

- Single honest, fixed Chrome UA — never rotated, never spoofed.
- `robots.txt` respected per host (allowlist-only policy when unreadable;
  `SCRAPER_ROBOTS` disables enforcement).
- One token-bucket token per request, keyed `account:{id}` or `host:{host}`,
  shared across instances via Redis (`ratelimit:*` keyspace) with an
  in-process fallback so a Redis outage never hard-blocks fetching.
- Exponential backoff with jitter on 429/5xx (capped 30 s, honors
  `Retry-After`); 10 MB body cap; no cookies persisted.

## Configuration

| Env | Default | Meaning |
|---|---|---|
| `HOST` | `0.0.0.0` | Bind host |
| `PORT` | `9334` | Bind port |
| `REDIS_URL` | — | Optional shared rate-limiter store |
| `SCRAPER_DELAY_SECONDS` | `2.5` | Min gap between requests |
| `SCRAPER_TIMEOUT_SECONDS` | `20` | Per-request timeout |
| `SCRAPER_MAX_RETRIES` | `3` | Retry threshold before failing |
| `SCRAPER_ROBOTS` | `1` | Enforce robots.txt (`0` disables) |
| `MAX_BODY_BYTES` | `10485760` | Body safety cap |
| `LOG_LEVEL` | `info` | `debug` enables Fastify logging |

## Develop

```bash
npm ci
npm run dev          # tsx watch on :9334
npm test            # Vitest (hermetic — no network, no Redis)
npm run typecheck   # tsc --noEmit
npm run build       # tsc -> dist/
```

## Contract flow

```
FastAPI (Python) ──POST /fetch──▶ node ──GET──▶ Facebook
                               ◀── FetchResponse ──
```

Fetch errors map to the unified envelope `{"error":{"code","message"}}` with
status codes from `src/errors.ts` (`rate_limited→429`, `timeout→504`,
`robots_disallowed→403`, `page_unavailable/network_error→502`).

## Next steps (not this slice)

- Playwright browser transport (`mode: "browser"`).
- FastAPI `fetch_client.py` + feature flag so orchestrator fetches via this
  service.
- Compose wiring (`docker/docker-compose.yml`) + service discovery.
- Golden-fixture contract test group against `shared/fixtures/`.