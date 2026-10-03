"""Count recurring warnings and errors by call site, in process.

``prax_doctor`` should be able to say "this warning has fired 400 times since
startup" without reading ``app.log``: that file is never rotated (tens of MB on
a dev box) and every line of it is formatted text.  So a ``logging.Handler`` on
the root logger counts WARNING-and-above records as they happen, grouped by
``(levelname, logger name, pathname:lineno)``.

Privacy: log lines carry user data and fetched, untrusted text (tool arguments,
exception messages).  The formatted message (``record.getMessage()``) is never
stored, and neither is exception text.  The only text kept is ``record.msg``,
and only when both hold:

- the call site is Prax's own code (``prax/`` or ``app.py`` in this repo, not
  a ``site-packages`` path such as the in-repo ``.venv``).  A library's
  template is not a fixed string Prax wrote: werkzeug bakes the client IP and a
  timestamp into the one it passes for every request line;
- ``record.args`` is non-empty, because then the data lives in the args and
  ``msg`` is the fixed %-style template.  With no args the message may be an
  f-string with the data already baked in, so only the location is kept.

A Prax template that mixes an f-string with %-args can still carry data;
truncation to ``TEMPLATE_MAX_CHARS`` limits how much.

Locations are shortened so they name no user: repo-relative for Prax's code,
package-relative under ``site-packages``, ``<workspace>/…`` for code in a user
workspace (the user-id directory dropped), and ``<external>/<basename>`` for
any other path.

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
_PRAX_PACKAGE = os.path.join(_REPO_ROOT, "prax") + os.sep
_APP_PY = os.path.join(_REPO_ROOT, "app.py")
_SITE_PACKAGES = "site-packages" + os.sep


def _is_prax_call_site(pathname: str) -> bool:
    """True for Prax's own modules, whose templates Prax wrote."""
    if _SITE_PACKAGES in pathname:
        return False
    return pathname.startswith(_PRAX_PACKAGE) or pathname == _APP_PY


def _template(record: logging.LogRecord) -> str | None:
    if not record.args or not isinstance(record.msg, str):
        return None
    if not _is_prax_call_site(record.pathname):
        return None
    text = " ".join(record.msg.split())
    if len(text) > TEMPLATE_MAX_CHARS:
        text = text[: TEMPLATE_MAX_CHARS - 1] + "…"
    return text


def _workspace_roots() -> list[str]:
    """``settings.workspace_dir`` as given and resolved, read at call time."""
    try:
        from prax.settings import settings

        raw = settings.workspace_dir
    except Exception:
        return []
    if not raw:
        return []
    roots = {os.path.abspath(raw), os.path.realpath(raw)}
    return [r.rstrip(os.sep) + os.sep for r in roots]


def _short_path(pathname: str) -> str:
    """A location that is useful in a report and names no user."""
    idx = pathname.rfind(_SITE_PACKAGES)
    if idx != -1:
        return pathname[idx + len(_SITE_PACKAGES):]
    # A workspace may sit inside the checkout (or the checkout inside the
    # workspace root), so the longer matching root decides.
    best: tuple[int, str] | None = None
    for root in _workspace_roots():
        if pathname.startswith(root) and (best is None or len(root) > best[0]):
            parts = pathname[len(root):].split(os.sep)
            # parts[0] is the user's directory; keep only what is inside it.
            best = (len(root), "<workspace>/" + "/".join(parts[1:] or parts))
    repo = _REPO_ROOT + os.sep
    if pathname.startswith(repo) and (best is None or len(repo) > best[0]):
        best = (len(repo), pathname[len(repo):])
    if best is not None:
        return best[1]
    return "<external>/" + os.path.basename(pathname)


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
_installed_on: logging.Logger | None = None
_install_lock = threading.Lock()


def install(target: logging.Logger | None = None) -> LogHealthHandler:
    """Attach the counter to *target* (the root logger) once, and return it.

    Idempotent, so an app factory that runs twice does not double-count.  The
    counter lives on one logger at a time: installing it on another moves it
    there, since a record propagating through both would count twice.
    """
    global _handler, _installed_on
    logger = target or logging.getLogger()
    with _install_lock:
        if _handler is None:
            _handler = LogHealthHandler()
        if _installed_on is not None and _installed_on is not logger:
            _installed_on.removeHandler(_handler)
        if _handler not in logger.handlers:
            logger.addHandler(_handler)
        _installed_on = logger
        return _handler


def uninstall(target: logging.Logger | None = None) -> None:
    """Detach the counter from the logger ``install`` put it on, and forget it.

    With a *target* other than that logger, only *target* loses the handler
    (should it have one): the counter stays installed where it is, and stays
    known, so ``get_handler`` never loses track of a handler that is still
    counting.
    """
    global _handler, _installed_on
    with _install_lock:
        if _handler is None:
            return
        if target is not None and target is not _installed_on:
            target.removeHandler(_handler)
            return
        if _installed_on is not None:
            _installed_on.removeHandler(_handler)
        _handler = None
        _installed_on = None


def get_handler() -> LogHealthHandler | None:
    """The installed counter, or None when ``LOG_HEALTH_ENABLED`` is off."""
    return _handler


def summarize(top_n: int = 10) -> list[dict]:
    """``LogHealthHandler.summarize`` on the installed counter; [] if none."""
    handler = _handler
    return handler.summarize(top_n) if handler is not None else []
