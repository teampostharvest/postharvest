// Golden byte-parity proofs: every expected value below was extracted by
// running the REAL Python (backend/scraper/normalizer.py) — see
// /tmp/opencode/golden_vector.py.  Nothing is asserted from belief.
package worker

import (
	"reflect"
	"testing"
	"time"
)

var goldenPublishedAt = time.Date(2026, 1, 15, 12, 0, 0, 0, time.UTC)
var goldenNow = time.Date(2026, 1, 15, 12, 0, 0, 0, time.UTC)

// goldenJSONL is the exact bytes Python json.dumps(post, cls=PostJSONEncoder,
// ensure_ascii=False) produced for the golden ParsedPost.
const goldenJSONL = `{"post_id": "1234567890", "facebook_url": "https://www.facebook.com/Page", "post_url": "https://www.facebook.com/permalink.php?story_fbid=1234567890&id=999", "page_name": "Page", "page_id": "999", "profile_url": "https://www.facebook.com/Page", "post_type": "image", "published_at": "2026-01-15T12:00:00+00:00", "timestamp": 1768478400, "text": "Hello #Go and #workers @postharvest see https://example.com #Go", "caption": null, "hashtags": ["#Go", "#workers"], "mentions": ["@handlegiven", "@postharvest"], "external_links": ["https://example.com"], "likes": 10, "reactions": 25, "comments_count": 3, "shares": 2, "views_count": null, "reaction_like_count": 7, "reaction_love_count": 2, "reaction_care_count": null, "reaction_haha_count": null, "reaction_wow_count": null, "reaction_sad_count": null, "reaction_angry_count": null, "media_type": "image", "thumbnail_url": "https://img.example/pic.jpg", "media_url": null, "video_url": null, "transcript": null, "transcript_language": null, "scraped_at": "2026-01-15T12:00:00+00:00"}`

func goldenParsed() *ParsedPost {
	text := "Hello #Go and #workers @postharvest see https://example.com #Go"
	return &ParsedPost{
		PostID:        ptrStr("1234567890"),
		PostURL:       ptrStr("https://www.facebook.com/permalink.php?story_fbid=1234567890&id=999"),
		Text:          &text,
		PublishedAt:   &goldenPublishedAt,
		Likes:         ptrInt(10),
		Reactions:     ptrInt(25),
		CommentsCount: ptrInt(3),
		Shares:        ptrInt(2),
		ReactionLike:  ptrInt(7),
		ReactionLove:  ptrInt(2),
		HasImage:      true,
		ThumbnailURL:  ptrStr("https://img.example/pic.jpg"),
		ExternalLinks: []string{"https://example.com"},
		Mentions:      []string{"@handlegiven"},
	}
}

func TestNormalizedKeysMatchPythonOrder(t *testing.T) {
	want := []string{
		"post_id", "facebook_url", "post_url", "page_name", "page_id",
		"profile_url", "post_type", "published_at", "timestamp", "text",
		"caption", "hashtags", "mentions", "external_links", "likes", "reactions",
		"comments_count", "shares", "views_count", "reaction_like_count",
		"reaction_love_count", "reaction_care_count", "reaction_haha_count",
		"reaction_wow_count", "reaction_sad_count", "reaction_angry_count",
		"media_type", "thumbnail_url", "media_url", "video_url", "transcript",
		"transcript_language", "scraped_at",
	}
	if len(NORMALIZED_KEYS) != 33 {
		t.Fatalf("NORMALIZED_KEYS length = %d, want 33", len(NORMALIZED_KEYS))
	}
	if !reflect.DeepEqual(NORMALIZED_KEYS, want) {
		t.Fatalf("NORMALIZED_KEYS mismatch:\ngot  %v\nwant %v", NORMALIZED_KEYS, want)
	}
}

func TestNormalizePostGoldenBytes(t *testing.T) {
	post := NormalizePost(goldenParsed(), ptrStr("Page"), ptrStr("999"),
		ptrStr("https://www.facebook.com/Page"), goldenNow)
	got, err := pythonDictString(post)
	if err != nil {
		t.Fatal(err)
	}
	if got != goldenJSONL {
		t.Fatalf("normalized JSONL byte mismatch\n got %s\nwant %s", got, goldenJSONL)
	}
}

func TestCleanTextGolden(t *testing.T) {
	messy := "  hello \t world\x00\x01 \n  again  "
	got := cleanText(&messy)
	if got == nil || *got != "hello world again" {
		t.Fatalf("cleanText = %#v, want \"hello world again\"", got)
	}
	if cleanText(nil) != nil {
		t.Fatal("cleanText(nil) must be nil")
	}
	blank := "   "
	if cleanText(&blank) != nil {
		t.Fatal("cleanText(whitespace-only) must be nil")
	}
}

func TestExtractHashtagsGolden(t *testing.T) {
	text := "Loving #Go today, see https://example.com #Go and #Worker!"
	got := ExtractHashtags(&text)
	want := []string{"#Go", "#Worker"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("hashtags = %v, want %v", got, want)
	}
}

func TestExtractMentionsGolden(t *testing.T) {
	text := "ping @postharvest and @team."
	got := ExtractMentions(&text)
	want := []string{"@postharvest", "@team"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("mentions = %v, want %v", got, want)
	}
}

func TestClassifyPostTypeGolden(t *testing.T) {
	cases := []struct {
		name           string
		hasVideo       bool
		videoURL       *string
		hasImage       bool
		mediaURL       *string
		thumbnailURL   *string
		hasLinkPreview bool
		externalLinks  []string
		text           *string
		want           string
	}{
		{"video", false, ptrStr("https://v"), false, nil, nil, false, nil, ptrStr("x"), "video"},
		{"link", false, nil, false, nil, nil, true, nil, ptrStr("x"), "link"},
		{"image", false, nil, true, nil, nil, false, nil, ptrStr("x"), "image"},
		{"text", false, nil, false, nil, nil, false, nil, ptrStr("hello"), "text"},
	}
	for _, c := range cases {
		got := classifyPostType(c.hasVideo, c.videoURL, c.hasImage, c.mediaURL,
			c.thumbnailURL, c.hasLinkPreview, c.externalLinks, c.text)
		if got == nil || *got != c.want {
			t.Fatalf("%s: got %v, want %q", c.name, got, c.want)
		}
	}
	if got := classifyPostType(false, nil, false, nil, nil, false, nil, nil); got != nil {
		t.Fatalf("nothing identifiable must be nil, got %v", got)
	}
}
