"""Research project service — group notes, links, files, and sources.

Stores projects under ``workspace_root/projects/<slug>/project.yaml``.
"""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

import yaml

from prax.services.workspace_service import (
    ensure_workspace,
    get_lock,
    git_commit,
    safe_join,
)
from prax.utils.text import slugify

logger = logging.getLogger(__name__)

_PROJECT_FILE = "project.yaml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    return slugify(text, fallback="project")


def _projects_dir(root: str) -> str:
    d = os.path.join(root, "projects")
    os.makedirs(d, exist_ok=True)
    return d


def _project_dir(root: str, project_id: str) -> str:
    return safe_join(_projects_dir(root), project_id)


def _read_project(project_path: str) -> dict:
    yaml_path = os.path.join(project_path, _PROJECT_FILE)
    if not os.path.isfile(yaml_path):
        raise FileNotFoundError(f"Project not found: {project_path}")
    with open(yaml_path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _write_project(project_path: str, data: dict) -> None:
    data["updated_at"] = datetime.now(UTC).isoformat()
    yaml_path = os.path.join(project_path, _PROJECT_FILE)
    with open(yaml_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


def _deep_merge(base: dict, updates: dict) -> None:
    """Recursively merge *updates* into *base* in-place."""
    for key, value in updates.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_project(user_id: str, title: str, description: str = "") -> dict:
    """Create a new research project and return its initial metadata."""
    with get_lock(user_id):
        root = ensure_workspace(user_id)
        project_id = _slugify(title)

        # Deduplicate if slug already exists.
        base_id = project_id
        counter = 2
        while os.path.isdir(os.path.join(_projects_dir(root), project_id)):
            project_id = f"{base_id}-{counter}"
            counter += 1

        project_path = _project_dir(root, project_id)
        os.makedirs(project_path, exist_ok=True)

        now = datetime.now(UTC).isoformat()
        data: dict = {
            "id": project_id,
            "title": title,
            "description": description,
            "status": "active",
            "notes": [],
            "links": [],
            "sources": [],
            "created_at": now,
            "updated_at": now,
        }
        _write_project(project_path, data)
        git_commit(root, f"Create project: {title}")
        return data


def get_project(user_id: str, project_id: str) -> dict:
    """Read a project's full metadata."""
    with get_lock(user_id):
        root = ensure_workspace(user_id)
        data = _read_project(_project_dir(root, project_id))
    try:
        from prax.services import access_log
        access_log.touch(user_id, "project", project_id)
    except Exception:
        pass
    return data


def list_projects(user_id: str) -> list[dict]:
    """List all projects with summary info.

    Sorted by most-recently-accessed first, then created_at desc.
    """
    from prax.services import access_log

    with get_lock(user_id):
        root = ensure_workspace(user_id)
        projects_root = _projects_dir(root)
        results = []
        if not os.path.isdir(projects_root):
            return results
        access_map = access_log.get_all(user_id, "project")
        for entry in os.listdir(projects_root):
            project_path = os.path.join(projects_root, entry)
            if not os.path.isdir(project_path):
                continue
            try:
                data = _read_project(project_path)
                results.append({
                    "id": data["id"],
                    "title": data["title"],
                    "description": data.get("description", ""),
                    "status": data["status"],
                    "notes_count": len(data.get("notes", [])),
                    "links_count": len(data.get("links", [])),
                    "sources_count": len(data.get("sources", [])),
                    "created_at": data.get("created_at", ""),
                    "accessed_at": access_map.get(data["id"], ""),
                })
            except Exception:
                continue
        results.sort(
            key=lambda p: (p["accessed_at"], p.get("created_at", "")),
            reverse=True,
        )
        return results


def update_project(user_id: str, project_id: str, updates: dict) -> dict:
    """Merge *updates* into project.yaml and return the full updated data."""
    with get_lock(user_id):
        root = ensure_workspace(user_id)
        project_path = _project_dir(root, project_id)
        data = _read_project(project_path)
        _deep_merge(data, updates)
        _write_project(project_path, data)
        git_commit(root, f"Update project: {data['title']}")
        return data


def add_note_to_project(user_id: str, project_id: str, note_slug: str) -> dict:
    """Add a note slug to the project's notes list (deduplicated)."""
    with get_lock(user_id):
        root = ensure_workspace(user_id)
        project_path = _project_dir(root, project_id)
        data = _read_project(project_path)
        notes = data.setdefault("notes", [])
        if note_slug not in notes:
            notes.append(note_slug)
        _write_project(project_path, data)
        git_commit(root, f"Add note '{note_slug}' to project: {data['title']}")
        return data


def add_link_to_project(
    user_id: str, project_id: str, url: str, title: str = "",
) -> dict:
    """Add a reference link to the project."""
    with get_lock(user_id):
        root = ensure_workspace(user_id)
        project_path = _project_dir(root, project_id)
        data = _read_project(project_path)
        links = data.setdefault("links", [])
        links.append({
            "url": url,
            "title": title,
            "added_at": datetime.now(UTC).isoformat(),
        })
        _write_project(project_path, data)
        git_commit(root, f"Add link to project: {data['title']}")
        return data


def add_source_to_project(
    user_id: str, project_id: str, filename: str, content: str,
) -> dict:
    """Save a source file in the project directory and record it."""
    with get_lock(user_id):
        root = ensure_workspace(user_id)
        project_path = _project_dir(root, project_id)
        data = _read_project(project_path)

        filepath = safe_join(project_path, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        sources = data.setdefault("sources", [])
        if filename not in sources:
            sources.append(filename)
        _write_project(project_path, data)
        git_commit(root, f"Add source '{filename}' to project: {data['title']}")
        return data


def generate_project_brief(user_id: str, project_id: str) -> str:
    """Read all linked notes and sources, return a combined markdown brief."""
    with get_lock(user_id):
        root = ensure_workspace(user_id)
        project_path = _project_dir(root, project_id)
        data = _read_project(project_path)

    # Import note_service here to avoid circular imports.
    from prax.services import note_service

    sections: list[str] = []
    sections.append(f"# {data['title']}")
    if data.get("description"):
        sections.append(f"\n{data['description']}\n")

    # Notes section.
    note_slugs = data.get("notes", [])
    if note_slugs:
        sections.append("\n## Notes\n")
        for slug in note_slugs:
            try:
                note = note_service.get_note(user_id, slug)
                sections.append(f"### {note['title']}\n")
                sections.append(note.get("content", "") + "\n")
            except FileNotFoundError:
                sections.append(f"### {slug}\n\n*(Note not found)*\n")

    # Links section.
    links = data.get("links", [])
    if links:
        sections.append("\n## Links\n")
        for link in links:
            link_title = link.get("title") or link["url"]
            sections.append(f"- [{link_title}]({link['url']})")

    # Sources section.
    sources = data.get("sources", [])
    if sources:
        sections.append("\n\n## Sources\n")
        for filename in sources:
            filepath = os.path.join(project_path, filename)
            if os.path.isfile(filepath):
                with open(filepath, encoding="utf-8") as f:
                    content = f.read()
                sections.append(f"### {filename}\n")
                sections.append(content + "\n")
            else:
                sections.append(f"### {filename}\n\n*(File not found)*\n")

    return "\n".join(sections)
