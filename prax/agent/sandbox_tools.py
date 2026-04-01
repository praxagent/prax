"""LangChain tool wrappers for sandbox code execution."""
from __future__ import annotations

from langchain_core.tools import tool

from prax.agent.action_policy import RiskLevel, risk_tool
from prax.agent.user_context import current_user_id
from prax.services import sandbox_service


def _get_user_id() -> str:
    uid = current_user_id.get()
    if not uid:
        return "unknown"
    return uid


@tool
def sandbox_shell(command: str, timeout: int = 60) -> str:
    """Run a shell command directly in the sandbox container.

    This executes instantly via docker exec — no AI coding agent, no session
    overhead.  Use for simple commands: ls, pwd, df -h, cat, grep, python -c,
    du, find, env, etc.

    Do NOT use this for complex multi-step coding tasks — use sandbox_start
    for those.

    Args:
        command: The shell command to run (passed to sh -c).
        timeout: Max seconds to wait (default 60).
    """
    result = sandbox_service.run_shell(command, timeout=timeout)
    if "error" in result:
        return f"Shell error: {result['error']}"
    parts = []
    if result.get("stdout"):
        parts.append(result["stdout"])
    if result.get("stderr"):
        parts.append(f"STDERR:\n{result['stderr']}")
    parts.append(f"(exit code: {result['exit_code']})")
    return "\n".join(parts)


@risk_tool(risk=RiskLevel.MEDIUM)
def sandbox_start(task_description: str, model: str | None = None) -> str:
    """Start a sandboxed coding session with an AI coding agent.

    The coding agent can write and execute code (Python, LaTeX, ffmpeg, etc.)
    inside an isolated container. Provide a clear description of the task.
    Optionally specify the model (e.g. 'anthropic/claude-sonnet-4-5' or 'openai/gpt-5.4').

    Returns the session_id — pass it to sandbox_message/review/finish/abort
    if you have multiple sessions running.
    """
    result = sandbox_service.start_session(_get_user_id(), task_description, model=model)
    if "error" in result:
        return f"Failed to start sandbox: {result['error']}"
    return (
        f"Sandbox session started (id: {result['session_id'][:12]}, model: {result['model']}). "
        f"The coding agent is working on your task. Use sandbox_review to check progress "
        f"or sandbox_message to send follow-up instructions."
    )


@risk_tool(risk=RiskLevel.MEDIUM)
def sandbox_message(message: str, model: str | None = None, session_id: str | None = None) -> str:
    """Send a follow-up message or instruction to a sandbox coding session.

    Use this to refine the task, request changes, or ask the coding agent to try
    a different approach. Optionally switch to a different model if the current one
    isn't producing good results.

    If session_id is omitted, targets the most recently created session.
    """
    result = sandbox_service.send_message(_get_user_id(), message, model=model, session_id=session_id)
    if result.get("auto_aborted"):
        return (
            f"⚠️ Sandbox AUTO-ABORTED: {result['error']}. "
            f"Start a new session with sandbox_start if you want to try again."
        )
    if "error" in result:
        return f"Sandbox error: {result['error']}"
    response = result.get("response", {})
    if isinstance(response, dict) and "error" in response:
        return (
            f"Coding agent error: {response['error']}. "
            f"If this keeps happening, use sandbox_abort to stop the session "
            f"and sandbox_start to begin fresh."
        )
    model_info = f" (model: {result.get('model', 'unknown')})" if model else ""
    rounds_left = result.get("rounds_remaining")
    budget_info = f" [{rounds_left} rounds remaining]" if rounds_left is not None else ""
    return f"Message sent to coding agent{model_info}.{budget_info} Response: {response}"


@tool
def sandbox_review(session_id: str | None = None) -> str:
    """Review the current status of a sandbox session.

    Shows elapsed time, files created/modified, and conversation state.
    If session_id is omitted, shows the most recent session.
    """
    result = sandbox_service.review_session(_get_user_id(), session_id=session_id)
    if "error" in result:
        return f"Sandbox error: {result['error']}"
    files = result.get("files", [])
    file_list = "\n".join(f"  - {f}" for f in files) if files else "  (no files yet)"
    elapsed = result.get("elapsed_seconds", 0)
    timeout = result.get("timeout_seconds", 0)
    rounds_used = result.get("rounds_used", 0)
    rounds_left = result.get("rounds_remaining", "?")
    return (
        f"Sandbox session {result['session_id'][:12]}:\n"
        f"  Status: {result['status']}\n"
        f"  Model: {result['model']}\n"
        f"  Elapsed: {elapsed}s / {timeout}s timeout\n"
        f"  Rounds: {rounds_used} used, {rounds_left} remaining\n"
        f"  Files:\n{file_list}"
    )


@tool
def sandbox_finish(summary: str = "", session_id: str | None = None) -> str:
    """Finish a sandbox session and archive all artifacts.

    Code, SOLUTION.md, and the full session log are saved to the workspace
    archive for future reference. Provide a brief summary of what was accomplished.
    If session_id is omitted, finishes the most recent session.
    """
    result = sandbox_service.finish_session(_get_user_id(), summary=summary, session_id=session_id)
    if "error" in result:
        return f"Sandbox error: {result['error']}"
    path = result.get("archived_path", "unknown")
    return (
        f"Sandbox session finished and archived to {path}. "
        f"The solution can be found and re-executed later with sandbox_search."
    )


@tool
def sandbox_abort(session_id: str | None = None) -> str:
    """Abort a sandbox session immediately.

    Destroys the container without archiving artifacts. Use only if the session
    is stuck or producing unwanted results.
    If session_id is omitted, aborts the most recent session.
    """
    result = sandbox_service.abort_session(_get_user_id(), session_id=session_id)
    if "error" in result:
        return f"Sandbox error: {result['error']}"
    elapsed = result.get("elapsed_seconds", "?")
    rounds = result.get("rounds_used", "?")
    return f"Sandbox session aborted after {elapsed}s ({rounds} rounds used)."


@tool
def sandbox_search(query: str) -> str:
    """Search past sandbox solutions by keyword.

    Returns matching solutions from the archive so you can re-execute them
    instead of solving the problem from scratch.
    """
    results = sandbox_service.search_solutions(_get_user_id(), query)
    if not results:
        return f"No archived solutions match '{query}'."
    lines = []
    for r in results:
        lines.append(f"**{r['session_id']}**:\n{r['snippet']}")
    return "Found solutions:\n\n" + "\n\n".join(lines)


@risk_tool(risk=RiskLevel.MEDIUM)
def sandbox_execute(solution_id: str, command: str | None = None) -> str:
    """Re-execute a previously archived sandbox solution.

    Use sandbox_search first to find the solution_id. Optionally provide
    a specific command to run. If no command is given, the agent will look
    for build.sh or main.py in the solution directory.
    """
    result = sandbox_service.execute_solution(_get_user_id(), solution_id, command=command)
    if "error" in result:
        return f"Sandbox error: {result['error']}"
    return (
        f"Re-executing solution '{solution_id}' in a new sandbox "
        f"(session: {result['session_id'][:12]}, model: {result['model']})."
    )


@tool
def sandbox_install(package_name: str) -> str:
    """Install a system package (apt-get) in the persistent sandbox.

    Use this when a task requires a package not pre-installed in the sandbox.
    Pre-installed: python3, texlive (full), ffmpeg, poppler-utils, pandoc, git, curl, wget, jq.

    In Docker deployment, packages are installed automatically. In local mode,
    returns instructions for the user to install manually.

    Note: Packages installed this way persist until the sandbox container restarts.
    For permanent additions, ask the user to update the sandbox Dockerfile.
    """
    result = sandbox_service.install_package(package_name)
    if "error" in result:
        hints = result.get("local_install_hints")
        if hints:
            lines = [f"Cannot auto-install in local mode. The user needs to install '{package_name}':"]
            for os_name, cmd in hints.items():
                lines.append(f"  {os_name}: {cmd}")
            return "\n".join(lines)
        return f"Failed to install '{package_name}': {result['error']}"
    return f"Successfully installed '{package_name}' in the sandbox."


@tool
def sandbox_rebuild(dockerfile_content: str | None = None) -> str:
    """Rebuild the sandbox Docker image and restart the container.

    Use this to permanently add system packages to the sandbox. If you provide
    dockerfile_content, it will overwrite sandbox/Dockerfile before building.
    Read the current Dockerfile first with source_read('sandbox/Dockerfile'),
    add your changes, then pass the full content here.

    Only works in Docker deployment mode. The rebuild takes a few minutes.
    All active sandbox sessions should be finished first.
    """
    result = sandbox_service.rebuild_sandbox(dockerfile_content)
    if "error" in result:
        return f"Sandbox rebuild failed: {result['error']}"
    return f"Sandbox rebuilt and restarted successfully (image: {result['image']})."


def build_sandbox_tools() -> list:
    return [
        sandbox_shell, sandbox_start, sandbox_message, sandbox_review,
        sandbox_finish, sandbox_abort, sandbox_search, sandbox_execute,
        sandbox_install, sandbox_rebuild,
    ]
