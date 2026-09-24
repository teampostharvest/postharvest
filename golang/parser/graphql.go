package parser

// GraphQL-feed extraction — direct mirror of parser.py
// extract_posts_from_graphql (+_iter_json_objects, _graphql_story_nodes,
// _graphql_story_to_post).
//
// Embedded Comet feed payloads live in <script data-fb-graphql-feed="1">
// blocks captured from the browser network. Each block is one or more
// JSON documents; story nodes carry post_id / creation_time / message.text
// media attachments and engagement counts.
//
// This path is stdlib-only (the plan's §5 split). Payloads are decoded with
// a hand-written *ordered* JSON parser because the deep-count fallbacks take
// the FIRST hit in document order — semantics encoding/json would destroy
// with its unordered maps. Number/value truthiness follows Python.
//
// Surrogates from \uXXXX escapes cannot live in a Go string: pairs are
// combined and lone surrogates become U+FFFD (same as the normalizer).

import (
	"fmt"
	"math"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/PuerkitoBio/goquery"

	worker "postharvest/golang"
)

// jKind is the JSON scalar/container kind of a parsed value.
type jKind int

const (
	jNull jKind = iota
	jBool
	jInt
	jFloat
	jString
	jArray
	jObject
)

// jValue is one JSON value with preserved object key order (ordered keys).
type jValue struct {
	kind jKind
	b    bool
	i    int64
	f    float64
	s    string
	arr  []*jValue
	keys []string
	obj  map[string]*jValue
}

// Get returns the object member key, or nil for non-objects/missing keys.
func (v *jValue) Get(key string) *jValue {
	if v == nil || v.kind != jObject {
		return nil
	}
	return v.obj[key]
}

// Str returns the value as a string ("" for non-strings).
func (v *jValue) Str() string {
	if v == nil || v.kind != jString {
		return ""
	}
	return v.s
}

// IsStr reports whether the value is a JSON string.
func (v *jValue) IsStr() bool { return v != nil && v.kind == jString }

// isObj reports whether the value is a JSON object.
func (v *jValue) isObj() bool { return v != nil && v.kind == jObject }

// isNum reports whether the value is a JSON number.
func (v *jValue) isNum() bool { return v != nil && (v.kind == jInt || v.kind == jFloat) }

// numInt mirrors int(v) for JSON numbers (truncation toward zero).
func (v *jValue) numInt() int64 {
	if v == nil {
		return 0
	}
	if v.kind == jInt {
		return v.i
	}
	if v.kind == jFloat {
		return int64(v.f)
	}
	return 0
}

// numFloat returns the numeric value as a float64 (0 for non-numbers).
func (v *jValue) numFloat() float64 {
	if v == nil {
		return 0
	}
	if v.kind == jInt {
		return float64(v.i)
	}
	if v.kind == jFloat {
		return v.f
	}
	return 0
}

// Truthy mirrors Python truthiness: None/0/""/[]/{} are falsy.
func (v *jValue) Truthy() bool {
	if v == nil {
		return false
	}
	switch v.kind {
	case jNull:
		return false
	case jBool:
		return v.b
	case jInt:
		return v.i != 0
	case jFloat:
		return v.f != 0
	case jString:
		return v.s != ""
	case jArray:
		return len(v.arr) > 0
	case jObject:
		return len(v.keys) > 0
	}
	return false
}

var emptyObj = &jValue{kind: jObject, obj: map[string]*jValue{}}
var emptyArr = &jValue{kind: jArray}

// orDefault mirrors `x or fallback` under Python truthiness.
func orDefault(v, fallback *jValue) *jValue {
	if v != nil && v.Truthy() {
		return v
	}
	return fallback
}

// jStrAny mirrors str(v) for plausible story values (post_id may be a
// JSON number). Float formatting follows Python str(float).
func jStrAny(v *jValue) string {
	if v == nil {
		return ""
	}
	switch v.kind {
	case jNull:
		return ""
	case jBool:
		if v.b {
			return "True"
		}
		return "False"
	case jInt:
		return strconv.FormatInt(v.i, 10)
	case jFloat:
		f := v.f
		if f == math.Trunc(f) && math.Abs(f) < 1e16 {
			return strconv.FormatFloat(f, 'f', 1, 64)
		}
		return strconv.FormatFloat(f, 'f', -1, 64)
	case jString:
		return v.s
	default:
		return ""
	}
}

// pickURI returns a usable URI string: the first truthy string among the
// candidates, unwrapping one level of {"uri": ...} object (Python
// `if isinstance(suri, dict): suri = suri.get("uri")`).
func pickURI(vals ...*jValue) string {
	for _, v := range vals {
		if v == nil {
			continue
		}
		if v.kind == jString {
			if v.s != "" {
				return v.s
			}
			continue
		}
		if v.kind == jObject {
			if u := v.Get("uri"); u != nil && u.kind == jString && u.s != "" {
				return u.s
			}
			continue
		}
	}
	return ""
}

// orStr mirrors `primary or fallback` with the fallback as a literal string.
func orStr(primary *jValue, fallback string) string {
	if s := pickURI(primary); s != "" {
		return s
	}
	return fallback
}

// --- ordered JSON decoder (RFC-8259-ish, stdlib-only) ---------------------

func skipWS(data []byte, i *int) {
	for *i < len(data) && (data[*i] == ' ' || data[*i] == '\n' || data[*i] == '\t' || data[*i] == '\r') {
		*i++
	}
}

// parseJSON decodes one JSON value at *pos, advancing *pos past it.
func parseJSON(data []byte, pos *int) (*jValue, error) {
	i := *pos
	skipWS(data, &i)
	if i >= len(data) {
		return nil, fmt.Errorf("unexpected end of input")
	}
	var v *jValue
	var err error
	switch data[i] {
	case '{':
		v, err = parseJSONObject(data, &i)
	case '[':
		v, err = parseJSONArray(data, &i)
	case '"':
		var s string
		s, err = parseJSONString(data, &i)
		if err == nil {
			v = &jValue{kind: jString, s: s}
		}
	case 't':
		if i+4 <= len(data) && string(data[i:i+4]) == "true" {
			v = &jValue{kind: jBool, b: true}
			i += 4
		} else {
			err = fmt.Errorf("invalid literal at %d", i)
		}
	case 'f':
		if i+5 <= len(data) && string(data[i:i+5]) == "false" {
			v = &jValue{kind: jBool}
			i += 5
		} else {
			err = fmt.Errorf("invalid literal at %d", i)
		}
	case 'n':
		if i+4 <= len(data) && string(data[i:i+4]) == "null" {
			v = &jValue{kind: jNull}
			i += 4
		} else {
			err = fmt.Errorf("invalid literal at %d", i)
		}
	default:
		v, err = parseJSONNumber(data, &i)
	}
	if err != nil {
		return nil, err
	}
	*pos = i
	return v, nil
}

func parseJSONObject(data []byte, pos *int) (*jValue, error) {
	i := *pos + 1
	v := &jValue{kind: jObject, obj: map[string]*jValue{}}
	for {
		skipWS(data, &i)
		if i >= len(data) {
			return nil, fmt.Errorf("unterminated object")
		}
		if data[i] == '}' {
			*pos = i + 1
			return v, nil
		}
		if data[i] != '"' {
			return nil, fmt.Errorf("expected object key at %d", i)
		}
		key, err := parseJSONString(data, &i)
		if err != nil {
			return nil, err
		}
		skipWS(data, &i)
		if i >= len(data) || data[i] != ':' {
			return nil, fmt.Errorf("expected ':' at %d", i)
		}
		i++
		val, err := parseJSON(data, &i)
		if err != nil {
			return nil, err
		}
		if _, dup := v.obj[key]; !dup {
			v.keys = append(v.keys, key)
			v.obj[key] = val
		} else {
			v.obj[key] = val // duplicate key: last wins (Python json)
		}
		skipWS(data, &i)
		if i >= len(data) {
			return nil, fmt.Errorf("unterminated object")
		}
		if data[i] == ',' {
			i++
			continue
		}
		if data[i] == '}' {
			*pos = i + 1
			return v, nil
		}
		return nil, fmt.Errorf("expected ',' or '}' at %d", i)
	}
}

func parseJSONArray(data []byte, pos *int) (*jValue, error) {
	i := *pos + 1
	v := &jValue{kind: jArray}
	for {
		skipWS(data, &i)
		if i >= len(data) {
			return nil, fmt.Errorf("unterminated array")
		}
		if data[i] == ']' {
			*pos = i + 1
			return v, nil
		}
		val, err := parseJSON(data, &i)
		if err != nil {
			return nil, err
		}
		v.arr = append(v.arr, val)
		skipWS(data, &i)
		if i >= len(data) {
			return nil, fmt.Errorf("unterminated array")
		}
		if data[i] == ',' {
			i++
			continue
		}
		if data[i] == ']' {
			*pos = i + 1
			return v, nil
		}
		return nil, fmt.Errorf("expected ',' or ']' at %d", i)
	}
}

func parseJSONString(data []byte, pos *int) (string, error) {
	i := *pos + 1
	var sb strings.Builder
	for {
		if i >= len(data) {
			return "", fmt.Errorf("unterminated string")
		}
		c := data[i]
		if c == '"' {
			*pos = i + 1
			return sb.String(), nil
		}
		if c == '\\' {
			i++
			if i >= len(data) {
				return "", fmt.Errorf("unterminated escape")
			}
			e := data[i]
			i++
			switch e {
			case '"':
				sb.WriteByte('"')
			case '\\':
				sb.WriteByte('\\')
			case '/':
				sb.WriteByte('/')
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
			case 'u':
				cp, _, err := readHex(data, i, 4)
				if err != nil {
					return "", err
				}
				i += 4
				if cp >= 0xD800 && cp <= 0xDBFF && i+6 <= len(data) &&
					data[i] == '\\' && data[i+1] == 'u' {
					low, _, err := readHex(data, i+2, 4)
					if err == nil && low >= 0xDC00 && low <= 0xDFFF {
						cp = 0x10000 + (cp-0xD800)<<10 + (low - 0xDC00)
						i += 6
						sb.WriteRune(rune(cp))
						continue
					}
				}
				if (cp >= 0xD800 && cp <= 0xDFFF) || cp > 0x10FFFF {
					sb.WriteRune(0xFFFD)
				} else {
					sb.WriteRune(rune(cp))
				}
			default:
				return "", fmt.Errorf("invalid escape \\%c", e)
			}
			continue
		}
		if c < 0x20 {
			return "", fmt.Errorf("unescaped control character")
		}
		_, size := utf8.DecodeRune(data[i:])
		sb.Write(data[i : i+size])
		i += size
	}
}

func parseJSONNumber(data []byte, pos *int) (*jValue, error) {
	i := *pos
	if data[i] == '-' {
		i++
	}
	hadDot, hadExp := false, false
	for i < len(data) && data[i] >= '0' && data[i] <= '9' {
		i++
	}
	if i < len(data) && data[i] == '.' {
		hadDot = true
		i++
		for i < len(data) && data[i] >= '0' && data[i] <= '9' {
			i++
		}
	}
	if i < len(data) && (data[i] == 'e' || data[i] == 'E') {
		hadExp = true
		i++
		if i < len(data) && (data[i] == '+' || data[i] == '-') {
			i++
		}
		for i < len(data) && data[i] >= '0' && data[i] <= '9' {
			i++
		}
	}
	tok := string(data[*pos:i])
	if tok == "" || tok == "-" {
		return nil, fmt.Errorf("invalid number %q", tok)
	}
	if !hadDot && !hadExp {
		if n, err := strconv.ParseInt(tok, 10, 64); err == nil {
			*pos = i
			return &jValue{kind: jInt, i: n}, nil
		}
	}
	f, err := strconv.ParseFloat(tok, 64)
	if err != nil {
		return nil, fmt.Errorf("invalid number %q", tok)
	}
	*pos = i
	return &jValue{kind: jFloat, f: f}, nil
}

// iterJSONObjects mirrors _iter_json_objects: yield every top-level JSON
// value in a (possibly concatenated) body. Stops at the first parse error.
func iterJSONObjects(text string) []*jValue {
	data := []byte(text)
	var out []*jValue
	i := 0
	for i < len(data) {
		for i < len(data) && (data[i] == ' ' || data[i] == '\n' || data[i] == '\t' || data[i] == '\r') {
			i++
		}
		if i >= len(data) {
			break
		}
		v, err := parseJSON(data, &i)
		if err != nil {
			break
		}
		out = append(out, v)
	}
	return out
}

// --- story extraction -----------------------------------------------------

// graphqlStoryNodes mirrors _graphql_story_nodes.
func graphqlStoryNodes(payload *jValue) []*jValue {
	if !payload.isObj() {
		return nil
	}
	data := payload.Get("data")
	if !data.isObj() {
		return nil
	}
	node := data.Get("node")
	var found []*jValue
	if node.isObj() {
		if node.Get("__typename").Str() == "Story" && node.Get("post_id").Truthy() {
			found = append(found, node)
		}
		tl := node.Get("timeline_list_feed_units")
		if tl.isObj() {
			for _, edge := range orDefault(tl.Get("edges"), emptyArr).arr {
				child := edge.Get("node")
				if child.isObj() && child.Get("post_id").Truthy() {
					found = append(found, child)
				}
			}
		}
	}
	return found
}

// graphqlStoryToPost mirrors _graphql_story_to_post.
func graphqlStoryToPost(story *jValue, pageURL string) *worker.ParsedPost {
	postID := pyTrim(jStrAny(story.Get("post_id")))
	if postID == "" {
		return nil
	}

	var publishedAt *time.Time
	ct := story.Get("creation_time")
	if ct.isNum() {
		sec := int64(ct.numFloat()) // Python int(ct) truncates toward zero
		dt := time.Unix(sec, 0).UTC()
		if dt.Year() < 1 || dt.Year() > 9999 {
			return nil // Python OverflowError -> this story is skipped
		}
		publishedAt = &dt
	}
	var publishedRaw string
	if ct.isNum() {
		publishedRaw = strconv.FormatInt(int64(ct.numFloat()), 10)
	}

	postURL := ""
	if permalink := story.Get("permalink_url"); permalink.Truthy() {
		absURL := urljoin(pageURL, jStrAny(permalink))
		if strings.HasPrefix(absURL, "http") {
			postURL = absURL
		}
	}

	var textVal string
	storyRender := story
	if cs := story.Get("comet_sections"); cs.isObj() {
		content := cs.Get("content")
		if inner := content.Get("story"); content.isObj() && inner.isObj() {
			storyRender = inner
		}
	}
	if msg := storyRender.Get("message"); msg.isObj() {
		if t := msg.Get("text"); t.Truthy() {
			textVal = t.Str()
		}
	} else if msg.IsStr() {
		if s := msg.Str(); pyTrim(s) != "" {
			textVal = s
		}
	}
	if textVal == "" {
		if alt := storyRender.Get("text"); alt.IsStr() {
			if s := alt.Str(); pyTrim(s) != "" {
				textVal = s
			}
		}
	}

	// feedback counts: both nodes are visited unconditionally (first non-nil
	// result per metric survives; both nodes start from 0).
	reactions := int64(0)
	commentsCount := int64(0)
	shares := int64(0)
	for _, node := range []*jValue{story, storyRender} {
		fb := node.Get("feedback")
		if !fb.isObj() {
			continue
		}
		if rc := graphqlGrabInt(fb, "reaction_count"); rc != nil {
			reactions = *rc
		}
		cc := graphqlGrabInt(fb, "comment_total_count")
		if cc == nil {
			cc = graphqlGrabInt(fb, "comment_widget_total_comment_count")
		}
		if cc != nil {
			commentsCount = *cc
		}
		if sc := graphqlGrabInt(fb, "share_count"); sc != nil {
			shares = *sc
		}
	}

	// deep-count fallbacks: first hit in document order (ordered keys!)
	if reactions == 0 {
		if hits := deepCounts(story, "reaction_count"); len(hits) > 0 {
			reactions = hits[0]
		}
	}
	if commentsCount == 0 {
		cc := deepCounts(story, "comment_total_count")
		if len(cc) == 0 {
			cc = deepCounts(story, "comment_widget_total_comment_count")
		}
		if len(cc) == 0 {
			cc = deepTotalCommentCounts(story)
		}
		if len(cc) > 0 {
			commentsCount = cc[0]
		}
	}
	if shares == 0 {
		if hits := deepCounts(story, "share_count"); len(hits) > 0 {
			shares = hits[0]
		}
	}
	likes := reactions

	// media attachments
	hasImage, hasVideo := false, false
	var thumbnailURL, mediaURL, videoURL string
	for _, att := range orDefault(story.Get("attachments"), emptyArr).arr {
		if !att.isObj() {
			continue
		}
		media := att.Get("media")
		if !media.isObj() {
			continue
		}
		typename := media.Get("__typename").Str()
		img := orDefault(orDefault(media.Get("image"), media.Get("target_image")), emptyObj)
		uri := pickURI(img, media.Get("uri"))
		if uri != "" {
			if strings.Contains(typename, "Video") || media.Get("playable_url").Truthy() {
				hasVideo = true
				videoURL = orStr(media.Get("playable_url"), uri)
				thumbnailURL = uri
			} else {
				hasImage = true
				if thumbnailURL == "" {
					thumbnailURL = uri
				}
				if mediaURL == "" {
					mediaURL = uri
				}
			}
		}
		if uri == "" && media.Get("image").isObj() {
			uri2 := pickURI(media.Get("image").Get("uri"))
			if uri2 != "" {
				hasImage = true
				if thumbnailURL == "" {
					thumbnailURL = uri2
				}
			}
		}

		// Comet newer shape: the real renderer sits under
		// attachments[].styles.attachment.media
		renderer := att.Get("styles")
		if uri == "" && renderer.isObj() {
			nested := renderer.Get("attachment")
			if nested.isObj() {
				// albums: styles.attachment.all_subattachments.nodes[].media
				for _, sub := range orDefault(orDefault(nested.Get("all_subattachments"), emptyObj).Get("nodes"), emptyArr).arr {
					smedia := sub.Get("media")
					if !smedia.isObj() {
						continue
					}
					simg := orDefault(orDefault(orDefault(
						orDefault(smedia.Get("image"), smedia.Get("target_image")),
						smedia.Get("photo_image")), smedia.Get("viewer_image")), emptyObj)
					suri := pickURI(simg, smedia.Get("uri"), smedia.Get("first_frame_thumbnail"))
					if suri == "" {
						continue
					}
					if strings.Contains(smedia.Get("__typename").Str(), "Video") {
						hasVideo = true
						if videoURL == "" {
							videoURL = pickURI(smedia.Get("playable_url"), smedia.Get("url"))
						}
						if thumbnailURL == "" {
							thumbnailURL = suri
						}
					} else {
						hasImage = true
						if thumbnailURL == "" {
							thumbnailURL = suri
						}
						if mediaURL == "" {
							mediaURL = suri
						}
					}
				}
				nmedia := nested.Get("media")
				if nmedia.isObj() {
					ntyp := nmedia.Get("__typename").Str()
					if ntyp == "" {
						ntyp = typename
					}
					nimg := orDefault(orDefault(orDefault(orDefault(
						orDefault(nmedia.Get("image"), nmedia.Get("target_image")),
						nmedia.Get("preferred_thumbnail")), nmedia.Get("photo_image")),
						nmedia.Get("viewer_image")), emptyObj)
					if nimg.isObj() && !nimg.Get("uri").Truthy() {
						nimg = orDefault(nimg.Get("image"), emptyObj)
					}
					nuri := pickURI(nimg, nmedia.Get("uri"), nmedia.Get("first_frame_thumbnail"))
					if nuri != "" && strings.Contains(ntyp, "Video") {
						hasVideo = true
						if videoURL == "" {
							videoURL = pickURI(nmedia.Get("playable_url"), nmedia.Get("url"))
						}
						if thumbnailURL == "" {
							thumbnailURL = nuri
						}
					} else if nuri != "" {
						hasImage = true
						if thumbnailURL == "" {
							thumbnailURL = nuri
						}
						if mediaURL == "" {
							mediaURL = nuri
						}
					}
				}
			}
		}
	}

	// mentions and external links from the decoded text (not deduped)
	var external, mentions []string
	for _, m := range scriptExternalLinkRe.FindAllStringSubmatch(textVal, -1) {
		if !strings.Contains(strings.ToLower(m[1]), "facebook.com") {
			external = append(external, m[1])
		}
	}
	for _, m := range scriptMentionRe.FindAllStringSubmatch(textVal, -1) {
		mentions = append(mentions, m[1])
	}

	return &worker.ParsedPost{
		PostID:         nz(postID),
		PostURL:        nz(postURL),
		Text:           nz(textVal),
		PublishedAt:    publishedAt,
		PublishedAtRaw: nz(publishedRaw),
		Reactions:      &reactions,
		Likes:          &likes,
		CommentsCount:  &commentsCount,
		Shares:         &shares,
		HasImage:       hasImage,
		HasVideo:       hasVideo,
		HasLinkPreview: false,
		ThumbnailURL:   nz(thumbnailURL),
		MediaURL:       nz(mediaURL),
		VideoURL:       nz(videoURL),
		ExternalLinks:  external,
		Mentions:       mentions,
	}
}

// graphqlGrabInt mirrors the inline _grab_int helper.
func graphqlGrabInt(fb *jValue, key string) *int64 {
	v := fb.Get(key)
	if v.isObj() {
		if c := v.Get("count"); c.isNum() {
			r := c.numInt()
			return &r
		}
	}
	if v == nil {
		v = fb.Get(strings.ReplaceAll(key, "_count", ""))
	}
	if v.isObj() {
		if tc := v.Get("total_count"); tc.isNum() {
			r := tc.numInt()
			return &r
		}
	}
	if v.isNum() {
		r := v.numInt()
		return &r
	}
	return nil
}

// deepCounts mirrors _deep_counts: every node whose key starts with prefix
// and whose value has a numeric "count", in document order.
func deepCounts(v *jValue, prefix string) []int64 {
	var hits []int64
	if v.isObj() {
		for _, k := range v.keys {
			child := v.Get(k)
			if strings.HasPrefix(k, prefix) && child.isObj() {
				if c := child.Get("count"); c.isNum() {
					hits = append(hits, c.numInt())
				}
			}
			hits = append(hits, deepCounts(child, prefix)...)
		}
	} else if v != nil && v.kind == jArray {
		for _, item := range v.arr {
			hits = append(hits, deepCounts(item, prefix)...)
		}
	}
	return hits
}

// deepTotalCommentCounts mirrors _deep_total_comment_counts: every
// {"comments": {"total_count": N}} node, in document order.
func deepTotalCommentCounts(v *jValue) []int64 {
	var hits []int64
	if v.isObj() {
		for _, k := range v.keys {
			child := v.Get(k)
			if k == "comments" && child.isObj() {
				if tc := child.Get("total_count"); tc.isNum() {
					hits = append(hits, tc.numInt())
				}
			}
			hits = append(hits, deepTotalCommentCounts(child)...)
		}
	} else if v != nil && v.kind == jArray {
		for _, item := range v.arr {
			hits = append(hits, deepTotalCommentCounts(item)...)
		}
	}
	return hits
}

// ExtractPostsFromGraphQL mirrors extract_posts_from_graphql.
func ExtractPostsFromGraphQL(html, pageURL string) []*worker.ParsedPost {
	if html == "" || !strings.Contains(html, `"post_id"`) {
		return nil
	}
	doc, _ := goquery.NewDocumentFromReader(strings.NewReader(html))
	var posts []*worker.ParsedPost
	seen := map[string]bool{}
	doc.Find("script[data-fb-graphql-feed]").Each(func(_ int, sel *goquery.Selection) {
		raw := sel.Text()
		if raw == "" || !strings.Contains(raw, `"post_id"`) {
			return
		}
		for _, obj := range iterJSONObjects(raw) {
			for _, story := range graphqlStoryNodes(obj) {
				pid := jStrAny(story.Get("post_id"))
				if pid != "" {
					if seen[pid] {
						continue
					}
					seen[pid] = true
				}
				post := graphqlStoryToPost(story, pageURL)
				if post != nil && (post.PostID != nil || post.Text != nil) {
					posts = append(posts, post)
				}
			}
		}
	})
	return posts
}
