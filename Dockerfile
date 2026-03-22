FROM python:3.13-slim

RUN apt-get update -qq && apt-get install -y --no-install-recommends \
    git ffmpeg default-jre-headless curl \
    imagemagick \
    texlive-latex-base texlive-latex-extra texlive-latex-recommended texlive-fonts-recommended \
    texlive-science lmodern cm-super \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
       | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
       > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update -qq && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/* \
    && sed -i 's/rights="none" pattern="PDF"/rights="read|write" pattern="PDF"/' /etc/ImageMagick-6/policy.xml 2>/dev/null || true

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
RUN mkdir /app/logs

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev

COPY . .

# Playwright's --with-deps references font packages renamed in Debian Trixie.
# Install Chromium runtime deps + replacement fonts ourselves, then download
# the browser binary separately.
RUN apt-get update -qq && apt-get install -y --no-install-recommends \
    fonts-unifont fonts-noto-color-emoji fonts-liberation \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 libgbm1 \
    libpango-1.0-0 libcairo2 libasound2 libxshmfence1 \
    && rm -rf /var/lib/apt/lists/*
RUN uv run playwright install chromium
RUN uv run python -m nltk.downloader punkt

EXPOSE 5000

ENV FLASK_APP=app.py
ENV FLASK_RUN_HOST=0.0.0.0

CMD ["uv", "run", "python", "app.py"]
