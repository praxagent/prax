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


def _get_settings():
    from prax.settings import settings
    return settings


# ---------------------------------------------------------------------------
# Path translation between the Prax host and the sandbox container
# ---------------------------------------------------------------------------

def to_sandbox_path(path: str | None) -> str | None:
    """Translate a Prax-side path to where the sandbox sees it.

    Derived from the directory actually mounted at ``/workspace``
    (:mod:`prax.services.sandbox_mount`), so it is right whether the sandbox
    mounts one user's workspace or the whole tree. A path outside the mount
    (``/tmp/x``, a command-line flag, a non-path argument) is returned
    unchanged, as before.
    """
    if not path:
        return path
    from prax.services.sandbox_mount import to_sandbox
    return to_sandbox(path) or path


def _translate_cmd_paths(cmd: list[str]) -> list[str]:
    """Translate workspace paths in command arguments for the sandbox."""
    return [to_sandbox_path(arg) or arg for arg in cmd]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def routes_to_sandbox() -> bool:
    """True when :func:`run_command` executes inside the sandbox container.

    Always in a docker-compose deployment. On a host install (Prax as a
    process beside the sandbox — `make run-local-all`, `deploy/update.sh`)
    only with ``SANDBOX_ROUTE_COMMANDS``; otherwise commands run on the Prax
    host itself, which is the prior behaviour.
    """
    s = _get_settings()
    if s.sandbox_persistent:
        return True
    return bool(getattr(s, "sandbox_route_commands", False) and s.sandbox_available)


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
    if routes_to_sandbox():
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
    if routes_to_sandbox():
        # Under whatever is mounted at /workspace — the workspaces/ root was
        # invisible to a sandbox that mounts only one user's directory.
        from prax.services.sandbox_mount import mount_source
        base = os.path.join(mount_source() or os.path.abspath(_get_settings().workspace_dir), ".tmp")
        os.makedirs(base, exist_ok=True)
        return tempfile.mkdtemp(prefix=prefix, dir=base)
    return tempfile.mkdtemp(prefix=prefix)


def is_sandbox_running() -> bool:
    """Return True if the always-on sandbox container is reachable."""
    if not routes_to_sandbox():
        return False
    try:
        from prax.services.sandbox_bridge import configured_client
        return bool(configured_client().health())
    except Exception:
        return False
