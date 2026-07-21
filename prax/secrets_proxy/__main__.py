"""Run the secrets proxy as a standalone process.

    # In the PROXY's environment (the ONLY place the real keys live):
    OPENAI_KEY=sk-real... ANTHROPIC_KEY=sk-ant-real... \
        uv run python -m prax.secrets_proxy

Then point a KEYLESS Prax at it (Prax's env has only placeholders):
    OPENAI_BASE_URL=http://127.0.0.1:8785/openai
    ANTHROPIC_BASE_URL=http://127.0.0.1:8785/anthropic
    OPENAI_KEY=proxy-placeholder      # any non-empty string; the proxy overwrites it
    ANTHROPIC_KEY=proxy-placeholder

For production use a WSGI server (gunicorn/waitress) instead of the dev server;
see docs/security/secrets-proxy.md.
"""
from __future__ import annotations

import logging

from prax.secrets_proxy.app import build_proxy_app
from prax.secrets_proxy.config import ProxyConfig


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [secrets-proxy] %(message)s")
    cfg = ProxyConfig.from_env()
    app = build_proxy_app(cfg)
    have = [n for n, up in cfg.upstreams.items() if up.real_key()]
    logging.getLogger("prax.secrets_proxy").info(
        "listening on %s:%d — keys present for: %s",
        cfg.host, cfg.port, ", ".join(have) or "(none — set OPENAI_KEY/ANTHROPIC_KEY)")
    # threaded=True so streaming responses don't block other requests. For prod,
    # front with gunicorn: `gunicorn -k gthread 'prax.secrets_proxy.app:build_proxy_app()'`.
    app.run(host=cfg.host, port=cfg.port, threaded=True)


if __name__ == "__main__":
    main()
