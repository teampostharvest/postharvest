// Hermetic proof for the Phase-1 health surface: httptest.NewRecorder only
// (no sockets at all — zero network).  Plan §5 requires /healthz and
// /readyz so Kubernetes Phase-4 has something to probe.
package worker

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestHealthzReadyzOK(t *testing.T) {
	h := NewHandler()
	for _, path := range []string{"/healthz", "/readyz"} {
		rec := httptest.NewRecorder()
		req := httptest.NewRequest(http.MethodGet, path, nil)
		h.ServeHTTP(rec, req)
		if rec.Code != http.StatusOK {
			t.Fatalf("%s: status = %d, want 200", path, rec.Code)
		}
		if rec.Body.String() != "ok\n" {
			t.Fatalf("%s: body = %q, want \"ok\\n\"", path, rec.Body.String())
		}
	}
}

func TestHealthzRejectsPOST(t *testing.T) {
	h := NewHandler()
	for _, path := range []string{"/healthz", "/readyz"} {
		rec := httptest.NewRecorder()
		req := httptest.NewRequest(http.MethodPost, path, nil)
		h.ServeHTTP(rec, req)
		if rec.Code != http.StatusMethodNotAllowed {
			t.Fatalf("%s POST: status = %d, want 405", path, rec.Code)
		}
	}
}
