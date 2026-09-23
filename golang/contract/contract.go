// Package contract holds the Phase-1 wire shapes for postharvest's Go
// compute worker, mirroring shared/proto/postharvest.proto field-for-field
// (finalplanv2.md §6). Same names as the proto3 fields, null for missing
// scalars — the JSON used on the wire during Phase 1 HTTP+JSON is the
// literal proto3 field-name shape, exactly like node/src/types.ts.
//
// Editing order (never skip):
//  1. Change shared/proto/postharvest.proto FIRST (add fields with the next
//     unused numbers only — field discipline block at the top of the file).
//  2. Mirror the change in node/src/types.ts.
//  3. Mirror the change here.
//  4. Update/create the golden fixtures in shared/fixtures/.
package contract

// Post is the canonical 33-key normalized post
// (backend/scraper/normalizer.py NORMALIZED_KEYS). Pointers encode the
// proto3 JSON "null" for missing scalars; slices encode repeated fields.
type Post struct {
	PostID         string   `json:"post_id"`
	FacebookURL    string   `json:"facebook_url"`
	PostURL        string   `json:"post_url"`
	PageName       string   `json:"page_name"`
	PageID         string   `json:"page_id"`
	ProfileURL     string   `json:"profile_url"`
	PostType       string   `json:"post_type"` // "text"|"image"|"video"|"link" ("" == null)
	PublishedAt    string   `json:"published_at"`
	Timestamp      int64    `json:"timestamp"`
	Text           string   `json:"text"`
	Caption        string   `json:"caption"` // ALWAYS "" for public-HTML scrapes
	Hashtags       []string `json:"hashtags"`
	Mentions       []string `json:"mentions"`
	ExternalLinks  []string `json:"external_links"`
	Likes          int64    `json:"likes"`
	Reactions      int64    `json:"reactions"`
	CommentsCount  int64    `json:"comments_count"`
	Shares         int64    `json:"shares"`
	ViewsCount     int64    `json:"views_count"`
	ReactionLike   int64    `json:"reaction_like_count"`
	ReactionLove   int64    `json:"reaction_love_count"`
	ReactionCare   int64    `json:"reaction_care_count"`
	ReactionHaha   int64    `json:"reaction_haha_count"`
	ReactionWow    int64    `json:"reaction_wow_count"`
	ReactionSad    int64    `json:"reaction_sad_count"`
	ReactionAngry  int64    `json:"reaction_angry_count"`
	MediaType      string   `json:"media_type"` // "video"|"image" ("" == none)
	ThumbnailURL   string   `json:"thumbnail_url"`
	MediaURL       string   `json:"media_url"`
	VideoURL       string   `json:"video_url"`
	Transcript     string   `json:"transcript"`
	TranscriptLang string   `json:"transcript_language"`
	ScrapedAt      string   `json:"scraped_at"`
}

// PositionalKey is the 1-based field position of a Post key, matching the
// proto field numbers 1..33 locked to NORMALIZED_KEYS order.
type PositionalKey struct {
	Name string
	Num  int
}

// PostFieldNumbers is the authoritative key->proto-field-number mapping.
// Field numbers 1..33 are locked to backend/scraper/normalizer.py
// NORMALIZED_KEYS order (proto header comment). Do not renumber.
var PostFieldNumbers = []PositionalKey{
	{"post_id", 1}, {"facebook_url", 2}, {"post_url", 3}, {"page_name", 4},
	{"page_id", 5}, {"profile_url", 6}, {"post_type", 7}, {"published_at", 8},
	{"timestamp", 9}, {"text", 10}, {"caption", 11}, {"hashtags", 12},
	{"mentions", 13}, {"external_links", 14}, {"likes", 15}, {"reactions", 16},
	{"comments_count", 17}, {"shares", 18}, {"views_count", 19},
	{"reaction_like_count", 20}, {"reaction_love_count", 21},
	{"reaction_care_count", 22}, {"reaction_haha_count", 23},
	{"reaction_wow_count", 24}, {"reaction_sad_count", 25},
	{"reaction_angry_count", 26}, {"media_type", 27}, {"thumbnail_url", 28},
	{"media_url", 29}, {"video_url", 30}, {"transcript", 31},
	{"transcript_language", 32}, {"scraped_at", 33},
}

// BrowserStats is the browser-mode capture summary (FetchResponse.browser_stats).
type BrowserStats struct {
	LoginWall   bool `json:"login_wall"`
	FeedMissing bool `json:"feed_missing"`
	PostsFound  int  `json:"posts_found"`
}

// FetchRequest is FastAPI -> node (mirrors the .proto message).
type FetchRequest struct {
	TargetURL    string   `json:"target_url"`
	Mode         string   `json:"mode"` // "http" | "browser"
	AccountID    string   `json:"account_id"`
	ScrollRounds int      `json:"scroll_rounds"`
	MaxPosts     int      `json:"max_posts"`
	Cookies      []string `json:"cookies"`
}

// FetchResponse is node -> FastAPI. RawPayload is base64 of the raw body
// bytes; it is astring here because that is what the wire carries (proto3
// `bytes` over HTTP+JSON = base64 string, matching node/src/types.ts).
type FetchResponse struct {
	StatusCode     int           `json:"status_code"`
	FinalURL       string        `json:"final_url"`
	ContentType    string        `json:"content_type"`
	RawPayload     string        `json:"raw_payload"`
	FetchedAtMs    int64         `json:"fetched_at_ms"`
	UpdatedCookies []string      `json:"updated_cookies"`
	BrowserStats   *BrowserStats `json:"browser_stats"` // nil == null
}

// ParseRequest is FastAPI -> go. RawPayload is base64 of the raw body bytes;
// ContentType is "html" | "graphql_json"; IdempotencyKey is job_id + ":" +
// source_id — finalplanv2.md §8(c). TargetURL is the page URL used for link
// resolution and normalization's facebook_url context (pass the final URL
// after redirects, like Python's crawler passes final_url to parse_page);
// Handle is the page handle, a page_name fallback when markup has none.
type ParseRequest struct {
	RawPayload     string `json:"raw_payload"`
	ContentType    string `json:"content_type"`
	IdempotencyKey string `json:"idempotency_key"`
	TargetURL      string `json:"target_url"`
	Handle         string `json:"handle"`
}

// ParseResponse is go -> FastAPI (mirrors the .proto message). Posts are the
// canonical 33-key normalized posts; Errors isolates per-post parse failures.
type ParseResponse struct {
	Posts  []Post   `json:"posts"`
	Errors []string `json:"errors"`
}
