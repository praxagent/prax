# Prax — consolidated single-container image.
# Bundles: Flask app, TeamWork (FastAPI + React), Qdrant, Neo4j, ngrok.
#
# Build:
#   docker compose build prax
# The compose file sets additional_contexts so the TeamWork repo is available.
#
# Architecture: supports both amd64 and arm64 via TARGETARCH.

# ─────────────────────────────────────────────────────────────────────
# Stage 1: Build TeamWork React frontend
# ─────────────────────────────────────────────────────────────────────
FROM node:22-slim AS frontend-build

WORKDIR /build/frontend

# Install deps first (layer cache)
COPY --from=teamwork frontend/package.json frontend/package-lock.json* ./
RUN npm ci

# Copy frontend source + Python package source (vite build outputs into src/teamwork/static/)
COPY --from=teamwork frontend/ ./
COPY --from=teamwork src/ /build/src/
COPY --from=teamwork pyproject.toml /build/

RUN npx vite build


# ─────────────────────────────────────────────────────────────────────
# Stage 2: Combined Prax image
# ─────────────────────────────────────────────────────────────────────
FROM python:3.13-slim

# TARGETARCH is auto-set by BuildKit (amd64 | arm64)
ARG TARGETARCH

# ── 1. System packages ──────────────────────────────────────────────
RUN apt-get update -qq && apt-get install -y --no-install-recommends \
    git ffmpeg curl hugo ca-certificates gnupg gosu \
    supervisor \
    imagemagick \
    texlive-latex-base texlive-latex-extra texlive-latex-recommended texlive-fonts-recommended \
    texlive-science lmodern cm-super \
    # Chromium runtime deps + fonts (Playwright --with-deps broken on Trixie)
    fonts-unifont fonts-noto-color-emoji fonts-liberation \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 libgbm1 \
    libpango-1.0-0 libcairo2 libasound2 libxshmfence1 \
    # Docker CLI (for sandbox exec)
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg \
       | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && chmod a+r /etc/apt/keyrings/docker.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
       https://download.docker.com/linux/debian $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
       > /etc/apt/sources.list.d/docker.list \
    && apt-get update -qq && apt-get install -y --no-install-recommends docker-ce-cli \
    # gh CLI
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
       | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
       > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update -qq && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/* \
    && sed -i 's/rights="none" pattern="PDF"/rights="read|write" pattern="PDF"/' /etc/ImageMagick-6/policy.xml 2>/dev/null || true

# ── 2. Java 21 headless (for Neo4j) ─────────────────────────────────
RUN apt-get update -qq \
    && apt-get install -y --no-install-recommends openjdk-21-jdk-headless \
    && rm -rf /var/lib/apt/lists/*

# ── 3. Neo4j Community 5.x ──────────────────────────────────────────
ENV NEO4J_HOME=/opt/neo4j
RUN curl -sSL "https://neo4j.com/artifact.php?name=neo4j-community-5.26.4-unix.tar.gz" \
    -o /tmp/neo4j.tar.gz \
    && mkdir -p /opt/neo4j \
    && tar xzf /tmp/neo4j.tar.gz --strip-components=1 -C /opt/neo4j \
    && rm /tmp/neo4j.tar.gz \
    # Accept license, disable telemetry
    && echo "server.directories.data=/data/neo4j" >> $NEO4J_HOME/conf/neo4j.conf \
    && echo "dbms.usage_report.enabled=false" >> $NEO4J_HOME/conf/neo4j.conf

# ── 4. Qdrant binary ────────────────────────────────────────────────
RUN QDRANT_ARCH="" \
    && if [ "$TARGETARCH" = "arm64" ]; then QDRANT_ARCH="aarch64-unknown-linux-gnu"; \
       else QDRANT_ARCH="x86_64-unknown-linux-gnu"; fi \
    && curl -sSL "https://github.com/qdrant/qdrant/releases/download/v1.13.2/qdrant-${QDRANT_ARCH}.tar.gz" \
       -o /tmp/qdrant.tar.gz \
    && tar xzf /tmp/qdrant.tar.gz -C /usr/local/bin/ \
    && rm /tmp/qdrant.tar.gz \
    && chmod +x /usr/local/bin/qdrant

# ── 5. ngrok ─────────────────────────────────────────────────────────
RUN NGROK_ARCH="" \
    && if [ "$TARGETARCH" = "arm64" ]; then NGROK_ARCH="arm64"; \
       else NGROK_ARCH="amd64"; fi \
    && curl -fsSL "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-${NGROK_ARCH}.tgz" \
       | tar xz -C /usr/local/bin

# ── 6. uv ───────────────────────────────────────────────────────────
COPY --from=ghcr.io/astral-sh/uv@sha256:e49fde5daf002023f0a2e2643861ce9ca8a8da5b73d0e6db83ef82ff99969baf /uv /usr/local/bin/uv

# ── 7. Trust all git directories (host-mounted volumes) ─────────────
RUN git config --global --add safe.directory '*'

WORKDIR /app

# ── 8. Install TeamWork Python package ──────────────────────────────
COPY --from=teamwork src/ /build/teamwork/src/
COPY --from=teamwork pyproject.toml /build/teamwork/pyproject.toml
COPY --from=teamwork README.md /build/teamwork/README.md
# Copy the built frontend into the package's static dir
COPY --from=frontend-build /build/src/teamwork/static/ /build/teamwork/src/teamwork/static/
RUN pip install --no-cache-dir /build/teamwork && rm -rf /build/teamwork

# ── 9. Install Prax Python app ──────────────────────────────────────
RUN mkdir /app/logs

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev

COPY . .

# Ensure supervisord launchers are executable (lost on COPY from some hosts).
RUN chmod +x scripts/entrypoint-combined.sh scripts/entrypoint-lite.sh \
             scripts/ngrok-launch.sh scripts/watchdog-launch.sh \
             scripts/teamwork-launch.sh

# ── 10. Patchright + NLTK ───────────────────────────────────────────
RUN uv run patchright install chromium
RUN uv run python -m nltk.downloader punkt

# ── Data directories ────────────────────────────────────────────────
RUN mkdir -p /data/qdrant /data/neo4j /data/teamwork

EXPOSE 5001 8000 4040 6333 6334 7474 7687

ENV FLASK_APP=app.py
ENV FLASK_RUN_HOST=0.0.0.0

ENTRYPOINT ["bash", "scripts/entrypoint-combined.sh"]
