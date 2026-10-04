"""History and trash for the Library.

- **Every write is a commit.** After a library write succeeds, what changed
  under ``library/`` (and only that) is committed in the workspace's git repo,
  with who did it and what. TeamWork's MCP docs promised "git-backed" writes
  and they were not: library files only got swept into whatever unrelated
  commit ran next, and two edits in a row could leave no record of the first.
- **Deletes go to the trash.** A deleted note, notebook, space or space file
  moves to ``library/.trash/<id>/`` with a manifest saying where it came from,
  and can be restored from there. Old trash is purged after
  ``LIBRARY_TRASH_DAYS``.
- **History per note.** The commits that touched a note, its text at any of
  them, a diff against today, and restoring an old version as a new edit.

Git is the history, not the write: a failed commit is logged and never fails
the write. Commits are serialised per process.
"""
from __future__ import annotations

import contextlib
import difflib
import json
import logging
import re
import secrets
import shutil
import subprocess
import threading
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

LIBRARY_DIR = "library"
TRASH_DIR = ".trash"

# Who is writing, when the write function has no editor/author argument:
# TeamWork's library routes set "human" for the request; everything else
# (agent tools, the task runner) is Prax.
library_actor: ContextVar[str] = ContextVar("library_actor", default="prax")

_git_lock = threading.Lock()
_COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")
_TRASH_ID_RE = re.compile(r"^\d{8}T\d{6}-[0-9a-f]{6}$")
_AUTHORS = {
    "prax": ("Prax", "prax@local"),
    "human": ("You (TeamWork)", "human@teamwork.local"),
}


def _library(root: Path) -> Path:
    return Path(root) / LIBRARY_DIR


def _git(root: Path, *args: str, timeout: int = 20) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=timeout)


@contextlib.contextmanager
def acting_as(actor: str):
    token = library_actor.set(actor)
    try:
        yield
    finally:
        library_actor.reset(token)


# ── A commit per write ──────────────────────────────────────────────────────

def commit(root: Path, message: str, *, author: str | None = None) -> str | None:
    """Commit the library's changes in the workspace at *root* as one commit.
    Returns the short hash, or ``None`` when there was nothing to commit or
    the workspace is not a git repo."""
    root = Path(root)
    if not (root / ".git").exists():
        return None
    who = author or library_actor.get()
    name, email = _AUTHORS.get(who, (who, "person@teamwork.local"))
    with _git_lock:
        try:
            _git(root, "add", "-A", "--", LIBRARY_DIR)
            r = _git(root, "-c", f"user.name={name}", "-c", f"user.email={email}",
                     "commit", "-q", "--no-verify", "-m", message, "--", LIBRARY_DIR)
            if r.returncode != 0:
                if "nothing" not in (r.stdout + r.stderr).lower():
                    logger.warning("library commit failed: %s", (r.stderr or r.stdout)[:300])
                return None
            return _git(root, "rev-parse", "--short", "HEAD").stdout.strip() or None
        except Exception as exc:
            logger.warning("library commit raised: %s", exc)
            return None


# ── Trash ───────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(UTC)


def to_trash(root: Path, path: Path, *, kind: str, label: str) -> dict:
    """Move *path* (a file or folder under the library) into the trash."""
    lib = _library(root).resolve()
    path = path.resolve()
    rel = path.relative_to(lib)
    tid = f"{_now():%Y%m%dT%H%M%S}-{secrets.token_hex(3)}"
    dest = lib / TRASH_DIR / tid
    dest.mkdir(parents=True)
    shutil.move(str(path), str(dest / path.name))
    manifest = {
        "id": tid, "kind": kind, "label": label, "original": str(rel), "name": path.name,
        "deleted_at": _now().isoformat(timespec="seconds"), "deleted_by": library_actor.get(),
    }
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def _trash_entry(root: Path, tid: str) -> tuple[Path, dict]:
    if not _TRASH_ID_RE.match(tid or ""):
        raise KeyError(f"no trash item {tid!r}")
    folder = _library(root) / TRASH_DIR / tid
    manifest_path = folder / "manifest.json"
    if not manifest_path.exists():
        raise KeyError(f"no trash item {tid!r}")
    return folder, json.loads(manifest_path.read_text(encoding="utf-8"))


def list_trash(root: Path) -> list[dict]:
    purge_old(root)
    base = _library(root) / TRASH_DIR
    if not base.exists():
        return []
    items = []
    for folder in base.iterdir():
        try:
            items.append(json.loads((folder / "manifest.json").read_text(encoding="utf-8")))
        except Exception:
            continue
    return sorted(items, key=lambda m: m.get("deleted_at", ""), reverse=True)


def restore(root: Path, tid: str) -> dict:
    """Put a trashed item back where it was. Refuses when something now
    occupies that place, or when its parent (notebook, space) is gone."""
    folder, manifest = _trash_entry(root, tid)
    lib = _library(root)
    target = lib / manifest["original"]
    if target.exists():
        return {"error": f"Something is already at {manifest['original']}: rename or delete it first."}
    if not target.parent.exists():
        return {"error": f"Its {('notebook' if manifest['kind'] == 'note' else 'space')} is gone "
                         f"({target.parent.relative_to(lib)}). Restore that from the trash first."}
    shutil.move(str(folder / manifest["name"]), str(target))
    shutil.rmtree(folder, ignore_errors=True)
    return {"status": "restored", "item": manifest}


def purge(root: Path, tid: str) -> dict:
    folder, manifest = _trash_entry(root, tid)
    shutil.rmtree(folder)
    return {"status": "purged", "item": manifest}


def purge_old(root: Path, days: int | None = None) -> int:
    if days is None:
        from prax.settings import settings
        days = settings.library_trash_days
    if days <= 0:
        return 0
    base = _library(root) / TRASH_DIR
    if not base.exists():
        return 0
    cutoff = _now() - timedelta(days=days)
    purged = 0
    for folder in list(base.iterdir()):
        try:
            when = datetime.fromisoformat(
                json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["deleted_at"])
        except Exception:
            continue
        if when < cutoff:
            shutil.rmtree(folder, ignore_errors=True)
            purged += 1
    return purged


# ── History of one note ─────────────────────────────────────────────────────

def history(root: Path, rel_path: str, limit: int = 50) -> list[dict]:
    """Commits that touched ``library/<rel_path>``, newest first, following
    renames. Each: commit, date, author, message, and the path at that time."""
    root = Path(root)
    if not (root / ".git").exists():
        return []
    r = _git(root, "log", "--follow", f"-n{int(limit)}", "--name-only",
             "--format=%x1e%H%x1f%aI%x1f%an%x1f%s", "--", f"{LIBRARY_DIR}/{rel_path}")
    out = []
    for block in r.stdout.split("\x1e"):
        lines = [ln for ln in block.strip().splitlines() if ln.strip()]
        if not lines:
            continue
        sha, date, author, subject = (lines[0].split("\x1f") + ["", "", "", ""])[:4]
        path = lines[1] if len(lines) > 1 else f"{LIBRARY_DIR}/{rel_path}"
        out.append({"commit": sha[:12], "date": date, "author": author,
                    "message": subject, "path": path})
    return out


def version(root: Path, rel_path: str, commit_id: str) -> str | None:
    """The note's full text at *commit_id*, or ``None``."""
    if not _COMMIT_RE.match(commit_id or ""):
        return None
    entry = next((h for h in history(root, rel_path, limit=500)
                  if h["commit"].startswith(commit_id) or commit_id.startswith(h["commit"])), None)
    if entry is None:
        return None
    r = _git(Path(root), "show", f"{commit_id}:{entry['path']}")
    return r.stdout if r.returncode == 0 else None


def diff(old: str, new: str, *, old_label: str = "then", new_label: str = "now") -> str:
    return "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=old_label, tofile=new_label, n=3,
    ))
