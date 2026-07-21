"""The secrets-proxy Flask app: inject the real key, forward, stream back.

Security properties this app must preserve (tested in test_secrets_proxy.py):
- The real key is injected server-side and is NEVER logged or returned.
- Client-supplied auth is stripped and replaced — the proxy owns auth.
- Only configured providers are reachable (allowlist by construction: an unknown
  provider prefix is a 404, so this can't be turned into an open relay).
- Responses stream (SSE passes through unbuffered), so token streaming works.
"""
from __future__ import annotations

import logging

import requests
from flask import Flask, Response, request, stream_with_context

from prax.secrets_proxy.config import ProxyConfig

logger = logging.getLogger("prax.secrets_proxy")

# Hop-by-hop headers must not be forwarded (RFC 7230 §6.1). Host/Content-Length
# are recomputed by requests; the rest are connection-scoped.
_HOP_BY_HOP = frozenset({
    "host", "content-length", "connection", "keep-alive", "proxy-authenticate",
    "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade",
    "accept-encoding",  # let requests negotiate; avoids returning compressed bytes we then re-stream
})


def _forward_headers(inbound: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in inbound.items() if k.lower() not in _HOP_BY_HOP}


def _response_headers(upstream_headers) -> list[tuple[str, str]]:
    return [(k, v) for k, v in upstream_headers.items()
            if k.lower() not in _HOP_BY_HOP and k.lower() != "content-encoding"]


def build_proxy_app(config: ProxyConfig | None = None) -> Flask:
    """Build the Flask app for the secrets proxy."""
    cfg = config or ProxyConfig()
    app = Flask("prax-secrets-proxy")

    def _audit(line: str) -> None:
        logger.info(line)
        if cfg.audit_log_path:
            try:
                with open(cfg.audit_log_path, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except Exception:  # pragma: no cover - audit must never break a call
                logger.debug("audit write failed", exc_info=True)

    @app.get("/healthz")
    def healthz():
        # Report which providers have a key configured (booleans only — no values).
        ready = {name: bool(up.real_key()) for name, up in cfg.upstreams.items()}
        return {"ok": True, "providers": ready}

    @app.route("/<provider>/<path:path>",
               methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def proxy(provider: str, path: str):
        up = cfg.upstreams.get(provider)
        if up is None:  # allowlist: unknown provider is never forwarded
            return {"error": f"unknown provider {provider!r}"}, 404
        if not up.real_key():
            return {"error": f"proxy has no key for {provider!r} "
                             f"(set {up.key_env} in the proxy's env)"}, 502

        url = f"{up.base_url}/{path}"
        if request.query_string:
            url = f"{url}?{request.query_string.decode()}"
        headers = up.inject_auth(_forward_headers(dict(request.headers)))
        body = request.get_data()  # raw bytes; LLM payloads are small JSON

        try:
            upstream = requests.request(
                request.method, url, headers=headers, data=body,
                stream=True, timeout=cfg.timeout, allow_redirects=False,
            )
        except requests.RequestException as exc:
            _audit(f"ERR {provider} /{path} -> {type(exc).__name__}")
            return {"error": f"upstream request failed: {type(exc).__name__}"}, 502

        _audit(f"{request.method} {provider} /{path} -> {upstream.status_code} "
               f"(req {len(body)}B)")

        def generate():
            try:
                for chunk in upstream.iter_content(chunk_size=8192):
                    if chunk:
                        yield chunk
            finally:
                upstream.close()

        return Response(
            stream_with_context(generate()),
            status=upstream.status_code,
            headers=_response_headers(upstream.headers),
        )

    return app
