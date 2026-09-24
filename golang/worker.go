// Stateless compute entry for slice A: normalize -> dedup, then export.
// Mirrors the Python job pipeline shape (normalizer -> dedup -> exporters)
// as pure functions so the exact bytes can be proven hermetically.  This
// file deliberately carries NO network / DB / session code — the plan §5
// "most boring service" contract.
package worker

import (
	"bytes"
	"io"
	"time"
)

// ProcessResult is what one raw payload becomes after normalize + dedup.
type ProcessResult struct {
	Posts             []map[string]interface{} `json:"posts"`
	DuplicatesRemoved int                      `json:"duplicates_removed"`
	PostsKept         int                      `json:"posts_kept"`
}

// ProcessRaw runs the pure pipeline for one source's parsed posts.
// now is the injectable clock (scraped_at); page metadata flows in like the
// Python normalize_post page_name/page_id/facebook_url parameters.
func ProcessRaw(parsed []*ParsedPost, pageName, pageID, facebookURL *string, now time.Time) ProcessResult {
	normalized := make([]map[string]interface{}, 0, len(parsed))
	for _, p := range parsed {
		normalized = append(normalized, NormalizePost(p, pageName, pageID, facebookURL, now))
	}
	kept, removed := DedupPosts(normalized)
	return ProcessResult{
		Posts:             kept,
		DuplicatesRemoved: removed,
		PostsKept:         len(kept),
	}
}

// CSVBytes returns the exact UTF-8-BOM CSV bytes for the kept posts.
func (r ProcessResult) CSVBytes() ([]byte, error) {
	var buf bytes.Buffer
	if err := WriteCSV(&buf, r.Posts); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

// JSONLBytes returns the exact Python-format JSONL bytes (one obj/line).
func (r ProcessResult) JSONLBytes() ([]byte, error) {
	var buf bytes.Buffer
	if err := WriteJSONL(&buf, r.Posts); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

// WriteExports mirrors the exporter file-writing contract over io.Writers
// (Python side keeps path-safety per plan §5; Go hands back bytes only).
func (r ProcessResult) WriteExports(csvW, jsonlW io.Writer) error {
	if err := WriteCSV(csvW, r.Posts); err != nil {
		return err
	}
	return WriteJSONL(jsonlW, r.Posts)
}
