// Hermetic proofs for the Phase-1 HTTP surface: httptest.NewRecorder only
// (no sockets).  Request/response bytes are asserted byte-for-byte against
// the real-Python goldens in testdata/ (testdata/gen_goldens.py), and the
// response decodes into the contract.ParseResponse shape locked by M1.
package httpapi

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	worker "postharvest/golang"
	"postharvest/golang/contract"
	"postharvest/golang/idempotency"
)

// goldenNow is the fixed clock injected into the handler; it must match the
// `NOW` constant in testdata/gen_goldens.py.
var goldenNow = time.Date(2026, 9, 24, 12, 0, 0, 0, time.UTC)

func newTestHandler() http.Handler {
	return NewHandler(func() time.Time { return goldenNow }, nil)
}

func newTestHandlerWithStore(store idempotency.Store) http.Handler {
	return NewHandler(func() time.Time { return goldenNow }, store)
}

var (
	sharedFixturesDir   = filepath.Join("..", "..", "shared", "fixtures")
	parseRequestFixture = filepath.Join(sharedFixturesDir, "parse_request.html.json")
	browserSnapshot     = filepath.Join(sharedFixturesDir, "browser_snapshot.html")
	domSampleAsset      = filepath.Join("..", "parser", "testdata", "dom_sample.html")
)

func readFile(t *testing.T, path string) []byte {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	return raw
}

func postParse(t *testing.T, h http.Handler, body []byte) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, parsePath, bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	return rec
}

// newFixtureRequest decodes the shared ParseRequest fixture so the request
// side of the contract is exercised end-to-end (not hand-typed bytes).
func newFixtureRequest(t *testing.T) contract.ParseRequest {
	t.Helper()
	var req contract.ParseRequest
	if err := json.Unmarshal(readFile(t, parseRequestFixture), &req); err != nil {
		t.Fatalf("unmarshal ParseRequest fixture: %v", err)
	}
	return req
}

func marshalReq(t *testing.T, req contract.ParseRequest) []byte {
	t.Helper()
	body, err := json.Marshal(req)
	if err != nil {
		t.Fatalf("marshal ParseRequest: %v", err)
	}
	return body
}

func goldenBytes(t *testing.T, name string) []byte {
	t.Helper()
	return readFile(t, filepath.Join("testdata", name))
}

// goldenName -> fixture asset + request builder; the fixture asset bytes
// become the base64 raw_payload.
type parseCase struct {
	golden      string
	payload     string // path of the raw HTML asset
	targetURL   string
	handle      string
	contentType string
}

func runGoldenCase(t *testing.T, tc parseCase) {
	t.Helper()
	h := newTestHandler()
	req := contract.ParseRequest{
		RawPayload:     base64.StdEncoding.EncodeToString(readFile(t, tc.payload)),
		ContentType:    tc.contentType,
		IdempotencyKey: "job_7:" + strings.ReplaceAll(tc.targetURL, "/", "_"),
		TargetURL:      tc.targetURL,
		Handle:         tc.handle,
	}
	rec := postParse(t, h, marshalReq(t, req))
	if rec.Code != http.StatusOK {
		t.Fatalf("%s: status = %d, body = %s", tc.golden, rec.Code, rec.Body.String())
	}
	want := goldenBytes(t, tc.golden)
	if !reflect.DeepEqual(rec.Body.Bytes(), want) {
		t.Fatalf("%s: response bytes mismatch\n--- got ---\n%s\n--- want ---\n%s",
			tc.golden, rec.Body.String(), string(want))
	}
}

func TestParseHTMLDomSampleMatchesPythonGolden(t *testing.T) {
	// Uses the shared ParseRequest fixture itself (target_url/handle from
	// the fixture), so dom_sample.html's raw_payload matches the contract
	// fixture exactly.
	h := newTestHandler()
	req := newFixtureRequest(t)
	if _, err := os.Stat(domSampleAsset); err != nil {
		t.Fatalf("dom_sample asset missing: %v", err)
	}
	rec := postParse(t, h, marshalReq(t, req))
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	want := goldenBytes(t, "parse_response_dom_sample.json")
	if !reflect.DeepEqual(rec.Body.Bytes(), want) {
		t.Fatalf("dom_sample response mismatch\n--- got ---\n%s\n--- want ---\n%s",
			rec.Body.String(), string(want))
	}
}

func TestParseHTMLBrowserSnapshotMatchesPythonGolden(t *testing.T) {
	runGoldenCase(t, parseCase{
		golden:      "parse_response_browser_snapshot_html.json",
		payload:     browserSnapshot,
		targetURL:   "https://www.facebook.com/NASA",
		handle:      "NASA",
		contentType: "html",
	})
}

func TestParseGraphQLBrowserSnapshotMatchesPythonGolden(t *testing.T) {
	runGoldenCase(t, parseCase{
		golden:      "parse_response_browser_snapshot_graphql.json",
		payload:     browserSnapshot,
		targetURL:   "https://www.facebook.com/NASA",
		handle:      "NASA",
		contentType: "graphql_json",
	})
}

func TestParseResponseDecodesToContractShape(t *testing.T) {
	rec := postParse(t, newTestHandler(), marshalReq(t, newFixtureRequest(t)))
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d", rec.Code)
	}
	if ct := rec.Header().Get("Content-Type"); !strings.HasPrefix(ct, "application/json") {
		t.Fatalf("Content-Type = %q, want application/json", ct)
	}
	var resp contract.ParseResponse
	if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
		t.Fatalf("response does not decode into contract.ParseResponse: %v", err)
	}
	if len(resp.Posts) != 1 {
		t.Fatalf("posts = %d, want 1", len(resp.Posts))
	}
	p := resp.Posts[0]
	if p.PostID != "515151" {
		t.Fatalf("post_id = %q", p.PostID)
	}
	if p.PageName != "Acme Widgets" {
		t.Fatalf("page_name = %q", p.PageName)
	}
	// The wire JSON must carry all 33 canonical keys (the golden byte test
	// already locks their order; this locks the set/values after a JSON
	// round-trip through the contract structs).
	var raw map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &raw); err != nil {
		t.Fatalf("unmarshal wire JSON: %v", err)
	}
	if raw["posts"] == nil || raw["errors"] == nil {
		t.Fatalf("wire JSON missing posts/errors keys: %v", raw)
	}
	postsArr, ok := raw["posts"].([]any)
	if !ok || len(postsArr) != 1 {
		t.Fatalf("wire posts = %#v", raw["posts"])
	}
	postObj, ok := postsArr[0].(map[string]any)
	if !ok {
		t.Fatalf("wire posts[0] = %#v", postsArr[0])
	}
	for _, key := range worker.NORMALIZED_KEYS {
		if _, ok := postObj[key]; !ok {
			t.Fatalf("wire post missing canonical key %q", key)
		}
	}
	if len(postObj) != len(worker.NORMALIZED_KEYS) {
		t.Fatalf("wire post has %d keys, want %d", len(postObj), len(worker.NORMALIZED_KEYS))
	}
}

func TestProcessThreadsPageContext(t *testing.T) {
	out, err := Process(newFixtureRequest(t), goldenNow)
	if err != nil {
		t.Fatalf("Process: %v", err)
	}
	if len(out.Posts) != 1 {
		t.Fatalf("posts = %d, want 1", len(out.Posts))
	}
	p := out.Posts[0]
	if got := strPtrVal(p["page_name"]); got != "Acme Widgets" {
		t.Fatalf("page_name = %q", got)
	}
	if got := strPtrVal(p["page_id"]); got != "424242" {
		t.Fatalf("page_id = %q", got)
	}
	if got := strPtrVal(p["facebook_url"]); got != "https://www.facebook.com/acmewidgets" {
		t.Fatalf("facebook_url = %q", got)
	}
	if len(out.Errors) != 0 {
		t.Fatalf("errors = %v, want empty", out.Errors)
	}
}

func TestProcessGraphQLPathLeavesPageMetadataNull(t *testing.T) {
	snapshot := readFile(t, browserSnapshot)
	req := contract.ParseRequest{
		RawPayload:  base64.StdEncoding.EncodeToString(snapshot),
		ContentType: "graphql_json",
		TargetURL:   "https://www.facebook.com/NASA",
	}
	out, err := Process(req, goldenNow)
	if err != nil {
		t.Fatalf("Process: %v", err)
	}
	// extract_posts_from_graphql carries no page-level metadata; page_name
	// and page_id stay null while facebook_url takes the target_url context.
	if len(out.Posts) != 2 {
		t.Fatalf("posts = %d, want 2", len(out.Posts))
	}
	for _, p := range out.Posts {
		if strPtrVal(p["page_name"]) != "" {
			t.Fatalf("page_name = %q, want null", strPtrVal(p["page_name"]))
		}
		if strPtrVal(p["page_id"]) != "" {
			t.Fatalf("page_id = %q, want null", strPtrVal(p["page_id"]))
		}
		if got := strPtrVal(p["facebook_url"]); got != "https://www.facebook.com/NASA" {
			t.Fatalf("facebook_url = %q", got)
		}
		if got := strPtrVal(p["post_url"]); got != "https://www.facebook.com/NASA/posts/2001" &&
			got != "https://www.facebook.com/NASA/posts/2002" {
			t.Fatalf("post_url = %q, want permalink joined to target_url", got)
		}
	}
}

// strPtrVal dereferences the *string values NormalizePost stores in the
// canonical maps ("" when absent/null).
func strPtrVal(v any) string {
	if s, ok := v.(*string); ok && s != nil {
		return *s
	}
	return ""
}

func TestParseRejectsBadRequests(t *testing.T) {
	h := newTestHandler()
	cases := []struct {
		name string
		body []byte
		want int
	}{
		{"bad json", []byte("{nope"), http.StatusBadRequest},
		{"missing content_type", []byte(`{"raw_payload":"aGVsbG8="}`), http.StatusBadRequest},
		{"unknown content_type", []byte(`{"raw_payload":"aGVsbG8=","content_type":"xml"}`), http.StatusBadRequest},
		{"bad base64", []byte(`{"raw_payload":"!!!","content_type":"html"}`), http.StatusBadRequest},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			rec := postParse(t, h, tc.body)
			if rec.Code != tc.want {
				t.Fatalf("status = %d, want %d (body %s)", rec.Code, tc.want, rec.Body.String())
			}
			if !strings.HasPrefix(rec.Body.String(), `{"error": `) {
				t.Fatalf("error body = %s, want {\"error\": ...} envelope", rec.Body.String())
			}
		})
	}
}

func TestParseRejectsNonPOST(t *testing.T) {
	req := newFixtureRequest(t)
	body := marshalReq(t, req)
	for _, method := range []string{http.MethodGet, http.MethodDelete, http.MethodPut} {
		h := newTestHandler()
		r := httptest.NewRequest(method, parsePath, bytes.NewReader(body))
		rec := httptest.NewRecorder()
		h.ServeHTTP(rec, r)
		if rec.Code != http.StatusMethodNotAllowed {
			t.Fatalf("%s %s: status = %d, want 405", method, parsePath, rec.Code)
		}
		if !strings.HasPrefix(rec.Body.String(), `{"error": `) {
			t.Fatalf("%s: error body = %s", method, rec.Body.String())
		}
	}
}

func TestHandlerIncludesHealthProbes(t *testing.T) {
	h := newTestHandler()
	for _, path := range []string{"/healthz", "/readyz"} {
		rec := httptest.NewRecorder()
		req := httptest.NewRequest(http.MethodGet, path, nil)
		h.ServeHTTP(rec, req)
		if rec.Code != http.StatusOK || rec.Body.String() != "ok\n" {
			t.Fatalf("%s: status=%d body=%q", path, rec.Code, rec.Body.String())
		}
	}
}

func TestHandlerExposesMetrics(t *testing.T) {
	h := newTestHandler()
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/metrics", nil)
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("/metrics: status = %d, want 200", rec.Code)
	}
	body := rec.Body.String()
	if !strings.Contains(body, "go_goroutines") {
		t.Fatalf("/metrics should include Go runtime metrics, got:\n%s", body)
	}
	if !strings.Contains(body, "process_cpu_seconds_total") {
		t.Fatalf("/metrics should include process metrics, got:\n%s", body)
	}
}

// recordingStore wraps idempotency.Memory and counts cache operations so the
// HTTP proofs can distinguish "served from cache" from "re-parsed" without
// any network.
type recordingStore struct {
	*idempotency.Memory
	gets, sets int
}

func newRecordingStore(clock func() time.Time) *recordingStore {
	return &recordingStore{Memory: idempotency.NewMemory(clock)}
}

func (r *recordingStore) Get(ctx context.Context, key string) ([]byte, bool, error) {
	r.gets++
	return r.Memory.Get(ctx, key)
}

func (r *recordingStore) Set(ctx context.Context, key string, data []byte, ttl time.Duration) error {
	r.sets++
	return r.Memory.Set(ctx, key, data, ttl)
}

func TestParseIdempotencyReplaysCachedBytes(t *testing.T) {
	// §8(c): the second request with the same idempotency_key must return
	// the cached ParseResponse bytes verbatim — no re-parse, no re-set.
	store := newRecordingStore(nil)
	h := newTestHandlerWithStore(store)
	body := marshalReq(t, newFixtureRequest(t)) // idempotency_key "job_7:acmewidgets_1"

	first := postParse(t, h, body)
	if first.Code != http.StatusOK {
		t.Fatalf("first: status = %d, body = %s", first.Code, first.Body.String())
	}
	if first.Header().Get(cacheHitHeader) != "miss" {
		t.Fatalf("first X-PostHarvest-Cache = %q, want miss", first.Header().Get(cacheHitHeader))
	}
	want := goldenBytes(t, "parse_response_dom_sample.json")
	if !reflect.DeepEqual(first.Body.Bytes(), want) {
		t.Fatal("first response must match the Python golden")
	}
	if store.gets != 1 || store.sets != 1 {
		t.Fatalf("first: gets=%d sets=%d, want 1/1", store.gets, store.sets)
	}

	second := postParse(t, h, body)
	if second.Code != http.StatusOK {
		t.Fatalf("second: status = %d, body = %s", second.Code, second.Body.String())
	}
	if second.Header().Get(cacheHitHeader) != "hit" {
		t.Fatalf("second X-PostHarvest-Cache = %q, want hit", second.Header().Get(cacheHitHeader))
	}
	if !reflect.DeepEqual(second.Body.Bytes(), first.Body.Bytes()) {
		t.Fatal("cached replay must be byte-identical to the original response")
	}
	// One extra Get, no extra Set: the parser never ran again.
	if store.gets != 2 || store.sets != 1 {
		t.Fatalf("second: gets=%d sets=%d, want 2/1", store.gets, store.sets)
	}
}

func TestParseIdempotencyExpiresAndRecomputes(t *testing.T) {
	// After TTL elapses the cache is a miss again and the work is redone
	// (§8(c): short TTL — this is not a durability store).
	now := goldenNow
	clock := func() time.Time { return now }
	store := newRecordingStore(clock)
	h := newTestHandlerWithStore(store)
	body := marshalReq(t, newFixtureRequest(t))

	if rec := postParse(t, h, body); rec.Header().Get(cacheHitHeader) != "miss" {
		t.Fatalf("first = %q, want miss", rec.Header().Get(cacheHitHeader))
	}
	if store.gets != 1 || store.sets != 1 {
		t.Fatalf("first: gets=%d sets=%d, want 1/1", store.gets, store.sets)
	}

	now = now.Add(idempotency.TTL + time.Minute) // expire the entry
	if rec := postParse(t, h, body); rec.Header().Get(cacheHitHeader) != "miss" {
		t.Fatalf("after TTL = %q, want miss", rec.Header().Get(cacheHitHeader))
	}
	if store.gets != 2 || store.sets != 2 {
		t.Fatalf("after TTL: gets=%d sets=%d, want 2/2 (recomputed)", store.gets, store.sets)
	}
}

func TestParseDoesNotTouchCacheWithoutKey(t *testing.T) {
	store := newRecordingStore(nil)
	h := newTestHandlerWithStore(store)
	req := newFixtureRequest(t)
	req.IdempotencyKey = "" // job without an idempotency key
	rec := postParse(t, h, marshalReq(t, req))
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	if store.gets != 0 || store.sets != 0 {
		t.Fatalf("no idempotency_key: gets=%d sets=%d, want 0/0", store.gets, store.sets)
	}
}

func TestParseNilStoreBypassesIdempotency(t *testing.T) {
	// A nil store (single-shot mode) must behave exactly like M3: parse
	// every time, never consult a cache.
	h := newTestHandler()
	body := marshalReq(t, newFixtureRequest(t))
	for i := 0; i < 2; i++ {
		rec := postParse(t, h, body)
		if rec.Code != http.StatusOK {
			t.Fatalf("request %d: status = %d", i, rec.Code)
		}
		if !reflect.DeepEqual(rec.Body.Bytes(), goldenBytes(t, "parse_response_dom_sample.json")) {
			t.Fatalf("request %d: response must match the Python golden", i)
		}
	}
}

func TestParseInvalidPayloadIsNeverServedFromCache(t *testing.T) {
	// A request whose base64/content_type is garbage returns 400 even if
	// the idempotency_key would match a cached entry — only validated
	// requests are cacheable or cache-served.
	store := newRecordingStore(nil)
	h := newTestHandlerWithStore(store)
	good := marshalReq(t, newFixtureRequest(t))
	if rec := postParse(t, h, good); rec.Code != http.StatusOK {
		t.Fatalf("seed request: status = %d", rec.Code)
	}
	var bad contract.ParseRequest
	if err := json.Unmarshal(good, &bad); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	bad.ContentType = "xml" // same key, invalid content type
	rec := postParse(t, h, marshalReq(t, bad))
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("invalid content_type with cached key: status = %d, want 400", rec.Code)
	}
	bad2 := newFixtureRequest(t)
	bad2.RawPayload = "not-base64!!"
	rec = postParse(t, h, marshalReq(t, bad2))
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("invalid base64 with cached key: status = %d, want 400", rec.Code)
	}
}
