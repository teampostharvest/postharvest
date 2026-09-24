package parser

// Golden byte-truth tests: every case runs the REAL Python (via
// testdata/gen_goldens.py) and asserts this package reproduces the bytes
// exactly.  Never hand-edit goldens — re-run the generator after changing
// backend/scraper/parser.py, then make the Go port match.
//
// The marshaling helpers below reproduce Python
// json.dumps(..., ensure_ascii=False, separators=(",", ":")) for the exact
// ParsedPost.to_dict() shape (key order matters, raw UTF-8 not \uXXXX).

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"testing"
	"time"

	worker "postharvest/golang"
)

const fbURL = "https://www.facebook.com"

var clock = time.Date(2026, 9, 21, 10, 0, 0, 0, time.UTC)

func readFixture(t *testing.T, path string) string {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	return string(b)
}

// --- Python json.dumps-compatible marshaling ------------------------------

// pyQuote emits a JSON string exactly as
// json.dumps(s, ensure_ascii=False) does: " and \ escaped, the \b \f \n \r
// \t shortcuts, other control chars as \u00XX (lowercase), raw UTF-8.
func pyQuote(s string) string {
	var b strings.Builder
	b.WriteByte('"')
	for _, r := range s {
		switch r {
		case '"':
			b.WriteString(`\"`)
		case '\\':
			b.WriteString(`\\`)
		case '\n':
			b.WriteString(`\n`)
		case '\r':
			b.WriteString(`\r`)
		case '\t':
			b.WriteString(`\t`)
		case '\b':
			b.WriteString(`\b`)
		case '\f':
			b.WriteString(`\f`)
		default:
			if r < 0x20 {
				fmt.Fprintf(&b, `\u%04x`, r)
			} else {
				b.WriteRune(r)
			}
		}
	}
	b.WriteByte('"')
	return b.String()
}

// pyIso mirrors datetime.isoformat(): seconds precision, ".ffffff" when a
// fractional part exists, and always an explicit +HH:MM (never "Z").
func pyIso(t time.Time) string {
	base := t.Format("2006-01-02T15:04:05")
	if t.Nanosecond() != 0 {
		base += fmt.Sprintf(".%06d", t.Nanosecond())
	}
	return base + t.Format("-07:00")
}

func pyStr(p *string) string {
	if p == nil {
		return "null"
	}
	return pyQuote(*p)
}

func pyInt(p *int64) string {
	if p == nil {
		return "null"
	}
	return fmt.Sprintf("%d", *p)
}

func pyTime(p *time.Time) string {
	if p == nil {
		return "null"
	}
	return pyQuote(pyIso(*p))
}

func pyBool(v bool) string {
	if v {
		return "true"
	}
	return "false"
}

func pyStrList(seq []string) string {
	parts := make([]string, len(seq))
	for i, s := range seq {
		parts[i] = pyQuote(s)
	}
	return "[" + strings.Join(parts, ",") + "]"
}

// marshalPost mirrors p.to_dict() after published_at was replaced by its
// isoformat string (see gen_goldens.py _dump).
func marshalPost(p *worker.ParsedPost) string {
	var b strings.Builder
	b.WriteByte('{')
	field := func(name, val string) {
		if b.Len() > 1 {
			b.WriteByte(',')
		}
		b.WriteString(pyQuote(name))
		b.WriteByte(':')
		b.WriteString(val)
	}
	field("post_id", pyStr(p.PostID))
	field("post_url", pyStr(p.PostURL))
	field("text", pyStr(p.Text))
	field("published_at", pyTime(p.PublishedAt))
	field("published_at_raw", pyStr(p.PublishedAtRaw))
	field("likes", pyInt(p.Likes))
	field("reactions", pyInt(p.Reactions))
	field("likes_total", pyInt(p.LikesTotal))
	field("comments_count", pyInt(p.CommentsCount))
	field("shares", pyInt(p.Shares))
	field("views_count", pyInt(p.ViewsCount))
	field("reaction_like_count", pyInt(p.ReactionLike))
	field("reaction_love_count", pyInt(p.ReactionLove))
	field("reaction_care_count", pyInt(p.ReactionCare))
	field("reaction_haha_count", pyInt(p.ReactionHaha))
	field("reaction_wow_count", pyInt(p.ReactionWow))
	field("reaction_sad_count", pyInt(p.ReactionSad))
	field("reaction_angry_count", pyInt(p.ReactionAngry))
	field("has_image", pyBool(p.HasImage))
	field("has_video", pyBool(p.HasVideo))
	field("has_link_preview", pyBool(p.HasLinkPreview))
	field("thumbnail_url", pyStr(p.ThumbnailURL))
	field("media_url", pyStr(p.MediaURL))
	field("video_url", pyStr(p.VideoURL))
	field("external_links", pyStrList(p.ExternalLinks))
	field("mentions", pyStrList(p.Mentions))
	b.WriteByte('}')
	return b.String()
}

func dumpPosts(posts []*worker.ParsedPost) string {
	lines := make([]string, len(posts))
	for i, p := range posts {
		lines[i] = marshalPost(p)
	}
	out := strings.Join(lines, "\n")
	if len(posts) > 0 {
		out += "\n"
	}
	return out
}

// dumpMetaPage mirrors gen_goldens._dump_meta ("" -> null like Python None).
func dumpMetaPage(page ParsedPage) string {
	ns := func(s string) string {
		if s == "" {
			return "null"
		}
		return pyQuote(s)
	}
	return `{"page_name":` + ns(page.PageName) +
		`,"page_id":` + ns(page.PageID) +
		`,"profile_url":` + ns(page.ProfileURL) +
		`,"og_image":` + ns(page.OGImage) + "}\n"
}

func assertBytes(t *testing.T, name, got, want string) {
	t.Helper()
	if got != want {
		t.Errorf("%s mismatch\n--- got ---\n%s\n--- want ---\n%s", name, got, want)
	}
}

// --- golden tests ---------------------------------------------------------

func TestParsePageDOMSampleGolden(t *testing.T) {
	html := readFixture(t, "testdata/dom_sample.html")
	page := ParsePage(html, fbURL+"/acmewidgets", clock, "acmewidgets")
	assertBytes(t, "parse_page_dom_sample",
		dumpPosts(page.Posts), readFixture(t, "testdata/parse_page_dom_sample.json"))
	assertBytes(t, "parse_page_dom_sample_meta",
		dumpMetaPage(page), readFixture(t, "testdata/parse_page_dom_sample_meta.json"))
}

func TestParsePageBrowserSnapshotGolden(t *testing.T) {
	html := readFixture(t, "../../shared/fixtures/browser_snapshot.html")
	page := ParsePage(html, fbURL+"/NASA", clock, "NASA")
	assertBytes(t, "parse_page_browser_snapshot",
		dumpPosts(page.Posts), readFixture(t, "testdata/parse_page_browser_snapshot.json"))
	assertBytes(t, "parse_page_browser_snapshot_meta",
		dumpMetaPage(page), readFixture(t, "testdata/parse_page_browser_snapshot_meta.json"))
}

func TestGraphQLBrowserSnapshotGolden(t *testing.T) {
	html := readFixture(t, "../../shared/fixtures/browser_snapshot.html")
	posts := ExtractPostsFromGraphQL(html, fbURL+"/NASA")
	assertBytes(t, "graphql_browser_snapshot",
		dumpPosts(posts), readFixture(t, "testdata/graphql_browser_snapshot.json"))
}

func TestGraphQLStoryPhotoGolden(t *testing.T) {
	var story any
	if err := json.Unmarshal(
		[]byte(readFixture(t, "../../tests/fixtures/graphql_story_photo.json")), &story); err != nil {
		t.Fatalf("story fixture: %v", err)
	}
	wrapped := `<script type="application/json" data-fb-graphql-feed="1">` +
		mustJSON(story) + `</script>`
	posts := ExtractPostsFromGraphQL(wrapped, fbURL+"/testpage")
	assertBytes(t, "graphql_story_photo",
		dumpPosts(posts), readFixture(t, "testdata/graphql_story_photo.json"))
}

func TestScriptFallbackGolden(t *testing.T) {
	html := readFixture(t, "testdata/script_fallback.html")
	posts, err := extractPostsFromScripts(html, fbURL+"/acmewidgets", clock)
	if err != nil {
		t.Fatalf("script extraction: %v", err)
	}
	assertBytes(t, "script_fallback",
		dumpPosts(posts), readFixture(t, "testdata/script_fallback.json"))
}

func TestParseTimestampGolden(t *testing.T) {
	var rows [][]any
	if err := json.Unmarshal([]byte(readFixture(t, "testdata/timestamps.json")), &rows); err != nil {
		t.Fatalf("timestamps fixture: %v", err)
	}
	var got []string
	for _, row := range rows {
		in, _ := row[0].(string)
		dt := ParseTimestamp(in, clock)
		v := "null"
		if dt != nil {
			v = pyQuote(pyIso(*dt))
		}
		got = append(got, "["+pyQuote(in)+", "+v+"]")
	}
	assertBytes(t, "timestamps",
		"["+strings.Join(got, ", ")+"]\n", readFixture(t, "testdata/timestamps.json"))
}

func TestParseCountGolden(t *testing.T) {
	var rows [][]any
	if err := json.Unmarshal([]byte(readFixture(t, "testdata/counts.json")), &rows); err != nil {
		t.Fatalf("counts fixture: %v", err)
	}
	var got []string
	for _, row := range rows {
		in, _ := row[0].(string)
		c := ParseCount(in)
		v := "null"
		if c != nil {
			v = fmt.Sprintf("%d", *c)
		}
		got = append(got, "["+pyQuote(in)+", "+v+"]")
	}
	assertBytes(t, "counts",
		"["+strings.Join(got, ", ")+"]\n", readFixture(t, "testdata/counts.json"))
}

func mustJSON(v any) string {
	b, err := json.Marshal(v)
	if err != nil {
		panic(err)
	}
	return string(b)
}
