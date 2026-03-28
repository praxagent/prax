"""Environment restrictions for IMPORTED plugins.

Provides :class:`SanitizedEnviron` (a dict subclass that blocks reads of
sensitive env vars) and :func:`restricted_import_env` (a context manager
that patches ``os.environ`` during plugin ``exec_module``).

Defence-in-depth: not unbreakable in-process, but catches the 90% of
casual or low-effort credential exfiltration attempts.
"""
from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

# Patterns that mark an env-var name as sensitive.
SENSITIVE_PATTERNS: list[str] = [
    "KEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL",
    "OPENAI", "ANTHROPIC", "ELEVENLABS", "AMADEUS",
    "AWS_", "AZURE_", "TWILIO", "GOOGLE_APPLICATION",
]

_SENSITIVE_RE = re.compile(
    "|".join(re.escape(p) for p in SENSITIVE_PATTERNS),
    re.IGNORECASE,
)


def _is_sensitive(key: str) -> bool:
    """Return True if *key* matches any sensitive pattern."""
    return bool(_SENSITIVE_RE.search(key))


class SanitizedEnviron(dict):
    """Dict that blocks reads of sensitive environment variables.

    Safe keys (``PATH``, ``HOME``, ``LANG``, etc.) pass through normally.
    Reads of sensitive keys log a warning and behave as if the key
    doesn't exist.
    """

    def __init__(self, real_env: dict[str, str] | None = None, *, plugin_name: str = "<unknown>") -> None:
        # Copy only non-sensitive entries from the real environment.
        source = real_env if real_env is not None else dict(os.environ)
        safe = {k: v for k, v in source.items() if not _is_sensitive(k)}
        super().__init__(safe)
        self._plugin_name = plugin_name

    def __getitem__(self, key: str) -> str:
        if _is_sensitive(key):
            logger.warning(
                "Plugin %s tried to read sensitive env var %s — blocked",
                self._plugin_name, key,
            )
            raise KeyError(key)
        return super().__getitem__(key)

    def get(self, key: str, default: Any = None) -> Any:
        if _is_sensitive(key):
            logger.warning(
                "Plugin %s tried to read sensitive env var %s via get() — blocked",
                self._plugin_name, key,
            )
            return default
        return super().get(key, default)

    def __contains__(self, key: object) -> bool:
        if isinstance(key, str) and _is_sensitive(key):
            return False
        return super().__contains__(key)

    def __iter__(self) -> Iterator[str]:
        return (k for k in super().__iter__() if not _is_sensitive(k))

    def keys(self):  # type: ignore[override]
        return [k for k in super().keys() if not _is_sensitive(k)]

    def values(self):  # type: ignore[override]
        return [v for k, v in super().items() if not _is_sensitive(k)]

    def items(self):  # type: ignore[override]
        return [(k, v) for k, v in super().items() if not _is_sensitive(k)]

    def copy(self) -> dict[str, str]:
        return {k: v for k, v in super().items() if not _is_sensitive(k)}


@contextmanager
def restricted_import_env(plugin_name: str = "<unknown>"):
    """Context manager: patches ``os.environ`` with a sanitized version.

    Used during ``exec_module()`` for IMPORTED plugins so that any
    top-level code reading ``os.environ`` at import time gets the
    restricted view.

    Also patches ``os.getenv`` to use the sanitized environ.
    """
    original_environ = os.environ
    original_getenv = os.getenv
    sanitized = SanitizedEnviron(plugin_name=plugin_name)

    # Monkey-patch os.environ and os.getenv.
    os.environ = sanitized  # type: ignore[assignment]  # noqa: B003

    def _restricted_getenv(key: str, default: str | None = None) -> str | None:
        if _is_sensitive(key):
            logger.warning(
                "Plugin %s tried os.getenv(%s) — blocked",
                plugin_name, key,
            )
            return default
        return original_environ.get(key, default)

    os.getenv = _restricted_getenv  # type: ignore[assignment]

    try:
        yield sanitized
    finally:
        os.environ = original_environ  # noqa: B003
        os.getenv = original_getenv  # type: ignore[assignment]
