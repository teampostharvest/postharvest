// Cross-language contract fixtures (finalplanv2.md §6, "Critical Note 8").
//
// Go asserts the shared/fixtures golden payloads against the same shapes it
// serves (contract.Post / FetchRequest / FetchResponse / ParseResponse), so
// the .proto, this package, node/src/types.ts and the fixtures cannot drift.
// Editing order: shared/proto/postharvest.proto -> node/src/types.ts ->
// golang/contract/contract.go -> shared/fixtures/ (README.md).
package contract

import (
	"encoding/base64"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"

	worker "postharvest/golang"
)

// fixturesDir resolves to shared/fixtures relative to this package
// (golang/contract -> ../../shared/fixtures).
var fixturesDir = filepath.Join("..", "..", "shared", "fixtures")

const (
	fetchResponseHTMLFixture    = "fetch_response.html.json"
	fetchResponseBrowserFixture = "fetch_response.browser.json"
	fetchRequestBrowserFixture  = "fetch_request.browser.json"
	parseResponsePostsFixture   = "parse_response.posts.json"
	browserSnapshotAsset        = "browser_snapshot.html"
)

// fetchResponseKeys mirrors node/test/contract.test.ts FETCH_RESPONSE_KEYS.
var fetchResponseKeys = []string{
	"status_code", "final_url", "content_type", "raw_payload",
	"fetched_at_ms", "updated_cookies", "browser_stats",
}

// browserStatsKeys mirrors node/test/contract.test.ts BROWSER_STATS_KEYS.
var browserStatsKeys = []string{"login_wall", "feed_missing", "posts_found"}

func loadFixture(t *testing.T, name string) []byte {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(fixturesDir, name))
	if err != nil {
		t.Fatalf("read fixture %s: %v", name, err)
	}
	return raw
}

func decodeFixture[T any](t *testing.T, name string) T {
	t.Helper()
	var out T
	if err := json.Unmarshal(loadFixture(t, name), &out); err != nil {
		t.Fatalf("unmarshal fixture %s: %v", name, err)
	}
	return out
}

func exactKeys(t *testing.T, obj map[string]any, want []string) {
	t.Helper()
	got := make([]string, 0, len(obj))
	for k := range obj {
		got = append(got, k)
	}
	sortStrings(got)
	sortStrings(want)
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("key mismatch:\n  got:  %v\n  want: %v", got, want)
	}
}

func sortStrings(s []string) {
	for i := 1; i < len(s); i++ {
		for j := i; j > 0 && s[j] < s[j-1]; j-- {
			s[j], s[j-1] = s[j-1], s[j]
		}
	}
}

func TestHTTPFetchResponseFixtureIsCanonicalShape(t *testing.T) {
	var m map[string]any
	if err := json.Unmarshal(loadFixture(t, fetchResponseHTMLFixture), &m); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	exactKeys(t, m, fetchResponseKeys)

	if m["content_type"] != "text/html" {
		t.Fatalf("content_type = %v, want text/html", m["content_type"])
	}
	// proto3 JSON defaults for browser-only fields (§6).
	if arr, ok := m["updated_cookies"].([]any); !ok || len(arr) != 0 {
		t.Fatalf("updated_cookies = %v, want []", m["updated_cookies"])
	}
	if m["browser_stats"] != nil {
		t.Fatalf("browser_stats = %v, want null", m["browser_stats"])
	}

	// It must also decode into the typed struct.
	resp := decodeFixture[FetchResponse](t, fetchResponseHTMLFixture)
	if resp.ContentType != "text/html" {
		t.Fatalf("typed content_type = %q", resp.ContentType)
	}
	if resp.BrowserStats != nil {
		t.Fatalf("typed browser_stats must be nil, got %+v", resp.BrowserStats)
	}
	if len(resp.UpdatedCookies) != 0 {
		t.Fatalf("typed updated_cookies = %v, want empty", resp.UpdatedCookies)
	}
	html, err := base64.StdEncoding.DecodeString(resp.RawPayload)
	if err != nil || len(html) == 0 {
		t.Fatalf("raw_payload is not valid non-empty base64 (err=%v)", err)
	}
}

func TestBrowserFetchResponseFixtureDecodesToSnapshotAsset(t *testing.T) {
	var m map[string]any
	if err := json.Unmarshal(loadFixture(t, fetchResponseBrowserFixture), &m); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	exactKeys(t, m, fetchResponseKeys)
	if m["content_type"] != "text/html" {
		t.Fatalf("content_type = %v, want text/html", m["content_type"])
	}

	statsRaw, ok := m["browser_stats"].(map[string]any)
	if !ok {
		t.Fatalf("browser_stats = %v, want object", m["browser_stats"])
	}
	exactKeys(t, statsRaw, browserStatsKeys)
	for _, k := range browserStatsKeys {
		switch statsRaw[k].(type) {
		case bool:
		case float64:
		default:
			t.Fatalf("browser_stats.%s is %T, want bool/int", k, statsRaw[k])
		}
	}

	// raw_payload is base64 of browser_snapshot.html, byte-for-byte.
	resp := decodeFixture[FetchResponse](t, fetchResponseBrowserFixture)
	decoded, err := base64.StdEncoding.DecodeString(resp.RawPayload)
	if err != nil {
		t.Fatalf("base64 decode raw_payload: %v", err)
	}
	snapshot, err := os.ReadFile(filepath.Join(fixturesDir, browserSnapshotAsset))
	if err != nil {
		t.Fatalf("read snapshot asset: %v", err)
	}
	if !reflect.DeepEqual(decoded, snapshot) {
		t.Fatal("raw_payload does not decode byte-for-byte to browser_snapshot.html")
	}
	asText := string(decoded)
	if !contains(asText, `data-fb-graphql-feed="1"`) {
		t.Fatal("snapshot missing data-fb-graphql-feed marker (must be byte-parity with fetch_with_browser)")
	}
	if !contains(asText, `"post_id":"2001"`) {
		t.Fatal("snapshot missing story node post_id marker")
	}
	if contains(asText, `<!-- fb-scrape-feed-missing -->`) {
		t.Fatal("snapshot must NOT contain the feed-missing marker")
	}
}

func TestBrowserFetchRequestFixtureCarriesBrowserModeOptions(t *testing.T) {
	var m map[string]any
	if err := json.Unmarshal(loadFixture(t, fetchRequestBrowserFixture), &m); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	exactKeys(t, m, []string{"target_url", "mode", "account_id", "scroll_rounds", "max_posts", "cookies"})

	req := decodeFixture[FetchRequest](t, fetchRequestBrowserFixture)
	if req.Mode != "browser" {
		t.Fatalf("mode = %q, want browser", req.Mode)
	}
	if req.AccountID != "ops:maverick" {
		t.Fatalf("account_id = %q, want ops:maverick", req.AccountID)
	}
	if req.ScrollRounds <= 0 {
		t.Fatalf("scroll_rounds = %d, want > 0", req.ScrollRounds)
	}
	if len(req.Cookies) != 2 {
		t.Fatalf("cookies = %v, want 2 entries", req.Cookies)
	}
}

func TestParseResponsePostsFixtureHas33KeyCanonicalPost(t *testing.T) {
	resp := decodeFixture[ParseResponse](t, parseResponsePostsFixture)
	if len(resp.Posts) != 1 {
		t.Fatalf("posts = %d, want 1", len(resp.Posts))
	}
	if len(resp.Errors) != 0 {
		t.Fatalf("errors = %v, want empty", resp.Errors)
	}
	p := resp.Posts[0]
	if p.PostID != "1234567890100_9876543210" {
		t.Fatalf("post_id = %q", p.PostID)
	}
	if p.PageName != "NASA Lunar Pictures" {
		t.Fatalf("page_name = %q", p.PageName)
	}
	if p.PostType != "image" {
		t.Fatalf("post_type = %q", p.PostType)
	}
	if p.Timestamp != 1787310000 {
		t.Fatalf("timestamp = %d, want 1787310000", p.Timestamp)
	}
	if len(p.Hashtags) != 1 || p.Hashtags[0] != "#hashtag" {
		t.Fatalf("hashtags = %v", p.Hashtags)
	}
	if p.MediaType != "image" {
		t.Fatalf("media_type = %q", p.MediaType)
	}
}

// NORMALIZED_KEYS order ⇔ proto field numbers 1..33, per the .proto header
// comment. This test locks that mapping so a fixture edit cannot silently
// renumber the wire contract.
func TestPostFieldNumbersAreContiguous13To33InKeyOrder(t *testing.T) {
	if len(PostFieldNumbers) != 33 {
		t.Fatalf("PostFieldNumbers = %d entries, want 33", len(PostFieldNumbers))
	}
	for i, fk := range PostFieldNumbers {
		if fk.Num != i+1 {
			t.Fatalf("PostFieldNumbers[%d].Num = %d, want %d", i, fk.Num, i+1)
		}
		if fk.Name != worker.NORMALIZED_KEYS[i] {
			t.Fatalf("PostFieldNumbers[%d].Name = %q, want %q (NORMALIZED_KEYS order)",
				i, fk.Name, worker.NORMALIZED_KEYS[i])
		}
	}
}

func contains(s, sub string) bool {
	return len(s) >= len(sub) && (s == sub || len(sub) == 0 ||
		indexOf(s, sub) >= 0)
}

func indexOf(s, sub string) int {
	for i := 0; i+len(sub) <= len(s); i++ {
		if s[i:i+len(sub)] == sub {
			return i
		}
	}
	return -1
}
