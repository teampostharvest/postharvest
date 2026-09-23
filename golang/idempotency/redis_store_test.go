// Hermetic proofs for the Redis §8(c) store (deployment implementation of
// the Store seam).  Zero sockets: the store is driven against a recording
// double of the go-redis surface (idempotency.Cmdable), so these lock the
// *contract* the deployment store must keep — keyspace prefixing, miss vs
// error semantics, TTL propagation — without assuming anything about RESPs
// or a real server.  go-redis's own wire behavior is upstream-tested.
package idempotency

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

// recordingCmdable records the commands a store issues and answers Get/Set
// from an in-process map, honoring the redis.Nil miss convention.
type recordingCmdable struct {
	data map[string][]byte
	ttls map[string]time.Duration
	gets []string // full keys (CacheKey applied)
	sets []setCall
	fail error // when set, every command returns this error
}

type setCall struct {
	key string
	val string
	ttl time.Duration
}

func newRecording() *recordingCmdable {
	return &recordingCmdable{data: map[string][]byte{}, ttls: map[string]time.Duration{}}
}

func (r *recordingCmdable) Get(ctx context.Context, key string) *redis.StringCmd {
	r.gets = append(r.gets, key)
	cmd := redis.NewStringCmd(ctx, "GET", key)
	if r.fail != nil {
		cmd.SetErr(r.fail)
		return cmd
	}
	val, ok := r.data[key]
	if !ok {
		cmd.SetErr(redis.Nil)
		return cmd
	}
	cmd.SetVal(string(val))
	return cmd
}

func (r *recordingCmdable) Set(ctx context.Context, key string, value interface{}, expiration time.Duration) *redis.StatusCmd {
	// The store sends []byte (marshaled ParseResponse); go-redis would write
	// it verbatim.  Record a copy so later mutation can't corrupt the double.
	raw, ok := value.([]byte)
	if !ok {
		raw = []byte(value.(string))
	}
	r.sets = append(r.sets, setCall{key: key, val: string(raw), ttl: expiration})
	cmd := redis.NewStatusCmd(ctx, "SET", key)
	if r.fail != nil {
		cmd.SetErr(r.fail)
		return cmd
	}
	r.data[key] = append([]byte(nil), raw...)
	r.ttls[key] = expiration
	cmd.SetVal("OK")
	return cmd
}

func TestRedisStoreGetMissUsesRedisNil(t *testing.T) {
	c := newRecording()
	r := NewRedis(c)
	got, ok, err := r.Get(context.Background(), "job_7:acmewidgets_1")
	if err != nil || ok || got != nil {
		t.Fatalf("missing key: ok=%v err=%v data=%v", ok, err, got)
	}
	// The store must address the idemp: keyspace, never a raw key.
	if len(c.gets) != 1 || c.gets[0] != "idemp:job_7:acmewidgets_1" {
		t.Fatalf("GET key = %v, want [idemp:job_7:acmewidgets_1]", c.gets)
	}
}

func TestRedisStoreRoundTrip(t *testing.T) {
	c := newRecording()
	r := NewRedis(c)
	if err := r.Set(context.Background(), "job_7:acmewidgets_1", []byte(`{"posts":[]}`), TTL); err != nil {
		t.Fatalf("Set: %v", err)
	}
	if len(c.sets) != 1 {
		t.Fatalf("SET calls = %d, want 1", len(c.sets))
	}
	call := c.sets[0]
	if call.key != "idemp:job_7:acmewidgets_1" {
		t.Fatalf("SET key = %q, want idemp: job_7:acmewidgets_1", call.key)
	}
	// The five-minute TTL from the contract must propagate to Redis (SETEX).
	if call.ttl != TTL {
		t.Fatalf("SET ttl = %v, want %v", call.ttl, TTL)
	}
	got, ok, err := r.Get(context.Background(), "job_7:acmewidgets_1")
	if err != nil || !ok || string(got) != `{"posts":[]}` {
		t.Fatalf("round trip: ok=%v err=%v data=%q", ok, err, got)
	}
}

func TestRedisStoreGetSurfacesErrors(t *testing.T) {
	boom := errors.New("conn refused")
	c := newRecording()
	c.fail = boom
	r := NewRedis(c)
	if _, _, err := r.Get(context.Background(), "k"); !errors.Is(err, boom) {
		t.Fatalf("Get error = %v, want the store error surfaced (caller treats as miss)", err)
	}
	if err := r.Set(context.Background(), "k", []byte("v"), TTL); !errors.Is(err, boom) {
		t.Fatalf("Set error = %v, want surfaced", err)
	}
}

func TestRedisStoreParseURLRejectsGarbage(t *testing.T) {
	if _, err := NewRedisFromURL("://not-a-dsn"); err == nil {
		t.Fatal("NewRedisFromURL must reject an unparsable DSN")
	}
}

func TestRedisStoreKeysAreIsolated(t *testing.T) {
	c := newRecording()
	r := NewRedis(c)
	if _, ok, _ := r.Get(context.Background(), "a:1"); ok {
		t.Fatal("a:1 must miss before Set")
	}
	if err := r.Set(context.Background(), "a:1", []byte("A"), TTL); err != nil {
		t.Fatalf("Set a:1: %v", err)
	}
	if err := r.Set(context.Background(), "a", []byte("root"), TTL); err != nil {
		t.Fatalf("Set a: %v", err)
	}
	// 'a' and 'a:1' are distinct keys — no prefix collision in the idemp: set.
	got, ok, _ := r.Get(context.Background(), "a:1")
	if !ok || string(got) != "A" {
		t.Fatalf("a:1 = %q (ok=%v), want A", got, ok)
	}
}
