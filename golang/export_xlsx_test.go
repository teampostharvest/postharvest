// Hermetic proof for the XLSX builder (M5): the golden workbook is written
// by the REAL Python exporter — backend/exporters/xlsx_exporter.py via
// testdata/gen_xlsx_golden.py — applied to the SAME posts the HTTP goldens
// already prove byte-for-byte.  openpyxl and excelize can never produce
// byte-identical .xlsx files (zip/package metadata differs), so the honest
// proof is logical cell-model equality: each sheet's values, number
// formats, hyperlinks, frozen panes, auto-filter range and column widths
// match.  Date cells are additionally decoded from their Excel serial and
// asserted to carry the original wall-clock time (proving the tz-strip).
package worker

import (
	"archive/zip"
	"bytes"
	"encoding/json"
	"encoding/xml"
	"fmt"
	"io"
	"math"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"testing"
	"time"
)

// ---- fixed export context (must match testdata/gen_xlsx_golden.py) ----

const (
	xlsxGoldenJobID    = "job_7"
	xlsxGoldenSource   = "postharvest"
	xlsxGoldenGenValue = "postharvest golang/worker export.go"
)

var xlsxGoldenExportedAt = time.Date(2026, 9, 24, 12, 0, 0, 0, time.UTC)

// ---- fixture loading ----

// xlsxGoldenPosts decodes the posts from the httpapi goldens exactly as
// gen_xlsx_golden.py does (JSON -> interface{}: floats, []interface{}).
func xlsxGoldenPosts(t *testing.T) []map[string]interface{} {
	t.Helper()
	var posts []map[string]interface{}
	for _, name := range []string{
		"parse_response_dom_sample.json",
		"parse_response_browser_snapshot_html.json",
		"parse_response_browser_snapshot_graphql.json",
	} {
		b, err := os.ReadFile(filepath.Join("httpapi", "testdata", name))
		if err != nil {
			t.Fatalf("read httpapi golden %s: %v", name, err)
		}
		var doc struct {
			Posts []map[string]interface{} `json:"posts"`
		}
		if err := json.Unmarshal(b, &doc); err != nil {
			t.Fatalf("decode %s: %v", name, err)
		}
		posts = append(posts, doc.Posts...)
	}
	return posts
}

func TestWriteXLSXMatchesPythonCellModel(t *testing.T) {
	golden, err := os.ReadFile("testdata/facebook_posts_golden.xlsx")
	if err != nil {
		t.Fatalf("read xlsx golden: %v (run ./.venv/bin/python golang/testdata/gen_xlsx_golden.py)", err)
	}
	posts := xlsxGoldenPosts(t)
	var got bytes.Buffer
	if err := WriteXLSX(&got, posts, xlsxGoldenJobID, xlsxGoldenSource,
		xlsxGoldenExportedAt, "1.0"); err != nil {
		t.Fatalf("WriteXLSX: %v", err)
	}

	gm := readXlsxModel(t, golden)
	om := readXlsxModel(t, got.Bytes())

	// Sheet names and order (Posts, Engagement, Media, Metadata).
	if !reflect.DeepEqual(gm.names, om.names) {
		t.Fatalf("sheet order:\n golden %v\n got    %v", gm.names, om.names)
	}

	for _, name := range []string{"Posts", "Engagement", "Media"} {
		gs, okg := gm.sheets[name]
		os, oko := om.sheets[name]
		if !okg || !oko {
			t.Fatalf("sheet %q missing on %s", name, map[bool]string{true: "got", false: "golden"}[oko && okg])
		}
		compareXlsxSheet(t, name, gs, os)
	}

	// Metadata: compare cell-by-cell EXCLUDING the generator row (the two
	// exporters honestly identify themselves differently), then assert the
	// Go side's value directly.
	gs, okg := gm.sheets["Metadata"]
	os, oko := om.sheets["Metadata"]
	if !okg || !oko {
		t.Fatalf("Metadata sheet missing on one side")
	}
	excluded := map[string]bool{"B10": true}
	for _, ref := range unionRefs(gs.cells, os.cells) {
		if excluded[ref] {
			continue
		}
		gc, oc := gs.cells[ref], os.cells[ref]
		if gc.value != oc.value {
			t.Fatalf("Metadata %s: value golden %q != got %q", ref, gc.value, oc.value)
		}
	}
	gen := os.cells["B10"]
	if gen.value != xlsxGoldenGenValue {
		t.Fatalf("Metadata generator = %q, want %q", gen.value, xlsxGoldenGenValue)
	}
}

// compareXlsxSheet asserts logical cell-model equality between the openpyxl
// golden sheet and the excelize produced sheet.
func compareXlsxSheet(t *testing.T, name string, g, o *xlsxSheet) {
	t.Helper()
	// Cells: union of refs (excelize omits blank cells openpyxl styles).
	for _, ref := range unionRefs(g.cells, o.cells) {
		gc, gok := g.cells[ref]
		oc, ook := o.cells[ref]
		// Missing side == blank "general" cell (openpyxl writes styled
		// blanks, excelize omits them).
		if !gok {
			gc = xlsxCellModel{numFmt: "general"}
		}
		if !ook {
			oc = xlsxCellModel{numFmt: "general"}
		}
		if gc.numFmt == "" {
			gc.numFmt = "general"
		}
		if oc.numFmt == "" {
			oc.numFmt = "general"
		}
		if gc.numFmt != oc.numFmt {
			t.Fatalf("%s %s: numFmt golden %q != got %q (value %q/%q)",
				name, ref, gc.numFmt, oc.numFmt, gc.value, oc.value)
		}
		if !sameCellValue(gc.value, oc.value) {
			t.Fatalf("%s %s: value golden %q != got %q (numFmt %q)",
				name, ref, gc.value, oc.value, gc.numFmt)
		}
	}
	// Hyperlink target cells match exactly.
	if !reflect.DeepEqual(g.links, o.links) {
		t.Fatalf("%s hyperlinks:\n golden %v\n got    %v", name, keys(g.links), keys(o.links))
	}
	// Frozen panes: same split/topLeft/state.
	if g.pane != o.pane {
		t.Fatalf("%s panes:\n golden %+v\n got    %+v", name, g.pane, o.pane)
	}
	// Auto-filter over the used range (strip excelize's $ refs).
	if g.filter != o.filter {
		t.Fatalf("%s autofilter: golden %q != got %q", name, g.filter, o.filter)
	}
	// Column widths per index.
	if !reflect.DeepEqual(g.widths, o.widths) {
		t.Fatalf("%s column widths:\n golden %v\n got    %v", name, g.widths, o.widths)
	}
}

func TestWriteXLSXDateCellsKeepWallClock(t *testing.T) {
	// The date cells decode their Excel serial back to the original wall
	// clock (zone stripped, like openpyxl).  Serial epoch is 1899-12-30.
	cases := []struct{ ref, wantISO string }{
		{"D2", "2023-11-14T22:29:20+00:00"},
		{"D5", "2026-08-21T11:00:00+00:00"},
		{"D6", "2026-08-21T08:13:20+00:00"},
	}
	posts := xlsxGoldenPosts(t)
	var got bytes.Buffer
	if err := WriteXLSX(&got, posts, xlsxGoldenJobID, xlsxGoldenSource,
		xlsxGoldenExportedAt, "1.0"); err != nil {
		t.Fatalf("WriteXLSX: %v", err)
	}
	om := readXlsxModel(t, got.Bytes())
	sheet := om.sheets["Posts"]
	for _, c := range cases {
		cell := sheet.cells[c.ref]
		if cell.numFmt != XLSX_DATE_FORMAT {
			t.Fatalf("%s: numFmt = %q, want %q", c.ref, cell.numFmt, XLSX_DATE_FORMAT)
		}
		serial, err := strconv.ParseFloat(cell.value, 64)
		if err != nil {
			t.Fatalf("%s: value = %q, want Excel serial", c.ref, cell.value)
		}
		want, err := time.Parse(time.RFC3339, c.wantISO)
		if err != nil {
			t.Fatal(err)
		}
		gotSerial := excelSerial(want)
		if math.Abs(serial-gotSerial) > 1e-6 {
			t.Fatalf("%s: serial %f != %f for wall time %s", c.ref, serial, gotSerial, c.wantISO)
		}
	}
}

// excelSerial computes an Excel serial (1899-12-30 epoch) for the naive
// wall-time components, matching how both libraries store date cells.
func excelSerial(wall time.Time) float64 {
	epoch := time.Date(1899, 12, 30, 0, 0, 0, 0, time.UTC)
	wall = time.Date(wall.Year(), wall.Month(), wall.Day(), wall.Hour(),
		wall.Minute(), wall.Second(), 0, time.UTC)
	return wall.Sub(epoch).Hours() / 24
}

func sameCellValue(a, b string) bool {
	if a == b {
		return true
	}
	af, aerr := strconv.ParseFloat(a, 64)
	bf, berr := strconv.ParseFloat(b, 64)
	if aerr != nil || berr != nil {
		return false
	}
	return math.Abs(af-bf) <= 1e-6*math.Max(1, math.Abs(af))
}

func unionRefs(a, b map[string]xlsxCellModel) []string {
	set := map[string]bool{}
	for k := range a {
		set[k] = true
	}
	for k := range b {
		set[k] = true
	}
	refs := make([]string, 0, len(set))
	for k := range set {
		refs = append(refs, k)
	}
	sort.Strings(refs)
	return refs
}

func keys(m map[string]bool) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// ---- minimal OOXML reader (values, formats, panes, filters, widths) ----

type xlsxCellModel struct {
	value  string
	numFmt string
}

type xlsxPane struct {
	activePane  string
	state       string
	topLeftCell string
	ySplit      float64
}

type xlsxSheet struct {
	cells  map[string]xlsxCellModel
	links  map[string]bool
	pane   xlsxPane
	filter string
	widths map[int]float64
}

type xlsxModel struct {
	names  []string
	sheets map[string]*xlsxSheet
}

func readXlsxModel(t *testing.T, data []byte) *xlsxModel {
	t.Helper()
	zr, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
	if err != nil {
		t.Fatalf("xlsx zip: %v", err)
	}
	get := func(name string) ([]byte, bool) {
		for _, zf := range zr.File {
			if zf.Name == name || zf.Name == "xl/"+name {
				rc, err := zf.Open()
				if err != nil {
					t.Fatalf("open %s: %v", zf.Name, err)
				}
				b, err := io.ReadAll(rc)
				rc.Close()
				if err != nil {
					t.Fatalf("read %s: %v", zf.Name, err)
				}
				return b, true
			}
		}
		return nil, false
	}

	wbXML, ok := get("workbook.xml")
	if !ok {
		t.Fatal("no xl/workbook.xml")
	}
	relsXML, _ := get("xl/_rels/workbook.xml.rels") // openpyxl path
	if relsXML == nil {
		relsXML, _ = get("_rels/workbook.xml.rels")
	}
	ssXML, _ := get("sharedStrings.xml")
	stXML, _ := get("styles.xml")

	var wb struct {
		Sheets []struct {
			Name string `xml:"name,attr"`
			RID  string `xml:"http://schemas.openxmlformats.org/officeDocument/2006/relationships id,attr"`
		} `xml:"sheets>sheet"`
	}
	if err := xml.Unmarshal(wbXML, &wb); err != nil {
		t.Fatalf("workbook.xml: %v", err)
	}
	var rels struct {
		Rels []struct {
			ID     string `xml:"Id,attr"`
			Target string `xml:"Target,attr"`
		} `xml:"Relationship"`
	}
	if len(relsXML) > 0 {
		if err := xml.Unmarshal(relsXML, &rels); err != nil {
			t.Fatalf("workbook.xml.rels: %v", err)
		}
	}
	target := map[string]string{}
	for _, r := range rels.Rels {
		target[r.ID] = r.Target
	}
	shared := readSharedStrings(t, ssXML)
	styles := readStyles(t, stXML)

	m := &xlsxModel{sheets: map[string]*xlsxSheet{}}
	for _, s := range wb.Sheets {
		tgt, ok := target[s.RID]
		if !ok {
			t.Fatalf("sheet %s: no rel target for %s", s.Name, s.RID)
		}
		sheetXML, ok := get(strings.TrimPrefix(tgt, "/"))
		if !ok {
			t.Fatalf("sheet %s: missing %s", s.Name, tgt)
		}
		m.names = append(m.names, s.Name)
		m.sheets[s.Name] = parseXlsxSheet(t, sheetXML, shared, styles)
	}
	return m
}

func readSharedStrings(t *testing.T, data []byte) []string {
	t.Helper()
	if len(data) == 0 {
		return nil
	}
	dec := xml.NewDecoder(bytes.NewReader(data))
	var out []string
	for {
		tok, err := dec.Token()
		if err != nil {
			break
		}
		if se, ok := tok.(xml.StartElement); ok && se.Name.Local == "si" {
			out = append(out, siText(dec, se))
		}
	}
	return out
}

// siText collects the concatenated <t> character data inside one <si>,
// handling both the plain `<si><t>…</t></si>` and rich-text run form.
func siText(dec *xml.Decoder, start xml.StartElement) string {
	var b strings.Builder
	for {
		tok, err := dec.Token()
		if err != nil {
			return b.String()
		}
		switch tt := tok.(type) {
		case xml.StartElement:
			if tt.Name.Local == "t" {
				if cd, err := dec.Token(); err == nil {
					if chars, ok := cd.(xml.CharData); ok {
						b.Write(chars)
					}
				}
			}
		case xml.EndElement:
			if tt.Name == start.Name {
				return b.String()
			}
		}
	}
}

type xlsxStyleModel struct {
	codeByXf map[int]string
}

func readStyles(t *testing.T, data []byte) *xlsxStyleModel {
	t.Helper()
	m := &xlsxStyleModel{codeByXf: map[int]string{}}
	if len(data) == 0 {
		return m
	}
	var st struct {
		NumFmts struct {
			NumFmt []struct {
				ID   int    `xml:"numFmtId,attr"`
				Code string `xml:"formatCode,attr"`
			} `xml:"numFmt"`
		} `xml:"numFmts"`
		CellXfs struct {
			Xf []struct {
				NumFmtID int `xml:"numFmtId,attr"`
			} `xml:"xf"`
		} `xml:"cellXfs"`
	}
	if err := xml.Unmarshal(data, &st); err != nil {
		t.Fatalf("styles.xml: %v", err)
	}
	codeByID := map[int]string{}
	for _, nf := range st.NumFmts.NumFmt {
		codeByID[nf.ID] = nf.Code
	}
	for i, xf := range st.CellXfs.Xf {
		if code, ok := codeByID[xf.NumFmtID]; ok {
			m.codeByXf[i] = code
		}
	}
	return m
}

func parseXlsxSheet(t *testing.T, data []byte, shared []string, styles *xlsxStyleModel) *xlsxSheet {
	t.Helper()
	var ws struct {
		Rows []struct {
			Cells []struct {
				Ref string `xml:"r,attr"`
				T   string `xml:"t,attr"`
				S   int    `xml:"s,attr"`
				V   string `xml:"v"`
				IS  struct {
					T string `xml:"t"`
				} `xml:"is"`
			} `xml:"c"`
		} `xml:"sheetData>row"`
		AutoFilter *struct {
			Ref string `xml:"ref,attr"`
		} `xml:"autoFilter"`
		Hyperlinks []struct {
			Ref string `xml:"ref,attr"`
		} `xml:"hyperlinks>hyperlink"`
		Pane *struct {
			ActivePane  string  `xml:"activePane,attr"`
			State       string  `xml:"state,attr"`
			TopLeftCell string  `xml:"topLeftCell,attr"`
			YSplit      float64 `xml:"ySplit,attr"`
		} `xml:"sheetViews>sheetView>pane"`
		Cols []struct {
			Min   int     `xml:"min,attr"`
			Max   int     `xml:"max,attr"`
			Width float64 `xml:"width,attr"`
		} `xml:"cols>col"`
	}
	if err := xml.Unmarshal(data, &ws); err != nil {
		t.Fatalf("worksheet: %v", err)
	}
	sm := &xlsxSheet{
		cells:  map[string]xlsxCellModel{},
		links:  map[string]bool{},
		widths: map[int]float64{},
	}
	for _, row := range ws.Rows {
		for _, c := range row.Cells {
			val := ""
			switch c.T {
			case "s":
				if i, err := strconv.Atoi(strings.TrimSpace(c.V)); err == nil && i >= 0 && i < len(shared) {
					val = shared[i]
				}
			case "inlineStr", "str":
				if c.IS.T != "" {
					val = c.IS.T
				} else {
					val = c.V
				}
			default:
				val = c.V
			}
			nf := "general"
			if code, ok := styles.codeByXf[c.S]; ok {
				nf = code
			}
			sm.cells[c.Ref] = xlsxCellModel{value: val, numFmt: nf}
		}
	}
	for _, h := range ws.Hyperlinks {
		sm.links[h.Ref] = true
	}
	if ws.Pane != nil {
		sm.pane = xlsxPane{
			activePane:  ws.Pane.ActivePane,
			state:       ws.Pane.State,
			topLeftCell: ws.Pane.TopLeftCell,
			ySplit:      ws.Pane.YSplit,
		}
	}
	if ws.AutoFilter != nil {
		// excelize writes $A$1:$D$2, openpyxl writes A1:D2 — normalize.
		sm.filter = strings.ReplaceAll(strings.TrimSpace(ws.AutoFilter.Ref), "$", "")
	}
	for _, col := range ws.Cols {
		for m := col.Min; m <= col.Max; m++ {
			sm.widths[m] = col.Width
		}
	}
	return sm
}

// silence unused-import lints for fmt/io in some builds.
var _ = fmt.Sprintf
