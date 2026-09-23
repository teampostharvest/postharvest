package parser

// Script-fallback extraction — direct mirror of parser.py
// _extract_posts_from_scripts (+ _decode_fb_text).
//
// Modern Facebook (2024+) embeds Relay/Comet post data as JSON inside
// <script> tags. When FindPostRoots finds no DOM containers this path
// re-scans the raw HTML for "post_id" anchors and harvests data around each
// occurrence: creation_time, message text (patterns 1-5, priority order),
// engagement windows (±20000 chars around every post_id hit), attachment
// signals, owner_id permalink and mentions/external links from the text.

import (
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	worker "postharvest/golang"
)

var (
	scriptPostIDRe       = regexp.MustCompile(`"post_id"\s*:\s*"(\d+)"`)
	scriptCreationRe     = regexp.MustCompile(`"creation_time"\s*:\s*(\d{10})`)
	scriptJSONTextRe     = regexp.MustCompile(`"text"\s*:\s*"((?:[^"\\]|\\.)*)"`)
	scriptMessageRe      = regexp.MustCompile(`"message"\s*:\s*\{`)
	scriptMessageTextRe  = regexp.MustCompile(`"message_text"\s*:\s*"((?:[^"\\]|\\.)*)"`)
	scriptTextValueRe    = regexp.MustCompile(`"text_value"\s*:\s*"((?:[^"\\]|\\.)*)"`)
	scriptContentRe      = regexp.MustCompile(`"content"\s*:\s*\{`)
	scriptReactionRe     = regexp.MustCompile(`"reaction_count"\s*:\s*\{[^}]*?"count"\s*:\s*(\d+)`)
	scriptCommentRe      = regexp.MustCompile(`"comment_count"\s*:\s*\{[^}]*?"count"\s*:\s*(\d+)`)
	scriptShareRe        = regexp.MustCompile(`"share_count"\s*:\s*\{[^}]*?"count"\s*:\s*(\d+)`)
	scriptAttStyleRe     = regexp.MustCompile(`"story_attachment_style"\s*:\s*"(\w+)"`)
	scontentURIRE        = regexp.MustCompile(`"uri"\s*:\s*"(https?://scontent[^"]+)"`)
	scriptOwnerRe        = regexp.MustCompile(`"owner_id"\s*:\s*"(\d+)"`)
	scriptMentionRe      = regexp.MustCompile(`@([A-Za-z0-9_.\-]+)`)
	scriptExternalLinkRe = regexp.MustCompile(`(https?://[^\s"'<>]+)`)
	delightRangesRe      = regexp.MustCompile(`"delight_ranges"\s*:\s*\[[^\]]*\]`)
	inlineRangesRe       = regexp.MustCompile(`"inline_style_ranges"\s*:\s*\[[^\]]*\]`)
)

// extractPostsFromScripts mirrors _extract_posts_from_scripts: unique post
// ids in first-seen order, anchored 3000 bytes back / 15000 forward.
func extractPostsFromScripts(html, pageURL string, now time.Time) ([]*worker.ParsedPost, error) {
	if html == "" {
		return nil, nil
	}
	var posts []*worker.ParsedPost

	// The Python source slices html by *str* offsets; Go regexes report byte
	// offsets. Every window below goes through the rune conversion so pages
	// with non-ASCII before the first script stay byte-true.
	var order []string
	positions := map[string][]int{}
	for _, idx := range scriptPostIDRe.FindAllStringSubmatchIndex(html, -1) {
		pid := html[idx[2]:idx[3]]
		if _, ok := positions[pid]; !ok {
			order = append(order, pid)
		}
		positions[pid] = append(positions[pid], runeIndexOf(html, idx[0]))
	}

	for _, postID := range order {
		posList := positions[postID]
		anchor := posList[0]
		block := charWindow(html, anchor-3000, anchor+15000)

		// creation_time (nearest to the post_id anchor)
		var publishedAt *time.Time
		if m := scriptCreationRe.FindStringSubmatch(block); m != nil {
			if ts, err := strconv.ParseInt(m[1], 10, 64); err == nil {
				publishedAt = fromTimestampFloatVal(float64(ts))
			}
		}

		// message text — multiple patterns in priority order
		textVal := ""

		// Pattern 1: delight_ranges / inline_style_ranges, then nearby "text"
		for _, rangesRe := range []*regexp.Regexp{delightRangesRe, inlineRangesRe} {
			rm := rangesRe.FindStringIndex(block)
			if rm == nil {
				continue
			}
			nearby := charWindow(block, runeIndexOf(block, rm[1]), runeIndexOf(block, rm[1])+2000)
			if tm := scriptJSONTextRe.FindStringSubmatch(nearby); tm != nil && pyRuneLen(tm[1]) > 2 {
				textVal = fbText(tm[1])
				break
			}
		}

		// Pattern 2: "message":{"text":"..."}
		if textVal == "" {
			for _, ms := range scriptMessageRe.FindAllStringIndex(block, -1) {
				window := charWindow(block, runeIndexOf(block, ms[0]), runeIndexOf(block, ms[0])+3000)
				if tm := scriptJSONTextRe.FindStringSubmatch(window); tm != nil && pyRuneLen(tm[1]) > 5 {
					textVal = fbText(tm[1])
					break
				}
			}
		}

		// Pattern 3: "message_text":"..."
		if textVal == "" {
			if tm := scriptMessageTextRe.FindStringSubmatch(block); tm != nil && pyRuneLen(tm[1]) > 5 {
				textVal = fbText(tm[1])
			}
		}

		// Pattern 4: "text_value":"..."
		if textVal == "" {
			if tm := scriptTextValueRe.FindStringSubmatch(block); tm != nil && pyRuneLen(tm[1]) > 5 {
				textVal = fbText(tm[1])
			}
		}

		// Pattern 5: "content":{"text":"..."} (shared content blocks)
		if textVal == "" {
			for _, cs := range scriptContentRe.FindAllStringIndex(block, -1) {
				window := charWindow(block, runeIndexOf(block, cs[0]), runeIndexOf(block, cs[0])+3000)
				if tm := scriptJSONTextRe.FindStringSubmatch(window); tm != nil && pyRuneLen(tm[1]) > 5 {
					textVal = fbText(tm[1])
					break
				}
			}
		}

		// engagement — search a 20000-char window around EVERY occurrence of
		// this post_id (short-circuiting each metric once found)
		var reactions, commentsCount, shares *int64
		for _, sp := range posList {
			searchBlock := charWindow(html, sp, sp+20000)
			if reactions == nil {
				if m := scriptReactionRe.FindStringSubmatch(searchBlock); m != nil {
					if v, err := strconv.ParseInt(m[1], 10, 64); err == nil {
						reactions = &v
					}
				}
			}
			if commentsCount == nil {
				if m := scriptCommentRe.FindStringSubmatch(searchBlock); m != nil {
					if v, err := strconv.ParseInt(m[1], 10, 64); err == nil {
						commentsCount = &v
					}
				}
			}
			if shares == nil {
				if m := scriptShareRe.FindStringSubmatch(searchBlock); m != nil {
					if v, err := strconv.ParseInt(m[1], 10, 64); err == nil {
						shares = &v
					}
				}
			}
		}

		// attachment type and media signals
		attachmentStyle := ""
		if m := scriptAttStyleRe.FindStringSubmatch(block); m != nil {
			attachmentStyle = m[1]
		}
		hasImage := strings.Contains(block, `"__typename":"Photo"`)
		hasVideo := strings.Contains(block, `"__typename":"Video"`)
		hasLink := attachmentStyle != "" && (attachmentStyle == "share" || attachmentStyle == "link")

		// thumbnail/media URL (blocked by the leading https?:// elsewhere)
		var thumbnailURL, mediaURL string
		if m := scontentURIRE.FindStringSubmatch(block); m != nil {
			thumb := strings.ReplaceAll(m[1], `\/`, "/")
			thumbnailURL = thumb
			mediaURL = thumb
		}

		// permalink construction
		postURL := "https://www.facebook.com/permalink.php?story_fbid=" + postID
		if m := scriptOwnerRe.FindStringSubmatch(block); m != nil {
			postURL = "https://www.facebook.com/permalink.php?story_fbid=" + postID + "&id=" + m[1]
		}

		// mentions and external links from the decoded text (NOT deduped —
		// the Python source keeps every findall hit)
		var mentions, external []string
		for _, m := range scriptMentionRe.FindAllStringSubmatch(textVal, -1) {
			mentions = append(mentions, m[1])
		}
		for _, m := range scriptExternalLinkRe.FindAllStringSubmatch(textVal, -1) {
			if !strings.Contains(strings.ToLower(m[1]), "facebook.com") {
				external = append(external, m[1])
			}
		}

		var publishedRaw string
		if publishedAt != nil {
			publishedRaw = strconv.FormatInt(publishedAt.Unix(), 10)
		}

		posts = append(posts, &worker.ParsedPost{
			PostID:         nz(postID),
			PostURL:        nz(postURL),
			Text:           nz(textVal),
			PublishedAt:    publishedAt,
			PublishedAtRaw: nz(publishedRaw),
			Reactions:      reactions,
			Likes:          reactions,
			CommentsCount:  commentsCount,
			Shares:         shares,
			HasImage:       hasImage,
			HasVideo:       hasVideo,
			HasLinkPreview: hasLink,
			ThumbnailURL:   nz(thumbnailURL),
			MediaURL:       nz(mediaURL),
			ExternalLinks:  external,
			Mentions:       mentions,
		})
	}
	return posts, nil
}

// fbText decodes a JSON-escaped text group; "" when empty/whitespace only
// (Python: _decode_fb_text(...) or None).
func fbText(s string) string {
	if p := decodeFbText(s); p != nil {
		return *p
	}
	return ""
}

// decodeFbText mirrors _decode_fb_text: raw.encode("utf-8").
// decode("unicode_escape") with a fallback that decodes common sequences.
// The Python codec maps non-ASCII *bytes* 1:1 to Latin-1 characters (the
// mojibake behavior is intentional and mirrored). Unknown escapes keep their
// backslash (empirical CPython codec behaviour). Since Go strings cannot
// hold lone surrogates, \uXXXX pairs are combined and lone surrogates become
// U+FFFD — the same transformation the normalizer applies downstream.
func decodeFbText(raw string) *string {
	decoded := raw
	if out, err := unicodeEscape(raw); err == nil {
		decoded = out
	} else {
		// Fallback: manually decode common sequences (same order as Python).
		decoded = strings.ReplaceAll(decoded, `\n`, "\n")
		decoded = strings.ReplaceAll(decoded, `\r`, "\r")
		decoded = strings.ReplaceAll(decoded, `\t`, "\t")
		decoded = strings.ReplaceAll(decoded, `\"`, `"`)
		decoded = strings.ReplaceAll(decoded, `\\`, `\`)
	}
	decoded = pyTrim(decoded)
	if decoded == "" {
		return nil
	}
	return &decoded
}

var errTruncatedEscape = fmt.Errorf("truncated unicode escape")

// unicodeEscape mirrors CPython's "unicode_escape" codec on the UTF-8 bytes
// of a string (raw.encode("utf-8").decode("unicode_escape")). Errors mirror
// ValueError (truncated \x/\u/\U hex sequences).
func unicodeEscape(raw string) (string, error) {
	b := []byte(raw)
	var sb strings.Builder
	i := 0
	for i < len(b) {
		c := b[i]
		if c != '\\' {
			sb.WriteRune(rune(c)) // non-ASCII bytes map 1:1 (Latin-1)
			i++
			continue
		}
		if i+1 >= len(b) { // trailing lone backslash: kept
			sb.WriteByte('\\')
			break
		}
		e := b[i+1]
		i += 2
		switch e {
		case '\\', '\'', '"':
			sb.WriteByte(e)
		case 'a':
			sb.WriteByte('\a')
		case 'b':
			sb.WriteByte('\b')
		case 'f':
			sb.WriteByte('\f')
		case 'n':
			sb.WriteByte('\n')
		case 'r':
			sb.WriteByte('\r')
		case 't':
			sb.WriteByte('\t')
		case 'v':
			sb.WriteByte('\v')
		case 'x':
			val, n, err := readHex(b, i, 2)
			if err != nil {
				return "", errTruncatedEscape
			}
			sb.WriteByte(byte(val))
			i += n
		case 'u', 'U':
			n := 4
			if e == 'U' {
				n = 8
			}
			cp, m, err := readHex(b, i, n)
			if err != nil {
				return "", errTruncatedEscape
			}
			i += m
			if cp >= 0xD800 && cp <= 0xDBFF {
				// combine with a following \uXXXX low surrogate
				if i+6 <= len(b) && b[i] == '\\' && b[i+1] == 'u' {
					low, m2, err2 := readHex(b, i+2, 4)
					if err2 == nil && low >= 0xDC00 && low <= 0xDFFF {
						cp = 0x10000 + (cp-0xD800)<<10 + (low - 0xDC00)
						sb.WriteRune(rune(cp))
						i += 2 + m2
						continue
					}
				}
				sb.WriteRune(0xFFFD) // lone high surrogate
			} else if cp >= 0xDC00 && cp <= 0xDFFF {
				sb.WriteRune(0xFFFD) // lone low surrogate
			} else if cp > 0x10FFFF {
				return "", errTruncatedEscape // Python errors here
			} else {
				sb.WriteRune(rune(cp))
			}
		case '0', '1', '2', '3', '4', '5', '6', '7':
			val := int(e - '0')
			for k := 0; k < 2 && i < len(b) && b[i] >= '0' && b[i] <= '7'; k++ {
				val = val*8 + int(b[i]-'0')
				i++
			}
			sb.WriteByte(byte(val))
		default:
			// unknown escape: backslash preserved (empirical codec quirk)
			sb.WriteByte('\\')
			sb.WriteByte(e)
		}
	}
	return sb.String(), nil
}

// readHex reads up to n hex digits from b[i:]; returns the value, bytes
// consumed and an error when fewer than n hex digits are available.
func readHex(b []byte, i, n int) (int64, int, error) {
	if i+n > len(b) {
		return 0, 0, errTruncatedEscape
	}
	var v int64
	for k := 0; k < n; k++ {
		d := hexDigit(b[i+k])
		if d < 0 {
			return 0, 0, errTruncatedEscape
		}
		v = v<<4 | int64(d)
	}
	return v, n, nil
}

func hexDigit(b byte) int {
	switch {
	case b >= '0' && b <= '9':
		return int(b - '0')
	case b >= 'a' && b <= 'f':
		return int(b-'a') + 10
	case b >= 'A' && b <= 'F':
		return int(b-'A') + 10
	}
	return -1
}

// runeIndexOf converts a byte offset into a rune (Python str) offset.
func runeIndexOf(s string, bytePos int) int {
	if bytePos <= 0 {
		return 0
	}
	if bytePos >= len(s) {
		return utf8.RuneCountInString(s)
	}
	return utf8.RuneCountInString(s[:bytePos])
}

// charWindow mirrors Python s[lower:upper] with implicit clamping of the
// bounds to the string (character offsets). Empty start/end clamp safely.
func charWindow(s string, start, end int) string {
	rs := []rune(s)
	if start < 0 {
		start = 0
	}
	if end > len(rs) {
		end = len(rs)
	}
	if start >= end {
		return ""
	}
	return string(rs[start:end])
}
