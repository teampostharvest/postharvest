// Command server is the deployable go worker process (finalplanv2.md §5
// "Go (Compute)" / §6): one binary that mounts the Phase-1 HTTP surface
// (POST /v1/parse + /healthz + /readyz, via golang/httpapi) and wires the
// §8(c) idempotency store from the environment.
//
// Configuration (env):
//
//	PORT      listen port (default 8080)
//	REDIS_URL when set and parseable, the §8(c) idempotency cache backs
//	          onto Redis ("redis://..."); when unset/empty the worker runs
//	          single-shot (no cache), which is exactly the hermetic mode the
//	          golden byte-parity proofs exercise.  A store failure NEVER
//	          fails a parse — errors are best-effort misses (availability
//	          over dedup, golang/httpapi).
//
// The worker is deliberately stateless and makes no outbound network calls
// to Facebook or the database (§5): the only optional external touch is the
// idempotency cache.
package main

import (
	"log"
	"net/http"
	"os"
	"time"

	"postharvest/golang/httpapi"
	"postharvest/golang/idempotency"
)

const (
	defaultPort = "8080"

	// readHeaderTimeout guards against slowloris-style header stalls; idle
	// keeps keep-alive sockets from hanging around after a deploy drains.
	readHeaderTimeout = 5 * time.Second
	idleTimeout       = 60 * time.Second
)

func main() {
	port := os.Getenv("PORT")
	if port == "" {
		port = defaultPort
	}

	store := idempotencyStoreFromEnv()

	handler := httpapi.NewHandler(nil, store)
	addr := ":" + port
	srv := &http.Server{
		Addr:              addr,
		Handler:           handler,
		ReadHeaderTimeout: readHeaderTimeout,
		IdleTimeout:       idleTimeout,
	}

	mode := "single-shot (no idempotency cache)"
	if store != nil {
		mode = "redis-backed idempotency cache"
	}
	log.Printf("go worker listening on %s (%s)", addr, mode)
	if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Fatal(err)
	}
}

// idempotencyStoreFromEnv builds the §8(c) store from REDIS_URL, or returns
// nil (single-shot) when unset.  An unparsable DSN is fatal at boot — it is
// a configuration error, not a transient Redis outage (those surface later
// as best-effort misses).
func idempotencyStoreFromEnv() idempotency.Store {
	raw := os.Getenv("REDIS_URL")
	if raw == "" {
		return nil
	}
	store, err := idempotency.NewRedisFromURL(raw)
	if err != nil {
		log.Fatalf("bad REDIS_URL: %v", err)
	}
	return store
}
