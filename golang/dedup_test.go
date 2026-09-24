// Golden byte-parity proofs for dedup: the fingerprint, id:/fp: keys and
// first-wins counts were extracted from the REAL Python dedup.py — see
// /tmp/opencode/golden_vector.py.
package worker

import (
	"reflect"
	"testing"
)

// goldenFingerprintNoID is Python make_fingerprint(normalized with
// post_id=None) -> sha256("999|1768478400|Hello #Go ... #Go").
const goldenFingerprintNoID = "2f724cacfd960cc738d0286f99da3ba06ea46e89cb2a83d58e93ea990ab4b916"

func TestMakeFingerprintGolden(t *testing.T) {
	post := NormalizePost(goldenParsed(), ptrStr("Page"), ptrStr("999"),
		ptrStr("https://www.facebook.com/Page"), goldenNow)
	noID := clonePost(post)
	noID["post_id"] = nil
	got := MakeFingerprint(noID)
	if got != goldenFingerprintNoID {
		t.Fatalf("fingerprint mismatch:\n got %s\nwant %s", got, goldenFingerprintNoID)
	}
}

func TestDedupKeyGolden(t *testing.T) {
	post := NormalizePost(goldenParsed(), ptrStr("Page"), ptrStr("999"),
		ptrStr("https://www.facebook.com/Page"), goldenNow)
	if k := DedupKey(post); k != "id:1234567890" {
		t.Fatalf("dedup_key with post_id = %q, want id:1234567890", k)
	}
	noID := clonePost(post)
	noID["post_id"] = nil
	if k := DedupKey(noID); k != "fp:"+goldenFingerprintNoID {
		t.Fatalf("dedup_key without post_id = %q, want fp:%s", k, goldenFingerprintNoID)
	}
}

func TestDedupPostsGolden(t *testing.T) {
	post := NormalizePost(goldenParsed(), ptrStr("Page"), ptrStr("999"),
		ptrStr("https://www.facebook.com/Page"), goldenNow)
	other := clonePost(post)
	other["post_id"] = ptrStr("999999")

	kept, removed := DedupPosts([]map[string]interface{}{clonePost(post), clonePost(post), other})
	if removed != 1 {
		t.Fatalf("duplicates_removed = %d, want 1", removed)
	}
	if len(kept) != 2 {
		t.Fatalf("kept = %d, want 2", len(kept))
	}
	var ids []string
	for _, p := range kept {
		ids = append(ids, *p["post_id"].(*string))
	}
	want := []string{"1234567890", "999999"}
	if !reflect.DeepEqual(ids, want) {
		t.Fatalf("kept post_ids = %v, want %v", ids, want)
	}
}

func TestDedupAcrossSourcesGolden(t *testing.T) {
	post := NormalizePost(goldenParsed(), ptrStr("Page"), ptrStr("999"),
		ptrStr("https://www.facebook.com/Page"), goldenNow)
	other := clonePost(post)
	other["post_id"] = ptrStr("999999")
	// Python golden (verified against the real dedup.py): the same-text
	// post with a new id is caught by the content fingerprint too, so
	// across two sources the total is removed=2, kept=1.
	all, removed := DedupPostsAcrossSources(
		[]map[string]interface{}{clonePost(post)},
		[]map[string]interface{}{clonePost(post), other},
	)
	if removed != 2 {
		t.Fatalf("cross-source duplicates_removed = %d, want 2", removed)
	}
	if len(all) != 1 {
		t.Fatalf("cross-source kept = %d, want 1", len(all))
	}
}

func clonePost(p map[string]interface{}) map[string]interface{} {
	out := make(map[string]interface{}, len(p))
	for k, v := range p {
		out[k] = v
	}
	return out
}
