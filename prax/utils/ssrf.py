"""SSRF egress guard for outbound HTTP from agent/plugin-controlled URLs.

Agent- and plugin-supplied URLs must not be able to reach internal resources:
``localhost``, RFC1918 ranges, link-local (incl. the cloud metadata endpoint
``169.254.169.254``), reserved/multicast space, etc. :func:`validate_url`
enforces a scheme allowlist and blocks any host that *is* or *resolves to* a
non-public address; :func:`safe_request` follows redirects manually so every
hop is re-validated (a redirect to ``http://169.254.169.254`` is caught).

Design choices for robustness without breaking offline/test environments:
- IP-literal hosts are checked directly (no DNS needed) — the common SSRF
  vector (``http://169.254.169.254``, ``http://127.0.0.1``) is always caught.
- Named hosts are resolved best-effort; if they resolve to an internal address
  we block, but if resolution *fails* we allow (a non-resolving host is not an
  internal-resource risk, and this keeps mocked/offline tests working).
- An allowlist (``settings.ssrf_allowed_hosts``) permits explicit dev hosts.

Residual caveat: this validates at check time, not connect time, so it is not a
full DNS-rebinding defense (that needs pinning the resolved IP into the socket).
It blocks the realistic vectors; rebinding hardening is a possible follow-up.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urljoin, urlparse

from prax.settings import settings

logger = logging.getLogger(__name__)

_ALLOWED_SCHEMES = ("http", "https")


class SSRFError(ValueError):
    """Raised when a URL is rejected by the SSRF guard."""


def _is_blocked_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # unparseable — fail closed
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _allowlisted(host: str) -> bool:
    raw = getattr(settings, "ssrf_allowed_hosts", "") or ""
    allowed = {h.strip().lower() for h in raw.split(",") if h.strip()}
    return host.lower() in allowed


def validate_url(url: str, *, allowed_schemes: tuple[str, ...] = _ALLOWED_SCHEMES) -> str:
    """Return *url* if it passes the SSRF guard, else raise :class:`SSRFError`.

    No-op when ``settings.ssrf_protection_enabled`` is False.
    """
    if not getattr(settings, "ssrf_protection_enabled", True):
        return url

    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in allowed_schemes:
        raise SSRFError(f"scheme {scheme or '(none)'!r} not allowed (only {allowed_schemes})")

    host = parsed.hostname
    if not host:
        raise SSRFError("URL has no host")

    if _allowlisted(host):
        return url

    # IP-literal host — check directly, no DNS.  (Parse separately from the
    # raise: SSRFError subclasses ValueError, so raising inside an
    # `except ValueError` guard would swallow it.)
    is_ip_literal = True
    try:
        ipaddress.ip_address(host)
    except ValueError:
        is_ip_literal = False
    if is_ip_literal:
        if _is_blocked_ip(host):
            raise SSRFError(f"blocked address: {host}")
        return url

    low = host.lower()
    if low == "localhost" or low.endswith((".localhost", ".local", ".internal")):
        raise SSRFError(f"blocked internal host: {host}")

    # Best-effort resolution: block if it resolves to an internal address;
    # allow on resolution failure (not an internal-resource risk).
    port = parsed.port or (443 if scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return url
    for info in infos:
        ip = info[4][0]
        if _is_blocked_ip(ip):
            raise SSRFError(f"host {host} resolves to blocked address {ip}")
    return url


def safe_request(method: str, url: str, *, max_redirects: int = 5, **kwargs):
    """Perform an HTTP request with per-hop SSRF validation.

    Redirects are followed manually so every Location is re-validated. Uses
    ``requests`` resolved by method name (so callers' monkeypatches of
    ``requests.get``/``requests.post`` still apply). Raises :class:`SSRFError`
    on a blocked hop or too many redirects.
    """
    import requests

    kwargs.setdefault("timeout", 30)
    kwargs["allow_redirects"] = False
    fn = getattr(requests, method.lower())

    current = url
    for _ in range(max_redirects + 1):
        validate_url(current)
        resp = fn(current, **kwargs)
        if getattr(resp, "status_code", None) in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location") if hasattr(resp, "headers") else None
            if not location:
                return resp
            current = urljoin(current, location)
            continue
        return resp
    raise SSRFError(f"too many redirects (> {max_redirects})")
