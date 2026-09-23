// Package documentation: pin-notes for the runtime libraries plan §5 names.
//
// THESE ARE NOTES ONLY.  Nothing here is imported or fetched: slice A is
// hermetic (zero network / zero go-get), so only the stdlib is used.  When
// a real deployment slice asks for the pinned versions, these are the
// exact versions the plan locks:
//
//   - github.com/PuerkitoBio/goquery v1.9.2   (HTML parse, BeautifulSoup eq)
//   - github.com/qax-os/excelize/v2 v2.11.0+  (XLSX; MUST stay >=2.11.0 —
//     fixes CVE-2026-59161, CVE-2026-59162, CVE-2026-54063; see §5)
//   - github.com/go-chi/chi                     (Phase-1 HTTP router)
//   - golang.org/x/sync/errgroup                (bounded concurrency)
//   - github.com/stretchr/testify               (assertions, when network is
//     allowed; the hermetic proofs intentionally use stdlib testing only)
//   - google.golang.org/grpc                    (Phase-2 gRPC, from the
//     shared postharvest.proto)
//
// Slice A's normalize/dedup/export layers are implemented on the stdlib so
// the byte-parity proofs run fully offline.  Swapping any of the above in
// later must not change the golden bytes asserted in *_test.go.
package worker
