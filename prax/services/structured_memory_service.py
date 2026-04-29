"""Durable structured memory records stored in the user workspace.

This service complements the existing STM/LTM memory backends with a small,
inspectable ledger for facts that need explicit type, scope, and lifecycle
metadata.  It intentionally uses workspace JSON so it works even when vector or
graph memory infrastructure is unavailable.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from prax.services import workspace_service

BUCKETS = {
    "preference",
    "project_fact",
    "session_scratchpad",
    "decision",
    "tool_note",
}
SCOPES = {"user", "project", "session"}
STATUSES = {"active", "archived", "superseded"}

_FILENAME = "structured_memory.json"


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clamp(value: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))


def _clean_token(value: str, allowed: set[str], default: str) -> str:
    token = (value or "").strip().lower()
    return token if token in allowed else default


def _normalise_key(value: str) -> str:
    key = re.sub(r"\s+", " ", (value or "").strip())
    if not key:
        key = "untitled"
    return key[:160]


def _path(user_id: str) -> str:
    root = workspace_service.ensure_workspace(user_id)
    return workspace_service.safe_join(root, _FILENAME)


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
    records = data.get("records")
    if not isinstance(records, list):
        data["records"] = []
    data.setdefault("version", 1)
    return data


def _save(user_id: str, data: dict[str, Any], message: str) -> None:
    path = _path(user_id)
    root = workspace_service.workspace_root(user_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
    workspace_service.git_commit(root, message)


def _find_active(records: list[dict[str, Any]], *, bucket: str, scope: str, key: str) -> dict[str, Any] | None:
    key_lower = key.lower()
    for record in records:
        if (
            record.get("bucket") == bucket
            and record.get("scope") == scope
            and record.get("status") == "active"
            and str(record.get("key", "")).lower() == key_lower
        ):
            return record
    return None


def record_memory(
    user_id: str,
    *,
    bucket: str,
    key: str,
    content: str,
    scope: str = "user",
    source: str = "",
    confidence: float = 0.7,
    importance: float = 0.5,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    ttl_days: int | None = None,
    supersedes: str = "",
) -> dict[str, Any]:
    """Create or update a structured memory record."""
    bucket = _clean_token(bucket, BUCKETS, "session_scratchpad")
    scope = _clean_token(scope, SCOPES, "user")
    key = _normalise_key(key)
    content = re.sub(r"\s+", " ", (content or "").strip())
    if not content:
        raise ValueError("content is required")

    now = _now()
    tag_list = sorted({t.strip() for t in (tags or []) if t and t.strip()})
    data = _load(user_id)
    records: list[dict[str, Any]] = data["records"]

    if supersedes:
        for record in records:
            if record.get("id") == supersedes and record.get("status") == "active":
                record["status"] = "superseded"
                record["updated_at"] = now

    existing = _find_active(records, bucket=bucket, scope=scope, key=key)
    if existing:
        existing.update({
            "content": content,
            "source": source.strip() or existing.get("source", ""),
            "confidence": _clamp(confidence, 0.7),
            "importance": _clamp(importance, 0.5),
            "tags": tag_list,
            "metadata": metadata or {},
            "ttl_days": ttl_days,
            "updated_at": now,
        })
        record = existing
        action = "Update"
    else:
        record = {
            "id": uuid.uuid4().hex[:12],
            "bucket": bucket,
            "key": key,
            "content": content,
            "scope": scope,
            "source": source.strip(),
            "confidence": _clamp(confidence, 0.7),
            "importance": _clamp(importance, 0.5),
            "status": "active",
            "tags": tag_list,
            "metadata": metadata or {},
            "ttl_days": ttl_days,
            "created_at": now,
            "updated_at": now,
        }
        if supersedes:
            record["supersedes"] = supersedes
        records.append(record)
        action = "Add"

    _save(user_id, data, f"{action} structured memory: {bucket}/{key[:50]}")
    return record


def list_memories(
    user_id: str,
    *,
    query: str = "",
    bucket: str = "",
    scope: str = "",
    status: str = "active",
    limit: int = 20,
) -> list[dict[str, Any]]:
    """List structured memory records with simple filtering."""
    data = _load(user_id)
    records = list(data["records"])
    bucket = (bucket or "").strip().lower()
    scope = (scope or "").strip().lower()
    status = (status or "").strip().lower()
    query_lower = (query or "").strip().lower()

    filtered: list[dict[str, Any]] = []
    for record in records:
        if bucket and record.get("bucket") != bucket:
            continue
        if scope and record.get("scope") != scope:
            continue
        if status and record.get("status") != status:
            continue
        if query_lower:
            haystack = " ".join([
                str(record.get("key", "")),
                str(record.get("content", "")),
                " ".join(record.get("tags", [])),
                json.dumps(record.get("metadata", {}), sort_keys=True),
            ]).lower()
            if query_lower not in haystack:
                continue
        filtered.append(record)

    filtered.sort(
        key=lambda item: (
            float(item.get("importance", 0.0)),
            str(item.get("updated_at", "")),
        ),
        reverse=True,
    )
    return filtered[: max(1, min(limit, 100))]


def archive_memory(user_id: str, memory_id: str, reason: str = "") -> dict[str, Any] | None:
    """Archive a structured memory by id."""
    data = _load(user_id)
    now = _now()
    for record in data["records"]:
        if record.get("id") == memory_id:
            record["status"] = "archived"
            record["updated_at"] = now
            if reason:
                record.setdefault("metadata", {})["archive_reason"] = reason
            _save(user_id, data, f"Archive structured memory: {memory_id}")
            return record
    return None
