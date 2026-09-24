// Package worker is slice A of postharvest's Go compute service.
//
// Byte-mirror of backend/scraper/normalizer.py (single source of truth):
// the canonical 33-key normalized post dict, clean_text, hashtag/mention
// extraction and post-type classification.  Golden bytes for every value
// asserted in *_test.go were extracted by running the real Python (see
// README.md) — nothing here is asserted from belief.
package worker

import (
	"regexp"
	"strings"
	"time"
	"unicode"
)

// NORMALIZED_KEYS is the canonical 33-key schema, in Python's exact order
// (backend/scraper/normalizer.py:47-56).
var NORMALIZED_KEYS = []string{
	"post_id", "facebook_url", "post_url", "page_name", "page_id",
	"profile_url", "post_type", "published_at", "timestamp", "text",
	"caption", "hashtags", "mentions", "external_links", "likes", "reactions",
	"comments_count", "shares", "views_count", "reaction_like_count",
	"reaction_love_count", "reaction_care_count", "reaction_haha_count",
	"reaction_wow_count", "reaction_sad_count", "reaction_angry_count",
	"media_type", "thumbnail_url", "media_url", "video_url", "transcript",
	"transcript_language", "scraped_at",
}

// ParsedPost mirrors backend/scraper/parser.py:276-308 byte for byte.
type ParsedPost struct {
	PostID         *string
	PostURL        *string
	Text           *string
	PublishedAt    *time.Time
	PublishedAtRaw *string
	Likes          *int64
	Reactions      *int64
	LikesTotal     *int64
	CommentsCount  *int64
	Shares         *int64
	ViewsCount     *int64
	ReactionLike   *int64
	ReactionLove   *int64
	ReactionCare   *int64
	ReactionHaha   *int64
	ReactionWow    *int64
	ReactionSad    *int64
	ReactionAngry  *int64
	HasImage       bool
	HasVideo       bool
	HasLinkPreview bool
	ThumbnailURL   *string
	MediaURL       *string
	VideoURL       *string
	ExternalLinks  []string
	Mentions       []string
}

// FromDict maps a PostID-present JSON-ish shape onto ParsedPost.  Reserved
// for worker input parsing (mirrors ParsedPost.to_dict() round-trips); the
// hermetic tests construct ParsedPost directly, so this stays thin.
func (p *ParsedPost) FromDict(d map[string]interface{}) { unifyParsed(p, d) }

func ptrStr(s string) *string { return &s }
func ptrInt(i int64) *int64   { return &i }

func unifyParsed(p *ParsedPost, d map[string]interface{}) {
	if v, ok := d["post_id"]; ok && v != nil {
		p.PostID = ptrStr(v.(string))
	}
	if v, ok := d["post_url"]; ok && v != nil {
		p.PostURL = ptrStr(v.(string))
	}
	if v, ok := d["text"]; ok && v != nil {
		p.Text = ptrStr(v.(string))
	}
	if v, ok := d["published_at_raw"]; ok && v != nil {
		p.PublishedAtRaw = ptrStr(v.(string))
	}
	if v, ok := d["likes"]; ok && v != nil {
		p.Likes = ptrInt(int64(v.(float64)))
	}
	if v, ok := d["reactions"]; ok && v != nil {
		p.Reactions = ptrInt(int64(v.(float64)))
	}
	if v, ok := d["likes_total"]; ok && v != nil {
		p.LikesTotal = ptrInt(int64(v.(float64)))
	}
	if v, ok := d["comments_count"]; ok && v != nil {
		p.CommentsCount = ptrInt(int64(v.(float64)))
	}
	if v, ok := d["shares"]; ok && v != nil {
		p.Shares = ptrInt(int64(v.(float64)))
	}
	if v, ok := d["views_count"]; ok && v != nil {
		p.ViewsCount = ptrInt(int64(v.(float64)))
	}
	p.HasImage, _ = d["has_image"].(bool)
	p.HasVideo, _ = d["has_video"].(bool)
	p.HasLinkPreview, _ = d["has_link_preview"].(bool)
	if v, ok := d["thumbnail_url"]; ok && v != nil {
		p.ThumbnailURL = ptrStr(v.(string))
	}
	if v, ok := d["media_url"]; ok && v != nil {
		p.MediaURL = ptrStr(v.(string))
	}
	if v, ok := d["video_url"]; ok && v != nil {
		p.VideoURL = ptrStr(v.(string))
	}
}

// fixSurrogates mirrors normalizer._fix_surrogates: HTML-entity-decoded
// surrogate halves are recombined into their code point; lone halves become
// U+FFFD.  (Go strings cannot normally carry surrogates, so this is the
// faithful port and a no-op on valid UTF-8 — kept for byte-parity with
// Python when inputs are crafted from entity escapes.)
func fixSurrogates(s string) string {
	rs := []rune(s)
	if len(rs) == 0 {
		return s
	}
	var b strings.Builder
	i := 0
	for i < len(rs) {
		c := rs[i]
		if c >= 0xD800 && c <= 0xDBFF && i+1 < len(rs) {
			low := rs[i+1]
			if low >= 0xDC00 && low <= 0xDFFF {
				b.WriteRune(0x10000 + ((c - 0xD800) << 10) + (low - 0xDC00))
				i += 2
				continue
			}
		}
		if c >= 0xD800 && c <= 0xDFFF {
			b.WriteRune(0xFFFD)
		} else {
			b.WriteRune(c)
		}
		i++
	}
	return b.String()
}

// pySpace reports whether r is whitespace per Python's str.isspace(): the
// Go White_Space property PLUS U+001C..U+001F (which Go's unicode.IsSpace
// omits but Python includes).
func pySpace(r rune) bool {
	if r >= 0x1C && r <= 0x1F {
		return true
	}
	return unicode.IsSpace(r)
}

// pySplit mirrors str.split() with no args: split on runs of Python
// whitespace, no separators kept, empties dropped.  (differs from
// strings.Fields only for U+001C..U+001F, handled via pySpace.)
func pySplit(s string) []string {
	var out []string
	var cur strings.Builder
	spacing := false
	for _, r := range s {
		if pySpace(r) {
			spacing = true
			continue
		}
		if spacing && cur.Len() > 0 {
			out = append(out, cur.String())
			cur.Reset()
		}
		spacing = false
		cur.WriteRune(r)
	}
	if cur.Len() > 0 {
		out = append(out, cur.String())
	}
	return out
}

var ctrlRe = regexp.MustCompile(`[\x00-\x08\x0b\x0c\x0e-\x1f]`)

// cleanText mirrors normalizer.clean_text: collapse whitespace, strip the
// documented control range, return nil when nothing remains.
func cleanText(raw *string) *string {
	if raw == nil {
		return nil
	}
	text := fixSurrogates(*raw)
	text = strings.Join(pySplit(text), " ")
	text = ctrlRe.ReplaceAllString(text, "")
	text = strings.TrimSpace(text)
	if text == "" {
		return nil
	}
	return &text
}

var hashtagRe = regexp.MustCompile(`#([A-Za-z0-9_\x{80}-\x{FFFF}]+)`)
var mentionRe = regexp.MustCompile(`@([A-Za-z0-9_.\-\x{80}-\x{FFFF}]+)`)

// uniq preserves order and drops repeats, mirroring normalizer._uniq.
func uniq(seq []string) []string {
	seen := make(map[string]struct{}, len(seq))
	out := make([]string, 0, len(seq))
	for _, s := range seq {
		if _, ok := seen[s]; ok {
			continue
		}
		seen[s] = struct{}{}
		out = append(out, s)
	}
	return out
}

// ExtractHashtags mirrors normalizer.extract_hashtags: "#tag" from text,
// trailing ".,;:!?" trimmed, "#" itself dropped, deduped in order.
func ExtractHashtags(text *string) []string {
	if text == nil || *text == "" {
		return nil
	}
	var tags []string
	for _, m := range hashtagRe.FindAllStringSubmatch(*text, -1) {
		tag := "#" + strings.TrimRight(m[1], ".,;:!?")
		tags = append(tags, tag)
	}
	kept := make([]string, 0, len(tags))
	for _, t := range tags {
		if len([]rune(t)) > 1 {
			kept = append(kept, t)
		}
	}
	return uniq(kept)
}

// ExtractMentions mirrors normalizer.extract_mentions: "@handle" from text,
// trailing ".,;:!?" trimmed, deduped in order.
func ExtractMentions(text *string) []string {
	if text == nil || *text == "" {
		return nil
	}
	var out []string
	for _, m := range mentionRe.FindAllStringSubmatch(*text, -1) {
		out = append(out, "@"+strings.TrimRight(m[1], ".,;:!?"))
	}
	return uniq(out)
}

// classifyPostType mirrors normalizer.classify_post_type's priority:
// video > link-preview/link > image > text, else nil.
func classifyPostType(hasVideo bool, videoURL *string, hasImage bool,
	mediaURL, thumbnailURL *string, hasLinkPreview bool,
	externalLinks []string, text *string) *string {

	if hasVideo || videoURL != nil {
		return ptrStr("video")
	}
	if hasLinkPreview || (len(externalLinks) > 0 && !hasImage && !hasVideo) {
		return ptrStr("link")
	}
	if hasImage || mediaURL != nil || thumbnailURL != nil {
		return ptrStr("image")
	}
	if len(externalLinks) > 0 {
		return ptrStr("link")
	}
	if text != nil {
		return ptrStr("text")
	}
	return nil
}

// isoTime mirrors datetime.isoformat() in UTC ("+00:00", microseconds only
// when non-zero — normalizer._to_iso_and_epoch).
func isoTime(t time.Time) string {
	t = t.UTC()
	if t.Nanosecond() == 0 {
		return t.Format("2006-01-02T15:04:05-07:00")
	}
	return t.Format("2006-01-02T15:04:05.000000-07:00")
}

// NormalizePost mirrors normalizer.normalize_post: one ParsedPost -> the
// canonical 33-key dict (nil values remain nil -> JSON null).  now is the
// injectable clock used for scraped_at.
func NormalizePost(parsed *ParsedPost, pageName, pageID, facebookURL *string, now time.Time) map[string]interface{} {
	scrapedAt := isoTime(now)

	text := cleanText(parsed.Text)

	hashtags := ExtractHashtags(text)

	var rawMentions []string
	for _, m := range parsed.Mentions {
		if m != "" {
			rawMentions = append(rawMentions, m)
		}
	}
	mentions := uniq(append(rawMentions, ExtractMentions(text)...))

	var external []string
	for _, u := range parsed.ExternalLinks {
		if u != "" {
			external = append(external, u)
		}
	}
	external = uniq(external)

	hasVideo := parsed.HasVideo || parsed.VideoURL != nil
	hasImage := parsed.HasImage || parsed.ThumbnailURL != nil || parsed.MediaURL != nil

	postType := classifyPostType(hasVideo, parsed.VideoURL, hasImage,
		parsed.MediaURL, parsed.ThumbnailURL, parsed.HasLinkPreview,
		external, text)

	var publishedAt *string
	var timestamp *int64
	if parsed.PublishedAt != nil {
		pub := isoTime(*parsed.PublishedAt)
		publishedAt = &pub
		ts := (*parsed.PublishedAt).UTC().Unix()
		timestamp = &ts
	}

	// upgrade: media present but classify said "text"
	if postType != nil && *postType == "text" {
		if parsed.VideoURL != nil {
			postType = ptrStr("video")
		} else if parsed.MediaURL != nil || parsed.ThumbnailURL != nil {
			postType = ptrStr("image")
		}
	}

	// reactions: explicit public total, else the sum of a rendered
	// breakdown when at least one per-reaction count exists.
	reactions := parsed.Reactions
	if reactions == nil {
		names := []*int64{
			parsed.ReactionLike, parsed.ReactionLove, parsed.ReactionCare,
			parsed.ReactionHaha, parsed.ReactionWow, parsed.ReactionSad,
			parsed.ReactionAngry,
		}
		var sum int64
		any := false
		for _, n := range names {
			if n != nil {
				sum += *n
				any = true
			}
		}
		if any && sum != 0 {
			reactions = &sum
		}
	}

	var mediaType *string
	switch {
	case postType != nil && *postType == "video":
		mediaType = ptrStr("video")
	case postType != nil && *postType == "image":
		mediaType = ptrStr("image")
	}

	return map[string]interface{}{
		"post_id":              cleanStr(parsed.PostID),
		"facebook_url":         cleanStr(facebookURL),
		"post_url":             cleanStr(parsed.PostURL),
		"page_name":            cleanStr(pageName),
		"page_id":              cleanStr(pageID),
		"profile_url":          cleanStr(facebookURL), // source is a page/profile URL
		"post_type":            postType,
		"published_at":         publishedAt,
		"timestamp":            timestamp,
		"text":                 text,
		"caption":              nil, // not separately present in public markup
		"hashtags":             hashtags,
		"mentions":             mentions,
		"external_links":       external,
		"likes":                parsed.Likes,
		"reactions":            reactions,
		"comments_count":       parsed.CommentsCount,
		"shares":               parsed.Shares,
		"views_count":          parsed.ViewsCount,
		"reaction_like_count":  parsed.ReactionLike,
		"reaction_love_count":  parsed.ReactionLove,
		"reaction_care_count":  parsed.ReactionCare,
		"reaction_haha_count":  parsed.ReactionHaha,
		"reaction_wow_count":   parsed.ReactionWow,
		"reaction_sad_count":   parsed.ReactionSad,
		"reaction_angry_count": parsed.ReactionAngry,
		"media_type":           mediaType,
		"thumbnail_url":        cleanStr(parsed.ThumbnailURL),
		"media_url":            cleanStr(parsed.MediaURL),
		"video_url":            cleanStr(parsed.VideoURL),
		"transcript":           nil, // never public on HTML pages
		"transcript_language":  nil, // never public on HTML pages
		"scraped_at":           &scrapedAt,
	}
}

// cleanStr replaces nil with nil and passes valid UTF-8 through the same
// surrogate-repair every string value gets in normalizer.normalize_post.
func cleanStr(s *string) interface{} {
	if s == nil {
		return nil
	}
	fixed := fixSurrogates(*s)
	return &fixed
}
