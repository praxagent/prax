"""Git-backed per-user workspace for long-term document memory."""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
from datetime import UTC, datetime

import yaml

from prax.settings import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Workspace .gitignore — written on init to every new workspace.
# Blocks media, LaTeX build artifacts, and Python caches.
# Allows: .pdf, .tex, .png, .jpg, .txt, .md, .json, etc.
# ---------------------------------------------------------------------------

_WORKSPACE_GITIGNORE = """\
# === Python ===
__pycache__/
*.py[cod]
*.pyo
*.egg-info/
*.egg
.eggs/
*.so

# === LaTeX build artifacts ===
*.aux
*.bbl
*.bcf
*.blg
*.fdb_latexmk
*.fls
*.idx
*.ilg
*.ind
*.lof
*.log
*.lot
*.nav
*.nlo
*.nls
*.out
*.run.xml
*.snm
*.synctex.gz
*.toc

# === Media (audio/video) ===
*.mp3
*.mp4
*.m4a
*.wav
*.ogg
*.flac
*.aac
*.wma
*.avi
*.mkv
*.mov
*.webm
*.wmv

# === OS junk ===
.DS_Store
Thumbs.db

# === Browser profile (binary blobs — not for git) ===
.browser_profile/

# === Rotated logs (kept as plain text for grep) ===
# archive/trace_logs/ — tracked by git for searchability

# === Shared temp dir (sandbox ↔ app scratch space) ===
.tmp/

# === Misc ===
*.tmp
*.swp
*~
"""


def safe_join(base: str, *parts: str) -> str:
    """Join paths and verify the result stays within *base*.

    Raises ``ValueError`` if the resolved path escapes the base directory
    (e.g. via ``../`` traversal or absolute path injection).
    """
    joined = os.path.normpath(os.path.join(base, *parts))
    # Use os.path.commonpath to ensure containment.
    base_resolved = os.path.realpath(base)
    joined_resolved = os.path.realpath(joined)
    if not joined_resolved.startswith(base_resolved + os.sep) and joined_resolved != base_resolved:
        raise ValueError(f"Path traversal blocked: {parts!r} escapes {base}")
    return joined


# Per-user locks to prevent concurrent git operations on the same workspace.
_workspace_locks: dict[str, threading.Lock] = {}
_lock_guard = threading.Lock()


def get_lock(user_id: str) -> threading.Lock:
    with _lock_guard:
        if user_id not in _workspace_locks:
            _workspace_locks[user_id] = threading.Lock()
        return _workspace_locks[user_id]


def workspace_root(user_id: str) -> str:
    """Return the workspace root path for *user_id* (without creating it)."""
    safe_id = user_id.lstrip("+")
    return os.path.join(settings.workspace_dir, safe_id)


def ensure_workspace(user_id: str) -> str:
    """Create workspace dirs + git init if they don't exist. Returns workspace root."""
    root = workspace_root(user_id)
    active = os.path.join(root, "active")
    archive = os.path.join(root, "archive")
    plugins_custom = os.path.join(root, "plugins", "custom")
    plugins_shared = os.path.join(root, "plugins", "shared")
    os.makedirs(active, exist_ok=True)
    os.makedirs(archive, exist_ok=True)
    os.makedirs(plugins_custom, exist_ok=True)
    os.makedirs(plugins_shared, exist_ok=True)
    if not os.path.isdir(os.path.join(root, ".git")):
        subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", settings.git_author_email],
            cwd=root, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", settings.git_author_name],
            cwd=root, check=True, capture_output=True,
        )
        # Write mandatory .gitignore.
        with open(os.path.join(root, ".gitignore"), "w", encoding="utf-8") as f:
            f.write(_WORKSPACE_GITIGNORE)
        git_commit(root, "Initialize workspace")
    # Ensure .gitignore exists even for workspaces created before this change.
    gitignore_path = os.path.join(root, ".gitignore")
    if not os.path.isfile(gitignore_path):
        with open(gitignore_path, "w", encoding="utf-8") as f:
            f.write(_WORKSPACE_GITIGNORE)
        git_commit(root, "Add workspace .gitignore")
    return root


def git_commit(root: str, message: str) -> None:
    """Stage all changes and commit if there's anything to commit."""
    r = subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True, text=True)
    if r.returncode != 0:
        logger.warning("git add -A failed (rc=%d): %s", r.returncode, r.stderr[:300])
        # Fallback: try adding without -A (just tracked files + new).
        subprocess.run(["git", "add", "."], cwd=root, capture_output=True)
    result = subprocess.run(
        ["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True,
    )
    if result.stdout.strip():
        subprocess.run(
            ["git", "commit", "-m", message], cwd=root, check=True, capture_output=True,
        )


def save_user_notes(user_id: str, content: str) -> str:
    """Save user_notes.md to the workspace root (not active/). Git commit."""
    with get_lock(user_id):
        root = ensure_workspace(user_id)
        filepath = os.path.join(root, "user_notes.md")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        git_commit(root, "Update user notes")
        logger.info("Updated user_notes.md for %s", user_id)
        return filepath


def read_user_notes(user_id: str) -> str:
    """Read user_notes.md from the workspace root. Returns empty string if missing."""
    root = workspace_root(user_id)
    filepath = os.path.join(root, "user_notes.md")
    if not os.path.isfile(filepath):
        return ""
    with open(filepath, encoding="utf-8") as f:
        return f.read()


def append_link(user_id: str, url: str, description: str = "") -> str:
    """Append a link entry to links.md in the workspace root. Git commit."""
    with get_lock(user_id):
        root = ensure_workspace(user_id)
        filepath = os.path.join(root, "links.md")

        existing = ""
        if os.path.isfile(filepath):
            with open(filepath, encoding="utf-8") as f:
                existing = f.read()

        if not existing:
            existing = "# Links\n\nAll links shared by this user.\n\n"

        timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        entry = f"- [{timestamp}] {url}"
        if description:
            entry += f" — {description}"
        entry += "\n"

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(existing + entry)

        git_commit(root, f"Log link: {url[:50]}")
        logger.info("Logged link for %s: %s", user_id, url[:80])
        return filepath


def read_links(user_id: str) -> str:
    """Read links.md from the workspace root. Returns empty string if missing."""
    root = workspace_root(user_id)
    filepath = os.path.join(root, "links.md")
    if not os.path.isfile(filepath):
        return ""
    with open(filepath, encoding="utf-8") as f:
        return f.read()


def save_file(user_id: str, filename: str, content: str) -> str:
    """Save text content to active/{filename}, git commit. Returns the file path."""
    with get_lock(user_id):
        root = ensure_workspace(user_id)
        filepath = safe_join(root, "active", filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        git_commit(root, f"Save {filename} to active workspace")
        logger.info("Saved %s to workspace for %s", filename, user_id)
        return filepath


def save_binary(user_id: str, filename: str, src_path: str) -> str:
    """Copy a binary file to archive/{filename}, git commit. Returns dest path."""
    with get_lock(user_id):
        root = ensure_workspace(user_id)
        dest = safe_join(root, "archive", filename)
        shutil.copy2(src_path, dest)
        git_commit(root, f"Archive original: {filename}")
        logger.info("Archived binary %s for %s", filename, user_id)
        return dest


def read_file(user_id: str, filename: str) -> str:
    """Read a file from active/. Raises FileNotFoundError if missing."""
    root = workspace_root(user_id)
    filepath = safe_join(root, "active", filename)
    with open(filepath, encoding="utf-8") as f:
        return f.read()


_BUILD_ARTIFACT_EXTS = frozenset({
    ".aux", ".log", ".nav", ".snm", ".toc", ".out",
    ".synctex.gz", ".fls", ".fdb_latexmk",
})


def list_active(user_id: str) -> list[str]:
    """List filenames in active/. Filters out hidden files, dirs, and build artifacts."""
    root = workspace_root(user_id)
    active_dir = os.path.join(root, "active")
    if not os.path.isdir(active_dir):
        return []
    results = []
    for f in os.listdir(active_dir):
        if f.startswith("."):
            continue
        if os.path.isdir(os.path.join(active_dir, f)):
            continue
        if any(f.endswith(ext) for ext in _BUILD_ARTIFACT_EXTS):
            continue
        results.append(f)
    return sorted(results)


def archive_file(user_id: str, filename: str) -> str:
    """Move file from active/ to archive/. Git commit. Returns new path."""
    with get_lock(user_id):
        root = ensure_workspace(user_id)
        src = safe_join(root, "active", filename)
        dst = safe_join(root, "archive", filename)
        if not os.path.exists(src):
            raise FileNotFoundError(f"{filename} not found in active workspace")
        shutil.move(src, dst)
        git_commit(root, f"Archive {filename}: moved from active to archive")
        logger.info("Archived %s for %s", filename, user_id)
        return dst


def search_archive(user_id: str, query: str) -> list[dict]:
    """Grep archive/ for query. Returns list of {filename, snippet} dicts."""
    root = workspace_root(user_id)
    archive_dir = os.path.join(root, "archive")
    if not os.path.isdir(archive_dir):
        return []
    results = []
    try:
        proc = subprocess.run(
            ["grep", "-ril", "--include=*.md", "--", query, archive_dir],
            capture_output=True, text=True, timeout=10,
        )
        for filepath in proc.stdout.strip().splitlines():
            if not filepath:
                continue
            fname = os.path.basename(filepath)
            snippet_proc = subprocess.run(
                ["grep", "-i", "-m", "3", "-C", "1", "--", query, filepath],
                capture_output=True, text=True, timeout=5,
            )
            results.append({"filename": fname, "snippet": snippet_proc.stdout.strip()[:500]})
    except subprocess.TimeoutExpired:
        logger.warning("Archive search timed out for user %s query '%s'", user_id, query)
    return results


def restore_file(user_id: str, filename: str) -> str:
    """Move file from archive/ to active/. Git commit. Returns new path."""
    with get_lock(user_id):
        root = ensure_workspace(user_id)
        src = safe_join(root, "archive", filename)
        dst = safe_join(root, "active", filename)
        if not os.path.exists(src):
            raise FileNotFoundError(f"{filename} not found in archive")
        shutil.move(src, dst)
        git_commit(root, f"Restore {filename}: moved from archive to active")
        logger.info("Restored %s for %s", filename, user_id)
        return dst


# ---------------------------------------------------------------------------
# User todo list
# ---------------------------------------------------------------------------

def _todos_path(user_id: str) -> str:
    return os.path.join(workspace_root(user_id), "todos.yaml")


def _read_todos(user_id: str) -> list[dict]:
    path = _todos_path(user_id)
    # Migrate: read legacy .json if .yaml doesn't exist yet.
    if not os.path.isfile(path):
        legacy = os.path.join(workspace_root(user_id), "todos.json")
        if os.path.isfile(legacy):
            with open(legacy, encoding="utf-8") as f:
                return json.load(f)
        return []
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or []


def _write_todos(user_id: str, todos: list[dict]) -> None:
    root = ensure_workspace(user_id)
    with open(os.path.join(root, "todos.yaml"), "w", encoding="utf-8") as f:
        yaml.dump(todos, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    # Remove legacy .json if it exists.
    legacy = os.path.join(root, "todos.json")
    if os.path.isfile(legacy):
        os.remove(legacy)
    git_commit(root, "Update todos")


def add_todo(user_id: str, task: str) -> dict:
    """Add a task to the user's todo list."""
    with get_lock(user_id):
        ensure_workspace(user_id)
        todos = _read_todos(user_id)
        entry = {
            "id": len(todos) + 1,
            "task": task,
            "done": False,
            "created_at": datetime.now(UTC).isoformat(),
        }
        todos.append(entry)
        # Re-number sequentially.
        for i, t in enumerate(todos):
            t["id"] = i + 1
        _write_todos(user_id, todos)
    return entry


def list_todos(user_id: str, show_completed: bool = False) -> list[dict]:
    """Return the user's todo list. By default hides completed items."""
    todos = _read_todos(user_id)
    if not show_completed:
        todos = [t for t in todos if not t.get("done", False)]
    return todos


def complete_todo(user_id: str, item_ids: list[int]) -> dict:
    """Mark one or more todo items as completed."""
    with get_lock(user_id):
        todos = _read_todos(user_id)
        completed = []
        for t in todos:
            if t["id"] in item_ids:
                t["done"] = True
                t["completed_at"] = datetime.now(UTC).isoformat()
                completed.append(t["id"])
        if not completed:
            return {"error": f"No todos found with ids {item_ids}"}
        _write_todos(user_id, todos)
    return {"status": "completed", "ids": completed}


def remove_todos(user_id: str, item_ids: list[int]) -> dict:
    """Remove items from the todo list entirely and re-number."""
    with get_lock(user_id):
        todos = _read_todos(user_id)
        original_len = len(todos)
        todos = [t for t in todos if t["id"] not in item_ids]
        if len(todos) == original_len:
            return {"error": f"No todos found with ids {item_ids}"}
        # Re-number sequentially.
        for i, t in enumerate(todos):
            t["id"] = i + 1
        _write_todos(user_id, todos)
    return {"status": "removed", "remaining": len(todos)}


# ---------------------------------------------------------------------------
# Agent internal plan / task decomposition
# ---------------------------------------------------------------------------

def _plan_path(user_id: str) -> str:
    return os.path.join(workspace_root(user_id), "agent_plan.yaml")


def _write_plan(root: str, plan: dict) -> None:
    with open(os.path.join(root, "agent_plan.yaml"), "w", encoding="utf-8") as f:
        yaml.dump(plan, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    # Remove legacy .json if it exists.
    legacy = os.path.join(root, "agent_plan.json")
    if os.path.isfile(legacy):
        os.remove(legacy)


def create_plan(user_id: str, goal: str, steps: list[str]) -> dict:
    """Create a multi-step plan for a complex request."""
    with get_lock(user_id):
        root = ensure_workspace(user_id)
        plan = {
            "id": f"plan-{uuid.uuid4().hex[:6]}",
            "goal": goal,
            "steps": [
                {"step": i + 1, "description": s, "done": False}
                for i, s in enumerate(steps)
            ],
            "created_at": datetime.now(UTC).isoformat(),
        }
        _write_plan(root, plan)
        git_commit(root, f"Plan: {goal[:40]}")
    return plan


def read_plan(user_id: str) -> dict | None:
    """Read the current plan. Returns None if no plan exists."""
    path = _plan_path(user_id)
    # Migrate: read legacy .json if .yaml doesn't exist yet.
    if not os.path.isfile(path):
        legacy = os.path.join(workspace_root(user_id), "agent_plan.json")
        if os.path.isfile(legacy):
            with open(legacy, encoding="utf-8") as f:
                return json.load(f)
        return None
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def complete_plan_step(user_id: str, step: int) -> dict:
    """Mark a plan step as done."""
    with get_lock(user_id):
        plan = read_plan(user_id)
        if not plan:
            return {"error": "No active plan"}
        for s in plan["steps"]:
            if s["step"] == step:
                s["done"] = True
                root = ensure_workspace(user_id)
                _write_plan(root, plan)
                git_commit(root, f"Step {step} done: {s['description'][:30]}")
                return {"status": "completed", "step": s}
        return {"error": f"Step {step} not found"}


def clear_plan(user_id: str) -> dict:
    """Remove the current plan (call when all steps are done)."""
    path = _plan_path(user_id)
    if os.path.isfile(path):
        os.remove(path)
        root = workspace_root(user_id)
        git_commit(root, "Plan completed")
    # Also clean up legacy .json.
    legacy = os.path.join(workspace_root(user_id), "agent_plan.json")
    if os.path.isfile(legacy):
        os.remove(legacy)
        root = workspace_root(user_id)
        git_commit(root, "Plan completed")
    return {"status": "cleared"}


# ---------------------------------------------------------------------------
# Instructions reference (so the agent can re-read its prompt)
# ---------------------------------------------------------------------------

def save_instructions(user_id: str, content: str) -> None:
    """Write the system prompt to instructions.md in the workspace root.

    Only writes if the content has actually changed (avoids noisy git history).
    """
    with get_lock(user_id):
        root = ensure_workspace(user_id)
        filepath = os.path.join(root, "instructions.md")
        if os.path.isfile(filepath):
            with open(filepath, encoding="utf-8") as f:
                if f.read() == content:
                    return
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        git_commit(root, "Update instructions reference")


def read_instructions(user_id: str) -> str:
    """Read the instructions reference file. Returns empty string if missing."""
    root = workspace_root(user_id)
    filepath = os.path.join(root, "instructions.md")
    if not os.path.isfile(filepath):
        return ""
    with open(filepath, encoding="utf-8") as f:
        return f.read()


def get_workspace_context(user_id: str) -> str:
    """Build a context string for the system prompt listing active workspace files.

    If ``user_notes.md`` exists in the workspace root, its contents are
    included so the agent always has the user's preferences in context.
    """
    parts: list[str] = []

    # Load user notes if they exist.
    root = workspace_root(user_id)
    notes_path = os.path.join(root, "user_notes.md")
    if os.path.isfile(notes_path):
        try:
            with open(notes_path, encoding="utf-8") as f:
                notes = f.read().strip()
            if notes:
                parts.append(
                    "\n\n## User Notes\n"
                    "Things you've learned about this user (maintained by you in user_notes.md):\n"
                    f"{notes}"
                )
        except Exception:
            pass

    # Load active plan if one exists.
    plan = read_plan(user_id)
    if plan:
        steps_text = []
        done_count = 0
        for s in plan.get("steps", []):
            mark = "x" if s.get("done") else " "
            if s.get("done"):
                done_count += 1
            steps_text.append(f"  [{mark}] {s['step']}. {s['description']}")
        total = len(plan.get("steps", []))
        parts.append(
            f"\n\n## Active Plan ({done_count}/{total} done)\n"
            f"Goal: {plan.get('goal', '(unknown)')}\n"
            + "\n".join(steps_text)
            + "\n\nContinue working through this plan. Mark steps done with "
            "agent_step_done as you complete them. Do NOT respond to the user "
            "about completed work until the relevant plan steps are actually done."
        )

    files = list_active(user_id)
    if files:
        parts.append(
            "\n\n## Active Workspace\n"
            f"The user has {len(files)} file(s) in their active workspace. "
            "Use workspace_list to see them, workspace_read to read one, "
            "workspace_send_file to deliver a file to the user (PDF, image, etc.), "
            "or workspace_archive when the conversation has moved on.\n"
            "Use workspace_search to find past documents in the archive "
            "and workspace_restore to bring them back."
        )

    return "".join(parts)


# ---------------------------------------------------------------------------
# SSH key + git remote push
# ---------------------------------------------------------------------------

_ssh_key_file: str | None = None
_ssh_key_lock = threading.Lock()


def _write_ssh_key() -> str | None:
    """Decode PRAX_SSH_KEY_B64 to a temp file. Returns path, or None if not configured."""
    global _ssh_key_file
    with _ssh_key_lock:
        if _ssh_key_file and os.path.exists(_ssh_key_file):
            return _ssh_key_file
        key_b64 = settings.ssh_key_b64
        if not key_b64:
            return None
        key_bytes = base64.b64decode(key_b64)
        fd, path = tempfile.mkstemp(prefix="prax_ssh_key_", suffix=".pem")
        os.write(fd, key_bytes)
        os.close(fd)
        os.chmod(path, 0o600)
        _ssh_key_file = path
        return path


def _git_ssh_env() -> dict[str, str] | None:
    """Return env dict with GIT_SSH_COMMAND set, or None if no SSH key configured."""
    key_path = _write_ssh_key()
    if not key_path:
        return None
    env = os.environ.copy()
    env["GIT_SSH_COMMAND"] = (
        f"ssh -i {key_path} -o StrictHostKeyChecking=no "
        f"-o UserKnownHostsFile=/dev/null"
    )
    return env


def _run_git_ssh(
    *args: str, cwd: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    """Run a git command with SSH key configured."""
    if env is None:
        env = _git_ssh_env()
    cmd = ["git"] + list(args)
    return subprocess.run(
        cmd, capture_output=True, text=True, cwd=cwd,
        env=env, timeout=60,
    )


def _verify_remote_is_private(remote_url: str) -> bool:
    """Check that a remote URL points to a private repo.

    Uses the GitHub/GitLab public API. Returns True if private (or can't determine
    for unknown hosts). Returns False if confirmed public.
    """
    import urllib.error
    import urllib.request as _req

    # Parse the URL to extract host/owner/repo.
    m = re.match(r"^git@([^:]+):([^/]+)/([^/]+?)(?:\.git)?$", remote_url.strip())
    if not m:
        m = re.match(
            r"^(?:https?|ssh)://(?:git@)?([^/]+)/([^/]+)/([^/]+?)(?:\.git)?$",
            remote_url.strip(),
        )
    if not m:
        logger.warning("Cannot parse remote URL %s — refusing push to be safe", remote_url)
        return False

    host, owner, name = m.group(1), m.group(2), m.group(3)

    if "github.com" in host:
        api_url = f"https://api.github.com/repos/{owner}/{name}"
    elif "gitlab.com" in host:
        api_url = (
            f"https://gitlab.com/api/v4/projects/"
            f"{_req.quote(f'{owner}/{name}', safe='')}"
        )
    else:
        logger.info("Unknown host %s — cannot verify visibility, refusing push", host)
        return False

    try:
        req = _req.Request(api_url, headers={"User-Agent": "prax-workspace"})
        with _req.urlopen(req, timeout=10) as resp:
            import json as _json
            data = _json.loads(resp.read())
            if "github.com" in host:
                is_private = data.get("private", False)
            else:
                is_private = data.get("visibility") != "public"
            if not is_private:
                logger.error("Remote %s/%s on %s is PUBLIC — refusing push", owner, name, host)
            return is_private
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return True  # Not publicly visible = private.
        logger.warning("Visibility check failed (HTTP %d) — refusing push", e.code)
        return False
    except Exception:
        logger.warning("Could not verify remote visibility — refusing push", exc_info=True)
        return False


def set_remote(user_id: str, remote_url: str) -> dict:
    """Set the git remote 'origin' for a user's workspace.

    Verifies the remote is a private repo before setting it.
    """
    if not _verify_remote_is_private(remote_url):
        return {"error": "Remote repo is public (or visibility could not be verified). "
                "Only private repos are allowed for workspace push."}

    with get_lock(user_id):
        root = ensure_workspace(user_id)
        # Check if origin already exists.
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=root, capture_output=True, text=True,
        )
        if result.returncode == 0:
            # Remote exists — update it.
            subprocess.run(
                ["git", "remote", "set-url", "origin", remote_url],
                cwd=root, check=True, capture_output=True,
            )
        else:
            subprocess.run(
                ["git", "remote", "add", "origin", remote_url],
                cwd=root, check=True, capture_output=True,
            )
    return {"status": "remote_set", "url": remote_url}


def push(user_id: str) -> dict:
    """Push the workspace to its remote using the configured SSH key.

    Requires: PRAX_SSH_KEY_B64 set in .env and a remote configured via set_remote().
    """
    env = _git_ssh_env()
    if not env:
        return {"error": "No SSH key configured. Set PRAX_SSH_KEY_B64 in .env."}

    with get_lock(user_id):
        root = ensure_workspace(user_id)

        # Check remote is configured.
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=root, capture_output=True, text=True,
        )
        if result.returncode != 0:
            return {"error": "No remote configured. Use workspace_set_remote first."}

        remote_url = result.stdout.strip()
        if not _verify_remote_is_private(remote_url):
            return {"error": "Remote repo is public — refusing to push."}

        # Commit any pending changes.
        git_commit(root, "Workspace sync")

        # Get current branch name.
        branch_result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=root, capture_output=True, text=True,
        )
        branch = branch_result.stdout.strip() or "main"

        # Push.
        result = _run_git_ssh("push", "-u", "origin", branch, cwd=root, env=env)
        if result.returncode != 0:
            return {"error": f"Push failed: {result.stderr[:300]}"}

    return {"status": "pushed", "branch": branch}


# ---------------------------------------------------------------------------
# Plugin import via git submodule
# ---------------------------------------------------------------------------

# Patterns that warrant a security warning when found in plugin code.
_SECURITY_PATTERNS: list[tuple[str, str]] = [
    (r"\bsubprocess\b", "subprocess calls — may execute arbitrary shell commands"),
    (r"\bos\.system\b", "os.system — executes shell commands"),
    (r"\bos\.popen\b", "os.popen — executes shell commands"),
    (r"\beval\s*\(", "eval() — executes arbitrary Python code"),
    (r"\bexec\s*\(", "exec() — executes arbitrary Python code"),
    (r"\b__import__\s*\(", "dynamic import — may load arbitrary modules"),
    (r"\brequests\.(get|post|put|delete|patch)\b", "HTTP requests to external services"),
    (r"\burllib\.request\b", "HTTP requests to external services"),
    (r"\bhttpx\b", "HTTP requests to external services"),
    (r"\bsocket\b", "raw socket access — may open network connections"),
    (r"\bos\.environ\b", "reads environment variables — may access secrets"),
    (r"\bopen\s*\(.*/etc/", "reads system files outside workspace"),
    (r"\bos\.remove\b|\bos\.unlink\b|\bshutil\.rmtree\b", "file deletion operations"),
    (r"\bbase64\.b64decode\b", "base64 decoding — may hide obfuscated code"),
    (r"\\x[0-9a-fA-F]{2}", "hex-escaped strings — possibly obfuscated code"),
]


def _ast_scan(source: str, rel_path: str = "<unknown>") -> list[dict]:
    """Parse *source* with the ``ast`` module and look for dangerous patterns.

    Returns findings in the same format as :func:`scan_plugin_security`:
    ``[{"file", "line", "pattern", "code"}, ...]``
    """
    import ast as _ast

    findings: list[dict] = []
    try:
        tree = _ast.parse(source)
    except SyntaxError:
        return findings

    source_lines = source.splitlines()

    def _code_at(lineno: int) -> str:
        if 1 <= lineno <= len(source_lines):
            return source_lines[lineno - 1].strip()[:120]
        return ""

    # Dangerous built-in function names.
    _DANGEROUS_CALLS = {"eval", "exec", "compile", "__import__"}

    for node in _ast.walk(tree):
        # 1. import subprocess / from subprocess import ...
        if isinstance(node, _ast.Import):
            for alias in node.names:
                if alias.name == "subprocess" or alias.name.startswith("subprocess."):
                    findings.append({
                        "file": rel_path,
                        "line": node.lineno,
                        "pattern": "AST: import subprocess — may execute arbitrary shell commands",
                        "code": _code_at(node.lineno),
                    })
        elif isinstance(node, _ast.ImportFrom):
            if node.module and (node.module == "subprocess" or node.module.startswith("subprocess.")):
                findings.append({
                    "file": rel_path,
                    "line": node.lineno,
                    "pattern": "AST: from subprocess import — may execute arbitrary shell commands",
                    "code": _code_at(node.lineno),
                })

        # 2–4. Calls to eval/exec/compile/__import__, os.system, os.popen
        elif isinstance(node, _ast.Call):
            func = node.func
            # Plain name calls: eval(...), exec(...), etc.
            if isinstance(func, _ast.Name) and func.id in _DANGEROUS_CALLS:
                findings.append({
                    "file": rel_path,
                    "line": node.lineno,
                    "pattern": f"AST: {func.id}() — executes arbitrary code",
                    "code": _code_at(node.lineno),
                })
            # Attribute calls: os.system(...), os.popen(...)
            elif isinstance(func, _ast.Attribute):
                if (
                    isinstance(func.value, _ast.Name)
                    and func.value.id == "os"
                    and func.attr in ("system", "popen")
                ):
                    findings.append({
                        "file": rel_path,
                        "line": node.lineno,
                        "pattern": f"AST: os.{func.attr}() — executes shell commands",
                        "code": _code_at(node.lineno),
                    })
                # 6. getattr on __builtins__
                if (
                    isinstance(func.value, _ast.Name)
                    and func.value.id == "getattr"
                ):
                    # getattr(__builtins__, ...) is caught when getattr is the call name;
                    # but getattr is a Name call, handled below.
                    pass

            # getattr(__builtins__, ...)
            if isinstance(func, _ast.Name) and func.id == "getattr":
                if node.args and isinstance(node.args[0], _ast.Name) and node.args[0].id == "__builtins__":
                    findings.append({
                        "file": rel_path,
                        "line": node.lineno,
                        "pattern": "AST: getattr(__builtins__) — may bypass restrictions",
                        "code": _code_at(node.lineno),
                    })

        # 3. Access to os.environ (attribute access, not necessarily a call)
        elif isinstance(node, _ast.Attribute):
            if (
                isinstance(node.value, _ast.Name)
                and node.value.id == "os"
                and node.attr == "environ"
            ):
                findings.append({
                    "file": rel_path,
                    "line": node.lineno,
                    "pattern": "AST: os.environ — reads environment variables / secrets",
                    "code": _code_at(node.lineno),
                })

        # 5. import socket / from socket import ...
        if isinstance(node, _ast.Import):
            for alias in node.names:
                if alias.name == "socket" or alias.name.startswith("socket."):
                    findings.append({
                        "file": rel_path,
                        "line": node.lineno,
                        "pattern": "AST: import socket — raw network access",
                        "code": _code_at(node.lineno),
                    })
        elif isinstance(node, _ast.ImportFrom):
            if node.module and (node.module == "socket" or node.module.startswith("socket.")):
                findings.append({
                    "file": rel_path,
                    "line": node.lineno,
                    "pattern": "AST: from socket import — raw network access",
                    "code": _code_at(node.lineno),
                })

    return findings


def scan_plugin_security(plugin_dir: str, subfolder: str | None = None) -> list[dict]:
    """Scan plugin Python files for potentially risky patterns.

    Returns a list of findings, each with 'file', 'line', 'pattern', and 'code'.
    An empty list means no concerns were found.

    Performs two passes:
      1. Regex-based line scanning (original patterns).
      2. AST-based tree walking (catches patterns regex may miss).
    """
    scan_root = plugin_dir
    if subfolder:
        scan_root = os.path.join(plugin_dir, subfolder)

    findings: list[dict] = []
    if not os.path.isdir(scan_root):
        return findings

    for dirpath, _dirs, files in os.walk(scan_root):
        for fname in files:
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(dirpath, fname)
            rel_path = os.path.relpath(fpath, plugin_dir)
            try:
                with open(fpath) as f:
                    source = f.read()
            except Exception:
                continue

            # Pass 1: regex scan.
            lines = source.splitlines(keepends=True)
            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                # Skip comments.
                if stripped.startswith("#"):
                    continue
                for pattern, description in _SECURITY_PATTERNS:
                    if re.search(pattern, line):
                        findings.append({
                            "file": rel_path,
                            "line": i,
                            "pattern": description,
                            "code": stripped[:120],
                        })

            # Pass 2: AST scan.
            findings.extend(_ast_scan(source, rel_path))

    return findings


def _parse_plugin_url(raw_url: str) -> tuple[str, str | None]:
    """Parse a plugin URL into (git_repo_url, subfolder | None).

    Supports:
      - ``https://github.com/owner/repo``                          → (url, None)
      - ``https://github.com/owner/repo/tree/branch/subfolder``    → (url, subfolder)
      - ``https://github.com/owner/repo.git``                      → (url, None)
      - ``git@github.com:owner/repo.git``                          → (url, None)

    The ``/tree/<branch>/<path>`` pattern is how GitHub represents subfolder
    links.  We strip it to get the bare repo URL and extract the path.
    """
    url = raw_url.strip().rstrip("/")

    # GitHub / GitLab subfolder link: .../tree/<branch>/<path>
    m = re.match(
        r"(https?://[^/]+/[^/]+/[^/]+?)"   # repo root
        r"(?:\.git)?"
        r"/tree/[^/]+/"                       # /tree/<branch>/
        r"(.+)",                              # subfolder path
        url,
    )
    if m:
        return m.group(1), m.group(2).strip("/")

    # Plain HTTPS URL — anything beyond owner/repo is a subfolder hint
    m = re.match(
        r"(https?://[^/]+/[^/]+/[^/]+?)"    # repo root
        r"(?:\.git)?$",                       # optional .git suffix, end of string
        url,
    )
    if m:
        return m.group(1), None

    # SSH shorthand: git@host:owner/repo.git
    if url.startswith("git@"):
        return url, None

    return url, None


def import_plugin_repo(
    user_id: str,
    repo_url: str,
    name: str | None = None,
    plugin_subfolder: str | None = None,
) -> dict:
    """Import a shared plugin repository as a git submodule.

    The repo is cloned into ``plugins/shared/<name>/`` within the workspace.
    Public repos are fine here (read-only import), unlike workspace push which
    requires a private remote.

    Multi-plugin repos are supported: if the repo contains multiple plugin
    subfolders (each with its own ``plugin.py``), the caller can specify
    *plugin_subfolder* to load only that one.  If omitted, the plugin loader
    will discover all ``plugin.py`` files within the cloned repo automatically.

    Args:
        user_id: The user whose workspace to import into.
        repo_url: Git URL of the plugin repo (HTTPS, SSH, or a GitHub subfolder link).
        name: Optional name for the submodule directory. Auto-derived from URL if omitted.
        plugin_subfolder: Optional subfolder within the repo to treat as the active plugin.
                          Stored in a ``.prax_plugin_filter`` file so the loader knows
                          which subfolder(s) to use.
    """
    # Parse the URL — it might contain a subfolder hint (GitHub /tree/ links).
    git_url, url_subfolder = _parse_plugin_url(repo_url)
    subfolder = plugin_subfolder or url_subfolder  # explicit arg wins

    # Derive name from the repo URL (not the subfolder).
    if not name:
        m = re.search(r"/([^/]+?)(?:\.git)?$", git_url.strip())
        if m:
            name = m.group(1)
        else:
            return {"error": f"Could not derive plugin name from URL: {repo_url}"}

    # Sanitize name — alphanumeric, hyphens, underscores only.
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
    if not safe_name:
        return {"error": f"Invalid plugin name: {name}"}

    with get_lock(user_id):
        root = ensure_workspace(user_id)
        submodule_path = os.path.join("plugins", "shared", safe_name)
        abs_submodule_path = os.path.join(root, submodule_path)

        if os.path.isdir(abs_submodule_path):
            return {"error": f"Plugin '{safe_name}' already exists. Remove it first to re-import."}

        # GIT_TERMINAL_PROMPT=0 prevents git from hanging on auth prompts
        # (e.g. if the repo doesn't exist or is private).
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        result = subprocess.run(
            ["git", "submodule", "add", git_url, submodule_path],
            cwd=root, capture_output=True, text=True, timeout=120, env=env,
        )
        if result.returncode != 0:
            return {"error": f"Submodule add failed: {result.stderr[:300]}"}

        # --- Security scan ---
        # Scan the cloned code BEFORE committing.  Return warnings so the
        # calling tool / LLM can decide whether to proceed.
        security_warnings = scan_plugin_security(abs_submodule_path, subfolder)

        # If a specific subfolder was requested, write a filter file so the
        # plugin loader only activates that subfolder.  If no subfolder, the
        # loader scans the whole repo for plugin.py files.
        # NOTE: The filter file lives NEXT TO the submodule (not inside it)
        # to avoid modifying the submodule's working tree, which breaks git add.
        if subfolder:
            filter_path = os.path.join(
                root, "plugins", "shared", f".{safe_name}_plugin_filter"
            )
            with open(filter_path, "w") as f:
                f.write(subfolder.strip("/") + "\n")

        msg = f"Import shared plugin: {safe_name}"
        if subfolder:
            msg += f" (subfolder: {subfolder})"
        git_commit(root, msg)

    return {
        "status": "imported",
        "name": safe_name,
        "path": submodule_path,
        "url": git_url,
        "subfolder": subfolder,
        "security_warnings": security_warnings,
    }


def remove_plugin_repo(user_id: str, name: str) -> dict:
    """Remove a shared plugin submodule from the workspace."""
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
    with get_lock(user_id):
        root = ensure_workspace(user_id)
        submodule_path = os.path.join("plugins", "shared", safe_name)
        abs_submodule_path = os.path.join(root, submodule_path)

        if not os.path.isdir(abs_submodule_path):
            return {"error": f"Plugin '{safe_name}' not found."}

        # Remove submodule.
        subprocess.run(
            ["git", "submodule", "deinit", "-f", submodule_path],
            cwd=root, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "rm", "-f", submodule_path],
            cwd=root, capture_output=True, text=True,
        )
        # Clean up .git/modules entry.
        git_modules = os.path.join(root, ".git", "modules", submodule_path)
        if os.path.isdir(git_modules):
            shutil.rmtree(git_modules)

        git_commit(root, f"Remove shared plugin: {safe_name}")

    return {"status": "removed", "name": safe_name}


def update_plugin_repo(user_id: str, name: str) -> dict:
    """Pull the latest version of an imported shared plugin.

    Runs ``git submodule update --remote`` to fetch the newest commit from
    the plugin's upstream branch, then re-runs the security scan.

    Returns a dict with status, changed files, and any security warnings.
    """
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
    with get_lock(user_id):
        root = ensure_workspace(user_id)
        submodule_path = os.path.join("plugins", "shared", safe_name)
        abs_submodule_path = os.path.join(root, submodule_path)

        if not os.path.isdir(abs_submodule_path):
            return {"error": f"Plugin '{safe_name}' not found."}

        # Capture the old commit hash.
        old_hash = subprocess.run(
            ["git", "-C", abs_submodule_path, "rev-parse", "HEAD"],
            capture_output=True, text=True,
        ).stdout.strip()

        # Pull latest from remote.
        result = subprocess.run(
            ["git", "submodule", "update", "--remote", "--merge", submodule_path],
            cwd=root, capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            return {"error": f"Submodule update failed: {result.stderr[:300]}"}

        new_hash = subprocess.run(
            ["git", "-C", abs_submodule_path, "rev-parse", "HEAD"],
            capture_output=True, text=True,
        ).stdout.strip()

        if old_hash == new_hash:
            return {"status": "up_to_date", "name": safe_name, "commit": old_hash[:12]}

        # Re-scan for security concerns.
        # Read existing subfolder filter if present.
        filter_path = os.path.join(
            root, "plugins", "shared", f".{safe_name}_plugin_filter"
        )
        subfolder = None
        if os.path.isfile(filter_path):
            subfolder = open(filter_path).read().strip() or None

        security_warnings = scan_plugin_security(abs_submodule_path, subfolder)

        git_commit(root, f"Update shared plugin: {safe_name} ({old_hash[:8]}→{new_hash[:8]})")

    return {
        "status": "updated",
        "name": safe_name,
        "old_commit": old_hash[:12],
        "new_commit": new_hash[:12],
        "security_warnings": security_warnings,
    }


def list_shared_plugins(user_id: str) -> list[dict]:
    """List imported shared plugin repos."""
    root = workspace_root(user_id)
    shared_dir = os.path.join(root, "plugins", "shared")
    if not os.path.isdir(shared_dir):
        return []
    results = []
    for entry in sorted(os.listdir(shared_dir)):
        entry_path = os.path.join(shared_dir, entry)
        if os.path.isdir(entry_path) and not entry.startswith("."):
            # Try to get the remote URL.
            url = ""
            try:
                r = subprocess.run(
                    ["git", "config", f"submodule.plugins/shared/{entry}.url"],
                    cwd=root, capture_output=True, text=True,
                )
                url = r.stdout.strip()
            except Exception:
                pass
            # Check for sibling subfolder filter.
            subfolder = None
            filter_path = os.path.join(shared_dir, f".{entry}_plugin_filter")
            if os.path.isfile(filter_path):
                with open(filter_path) as f:
                    subfolder = f.read().strip()
            # List plugin.py files found.
            plugins_found = []
            for dirpath, _dirs, files in os.walk(entry_path):
                if "plugin.py" in files:
                    rel = os.path.relpath(dirpath, entry_path)
                    plugins_found.append(rel if rel != "." else "(root)")
            results.append({
                "name": entry,
                "url": url,
                "subfolder_filter": subfolder,
                "plugins_found": plugins_found,
            })
    return results


def get_workspace_plugins_dir(user_id: str) -> str | None:
    """Return the path to a user's workspace plugins directory, if it exists.

    If the plugins directory contains git submodules (shared plugins), they
    are re-initialized on first access so that plugin files survive container
    restarts.
    """
    root = workspace_root(user_id)
    plugins_dir = os.path.join(root, "plugins")
    if not os.path.isdir(plugins_dir):
        return None

    # Ensure git submodules are checked out.  After a container restart the
    # workspace volume persists but submodule working trees may need init.
    shared_dir = os.path.join(plugins_dir, "shared")
    if os.path.isdir(shared_dir):
        try:
            subprocess.run(
                ["git", "submodule", "update", "--init", "--recursive"],
                cwd=root, capture_output=True, text=True, timeout=60,
            )
        except Exception:
            logger.debug("git submodule update failed for %s", user_id, exc_info=True)

    return plugins_dir


# ---------------------------------------------------------------------------
# File sharing (publish / unpublish)
# ---------------------------------------------------------------------------

# In-memory registry of published files.  token -> (absolute file path, public name).
_published_files: dict[str, tuple[str, str]] = {}
_publish_lock = threading.Lock()


def publish_file(user_id: str, relative_path: str) -> dict:
    """Publish a workspace file so it can be accessed via a public URL.

    Only the specific file is shared — not the whole workspace. Both the
    path token and the served filename are randomized UUIDs so the URL
    reveals nothing about the file's real name or contents.

    Returns dict with 'url' and 'token', or 'error'.
    """
    root = workspace_root(user_id)
    abs_path = os.path.abspath(os.path.join(root, relative_path))
    # Safety: ensure path stays within workspace.
    if not abs_path.startswith(os.path.abspath(root) + os.sep):
        return {"error": "Path escapes workspace."}
    if not os.path.isfile(abs_path):
        return {"error": f"File not found: {relative_path}"}

    from prax.utils.ngrok import get_ngrok_url
    ngrok_url = get_ngrok_url()
    if not ngrok_url:
        return {"error": "NGROK_URL is not configured — cannot generate a public link."}

    import uuid
    token = uuid.uuid4().hex  # 32 random hex chars
    # Randomize the public filename — only preserve the extension so
    # browsers / media players know the content type.
    ext = os.path.splitext(abs_path)[1]  # e.g. ".mp4"
    public_name = f"{uuid.uuid4().hex}{ext}"

    with _publish_lock:
        _published_files[token] = (abs_path, public_name)

    url = f"{ngrok_url.rstrip('/')}/shared/{token}/{public_name}"
    return {"url": url, "token": token, "file": relative_path}


def unpublish_file(token: str) -> dict:
    """Remove a previously published file share."""
    with _publish_lock:
        if token in _published_files:
            del _published_files[token]
            return {"status": "unpublished", "token": token}
    return {"error": f"Token not found: {token}"}


def get_published_file(token: str, filename: str | None = None) -> str | None:
    """Look up a published file by its share token.

    If *filename* is provided, it must match the randomized public name that
    was generated when the file was published — this prevents a valid token
    from being reused with a different filename.

    Returns the absolute path to the real file, or None.
    """
    with _publish_lock:
        entry = _published_files.get(token)
        if entry is None:
            return None
        abs_path, public_name = entry
        if filename is not None and filename != public_name:
            return None
        return abs_path


# ---------------------------------------------------------------------------
# Conversation & agent trace log
# ---------------------------------------------------------------------------

_TRACE_FILENAME = "trace.log"
_TRACE_MAX_BYTES = 512 * 1024  # 0.5 MB — rotate when exceeded
_TRACE_KEEP_ROTATED = 3  # keep last 3 rotated files


def _rotate_trace(trace_path: str) -> None:
    """Rotate trace.log when it exceeds the size limit.

    Moves trace.log → archive/trace_logs/trace.<timestamp>.log (plain text
    for grep-ability) and prunes old rotated files beyond _TRACE_KEEP_ROTATED.
    """
    try:
        if not os.path.isfile(trace_path):
            return
        if os.path.getsize(trace_path) < _TRACE_MAX_BYTES:
            return

        root = os.path.dirname(trace_path)
        archive_dir = os.path.join(root, "archive", "trace_logs")
        os.makedirs(archive_dir, exist_ok=True)

        ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        rotated = os.path.join(archive_dir, f"trace.{ts}.log")
        shutil.move(trace_path, rotated)
        # Create a fresh trace file with a pointer to archives.
        with open(trace_path, "w", encoding="utf-8") as f:
            f.write(f"=== Log rotated at {ts} — previous entries in archive/trace_logs/ ===\n")
        git_commit(root, f"Rotate trace log ({ts})")

        # Prune old rotated files.
        rotated_files = sorted(
            [f for f in os.listdir(archive_dir) if f.endswith(".log")],
            reverse=True,
        )
        for old in rotated_files[_TRACE_KEEP_ROTATED:]:
            os.remove(os.path.join(archive_dir, old))
    except OSError:
        logger.debug("Trace rotation failed for %s", trace_path, exc_info=True)


def append_trace(user_id: str, entries: list[dict]) -> None:
    """Append structured trace entries to the user's workspace trace log.

    Each entry is a dict with at least ``type`` and ``content`` keys.
    See :class:`prax.trace_events.TraceEvent` for the canonical list of
    supported types.

    The trace file is append-only, committed to git, and searchable via
    conversation_search / conversation_history tools.  Rotated to plain-text
    archive when it exceeds 0.5 MB.
    """
    if not entries:
        return
    root = workspace_root(user_id)
    if not os.path.isdir(root):
        return  # workspace not initialised yet
    trace_path = os.path.join(root, _TRACE_FILENAME)

    _rotate_trace(trace_path)

    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines: list[str] = [f"\n=== {ts} ===\n"]
    for entry in entries:
        tag = entry.get("type", "unknown").upper()
        content = entry.get("content", "")
        # Truncate very long content to keep the file manageable.
        if len(content) > 5000:
            content = content[:5000] + "\n... [truncated]"
        lines.append(f"[{tag}] {content}\n")
    try:
        with open(trace_path, "a", encoding="utf-8") as f:
            f.writelines(lines)
    except OSError:
        logger.debug("Failed to write trace log for %s", user_id, exc_info=True)


def read_trace_tail(user_id: str, lines: int = 200) -> str:
    """Return the last *lines* lines of the user's trace log."""
    root = workspace_root(user_id)
    trace_path = os.path.join(root, _TRACE_FILENAME)
    if not os.path.isfile(trace_path):
        return ""
    with open(trace_path, encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()
    return "".join(all_lines[-lines:])


def _search_trace_file(path: str, query_lower: str, results: list[dict],
                       max_results: int,
                       type_filter: str | None = None) -> None:
    """Search a single trace file for blocks matching *query_lower*.

    If *type_filter* is given (e.g. ``"audit"``, ``"tool_call"``), only blocks
    that contain at least one line with the corresponding ``[TAG]`` prefix are
    returned, and the excerpt only includes lines matching that tag.
    """
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8", errors="replace") as f:
        content = f.read()

    tag_prefix: str | None = None
    if type_filter is not None:
        tag_prefix = f"[{type_filter.upper()}]"

    import re as _re
    blocks = _re.split(r"\n(?==== \d{4}-)", content)
    for block in reversed(blocks):
        if len(results) >= max_results:
            return
        if query_lower not in block.lower():
            continue
        ts_match = _re.match(r"=== (\S+) ===", block.strip())
        ts = ts_match.group(1) if ts_match else "unknown"
        excerpt_lines = []
        if tag_prefix is not None:
            # Only include lines that match both the type tag and the query.
            for line in block.splitlines():
                stripped = line.strip()
                if stripped.startswith(tag_prefix) and query_lower in line.lower():
                    excerpt_lines.append(stripped)
            # Skip block entirely if no lines match the type filter.
            if not excerpt_lines:
                continue
        else:
            for line in block.splitlines():
                if query_lower in line.lower():
                    excerpt_lines.append(line.strip())
        excerpt = "\n".join(excerpt_lines[:5])
        if len(excerpt) > 500:
            excerpt = excerpt[:500] + "..."
        results.append({"timestamp": ts, "excerpt": excerpt})


def search_trace(user_id: str, query: str, max_results: int = 20,
                 type_filter: str | None = None) -> list[dict]:
    """Search the trace log for blocks containing *query*.

    Searches both the current trace.log and any rotated plain-text
    archives.  Returns a list of dicts with ``timestamp`` and ``excerpt``
    keys, most recent first.

    If *type_filter* is given (e.g. ``"audit"``, ``"tool_call"``), only
    blocks containing at least one line with the corresponding ``[TAG]``
    prefix are returned, and excerpts only include matching-type lines.
    """
    root = workspace_root(user_id)
    query_lower = query.lower()
    results: list[dict] = []

    # Search current trace first (most recent).
    trace_path = os.path.join(root, _TRACE_FILENAME)
    _search_trace_file(trace_path, query_lower, results, max_results,
                       type_filter=type_filter)

    # Then search archived files newest-first.
    archive_dir = os.path.join(root, "archive", "trace_logs")
    if os.path.isdir(archive_dir):
        for fname in sorted(os.listdir(archive_dir), reverse=True):
            if len(results) >= max_results:
                break
            if fname.endswith(".log"):
                _search_trace_file(
                    os.path.join(archive_dir, fname),
                    query_lower, results, max_results,
                    type_filter=type_filter,
                )

    return results


# ---------------------------------------------------------------------------
# Deprecated aliases — old underscore-prefixed names kept for backward compat.
# New code should import the public names above.
# ---------------------------------------------------------------------------
_workspace_root = workspace_root
_safe_join = safe_join
_ensure_workspace = ensure_workspace
_get_lock = get_lock
_git_commit = git_commit
