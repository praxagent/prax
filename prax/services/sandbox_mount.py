"""Where the workspace sits inside the sandbox — one answer, from one source.

The sandbox container bind-mounts some host directory at ``/workspace``. Which
one depends on how the container was started, and the deploy paths disagree:
``make run-local-all`` and the compose files mount one user's workspace,
``deploy/update.sh`` mounts the whole ``workspaces/`` tree. Prax used to
hard-code both shapes in different places — the shell translation assumed one
user's directory, the sandbox agent's paths assumed the whole tree — so on any
given box one of them pointed the model at directories that did not exist.

Every sandbox path is now derived from :func:`mount_source`: the host
directory bound at ``/workspace`` — ``SANDBOX_WORKSPACE_MOUNT_SOURCE`` when set
(``make run-local-all`` sets it), else asked of Docker when Prax can reach the
container, else the deploy path's documented shape (logged). From it:

* :func:`to_sandbox` — host path -> container path (``None`` if not mounted)
* :func:`from_sandbox` — container path -> host path
* :func:`user_root_in_sandbox` — a user's workspace root in the container

A user whose workspace is not under the mount gets ``None`` rather than a path
that silently resolves to someone else's directory or to nothing.
"""
from __future__ import annotations

import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

SANDBOX_ROOT = "/workspace"
_CACHE_SECONDS = 60.0

_lock = threading.Lock()
_cached: tuple[float, str | None] | None = None


def _settings():
    from prax.settings import settings
    return settings


def _detect_from_docker() -> str | None:
    """The host path bound at /workspace, read from the running container.

    Only meaningful when Prax runs on the same host as the container (the
    Source is a host path) and drives it locally rather than through a
    remote daemon.
    """
    s = _settings()
    if s.running_in_docker or s.sandbox_daemon_url or not s.sandbox_enabled:
        return None
    try:
        from prax_sandbox.exec import find_sandbox_container

        from prax.services.sandbox_bridge import build_config

        container = find_sandbox_container(build_config())
        for mount in container.attrs.get("Mounts", []):
            if mount.get("Destination") == SANDBOX_ROOT and mount.get("Source"):
                return os.path.realpath(mount["Source"])
    except Exception:
        logger.debug("Could not read the sandbox's /workspace mount from Docker", exc_info=True)
    return None


def _documented_default() -> str:
    """The shape each deploy path documents, as Prax sees the filesystem."""
    s = _settings()
    ws = os.path.realpath(s.workspace_dir)
    if s.running_in_docker and s.prax_user_id:
        # docker-compose.yml / .lite.yml: ${WORKSPACE_DIR}/${PRAX_USER_ID}:/workspace
        return os.path.join(ws, s.prax_user_id)
    # deploy/update.sh default: the whole workspaces/ tree.
    return ws


def mount_source() -> str | None:
    """Host directory (as Prax sees it) that the sandbox shows at /workspace."""
    global _cached
    override = (getattr(_settings(), "sandbox_workspace_mount_source", "") or "").strip()
    if override:
        return os.path.realpath(override)
    now = time.monotonic()
    with _lock:
        if _cached and now - _cached[0] < _CACHE_SECONDS:
            return _cached[1]
    value = _detect_from_docker()
    if value is None:
        value = _documented_default()
        _warn_fallback_once(value)
    with _lock:
        _cached = (now, value)
    return value


_warned = False


def _warn_fallback_once(value: str) -> None:
    """Say so when a host install could not read the mount from Docker.

    The fallback (the whole workspaces/ tree) is wrong for a per-user mount,
    and a wrong mount shows up only as files "not found" — so log it once.
    """
    global _warned
    s = _settings()
    if _warned or s.running_in_docker or not s.sandbox_enabled:
        return
    _warned = True
    logger.warning(
        "Could not read the sandbox's /workspace mount from Docker; assuming %s. "
        "If the sandbox mounts a different directory, set SANDBOX_WORKSPACE_MOUNT_SOURCE.",
        value,
    )


def reset_cache() -> None:
    """Forget the detected mount (after the sandbox is recreated, and in tests)."""
    global _cached
    with _lock:
        _cached = None


def _under(path: str, root: str) -> str | None:
    """*path* relative to *root* if it is inside it, else ``None``."""
    if path == root:
        return ""
    if path.startswith(root.rstrip(os.sep) + os.sep):
        return path[len(root.rstrip(os.sep)) + 1:]
    return None


def to_sandbox(host_path: str) -> str | None:
    """Container path for *host_path*, or ``None`` if the sandbox cannot see it."""
    source = mount_source()
    if not source or not host_path:
        return None
    rel = _under(os.path.realpath(host_path), source)
    if rel is None:
        return None
    return f"{SANDBOX_ROOT}/{rel}" if rel else SANDBOX_ROOT


def from_sandbox(container_path: str) -> str | None:
    """Host path for a ``/workspace/...`` container path, or ``None``.

    The result is contained: ``..`` cannot climb out of the mount source.
    """
    source = mount_source()
    if not source or not container_path:
        return None
    rel = _under(os.path.normpath(container_path), SANDBOX_ROOT)
    if rel is None:
        return None
    host = os.path.realpath(os.path.join(source, rel))
    return host if _under(host, source) is not None else None


def user_root_in_sandbox(uid: str) -> str | None:
    """Where *uid*'s workspace root appears in the container, if it does."""
    try:
        from prax.services import workspace_service
        root = workspace_service.workspace_root(uid)
    except Exception:
        return None
    return to_sandbox(root)
