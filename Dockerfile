# syntax=docker/dockerfile:1
# ──────────────────────────────────────────────────────────────────────────────
# MEXC Spread Monitor — single-image deploy.
#
# The FastAPI backend already serves the built React app (mounts /assets and
# falls back to index.html for client-side routes), so one container hosts both
# the API and the UI from the same origin — no CORS, no separate web server.
#
# Stage 1 builds the frontend with Node; stage 2 is a slim Python runtime that
# serves everything on port 8006.
# ──────────────────────────────────────────────────────────────────────────────

# ─── Stage 1: build the React/Vite frontend → frontend/dist ───────────────────
FROM node:20-slim AS frontend
WORKDIR /app/frontend

# Install deps from the lockfile first for better layer caching.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# Build the production bundle.
COPY frontend/ ./
RUN npm run build


# ─── Stage 2: Python runtime that serves API + static UI ──────────────────────
FROM python:3.13-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # App reads config/data relative to CWD; keep it explicit.
    MEXC_HISTORY_DB_PATH=/app/data/spread_history.sqlite

WORKDIR /app

# curl is only needed for the container HEALTHCHECK below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Python deps first (cached until requirements.txt changes).
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Application code.
COPY backend/ ./backend/
COPY mexc_monitor/ ./mexc_monitor/
COPY config/ ./config/

# Built frontend + the public assets the backend reads at runtime
# (backend/main.py serves frontend/dist and reads frontend/public/metrics-reference.json).
COPY --from=frontend /app/frontend/dist ./frontend/dist
COPY --from=frontend /app/frontend/public ./frontend/public

# Run as an unprivileged user; data/ is a writable volume mount point.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8006

# Liveness: the app is healthy once /api/health responds.
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8006/api/health || exit 1

# Bind to 0.0.0.0 so the port is reachable outside the container.
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8006"]
