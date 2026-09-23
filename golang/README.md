# golang — slice A (parse / normalize / dedup / export, in Go)

Plan: `plans/finalplanv2.md` §5 (Go, Compute) + §15 (Trigger Criteria).

This is slice A of the four-slice order **D → B → C → A**.  It is the
hermetic `go test` scaffolding for the Go service — **zero network, zero
browser, zero env** — so it can be proven on this machine without Node,
without Playwright, and without ever fetching a real page.

## What moved in from Python (mirrors, byte for byte)

| Python source of truth | Go mirror (`golang/`) |
|---|---|
| `backend/scraper/normalizer.py` (33-key canonical schema, `normalize_post`, `clean_text`, `extract_hashtags`, `extract_mentions`, `classify_post_type`) | `normalize.go` |
| `backend/scraper/dedup.py` (`make_fingerprint`, `dedup_key`, `dedup_posts`) | `dedup.go` |
| `backend/exporters/csv_exporter.py` (`FLAT_COLUMNS`, `flatten_post`, UTF-8-BOM + `\r\n`) | `export.go` |
| `backend/exporters/jsonl_exporter.py` (`PostJSONEncoder`, `ensure_ascii=False`, Python `(', ', ': ')` separators) | `export.go` |
| `backend/scraper/parser.py` (`parse_page`, `extract_posts_from_graphql`, `_extract_posts_from_scripts`, GraphQL/JSON + timestamp helpers) | `parser/` |
| Phase-1 Parse RPC (`shared/proto/postharvest.proto` `ParseRequest`/`ParseResponse`, finalplanv2.md §5/§6) | `httpapi/` |

The core is **stdlib-only** for slice A (`regexp`, `crypto/sha256`,
`encoding/json`, `encoding/csv`, `net/http`, `time`).  The only non-stdlib
dependency is `github.com/PuerkitoBio/goquery v1.9.2` (the plan's §5 pin),
**vendored** in `vendor/` so `GOPROXY=off go test ./...` still runs fully
offline.  The plan's other §5 extras (chi, excelize v2.11.0+, testify) are
*pin-notes only* in `dep_notes.go` and must not be fetched until a real
deployment slice asks for them.

## What the hermetic tests prove

`normalize_test.go`, `dedup_test.go`, `export_test.go` assert the Go
implementations against **golden bytes extracted from the real Python**
(see `tests` in each file — every expected value in this repo's tests came
from running `backend/scraper/normalizer.py` / `dedup.py` /
`backend/exporters/*`) rather than from belief:

* the 33 normalized keys, in Python's exact order;
* `normalizePost` output for a full `ParsedPost` (every one of the 33 keys,
  including `media_type`, `reactions` fallback-to-breakdown, `caption`
  `None`, `scraped_at` ISO, epoch `timestamp`);
* `classifyPostType` priority (video > link > image > text, post-hoc upgrade);
* `cleanText` control-char/whitespace semantics;
* fingerprint `2f724cac…b916` and `id:`/`fp:` dedup keys, first-wins dedup
  (`kept=2, removed=1`);
* the exact CSV bytes (`\ufeff` BOM + header + row, `\r\n` line endings) and
  the exact JSONL dict-ordered bytes with `, `/`: ` separators and no ASCII
  HTML-escaping.

`health_test.go` proves `/healthz` / `/readyz` (stdlib `net/http`, requested
by plan §5) via `httptest.NewRecorder` — in-memory, no sockets at all.

## §15 trigger check (honest gate record)

`plans/finalplanv2.md` §15 says §3–§12 work is gated on **at least one of
four triggers firing today** (real compute bottleneck / real horizontal
scaling need / real reliability need isolating Facebook traffic / team or
ownership reasons).  None of the four is demonstrably true from this
repo's bytes at slice-A time:

* no profiling data pointing at CPU-bound parse/export (the first trigger's
  own wording), and `WORKER_THREADS` tuning is untried;
* still a single VPS / single backend replica — §11 Phase 1 has no consumer;
* no PageIsolation incident record in `plans/faults.md`;
* no team/ownership or audit reason on record.

Slice A was nevertheless authored now **because the user explicitly asked**
("go on do the rest … START GO WORKER PLS").  It is therefore strictly the
hermetic, non-deployable compute core: the exact Go code the plan's §12
would run, proven byte-equal to Python, with **no container, no feature
flag flip, no deployment**.  Milestones M2/M3 added the parser port
(`golang/parser/`, byte-vs-`parser.py` goldens) and the Phase-1 HTTP surface
(`golang/httpapi/`: `POST /v1/parse` plus `/healthz`/`/readyz`, byte-vs-real
Python-Pipeline goldens) — still hermetic (`httptest`, no sockets) and still
unwired: nothing calls it over a socket and FastAPI's flagged Python client
is milestone M7.  The §15 gate still holds for anything operational; this
scaffolding only removes the "zero `.go` files" gap so that when a trigger
fires, the Go side is already byte-proven against Python rather than
greenfield.