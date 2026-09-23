# go — PostHarvest compute worker (finalplanv2.md §5 "Go (Compute)").
# Multi-stage: build the single static binary from the vendored tree, ship
# only that binary.  Self-contained build context: this Dockerfile assumes
# golang/ as context (mirrors node/Dockerfile), so every build dependency
# (go.mod/go.sum, vendor/, cmd/, the packages) is under ../golang.
#
# The worker is stateless and makes no outbound calls to Facebook or the
# database — its only optional external touch is the §8(c) idempotency
# Redis, configured at runtime via REDIS_URL.

FROM golang:1.27-alpine AS build
WORKDIR /src

# Vendor tree first (only change in the common case), then the module files,
# so dependency changes invalidate just these layers.
COPY go.mod go.sum ./
COPY vendor/ vendor/
COPY . .

# -mod=vendor: hermetic by construction — the image never needs GOPROXY.
# CGO_ENABLED=0: static binary, no libc across distroless/alpine runtimes.
RUN CGO_ENABLED=0 GOFLAGS=-mod=vendor go build -trimpath \
    -ldflags="-s -w" -o /out/go-worker ./cmd/server

FROM alpine:3.20
RUN adduser -D -u 10001 worker
COPY --from=build /out/go-worker /usr/local/bin/go-worker
USER worker
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
ENTRYPOINT ["/usr/local/bin/go-worker"]