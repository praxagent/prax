"""Registry for tools Prax authors for itself.

The plugin system owns executable code.  This registry owns the durable,
inspectable metadata around why a self-authored tool exists, which plugin it
belongs to, what state it is in, and how it has performed over time.
"""
from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from typing import Any

from prax.services import workspace_service

STATUSES = {"draft", "tested", "active", "deprecated", "failed"}
RISK_LEVELS = {"low", "medium", "high"}

_REGISTRY_PATH = ("self_tools", "registry.json")


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", (value or "").strip().lower()).strip("_")
    if not slug:
        raise ValueError("tool name is required")
    return slug[:80]


def _string_list(values: list[str] | None) -> list[str]:
    return sorted({v.strip() for v in (values or []) if v and v.strip()})


def _path(user_id: str) -> str:
    root = workspace_service.ensure_workspace(user_id)
    path = workspace_service.safe_join(root, *_REGISTRY_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def _load(user_id: str) -> dict[str, Any]:
    path = _path(user_id)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {}
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    tools = data.get("tools")
    if not isinstance(tools, dict):
        data["tools"] = {}
    data.setdefault("version", 1)
    return data


def _save(user_id: str, data: dict[str, Any], message: str) -> None:
    path = _path(user_id)
    root = workspace_service.workspace_root(user_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
    workspace_service.git_commit(root, message)


def register_tool(
    user_id: str,
    *,
    name: str,
    description: str,
    capabilities: list[str] | None = None,
    plugin_name: str = "",
    tool_names: list[str] | None = None,
    tags: list[str] | None = None,
    risk_level: str = "medium",
    examples: list[str] | None = None,
    provenance_trace_id: str = "",
) -> dict[str, Any]:
    """Create or update a self-authored tool registry entry."""
    tool_id = _slug(name)
    description = re.sub(r"\s+", " ", (description or "").strip())
    if not description:
        raise ValueError("description is required")

    now = _now()
    data = _load(user_id)
    tools: dict[str, Any] = data["tools"]
    existing = tools.get(tool_id, {})
    record = {
        "id": tool_id,
        "name": name.strip(),
        "description": description,
        "capabilities": _string_list(capabilities),
        "plugin_name": plugin_name.strip(),
        "tool_names": _string_list(tool_names),
        "tags": _string_list(tags),
        "risk_level": risk_level.strip().lower() if risk_level.strip().lower() in RISK_LEVELS else "medium",
        "status": existing.get("status", "draft"),
        "version": existing.get("version", 1),
        "created_at": existing.get("created_at", now),
        "updated_at": now,
        "provenance_trace_id": provenance_trace_id.strip() or existing.get("provenance_trace_id", ""),
        "examples": _string_list(examples),
        "history": existing.get("history", []),
    }
    if existing:
        record["version"] = int(existing.get("version", 1)) + 1
        record["history"].append({
            "at": now,
            "event": "metadata_updated",
            "summary": "Registry metadata updated.",
        })
        action = "Update"
    else:
        record["history"].append({
            "at": now,
            "event": "registered",
            "summary": description,
        })
        action = "Register"
    tools[tool_id] = record
    _save(user_id, data, f"{action} self-authored tool: {tool_id}")
    return record


def list_tools(
    user_id: str,
    *,
    status: str = "",
    query: str = "",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List self-authored tool registry entries."""
    data = _load(user_id)
    records = list(data["tools"].values())
    status = (status or "").strip().lower()
    query_lower = (query or "").strip().lower()

    filtered: list[dict[str, Any]] = []
    for record in records:
        if status and record.get("status") != status:
            continue
        if query_lower:
            haystack = " ".join([
                str(record.get("id", "")),
                str(record.get("name", "")),
                str(record.get("description", "")),
                " ".join(record.get("capabilities", [])),
                " ".join(record.get("tool_names", [])),
                " ".join(record.get("tags", [])),
            ]).lower()
            if query_lower not in haystack:
                continue
        filtered.append(record)

    filtered.sort(key=lambda item: str(item.get("updated_at", "")), reverse=True)
    return filtered[: max(1, min(limit, 100))]


def get_tool(user_id: str, name: str) -> dict[str, Any] | None:
    """Return one registry entry by name/id."""
    data = _load(user_id)
    return data["tools"].get(_slug(name))


def update_status(
    user_id: str,
    *,
    name: str,
    status: str,
    summary: str = "",
    trace_id: str = "",
    error: str = "",
) -> dict[str, Any] | None:
    """Update lifecycle status and append a history event."""
    status = (status or "").strip().lower()
    if status not in STATUSES:
        raise ValueError(f"status must be one of: {', '.join(sorted(STATUSES))}")

    data = _load(user_id)
    tool_id = _slug(name)
    record = data["tools"].get(tool_id)
    if not record:
        return None

    now = _now()
    record["status"] = status
    record["updated_at"] = now
    event: dict[str, Any] = {
        "at": now,
        "event": f"status_{status}",
        "summary": summary.strip(),
    }
    if trace_id:
        event["trace_id"] = trace_id.strip()
    if error:
        event["error"] = error.strip()
    record.setdefault("history", []).append(event)
    _save(user_id, data, f"Update self-authored tool status: {tool_id} -> {status}")
    return record
