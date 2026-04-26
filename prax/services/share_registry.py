"""Persistent registry of files, courses, and notes that the user has
explicitly opted to expose via the public ngrok URL.

This is the single source of truth for "what is publicly reachable":

- Per-file shares (workspace_share_file) — randomized token + filename,
  served at /shared/<token>/<filename>.
- Course publishes (course_publish with public=True) — Hugo-rendered
  course site, served at /courses/<course_id>/...
- Note publishes (note_publish with public=True) — Hugo-rendered note,
  served at /notes/<slug>/...

Without an entry in this registry, the matching Flask route returns 404
even when ngrok is up.  Local/tailscale/SSH access goes through TeamWork
and is never gated by this file (different port, different threat model).

Storage is a JSON file at ``{workspace}/.shares.json`` — atomic writes,
per-user lock.  The file is intentionally inside the user's workspace so
backups / git ignore rules apply uniformly.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

_REGISTRY_FILENAME = ".shares.json"
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()

ENTRY_KIND_FILE = "file"
ENTRY_KIND_COURSE = "course"
ENTRY_KIND_NOTE = "note"


def _lock_for(user_id: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(user_id)
        if lock is None:
            lock = threading.Lock()
            _locks[user_id] = lock
        return lock


def _registry_path(user_id: str) -> str:
    from prax.services.workspace_service import workspace_root
    return os.path.join(workspace_root(user_id), _REGISTRY_FILENAME)


def _load(user_id: str) -> dict[str, dict[str, Any]]:
    path = _registry_path(user_id)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("share registry corrupt at %s — starting fresh: %s", path, exc)
    return {}


def _save(user_id: str, entries: dict[str, dict[str, Any]]) -> None:
    path = _registry_path(user_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def _public_url(token: str, *, kind: str, public_name: str | None = None,
                slug: str | None = None) -> str | None:
    """Build the ngrok-fronted public URL for a registry entry.

    Returns None when ngrok isn't configured — callers should fall back
    to the local TeamWork URL in that case.
    """
    from prax.utils.ngrok import get_ngrok_url
    base = get_ngrok_url()
    if not base:
        return None
    base = base.rstrip("/")
    if kind == ENTRY_KIND_FILE:
        return f"{base}/shared/{token}/{public_name}"
    if kind == ENTRY_KIND_COURSE:
        return f"{base}/courses/{slug}/"
    if kind == ENTRY_KIND_NOTE:
        return f"{base}/notes/{slug}/"
    return None


def register_file(user_id: str, abs_path: str, *,
                  channel: str | None = None) -> dict[str, Any]:
    """Register a workspace file for public sharing.

    Generates a random token + a randomized public filename (extension
    preserved) so the URL leaks nothing about the original file.
    """
    ext = os.path.splitext(abs_path)[1]
    token = uuid.uuid4().hex
    public_name = f"{uuid.uuid4().hex}{ext}"
    entry = {
        "kind": ENTRY_KIND_FILE,
        "token": token,
        "abs_path": abs_path,
        "public_name": public_name,
        "created_at": datetime.now(UTC).isoformat(),
        "created_via": channel or "unknown",
    }
    with _lock_for(user_id):
        entries = _load(user_id)
        entries[token] = entry
        _save(user_id, entries)
    return entry


def register_course(user_id: str, course_id: str, *,
                    channel: str | None = None) -> dict[str, Any]:
    """Register a course for public access at /courses/<course_id>/.

    Course slug doubles as the public path segment, so it isn't randomized
    (Hugo's internal links assume the original slug).  Idempotent — calling
    twice with the same course_id returns the existing entry.
    """
    with _lock_for(user_id):
        entries = _load(user_id)
        for existing in entries.values():
            if (existing.get("kind") == ENTRY_KIND_COURSE
                    and existing.get("slug") == course_id):
                return existing
        token = uuid.uuid4().hex
        entry = {
            "kind": ENTRY_KIND_COURSE,
            "token": token,
            "slug": course_id,
            "created_at": datetime.now(UTC).isoformat(),
            "created_via": channel or "unknown",
        }
        entries[token] = entry
        _save(user_id, entries)
    return entry


def register_note(user_id: str, note_slug: str, *,
                  channel: str | None = None) -> dict[str, Any]:
    """Register a note for public access at /notes/<slug>/.

    Same idempotent semantics as register_course().
    """
    with _lock_for(user_id):
        entries = _load(user_id)
        for existing in entries.values():
            if (existing.get("kind") == ENTRY_KIND_NOTE
                    and existing.get("slug") == note_slug):
                return existing
        token = uuid.uuid4().hex
        entry = {
            "kind": ENTRY_KIND_NOTE,
            "token": token,
            "slug": note_slug,
            "created_at": datetime.now(UTC).isoformat(),
            "created_via": channel or "unknown",
        }
        entries[token] = entry
        _save(user_id, entries)
    return entry


def revoke(user_id: str, token: str) -> bool:
    with _lock_for(user_id):
        entries = _load(user_id)
        if token in entries:
            del entries[token]
            _save(user_id, entries)
            return True
    return False


def revoke_by_slug(user_id: str, kind: str, slug: str) -> bool:
    """Remove a course/note entry by its slug rather than token."""
    with _lock_for(user_id):
        entries = _load(user_id)
        for token, entry in list(entries.items()):
            if entry.get("kind") == kind and entry.get("slug") == slug:
                del entries[token]
                _save(user_id, entries)
                return True
    return False


def lookup_by_token(user_id: str, token: str) -> dict[str, Any] | None:
    with _lock_for(user_id):
        return _load(user_id).get(token)


def is_course_public(user_id: str, course_id: str) -> bool:
    with _lock_for(user_id):
        for entry in _load(user_id).values():
            if (entry.get("kind") == ENTRY_KIND_COURSE
                    and entry.get("slug") == course_id):
                return True
    return False


def is_note_public(user_id: str, note_slug: str) -> bool:
    with _lock_for(user_id):
        for entry in _load(user_id).values():
            if (entry.get("kind") == ENTRY_KIND_NOTE
                    and entry.get("slug") == note_slug):
                return True
    return False


def _iter_user_dirs() -> list[str]:
    from prax.settings import settings
    base = settings.workspace_dir
    if not os.path.isdir(base):
        return []
    return [os.path.join(base, name) for name in os.listdir(base)
            if os.path.isdir(os.path.join(base, name))]


def find_file_share_globally(token: str,
                             public_name: str | None = None) -> dict[str, Any] | None:
    """Scan every workspace's .shares.json for a matching file token.

    Used by the unauthenticated /shared/<token>/<filename> route, which
    has no user context.  Tokens are 32-char random hex so collisions
    are vanishingly rare; we still verify the public_name to defend
    against the (theoretical) collision case.
    """
    for user_dir in _iter_user_dirs():
        path = os.path.join(user_dir, _REGISTRY_FILENAME)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                entries = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(entries, dict):
            continue
        entry = entries.get(token)
        if entry is None or entry.get("kind") != ENTRY_KIND_FILE:
            continue
        if public_name is not None and entry.get("public_name") != public_name:
            continue
        return entry
    return None


def is_course_public_globally(course_id: str) -> bool:
    """Check whether *course_id* is registered as public in any workspace."""
    return _slug_registered_globally(ENTRY_KIND_COURSE, course_id)


def is_note_public_globally(note_slug: str) -> bool:
    return _slug_registered_globally(ENTRY_KIND_NOTE, note_slug)


def _slug_registered_globally(kind: str, slug: str) -> bool:
    for user_dir in _iter_user_dirs():
        path = os.path.join(user_dir, _REGISTRY_FILENAME)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                entries = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(entries, dict):
            continue
        for entry in entries.values():
            if entry.get("kind") == kind and entry.get("slug") == slug:
                return True
    return False


def list_all(user_id: str) -> list[dict[str, Any]]:
    """Return every registered share, with the resolved public URL when ngrok is up."""
    with _lock_for(user_id):
        entries = list(_load(user_id).values())
    for entry in entries:
        entry["url"] = _public_url(
            entry["token"],
            kind=entry["kind"],
            public_name=entry.get("public_name"),
            slug=entry.get("slug"),
        )
    return entries


def public_url_for(entry: dict[str, Any]) -> str | None:
    """Helper for callers that already hold an entry dict."""
    return _public_url(
        entry["token"],
        kind=entry["kind"],
        public_name=entry.get("public_name"),
        slug=entry.get("slug"),
    )
