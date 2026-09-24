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
| `backend/exporters/xlsx_exporter.py` (4-sheet styled workbook, openpyxl) | `export_xlsx.go` |
| `backend/scraper/parser.py` (`parse_page`, `extract_posts_from_graphql`, `_extract_posts_from_scripts`, GraphQL/JSON + timestamp helpers) | `parser/` |
| Phase-1 Parse RPC (`shared/proto/postharvest.proto` `ParseRequest`/`ParseResponse`, finalplanv2.md §5/§6) | `httpapi/` |
| §8(c) idempotency cache (Redis at deployment; hermetic in-memory seam now) | `idempotency/` |

The core is **stdlib-only** for slice A (`regexp`, `crypto/sha256`,
`encoding/json`, `encoding/csv`, `net/http`, `time`) apart from the two
vendored deps the plan's §5 pins: `github.com/PuerkitoBio/goquery v1.9.2`
(parser, from M2) and `github.com/xuri/excelize/v2 v2.11.0` (XLSX, from
M5 — the plan's `qax-os` module path was renamed upstream; see
`dep_notes.go`).  Both live in `vendor/`, so `GOPROXY=off go test ./...`
still runs fully offline.  The plan's other §5 extras (chi, testify) are
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

`parser/` (M2) and `httpapi/` (M3) extend the same proof style: the parser
asserts byte-for-byte against goldens produced by running real
`backend/scraper/parser.py` (`parser/testdata/gen_goldens.py`), and
`httpapi` asserts the `/v1/parse` wire bytes against real-Python goldens of
the full pipeline (parse → normalize → dedup → `json.dumps`, see
`httpapi/testdata/gen_goldens.py`), driving every request through
`httptest` only.  `idempotency/` (M4) proves the §8(c) retry-safety
semantics — cached ParseResponse replay without re-parse, short-TTL expiry,
`idemp:` keyspace — against an in-memory store implementing the same
`Store` seam a deployment Redis client will implement later.

`export_xlsx.go` (M5) mirrors `backend/exporters/xlsx_exporter.py`: the
styled 4-sheet workbook (Posts / Engagement / Media / Metadata) with frozen
header row, auto-filter, capped column widths, bold-blue header, custom
date number format, hyperlinks and wrap alignment.  Openpyxl and excelize
can never produce byte-identical `.xlsx` files (zip metadata differs), so
the honest proof here is **logical cell-model equality**: `export_xlsx_test.go`
unzips both the real-Python golden (`testdata/facebook_posts_golden.xlsx`,
from `testdata/gen_xlsx_golden.py`) and the Go output and compares values,
number formats, hyperlinks, panes, auto-filter ranges and column widths for
all four sheets (the generator row in Metadata is asserted separately —
each exporter honestly names itself).  Date cells are decoded from their
Excel serials and proved to keep the original wall-clock time.  The flat
rows themselves are additionally byte-proven by `export_test.go` (same
`FLAT_COLUMNS` as the CSV path).

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
("go on do the rest … START GO WORKER PLS").  Milestones M2/M3/M4/M5 built
the byte-proven core: the parser port (`golang/parser/`, byte-vs-`parser.py`
goldens), the Phase-1 HTTP surface (`golang/httpapi/`: `POST /v1/parse` plus
`/healthz`/`/readyz`, byte-vs-real-Python-Pipeline goldens), the §8(c)
idempotency seam (`golang/idempotency/`: `Store` interface + in-memory
implementation), and the XLSX exporter (`export_xlsx.go`,
cell-model-vs-real-openpyxl goldens; excelize was the second network fetch
of the slice, required by §5's pin and vendored).

**M6 (deployment slice) and M7 (FastAPI flagged client) then made it
deployable *in code*, with zero live exposure:**

* **M7** — `backend/services/go_worker.py` is the FastAPI→Go seam behind the
  `USE_GO_WORKER` flag (default off, same discipline as `USE_NODE`): when
  flipped, HTTP-mode scrapes route the compute slice (parse → normalize →
  dedup) through `POST /v1/parse` instead of in-process Python, proven
  byte-parity against the real pipeline in `tests/test_go_worker_seam.py`.
* **M6** — the deployables exist in the repo: `cmd/server/main.go` (one
  binary mounting `httpapi.NewHandler` with a `PORT` env and an optional
  `REDIS_URL`-armed §8(c) cache), `golang/Dockerfile` (static, non-root,
  hermetic build with `-mod=vendor`), and the `go:` compose service
  (internal-only, `isolated` network, `http://go:8080`) with backend env
  `USE_GO_WORKER`/`GO_WORKER_BASE_URL`.  The Redis store
  (`golang/idempotency/redis_store.go`) is the deployment `Store`
  implementation over the vendored `github.com/redis/go-redis/v9`, proven
  hermetically against a recording double.

The §15 gate still holds for **anything operational**: no live image, no
compose up, no flag flip.  The uniqueness is that when a trigger finally
fires, the Go side is already byte-proven against Python, container-ready,
and behind the same flag discipline as the node seam — not greenfield.