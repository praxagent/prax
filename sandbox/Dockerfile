FROM ubuntu:26.04

ENV DEBIAN_FRONTEND=noninteractive

# ── System packages ──
RUN apt-get update -qq && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv \
    nodejs npm \
    texlive-latex-base texlive-latex-extra texlive-latex-recommended \
    texlive-fonts-recommended texlive-fonts-extra \
    texlive-science texlive-bibtex-extra texlive-publishers \
    lmodern cm-super latexmk biber \
    ffmpeg poppler-utils pandoc hugo \
    git curl wget jq tmux \
    chromium-browser socat \
    xvfb x11vnc xfce4 xfce4-terminal dbus-x11 \
    novnc websockify \
    xdotool scrot imagemagick \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# ── code-server (web-based VS Code, works on amd64 + arm64) ──
RUN curl -fsSL https://code-server.dev/install.sh | sh

# ── Coding agents + uv ──
RUN npm install -g opencode-ai @anthropic-ai/claude-code @openai/codex \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && ln -s /root/.local/bin/uv /usr/local/bin/uv \
    && ln -s /root/.local/bin/uvx /usr/local/bin/uvx

WORKDIR /workspace

# Seed config — copied to /opt/ so it survives volume mounts.
# The entrypoint seeds /root/.config/opencode/ on first run.
COPY opencode.json /opt/opencode.json

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY tmux-shell.sh /usr/local/bin/tmux-shell.sh
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/tmux-shell.sh

# Set tmux-shell as the default shell so terminal WebSocket connections
# attach to the persistent tmux session instead of spawning fresh shells.
ENV SHELL=/usr/local/bin/tmux-shell.sh

# 4096 = OpenCode serve, 9223 = CDP proxy, 6080 = noVNC web desktop, 8443 = code-server
EXPOSE 4096 9223 6080 8443

HEALTHCHECK --interval=3s --timeout=2s --start-period=15s --retries=5 \
    CMD curl -sf http://localhost:4096/global/health > /dev/null || exit 1

CMD ["/usr/local/bin/entrypoint.sh"]
