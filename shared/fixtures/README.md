# shared/fixtures — golden contract payloads

Cross-language golden fixtures (finalplanv2.md §6 "Critical Note 8"): the JSON
shapes the three services agree on, asserted by each service's test suite so a
change in one language cannot silently drift from the others.

**Editing order (never skip):**
1. Change `shared/proto/postharvest.proto` FIRST (add fields with the next
   unused numbers only — see the field-discipline block at the top of the file).
2. Mirror the change in `node/src/types.ts` (same names; `null` for missing
   scalars, `[]` for repeated).
3. Update/create the fixtures here so the emitting/target services' tests keep
   passing against the new shape.

## Fixtures

| File | Contract message | Meaning |
|---|---|---|
| `fetch_request.browser.json` | `FetchRequest` | Browser-mode request with session cookie lines, scroll budget and post quota. |
| `fetch_response.html.json` | `FetchResponse` | Canonical http-mode response: `updated_cookies: []`, `browser_stats: null` (proto3 JSON defaults). |
| `fetch_response.browser.json` | `FetchResponse` | Browser-mode response: assembled snapshot in `raw_payload` (base64), capture summary in `browser_stats`, refreshed session in `updated_cookies`. |
| `browser_snapshot.html` | (asset) | The exact bytes that `fetch_response.browser.json`'s `raw_payload` base64-encodes. Mirrors the snapshot format produced by the Python `fetch_with_browser` (dom pool + script pool + `data-fb-graphql-feed` blocks + final DOM) so the parser path is identical. |

## Consistency rules enforced by tests

- `fetch_response.browser.json` → `raw_payload` base64-decodes to the exact
  bytes of `browser_snapshot.html` (Node and backend suites both assert this).
- `fetch_response.html.json` must keep `updated_cookies: []` and
  `browser_stats: null`; node's `/fetch` http-mode response must match that
  canonical shape exactly (key-for-key).
- The browser snapshot is a *sample* of the shape, not real captured data —
  keep it small so anyone can eyeball the format.