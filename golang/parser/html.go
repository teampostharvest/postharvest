package parser

// DOM-based per-post extraction — direct mirrors of parser.py
// "Per-post extraction" (find_post_roots, _parse_post_root and helpers).

import (
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/PuerkitoBio/goquery"
	"golang.org/x/net/html"

	worker "postharvest/golang"
)

// postIDPatterns mirrors _POST_ID_PATTERNS (priority order).  The first
// capture group is the numeric post id.
var postIDPatterns = []*regexp.Regexp{
	regexp.MustCompile(`/story\.php\?[^"'\s]*story_fbid=(\d+)`),
	regexp.MustCompile(`/permalink\.php\?[^"'\s]*story_fbid=(\d+)`),
	regexp.MustCompile(`/photo\.php\?[^"'\s]*fbid=(\d+)`),
	regexp.MustCompile(`/watch/\?[^"'\s]*v=(\d+)`),
	regexp.MustCompile(`/reel/(\d+)`),
	regexp.MustCompile(`/posts/(\d+)`),
	regexp.MustCompile(`/photos/(?:[^/"'\s]*/)*?(\d+)(?:[?/]|$)`),
	regexp.MustCompile(`/videos/(?:[^/"'\s]*/)*?(\d+)(?:[?/]|$)`),
}

// linkCardSelectors mirrors _LINK_CARD_SELECTORS.
var linkCardSelectors = []string{
	"div[data-testid='story-attachment']",
	"a[class*='oembed']",
	"div[class*='share_wrapper']",
	"div[class*='shareWrapper']",
	"div[class*='attachment']",
}

// ignoredAnchorLabels mirrors _IGNORED_ANCHOR_LABELS.
var ignoredAnchorLabels = map[string]bool{
	"see more": true, "see more...": true, "read more": true,
	"continue reading": true, "full story": true, "read full story": true,
	"more": true, "translate": true, "see translation": true,
	"view more": true, "show more": true, "see all": true,
	"learn more": true, "details": true,
}

// nodeSel wraps a single html node as a Selection. goquery has no exported
// single-node constructor; a Selection with just Nodes set is enough for the
// attribute/text/remove operations this package uses (document lookups are
// never needed on these ephemeral selections).
func nodeSel(node *html.Node) *goquery.Selection {
	return &goquery.Selection{Nodes: []*html.Node{node}}
}

// FindPostRoots mirrors parser.py find_post_roots(soup): locate post
// container elements tolerantly, dropping nested candidates.
func FindPostRoots(doc *goquery.Document) []*goquery.Selection {
	var candidates []*goquery.Selection

	// 1. <article> tags
	if articles := doc.Find("article"); len(articles.Nodes) > 0 {
		candidates = selList(articles)
	} else if roleArticles := doc.Find("[role='article']"); len(roleArticles.Nodes) > 0 {
		// 2. Modern Facebook: div[role="article"]
		candidates = selList(roleArticles)
	} else if adPreview := doc.Find("[data-ad-preview='message']"); len(adPreview.Nodes) > 0 {
		// 3. data-ad-preview marks the post text container; walk up to the
		// nearest post-level container (mirrors the Python for/else exactly:
		// the fallback fires ONLY when the 8-step walk completes without
		// any break — including the parent-is-root break).
		adPreview.Each(func(_ int, tag *goquery.Selection) {
			parent := tag.Parent()
			broke := false
			for i := 0; i < 8; i++ {
				if len(parent.Nodes) == 0 || parent.Get(0).Data == "" {
					broke = true
					break
				}
				if parent.AttrOr("role", "") == "article" || parent.Get(0).Data == "article" {
					candidates = append(candidates, parent)
					broke = true
					break
				}
				parent = parent.Parent()
			}
			if !broke {
				candidates = append(candidates, tag)
			}
		})
	} else if dataFt := doc.Find("[data-ft]"); len(dataFt.Nodes) > 0 {
		// 4. data-ft (mbasic / older www)
		candidates = selList(dataFt)
	} else {
		// 5. Legacy class-based fallback
		classyRe := regexp.MustCompile(`(?i)(story_body_container|userContent|fbUserPost|story)`)
		doc.Find("div").Each(func(_ int, s *goquery.Selection) {
			if cls, ok := s.Attr("class"); ok && classyRe.MatchString(cls) &&
				pyRuneLen(getText(s, "", true)) > 10 {
				candidates = append(candidates, s)
			}
		})
	}

	// drop elements nested inside another candidate (same order preserved)
	var result []*goquery.Selection
	for _, tag := range candidates {
		inside := false
		for _, anc := range result {
			if isAncestor(anc, tag) {
				inside = true
				break
			}
		}
		if !inside {
			result = append(result, tag)
		}
	}
	return result
}

func selList(s *goquery.Selection) []*goquery.Selection {
	out := make([]*goquery.Selection, 0, len(s.Nodes))
	s.Each(func(_ int, sel *goquery.Selection) { out = append(out, sel) })
	return out
}

// isAncestor mirrors _is_ancestor: is `ancestor` an ancestor of `tag`?
func isAncestor(ancestor, tag *goquery.Selection) bool {
	for i := 0; i < 64; i++ {
		if len(tag.Nodes) == 0 {
			return false
		}
		parent := tag.Parent()
		if len(parent.Nodes) == 0 {
			return false
		}
		if parent.Get(0) == ancestor.Get(0) {
			return true
		}
		tag = parent
	}
	return false
}

// cleanHref mirrors _clean_href: "&amp;" -> "&" and strip.
func cleanHref(href string) string {
	return strings.TrimSpace(strings.ReplaceAll(href, "&amp;", "&"))
}

// urljoin mirrors url.urljoin(base, ref) for the http(s) URLs this parser
// resolves. Rules rarely diverge for absolute refs.
func urljoin(base, ref string) string {
	b, err := url.Parse(base)
	if err != nil {
		return ref
	}
	r, err := url.Parse(ref)
	if err != nil {
		return ref
	}
	return b.ResolveReference(r).String()
}

// postURLAndID mirrors _post_url_and_id (first match wins).
func postURLAndID(root *goquery.Selection, pageURL string) (string, string) {
	if dataHref, ok := root.Attr("data-href"); ok && dataHref != "" {
		for _, pat := range postIDPatterns {
			if m := pat.FindStringSubmatch(dataHref); m != nil {
				return urljoin(pageURL, dataHref), m[1]
			}
		}
	}
	for _, node := range root.Find("a[href]").Nodes {
		sel := nodeSel(node)
		href, ok := sel.Attr("href")
		if !ok {
			continue
		}
		href = cleanHref(href)
		for _, pat := range postIDPatterns {
			if m := pat.FindStringSubmatch(href); m != nil {
				return urljoin(pageURL, href), m[1]
			}
		}
	}
	return "", ""
}

// postText mirrors _post_text: strip scripts/headers/ignored control anchors,
// then join the remaining visible text.
func postText(root *goquery.Selection) *string {
	root.Find("script,style,noscript").Remove()
	root.Find("h3,h4,header").Remove()

	var anchorRe = regexp.MustCompile(`/(comment|share|like|reaction|react|report)`)
	// snapshot anchors before removal (mirrors list(root.find_all("a")))
	anchors := append([]*goquery.Selection(nil), selList(root.Find("a"))...)
	for _, a := range anchors {
		label := strings.ToLower(collapse(getText(a, " ", true)))
		if ignoredAnchorLabels[label] {
			a.Remove()
			continue
		}
		href := strings.ToLower(a.AttrOr("href", ""))
		if anchorRe.MatchString(href) && pyRuneLen(getText(a, "", true)) < 40 {
			a.Remove()
		}
	}

	text := getText(root, " ", true)
	if text == "" {
		return nil
	}
	return &text
}

var timeMonthRe = regexp.MustCompile(`(?i)\b(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)\b`)

// postTime mirrors _post_time.  Returns (published, publishedRaw).
func postTime(root *goquery.Selection, now time.Time) (*time.Time, *string) {
	// 1. data-utime epoch (exact) — on any element, not just abbr
	for _, node := range root.Find("[data-utime]").Nodes {
		el := nodeSel(node)
		utime := el.AttrOr("data-utime", "")
		if utime == "" || !epochRe.MatchString(utime) {
			continue
		}
		if dt := fromTimestampFloat(utime); dt != nil {
			raw := getText(el, "", true)
			if raw == "" {
				return dt, nil
			}
			return dt, &raw
		}
	}

	// 2. abbr text
	if dt, raw := scanTime(root.Find("abbr"), now); dt != nil {
		return dt, raw
	}
	// 3. span.timestamp / modern timestamp classes
	if dt, raw := scanTime(root.Find("span.timestamp, span[class*='timestamp']"), now); dt != nil {
		return dt, raw
	}
	// 4. Modern Facebook: aria-label time on anchor/div/span
	for _, node := range root.Find("a[aria-label], span[aria-label], div[aria-label]").Nodes {
		el := nodeSel(node)
		label := pyTrim(el.AttrOr("aria-label", ""))
		if label != "" && timeMonthRe.MatchString(label) {
			if dt := ParseTimestamp(label, now); dt != nil {
				return dt, &label
			}
		}
	}

	// 5. strict text scan: month names / year / relative units / "ago"
	strict := regexp.MustCompile(`(?i)\b(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec|20\d{2}|\d{1,2}\s+ago|ago|yesterday|just now|now|\d{1,3}\s*[smhdw]\s*(ago)?)\b`)
	shortTokRe := regexp.MustCompile(`(?i)^\d{1,3}\s*[smhdw]\s*(ago)?$`)
	monthTokRe := regexp.MustCompile(`(?i)^(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*`)
	text := getText(root, " ", true)
	// Python slices this text by character offsets while Go regexes report
	// byte offsets — convert each match so multi-byte text stays byte-true.
	runes := []rune(text)
	for _, m := range strict.FindAllStringIndex(text, -1) {
		token := text[m[0]:m[1]]
		if m2 := shortTokRe.FindStringSubmatch(strings.TrimSpace(token)); m2 != nil {
			if dt := ParseTimestamp(token, now); dt != nil {
				return dt, strPtr(strings.TrimSpace(token))
			}
		}
		charStart := utf8.RuneCountInString(text[:m[0]])
		if m3 := monthTokRe.FindString(token); m3 != "" {
			// absolute dates ("31 August at 21:29"): parse_timestamp needs a
			// fullmatch, so wide snippets clip at the real date boundary by
			// trying shrinking prefixes of the date region.
			rs := charStart - 8
			if rs < 0 {
				rs = 0
			}
			re := charStart + 36
			if re > len(runes) {
				re = len(runes)
			}
			region := runes[rs:re]
			for end := len(region); end > 6; end-- {
				piece := pyTrim(string(region[:end]))
				if dt := ParseTimestamp(piece, now); dt != nil {
					return dt, strPtr(piece)
				}
			}
		}
		ss := charStart - 12
		if ss < 0 {
			ss = 0
		}
		charEnd := utf8.RuneCountInString(text[:m[1]])
		se := charEnd + 24
		if se > len(runes) {
			se = len(runes)
		}
		snippet := strings.TrimSpace(string(runes[ss:se]))
		if dt := ParseTimestamp(snippet, now); dt != nil {
			return dt, strPtr(snippet)
		}
	}
	return nil, nil
}

// scanTime parses the first element whose text yields a timestamp.
func scanTime(sels *goquery.Selection, now time.Time) (*time.Time, *string) {
	for _, node := range sels.Nodes {
		el := nodeSel(node)
		label := collapse(getText(el, " ", true))
		if label == "" {
			continue
		}
		if dt := ParseTimestamp(label, now); dt != nil {
			return dt, &label
		}
	}
	return nil, nil
}

// isInLinkCard mirrors _is_in_link_card.
func isInLinkCard(img *goquery.Selection) bool {
	clsRe := regexp.MustCompile(`(?i)(share|attachment|oembed)`)
	for n := img.Get(0); n != nil; n = n.Parent {
		if n.Parent == nil || n.Parent.Type != html.ElementNode {
			continue
		}
		parent := n.Parent
		var cls string
		for _, a := range parent.Attr {
			if a.Key == "class" {
				cls = a.Val
			}
		}
		if clsRe.MatchString(cls) {
			return true
		}
		if parent.Data == "h3" || parent.Data == "header" {
			return true
		}
	}
	return false
}

// imgSrc mirrors _img_src: explicit src when real, else data-src.
func imgSrc(img *goquery.Selection) string {
	src := strings.TrimSpace(img.AttrOr("src", ""))
	if src == "" || (strings.Contains(src, "/rsrc.php/") && strings.HasSuffix(src, ".png")) {
		src = strings.TrimSpace(img.AttrOr("data-src", ""))
	}
	return src
}

// dim mirrors _dim.
func dim(img *goquery.Selection, attr string) (int, bool) {
	v, ok := img.Attr(attr)
	if !ok {
		return 0, false
	}
	n, err := strconv.Atoi(v)
	if err != nil {
		return 0, false
	}
	return n, true
}

var iconEmojiRe = regexp.MustCompile(`/(icons?|emojis?)/|emoji`)
var avatarRe = regexp.MustCompile(`(avatar|profilepic|profile_pic)`)

// contentImage is one candidate image from iterContentImages.
type contentImage struct {
	img  *goquery.Selection
	src  string
	area int
}

// iterContentImages mirrors _iter_content_images.
func iterContentImages(root *goquery.Selection) []contentImage {
	var out []contentImage
	root.Find("img").Each(func(_ int, img *goquery.Selection) {
		src := imgSrc(img)
		if src == "" {
			return
		}
		low := strings.ToLower(src)
		if iconEmojiRe.MatchString(low) {
			return
		}
		if avatarRe.MatchString(low) {
			return
		}
		w, wok := dim(img, "width")
		h, hok := dim(img, "height")
		// area = (w or 0) * (h or 0)
		var area int
		if wok {
			area = w
		}
		if hok {
			area *= h
		}
		if wok && hok && area < 60*60 {
			return
		}
		if isInLinkCard(img) {
			return
		}
		out = append(out, contentImage{img: img, src: src, area: area})
	})
	return out
}

var videoURLRe = regexp.MustCompile(`/(videos?|reel|watch)/`)
var videoIDRe = regexp.MustCompile(`[?&](?:v|video_id|fbid)=\d+`)

var mimeVideoProps = map[string]bool{
	"og:video": true, "og:video:url": true, "twitter:player:stream": true,
}

// postMedia mirrors _post_media.
func postMedia(root *goquery.Selection, pageURL string) map[string]interface{} {
	out := map[string]interface{}{
		"has_image":        false,
		"has_video":        false,
		"has_link_preview": false,
		"thumbnail_url":    nil,
		"media_url":        nil,
		"video_url":        nil,
	}

	// video?  (anchors to /videos/|/reel/|/watch/ or a <video> element)
	for _, node := range root.Find("a[href]").Nodes {
		href := cleanHref(nodeSel(node).AttrOr("href", ""))
		if videoURLRe.MatchString(href) || videoIDRe.MatchString(href) {
			out["has_video"] = true
			break
		}
	}
	if videos := root.Find("video"); len(videos.Nodes) > 0 {
		out["has_video"] = true
		for _, node := range videos.Nodes {
			src := nodeSel(node).AttrOr("src", "")
			if src == "" {
				continue
			}
			abs := urljoin(pageURL, src)
			if strings.HasPrefix(abs, "http://") || strings.HasPrefix(abs, "https://") {
				out["video_url"] = abs
				break
			}
		}
	}

	// direct mp4 metadata (rare on public HTML - usually nil, kept honest)
	if out["video_url"] == nil {
		root.Find("meta").EachWithBreak(func(_ int, meta *goquery.Selection) bool {
			prop := meta.AttrOr("property", "")
			if prop == "" {
				prop = meta.AttrOr("itemprop", "")
			}
			content := meta.AttrOr("content", "")
			if content == "" || !mimeVideoProps[strings.ToLower(prop)] {
				return true
			}
			abs := urljoin(pageURL, content)
			path := strings.Split(abs, "?")[0]
			if (strings.HasPrefix(abs, "http://") || strings.HasPrefix(abs, "https://")) &&
				(strings.HasSuffix(path, ".mp4") || strings.HasSuffix(path, ".m4v") || strings.HasSuffix(path, ".webm")) {
				out["video_url"] = abs
				return false
			}
			return true
		})
	}

	// content images (excludes link-card previews)
	images := iterContentImages(root)
	if len(images) > 0 {
		sort.SliceStable(images, func(i, j int) bool { return images[i].area > images[j].area })
		abs := urljoin(pageURL, images[0].src)
		out["has_image"] = true
		out["media_url"] = abs
		out["thumbnail_url"] = abs
	}

	// link-card preview image (thumbnail for link posts)
	for _, sel := range linkCardSelectors {
		card := root.Find(sel).First()
		if len(card.Nodes) == 0 {
			continue
		}
		out["has_link_preview"] = true
		var src string
		if img := card.Find("img").First(); len(img.Nodes) > 0 {
			src = imgSrc(img)
		}
		if src != "" && out["thumbnail_url"] == nil {
			out["thumbnail_url"] = urljoin(pageURL, src)
		}
		break
	}

	// og:image fallback when the post itself carries no image
	if out["thumbnail_url"] == nil {
		if og := root.Find("meta[property=og:image]").First(); len(og.Nodes) > 0 {
			if content := og.AttrOr("content", ""); content != "" {
				out["thumbnail_url"] = urljoin(pageURL, content)
				if !out["has_image"].(bool) {
					out["has_image"] = true
				}
			}
		}
	}

	return out
}

// reactionKeys mirrors _REACTION_KEYS.
var reactionKeys = map[string]string{
	"like": "reaction_like_count", "love": "reaction_love_count",
	"care": "reaction_care_count", "haha": "reaction_haha_count",
	"wow": "reaction_wow_count", "sad": "reaction_sad_count",
	"angry": "reaction_angry_count",
}

var shareCountRe = regexp.MustCompile(`(?i)(\d[\d.,]*\s*[km]?)\s*shares?\b`)

// postEngagement mirrors _post_engagement.
func postEngagement(root *goquery.Selection) map[string]*int64 {
	text := getText(root, " ", true)
	counts := map[string]*int64{
		"likes": nil, "reactions": nil, "comments_count": nil,
		"shares": nil, "views_count": nil,
		"reaction_like_count": nil, "reaction_love_count": nil,
		"reaction_care_count": nil, "reaction_haha_count": nil,
		"reaction_wow_count": nil, "reaction_sad_count": nil,
		"reaction_angry_count": nil,
	}

	counts["comments_count"] = countNear(text, []string{"comment"})
	// "X shares" — precise pattern so "Shared with Public 13m" doesn't read
	// the relative time (13m) as 13,000,000 shares.
	if m := shareCountRe.FindStringSubmatch(text); m != nil {
		counts["shares"] = ParseCount(m[1])
	}
	counts["views_count"] = countNear(text, []string{"view"})
	counts["reactions"] = countNear(text, []string{"all reactions", "reacted", "reactions"})
	counts["likes"] = countNear(text, []string{"like"})

	// aria-label driven counts (most reliable on modern markup)
	for _, node := range root.Find("[aria-label]").Nodes {
		label := nodeSel(node).AttrOr("aria-label", "")
		count := ParseCount(label)
		if count == nil {
			continue
		}
		low := strings.ToLower(label)
		if strings.Contains(low, "comment") {
			counts["comments_count"] = count
		} else if strings.Contains(low, "share") {
			counts["shares"] = count
		} else if strings.Contains(low, "view") {
			counts["views_count"] = count
		} else if strings.Contains(low, "react") {
			counts["reactions"] = count
		}
		for name, key := range reactionKeys {
			if strings.Contains(low, name) {
				counts[key] = count
				break
			}
		}
	}

	// reaction emoji images with an own count in their parent text
	for _, node := range root.Find("img[alt]").Nodes {
		img := nodeSel(node)
		alt := strings.ToLower(strings.TrimSpace(img.AttrOr("alt", "")))
		key, ok := reactionKeys[alt]
		if !ok {
			continue
		}
		parent := img.Parent()
		// Python quirk: `" ".join(img.parent.get_text(" ", strip=True))`
		// iterates the *characters* of the text, splitting each into its own
		// token, then re-joins with single spaces ("1.2K" -> "1 . 2 K").
		parentText := strings.Join(strings.Split(getText(parent, " ", true), ""), " ")
		if count := ParseCount(parentText); count != nil {
			counts[key] = count
		}
	}

	return counts
}

var fbShimHosts = map[string]bool{
	"l.facebook.com": true, "lm.facebook.com": true, "l.messenger.com": true,
}

// decodeFacebookShim mirrors _decode_facebook_shim: resolve the obvious
// u= parameter of Facebook's link shim (pure URL parsing, not evasion).
func decodeFacebookShim(href string) string {
	parsed, err := url.Parse(href)
	if err != nil {
		return href
	}
	if fbShimHosts[parsed.Hostname()] && strings.HasPrefix(parsed.Path, "/l.php") {
		if target := parsed.Query().Get("u"); target != "" {
			return target
		}
	}
	return href
}

var pageHosts = map[string]bool{
	"facebook.com": true, "www.facebook.com": true, "m.facebook.com": true,
	"mbasic.facebook.com": true, "touch.facebook.com": true,
}

var profileMentionRe = regexp.MustCompile(`/(?:profile\.php\?[^"'\s]*id=\d+|people/[^/]+/\d+)`)
var mentionExtractRe = regexp.MustCompile(`@([A-Za-z0-9_.\-\x{0080}-\x{FFFF}]+)`)

// postLinksAndMentions mirrors _post_links_and_mentions.
func postLinksAndMentions(root *goquery.Selection, text, pageURL string) ([]string, []string) {
	external := make([]string, 0)
	mentions := make([]string, 0)

	for _, node := range root.Find("a[href]").Nodes {
		a := nodeSel(node)
		href, ok := a.Attr("href")
		if !ok {
			continue
		}
		parsed, err := url.Parse(href)
		if err != nil {
			continue
		}
		label := collapse(getText(a, " ", true))
		host := strings.ToLower(parsed.Hostname())
		if (parsed.Scheme == "http" || parsed.Scheme == "https") && host != "" && !pageHosts[host] {
			href = decodeFacebookShim(cleanHref(href))
			abs := urljoin(pageURL, href)
			if strings.HasPrefix(abs, "http://") || strings.HasPrefix(abs, "https://") {
				external = append(external, abs)
			}
		} // other facebook subdomains (host ends .facebook.com): not external
		href2 := cleanHref(a.AttrOr("href", ""))
		if label != "" && pyRuneLen(label) <= 60 && profileMentionRe.MatchString(href2) {
			mentions = append(mentions, label)
		}
	}

	// @handle style mentions from the post text
	for _, m := range mentionExtractRe.FindAllStringSubmatch(text, -1) {
		mentions = append(mentions, m[1])
	}

	return uniqStrings(external), uniqStrings(mentions)
}

func uniqStrings(seq []string) []string {
	seen := make(map[string]bool, len(seq))
	out := make([]string, 0, len(seq))
	for _, s := range seq {
		if seen[s] {
			continue
		}
		seen[s] = true
		out = append(out, s)
	}
	return out
}

// parsePostRoot mirrors _parse_post_root.
func parsePostRoot(root *goquery.Selection, pageURL string, now time.Time) (*worker.ParsedPost, error) {
	postURL, postID := postURLAndID(root, pageURL)
	publishedAt, publishedRaw := postTime(root, now)
	text := postText(root)
	media := postMedia(root, pageURL)
	engagement := postEngagement(root)
	external, mentions := postLinksAndMentions(root, textValue(text), pageURL)

	return &worker.ParsedPost{
		PostID:         nz(postID),
		PostURL:        nz(postURL),
		Text:           text,
		PublishedAt:    publishedAt,
		PublishedAtRaw: publishedRaw,
		Likes:          engagement["likes"],
		Reactions:      engagement["reactions"],
		CommentsCount:  engagement["comments_count"],
		Shares:         engagement["shares"],
		ViewsCount:     engagement["views_count"],
		ReactionLike:   engagement["reaction_like_count"],
		ReactionLove:   engagement["reaction_love_count"],
		ReactionCare:   engagement["reaction_care_count"],
		ReactionHaha:   engagement["reaction_haha_count"],
		ReactionWow:    engagement["reaction_wow_count"],
		ReactionSad:    engagement["reaction_sad_count"],
		ReactionAngry:  engagement["reaction_angry_count"],
		HasImage:       media["has_image"].(bool),
		HasVideo:       media["has_video"].(bool),
		HasLinkPreview: media["has_link_preview"].(bool),
		ThumbnailURL:   mediaStr(media["thumbnail_url"]),
		MediaURL:       mediaStr(media["media_url"]),
		VideoURL:       mediaStr(media["video_url"]),
		ExternalLinks:  external,
		Mentions:       mentions,
	}, nil
}

func nz(s string) *string {
	if s == "" {
		return nil
	}
	return &s
}

func mediaStr(v interface{}) *string {
	if v == nil {
		return nil
	}
	s, ok := v.(string)
	if !ok || s == "" {
		return nil
	}
	return &s
}

func textValue(t *string) string {
	if t == nil {
		return ""
	}
	return *t
}

// getText mirrors BeautifulSoup get_text(separator, strip=True): the
// non-empty, Python-stripped text pieces of every NavigableString in the
// subtree, joined with `sep`.  Empirically (bs4 4.15 + lxml) comment nodes
// and the content of <script>/<style> are excluded from get_text output.
func getText(sel *goquery.Selection, sep string, strip bool) string {
	var pieces []string
	for _, node := range sel.Nodes {
		collectText(node, sep, strip, &pieces)
	}
	return strings.Join(pieces, sep)
}

func collectText(node *html.Node, sep string, strip bool, pieces *[]string) {
	switch node.Type {
	case html.TextNode:
		s := node.Data
		if strip {
			s = pyTrim(s)
		}
		if s != "" {
			*pieces = append(*pieces, s)
		}
	case html.ElementNode:
		if node.Data == "script" || node.Data == "style" {
			return // excluded from get_text output (empirical, bs4 4.15)
		}
		for c := node.FirstChild; c != nil; c = c.NextSibling {
			collectText(c, sep, strip, pieces)
		}
	}
}
