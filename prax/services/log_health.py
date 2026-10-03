"""Count recurring warnings and errors by call site, in process.

``prax_doctor`` should be able to say "this warning has fired 400 times since
startup" without reading ``app.log``: that file is never rotated (tens of MB on
a dev box) and every line of it is formatted text.  So a ``logging.Handler`` on
the root logger counts WARNING-and-above records as they happen, grouped by
``(levelname, logger name, pathname:lineno)``.

Privacy: log lines carry user data and fetched, untrusted text (tool arguments,
exception messages).  The formatted message (``record.getMessage()``) is never
stored, and neither is exception text.  The only text kept is ``record.msg`` —
and only when ``record.args`` is non-empty, because then the data lives in the
args and ``msg`` is the fixed %-style template.  With no args the message may be
an f-string with the data already baked in, so only the location is kept.  A
template that mixes an f-string with %-args can still carry data; truncation to
``TEMPLATE_MAX_CHARS`` limits how much.

The table is bounded at ``MAX_GROUPS`` call sites; records from sites beyond
that are counted in ``overflow`` rather than silently dropped.

Installed by ``app.py`` only when ``LOG_HEALTH_ENABLED`` is true.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

MAX_GROUPS = 500
TEMPLATE_MAX_CHARS = 160

_REPO_ROOT = str(Path(__file__).resolve().parents[2])
_SITE_PACKAGES = "site-packages" + os.sep


def _template(record: logging.LogRecord) -> str | None:
    if not record.args or not isinstance(record.msg, str):
        return None
    text = " ".join(record.msg.split())
    if len(text) > TEMPLATE_MAX_CHARS:
        text = text[: TEMPLATE_MAX_CHARS - 1] + "…"
    return text


def _short_path(pathname: str) -> str:
    """Repo-relative for Prax's own code, package-relative for libraries."""
    if pathname.startswith(_REPO_ROOT + os.sep):
        return pathname[len(_REPO_ROOT) + 1:]
    idx = pathname.rfind(_SITE_PACKAGES)
    if idx != -1:
        return pathname[idx + len(_SITE_PACKAGES):]
    return pathname


@dataclass
class _Group:
    level: str
    logger: str
    pathname: str
    lineno: int
    template: str | None
    first_seen: float
    last_seen: float
    count: int = 0

    def row(self) -> dict:
        return {
            "level": self.level,
            "logger": self.logger,
            "location": f"{_short_path(self.pathname)}:{self.lineno}",
            "template": self.template,
            "count": self.count,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }


class LogHealthHandler(logging.Handler):
    """Counts WARNING+ records per call site; never stores a formatted message."""

    def __init__(self, max_groups: int = MAX_GROUPS) -> None:
        super().__init__(level=logging.WARNING)
        self.max_groups = max_groups
        self.installed_at = time.time()
        self.overflow = 0
        self._groups: dict[tuple[str, str, str, int], _Group] = {}

    def emit(self, record: logging.LogRecord) -> None:
        # Handler.handle() already holds self.lock around emit().
        try:
            key = (record.levelname, record.name, record.pathname, record.lineno)
            group = self._groups.get(key)
            if group is None:
                if len(self._groups) >= self.max_groups:
                    self.overflow += 1
                    return
                group = _Group(
                    level=record.levelname,
                    logger=record.name,
                    pathname=record.pathname,
                    lineno=record.lineno,
                    template=_template(record),
                    first_seen=record.created,
                    last_seen=record.created,
                )
                self._groups[key] = group
            elif group.template is None:
                group.template = _template(record)
            group.count += 1
            group.last_seen = record.created
        except Exception:  # noqa: BLE001 — a health counter must never break logging
            pass

    def summarize(self, top_n: int = 10) -> list[dict]:
        """The ``top_n`` busiest call sites, most records first."""
        with self.lock:
            groups = sorted(
                self._groups.values(), key=lambda g: (-g.count, -g.last_seen),
            )[: max(top_n, 0)]
            return [g.row() for g in groups]

    def group_count(self) -> int:
        with self.lock:
            return len(self._groups)


_handler: LogHealthHandler | None = None
_install_lock = threading.Lock()


def install(target: logging.Logger | None = None) -> LogHealthHandler:
    """Attach the counter to *target* (the root logger) once, and return it.

    Idempotent, so an app factory that runs twice does not double-count.
    """
    global _handler
    logger = target or logging.getLogger()
    with _install_lock:
        if _handler is None:
            _handler = LogHealthHandler()
        if _handler not in logger.handlers:
            logger.addHandler(_handler)
        return _handler


def uninstall(target: logging.Logger | None = None) -> None:
    """Detach and forget the counter (tests, or turning it off at runtime)."""
    global _handler
    logger = target or logging.getLogger()
    with _install_lock:
        if _handler is not None:
            logger.removeHandler(_handler)
        _handler = None


def get_handler() -> LogHealthHandler | None:
    """The installed counter, or None when ``LOG_HEALTH_ENABLED`` is off."""
    return _handler


def summarize(top_n: int = 10) -> list[dict]:
    """``LogHealthHandler.summarize`` on the installed counter; [] if none."""
    handler = _handler
    return handler.summarize(top_n) if handler is not None else []
