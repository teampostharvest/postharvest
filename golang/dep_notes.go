// Package documentation: pin-notes for the runtime libraries plan §5 names.
//
// THESE ARE NOTES ONLY plus the already-vendored exceptions (goquery,
// excelize).  Nothing else here is imported or fetched: slice A is hermetic
// (zero network / zero go-get), so only the stdlib + vendored deps are used.
// When a real deployment slice asks for the pinned versions, these are the
// exact versions the plan locks:
//
//   - github.com/PuerkitoBio/goquery v1.9.2   (HTML parse, BeautifulSoup eq;
//     VENDORED from M2 — golang/parser depends on it)
//   - github.com/xuri/excelize/v2 v2.11.0     (XLSX export; VENDORED from M5 —
//     golang export_xlsx.go depends on it.  NOTE: plan §5 pins this as
//     github.com/qax-os/excelize/v2, but the upstream project renamed the
//     module to github.com/xuri/excelize/v2 — the qax-os path no longer
//     resolves.  v2.11.0+ is the CVE-locked line either way: MUST stay
//     >=2.11.0 for CVE-2026-59161, CVE-2026-59162, CVE-2026-54063.)
//   - github.com/redis/go-redis/v9             (§8(c) idempotency cache;
//     golang/idempotency exposes the Store seam — the hermetic proofs run
//     against its in-memory Memory store, so wiring Redis later must not
//     change the cached ParseResponse bytes or the retry-safety semantics)
//   - github.com/go-chi/chi                     (Phase-1 HTTP router)
//   - golang.org/x/sync/errgroup                (bounded concurrency)
//   - github.com/stretchr/testify               (assertions, when network is
//     allowed; the hermetic proofs intentionally use stdlib testing only)
//   - google.golang.org/grpc                    (Phase-2 gRPC, from the
//     shared postharvest.proto)
//
// Slice A's normalize/dedup/export layers plus the idempotency seam are
// implemented on the stdlib so the byte-parity proofs run fully offline.
// Swapping any of the above in later must not change the golden bytes
// asserted in *_test.go.
package worker
