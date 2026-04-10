"""Sandbox-aware shell execution for plugins.

Provides :func:`run_command`, :func:`which`, and :func:`shared_tempdir` —
drop-in replacements for :mod:`subprocess` helpers that transparently route
to the sandbox container in Docker-compose deployments.

In local mode, commands execute on the host as usual.  In Docker mode,
commands are sent to the always-on sandbox container via ``docker exec``.
Paths under the shared workspace volume are translated automatically so
files written by the app container are visible to the sandbox and vice-versa.

Plugins should import from here instead of using :func:`subprocess.run`
directly for any command that needs system packages (pdflatex, ffmpeg, …).
"""
from __future__ import annotations

import logging
import os
import shlex
import subprocess
import tempfile

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal: lazy imports to avoid hard dep on docker / settings at import time
# ---------------------------------------------------------------------------

_APP_WORKSPACE_PREFIX = "/app/workspaces/"
# User-scoped sandbox mount: /workspace (singular) is the user's own folder.
_SANDBOX_WORKSPACE_PREFIX = "/workspace/"


def _get_settings():
    from prax.settings import settings
    return settings


def _get_docker_client():
    import docker
    return docker.from_env()


def _find_sandbox_container():
    """Find the running sandbox container (docker-compose service)."""
    client = _get_docker_client()
    containers = client.containers.list(
        filters={"label": "com.docker.compose.service=sandbox"}
    )
    if not containers:
        raise RuntimeError(
            "Sandbox container not running. "
            "Start it with: docker compose up sandbox"
        )
    return containers[0]


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

    This is a near-drop-in replacement for :func:`subprocess.run`.  In
    Docker-compose deployments (``RUNNING_IN_DOCKER=true``), the command is
    executed inside the sandbox container via ``docker exec``.  Workspace
    paths in *cmd* and *cwd* are automatically translated.

    Args:
        cmd:  Command as a list of strings (same as subprocess).
        cwd:  Working directory.
        capture_output:  Capture stdout/stderr (default True).
        text:  Decode output as UTF-8 (default True).
        timeout:  Seconds before killing the command.
        check:  Raise on non-zero exit code (default False).
        env:  Extra environment variables (merged, not replaced).

    Returns:
        :class:`subprocess.CompletedProcess` with returncode, stdout, stderr.
    """
    settings = _get_settings()
    if settings.sandbox_persistent:
        return _run_in_sandbox(
            cmd, cwd=cwd, timeout=timeout, env=env,
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
        _find_sandbox_container()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Internal: sandbox execution via docker exec
# ---------------------------------------------------------------------------

def _run_in_sandbox(
    cmd: list[str],
    *,
    cwd: str | None = None,
    timeout: int = 300,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Execute a command in the sandbox container via ``docker exec``."""
    container = _find_sandbox_container()

    sandbox_cmd = _translate_cmd_paths(cmd)
    sandbox_cwd = to_sandbox_path(cwd)

    # Build a shell one-liner: optionally cd, then run the command.
    parts: list[str] = []
    if sandbox_cwd:
        parts.append(f"cd {shlex.quote(sandbox_cwd)}")
    parts.append(" ".join(shlex.quote(str(c)) for c in sandbox_cmd))
    shell_cmd = " && ".join(parts)

    # Merge extra env vars if provided.
    exec_env = {}
    if env:
        for k, v in env.items():
            exec_env[k] = v

    exit_code, output = container.exec_run(
        ["sh", "-c", shell_cmd],
        demux=True,
        environment=exec_env or None,
    )
    stdout = (output[0] or b"").decode(errors="replace") if output else ""
    stderr = (output[1] or b"").decode(errors="replace") if output else ""

    result = subprocess.CompletedProcess(
        args=cmd, returncode=exit_code, stdout=stdout, stderr=stderr,
    )

    if result.returncode != 0:
        logger.debug(
            "Sandbox command failed (rc=%d): %s\nstderr: %s",
            exit_code, shell_cmd, stderr[:500],
        )

    return result
