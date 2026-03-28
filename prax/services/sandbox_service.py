"""Sandbox code execution service using Docker + OpenCode.

Supports two modes:
  - **Persistent** (docker-compose): Sandbox container runs 24/7 alongside
    the app. Sessions are created inside the shared container. Prax can
    install system packages via ``docker exec``.
  - **Ephemeral** (local dev): A fresh container is spun up per session and
    torn down when the session ends. Requires Docker Desktop.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field

import requests
from requests.auth import HTTPBasicAuth

from prax.settings import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy Docker import — not every deployment needs Docker
# ---------------------------------------------------------------------------
_docker_client = None


def _get_docker():
    """Return the ``docker`` module, importing lazily."""
    import docker
    return docker


def _get_docker_client():
    global _docker_client
    if _docker_client is None:
        _docker_client = _get_docker().from_env()
    return _docker_client


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

@dataclass
class SandboxSession:
    session_id: str
    user_id: str
    model: str
    created_at: float
    # Ephemeral-only fields (None in persistent mode)
    container_id: str | None = None
    container_name: str | None = None
    host_port: int | None = None
    config_dir: str | None = None
    opencode_session_id: str | None = None
    timeout_timer: threading.Timer | None = field(default=None, repr=False)
    status: str = "starting"  # starting | running | finished | aborted | timed_out
    rounds_used: int = 0
    max_rounds: int = 10
    consecutive_failures: int = 0


_sessions: dict[str, SandboxSession] = {}
_user_sessions: dict[str, str] = {}  # user_id -> active session_id
_lock = threading.Lock()

# ---------------------------------------------------------------------------
# OpenCode config helpers
# ---------------------------------------------------------------------------

_SANDBOX_CONTAINER_PORT = 4096
_SANDBOX_AUTH_KEY = secrets.token_urlsafe(32)
_OPENCODE_INSTRUCTIONS = (
    "You are a coding agent inside a sandboxed environment. "
    "Write clean, well-documented code. Test your work by running it. "
    "When you are done, summarize what you built and how to use it."
)


def _build_opencode_config(model: str) -> dict:
    """Build the opencode.json dict for a sandbox session."""
    return {
        "$schema": "https://opencode.ai/config.json",
        "model": model,
        "provider": {
            "anthropic": {
                "options": {"apiKey": "{env:ANTHROPIC_API_KEY}"}
            },
            "openai": {
                "options": {"apiKey": "{env:OPENAI_API_KEY}"}
            },
        },
    }


def _build_container_env() -> dict[str, str]:
    """Build environment variables for the sandbox container."""
    env = {"OPENCODE_SERVER_PASSWORD": _SANDBOX_AUTH_KEY}
    if settings.anthropic_key:
        env["ANTHROPIC_API_KEY"] = settings.anthropic_key
    if settings.openai_key:
        env["OPENAI_API_KEY"] = settings.openai_key
    return env


# ---------------------------------------------------------------------------
# OpenCode HTTP API helpers
# ---------------------------------------------------------------------------

def _api_url(session: SandboxSession, path: str) -> str:
    if settings.sandbox_persistent:
        return f"http://{settings.sandbox_host}:{_SANDBOX_CONTAINER_PORT}{path}"
    return f"http://localhost:{session.host_port}{path}"


def _oc_auth() -> HTTPBasicAuth | None:
    """Basic Auth credentials for OpenCode server requests.

    In persistent mode the sandbox doesn't require auth (no password set).
    """
    if settings.sandbox_persistent:
        return None
    return HTTPBasicAuth("opencode", _SANDBOX_AUTH_KEY)


def _wait_for_ready(session: SandboxSession, timeout: float = 30) -> tuple[bool, str]:
    """Poll the OpenCode health endpoint until ready or timeout.

    Returns (success, detail) — *detail* is empty on success and describes
    the last error on failure.
    """
    deadline = time.time() + timeout
    last_error = "no response within timeout"
    while time.time() < deadline:
        try:
            r = requests.get(_api_url(session, "/global/health"), auth=_oc_auth(), timeout=2)
            if r.status_code == 200:
                return True, ""
            last_error = f"health endpoint returned HTTP {r.status_code}"
        except requests.ConnectionError as e:
            last_error = f"connection refused ({e})"
        except Exception as e:
            last_error = str(e)
        time.sleep(1)
    return False, last_error


def _create_opencode_session(session: SandboxSession, task: str) -> tuple[str | None, str]:
    """Create an OpenCode session. The task is used as the title only;
    the first real prompt is sent via send_message / _send_opencode_message.

    Returns (session_id, error_detail).  *error_detail* is empty on success.
    """
    try:
        r = requests.post(
            _api_url(session, "/session"),
            json={"title": task[:80]},
            auth=_oc_auth(),
            timeout=30,
        )
        if r.status_code >= 400:
            body = r.text[:300]
            logger.error(
                "OpenCode session creation failed HTTP %d for %s: %s",
                r.status_code, session.session_id, body,
            )
            return None, f"HTTP {r.status_code}: {body}"
        data = r.json()
        oc_id = data.get("id") or data.get("session_id")
        if not oc_id:
            logger.error("No session ID in OpenCode response: %s", data)
            return None, f"OpenCode returned no session ID (response: {str(data)[:200]})"
        return oc_id, ""
    except Exception as e:
        logger.exception("Failed to create OpenCode session for %s", session.session_id)
        return None, str(e)


def _send_opencode_message(session: SandboxSession, message: str, model: str | None = None) -> dict:
    """Send a message to the OpenCode session (async + poll)."""
    oc_id = session.opencode_session_id

    # Build workspace path instruction based on mode.
    if settings.sandbox_persistent:
        safe_id = session.user_id.lstrip("+")
        workspace_path = f"/workspaces/{safe_id}/active"
        instructions = f"{_OPENCODE_INSTRUCTIONS} The user's files are at {workspace_path}. Work there."
    else:
        instructions = f"{_OPENCODE_INSTRUCTIONS} The user's project files are mounted at /workspace."

    payload: dict = {
        "parts": [{"type": "text", "text": message}],
        "system": instructions,
    }
    if model:
        payload["model"] = model

    # Snapshot current message count so we know when a new response arrives
    try:
        r = requests.get(
            _api_url(session, f"/session/{oc_id}/message"),
            auth=_oc_auth(),
            timeout=10,
        )
        before_count = len(r.json()) if r.status_code == 200 else 0
    except Exception:
        before_count = 0

    # Send async — returns 204 immediately
    try:
        r = requests.post(
            _api_url(session, f"/session/{oc_id}/prompt_async"),
            json=payload,
            auth=_oc_auth(),
            timeout=10,
        )
        if r.status_code not in (200, 204):
            return {"error": f"Failed to send message: HTTP {r.status_code}"}
    except Exception as e:
        logger.exception("Failed to send message to sandbox %s", session.session_id)
        return {"error": str(e)}

    # Poll for the assistant's response (up to 5 min)
    deadline = time.time() + 300
    poll_errors = 0
    last_poll_error = ""
    while time.time() < deadline:
        time.sleep(5)
        try:
            r = requests.get(
                _api_url(session, f"/session/{oc_id}/message"),
                auth=_oc_auth(),
                timeout=10,
            )
            if r.status_code != 200:
                poll_errors += 1
                last_poll_error = f"HTTP {r.status_code}: {r.text[:200]}"
                logger.warning(
                    "Poll error for sandbox %s: %s", session.session_id[:12], last_poll_error,
                )
                if poll_errors >= 10:
                    return {
                        "error": (
                            f"Sandbox polling failed {poll_errors} times. "
                            f"Last error: {last_poll_error}"
                        ),
                    }
                continue
            messages = r.json()
            if not isinstance(messages, list) or len(messages) <= before_count:
                continue
            # Find the latest assistant message that has completed
            last = messages[-1]
            info = last.get("info", {})
            if info.get("role") != "assistant":
                continue
            if not info.get("time", {}).get("completed"):
                continue  # still streaming
            # Extract text from parts
            parts = last.get("parts", [])
            text = "\n".join(
                p.get("text", "") for p in parts if p.get("type") == "text"
            )
            return {"response": text or "(no text output)", "raw": last}
        except requests.ConnectionError as e:
            poll_errors += 1
            last_poll_error = f"connection lost ({e})"
            logger.warning("Poll connection error for sandbox %s: %s", session.session_id[:12], e)
        except Exception as e:
            poll_errors += 1
            last_poll_error = str(e)
            logger.warning("Poll exception for sandbox %s: %s", session.session_id[:12], e)

        if poll_errors >= 10:
            return {
                "error": (
                    f"Sandbox polling failed {poll_errors} times. "
                    f"Last error: {last_poll_error}"
                ),
            }

    elapsed_poll = int(300 - max(0, deadline - time.time()))
    return {
        "error": (
            f"Sandbox timed out waiting for response ({elapsed_poll}s). "
            f"The coding agent may still be running. "
            f"Poll errors during wait: {poll_errors}."
        ),
    }


def _get_opencode_session(session: SandboxSession) -> dict:
    """Get the current OpenCode session state."""
    oc_id = session.opencode_session_id
    try:
        r = requests.get(_api_url(session, f"/session/{oc_id}"), auth=_oc_auth(), timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.exception("Failed to get sandbox session %s", session.session_id)
        return {"error": str(e)}


def _export_opencode_session(session: SandboxSession) -> dict | None:
    """Export the OpenCode session for archival."""
    oc_id = session.opencode_session_id
    try:
        r = requests.get(_api_url(session, f"/session/{oc_id}/message"), auth=_oc_auth(), timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception:
        logger.exception("Failed to export session %s", session.session_id)
        return None


# ---------------------------------------------------------------------------
# Port allocation (ephemeral mode only)
# ---------------------------------------------------------------------------

_allocated_ports: set[int] = set()
_port_counter = 0


def _allocate_port() -> int:
    """Allocate a host port for a sandbox container."""
    global _port_counter
    # Use high ephemeral ports starting at 19000
    base = 19000
    port = base + (_port_counter % 1000)
    _port_counter += 1
    while port in _allocated_ports:
        port = base + (_port_counter % 1000)
        _port_counter += 1
    _allocated_ports.add(port)
    return port


def _release_port(port: int) -> None:
    _allocated_ports.discard(port)


# ---------------------------------------------------------------------------
# Workspace integration
# ---------------------------------------------------------------------------

def _workspace_root(user_id: str) -> str:
    safe_id = user_id.lstrip("+")
    return os.path.abspath(os.path.join(settings.workspace_dir, safe_id))


def _solutions_dir(user_id: str) -> str:
    return os.path.join(_workspace_root(user_id), "archive", "code")


def _archive_solution(session: SandboxSession, summary: str = "") -> str:
    """Archive sandbox artifacts to the user's workspace git repo."""
    from prax.services.workspace_service import ensure_workspace, git_commit

    root = ensure_workspace(session.user_id)
    dest = os.path.join(root, "archive", "code", session.session_id[:12])
    os.makedirs(dest, exist_ok=True)

    # Sandbox output lands directly in active/ (shared mount), so no file copy needed.

    # Write SOLUTION.md
    solution_md = (
        f"## Solution: {session.session_id[:12]}\n\n"
        f"- **Session ID**: {session.session_id}\n"
        f"- **Model**: {session.model}\n"
        f"- **Date**: {time.strftime('%Y-%m-%d %H:%M')}\n"
        f"- **Duration**: {int(time.time() - session.created_at)}s\n\n"
    )
    if summary:
        solution_md += f"### Summary\n\n{summary}\n\n"
    with open(os.path.join(dest, "SOLUTION.md"), "w") as f:
        f.write(solution_md)

    # Export and save OpenCode session log
    session_log = _export_opencode_session(session)
    if session_log:
        with open(os.path.join(dest, "session_log.json"), "w") as f:
            json.dump(session_log, f, indent=2)

    git_commit(root, f"Sandbox solution: {session.session_id[:12]}")
    logger.info("Archived sandbox solution %s for %s", session.session_id[:12], session.user_id)
    return dest


# ---------------------------------------------------------------------------
# Container lifecycle
# ---------------------------------------------------------------------------

def _teardown_container(session: SandboxSession) -> None:
    """Stop and remove the sandbox container, clean up temp files."""
    if settings.sandbox_persistent:
        # Persistent mode — don't touch the shared container.
        return

    try:
        client = _get_docker_client()
        container = client.containers.get(session.container_id)
        container.stop(timeout=5)
        container.remove(force=True)
    except Exception:
        logger.debug("Container %s already removed or not found", session.container_id)

    # Clean up temp config dir
    if session.config_dir and os.path.isdir(session.config_dir):
        shutil.rmtree(session.config_dir, ignore_errors=True)

    if session.host_port:
        _release_port(session.host_port)


def _on_timeout(session_id: str) -> None:
    """Timer callback — abort the session on timeout."""
    with _lock:
        session = _sessions.get(session_id)
        if session and session.status == "running":
            elapsed = int(time.time() - session.created_at)
            logger.warning(
                "Sandbox session %s timed out after %ds "
                "(%d/%d rounds used, user=%s, model=%s)",
                session_id[:12], elapsed,
                session.rounds_used, session.max_rounds,
                session.user_id, session.model,
            )
            session.status = "timed_out"
            _teardown_container(session)
            _user_sessions.pop(session.user_id, None)
            _sessions.pop(session_id, None)


# ---------------------------------------------------------------------------
# Package installation (persistent mode)
# ---------------------------------------------------------------------------

def install_package(package_name: str) -> dict:
    """Install a system package in the sandbox container.

    Only works in persistent (docker-compose) mode. In local mode, returns
    an error with instructions for the user to install manually.
    """
    if not settings.sandbox_persistent:
        return {
            "error": (
                "Cannot auto-install packages in local mode. "
                f"The user needs to install '{package_name}' on their system."
            ),
            "local_install_hints": {
                "macOS": f"brew install {package_name}",
                "Ubuntu": f"sudo apt-get install {package_name}",
            },
        }

    # Sanitize package name — alphanumeric, dots, hyphens, plus, colons only.
    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9._+:-]*$", package_name):
        return {"error": f"Invalid package name: {package_name}"}

    try:
        client = _get_docker_client()
        containers = client.containers.list(
            filters={"label": "com.docker.compose.service=sandbox"}
        )
        if not containers:
            return {"error": "Sandbox container not found."}
        container = containers[0]
        exit_code, output = container.exec_run(
            ["sh", "-c", f"apt-get update -qq && apt-get install -y --no-install-recommends {package_name}"],
            demux=True,
        )
        stdout = (output[0] or b"").decode(errors="replace")
        stderr = (output[1] or b"").decode(errors="replace")
        if exit_code != 0:
            return {"error": f"apt-get failed (exit {exit_code}): {stderr[-500:]}"}
        return {"installed": package_name, "output": stdout[-300:]}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_session(
    user_id: str,
    task: str,
    model: str | None = None,
) -> dict:
    """Start a new sandbox coding session.

    Returns dict with session_id, status, model.
    """
    model = model or settings.sandbox_default_model

    with _lock:
        if user_id in _user_sessions:
            return {"error": "You already have an active sandbox session. Finish or abort it first."}
        if len(_sessions) >= settings.sandbox_max_concurrent:
            return {"error": "Maximum concurrent sandbox sessions reached. Try again later."}

    session_id = str(uuid.uuid4())

    # ----- Persistent mode: reuse the always-on sandbox container -----
    if settings.sandbox_persistent:
        active_workspace = os.path.join(_workspace_root(user_id), "active")
        os.makedirs(active_workspace, exist_ok=True)

        session = SandboxSession(
            session_id=session_id,
            user_id=user_id,
            model=model,
            created_at=time.time(),
            max_rounds=settings.sandbox_max_rounds,
        )

        ready, ready_detail = _wait_for_ready(session, timeout=10)
        if not ready:
            return {"error": f"Persistent sandbox is not responding ({ready_detail}). Check docker-compose logs."}

        session.status = "running"

        oc_session_id, oc_error = _create_opencode_session(session, task)
        if not oc_session_id:
            return {"error": f"Failed to create coding session inside the sandbox: {oc_error}"}
        session.opencode_session_id = oc_session_id

        # Start timeout timer
        timer = threading.Timer(settings.sandbox_timeout, _on_timeout, args=[session_id])
        timer.daemon = True
        timer.start()
        session.timeout_timer = timer

        with _lock:
            _sessions[session_id] = session
            _user_sessions[user_id] = session_id

        logger.info("Started persistent sandbox session %s for %s (model=%s)", session_id[:12], user_id, model)
        return {"session_id": session_id, "status": "running", "model": model}

    # ----- Ephemeral mode: spin up a new container -----
    config_dir = tempfile.mkdtemp(prefix="opencode-config-")
    config = _build_opencode_config(model)
    with open(os.path.join(config_dir, "opencode.json"), "w") as f:
        json.dump(config, f)

    active_workspace = os.path.join(_workspace_root(user_id), "active")
    os.makedirs(active_workspace, exist_ok=True)

    host_port = _allocate_port()

    try:
        client = _get_docker_client()
        container = client.containers.run(
            image=settings.sandbox_image,
            command=f"opencode serve --hostname 0.0.0.0 --port {_SANDBOX_CONTAINER_PORT}",
            name=f"sandbox-{session_id[:12]}",
            detach=True,
            environment=_build_container_env(),
            volumes={
                active_workspace: {"bind": "/workspace", "mode": "rw"},
                config_dir: {"bind": "/root/.config/opencode", "mode": "ro"},
            },
            ports={f"{_SANDBOX_CONTAINER_PORT}/tcp": host_port},
            mem_limit=settings.sandbox_mem_limit,
            nano_cpus=settings.sandbox_cpu_limit,
            pids_limit=512,
            labels={
                "prax-sandbox": "true",
                "sandbox-session-id": session_id,
                "sandbox-user-id": user_id,
            },
        )
    except Exception as e:
        _release_port(host_port)
        shutil.rmtree(config_dir, ignore_errors=True)
        logger.exception("Failed to create sandbox container for %s", user_id)
        return {"error": f"Failed to start sandbox: {e}"}

    session = SandboxSession(
        session_id=session_id,
        user_id=user_id,
        container_id=container.id,
        container_name=container.name,
        host_port=host_port,
        model=model,
        created_at=time.time(),
        config_dir=config_dir,
        max_rounds=settings.sandbox_max_rounds,
    )

    # Wait for OpenCode to be ready
    ready, ready_detail = _wait_for_ready(session, timeout=45)
    if not ready:
        logger.error("OpenCode never became ready on port %d — tearing down: %s", host_port, ready_detail)
        _teardown_container(session)
        return {"error": f"Sandbox container started but OpenCode failed to become ready: {ready_detail}"}

    session.status = "running"

    # Create the OpenCode session with the initial task
    oc_session_id, oc_error = _create_opencode_session(session, task)
    if not oc_session_id:
        _teardown_container(session)
        return {"error": f"Failed to create coding session inside the sandbox: {oc_error}"}

    session.opencode_session_id = oc_session_id

    # Start timeout timer
    timer = threading.Timer(settings.sandbox_timeout, _on_timeout, args=[session_id])
    timer.daemon = True
    timer.start()
    session.timeout_timer = timer

    with _lock:
        _sessions[session_id] = session
        _user_sessions[user_id] = session_id

    logger.info("Started sandbox %s for %s (model=%s, port=%d)", session_id[:12], user_id, model, host_port)
    return {
        "session_id": session_id,
        "status": "running",
        "model": model,
    }


def send_message(user_id: str, message: str, model: str | None = None) -> dict:
    """Send a message to the user's active sandbox session."""
    with _lock:
        session_id = _user_sessions.get(user_id)
        if not session_id:
            return {"error": "No active sandbox session."}
        session = _sessions.get(session_id)
        if not session or session.status != "running":
            return {"error": "Sandbox session is not running."}

    if session.rounds_used >= session.max_rounds:
        remaining_action = "Use sandbox_finish to save what you have, or sandbox_abort to discard."
        return {
            "error": (
                f"Sandbox has reached the maximum of {session.max_rounds} message rounds. "
                f"{remaining_action}"
            ),
            "rounds_used": session.rounds_used,
            "max_rounds": session.max_rounds,
        }

    if model and model != session.model:
        session.model = model
        logger.info("Switched sandbox %s to model %s", session_id[:12], model)

    response = _send_opencode_message(session, message, model=model)

    # Only count the round if the message was actually processed.
    if "error" in response:
        session.consecutive_failures += 1
        logger.warning(
            "Sandbox %s message failed (consecutive=%d): %s",
            session_id[:12], session.consecutive_failures, response["error"],
        )
        # Auto-abort after 3 consecutive failures — the session is stuck.
        if session.consecutive_failures >= 3:
            logger.error(
                "Sandbox %s auto-aborting after %d consecutive failures",
                session_id[:12], session.consecutive_failures,
            )
            return {
                "error": (
                    f"Sandbox session auto-aborted after {session.consecutive_failures} "
                    f"consecutive failures. The coding agent appears stuck. "
                    f"Last error: {response['error']}"
                ),
                "auto_aborted": True,
            }
    else:
        session.rounds_used += 1
        session.consecutive_failures = 0  # Reset on success.

    rounds_left = session.max_rounds - session.rounds_used
    return {
        "session_id": session_id,
        "model": session.model,
        "response": response,
        "rounds_used": session.rounds_used,
        "rounds_remaining": rounds_left,
    }


def review_session(user_id: str) -> dict:
    """Get status and details of the user's active sandbox session."""
    with _lock:
        session_id = _user_sessions.get(user_id)
        if not session_id:
            return {"error": "No active sandbox session."}
        session = _sessions.get(session_id)
        if not session:
            return {"error": "Session not found."}

    elapsed = int(time.time() - session.created_at)
    oc_state = _get_opencode_session(session)

    # List files in the session workspace
    session_workspace = os.path.join(_workspace_root(user_id), "active", "sessions", session_id)
    files = []
    if os.path.isdir(session_workspace):
        for root_dir, _dirs, filenames in os.walk(session_workspace):
            for fname in filenames:
                rel = os.path.relpath(os.path.join(root_dir, fname), session_workspace)
                files.append(rel)

    return {
        "session_id": session_id,
        "status": session.status,
        "model": session.model,
        "elapsed_seconds": elapsed,
        "timeout_seconds": settings.sandbox_timeout,
        "rounds_used": session.rounds_used,
        "rounds_remaining": session.max_rounds - session.rounds_used,
        "files": sorted(files),
        "opencode_state": oc_state,
    }


def finish_session(user_id: str, summary: str = "") -> dict:
    """Finish the active sandbox session, archiving artifacts to the workspace."""
    with _lock:
        session_id = _user_sessions.get(user_id)
        if not session_id:
            return {"error": "No active sandbox session."}
        session = _sessions.get(session_id)
        if not session:
            return {"error": "Session not found."}

    # Cancel timeout
    if session.timeout_timer:
        session.timeout_timer.cancel()

    # Archive artifacts
    try:
        archive_path = _archive_solution(session, summary)
    except Exception:
        logger.exception("Failed to archive sandbox %s", session_id[:12])
        archive_path = None

    session.status = "finished"
    _teardown_container(session)

    with _lock:
        _sessions.pop(session_id, None)
        _user_sessions.pop(user_id, None)

    logger.info("Finished sandbox %s for %s", session_id[:12], user_id)
    return {
        "session_id": session_id,
        "status": "finished",
        "archived_path": archive_path,
    }


def abort_session(user_id: str) -> dict:
    """Abort the active sandbox session without archiving."""
    with _lock:
        session_id = _user_sessions.get(user_id)
        if not session_id:
            return {"error": "No active sandbox session."}
        session = _sessions.get(session_id)
        if not session:
            return {"error": "Session not found."}

    if session.timeout_timer:
        session.timeout_timer.cancel()

    elapsed = int(time.time() - session.created_at)
    session.status = "aborted"
    _teardown_container(session)

    with _lock:
        _sessions.pop(session_id, None)
        _user_sessions.pop(user_id, None)

    logger.warning(
        "Aborted sandbox %s for %s after %ds (%d/%d rounds used, model=%s)",
        session_id[:12], user_id, elapsed,
        session.rounds_used, session.max_rounds, session.model,
    )
    return {
        "session_id": session_id,
        "status": "aborted",
        "elapsed_seconds": elapsed,
        "rounds_used": session.rounds_used,
    }


def search_solutions(user_id: str, query: str) -> list[dict]:
    """Search past sandbox solutions in the workspace archive."""
    code_dir = _solutions_dir(user_id)
    if not os.path.isdir(code_dir):
        return []

    results = []
    try:
        proc = subprocess.run(
            ["grep", "-ril", "--include=SOLUTION.md", "--", query, code_dir],
            capture_output=True, text=True, timeout=10,
        )
        for filepath in proc.stdout.strip().splitlines():
            if not filepath:
                continue
            solution_dir = os.path.dirname(filepath)
            session_short = os.path.basename(solution_dir)
            snippet_proc = subprocess.run(
                ["grep", "-i", "-m", "5", "-C", "1", "--", query, filepath],
                capture_output=True, text=True, timeout=5,
            )
            results.append({
                "session_id": session_short,
                "path": solution_dir,
                "snippet": snippet_proc.stdout.strip()[:500],
            })
    except subprocess.TimeoutExpired:
        logger.warning("Solution search timed out for %s query '%s'", user_id, query)
    return results


def execute_solution(user_id: str, solution_id: str, command: str | None = None) -> dict:
    """Re-execute a known solution from the archive in a fresh container.

    If command is not provided, looks for a build.sh or main.py in the solution dir.
    """
    code_dir = _solutions_dir(user_id)
    solution_dir = os.path.join(code_dir, solution_id)
    if not os.path.isdir(solution_dir):
        return {"error": f"Solution '{solution_id}' not found in archive."}

    # Read SOLUTION.md for context
    solution_md = os.path.join(solution_dir, "SOLUTION.md")
    context = ""
    if os.path.isfile(solution_md):
        with open(solution_md) as f:
            context = f.read()

    # Start a new session with the solution context
    task = (
        f"Re-execute a previously archived solution.\n\n"
        f"Solution archive contents are available at /workspace.\n\n"
        f"Previous solution context:\n{context}\n\n"
    )
    if command:
        task += f"Run this command: {command}"
    else:
        task += "Look for build.sh, main.py, or similar entry point and run it."

    return start_session(user_id, task)


def cleanup_stale_sessions() -> int:
    """Find and remove orphaned sandbox containers. Call on app startup.

    In persistent mode, skips container cleanup (the sandbox is managed by
    docker-compose) but still clears any stale in-memory session state.
    """
    if settings.sandbox_persistent:
        with _lock:
            count = len(_sessions)
            _sessions.clear()
            _user_sessions.clear()
        if count:
            logger.info("Cleared %d stale in-memory sandbox sessions", count)
        return count

    try:
        client = _get_docker_client()
    except Exception:
        logger.debug("Docker not available, skipping sandbox cleanup")
        return 0

    count = 0
    try:
        containers = client.containers.list(
            all=True,
            filters={"label": "prax-sandbox=true"},
        )
        for container in containers:
            try:
                container.remove(force=True)
                count += 1
            except Exception:
                pass
    except Exception:
        logger.debug("Failed to list sandbox containers for cleanup")
    if count:
        logger.info("Cleaned up %d stale sandbox containers", count)
    return count


def get_active_session(user_id: str) -> SandboxSession | None:
    """Return the user's active session or None."""
    with _lock:
        session_id = _user_sessions.get(user_id)
        if session_id:
            return _sessions.get(session_id)
    return None


def get_runtime_mode() -> str:
    """Return a human-readable description of the current sandbox mode."""
    if settings.sandbox_persistent:
        return "docker (persistent sandbox — can auto-install packages)"
    return "local (ephemeral sandbox — user must install system packages)"


def rebuild_sandbox(dockerfile_content: str | None = None) -> dict:
    """Rebuild the sandbox Docker image and restart the container.

    Only works in persistent (docker-compose) mode. If *dockerfile_content*
    is provided, it overwrites ``sandbox/Dockerfile`` before building.

    This enables Prax to permanently add packages by editing the Dockerfile.
    """
    if not settings.sandbox_persistent:
        return {"error": "Sandbox rebuild is only available in Docker deployment mode."}

    try:
        client = _get_docker_client()
    except Exception as e:
        return {"error": f"Docker not available: {e}"}

    # Optionally update the Dockerfile.
    sandbox_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "sandbox")
    if dockerfile_content:
        dockerfile_path = os.path.join(sandbox_dir, "Dockerfile")
        if not os.path.isfile(dockerfile_path):
            return {"error": f"Cannot find sandbox Dockerfile at {dockerfile_path}"}
        with open(dockerfile_path, "w") as f:
            f.write(dockerfile_content)

    # Build the image.
    try:
        result = subprocess.run(
            ["docker", "build", "-t", settings.sandbox_image, sandbox_dir],
            capture_output=True, text=True, timeout=600,
        )
        if result.returncode != 0:
            return {"error": f"Docker build failed:\n{result.stderr[-1000:]}"}
    except subprocess.TimeoutExpired:
        return {"error": "Docker build timed out (10 min)."}

    # Find and restart the sandbox container.
    try:
        containers = client.containers.list(
            filters={"label": "com.docker.compose.service=sandbox"}
        )
        if not containers:
            return {"error": "Sandbox container not found. Restart docker-compose."}
        container = containers[0]
        container.restart(timeout=10)
    except Exception as e:
        return {"error": f"Failed to restart sandbox container: {e}"}

    # Wait for the sandbox to come back up.
    dummy_session = SandboxSession(
        session_id="rebuild-check", user_id="system",
        model="", created_at=time.time(),
    )
    ready, ready_detail = _wait_for_ready(dummy_session, timeout=60)
    if not ready:
        return {"error": f"Sandbox rebuilt but failed to become healthy within 60s: {ready_detail}"}

    return {"status": "rebuilt", "image": settings.sandbox_image}
