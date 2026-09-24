// Package httpapi is the Phase-1 HTTP surface of the Go compute worker
// (finalplanv2.md §5 "Go (Compute)" + §6 inter-service communication).
//
// It is deliberately a thin adapter: request validation, base64 decode,
// content-type dispatch to the hermetic parser (golang/parser, M2), the
// normalize+dedup slice-A pipeline (golang/worker), the §8(c) idempotency
// guard (golang/idempotency, M4), and Python-format JSON response bytes.
// All interesting bytes live in those packages; this one only wires them to
// HTTP and proves the wire shape against real-Python goldens
// (testdata/gen_goldens.py + *_test.go).
//
// Phase 1 transport is plain HTTP + JSON (finalplanv2.md §6); the Parse RPC
// hangs off POST /v1/parse with the contract.ParseRequest/ParseResponse
// shapes from shared/proto/postharvest.proto.  /healthz and /readyz
// delegate to golang/health.go so Kubernetes (Phase 4) has probes from
// day one.
package httpapi

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"

	worker "postharvest/golang"
	"postharvest/golang/contract"
	"postharvest/golang/idempotency"
	"postharvest/golang/parser"
)

// maxRequestBody caps one ParseRequest body.  Browser-mode snapshots can be
// several MiB, so the cap is generous; it bounds memory and is not a
// policy limit.
const maxRequestBody = 64 << 20 // 64 MiB

// parsePath is the Phase-1 Parse RPC route (finalplanv2.md §6).
const parsePath = "/v1/parse"

// cacheHitHeader is a non-contract observability header telling callers
// whether a ParseResponse came from the §8(c) idempotency cache ("hit") or
// fresh work ("miss").  It exists so retry diagnostics (and M7 client tests)
// can see the cache working without parsing the body.
const cacheHitHeader = "X-PostHarvest-Cache"

// Clock is an injectable wall clock so golden tests are deterministic
// (mirrors parser.py's `now` parameter).  nil means time.Now.
type Clock func() time.Time

// Prometheus metrics for the HTTP surface (plans/monitoring.md): request
// count by handler/status plus a handler-latency histogram, registered on
// the default registry so the promhttp.Handler() at /metrics serves them.
// Like every other /metrics in the stack they are never proxied by nginx
// and never published (ADRs D15/D16).
var (
	requestTotal = prometheus.NewCounterVec(
		prometheus.CounterOpts{
			Name: "postharvest_go_requests_total",
			Help: "HTTP requests served by the go worker, by handler and status.",
		},
		[]string{"handler", "status"},
	)
	requestDuration = prometheus.NewHistogramVec(
		prometheus.HistogramOpts{
			Name:    "postharvest_go_request_duration_seconds",
			Help:    "HTTP request latency in seconds for the go worker handlers.",
			Buckets: prometheus.DefBuckets,
		},
		[]string{"handler"},
	)
)

func init() {
	prometheus.MustRegister(requestTotal, requestDuration)
}

// statusRecorder captures the response status for the metrics middleware
// (handlers that never call WriteHeader leave the 200 default).
type statusRecorder struct {
	http.ResponseWriter
	status int
}

func (r *statusRecorder) WriteHeader(code int) {
	r.status = code
	r.ResponseWriter.WriteHeader(code)
}

// instrument observes request count + latency for every request the mux
// serves, bucketing by route family so the dashboard stays stable.
func instrument(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		started := time.Now()
		rec := &statusRecorder{ResponseWriter: w, status: http.StatusOK}
		next.ServeHTTP(rec, r)
		handler := "other"
		switch r.URL.Path {
		case parsePath:
			handler = "parse"
		case "/healthz", "/readyz":
			handler = "health"
		case "/metrics":
			handler = "metrics"
		}
		requestTotal.WithLabelValues(handler, strconv.Itoa(rec.status)).Inc()
		requestDuration.WithLabelValues(handler).Observe(time.Since(started).Seconds())
	})
}

// Outcome is one ParseRequest processed to completion: canonical 33-key
// posts after normalize+dedup, plus isolated per-source parse failures.
type Outcome struct {
	Posts  []map[string]interface{}
	Errors []string
}

// DecodeRequest validates the request-level fields (content_type whitelist,
// raw_payload base64) and returns the decoded payload.  Errors here are
// request-level 400s; per-post parse failures are never this function's.
// The handler uses it both as the validation gate before the idempotency
// lookup and as the payload source, so a large payload is decoded exactly
// once.
func DecodeRequest(req contract.ParseRequest) ([]byte, error) {
	switch req.ContentType {
	case "html", "graphql_json":
	default:
		return nil, fmt.Errorf(
			"unsupported content_type %q (want \"html\" or \"graphql_json\")",
			req.ContentType)
	}
	payload, err := base64.StdEncoding.DecodeString(req.RawPayload)
	if err != nil {
		return nil, fmt.Errorf("raw_payload is not valid base64: %v", err)
	}
	return payload, nil
}

// Process runs the Parse pipeline for one request.  It validates
// (DecodeRequest) and returns an error only for request-level problems
// (unknown content_type, undecodable base64); per-post parse failures are
// isolated into Outcome.Errors (finalplanv2.md §6 fault isolation), exactly
// like ParseResponse.errors.
func Process(req contract.ParseRequest, now time.Time) (Outcome, error) {
	payload, err := DecodeRequest(req)
	if err != nil {
		return Outcome{}, err
	}
	return processPayload(req, payload, now)
}

// processPayload runs the pipeline on an already-decoded, validated request.
func processPayload(req contract.ParseRequest, payload []byte, now time.Time) (Outcome, error) {
	var parsed []*worker.ParsedPost
	var errors []string
	var pageName, pageID *string
	switch req.ContentType {
	case "html":
		// Mirrors crawler.py: parse_page(html, page_url=final_url,
		// handle=...) then normalize_post(page_name=page.page_name,
		// page_id=page.page_id, facebook_url=<page url>).
		page := parser.ParsePage(string(payload), req.TargetURL, now, req.Handle)
		parsed = page.Posts
		pageName, pageID = emptyToNil(page.PageName), emptyToNil(page.PageID)
		for _, e := range page.PostErrors {
			errors = append(errors, postErrorString(e))
		}
	case "graphql_json":
		// Mirrors extract_posts_from_graphql: no page-level metadata in the
		// payload, so page_name/page_id stay null; facebook_url context is
		// the request's target_url.
		parsed = parser.ExtractPostsFromGraphQL(string(payload), req.TargetURL)
	}

	res := worker.ProcessRaw(parsed, pageName, pageID,
		emptyToNil(req.TargetURL), now)
	return Outcome{Posts: res.Posts, Errors: errors}, nil
}

// server carries the injectable clock and the §8(c) idempotency store for
// the HTTP handler.
type server struct {
	now  Clock
	idem idempotency.Store
}

// NewHandler returns the Phase-1 HTTP surface: POST /v1/parse plus the
// /healthz and /readyz probes (delegating to golang/health.go).  Passing a
// nil store disables the §8(c) idempotency cache (single-shot/hermetic
// mode); with a store, requests carrying a non-empty idempotency_key get
// retry-safe cached responses per finalplanv2.md §8(c).
func NewHandler(clock Clock, store idempotency.Store) http.Handler {
	if clock == nil {
		clock = time.Now
	}
	s := &server{now: clock, idem: store}
	mux := http.NewServeMux()
	mux.HandleFunc("POST "+parsePath, s.handleParse)
	// Non-POST methods on the route get a JSON 405 instead of Go's plain
	// text method-not-allowed, so every error body has the same shape.
	mux.HandleFunc(parsePath, func(w http.ResponseWriter, r *http.Request) {
		writeError(w, http.StatusMethodNotAllowed,
			"method not allowed; POST required")
	})
	health := worker.NewHandler()
	mux.Handle("/healthz", health)
	mux.Handle("/readyz", health)
	// Prometheus exposition (plans/monitoring.md): process + Go runtime
	// metrics from the default registry, plus the request count/latency
	// collectors instrumented below. Never published outside the compose
	// networks (like every other /metrics in the stack).
	mux.Handle("/metrics", promhttp.Handler())
	return instrument(mux)
}

func (s *server) handleParse(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, maxRequestBody)
	var req contract.ParseRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest,
			"invalid ParseRequest JSON: "+err.Error())
		return
	}
	payload, err := DecodeRequest(req)
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}

	// §8(c): check the idempotency cache before doing real work.  A hit
	// returns the exact cached ParseResponse bytes — no re-parse.  The
	// cache is best-effort: a store error is treated as a miss so the
	// worker keeps parsing when Redis is down (availability over dedup).
	if s.idem != nil && req.IdempotencyKey != "" {
		if cached, hit, err := s.idem.Get(r.Context(), req.IdempotencyKey); err == nil && hit {
			w.Header().Set("Content-Type", "application/json; charset=utf-8")
			w.Header().Set(cacheHitHeader, "hit")
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write(cached)
			return
		}
	}

	out, err := processPayload(req, payload, s.now())
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	body, err := marshalParseResponse(out.Posts, out.Errors)
	if err != nil {
		writeError(w, http.StatusInternalServerError,
			"failed to serialize response: "+err.Error())
		return
	}

	// Cache-write is best-effort too — it never fails or stalls the
	// response (§8(c): short TTL, not a durability store).
	if s.idem != nil && req.IdempotencyKey != "" {
		_ = s.idem.Set(r.Context(), req.IdempotencyKey, body, idempotency.TTL)
	}

	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set(cacheHitHeader, "miss")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(body)
}

// marshalParseResponse renders the ParseResponse JSON with Python's
// json.dumps(ensure_ascii=False) default separators (', ', ': ') so the
// bytes are identical to what a Python emitter would produce — asserted
// byte-for-byte against the goldens in testdata/.
func marshalParseResponse(posts []map[string]interface{}, errors []string) ([]byte, error) {
	var b bytes.Buffer
	b.WriteString(`{"posts": [`)
	for i, p := range posts {
		if i > 0 {
			b.WriteString(", ")
		}
		s, err := worker.PostDictString(p)
		if err != nil {
			return nil, err
		}
		b.WriteString(s)
	}
	b.WriteString(`], "errors": [`)
	for i, e := range errors {
		if i > 0 {
			b.WriteString(", ")
		}
		s, err := worker.PythonJSON(e)
		if err != nil {
			return nil, err
		}
		b.WriteString(s)
	}
	b.WriteString(`]}`)
	return b.Bytes(), nil
}

// postErrorString renders one parser.ParsedPage.post_errors entry exactly as
// Python's crawler appends it per failed post ({post_url, code, message}
// insertion order, json.dumps default separators).
func postErrorString(e parser.PostError) string {
	return `{"post_url": ` + pythonJSON(e.PostURL) +
		`, "code": ` + pythonJSON(e.Code) +
		`, "message": ` + pythonJSON(e.Message) + `}`
}

// pythonJSON marshals one scalar Python-style; marshalPythonJSON is total
// for scalars (never fails), so the error is intentionally fatal.
func pythonJSON(s string) string {
	v, err := worker.PythonJSON(s)
	if err != nil {
		panic("httpapi: pythonJSON failed on scalar: " + err.Error())
	}
	return v
}

func writeError(w http.ResponseWriter, status int, msg string) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_, _ = io.WriteString(w, `{"error": `+pythonJSON(msg)+`}`)
}

// emptyToNil maps Go's "" (nil-able value convention in golang/parser) to a
// nil pointer, matching Python's None.
func emptyToNil(s string) *string {
	if s == "" {
		return nil
	}
	return &s
}
