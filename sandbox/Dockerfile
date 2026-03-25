# Pin to digest to prevent supply-chain tag hijacking.
# To update: docker pull node:22-slim && docker inspect --format='{{index .RepoDigests 0}}' node:22-slim
FROM node:22-slim@sha256:4f77a690f2f8946ab16fe1e791a3ac0667ae1c3575c3e4d0d4589e9ed5bfaf3d

RUN apt-get update -qq && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv \
    texlive-latex-base texlive-latex-extra texlive-latex-recommended \
    texlive-fonts-recommended texlive-fonts-extra \
    texlive-science texlive-bibtex-extra texlive-publishers \
    lmodern cm-super latexmk biber \
    ffmpeg poppler-utils pandoc hugo \
    git curl wget jq \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g opencode-ai

WORKDIR /workspace

RUN mkdir -p /root/.config/opencode
COPY opencode.json /root/.config/opencode/opencode.json

EXPOSE 4096

HEALTHCHECK --interval=3s --timeout=2s --start-period=15s --retries=5 \
    CMD curl -sf http://localhost:4096/global/health > /dev/null || exit 1

CMD ["opencode", "serve", "--hostname", "0.0.0.0", "--port", "4096"]
