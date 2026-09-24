// Hermetic proof of the stateless pipeline: normalize -> dedup -> export.
// No network, no browser, no env — pure in-process functions.
package worker

import (
	"bytes"
	"strings"
	"testing"
)

func TestProcessRawPipeline(t *testing.T) {
	other := goldenParsed()
	other.PostID = ptrStr("999999")

	result := ProcessRaw(
		[]*ParsedPost{goldenParsed(), goldenParsed(), other},
		ptrStr("Page"), ptrStr("999"), ptrStr("https://www.facebook.com/Page"),
		goldenNow,
	)

	if result.DuplicatesRemoved != 1 {
		t.Fatalf("DuplicatesRemoved = %d, want 1", result.DuplicatesRemoved)
	}
	if result.PostsKept != 2 || len(result.Posts) != 2 {
		t.Fatalf("PostsKept = %d / len(Posts) = %d, want 2 / 2",
			result.PostsKept, len(result.Posts))
	}

	csv, err := result.CSVBytes()
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.HasPrefix(csv, []byte("\ufeffpost_id,")) {
		t.Fatalf("CSV must start with BOM + header, got %q", string(csv[:min(12, len(csv))]))
	}
	if bytes.Count(csv, []byte("\r\n")) != 3 { // header + 2 rows
		t.Fatalf("CSV row count wrong: %q", string(csv))
	}

	jsonl, err := result.JSONLBytes()
	if err != nil {
		t.Fatal(err)
	}
	lines := strings.Split(strings.TrimSuffix(string(jsonl), "\n"), "\n")
	if len(lines) != 2 {
		t.Fatalf("JSONL line count = %d, want 2", len(lines))
	}
	for i, line := range lines {
		if !strings.HasPrefix(line, `{"post_id": "`) || !strings.HasSuffix(line, `}`) {
			t.Fatalf("JSONL line %d malformed: %s", i, line)
		}
	}
	if !strings.Contains(lines[0], `"post_id": "1234567890"`) {
		t.Fatalf("JSONL line 0 post_id wrong: %s", lines[0])
	}
	if !strings.Contains(lines[1], `"post_id": "999999"`) {
		t.Fatalf("JSONL line 1 post_id wrong: %s", lines[1])
	}
}

func TestNormalizePostURL(t *testing.T) {
	cases := []struct {
		in  string
		out string
	}{
		{"https://www.facebook.com/page?fbclid=abc&ref=xyz",
			"https://www.facebook.com/page"},
		{"https://www.facebook.com/page/",
			"https://www.facebook.com/page"},
		{"https://www.facebook.com/page//",
			"https://www.facebook.com/page"},
	}
	for _, c := range cases {
		got := NormalizePostURL(&c.in)
		if got == nil || *got != c.out {
			t.Fatalf("NormalizePostURL(%q) = %v, want %q", c.in, got, c.out)
		}
	}
	if NormalizePostURL(nil) != nil {
		t.Fatal("NormalizePostURL(nil) must be nil")
	}
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
