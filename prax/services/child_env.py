"""Keep Prax's proxy credential out of the processes Prax starts.

Prax exports ``HTTPS_PROXY`` (and friends) into its own environment so its HTTP
clients route through the secrets proxy. Once the forward proxy authenticates
its callers, that URL carries Prax's proxy credential —
``http://prax-prod:<token>@127.0.0.1:8786`` — and every child process inherits
it: git, gh, uv, and plugin subprocesses running third-party code. Any of them
could then spend credentials as Prax, with Prax's egress rules.

With ``CHILD_ENV_STRIP_PROXY_CREDENTIALS`` on (the default — a no-op while the
proxy URL carries no credential), every ``subprocess.Popen`` gets a copy of its
environment in which the proxy URLs have no credential; or, when
``CHILD_PROXY_URL`` is set, the children's own proxy identity (e.g. a
``prax-tools`` caller whose egress rules allow only what tools need).

Covers everything built on ``subprocess.Popen`` (``subprocess.run`` and
friends, and asyncio's subprocesses). ``os.system``/``os.exec*`` are not
covered; Prax does not use them.
"""
from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Mapping
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger(__name__)

PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")

_original_init = None


def strip_userinfo(url: str) -> str:
    """``http://user:secret@host:8786`` → ``http://host:8786``; anything else unchanged."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if "@" not in parts.netloc:
        return url
    return urlunsplit(parts._replace(netloc=parts.netloc.rsplit("@", 1)[1]))


def has_credentials(env: Mapping[str, str]) -> bool:
    return any("@" in urlsplit(env.get(k) or "").netloc for k in PROXY_VARS if env.get(k))


def for_child(env: Mapping[str, str], child_proxy_url: str = "") -> dict[str, str]:
    """A copy of *env* safe to hand a child process."""
    out = dict(env)
    for key in PROXY_VARS:
        if out.get(key):
            out[key] = child_proxy_url or strip_userinfo(out[key])
    return out


def install(child_proxy_url: str = "") -> None:
    """Wrap ``subprocess.Popen`` so every child gets :func:`for_child` of its env.

    Idempotent. A call that passes ``env=`` gets that mapping cleaned too
    (callers often pass ``{**os.environ, ...}``).
    """
    global _original_init
    if _original_init is not None:
        return
    _original_init = subprocess.Popen.__init__

    def _init(self, *args, **kwargs):
        env = kwargs.get("env")
        source = os.environ if env is None else env
        if has_credentials(source) or (child_proxy_url and any(source.get(k) for k in PROXY_VARS)):
            kwargs["env"] = for_child(source, child_proxy_url)
        _original_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = _init
    logger.info("child processes get proxy URLs %s",
                "with their own identity (CHILD_PROXY_URL)" if child_proxy_url
                else "without Prax's proxy credential")


def uninstall() -> None:
    """Restore ``subprocess.Popen`` (tests)."""
    global _original_init
    if _original_init is not None:
        subprocess.Popen.__init__ = _original_init
        _original_init = None
