// Env-wiring proofs for the deployable server: REDIS_URL empty -> single-shot
// (nil store), REDIS_URL set -> the §8(c) cache is armed.  The Fatal path for
// an unparsable DSN is untestable in-process (os.Exit) and is a boot-time
// config error by design.
package main

import (
	"testing"
)

func TestStoreFromEnvEmptyIsSingleShot(t *testing.T) {
	t.Setenv("REDIS_URL", "")
	if s := idempotencyStoreFromEnv(); s != nil {
		t.Fatalf("empty REDIS_URL must yield a nil store (single-shot), got %T", s)
	}
}

func TestStoreFromEnvArmsRedisCache(t *testing.T) {
	t.Setenv("REDIS_URL", "redis://localhost:6379/0")
	s := idempotencyStoreFromEnv()
	if s == nil {
		t.Fatal("set REDIS_URL must arm the idempotency store")
	}
}
