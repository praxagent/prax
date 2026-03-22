"""Live ngrok URL resolver.

Polls the ngrok API to get the current tunnel URL, caching with a TTL.
Falls back to the NGROK_URL environment variable if the API is unreachable.
Writes the resolved URL to a file so other processes can read it too.

Usage:
    from prax.utils.ngrok import get_ngrok_url
    url = get_ngrok_url()  # always returns the current URL or None
"""
from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger(__name__)

_CACHE_TTL = 60  # seconds before re-polling the ngrok API
_NGROK_URL_FILE = ".ngrok_url"  # written to project root

_cached_url: str | None = None
_cached_at: float = 0
_api_unreachable_until: float = 0  # negative cache for failed API polls


def _ngrok_api_url() -> str:
    return os.environ.get("NGROK_API_URL", "http://ngrok:4040")


def _url_file_path() -> str:
    """Path to the file where the current ngrok URL is persisted."""
    # In Docker: /app/.ngrok_url.  Locally: ./.ngrok_url.
    return os.path.join(os.getcwd(), _NGROK_URL_FILE)


def _poll_ngrok_api() -> str | None:
    """Poll the ngrok local API for the current HTTPS tunnel URL."""
    import json
    import urllib.error
    import urllib.request

    api = _ngrok_api_url()
    try:
        resp = urllib.request.urlopen(f"{api}/api/tunnels", timeout=3)
        data = json.loads(resp.read())
        for tunnel in data.get("tunnels", []):
            public_url = tunnel.get("public_url", "")
            if public_url.startswith("https://"):
                return public_url
    except Exception:
        pass

    # Mark API as unreachable for 2 minutes to avoid repeated timeouts
    # when ngrok isn't running at all.
    global _api_unreachable_until
    _api_unreachable_until = time.monotonic() + 120
    return None


def _read_url_file() -> str | None:
    """Read the cached ngrok URL from disk."""
    path = _url_file_path()
    try:
        if os.path.isfile(path):
            url = open(path, encoding="utf-8").read().strip()
            if url:
                return url
    except Exception:
        pass
    return None


def _write_url_file(url: str) -> None:
    """Write the current ngrok URL to disk."""
    try:
        with open(_url_file_path(), "w", encoding="utf-8") as f:
            f.write(url)
    except Exception:
        pass


def get_ngrok_url() -> str | None:
    """Return the current ngrok tunnel URL, or None if unavailable.

    Resolution order:
    1. In-memory cache (if fresh, < TTL seconds old)
    2. Live poll of the ngrok API
    3. URL file on disk (.ngrok_url)
    4. NGROK_URL environment variable (set at startup)
    """
    global _cached_url, _cached_at

    now = time.monotonic()
    if _cached_url and (now - _cached_at) < _CACHE_TTL:
        return _cached_url

    # Try the live API (skip if recently unreachable to avoid timeout delays).
    url = None
    if now >= _api_unreachable_until:
        url = _poll_ngrok_api()
    if url:
        if url != _cached_url:
            logger.info("ngrok URL updated: %s", url)
            _write_url_file(url)
        _cached_url = url
        _cached_at = now
        return url

    # Fall back to the file.
    url = _read_url_file()
    if url:
        _cached_url = url
        _cached_at = now
        return url

    # Fall back to the startup env var.
    from prax.settings import settings
    url = settings.ngrok_url
    if url:
        _cached_url = url
        _cached_at = now
    return url
