// Hermetic proofs for the §8(c) idempotency seam: no sockets, injectable
// clock, "idemp:" keyspace.  These lock the retry-safety semantics the
// HTTP layer relies on (golang/httpapi) and that a future Redis store must
// preserve.
package idempotency

import (
	"context"
	"testing"
	"time"
)

func TestCacheKeyUsesIdempKeyspace(t *testing.T) {
	if got := CacheKey("job_7:acmewidgets_1"); got != "idemp:job_7:acmewidgets_1" {
		t.Fatalf("CacheKey = %q, want idemp:job_7:acmewidgets_1", got)
	}
	// The keyspace separation (§8) must never leak another prefix.
	if got := CacheKey("job:ratelimit:x"); got != "idemp:job:ratelimit:x" {
		t.Fatalf("CacheKey = %q, want idemp:job:ratelimit:x", got)
	}
}

func TestMemoryRoundTrip(t *testing.T) {
	m := NewMemory(nil)
	got, ok, err := m.Get(context.Background(), "job_7:acmewidgets_1")
	if err != nil || ok || got != nil {
		t.Fatalf("missing key: ok=%v err=%v data=%v", ok, err, got)
	}
	if err := m.Set(context.Background(), "job_7:acmewidgets_1", []byte(`{"posts": []}`), TTL); err != nil {
		t.Fatalf("Set: %v", err)
	}
	got, ok, err = m.Get(context.Background(), "job_7:acmewidgets_1")
	if err != nil || !ok || string(got) != `{"posts": []}` {
		t.Fatalf("round trip: ok=%v err=%v data=%q", ok, err, got)
	}
}

func TestMemoryExpiresOnRead(t *testing.T) {
	now := time.Date(2026, 9, 24, 12, 0, 0, 0, time.UTC)
	clock := func() time.Time { return now }
	m := NewMemory(clock)
	if err := m.Set(context.Background(), "k", []byte("v"), TTL); err != nil {
		t.Fatalf("Set: %v", err)
	}
	now = now.Add(TTL + time.Nanosecond) // just past expiry
	got, ok, err := m.Get(context.Background(), "k")
	if err != nil || ok || got != nil {
		t.Fatalf("expired key must miss (ok=%v err=%v)", ok, err)
	}
	// Lazy eviction removed it: the next read is a clean miss, not an
	// expired-entry re-read (indistinguishable by API, but must not panic).
	if _, _, err := m.Get(context.Background(), "k"); err != nil {
		t.Fatalf("second read after eviction: %v", err)
	}
}

func TestMemoryKeysAreIsolated(t *testing.T) {
	m := NewMemory(nil)
	if err := m.Set(context.Background(), "a:1", []byte("A"), TTL); err != nil {
		t.Fatalf("Set a: %v", err)
	}
	if err := m.Set(context.Background(), "b:1", []byte("B"), TTL); err != nil {
		t.Fatalf("Set b: %v", err)
	}
	got, ok, _ := m.Get(context.Background(), "b:1")
	if !ok || string(got) != "B" {
		t.Fatalf("b:1 = %q (ok=%v), want B", got, ok)
	}
	// A key that is a prefix of another must not collide.
	if err := m.Set(context.Background(), "a", []byte("root"), TTL); err != nil {
		t.Fatalf("Set a: %v", err)
	}
	if got, ok, _ := m.Get(context.Background(), "a:1"); !ok || string(got) != "A" {
		t.Fatalf("a:1 clobbered by key 'a': %q (ok=%v)", got, ok)
	}
}

func TestMemoryCopiesData(t *testing.T) {
	m := NewMemory(nil)
	orig := []byte("original")
	if err := m.Set(context.Background(), "k", orig, TTL); err != nil {
		t.Fatalf("Set: %v", err)
	}
	orig[0] = 'X' // mutate the caller's slice after Set
	got, ok, _ := m.Get(context.Background(), "k")
	if !ok || string(got) != "original" {
		t.Fatalf("Set must copy bytes: got %q (ok=%v)", got, ok)
	}
}
