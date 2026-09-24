// Package parser is the Go mirror of backend/scraper/parser.py — the
// public-HTML parser for Facebook pages (finalplanv2.md §5 "Parse").
//
// Single source of truth: the Python file.  Every exposed function ports a
// Python function 1:1, including its documented best-effort limits (fields
// not present in the public markup stay nil — never fabricated).  Golden
// bytes in testdata/ were produced by running the REAL Python
// (testdata/gen_goldens.py); *_test.go asserts this package byte-for-byte
// against them.  Nothing here is asserted from belief.
//
// Honesty rules carried over from parser.py:
//   - a field that is not present in the public markup stays nil
//   - per-reaction breakdowns are typically NOT rendered publicly -> nil
//   - raw .mp4 video_url is typically unavailable on public HTML -> nil
//   - naive (timezone-less) timestamp strings are stored as UTC
//
// HTML parsing uses goquery (the plan's §5 pin).  The GraphQL-feed path is
// regex/JSON-only and stdlib.
package parser

import (
	"fmt"
	"regexp"
	"strings"
	"time"

	"github.com/PuerkitoBio/goquery"

	worker "postharvest/golang"
)

// ParsedPage mirrors parser.py ParsedPage. nil-able values are "" in Go;
// Posts is nilable-len (0 == none) so JSON serializes to [] and null edge
// cases stay comparable with the Python golden dumps.
type ParsedPage struct {
	PageName   string
	PageID     string
	ProfileURL string
	OGImage    string
	Posts      []*worker.ParsedPost
	PostErrors []PostError
	FetchedURL string
}

// PostError mirrors one entry of parser.py ParsedPage.post_errors.
type PostError struct {
	PostURL string `json:"post_url"`
	Code    string `json:"code"`
	Message string `json:"message"`
}

// ParsePage mirrors parser.py parse_page(html, page_url, now=None, handle=None).
func ParsePage(html, pageURL string, now time.Time, handle string) ParsedPage {
	if html == "" || strings.TrimSpace(html) == "" {
		return ParsedPage{PageName: handle, FetchedURL: pageURL}
	}
	// lxml never fails to parse HTML; net/html always returns a (possibly
	// partial) tree plus a non-nil doc, so the returned doc is used even
	// when err mentions "unexpected EOF". Mirror the Python behaviour.
	doc, _ := goquery.NewDocumentFromReader(strings.NewReader(html))

	ogTitle := cleanName(metaContent(doc, "property", "og:title", "name", "og:title"))
	ogImage := metaContent(doc, "property", "og:image", "name", "og:image")
	ogURL := metaContent(doc, "property", "og:url", "name", "og:url")

	var title string
	if t := doc.Find("title").First(); len(t.Nodes) > 0 {
		title = cleanName(getText(t, "", true))
	}

	// page_name: og:title > <title> > header profile link > handle
	pageName := ogTitle
	if pageName == "" {
		pageName = title
	}
	if pageName == "" {
		headerLink := doc.Find("h3 a[href], header a[href]").First()
		if len(headerLink.Nodes) > 0 {
			pageName = cleanName(getText(headerLink, " ", true))
		}
		if pageName == "" {
			pageName = handle
		}
	}

	profileURL := ogURL
	if profileURL == "" {
		profileURL = pageURL
	}
	pageID := pageIDFromURL(profileURL)
	if pageID == "" {
		pageID = pageIDFromURL(pageURL)
	}

	var posts []*worker.ParsedPost
	var postErrors []PostError

	roots := FindPostRoots(doc)
	if len(roots) > 0 {
		for _, root := range roots {
			post, err := parsePostRoot(root, pageURL, now)
			if err != nil {
				postErrors = append(postErrors, PostError{
					PostURL: "",
					Code:    "extraction_failure",
					Message: fmt.Sprintf("failed to parse a post container: %v", err),
				})
				continue
			}
			posts = append(posts, post)
		}
	} else {
		// Fallback: extract posts from embedded JSON in <script> tags.
		got, err := extractPostsFromScripts(html, pageURL, now)
		if err != nil {
			postErrors = append(postErrors, PostError{
				PostURL: "",
				Code:    "extraction_failure",
				Message: fmt.Sprintf("script-based extraction failed: %v", err),
			})
		} else {
			posts = got
		}
	}

	return ParsedPage{
		PageName:   pageName,
		PageID:     pageID,
		ProfileURL: profileURL,
		OGImage:    ogImage,
		Posts:      posts,
		PostErrors: postErrors,
		FetchedURL: pageURL,
	}
}

// metaContent mirrors _meta_content: read a <meta> tag by any of several
// (key, value) attribute pairs; first pair match wins.
func metaContent(doc *goquery.Document, pairs ...string) string {
	for i := 0; i+1 < len(pairs); i += 2 {
		key, value := pairs[i], pairs[i+1]
		var match *goquery.Selection
		doc.Find("meta").EachWithBreak(func(_ int, s *goquery.Selection) bool {
			if v, ok := s.Attr(key); ok && v == value {
				match = s
				return false
			}
			return true
		})
		if match == nil {
			continue
		}
		content, ok := match.Attr("content")
		if ok && content != "" {
			return strings.TrimSpace(content)
		}
	}
	return ""
}

var cleanNameRe = regexp.MustCompile(`\s+[|\-–]\s+Facebook\s*$`)

// cleanName mirrors _clean_name: strip Facebook chrome from page titles.
func cleanName(raw string) string {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return ""
	}
	name := collapse(raw)
	name = cleanNameRe.ReplaceAllString(name, "")
	name = strings.TrimSpace(name)
	if name == "" {
		return ""
	}
	return name
}

var profilePhpRe = regexp.MustCompile(`/profile\.php\?[^"'\s]*id=(\d+)`)
var peopleRe = regexp.MustCompile(`/people/[^"'\s/]+/(\d+)`)

// pageIDFromURL mirrors _page_id_from_url.
func pageIDFromURL(url string) string {
	if m := profilePhpRe.FindStringSubmatch(url); m != nil {
		return m[1]
	}
	if m := peopleRe.FindStringSubmatch(url); m != nil {
		return m[1]
	}
	return ""
}

// collapse mirrors " ".join(str(raw).split()) over Python whitespace.
func collapse(s string) string {
	parts := pySplit(s)
	return strings.Join(parts, " ")
}
