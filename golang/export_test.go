// Golden byte-parity proofs for the exporters: expected bytes below were
// produced by the REAL Python exporters (backend/exporters/csv_exporter.py,
// jsonl_exporter.py) — see /tmp/opencode/golden_export.py.  The Go writer
// must reproduce them exactly.
package worker

import (
	"bytes"
	"reflect"
	"strings"
	"testing"
)

// goldenCSV is Python's exact export_csv output (utf-8-sig BOM included),
// for the golden parsed post.
const goldenCSV = "\ufeff" +
	"post_id,page_name,post_url,published_at,text,post_type,likes,comments_count,shares,views_count,thumbnail_url,media_url,page_id,profile_url,reactions,reaction_like_count,reaction_love_count,reaction_care_count,reaction_haha_count,reaction_wow_count,reaction_sad_count,reaction_angry_count,hashtags,mentions,external_links,media_type,video_url,transcript\r\n" +
	"1234567890,Page,https://www.facebook.com/permalink.php?story_fbid=1234567890&id=999,2026-01-15T12:00:00+00:00,Hello #Go and #workers @postharvest see https://example.com #Go,image,10,3,2,,https://img.example/pic.jpg,,999,https://www.facebook.com/Page,25,7,2,,,,,,#Go|#workers,@handlegiven|@postharvest,https://example.com,image,,\r\n"

func goldenPostMap(t *testing.T) map[string]interface{} {
	post := NormalizePost(goldenParsed(), ptrStr("Page"), ptrStr("999"),
		ptrStr("https://www.facebook.com/Page"), goldenNow)
	return post
}

func TestFlatColumnsGolden(t *testing.T) {
	want := []string{
		"post_id", "page_name", "post_url", "published_at", "text",
		"post_type", "likes", "comments_count", "shares", "views_count",
		"thumbnail_url", "media_url", "page_id", "profile_url", "reactions",
		"reaction_like_count", "reaction_love_count", "reaction_care_count",
		"reaction_haha_count", "reaction_wow_count", "reaction_sad_count",
		"reaction_angry_count", "hashtags", "mentions", "external_links",
		"media_type", "video_url", "transcript",
	}
	if !reflect.DeepEqual(FLAT_COLUMNS, want) {
		t.Fatalf("FLAT_COLUMNS mismatch:\ngot  %v\nwant %v", FLAT_COLUMNS, want)
	}
}

func TestWriteCSVGoldenBytes(t *testing.T) {
	post := goldenPostMap(t)
	var buf bytes.Buffer
	if err := WriteCSV(&buf, []map[string]interface{}{post}); err != nil {
		t.Fatal(err)
	}
	if got := buf.String(); got != goldenCSV {
		t.Fatalf("CSV byte mismatch\n got %q\nwant %q", got, goldenCSV)
	}
}

func TestWriteJSONLGoldenBytes(t *testing.T) {
	post := goldenPostMap(t)
	var buf bytes.Buffer
	if err := WriteJSONL(&buf, []map[string]interface{}{post}); err != nil {
		t.Fatal(err)
	}
	want := goldenJSONL + "\n"
	if got := buf.String(); got != want {
		t.Fatalf("JSONL byte mismatch\n got %s\nwant %s", got, want)
	}
}

func TestCellFlattenGolden(t *testing.T) {
	post := goldenPostMap(t)
	row := FlattenPost(post)
	if row[0] != "1234567890" {
		t.Fatalf("post_id cell = %q", row[0])
	}
	if row[22] != "#Go|#workers" {
		t.Fatalf("hashtags cell = %q, want #Go|#workers", row[22])
	}
	if row[23] != "@handlegiven|@postharvest" {
		t.Fatalf("mentions cell = %q", row[23])
	}
	if row[9] != "" || row[11] != "" || row[27] != "" {
		t.Fatalf("None cells must flatten to empty string")
	}
	// the full row must be embedded in the golden CSV line
	if !strings.Contains(goldenCSV, strings.Join(row, ",")) {
		t.Fatalf("flattened row not in golden CSV: %v", row)
	}
}
