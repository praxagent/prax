"""Harness-side sync for remote-sandbox mode.

In local (in-process) mode the control plane shares the workspace mount with the
sandbox, so archive/review/search already operate on the harness's own files —
these helpers just delegate, unchanged. In remote mode the sandbox's
``/workspace`` lives on the daemon's box, so a finished solution must be pulled
back into the harness's git-backed workspace and committed here.

review_session and search_solutions need no adapter: the daemon runs them
server-side (walking / grepping its own workspace, where the sandbox wrote the
files) and returns correct results over HTTP.
"""
from __future__ import annotations

import io
import logging
import os
import tarfile

logger = logging.getLogger(__name__)


def start_session(client, user_id: str, task: str, model: str | None = None) -> dict:
    """Start a session; in remote mode, push the local ``active/`` working copy
    to the sandbox first so the coding agent sees existing files."""
    from prax.settings import settings
    if settings.sandbox_remote:
        try:
            _push_active(client, user_id)
        except Exception:
            logger.warning("remote push of active/ failed; starting with remote state", exc_info=True)
    return client.start_session(user_id, task, model=model)


def _push_active(client, user_id: str) -> None:
    from prax.services.workspace_service import ensure_workspace
    active = os.path.join(ensure_workspace(user_id), "active")
    if not os.path.isdir(active):
        return
    tar_bytes = _tar_dir(active, "active")
    if tar_bytes:
        client.push_tar(user_id, tar_bytes, "")  # members are active/… under the user root
        logger.info("Pushed local active/ to remote sandbox for %s", user_id)


def _tar_dir(src_dir: str, arc_prefix: str) -> bytes | None:
    """Tar the regular files under *src_dir* (symlinks/devices skipped). None if empty."""
    buf = io.BytesIO()
    added = 0
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for dirpath, _dirs, files in os.walk(src_dir):
            for name in files:
                full = os.path.join(dirpath, name)
                if os.path.islink(full) or not os.path.isfile(full):
                    continue
                tar.add(full, arcname=os.path.join(arc_prefix, os.path.relpath(full, src_dir)), recursive=False)
                added += 1
    return buf.getvalue() if added else None


def finish_session(client, user_id: str, summary: str = "", session_id: str | None = None) -> dict:
    """Finish a session; in remote mode, pull the archived solution + commit locally."""
    result = client.finish_session(user_id, summary=summary, session_id=session_id)

    from prax.settings import settings
    if settings.sandbox_remote and isinstance(result, dict) and "error" not in result:
        try:
            _pull_solution(client, user_id, result.get("archived_path"))
        except Exception:
            logger.warning("remote solution pull/commit failed", exc_info=True)
    return result


def _pull_solution(client, user_id: str, archived_path: str | None) -> None:
    if not archived_path:
        return
    short = os.path.basename(archived_path.rstrip("/"))
    tar_bytes = client.pull_tar(user_id, f"archive/code/{short}")

    from prax.services.workspace_service import ensure_workspace, git_commit
    root = ensure_workspace(user_id)
    _safe_extract(tar_bytes, root)
    git_commit(root, f"Sandbox solution (remote): {short}")
    logger.info("Pulled remote sandbox solution %s for %s", short, user_id)


def _safe_extract(tar_bytes: bytes, dest_root: str) -> None:
    """Extract a daemon-produced tar into the local workspace, hardened.

    Mirrors the daemon's producer-side stripping: only regular files/dirs, every
    member re-confined under ``dest_root`` (zip-slip defense; a malicious or
    compromised sandbox cannot write outside the harness workspace).
    """
    dest_root = os.path.realpath(dest_root)
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:*") as tar:
        for m in tar.getmembers():
            if not (m.isfile() or m.isdir()):
                continue
            if os.path.isabs(m.name) or ".." in m.name.split("/"):
                continue
            target = os.path.realpath(os.path.join(dest_root, m.name))
            if target != dest_root and not target.startswith(dest_root + os.sep):
                continue  # escapes the workspace — skip
            if m.isdir():
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            extracted = tar.extractfile(m)
            if extracted is not None:
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
                try:
                    os.write(fd, extracted.read())
                finally:
                    os.close(fd)
