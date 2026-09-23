// Redis-backed §8(c) idempotency store — the deployment implementation of the
// Store seam (finalplanv2.md §5/§8(c); dep_notes pin
// github.com/redis/go-redis/v9).
//
// Same contract as Memory, different substrate: keys live under the shared
// "idemp:" keyspace (Namespace) so a KEYS * scan can tell subsystems apart,
// values are the cached ParseResponse bytes held for the store TTL (five
// minutes — "short TTL … this is not a durability store, Postgres already
// owns that").  A swap between Memory and Redis must NOT change the wire
// bytes cached or the retry-safety semantics proven in the hermetic proofs.
//
// Errors are surfaced to the caller and the httpapi layer treats them as
// best-effort misses (availability over dedup), exactly like Memory.
package idempotency

import (
	"context"
	"fmt"
	"time"

	"github.com/redis/go-redis/v9"
)

// Cmdable is the go-redis surface this store needs — a subset of
// redis.Cmdable.  Keeping the dependency behind this seam lets the hermetic
// tests drive the store with a recording double (zero sockets); the real
// wire behavior is go-redis's own (upstream-tested) responsibility.
type Cmdable interface {
	Get(ctx context.Context, key string) *redis.StringCmd
	Set(ctx context.Context, key string, value interface{}, expiration time.Duration) *redis.StatusCmd
}

// Redis is the deployment Store.  Not safe for concurrent use of the same
// key with different expiry expectations — callers already serialize by
// ParseRequest; a NewClient per-process is the normal shape.
type Redis struct {
	client Cmdable
}

// NewRedis wraps an existing go-redis client (or hermetic double).
func NewRedis(client Cmdable) *Redis {
	return &Redis{client: client}
}

// NewRedisFromURL parses a redis:// DSN and returns a store over a dedicated
// client.  Returns an error for unparsable DSNs; connectivity problems are
// NOT fatal here (Set errors become best-effort misses downstream).
func NewRedisFromURL(rawURL string) (*Redis, error) {
	opts, err := redis.ParseURL(rawURL)
	if err != nil {
		return nil, fmt.Errorf("parse REDIS_URL: %w", err)
	}
	return NewRedis(redis.NewClient(opts)), nil
}

// Get implements Store.  A missing key (redis.Nil) is a clean miss
// (ok=false, err=nil); any other error is surfaced so the caller can decide
// (httpapi treats it as a miss too — see its handler).
func (r *Redis) Get(ctx context.Context, key string) ([]byte, bool, error) {
	data, err := r.client.Get(ctx, CacheKey(key)).Bytes()
	switch err {
	case nil:
		return data, true, nil
	case redis.Nil:
		return nil, false, nil
	default:
		return nil, false, err
	}
}

// Set implements Store, storing under CacheKey(key) with the given TTL.
func (r *Redis) Set(ctx context.Context, key string, data []byte, ttl time.Duration) error {
	return r.client.Set(ctx, CacheKey(key), data, ttl).Err()
}
