"""Sandbox-aware shell execution for plugins.

Provides :func:`run_command`, :func:`which`, and :func:`shared_tempdir` —
drop-in replacements for :mod:`subprocess` helpers that transparently route
to the sandbox container in Docker-compose deployments.

In local mode, commands execute on the host as usual.  In Docker mode,
commands are sent to the always-on sandbox container via the prax-sandbox
client (``docker exec`` under the hood).  Paths under the shared workspace
volume are translated automatically so files written by the app container are
visible to the sandbox and vice-versa.

Plugins should import from here instead of using :func:`subprocess.run`
directly for any command that needs system packages (pdflatex, ffmpeg, …).
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile

logger = logging.getLogger(__name__)

_APP_WORKSPACE_PREFIX = "/app/workspaces/"
# User-scoped sandbox mount: /workspace (singular) is the user's own folder.
_SANDBOX_WORKSPACE_PREFIX = "/workspace/"


def _get_settings():
    from prax.settings import settings
    return settings


# ---------------------------------------------------------------------------
# Path translation between app and sandbox containers
# ---------------------------------------------------------------------------

def to_sandbox_path(path: str | None) -> str | None:
    """Translate an app-container path to the sandbox-container equivalent.

    The sandbox mounts a single user's workspace at ``/workspace/``.
    App-container paths like ``/app/workspaces/{user_id}/foo`` become
    ``/workspace/foo`` (the user_id prefix is stripped because the mount
    is already user-scoped).
    """
    if not path:
        return path

    settings = _get_settings()
    user_id = settings.prax_user_id

    # /app/workspaces/{user_id}/foo → /workspace/foo
    if path.startswith(_APP_WORKSPACE_PREFIX):
        rest = path[len(_APP_WORKSPACE_PREFIX):]
        if user_id and rest.startswith(user_id + "/"):
            rest = rest[len(user_id) + 1:]
        elif user_id and rest == user_id:
            rest = ""
        return _SANDBOX_WORKSPACE_PREFIX + rest if rest else _SANDBOX_WORKSPACE_PREFIX.rstrip("/")

    # Resolve relative/absolute host paths
    ws_dir = os.path.abspath(settings.workspace_dir)
    abs_path = os.path.abspath(path)
    if abs_path.startswith(ws_dir + os.sep):
        rest = abs_path[len(ws_dir) + 1:]
        # Strip user_id prefix — sandbox mount is user-scoped
        if user_id and rest.startswith(user_id + os.sep):
            rest = rest[len(user_id) + 1:]
        elif user_id and rest == user_id:
            rest = ""
        return _SANDBOX_WORKSPACE_PREFIX + rest if rest else _SANDBOX_WORKSPACE_PREFIX.rstrip("/")
    if abs_path == ws_dir:
        return _SANDBOX_WORKSPACE_PREFIX.rstrip("/")
    return path


def _translate_cmd_paths(cmd: list[str]) -> list[str]:
    """Translate workspace paths in command arguments for the sandbox."""
    return [to_sandbox_path(arg) or arg for arg in cmd]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_command(
    cmd: list[str],
    *,
    cwd: str | None = None,
    capture_output: bool = True,
    text: bool = True,
    timeout: int = 300,
    check: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run a shell command, routing to the sandbox in Docker mode.

    A near-drop-in replacement for :func:`subprocess.run`.  In Docker-compose
    deployments (``RUNNING_IN_DOCKER=true``) the command runs inside the sandbox
    container (via the prax-sandbox client); workspace paths in *cmd* and *cwd*
    are translated to the sandbox mount first.  Otherwise it runs on the host.
    """
    settings = _get_settings()
    if settings.sandbox_persistent:
        from prax.services.sandbox_bridge import configured_client
        sandbox_cmd = _translate_cmd_paths(cmd)
        sandbox_cwd = to_sandbox_path(cwd)
        return configured_client().run_command(
            sandbox_cmd, cwd=sandbox_cwd, env=env, timeout=timeout,
        )
    return subprocess.run(
        cmd, cwd=cwd, capture_output=capture_output,
        text=text, timeout=timeout, check=check, env=env,
    )


def which(cmd_name: str) -> bool:
    """Check if a command is available.  Checks the sandbox in Docker mode."""
    try:
        result = run_command(["which", cmd_name], timeout=10)
        return result.returncode == 0
    except Exception:
        return False


def shared_tempdir(prefix: str = "prax_") -> str:
    """Create a temp directory accessible from both app and sandbox.

    In Docker mode, the directory lives under the workspace volume so both
    containers can read/write.  In local mode, uses the system temp dir.

    The caller is responsible for cleanup (or not — workspace .gitignore
    blocks ``.tmp/``).
    """
    settings = _get_settings()
    if settings.sandbox_persistent:
        base = os.path.join(
            os.path.abspath(settings.workspace_dir), ".tmp",
        )
        os.makedirs(base, exist_ok=True)
        return tempfile.mkdtemp(prefix=prefix, dir=base)
    return tempfile.mkdtemp(prefix=prefix)


def is_sandbox_running() -> bool:
    """Return True if the always-on sandbox container is reachable."""
    settings = _get_settings()
    if not settings.sandbox_persistent:
        return False
    try:
        from prax.services.sandbox_bridge import configured_client
        return bool(configured_client().health())
    except Exception:
        return False
