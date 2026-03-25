"""LangChain tool wrappers for self-improvement code modification."""
from __future__ import annotations

from langchain_core.tools import tool

from prax.agent.action_policy import RiskLevel, risk_tool
from prax.services import codegen_service


@tool
def self_improve_start(branch_name: str, description: str = "") -> str:
    """Start a self-improvement branch to propose code changes.

    Creates an isolated git worktree in a staging clone so the live app
    is completely unaffected.  Use self_improve_deploy to verify and
    hot-swap changes, or self_improve_submit for a PR requiring review.
    """
    result = codegen_service.start_branch(branch_name, description)
    if "error" in result:
        return f"Error: {result['error']}"
    return (
        f"Branch '{result['branch']}' created.\n"
        f"Worktree: {result['worktree_path']}\n"
        f"Use self_improve_read/write to make changes, "
        f"self_improve_verify to check, and self_improve_deploy to go live."
    )


@tool
def self_improve_read(branch_name: str, filepath: str) -> str:
    """Read a file from the self-improvement worktree."""
    result = codegen_service.read_file(branch_name, filepath)
    if "error" in result:
        return f"Error: {result['error']}"
    return f"--- {result['filepath']} ---\n{result['content']}"


@tool
def self_improve_write(branch_name: str, filepath: str, content: str) -> str:
    """Write or update a file in the self-improvement worktree."""
    result = codegen_service.write_file(branch_name, filepath, content)
    if "error" in result:
        return f"Error: {result['error']}"
    return f"Written {result['size']} bytes to {result['filepath']}"


@tool
def self_improve_test(branch_name: str) -> str:
    """Run the test suite against the self-improvement branch."""
    result = codegen_service.run_tests(branch_name)
    if "error" in result:
        return f"Error: {result['error']}"
    output = result.get("stdout", "") + result.get("stderr", "")
    return f"Tests {result['status']}:\n{output[-2000:]}"


@tool
def self_improve_lint(branch_name: str) -> str:
    """Run the linter (ruff) against the self-improvement branch."""
    result = codegen_service.run_lint(branch_name)
    if "error" in result:
        return f"Error: {result['error']}"
    return f"Lint {result['status']}:\n{result.get('output', '')[-2000:]}"


@tool
def self_improve_verify(branch_name: str) -> str:
    """Run full verification (tests + lint + startup) without deploying.

    Use this to check if changes are ready before calling self_improve_deploy.
    """
    results = {}

    test_result = codegen_service.run_tests(branch_name)
    results["tests"] = test_result.get("status", "error")
    if "error" in test_result:
        return f"Error: {test_result['error']}"

    lint_result = codegen_service.run_lint(branch_name)
    results["lint"] = lint_result.get("status", "error")

    startup_result = codegen_service.verify_startup(branch_name)
    results["startup"] = startup_result.get("status", "error")

    all_pass = all(
        v in ("passed", "clean") for v in results.values()
    )
    status = "ALL PASSED" if all_pass else "ISSUES FOUND"
    lines = [f"Verification: {status}"]
    for check, result in results.items():
        icon = "OK" if result in ("passed", "clean") else "FAIL"
        lines.append(f"  [{icon}] {check}: {result}")

    if not all_pass:
        if results["tests"] == "failed":
            output = test_result.get("stdout", "") + test_result.get("stderr", "")
            lines.append(f"\nTest output:\n{output[-1500:]}")
        if results["lint"] == "issues_found":
            lines.append(f"\nLint output:\n{lint_result.get('output', '')[-1500:]}")
        if results["startup"] == "failed":
            lines.append(f"\nStartup error:\n{startup_result.get('stderr', '')[-1000:]}")

    return "\n".join(lines)


@risk_tool(risk=RiskLevel.HIGH)
def self_improve_deploy(branch_name: str, commit_message: str = "") -> str:
    """Verify and hot-swap changes into the live app.

    Runs the full verification pipeline (tests, lint, startup check).
    Only if ALL checks pass are the changed files copied to the live
    directory.  Flask's Werkzeug reloader auto-restarts the app.

    For complex changes needing human review, use self_improve_submit instead.
    """
    result = codegen_service.verify_and_deploy(branch_name, commit_message)
    if "error" in result:
        stage = result.get("stage", "")
        msg = f"Error: {result['error']}"
        if stage:
            details = result.get("details", {})
            output = details.get("stdout", "") + details.get("stderr", "") + details.get("output", "")
            if output:
                msg += f"\n\n{output[-2000:]}"
        return msg
    changed = result.get("files_changed", [])
    deleted = result.get("files_deleted", [])
    lines = [f"Deployed successfully! {result['message']}"]
    if changed:
        lines.append(f"\nChanged files: {', '.join(changed)}")
    if deleted:
        lines.append(f"Deleted files: {', '.join(deleted)}")
    lines.append(
        "\nIMPORTANT: Tell the user what you changed and why. "
        "Remind them: this fix is live in their local repo — "
        "they need to git add, commit, and push from the project folder to preserve it.\n"
        "WARNING: The app will auto-restart in a few seconds from the file changes. "
        "Do NOT call any more tools this turn — just respond to the user with "
        "your summary and let the restart happen."
    )
    return "\n".join(lines)


@tool
def self_improve_submit(branch_name: str, title: str, body: str = "") -> str:
    """Submit changes as a pull request (DISABLED — use self_improve_deploy instead).

    Git push is not allowed.  Use self_improve_deploy to verify and hot-swap
    changes into the live app locally, or ask the user to review and push manually.

    Args:
        branch_name: The branch name.
        title: PR title (unused).
        body: PR body (unused).
    """
    return (
        "Submitting PRs (git push) is disabled.  Use self_improve_deploy to "
        "verify and hot-swap your changes into the live app instead.  If the "
        "change is too complex for hot-swap, tell the user what you changed "
        "and where so they can review and push manually."
    )


@tool
def self_improve_rollback() -> str:
    """Rollback the most recent self-improve deploy.

    Reverts the last commit in the live repo if it was a self-improve deploy.
    The app will restart from the file changes.  Use this when a deploy
    broke something or the user says "rollback" / "undo that".
    """
    result = codegen_service.rollback_last_deploy()
    if "error" in result:
        return f"Rollback failed: {result['error']}"
    return (
        f"Rolled back: {result['reverted_commit']}\n"
        f"{result['message']}\n"
        f"Tell the user the rollback is done. The app will restart in a few seconds."
    )


@tool
def self_improve_pending() -> str:
    """Check if there's a pending self-improve deploy from before the last restart.

    Call this at the start of a conversation if you suspect the app just
    restarted from a deploy.  Returns details of what was deployed so you
    can report it to the user.
    """
    state = codegen_service.get_pending_deploy()
    if not state:
        return "No pending deploy — the last restart was not from a self-improve deploy."

    # Watchdog rolled back a bad deploy automatically.
    if state.get("watchdog_rollback"):
        files = state.get("files_changed", []) + state.get("files_deleted", [])
        return (
            f"WARNING: The watchdog rolled back your last deploy because the app crashed!\n"
            f"  Reverted: {state.get('reverted_commit', '?')}\n"
            f"  Reason: {state.get('reason', 'App crashed after deploy')}\n"
            f"  Rolled back at: {state.get('rolled_back_at', '?')}\n"
            f"  Branch: {state.get('branch', '?')}\n"
            f"  Files that were changed: {', '.join(files) if files else 'unknown'}\n\n"
            f"Tell the user honestly: your last fix crashed the app and the watchdog "
            f"automatically reverted it. Explain what you tried to change and why it "
            f"might have failed. Do NOT silently retry the same approach."
        )

    files = state.get("files_changed", []) + state.get("files_deleted", [])
    return (
        f"The app restarted after a self-improve deploy:\n"
        f"  Branch: {state.get('branch', '?')}\n"
        f"  Files: {', '.join(files) if files else 'unknown'}\n"
        f"  Attempt: {state.get('attempt', '?')}/{codegen_service.MAX_FIX_ATTEMPTS}\n"
        f"  Commit: {state.get('commit_message', '?')}\n\n"
        f"Tell the user what was deployed. Remind them to test it and "
        f"run git add/commit/push to preserve it."
    )


@tool
def self_improve_list() -> str:
    """List active self-improvement branches and open PRs."""
    result = codegen_service.list_branches()
    if "error" in result:
        return f"Error: {result['error']}"
    lines = ["Active worktrees:"]
    for wt in result.get("active_worktrees", []):
        lines.append(f"  {wt['branch']} -> {wt['worktree_path']}")
    if not result.get("active_worktrees"):
        lines.append("  (none)")
    lines.append("\nOpen PRs:")
    for pr in result.get("open_prs", []):
        lines.append(f"  #{pr['number']}: {pr['title']} ({pr['url']})")
    if not result.get("open_prs"):
        lines.append("  (none)")
    return "\n".join(lines)


@tool
def self_improve_cleanup(branch_name: str) -> str:
    """Clean up a self-improvement worktree after deploy or PR merge."""
    result = codegen_service.cleanup_branch(branch_name)
    if "error" in result:
        return f"Error: {result['error']}"
    return f"Cleaned up worktree for {result['branch']}"


def build_codegen_tools() -> list:
    """Return all codegen tools (for use by sub-agents that do the work)."""
    return [
        self_improve_start, self_improve_read, self_improve_write,
        self_improve_test, self_improve_lint, self_improve_verify,
        self_improve_deploy, self_improve_rollback, self_improve_submit,
        self_improve_pending, self_improve_list, self_improve_cleanup,
    ]


def build_codegen_tools_for_main_agent() -> list:
    """Return only the user-facing codegen tools for the main agent.

    Internal tools (start, read, write, test, lint, verify, deploy, submit,
    list, cleanup) are only used by the self-improvement sub-agent which
    builds its own tool list.  This keeps the main agent under the
    OpenAI 128-tool limit.
    """
    from prax.settings import settings
    if not settings.self_improve_enabled:
        return []
    return [self_improve_pending, self_improve_rollback]
