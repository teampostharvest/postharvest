package parser

// Timestamp / count helpers — direct mirrors of parser.py "Timestamp / count
// helpers (used by the parser and re-used by the normalizer)".

import (
	"math"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"
)

// relativeUnitSeconds mirrors _RELATIVE_UNIT_SECONDS.
var relativeUnitSeconds = map[string]int64{
	"s": 1, "sec": 1, "second": 1,
	"m": 60, "min": 60, "minute": 60,
	"h": 3600, "hr": 3600, "hour": 3600,
	"d": 86400, "day": 86400,
	"w": 604800, "wk": 604800, "week": 604800,
	"mo": 2592000, "mon": 2592000, "month": 2592000, // 30-day approximation
	"y": 31536000, "yr": 31536000, "year": 31536000, // 365-day approximation
}

// absFormats mirrors _ABS_FORMATS (Python strptime -> Go reference layouts).
// Formats without a year get the implied-year handling in parseAbs; %z
// formats produce offset-aware times.
var absFormats = []struct{ py, goLayout string }{
	{"%B %d, %Y at %I:%M %p", "January 2, 2006 at 3:04 PM"},
	{"%b %d, %Y at %I:%M %p", "Jan 2, 2006 at 3:04 PM"},
	{"%B %d, %Y at %H:%M", "January 2, 2006 at 15:04"},
	{"%B %d at %I:%M %p", "January 2 at 3:04 PM"},
	{"%b %d at %I:%M %p", "Jan 2 at 3:04 PM"},
	{"%B %d, %Y", "January 2, 2006"},
	{"%b %d, %Y", "Jan 2, 2006"},
	{"%d %B %Y at %H:%M", "2 January 2006 at 15:04"},
	{"%d %b %Y at %H:%M", "2 Jan 2006 at 15:04"},
	{"%d %B at %H:%M", "2 January at 15:04"},
	{"%d %b at %H:%M", "2 Jan at 15:04"},
	{"%d %B at %I:%M %p", "2 January at 3:04 PM"},
	{"%d %b at %I:%M %p", "2 Jan at 3:04 PM"},
	{"%d %B %Y", "2 January 2006"},
	{"%d %b %Y", "2 Jan 2006"},
	{"%d %B", "2 January"},
	{"%d %b", "2 Jan"},
	{"%Y-%m-%dT%H:%M:%S%z", "2006-01-02T15:04:05-07:00"},
	// Python strptime %z accepts +HH:MM, +HHMM and +HH; Go requires one
	// layout per form, so all three are tried in order.
	{"%Y-%m-%dT%H:%M:%S%z", "2006-01-02T15:04:05-0700"},
	{"%Y-%m-%dT%H:%M:%S%z", "2006-01-02T15:04:05-07"},
	{"%Y-%m-%dT%H:%M:%S", "2006-01-02T15:04:05"},
	{"%Y-%m-%d %H:%M:%S", "2006-01-02 15:04:05"},
	{"%Y-%m-%d", "2006-01-02"},
}

var clockFormats = []string{"3:04 PM", "15:04"}

// pySpace reports whether r is whitespace per Python str.isspace().
func pySpace(r rune) bool {
	if r >= 0x1C && r <= 0x1F {
		return true
	}
	return unicode.IsSpace(r)
}

// pySplit mirrors str.split() with no args: split on Python whitespace.
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

// pyTrim strips Python whitespace from both ends (like str.strip()).
func pyTrim(s string) string {
	rs := []rune(s)
	start, end := 0, len(rs)
	for start < end && pySpace(rs[start]) {
		start++
	}
	for end > start && pySpace(rs[end-1]) {
		end--
	}
	return string(rs[start:end])
}

// pyRuneLen mirrors len(str) in runes.
func pyRuneLen(s string) int {
	return len([]rune(s))
}

var countKMRe = regexp.MustCompile(`(?i)(^|[^\d.])(\d[\d.,]*)\s*([km])\b`)
var countNumRe = regexp.MustCompile(`(^|[^\d.])(\d[\d.,]*)\b`)

// ParseCount mirrors parser.py parse_count: "1.2K likes" -> 1200,
// "3.4M" -> 3400000, "1,234" -> 1234, bare "Share" -> nil.
// (The Python source uses (?<![\d.]) lookbehinds which RE2 does not support;
// the leading (^|[^\d.]) group below is equivalent.)
func ParseCount(text string) *int64 {
	if text == "" {
		return nil
	}
	s := collapse(strings.TrimSpace(text))
	if s == "" {
		return nil
	}
	multiplier := int64(1)
	var numStr, suffix string
	if m := countKMRe.FindStringSubmatch(s); m != nil {
		numStr = m[2]
		suffix = strings.ToLower(m[3])
	} else if m := countNumRe.FindStringSubmatch(s); m != nil {
		numStr = m[2]
	} else {
		return nil
	}
	num, err := strconv.ParseFloat(strings.ReplaceAll(numStr, ",", ""), 64)
	if err != nil {
		return nil
	}
	if suffix == "k" {
		multiplier = 1000
	} else if suffix == "m" {
		multiplier = 1000000
	}
	v := int64(num * float64(multiplier))
	return &v
}

// countNear mirrors _count_near: count within ~12 chars of a keyword.
// Python indexes are character offsets while Go regexes report byte offsets,
// so the window is converted via rune counts (matters with non-ASCII text).
func countNear(text string, keywords []string) *int64 {
	runes := []rune(text)
	for _, kw := range keywords {
		// Python re.escape() on these keywords is identity.
		re := regexp.MustCompile("(?i)" + regexp.QuoteMeta(kw))
		for _, m := range re.FindAllStringIndex(text, -1) {
			cs := utf8.RuneCountInString(text[:m[0]])
			ce := utf8.RuneCountInString(text[:m[1]])
			start := cs - 12
			if start < 0 {
				start = 0
			}
			end := ce + 12
			if end > len(runes) {
				end = len(runes)
			}
			if c := ParseCount(string(runes[start:end])); c != nil {
				return c
			}
		}
	}
	return nil
}

// parseClock mirrors _parse_clock: a bare clock ("8:05 AM") as a time on
// epoch 0 (only hour/minute are meaningful downstream).
func parseClock(s string) (time.Time, bool) {
	s = pyTrim(s)
	for _, lay := range clockFormats {
		t, err := time.Parse(lay, s)
		if err == nil {
			return t, true
		}
	}
	return time.Time{}, false
}

// parseAbs mirrors _parse_abs.  Year-implied formats assume the current year
// and roll back one year when the result is in the future.  Naive results
// stay UTC (Go time.Parse yields UTC for zone-less layouts).
func parseAbs(s string, now time.Time) *time.Time {
	raw := strings.TrimSpace(strings.ReplaceAll(s, "Z", "+00:00"))
	for _, f := range absFormats {
		t, err := time.Parse(f.goLayout, raw)
		if err != nil {
			continue
		}
		if !strings.Contains(f.py, "%Y") {
			t = time.Date(now.Year(), t.Month(), t.Day(), t.Hour(), t.Minute(),
				t.Second(), t.Nanosecond(), time.UTC)
			if t.After(now.Add(24 * time.Hour)) {
				t = t.AddDate(-1, 0, 0)
			}
		}
		// Zone-less layouts parse as UTC (naive strings are stored as UTC);
		// %z layouts keep their parsed offset. Nothing else to align.
		return &t
	}
	return nil
}

var epochRe = regexp.MustCompile(`^\d{9,13}$`)
var relativeRe = regexp.MustCompile(`^(\d+)\s*([a-z]+)\s*(ago)?$`)
var yesterdayRe = regexp.MustCompile(`^yesterday\s+at\s+(.+)$`)

var nowish = map[string]bool{
	"just now": true, "now": true, "just posted": true,
	"recently": true, "just shared": true,
}

// ParseTimestamp mirrors parser.py parse_timestamp(raw, now=None).
// Handles data-utime epochs, relative strings, "Yesterday at ..." and the
// absolute formats from _ABS_FORMATS.  Naive strings are stored as UTC.
func ParseTimestamp(raw string, now time.Time) *time.Time {
	if raw == "" {
		return nil
	}
	s := collapse(raw)
	if s == "" {
		return nil
	}
	low := strings.ToLower(s)

	if nowish[low] {
		t := now
		return &t
	}

	// plain epoch (from data-utime / int-like strings)
	if epochRe.MatchString(s) {
		if t := fromTimestampFloat(s); t != nil {
			return t
		}
	}

	// relative: "<n> <unit> [ago]"
	if m := relativeRe.FindStringSubmatch(low); m != nil {
		amount, aerr := strconv.Atoi(m[1])
		unit := strings.TrimRight(m[2], "s")
		if aerr == nil {
			if seconds, ok := relativeUnitSeconds[unit]; ok && amount > 0 && amount < 100000 {
				t := now.Add(-time.Duration(amount) * time.Duration(seconds) * time.Second)
				return &t
			}
		}
	}

	// "Yesterday at ..." (Python strptime %p accepts lower-case am/pm and
	// Go's 3:04 PM does not, so the clock is upper-cased first)
	if m := yesterdayRe.FindStringSubmatch(low); m != nil {
		if clock, ok := parseClock(strings.ToUpper(m[1])); ok {
			yesterday := now.AddDate(0, 0, -1)
			t := time.Date(yesterday.Year(), yesterday.Month(), yesterday.Day(),
				clock.Hour(), clock.Minute(), 0, 0, time.UTC)
			return &t
		}
	}

	// absolute formats
	return parseAbs(s, now)
}

// fromTimestampFloat mirrors datetime.fromtimestamp(float(s), tz=utc) with
// Python's 1..9999 year bounds (values outside raise OverflowError -> None).
func fromTimestampFloat(s string) *time.Time {
	f, err := strconv.ParseFloat(s, 64)
	if err != nil || math.IsNaN(f) || math.IsInf(f, 0) {
		return nil
	}
	return fromTimestampFloatVal(f)
}

// fromTimestampFloatVal mirrors datetime.fromtimestamp(f, tz=utc) with
// Python's 1..9999 year bounds (values outside -> OverflowError -> nil).
func fromTimestampFloatVal(f float64) *time.Time {
	sec := math.Floor(f)
	nsec := (f - sec) * 1e9
	t := time.Unix(int64(sec), int64(nsec)).UTC()
	if t.Year() < 1 || t.Year() > 9999 {
		return nil
	}
	return &t
}

func intPtr(v int64) *int64   { return &v }
func strPtr(v string) *string { return &v }
