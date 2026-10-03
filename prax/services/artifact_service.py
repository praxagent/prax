"""Artifacts: self-contained HTML pages Prax makes and keeps updating.

A plan, a table, a chart or a small app that should stay useful after the chat
scrolls past it. Each artifact lives in the user's workspace —
``artifacts/<id>/index.html`` plus ``manifest.json`` — and every write is a git
commit, so earlier versions can be recovered. Off unless ``ARTIFACTS_ENABLED``.

Where people see them:

- **TeamWork** (the normal, private path): its artifact viewer fetches the page
  from Prax (``/teamwork/artifacts/<id>``) and renders it in a sandboxed frame —
  scripts run, but with an opaque origin, never on TeamWork's own (it holds the
  session). Updates show up on the next poll.
- **A public link** (only through ``artifact_share_public``, which needs a
  person's decision every time): the share registry serves ``index.html`` at
  an unguessable ``/shared/<token>/<name>`` path on Prax's public app, for a
  limited time, with a sandboxing Content-Security-Policy.

Idea credit: Telepath's Television (television.run) — artifacts pinned for a
person and updated over time; its sandboxing of agent-written pages.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from prax.settings import settings

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_DIR = "artifacts"


class ArtifactError(ValueError):
    """A request Prax should be told about in plain words (bad id, too big…)."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _slug(title: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40].strip("-")
    return f"{base or 'artifact'}-{uuid.uuid4().hex[:6]}"


def _check_id(artifact_id: str) -> str:
    artifact_id = (artifact_id or "").strip().lower()
    if not _ID_RE.match(artifact_id):
        raise ArtifactError(f"not an artifact id: {artifact_id!r}")
    return artifact_id


def _paths(user_id: str, artifact_id: str) -> tuple[str, str, str]:
    from prax.services.workspace_service import ensure_workspace, safe_join

    root = ensure_workspace(user_id)
    folder = safe_join(root, _DIR, artifact_id)
    return root, safe_join(folder, "index.html"), safe_join(folder, "manifest.json")


def _read_manifest(path: str) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def publish(user_id: str, title: str, html: str, *, artifact_id: str = "",
            space: str = "") -> dict[str, Any]:
    """Create an artifact, or replace an existing one's page (a new version).

    Returns its manifest. Raises :class:`ArtifactError` for an unknown id, an
    empty or oversized page, or a page that is not HTML.
    """
    from prax.services.workspace_service import atomic_write, get_lock, git_commit

    title = (title or "").strip()[:200]
    html = html or ""
    if not html.strip():
        raise ArtifactError("the page is empty")
    if "<" not in html:
        raise ArtifactError("the page must be HTML (a complete, self-contained document)")
    limit = int(getattr(settings, "artifact_max_bytes", 2_000_000) or 2_000_000)
    size = len(html.encode("utf-8"))
    if size > limit:
        raise ArtifactError(f"the page is {size} bytes; the limit is {limit} (ARTIFACT_MAX_BYTES)")

    with get_lock(user_id):
        if artifact_id:
            artifact_id = _check_id(artifact_id)
            root, page, manifest_path = _paths(user_id, artifact_id)
            manifest = _read_manifest(manifest_path)
            if manifest is None:
                raise ArtifactError(f"no artifact {artifact_id!r}; omit artifact_id to create one")
            manifest.update(
                version=int(manifest.get("version", 0)) + 1,
                updated_at=_now(),
                size=size,
                **({"title": title} if title else {}),
            )
        else:
            if not title:
                raise ArtifactError("a new artifact needs a title")
            artifact_id = _slug(title)
            root, page, manifest_path = _paths(user_id, artifact_id)
            now = _now()
            manifest = {"id": artifact_id, "title": title, "version": 1,
                        "created_at": now, "updated_at": now, "size": size,
                        "space": (space or "").strip()[:80]}
        import os
        os.makedirs(os.path.dirname(page), exist_ok=True)
        atomic_write(page, html)
        atomic_write(manifest_path, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        git_commit(root, f"Artifact {artifact_id} v{manifest['version']}: {manifest['title'][:60]}")
    return manifest


def get(user_id: str, artifact_id: str) -> dict[str, Any] | None:
    """The manifest plus ``html``, or None when there is no such artifact."""
    try:
        artifact_id = _check_id(artifact_id)
    except ArtifactError:
        return None
    _, page, manifest_path = _paths(user_id, artifact_id)
    manifest = _read_manifest(manifest_path)
    if manifest is None:
        return None
    try:
        with open(page, encoding="utf-8") as f:
            return {**manifest, "html": f.read()}
    except OSError:
        return None


def page_path(user_id: str, artifact_id: str) -> str | None:
    """Absolute path of the artifact's page, or None when it does not exist."""
    item = get(user_id, artifact_id)
    if item is None:
        return None
    return _paths(user_id, item["id"])[1]


def list_all(user_id: str) -> list[dict[str, Any]]:
    """Every artifact's manifest, most recently updated first."""
    import os

    from prax.services.workspace_service import ensure_workspace, safe_join

    folder = safe_join(ensure_workspace(user_id), _DIR)
    if not os.path.isdir(folder):
        return []
    out = []
    for name in os.listdir(folder):
        if _ID_RE.match(name):
            manifest = _read_manifest(os.path.join(folder, name, "manifest.json"))
            if manifest:
                out.append(manifest)
    return sorted(out, key=lambda m: m.get("updated_at", ""), reverse=True)
