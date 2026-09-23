// Byte-mirror of backend/exporters/csv_exporter.py and
// backend/exporters/jsonl_exporter.py (single sources of truth).
//
// Golden bytes asserted in export_test.go were produced by the real Python
// exporters — the exact "\ufeff" BOM, the exact FLAT_COLUMNS order, the
// exact "\r\n" line endings, and the exact Python-format JSONL bytes
// (dict insertion order, ', ' / ': ' separators, ensure_ascii=False).
package worker

import (
	"bytes"
	"encoding/csv"
	"encoding/json"
	"fmt"
	"io"
	"strings"
)

// FLAT_COLUMNS is the shared flat schema — CSV columns and XLSX "Posts"
// sheet columns/order, mirroring csv_exporter.FLAT_COLUMNS byte-for-byte.
var FLAT_COLUMNS = []string{
	"post_id",
	"page_name",
	"post_url",
	"published_at",
	"text",
	"post_type",
	"likes",
	"comments_count",
	"shares",
	"views_count",
	"thumbnail_url",
	"media_url",
	"page_id",
	"profile_url",
	"reactions",
	"reaction_like_count",
	"reaction_love_count",
	"reaction_care_count",
	"reaction_haha_count",
	"reaction_wow_count",
	"reaction_sad_count",
	"reaction_angry_count",
	"hashtags",
	"mentions",
	"external_links",
	"media_type",
	"video_url",
	"transcript",
}

const listJoinSep = "|"

// cell mirrors csv_exporter._cell: None -> "", list/tuple -> "|"-joined.
func cell(v interface{}) string {
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
	case int64:
		return fmt.Sprintf("%d", t)
	case *int64:
		if t == nil {
			return ""
		}
		return fmt.Sprintf("%d", *t)
	case float64:
		return fmt.Sprintf("%v", t)
	case []string:
		var parts []string
		for _, item := range t {
			if item == "" {
				parts = append(parts, "")
			} else {
				parts = append(parts, item)
			}
		}
		return strings.Join(parts, listJoinSep)
	default:
		return fmt.Sprint(v)
	}
}

// FlattenPost mirrors csv_exporter.flatten_post: one normalized post ->
// FLAT_COLUMNS-order row (as strings, python csv.writer serializes ints).
func FlattenPost(post map[string]interface{}) []string {
	out := make([]string, 0, len(FLAT_COLUMNS))
	for _, key := range FLAT_COLUMNS {
		out = append(out, cell(post[key]))
	}
	return out
}

// WriteCSV mirrors csv_exporter.export_csv into w (streaming, no
// materialization): utf-8-sig BOM, FLAT_COLUMNS header, "\r\n" rows.
func WriteCSV(w io.Writer, posts []map[string]interface{}) error {
	// encoding: "utf-8-sig" => BOM prefix on the stream.
	if _, err := io.WriteString(w, "\ufeff"); err != nil {
		return err
	}
	cw := csv.NewWriter(w)
	cw.UseCRLF = true // Python csv.writer default line terminator
	if err := cw.Write(FLAT_COLUMNS); err != nil {
		return err
	}
	for _, post := range posts {
		if err := cw.Write(FlattenPost(post)); err != nil {
			return err
		}
	}
	cw.Flush()
	return cw.Error()
}

// marshalPythonJSON serializes one scalar exactly like Python json.dumps
// with cls=PostJSONEncoder, ensure_ascii=False: Go's HTML-escaping of
// "<>&" disabled (Python emits them raw), non-ASCII raw, and json.Encoder's
// trailing newline stripped (json.dumps emits none).
func marshalPythonJSON(v interface{}) (string, error) {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(v); err != nil {
		return "", err
	}
	out := buf.String()
	if len(out) > 0 && out[len(out)-1] == '\n' {
		out = out[:len(out)-1]
	}
	return out, nil
}

// pythonValue dereferences pointer fields and marshals the value exactly
// like Python json.dumps would (None -> null, ints unquoted, lists with
// ', ' separators, insertion order preserved by pythonDictString).
func pythonValue(v interface{}) (string, error) {
	switch t := v.(type) {
	case nil:
		return "null", nil
	case *string:
		if t == nil {
			return "null", nil
		}
		return marshalPythonJSON(*t)
	case *int64:
		if t == nil {
			return "null", nil
		}
		return marshalPythonJSON(*t)
	case []string:
		var b bytes.Buffer
		b.WriteByte('[')
		for i, s := range t {
			if i > 0 {
				b.WriteString(", ")
			}
			sv, err := marshalPythonJSON(s)
			if err != nil {
				return "", err
			}
			b.WriteString(sv)
		}
		b.WriteByte(']')
		return b.String(), nil
	default:
		return marshalPythonJSON(v)
	}
}

// pythonDictString renders one post dict in the 33-key schema order with
// Python's default separators (', ', ': ') and no ASCII escaping — the
// exact bytes jsonl_exporter writes per line.
func pythonDictString(post map[string]interface{}) (string, error) {
	var b bytes.Buffer
	b.WriteByte('{')
	for i, key := range NORMALIZED_KEYS {
		if i > 0 {
			b.WriteString(", ")
		}
		ks, err := marshalPythonJSON(key)
		if err != nil {
			return "", err
		}
		b.WriteString(ks)
		b.WriteString(": ")
		pv, err := pythonValue(post[key])
		if err != nil {
			return "", err
		}
		b.WriteString(pv)
	}
	b.WriteByte('}')
	return b.String(), nil
}

// WriteJSONL mirrors jsonl_exporter.export_jsonl: one JSON object per line
// (pythonDictString byte-exact), trailing "\n", UTF-8 no BOM.
func WriteJSONL(w io.Writer, posts []map[string]interface{}) error {
	for _, post := range posts {
		line, err := pythonDictString(post)
		if err != nil {
			return err
		}
		if _, err := io.WriteString(w, line+"\n"); err != nil {
			return err
		}
	}
	return nil
}
