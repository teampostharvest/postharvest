// Byte-mirror of backend/exporters/xlsx_exporter.py (single source of
// truth): the styled 4-sheet workbook — Posts (FLAT_COLUMNS rows),
// Engagement, Media, Metadata — with frozen header row, auto-filter,
// capped column widths, bold blue header, date number format, hyperlinks
// and wrap alignment.
//
// Honest proof note: two spreadsheet libraries (openpyxl vs excelize) can
// never produce byte-identical .xlsx files — the zip container metadata
// differs.  What CAN be proven is the logical cell model, and that is what
// export_test.go asserts: the real-Python golden workbook (openpyxl, via
// testdata/gen_xlsx_golden.py) and this builder's output are both unzipped
// and compared cell-by-cell (values, number formats, panes, autofilter
// range, column widths) so the OOXML data layer matches even though the
// file bytes do not.  The flat rows themselves are already byte-proven by
// WriteCSV (same FLAT_COLUMNS order/content).
package worker

import (
	"fmt"
	"io"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/xuri/excelize/v2"
)

// ENGAGEMENT_COLUMNS and MEDIA_COLUMNS mirror xlsx_exporter (same names and
// order).  FLAT_COLUMNS is shared with the CSV/Posts path.
var (
	// Engagement sheet: post_id + all engagement/reaction numbers.
	ENGAGEMENT_COLUMNS = []string{
		"post_id",
		"likes",
		"reactions",
		"comments_count",
		"shares",
		"views_count",
		"reaction_like_count",
		"reaction_love_count",
		"reaction_care_count",
		"reaction_haha_count",
		"reaction_wow_count",
		"reaction_sad_count",
		"reaction_angry_count",
	}
	// Media sheet: post_id + media fields.
	MEDIA_COLUMNS = []string{
		"post_id",
		"media_type",
		"thumbnail_url",
		"media_url",
		"video_url",
	}
)

// XSLX_SCHEMA_VERSION mirrors xlsx_exporter.SCHEMA_VERSION.
const XSLX_SCHEMA_VERSION = "1.0"

// XLSX_DATE_FORMAT mirrors xlsx_exporter.DATE_FORMAT — the custom number
// format applied to published_at cells.
const XLSX_DATE_FORMAT = "yyyy-mm-dd hh:mm:ss"

// xlsxDateFormat is a mutable copy for excelize's *string CustomNumFmt.
var xlsxDateFormat = XLSX_DATE_FORMAT

// XLSX_DATE_FMT_ID is the numFmtId excelize registers for the custom
// XLSX_DATE_FORMAT string (custom formats start at 164 in the stylesheet).
const XLSX_DATE_FMT_ID = 164

const (
	headerFillColor  = "4472C4"
	hyperlinkColor   = "0563C1"
	headerBorderSide = "2F5597"
	headerBorderBase = "1F3864"
)

// wrapColumns mirrors _WRAP_COLUMNS: cells that wrap their text vertically.
var wrapColumns = map[string]bool{
	"text": true, "transcript": true, "hashtags": true,
	"mentions": true, "external_links": true,
}

// exportXlsxOptions bundles the Metadata-sheet context (export_xlsx's
// keyword-only parameters plus the row count).
type exportXlsxOptions struct {
	JobID         string
	Source        string
	ExportedAt    time.Time
	SchemaVersion string
	PostsCount    int
}

// WriteXLSX mirrors export_xlsx: writes the styled 4-sheet workbook to w.
// io.Writer keeps it consistent with WriteCSV/WriteJSONL; the caller owns
// the destination (Python's safe_filename / path handling stays client-side
// exactly as the other exporters split it).
func WriteXLSX(w io.Writer, posts []map[string]interface{}, jobID, source string, exportedAt time.Time, schemaVersion string) error {
	f := excelize.NewFile()

	// Posts sheet (NewFile names the active sheet "Sheet1").
	if err := f.SetSheetName("Sheet1", "Posts"); err != nil {
		return fmt.Errorf("xlsx: rename Posts sheet: %w", err)
	}
	urlColumns := map[string]bool{}
	for _, col := range FLAT_COLUMNS {
		if strings.HasSuffix(col, "_url") {
			urlColumns[col] = true
		}
	}
	if err := writeXlsxSheet(f, "Posts", FLAT_COLUMNS, posts, xlsxSheetOpts{
		dateColumn:  "published_at",
		wrapColumns: wrapColumns,
		urlColumns:  urlColumns,
	}); err != nil {
		return err
	}

	// Engagement sheet.
	if _, err := f.NewSheet("Engagement"); err != nil {
		return fmt.Errorf("xlsx: Engagement sheet: %w", err)
	}
	if err := writeXlsxSheet(f, "Engagement", ENGAGEMENT_COLUMNS, posts, xlsxSheetOpts{}); err != nil {
		return err
	}

	// Media sheet.
	if _, err := f.NewSheet("Media"); err != nil {
		return fmt.Errorf("xlsx: Media sheet: %w", err)
	}
	mediaURls := map[string]bool{}
	for _, col := range MEDIA_COLUMNS {
		if col != "post_id" && col != "media_type" {
			mediaURls[col] = true
		}
	}
	if err := writeXlsxSheet(f, "Media", MEDIA_COLUMNS, posts, xlsxSheetOpts{urlColumns: mediaURls}); err != nil {
		return err
	}

	// Metadata sheet.
	if _, err := f.NewSheet("Metadata"); err != nil {
		return fmt.Errorf("xlsx: Metadata sheet: %w", err)
	}
	if err := writeXlsxMetadata(f, exportXlsxOptions{
		JobID:         jobID,
		Source:        source,
		ExportedAt:    exportedAt,
		SchemaVersion: schemaVersion,
		PostsCount:    len(posts),
	}); err != nil {
		return err
	}

	if _, err := f.WriteTo(w); err != nil {
		return fmt.Errorf("xlsx: write workbook: %w", err)
	}
	return nil
}

type xlsxSheetOpts struct {
	dateColumn  string
	wrapColumns map[string]bool
	urlColumns  map[string]bool
}

// xlsxDataRow is one data row of cell-ready values.
type xlsxDataRow []interface{}

// writeXlsxSheet mirrors _write_sheet: header styling, data cells with
// date/wrap/hyperlink handling, freeze panes, auto-filter, column widths.
func writeXlsxSheet(f *excelize.File, sheet string, header []string, posts []map[string]interface{}, opts xlsxSheetOpts) error {
	var rows []xlsxDataRow
	for _, post := range posts {
		row := make(xlsxDataRow, len(header))
		for i, col := range header {
			row[i] = xlsxCell(post[col])
		}
		rows = append(rows, row)
	}

	// Header cells: values first, then the styled wrapped range.
	lastCol := xlsxColumnLetter(len(header) - 1)
	for colIdx, name := range header {
		if err := f.SetCellValue(sheet, xlsxColumnLetter(colIdx)+"1", name); err != nil {
			return fmt.Errorf("xlsx: header cell %s: %w", name, err)
		}
	}
	headerStyle, err := f.NewStyle(&excelize.Style{
		Font:      &excelize.Font{Bold: true, Color: "FFFFFF", Size: 11},
		Fill:      excelize.Fill{Type: "pattern", Pattern: 1, Color: []string{headerFillColor}},
		Alignment: &excelize.Alignment{Horizontal: "center", Vertical: "center"},
		Border: []excelize.Border{
			{Type: "left", Color: headerBorderSide, Style: 1},
			{Type: "right", Color: headerBorderSide, Style: 1},
			{Type: "top", Color: headerBorderSide, Style: 1},
			{Type: "bottom", Color: headerBorderBase, Style: 2},
		},
	})
	if err != nil {
		return fmt.Errorf("xlsx: header style: %w", err)
	}
	if err := f.SetCellStyle(sheet, "A1", lastCol+"1", headerStyle); err != nil {
		return fmt.Errorf("xlsx: style header row: %w", err)
	}
	if err := f.SetRowHeight(sheet, 1, 22); err != nil {
		return fmt.Errorf("xlsx: header row height: %w", err)
	}

	// Shared cell styles.
	wrapStyle, err := f.NewStyle(&excelize.Style{
		Alignment: &excelize.Alignment{Vertical: "top", WrapText: true},
	})
	if err != nil {
		return fmt.Errorf("xlsx: wrap style: %w", err)
	}
	topStyle, err := f.NewStyle(&excelize.Style{
		Alignment: &excelize.Alignment{Vertical: "top"},
	})
	if err != nil {
		return fmt.Errorf("xlsx: top style: %w", err)
	}
	linkStyle, err := f.NewStyle(&excelize.Style{
		Font:      &excelize.Font{Color: hyperlinkColor, Underline: "single"},
		Alignment: &excelize.Alignment{Vertical: "top"},
	})
	if err != nil {
		return fmt.Errorf("xlsx: link style: %w", err)
	}
	dateStyle, err := f.NewStyle(&excelize.Style{
		CustomNumFmt: &xlsxDateFormat,
		Alignment:    &excelize.Alignment{Vertical: "top"},
	})
	if err != nil {
		return fmt.Errorf("xlsx: date style: %w", err)
	}

	for rIdx, row := range rows {
		rowNum := rIdx + 2
		for cIdx, raw := range row {
			name := header[cIdx]
			cell := xlsxColumnLetter(cIdx) + fmt.Sprintf("%d", rowNum)

			// Date column gets a parsed datetime + custom number format.
			if name == opts.dateColumn {
				if parsed, ok := isoToDateTime(raw).(time.Time); ok {
					if err := f.SetCellValue(sheet, cell, parsed); err != nil {
						return fmt.Errorf("xlsx: date cell %s: %w", cell, err)
					}
					if err := f.SetCellStyle(sheet, cell, cell, dateStyle); err != nil {
						return fmt.Errorf("xlsx: date style %s: %w", cell, err)
					}
					continue
				}
				// Unparseable -> raw string fallback (Python behavior).
			}

			// Wrap column: vertical-top + wrap_text when non-empty.
			if opts.wrapColumns[name] && raw != nil && fmt.Sprint(raw) != "" {
				if err := f.SetCellValue(sheet, cell, raw); err != nil {
					return fmt.Errorf("xlsx: cell %s: %w", cell, err)
				}
				if err := f.SetCellStyle(sheet, cell, cell, wrapStyle); err != nil {
					return fmt.Errorf("xlsx: wrap style %s: %w", cell, err)
				}
				continue
			}

			// URL column: hyperlink + blue underline for http(s) strings.
			if opts.urlColumns[name] {
				if s, ok := raw.(string); ok &&
					(strings.HasPrefix(s, "http://") || strings.HasPrefix(s, "https://")) {
					if err := f.SetCellValue(sheet, cell, s); err != nil {
						return fmt.Errorf("xlsx: link cell %s: %w", cell, err)
					}
					if err := f.SetCellHyperLink(sheet, cell, s, "External"); err != nil {
						return fmt.Errorf("xlsx: hyperlink %s: %w", cell, err)
					}
					if err := f.SetCellStyle(sheet, cell, cell, linkStyle); err != nil {
						return fmt.Errorf("xlsx: link style %s: %w", cell, err)
					}
					continue
				}
			}

			// Plain cell: top-aligned.
			if err := f.SetCellValue(sheet, cell, raw); err != nil {
				return fmt.Errorf("xlsx: cell %s: %w", cell, err)
			}
			if err := f.SetCellStyle(sheet, cell, cell, topStyle); err != nil {
				return fmt.Errorf("xlsx: top style %s: %w", cell, err)
			}
		}
	}

	// Freeze the header row ("A2").
	if err := f.SetPanes(sheet, &excelize.Panes{
		Freeze:      true,
		YSplit:      1,
		TopLeftCell: "A2",
		ActivePane:  "bottomLeft",
	}); err != nil {
		return fmt.Errorf("xlsx: freeze panes: %w", err)
	}

	// Auto-filter over the used range when data exists.
	if len(rows) > 0 {
		if err := f.AutoFilter(sheet, xlsxRange(header, len(rows)), []excelize.AutoFilterOptions{}); err != nil {
			return fmt.Errorf("xlsx: auto filter: %w", err)
		}
	}

	// Column widths (header length + cell lengths, capped).
	widths := xlsxColumnWidths(header, rows)
	for colIdx, width := range widths {
		letter := xlsxColumnLetter(colIdx)
		if err := f.SetColWidth(sheet, letter, letter, float64(width)); err != nil {
			return fmt.Errorf("xlsx: col width %s: %w", letter, err)
		}
	}
	return nil
}

// xlsxMetaRow is one Metadata-sheet row: key + value.  Values stay typed so
// posts_count lands as a number cell (Python writes int(posts_count)), the
// rest as strings.
type xlsxMetaRow struct {
	key   string
	value interface{}
}

// xlsxISOSeconds renders exported_at like Python's
// `.isoformat(timespec="seconds")` — always an explicit "+00:00"-style
// offset (Go's "Z07:00" layout prints "Z" for UTC; Python never abbreviates).
func xlsxISOSeconds(t time.Time) string {
	_, offset := t.Zone()
	sign := "+"
	if offset < 0 {
		sign = "-"
		offset = -offset
	}
	return fmt.Sprintf("%04d-%02d-%02dT%02d:%02d:%02d%s%02d:%02d",
		t.Year(), t.Month(), t.Day(), t.Hour(), t.Minute(), t.Second(),
		sign, offset/3600, (offset%3600)/60)
}

// writeXlsxMetadata mirrors _write_metadata: the key/value context sheet.
func writeXlsxMetadata(f *excelize.File, opts exportXlsxOptions) error {
	headerStyle, err := f.NewStyle(&excelize.Style{
		Font:      &excelize.Font{Bold: true, Color: "FFFFFF", Size: 11},
		Fill:      excelize.Fill{Type: "pattern", Pattern: 1, Color: []string{headerFillColor}},
		Alignment: &excelize.Alignment{Horizontal: "center", Vertical: "center"},
	})
	if err != nil {
		return err
	}
	wrapStyle, err := f.NewStyle(&excelize.Style{
		Alignment: &excelize.Alignment{Vertical: "top", WrapText: true},
	})
	if err != nil {
		return err
	}
	topStyle, err := f.NewStyle(&excelize.Style{
		Alignment: &excelize.Alignment{Vertical: "top"},
	})
	if err != nil {
		return err
	}
	linkStyle, err := f.NewStyle(&excelize.Style{
		Font: &excelize.Font{Color: hyperlinkColor, Underline: "single"},
	})
	if err != nil {
		return err
	}

	rows := []xlsxMetaRow{
		{"exported_at", xlsxISOSeconds(opts.ExportedAt)},
		{"job_id", opts.JobID},
		{"source", opts.Source},
		{"format", "xlsx"},
		{"filename", "facebook_posts.xlsx"},
		{"posts_count", opts.PostsCount},
		{"sheets", "Posts, Engagement, Media, Metadata"},
		{"schema_version", opts.SchemaVersion},
		{"generator", "postharvest golang/worker export.go"},
	}

	for colIdx, name := range []string{"key", "value"} {
		cell := xlsxColumnLetter(colIdx) + "1"
		if err := f.SetCellValue("Metadata", cell, name); err != nil {
			return err
		}
		if err := f.SetCellStyle("Metadata", cell, cell, headerStyle); err != nil {
			return err
		}
	}
	for rIdx, row := range rows {
		rowNum := rIdx + 2
		keyCell := "A" + fmt.Sprintf("%d", rowNum)
		valCell := "B" + fmt.Sprintf("%d", rowNum)
		if err := f.SetCellValue("Metadata", keyCell, row.key); err != nil {
			return err
		}
		if err := f.SetCellStyle("Metadata", keyCell, keyCell, topStyle); err != nil {
			return err
		}
		if err := f.SetCellValue("Metadata", valCell, row.value); err != nil {
			return err
		}
		if err := f.SetCellStyle("Metadata", valCell, valCell, wrapStyle); err != nil {
			return err
		}
		if s, ok := row.value.(string); ok &&
			(strings.HasPrefix(s, "http://") || strings.HasPrefix(s, "https://")) {
			if err := f.SetCellHyperLink("Metadata", valCell, s, "External"); err != nil {
				return err
			}
			if err := f.SetCellStyle("Metadata", valCell, valCell, linkStyle); err != nil {
				return err
			}
		}
	}
	if err := f.SetColWidth("Metadata", "A", "A", 22); err != nil {
		return err
	}
	if err := f.SetColWidth("Metadata", "B", "B", 80); err != nil {
		return err
	}
	return nil
}

// xlsxCell normalizes one field for a spreadsheet cell (_xcell): nil stays
// nil (blank cell, not ""), lists join by "|" Python-style, scalars pass
// through.  Handles both native []string (Go pipeline) and []interface{}
// (JSON-decoded posts, as the golden tests feed it).
func xlsxCell(v interface{}) interface{} {
	switch t := v.(type) {
	case nil:
		return nil
	case string:
		return t
	case []string:
		return strings.Join(t, "|")
	case []interface{}:
		parts := make([]string, len(t))
		for i, item := range t {
			parts[i] = pythonStr(item)
		}
		return strings.Join(parts, "|")
	default:
		return v
	}
}

// pythonStr mirrors str() for list-join elements: None -> "" (Python's
// "|".join uses "" for None items), everything else str(item).
func pythonStr(v interface{}) string {
	if v == nil {
		return ""
	}
	return fmt.Sprint(v)
}

// isoToDateTime mirrors _iso_to_datetime: best-effort ISO-8601 -> datetime
// (handles "+hh:mm", "+hhmm", "+hh" and "Z").  Like openpyxl's behavior,
// the result is a naive wall time: the zone is stripped, so the local wall
// clock from the ISO string is displayed as-is ("Excel does not support
// timezones").  Unparseable values are returned untouched (raw string).
func isoToDateTime(v interface{}) interface{} {
	s, ok := v.(string)
	if !ok {
		return v
	}
	t, err := parsePythonISO(s)
	if err != nil {
		return s
	}
	return naiveWall(t)
}

// naiveWall returns the wall-clock components of t with no zone, so the
// Excel serial produced from it displays the same clock time Python shows.
func naiveWall(t time.Time) time.Time {
	return time.Date(t.Year(), t.Month(), t.Day(), t.Hour(), t.Minute(), t.Second(), 0, time.UTC)
}

// pythonISOLayouts cover the ISO-8601 forms Python's datetime.fromisoformat
// accepts for the "yyyy-mm-dd hh:mm:ss"-style cells we mirror (T or space
// separator, optional fraction, optional offset as +hh:mm / +hhmm / +hh / Z).
var pythonISOLayouts = []string{
	"2006-01-02T15:04:05.999999999Z07:00",
	"2006-01-02T15:04:05.999999999-0700",
	"2006-01-02T15:04:05.999999999-07",
	"2006-01-02T15:04:05.999999999",
	"2006-01-02 15:04:05.999999999Z07:00",
	"2006-01-02 15:04:05.999999999-0700",
	"2006-01-02 15:04:05.999999999-07",
	"2006-01-02 15:04:05.999999999",
	"2006-01-02",
}

func parsePythonISO(s string) (time.Time, error) {
	var lastErr error
	for _, layout := range pythonISOLayouts {
		if t, err := time.Parse(layout, s); err == nil {
			return t, nil
		} else {
			lastErr = err
		}
	}
	if t, err := time.Parse(time.RFC3339Nano, s); err == nil {
		return t, nil
	} else {
		lastErr = err
	}
	return time.Time{}, lastErr
}

// xlsxColumnWidths mirrors _column_widths: width from header length and
// actual cell string lengths, capped at 80, +2 padding, floored at 8.
// Lengths are measured in Python terms (rune/character count, not bytes —
// str(value) counts code points), so multibyte text keeps the same widths
// as openpyxl computes.
func xlsxColumnWidths(header []string, rows []xlsxDataRow) map[int]int {
	widths := map[int]int{}
	for i, h := range header {
		widths[i] = utf8.RuneCountInString(h)
	}
	for _, row := range rows {
		for i, v := range row {
			if v == nil {
				continue
			}
			length := utf8.RuneCountInString(fmt.Sprint(v))
			if length > widths[i] {
				if length > 80 {
					length = 80
				}
				widths[i] = length
			}
		}
	}
	out := map[int]int{}
	for i, w := range widths {
		w += 2
		if w < 8 {
			w = 8
		}
		out[i] = w
	}
	return out
}

// xlsxColumnLetter mirrors _letter: 0-based column index -> Excel letters.
func xlsxColumnLetter(colIdx int) string {
	letters := ""
	n := colIdx + 1
	for n > 0 {
		n--
		letters = string(rune('A'+n%26)) + letters
		n /= 26
	}
	return letters
}

// xlsxRange returns the "A1:<lastcol><lastrow>" used range for autofilter.
func xlsxRange(header []string, rowCount int) string {
	return "A1:" + xlsxColumnLetter(len(header)-1) + fmt.Sprintf("%d", rowCount+1)
}
