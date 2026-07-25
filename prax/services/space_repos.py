"""Git repositories attached to a Library space.

A space is where a piece of work lives — its notes, its Kanban board, its files.
Attaching the repositories that work actually happens in lets the agent read the
code, see what changed, and connect a task to the branch that implements it.

Three properties this module exists to guarantee:

**Isolation by space.** Every repo is cloned inside its own space
(``library/spaces/{slug}/repos/{name}``). A space cannot see another space's
checkouts, and nothing here can escape the user's workspace — names are
validated and the resolved path is re-checked against its space root, so a
crafted name like ``../../../etc`` fails rather than traverses.

**Write is off until a human turns it on.** ``write: false`` is the default on
every attachment. Reading a repository and pushing to it are different risk
classes, and the second one should require somebody to have decided.

**Credentials are per-repo and live outside the workspace.** Each attachment
gets its own deploy key. A deploy key belongs to exactly one repository, so a
leaked key exposes one repo rather than everything the account can reach — which
is the difference between a scoped credential and an account-wide SSH key. The
private key is written under ``~/.prax/git-keys`` with mode 0600, never inside
the workspace: the workspace is itself a git repo that commits everything not
ignored, so a key stored there would end up in its history.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

REPOS_DIR = "repos"
SPACE_META = ".space.yaml"

# Deploy keys live outside the workspace — see the module docstring.
KEY_ROOT = Path(os.path.expanduser("~/.prax/git-keys"))

# A repo name becomes a directory name. Keep it boring so it cannot traverse.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

_GIT_TIMEOUT = 300


class RepoError(Exception):
    """An attach/clone/write operation was refused or failed."""


def _space_dir(user_id: str, slug: str) -> Path:
    from prax.services.library_service import _library_root  # local: layering

    return _library_root(user_id) / "spaces" / slug


def _repos_dir(user_id: str, slug: str) -> Path:
    return _space_dir(user_id, slug) / REPOS_DIR


def _validate_name(name: str) -> str:
    if not _SAFE_NAME.match(name or ""):
        raise RepoError(
            f"invalid repo name {name!r} — letters, digits, dot, dash and "
            "underscore only, up to 64 characters")
    return name


def _resolved_repo_path(user_id: str, slug: str, name: str) -> Path:
    """Resolve a repo's checkout path, refusing anything outside its space.

    The name is validated first, but resolve-and-compare is kept as a second
    check: symlinks and ``..`` are exactly the sort of thing a name-only guard
    misses, and the cost of being wrong here is reading or writing somebody
    else's files.
    """
    _validate_name(name)
    root = _repos_dir(user_id, slug).resolve()
    target = (root / name).resolve()
    if target != root and root not in target.parents:
        raise RepoError(f"repo path for {name!r} escapes its space")
    return target


def _read_meta(user_id: str, slug: str) -> dict[str, Any]:
    meta_file = _space_dir(user_id, slug) / SPACE_META
    if not meta_file.exists():
        raise RepoError(f"no such space: {slug}")
    return yaml.safe_load(meta_file.read_text(encoding="utf-8")) or {}


def _write_meta(user_id: str, slug: str, meta: dict[str, Any]) -> None:
    meta_file = _space_dir(user_id, slug) / SPACE_META
    meta_file.write_text(yaml.safe_dump(meta, sort_keys=False), encoding="utf-8")


def _key_path(user_id: str, slug: str, name: str) -> Path:
    return KEY_ROOT / user_id / slug / name


def generate_deploy_key(user_id: str, slug: str, name: str) -> str:
    """Create a per-repo deploy key, returning the PUBLIC half to register.

    One key per repository is the whole point: a deploy key is registered on a
    single repo, so it cannot be replayed against anything else the account can
    see.
    """
    _validate_name(name)
    key = _key_path(user_id, slug, name)
    key.parent.mkdir(parents=True, exist_ok=True)
    if key.exists():
        return (key.with_suffix(".pub")).read_text(encoding="utf-8").strip()

    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-f", str(key), "-N", "", "-q",
         "-C", f"prax-{user_id}-{slug}-{name}"],
        check=True, capture_output=True, timeout=60,
    )
    key.chmod(0o600)
    return key.with_suffix(".pub").read_text(encoding="utf-8").strip()


def _git_env(user_id: str, slug: str, name: str) -> dict[str, str]:
    """Environment pinning git to this repo's own deploy key.

    ``IdentitiesOnly=yes`` matters: without it ssh offers every key the agent
    has, and a repo could be reached with a credential that was never meant for
    it — silently widening the scope this module exists to narrow.
    """
    env = dict(os.environ)
    key = _key_path(user_id, slug, name)
    if key.exists():
        env["GIT_SSH_COMMAND"] = (
            f"ssh -i {key} -o IdentitiesOnly=yes "
            "-o StrictHostKeyChecking=accept-new"
        )
    return env


def _run_git(args: list[str], cwd: Path, env: dict[str, str] | None = None) -> str:
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                       text=True, env=env, timeout=_GIT_TIMEOUT)
    if r.returncode != 0:
        raise RepoError((r.stderr or r.stdout or "git failed").strip()[:400])
    return (r.stdout or "").strip()


# ── Attach / detach ──────────────────────────────────────────────────────────

def attach(user_id: str, slug: str, url: str, name: str,
           *, write: bool = False, clone: bool = True) -> dict[str, Any]:
    """Attach a repository to a space and clone it.

    Returns the entry plus the deploy key's public half, which a human must add
    to the repository before the clone can succeed for a private repo.
    """
    _validate_name(name)
    meta = _read_meta(user_id, slug)
    repos = meta.get("repos") or []
    if any(r.get("name") == name for r in repos):
        raise RepoError(f"a repo named {name!r} is already attached to {slug}")

    public_key = generate_deploy_key(user_id, slug, name)
    entry = {
        "name": name,
        "url": url,
        # Off by default, always. Turning this on is a decision a person makes.
        "write": bool(write),
        "default_branch": None,
    }

    if clone:
        target = _resolved_repo_path(user_id, slug, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise RepoError(f"{target} already exists")
        try:
            _run_git(["clone", url, str(target)], cwd=target.parent,
                     env=_git_env(user_id, slug, name))
        except RepoError as exc:
            raise RepoError(
                f"clone failed: {exc}\n\nIf this repository is private, add this "
                f"deploy key to it first:\n{public_key}") from exc
        try:
            entry["default_branch"] = _run_git(
                ["rev-parse", "--abbrev-ref", "HEAD"], cwd=target)
        except RepoError:
            pass

    repos.append(entry)
    meta["repos"] = repos
    _write_meta(user_id, slug, meta)
    return {"repo": entry, "public_key": public_key}


def detach(user_id: str, slug: str, name: str, *, delete_checkout: bool = True) -> bool:
    """Remove an attachment. The deploy key is destroyed with it."""
    meta = _read_meta(user_id, slug)
    repos = meta.get("repos") or []
    remaining = [r for r in repos if r.get("name") != name]
    if len(remaining) == len(repos):
        return False

    if delete_checkout:
        target = _resolved_repo_path(user_id, slug, name)
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)

    key = _key_path(user_id, slug, name)
    for p in (key, key.with_suffix(".pub")):
        if p.exists():
            p.unlink()

    meta["repos"] = remaining
    _write_meta(user_id, slug, meta)
    return True


def list_repos(user_id: str, slug: str) -> list[dict[str, Any]]:
    repos = []
    for entry in (_read_meta(user_id, slug).get("repos") or []):
        item = dict(entry)
        try:
            item["cloned"] = _resolved_repo_path(user_id, slug, entry["name"]).exists()
        except RepoError:
            item["cloned"] = False
        repos.append(item)
    return repos


def _entry(user_id: str, slug: str, name: str) -> dict[str, Any]:
    for entry in (_read_meta(user_id, slug).get("repos") or []):
        if entry.get("name") == name:
            return entry
    raise RepoError(f"{name!r} is not attached to {slug}")


# ── The write toggle ─────────────────────────────────────────────────────────

def set_write(user_id: str, slug: str, name: str, enabled: bool) -> dict[str, Any]:
    """Turn pushing on or off for one repository.

    Deliberately per-repo: granting the agent write everywhere because it needed
    it in one place is how a small permission becomes a large one.
    """
    meta = _read_meta(user_id, slug)
    for entry in (meta.get("repos") or []):
        if entry.get("name") == name:
            entry["write"] = bool(enabled)
            _write_meta(user_id, slug, meta)
            logger.info("space %s repo %s: write=%s", slug, name, bool(enabled))
            return entry
    raise RepoError(f"{name!r} is not attached to {slug}")


def _require_write(user_id: str, slug: str, name: str) -> None:
    if not _entry(user_id, slug, name).get("write"):
        raise RepoError(
            f"{name!r} is attached read-only. Turn on write for this repository "
            "if you intend to change it.")


# ── Reading ──────────────────────────────────────────────────────────────────

def status(user_id: str, slug: str, name: str) -> dict[str, Any]:
    """Branch, dirty files and divergence — what shape is this checkout in."""
    entry = _entry(user_id, slug, name)          # raises if not attached
    path = _resolved_repo_path(user_id, slug, name)
    if not path.exists():
        raise RepoError(f"{name!r} is attached but not cloned")
    dirty = [ln for ln in _run_git(["status", "--porcelain"], cwd=path).splitlines() if ln]
    try:
        ahead_behind = _run_git(
            ["rev-list", "--left-right", "--count", "HEAD...@{upstream}"], cwd=path)
        ahead, behind = (int(x) for x in ahead_behind.split())
    except (RepoError, ValueError):
        ahead = behind = 0
    return {
        "name": name,
        "branch": _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=path),
        "dirty_files": len(dirty),
        "changes": dirty[:50],
        "ahead": ahead,
        "behind": behind,
        "write": bool(entry.get("write")),
    }


def log(user_id: str, slug: str, name: str, limit: int = 10) -> list[dict[str, str]]:
    """Recent commits, newest first."""
    _entry(user_id, slug, name)                  # raises if not attached
    path = _resolved_repo_path(user_id, slug, name)
    if not path.exists():
        raise RepoError(f"{name!r} is attached but not cloned")
    raw = _run_git(
        ["log", f"-{max(1, min(limit, 100))}", "--pretty=format:%h\x1f%an\x1f%ar\x1f%s"],
        cwd=path)
    out = []
    for line in raw.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 4:
            out.append(dict(zip(("sha", "author", "when", "subject"), parts, strict=True)))
    return out


def pull(user_id: str, slug: str, name: str) -> str:
    """Fetch and fast-forward. Reading fresher code needs no write permission."""
    _entry(user_id, slug, name)                  # raises if not attached
    path = _resolved_repo_path(user_id, slug, name)
    if not path.exists():
        raise RepoError(f"{name!r} is attached but not cloned")
    return _run_git(["pull", "--ff-only"], cwd=path, env=_git_env(user_id, slug, name))


# ── Writing (gated) ──────────────────────────────────────────────────────────

def commit(user_id: str, slug: str, name: str, message: str,
           *, paths: list[str] | None = None) -> str:
    """Commit staged/specified changes — refused unless write is on.

    "Nothing to commit" is a normal outcome, not a failure: an agent that just
    made no net change should get told so, not have to catch an exception to
    discover it.
    """
    _require_write(user_id, slug, name)
    path = _resolved_repo_path(user_id, slug, name)
    _run_git(["add", *(paths or ["-A"])], cwd=path)
    if not _run_git(["status", "--porcelain"], cwd=path):
        return "nothing to commit — the working tree is clean"
    return _run_git(["commit", "-m", message], cwd=path)


def push(user_id: str, slug: str, name: str, *, branch: str | None = None) -> str:
    """Push — refused unless write is on for this specific repository."""
    _require_write(user_id, slug, name)
    path = _resolved_repo_path(user_id, slug, name)
    target = branch or _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=path)
    return _run_git(["push", "origin", target], cwd=path,
                    env=_git_env(user_id, slug, name))
