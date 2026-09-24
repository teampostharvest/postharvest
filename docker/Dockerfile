# syntax=docker/dockerfile:1
#
# PostHarvest — single Dockerfile, two build targets.
#
#   Backend image:   docker build --target backend  -t postharvest-backend  .
#   Frontend image:  docker build --target frontend -t postharvest-frontend .
#
# docker-compose.yml builds both images from this file via `target:`.
#
# Notes
# -----
# * The frontend target produces a Next.js **standalone** server
#   (`output: "standalone"` is already set in frontend/next.config.mjs), so the
#   runtime image is tiny and contains no source tree or node_modules beyond
#   the minimised production bundle Next emits.
# * NEXT_PUBLIC_* variables are inlined by Next.js at BUILD time — changing
#   the API URL requires a rebuild (see README "Configuration").
# * Both runtime images run as a non-root user (backend uid 10001, node uid
#   1000). `docker compose` mounts a **named volume** at /app/data so the
#   container can always write, regardless of host uid.
#
# Build order in docker-compose:
#   backend  -> target `backend`  (python:3.13-slim, FastAPI on :8000)
#   frontend -> target `frontend` (node:22-alpine, Next.js on :3000)

# =====================================================================
# STAGE: frontend-build — compile the Next.js app (build-time only)
# =====================================================================
FROM node:22-alpine AS frontend-build
WORKDIR /app

# NEXT_PUBLIC_* values are inlined by Next.js at build time.
# docker-compose passes them via build.args; the defaults match local dev.
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ARG NEXT_PUBLIC_SITE_URL=http://localhost:3000
ARG NEXT_PUBLIC_FIREBASE_API_KEY=
ARG NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN=postharvest-firebase.firebaseapp.com
ARG NEXT_PUBLIC_FIREBASE_PROJECT_ID=postharvest-firebase
ARG NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET=postharvest-firebase.firebasestorage.app
ARG NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID=
ARG NEXT_PUBLIC_FIREBASE_APP_ID=
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL} \
    NEXT_PUBLIC_SITE_URL=${NEXT_PUBLIC_SITE_URL} \
    NEXT_PUBLIC_FIREBASE_API_KEY=${NEXT_PUBLIC_FIREBASE_API_KEY} \
    NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN=${NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN} \
    NEXT_PUBLIC_FIREBASE_PROJECT_ID=${NEXT_PUBLIC_FIREBASE_PROJECT_ID} \
    NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET=${NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET} \
    NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID=${NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID} \
    NEXT_PUBLIC_FIREBASE_APP_ID=${NEXT_PUBLIC_FIREBASE_APP_ID}
ENV NEXT_TELEMETRY_DISABLED=1

# `npm ci` gives reproducible, lockfile-exact installs (package-lock.json is
# committed). Sources are copied path-by-path so local node_modules / .env /
# .next are never swept in (see .dockerignore at the repo root).
COPY frontend/package.json frontend/package-lock.json \
     frontend/tsconfig.json frontend/next-env.d.ts \
     frontend/next.config.mjs frontend/postcss.config.mjs \
     frontend/eslint.config.mjs ./
RUN npm ci

COPY frontend/app ./app
COPY frontend/components ./components
COPY frontend/lib ./lib
COPY frontend/public ./public
# vitest-setup.ts intentionally lands in the build stage: tsconfig's `include`
# sweeps **/*.ts, and without its @testing-library/jest-dom/vitest import the
# jest-dom matcher types used by __tests__/* fail `next build`'s type check.
# (It is not copied into the runtime stage.)
COPY frontend/vitest-setup.ts ./
RUN npm run build

# =====================================================================
# STAGE: frontend — self-contained standalone runtime (Node 22)
# =====================================================================
FROM node:22-alpine AS frontend
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV HOSTNAME=0.0.0.0
ENV PORT=3000

# Standalone output bundles server.js plus a minimal node_modules.
COPY --from=frontend-build --chown=node:node /app/.next/standalone ./
# Static assets and public files must sit next to the standalone root.
COPY --from=frontend-build --chown=node:node /app/.next/static ./.next/static
COPY --from=frontend-build --chown=node:node /app/public ./public

USER node
EXPOSE 3000
CMD ["node", "server.js"]

# =====================================================================
# STAGE: frontend-dev — `next dev` with hot reload (local development)
# =====================================================================
# Runs on top of frontend-build so node_modules + the source tree are present.
# The compose dev override bind-mounts ./frontend over /app and keeps
# node_modules/.next in volumes, so `next dev` picks up edits instantly.
FROM frontend-build AS frontend-dev
ENV NODE_ENV=development
ENV NEXT_TELEMETRY_DISABLED=1
ENV HOSTNAME=0.0.0.0
ENV PORT=3000
EXPOSE 3000
CMD ["npm", "run", "dev", "--", "--hostname", "0.0.0.0"]

# =====================================================================
# STAGE: backend — FastAPI + scraper + exporters (Python 3.13)
# =====================================================================
FROM python:3.13-slim AS backend
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# `useradd` lives in the `passwd` package, which is NOT present in slim
# images. Everything else in the toolchain (lxml wheels etc.) is prebuilt,
# so no build-essential is required.
RUN apt-get update \
    && apt-get install -y --no-install-recommends passwd \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 appuser

# Install pinned dependencies first (stable layer for caching).
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install -r backend/requirements.txt

# Install the Chromium browser for Playwright. `install --with-deps` handles
# both the browser binary and its system libraries (libnss3, libatk, etc.) via
# apt, so headless rendering works on python:slim. Browsers land in a dedicated
# dir baked into the image; the browser itself is never downloaded at runtime.
ENV PLAYWRIGHT_BROWSERS_PATH=/app/ms-playwright
RUN mkdir -p /app/ms-playwright \
    && chown appuser:appuser /app/ms-playwright \
    && playwright install --with-deps chromium 2>&1 \
    && chown -R appuser:appuser /app/ms-playwright

# Ship the application package (uvicorn imports `backend.main:app` from /app).
# Copy the whole tree in one shot: multi-source `COPY a b c ./dest/` FLATTENS
# each directory's contents into ./dest (no api/… subdirs), which breaks
# `backend.*` imports. A single `COPY backend/ ./backend/` preserves the
# package layout. .dockerignore strips __pycache__/.pytest_cache/.venv extras.
COPY --chown=appuser:appuser backend/ ./backend/

# Runtime data directory. `Settings.ensure_dirs()` also creates it on
# startup, but we pre-create + pre-chown it so the non-root user can write
# into a fresh (root-owned first-use) named volume.
RUN mkdir -p /app/data && chown appuser:appuser /app/data

# Job state lives in in-process worker threads, so this must stay a SINGLE
# process — never scale with `--workers > 1` or multiple replicas behind the
# same database, or in-flight job tracking will diverge per replica.
USER appuser
EXPOSE 8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]

# =====================================================================
# STAGE: backend-dev — uvicorn --reload (local development)
# =====================================================================
# Reuses the full backend image (deps + Playwright + Chromium); the compose
# dev override bind-mounts ./backend over /app/backend so edits hot-reload.
# Runs as root so the reloader watchdog + bind-mount writes never hit
# permission walls. Never use this stage in production.
FROM backend AS backend-dev
USER root
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
EXPOSE 8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# =====================================================================
# Legacy shorthand targets kept for any tooling that referenced the plain
# stage names above; prefer the compose `target:` pins.
# =====================================================================