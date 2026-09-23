// Byte-mirror of backend/scraper/dedup.py (single source of truth).
//
// Rules (product spec, mirrored verbatim):
//   - primary key: post_id (numerical FB id); two posts with the same
//     post_id are duplicates;
//   - fallback key: SHA-256 fingerprint of page_id | timestamp | text[:200],
//     stable across re-scrapes, order-independent;
//   - first occurrence wins; later duplicates dropped and counted.
package worker

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"net/url"
	"strings"
)

const textFingerprintLen = 200

func stringify(v interface{}) string {
	switch t := v.(type) {
	case nil:
		return ""
	case *string:
		if t == nil {
			return ""
		}
		return *t
	case string:
		return t
	case *int64:
		if t == nil {
			return ""
		}
		return fmt.Sprintf("%d", *t)
	case int64:
		return fmt.Sprintf("%d", t)
	case float64:
		return fmt.Sprintf("%v", t)
	default:
		return fmt.Sprint(v)
	}
}

// NormalizePostURL mirrors dedup.normalize_post_url: strips the documented
// tracking params, normalizes trailing slash / double slashes.
func NormalizePostURL(raw *string) *string {
	if raw == nil || *raw == "" {
		return nil
	}
	u, err := url.Parse(*raw)
	if err != nil {
		return raw
	}
	tracked := map[string]bool{
		"fbclid": true, "ref": true, "source": true,
		" medium": true, "medium": true, "__cft__": true, "__tn__": true,
		"hc_entry": true, "action_type": true,
	}
	q := u.Query()
	clean := make(url.Values)
	for k, vs := range q {
		if tracked[strings.ToLower(k)] {
			continue
		}
		clean[k] = vs
	}
	u.RawQuery = clean.Encode()
	path := strings.TrimRight(u.Path, "/")
	if path == "" {
		path = "/"
	}
	u.Path = path
	out := u.String()
	return &out
}

// MakeFingerprint mirrors dedup.make_fingerprint: sha256 of
// "page_id|timestamp_second|text[:200]" (timestamp reduced to second
// precision so two DOM snapshots differing only by microseconds hash the
// same).
func MakeFingerprint(post map[string]interface{}) string {
	pageID := stringify(post["page_id"])
	var ts string
	if v, ok := post["timestamp"]; ok && v != nil {
		ts = fmt.Sprintf("%d", toInt(v))
	} else {
		pub := stringify(post["published_at"])
		if len([]rune(pub)) > 19 {
			ts = string([]rune(pub)[:19])
		} else {
			ts = pub
		}
	}
	text := stringify(post["text"])
	tr := []rune(text)
	if len(tr) > textFingerprintLen {
		tr = tr[:textFingerprintLen]
	}
	payload := fmt.Sprintf("%s|%s|%s", pageID, ts, string(tr))
	sum := sha256.Sum256([]byte(payload))
	return hex.EncodeToString(sum[:])
}

func toInt(v interface{}) int64 {
	switch t := v.(type) {
	case int64:
		return t
	case *int64:
		if t != nil {
			return *t
		}
	case float64:
		return int64(t)
	case int:
		return int64(t)
	}
	return 0
}

// MakeContentFingerprint mirrors dedup.make_content_fingerprint: the
// cross-source content fingerprint (text hash + published_at).
func MakeContentFingerprint(post map[string]interface{}) string {
	text := stringify(post["text"])
	tr := []rune(text)
	if len(tr) > textFingerprintLen {
		tr = tr[:textFingerprintLen]
	}
	publishedAt := stringify(post["published_at"])
	sum := sha256.Sum256([]byte(string(tr)))
	return fmt.Sprintf("%s|%s", hex.EncodeToString(sum[:])[:16], publishedAt)
}

// DedupKey mirrors dedup.dedup_key: "id:<post_id>" when present, else
// "fp:<fingerprint>".  The prefixes keep namespaces apart so a numeric id
// can never collide with a hex fingerprint.
func DedupKey(post map[string]interface{}) string {
	if postID := stringify(post["post_id"]); postID != "" {
		return "id:" + postID
	}
	return "fp:" + MakeFingerprint(post)
}

// DedupPosts mirrors dedup.dedup_posts: sequential first-wins dedup.
// Returns (kept, duplicatesRemoved); kept preserves input order.
func DedupPosts(posts []map[string]interface{}) ([]map[string]interface{}, int) {
	seen := make(map[string]struct{})
	kept := make([]map[string]interface{}, 0, len(posts))
	removed := 0
	for _, post := range posts {
		k := DedupKey(post)
		if _, ok := seen[k]; ok {
			removed++
			continue
		}
		seen[k] = struct{}{}
		kept = append(kept, post)
	}
	return kept, removed
}

// DedupPostsAcrossSources mirrors dedup.dedup_posts_across_sources:
// first-dedupe within each source, then across all sources using both the
// id key and the content fingerprint.
func DedupPostsAcrossSources(sources ...[]map[string]interface{}) ([]map[string]interface{}, int) {
	seenIDs := make(map[string]struct{})
	seenContent := make(map[string]struct{})
	var all []map[string]interface{}
	total := 0
	for _, posts := range sources {
		kept, within := DedupPosts(posts)
		total += within
		for _, post := range kept {
			k := DedupKey(post)
			if _, ok := seenIDs[k]; ok {
				total++
				continue
			}
			cfp := MakeContentFingerprint(post)
			if _, ok := seenContent[cfp]; ok {
				total++
				continue
			}
			seenIDs[k] = struct{}{}
			seenContent[cfp] = struct{}{}
			all = append(all, post)
		}
	}
	return all, total
}
