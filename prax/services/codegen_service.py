"""Self-improvement code generation service — staging clone + verify + deploy.

The agent works in an isolated staging clone, never modifying the live repo
directly.  Changes are verified (tests + lint + startup check) before being
hot-swapped into the live directory.  Flask's Werkzeug reloader picks up
the changes automatically.

For complex changes requiring human review, ``submit_pr()`` pushes to GitHub
and opens a PR (the agent **cannot** merge to main).

Gated behind ``SELF_IMPROVE_ENABLED=true``.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from typing import Any

import yaml

from prax.settings import settings

logger = logging.getLogger(__name__)

# Active worktrees: {branch_name: worktree_path}
_worktrees: dict[str, str] = {}

# Path to the staging clone (set once by _ensure_staging).
_staging_repo: str | None = None


def _normalize_branch(branch_name: str) -> str:
    """Strip the ``self-improve/`` prefix if the LLM passed the full ref."""
    if branch_name.startswith("self-improve/"):
        return branch_name[len("self-improve/"):]
    return branch_name


def _get_worktree(branch_name: str) -> str | None:
    """Look up a worktree by branch name, recovering from disk if needed.

    The in-memory ``_worktrees`` dict is lost on Werkzeug reloader restarts.
    If a worktree directory still exists on disk, re-register it so the
    current process can continue using it.
    """
    branch_name = _normalize_branch(branch_name)

    wt = _worktrees.get(branch_name)
    if wt and os.path.isdir(wt):
        return wt

    # Try to recover from disk.
    candidate = os.path.join(tempfile.gettempdir(), f"self-improve-{branch_name}")
    if os.path.isdir(candidate) and os.path.isdir(os.path.join(candidate, ".git")):
        logger.info("Recovered worktree from disk: %s -> %s", branch_name, candidate)
        _worktrees[branch_name] = candidate
        return candidate

    return None

# Max fix attempts per branch before forcing a stop.
MAX_FIX_ATTEMPTS = 3

# Deploy state file — survives restarts.
_DEPLOY_STATE_FILE = ".self-improve-state.yaml"


def _state_path() -> str:
    return os.path.join(_live_repo(), _DEPLOY_STATE_FILE)


def _read_state() -> dict:
    path = _state_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _write_state(state: dict) -> None:
    path = _state_path()
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(state, f, default_flow_style=False, sort_keys=False)


def _clear_state() -> None:
    path = _state_path()
    if os.path.isfile(path):
        os.remove(path)


def get_pending_deploy() -> dict | None:
    """Return the last deploy info if the app restarted after a deploy.

    Called on startup / first turn to let the agent report what changed.
    Also detects watchdog-initiated rollbacks (app crashed after deploy).
    Returns the state dict and clears the flags, or None.
    """
    state = _read_state()

    # Watchdog rolled back a bad deploy — report that first.
    if state.get("watchdog_rollback"):
        rollback_info = state.pop("watchdog_rollback")
        state["pending"] = False
        _write_state(state)
        return {
            "watchdog_rollback": True,
            "reverted_commit": rollback_info.get("reverted_commit", "?"),
            "reason": rollback_info.get("reason", "App crashed"),
            "rolled_back_at": rollback_info.get("rolled_back_at", "?"),
            "branch": state.get("branch", "?"),
            "files_changed": state.get("files_changed", []),
            "files_deleted": state.get("files_deleted", []),
            "attempt": state.get("attempt", "?"),
        }

    if state.get("pending"):
        state["pending"] = False
        _write_state(state)
        return state

    return None


def get_fix_attempts(branch_name: str) -> int:
    """Return how many fix attempts have been made for a branch."""
    branch_name = _normalize_branch(branch_name)
    state = _read_state()
    return state.get("attempts", {}).get(branch_name, 0)


def rollback_last_deploy() -> dict[str, Any]:
    """Revert the most recent self-improve deploy commit in the live repo.

    Only reverts if the last commit is a self-improve deploy.
    """
    if not _enabled():
        return {"error": "Self-improvement is disabled"}

    live = _live_repo()
    result = _run(["git", "log", "-1", "--format=%s"], cwd=live)
    last_msg = result.stdout.strip()

    if not last_msg.startswith("self-improve deploy:"):
        return {"error": f"Last commit is not a self-improve deploy: '{last_msg}'"}

    result = _run(["git", "revert", "HEAD", "--no-edit"], cwd=live)
    if result.returncode != 0:
        return {"error": f"Revert failed: {result.stderr.strip()}"}

    # Clear deploy state.
    _clear_state()

    logger.info("Rolled back self-improve deploy: %s", last_msg)
    return {
        "status": "rolled_back",
        "reverted_commit": last_msg,
        "message": "Deploy reverted. The app will restart from the file changes.",
    }


def _enabled() -> bool:
    return settings.self_improve_enabled


def _live_repo() -> str:
    return settings.self_improve_repo_path or os.getcwd()


def _staging_path() -> str:
    return os.path.join(tempfile.gettempdir(), "self-improve-staging")


def _run(cmd: list[str], cwd: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=120, **kwargs
    )


# ---------------------------------------------------------------------------
# Staging clone management
# ---------------------------------------------------------------------------

def _ensure_staging() -> str:
    """Ensure a staging clone of the live repo exists and is up to date.

    The clone's ``origin`` points to the live repo path.  A second remote
    ``github`` is added pointing to the live repo's GitHub remote so that
    ``submit_pr`` can push branches upstream.
    """
    global _staging_repo
    staging = _staging_path()
    live = _live_repo()

    # Verify the live repo is actually a git repository.
    if not os.path.isdir(os.path.join(live, ".git")):
        raise RuntimeError(
            f"No git repo at {live}. In Docker dev mode, mount .git: "
            "add '- ./.git:/app/.git:ro' to docker-compose.dev.yml volumes."
        )

    if _staging_repo and os.path.isdir(os.path.join(staging, ".git")):
        # Update existing clone — verify each step succeeds.
        fetch = _run(["git", "fetch", "origin"], cwd=staging)
        checkout = _run(["git", "checkout", "main"], cwd=staging)
        reset = _run(["git", "reset", "--hard", "origin/main"], cwd=staging)

        if fetch.returncode == 0 and checkout.returncode == 0 and reset.returncode == 0:
            # Verify the staging HEAD matches the live repo's main.
            live_head = _run(["git", "rev-parse", "main"], cwd=live)
            staging_head = _run(["git", "rev-parse", "HEAD"], cwd=staging)
            if live_head.stdout.strip() == staging_head.stdout.strip():
                return staging
            logger.warning(
                "Staging HEAD %s != live main %s — recreating clone",
                staging_head.stdout.strip()[:8],
                live_head.stdout.strip()[:8],
            )
        else:
            logger.warning("Staging update failed — recreating clone")

        # Fast-path failed — fall through to fresh clone.
        _staging_repo = None

    # Create fresh clone.
    if os.path.exists(staging):
        shutil.rmtree(staging)

    result = _run(["git", "clone", live, staging], cwd=live)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to clone staging repo: {result.stderr.strip()}")

    # Ensure git identity is configured in the staging clone.
    _run(["git", "config", "user.email", settings.git_author_email], cwd=staging)
    _run(["git", "config", "user.name", settings.git_author_name], cwd=staging)

    # Add a "github" remote pointing to the live repo's upstream.
    gh_url = _run(["git", "remote", "get-url", "origin"], cwd=live)
    if gh_url.returncode == 0 and gh_url.stdout.strip():
        _run(["git", "remote", "add", "github", gh_url.stdout.strip()], cwd=staging)

    _staging_repo = staging
    logger.info("Created staging clone at %s", staging)
    return staging


# ---------------------------------------------------------------------------
# Worktree lifecycle
# ---------------------------------------------------------------------------

def start_branch(branch_name: str, description: str = "") -> dict[str, Any]:
    """Create an isolated git worktree off the staging clone."""
    if not _enabled():
        return {"error": "Self-improvement is disabled (SELF_IMPROVE_ENABLED=false)"}

    branch_name = _normalize_branch(branch_name)
    full_branch = f"self-improve/{branch_name}"

    if _get_worktree(branch_name):
        return {"error": f"Branch '{branch_name}' already has an active worktree"}

    try:
        staging = _ensure_staging()
    except RuntimeError as exc:
        return {"error": str(exc)}

    worktree_path = os.path.join(tempfile.gettempdir(), f"self-improve-{branch_name}")
    if os.path.exists(worktree_path):
        shutil.rmtree(worktree_path)

    # Determine the base ref: origin/main → main → HEAD.
    base_ref = "HEAD"
    for candidate in ("origin/main", "main", "HEAD"):
        check = _run(["git", "rev-parse", "--verify", candidate], cwd=staging)
        if check.returncode == 0:
            base_ref = candidate
            break

    # Create worktree with new branch from the base ref.
    result = _run(
        ["git", "worktree", "add", worktree_path, "-b", full_branch, base_ref],
        cwd=staging,
    )
    if result.returncode != 0:
        # Branch might already exist — try checking out.
        result = _run(
            ["git", "worktree", "add", worktree_path, full_branch],
            cwd=staging,
        )
        if result.returncode != 0:
            return {"error": f"Failed to create worktree: {result.stderr.strip()}"}

    _worktrees[branch_name] = worktree_path
    logger.info("Created self-improve worktree: %s at %s", full_branch, worktree_path)
    return {
        "status": "created",
        "branch": full_branch,
        "worktree_path": worktree_path,
        "description": description,
    }


# ---------------------------------------------------------------------------
# File operations
# ---------------------------------------------------------------------------

def _safe_join(base: str, *parts: str) -> str:
    """Join paths and verify the result stays within *base*."""
    joined = os.path.normpath(os.path.join(base, *parts))
    base_resolved = os.path.realpath(base)
    joined_resolved = os.path.realpath(joined)
    if not joined_resolved.startswith(base_resolved + os.sep) and joined_resolved != base_resolved:
        raise ValueError(f"Path traversal blocked: {parts!r} escapes {base}")
    return joined


def read_file(branch_name: str, filepath: str) -> dict[str, Any]:
    """Read a file from the worktree."""
    if not _enabled():
        return {"error": "Self-improvement is disabled"}

    wt = _get_worktree(branch_name)
    if not wt:
        return {"error": f"No active worktree for branch '{branch_name}'"}

    try:
        full_path = _safe_join(wt, filepath)
    except ValueError as exc:
        return {"error": str(exc)}
    if not os.path.isfile(full_path):
        return {"error": f"File not found: {filepath}"}

    with open(full_path, encoding="utf-8") as f:
        content = f.read()
    return {"filepath": filepath, "content": content}


def write_file(branch_name: str, filepath: str, content: str) -> dict[str, Any]:
    """Write a file in the worktree."""
    if not _enabled():
        return {"error": "Self-improvement is disabled"}

    wt = _get_worktree(branch_name)
    if not wt:
        return {"error": f"No active worktree for branch '{branch_name}'"}

    try:
        full_path = _safe_join(wt, filepath)
    except ValueError as exc:
        return {"error": str(exc)}
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(content)

    return {"status": "written", "filepath": filepath, "size": len(content)}


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def run_tests(branch_name: str) -> dict[str, Any]:
    """Run the test suite in the worktree."""
    if not _enabled():
        return {"error": "Self-improvement is disabled"}

    wt = _get_worktree(branch_name)
    if not wt:
        return {"error": f"No active worktree for branch '{branch_name}'"}

    result = _run(["uv", "run", "pytest", "tests/", "-x", "-q"], cwd=wt)
    passed = result.returncode == 0
    return {
        "status": "passed" if passed else "failed",
        "return_code": result.returncode,
        "stdout": result.stdout[-2000:] if result.stdout else "",
        "stderr": result.stderr[-1000:] if result.stderr else "",
    }


def run_lint(branch_name: str) -> dict[str, Any]:
    """Run ruff lint in the worktree."""
    if not _enabled():
        return {"error": "Self-improvement is disabled"}

    wt = _get_worktree(branch_name)
    if not wt:
        return {"error": f"No active worktree for branch '{branch_name}'"}

    result = _run(["uv", "run", "ruff", "check", "."], cwd=wt)
    clean = result.returncode == 0
    return {
        "status": "clean" if clean else "issues_found",
        "return_code": result.returncode,
        "output": result.stdout[-2000:] if result.stdout else "",
    }


def verify_startup(branch_name: str) -> dict[str, Any]:
    """Verify the app can start in the worktree (import + create_app)."""
    if not _enabled():
        return {"error": "Self-improvement is disabled"}

    wt = _get_worktree(branch_name)
    if not wt:
        return {"error": f"No active worktree for branch '{branch_name}'"}

    result = _run(
        ["uv", "run", "python", "-c",
         "from app import create_app; create_app()"],
        cwd=wt,
    )
    passed = result.returncode == 0
    return {
        "status": "passed" if passed else "failed",
        "return_code": result.returncode,
        "stdout": result.stdout[-1000:] if result.stdout else "",
        "stderr": result.stderr[-1000:] if result.stderr else "",
    }


# ---------------------------------------------------------------------------
# Deploy (hot-swap verified code into the live repo)
# ---------------------------------------------------------------------------

def verify_and_deploy(branch_name: str, commit_message: str = "") -> dict[str, Any]:
    """Verify changes then hot-swap into the live repo.

    Pipeline (all must pass):
      1. Commit changes in worktree
      2. Run tests
      3. Run lint
      4. Verify app startup
      5. Copy changed files to live repo + commit

    Flask's Werkzeug reloader picks up the file changes automatically.
    """
    if not _enabled():
        return {"error": "Self-improvement is disabled"}

    branch_name = _normalize_branch(branch_name)

    # Enforce max fix attempts.
    attempts = get_fix_attempts(branch_name)
    if attempts >= MAX_FIX_ATTEMPTS:
        return {
            "error": (
                f"Max fix attempts ({MAX_FIX_ATTEMPTS}) reached for '{branch_name}'. "
                f"Stop retrying and tell the user what's failing so they can fix it manually. "
                f"Use self_improve_rollback if a broken deploy is live."
            ),
        }

    wt = _get_worktree(branch_name)
    if not wt:
        return {"error": f"No active worktree for branch '{branch_name}'"}

    full_branch = f"self-improve/{branch_name}"
    live = _live_repo()
    msg = commit_message or f"self-improve: {branch_name}"

    # --- Step 1: Commit in worktree ---
    _run(["git", "add", "-A"], cwd=wt)
    status = _run(["git", "status", "--porcelain"], cwd=wt)
    if not status.stdout.strip():
        return {"error": "No changes to deploy"}

    result = _run(["git", "commit", "-m", msg], cwd=wt)
    if result.returncode != 0:
        return {"error": f"Commit failed: {result.stderr.strip()}"}

    # --- Step 2: Run tests ---
    test_result = run_tests(branch_name)
    if test_result.get("status") != "passed":
        return {
            "error": "Verification failed: tests did not pass",
            "stage": "tests",
            "details": test_result,
        }

    # --- Step 3: Run lint ---
    lint_result = run_lint(branch_name)
    if lint_result.get("status") != "clean":
        return {
            "error": "Verification failed: lint issues found",
            "stage": "lint",
            "details": lint_result,
        }

    # --- Step 4: Verify startup ---
    startup_result = verify_startup(branch_name)
    if startup_result.get("status") != "passed":
        return {
            "error": "Verification failed: app startup check failed",
            "stage": "startup",
            "details": startup_result,
        }

    # --- Step 5: Hot-swap to live ---
    base = _run(["git", "merge-base", "main", "HEAD"], cwd=wt)
    base_sha = base.stdout.strip() or "HEAD~1"

    # Changed/added files.
    diff_added = _run(
        ["git", "diff", "--name-only", "--diff-filter=ACMR", f"{base_sha}..HEAD"],
        cwd=wt,
    )
    changed_files = [f for f in diff_added.stdout.strip().split("\n") if f]

    # Deleted files.
    diff_deleted = _run(
        ["git", "diff", "--name-only", "--diff-filter=D", f"{base_sha}..HEAD"],
        cwd=wt,
    )
    deleted_files = [f for f in diff_deleted.stdout.strip().split("\n") if f]

    if not changed_files and not deleted_files:
        return {"error": "No file differences detected between worktree and base"}

    # Copy changed files to live repo.
    for filepath in changed_files:
        src = os.path.join(wt, filepath)
        dst = os.path.join(live, filepath)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)

    # Remove deleted files from live repo.
    for filepath in deleted_files:
        dst = os.path.join(live, filepath)
        if os.path.exists(dst):
            os.remove(dst)

    # Commit in live repo — only stage the specific files we touched.
    if changed_files:
        _run(["git", "add", "--"] + changed_files, cwd=live)
    if deleted_files:
        _run(["git", "rm", "--cached", "--ignore-unmatch", "--"] + deleted_files, cwd=live)
    # Rewrite commit message to use conventional commit scope.
    # "docs: polish README" → "docs(self-improve): polish README"
    # "fix typo" (no prefix) → "chore(self-improve): fix typo"
    import re as _re
    m = _re.match(r"^(\w+)(\(.+?\))?:\s*", msg)
    if m:
        prefix = m.group(1)
        rest = msg[m.end():]
        deploy_msg = f"{prefix}(self-improve): {rest}"
    else:
        deploy_msg = f"chore(self-improve): {msg}"
    # Use --author so we don't overwrite the repo's git config.
    author = f"{settings.git_author_name} <{settings.git_author_email}>"
    _run(["git", "commit", "-m", deploy_msg, "--author", author], cwd=live)

    deployed_count = len(changed_files) + len(deleted_files)
    logger.info(
        "Deployed %d files from %s to live repo", deployed_count, full_branch,
    )

    # Persist deploy state so it survives the restart.
    state = _read_state()
    attempts_map = state.get("attempts", {})
    attempts_map[branch_name] = attempts + 1
    _write_state({
        "pending": True,
        "branch": full_branch,
        "files_changed": changed_files,
        "files_deleted": deleted_files,
        "commit_message": msg,
        "attempt": attempts + 1,
        "attempts": attempts_map,
    })

    return {
        "status": "deployed",
        "branch": full_branch,
        "files_changed": changed_files,
        "files_deleted": deleted_files,
        "attempt": attempts + 1,
        "max_attempts": MAX_FIX_ATTEMPTS,
        "message": (
            f"Deployed {deployed_count} file(s) (attempt {attempts + 1}/{MAX_FIX_ATTEMPTS}). "
            "Werkzeug reloader will restart the app."
        ),
    }


# ---------------------------------------------------------------------------
# PR workflow (for changes needing human review)
# ---------------------------------------------------------------------------

def submit_pr(branch_name: str, title: str, body: str = "") -> dict[str, Any]:
    """Commit, push to GitHub, and create a PR.  Never merges."""
    if not _enabled():
        return {"error": "Self-improvement is disabled"}

    wt = _get_worktree(branch_name)
    if not wt:
        return {"error": f"No active worktree for branch '{branch_name}'"}

    full_branch = f"self-improve/{branch_name}"

    # Stage + commit.
    _run(["git", "add", "-A"], cwd=wt)
    status = _run(["git", "status", "--porcelain"], cwd=wt)
    if not status.stdout.strip():
        return {"error": "No changes to commit"}

    commit_msg = f"{title}\n\nSelf-improvement PR created by the agent.\n\n{body}"
    result = _run(["git", "commit", "-m", commit_msg], cwd=wt)
    if result.returncode != 0:
        return {"error": f"Commit failed: {result.stderr.strip()}"}

    # Push to GitHub via the "github" remote (set up by _ensure_staging).
    staging = _staging_path()
    remote = "github"
    check_remote = _run(["git", "remote", "get-url", remote], cwd=staging)
    if check_remote.returncode != 0:
        remote = "origin"  # Fallback if github remote wasn't configured.

    result = _run(["git", "push", "-u", remote, full_branch], cwd=wt)
    if result.returncode != 0:
        return {"error": f"Push failed: {result.stderr.strip()}"}

    # Create PR.
    pr_body = (
        f"## Self-Improvement PR\n\n{body}\n\n"
        "---\n"
        "This PR was created by the agent's self-improvement system.\n"
        "**Review and merge manually** — the agent cannot merge to main."
    )
    result = _run(
        ["gh", "pr", "create", "--base", "main", "--head", full_branch,
         "--title", title, "--body", pr_body],
        cwd=wt,
    )
    if result.returncode != 0:
        return {"error": f"PR creation failed: {result.stderr.strip()}"}

    pr_url = result.stdout.strip()
    logger.info("Created self-improvement PR: %s", pr_url)
    return {"status": "submitted", "branch": full_branch, "pr_url": pr_url}


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def cleanup_branch(branch_name: str) -> dict[str, Any]:
    """Remove the worktree and clean up."""
    if not _enabled():
        return {"error": "Self-improvement is disabled"}

    branch_name = _normalize_branch(branch_name)
    # Recover from disk if needed (Werkzeug restarts clear _worktrees).
    _get_worktree(branch_name)
    wt = _worktrees.pop(branch_name, None)
    if not wt:
        return {"error": f"No active worktree for branch '{branch_name}'"}

    staging = _staging_path()
    _run(["git", "worktree", "remove", wt, "--force"], cwd=staging)
    if os.path.exists(wt):
        shutil.rmtree(wt, ignore_errors=True)

    logger.info("Cleaned up worktree for %s", branch_name)
    return {"status": "cleaned_up", "branch": f"self-improve/{branch_name}"}


def list_branches() -> dict[str, Any]:
    """List active self-improvement worktrees and open PRs."""
    if not _enabled():
        return {"error": "Self-improvement is disabled"}

    active = [
        {"branch": f"self-improve/{name}", "worktree_path": path}
        for name, path in _worktrees.items()
    ]

    # List open PRs from self-improve branches (requires gh CLI).
    live = _live_repo()
    prs = []
    try:
        result = _run(
            ["gh", "pr", "list", "--head", "self-improve/", "--state", "open", "--json",
             "number,title,url,headRefName"],
            cwd=live,
        )
        if result.returncode == 0 and result.stdout.strip():
            prs = json.loads(result.stdout)
    except FileNotFoundError:
        logger.debug("gh CLI not installed — skipping PR list")
    except Exception:
        pass

    return {"active_worktrees": active, "open_prs": prs}
