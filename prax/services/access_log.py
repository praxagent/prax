"""Lightweight access log for tracking recency of reads across resources.

Used to sort notes, courses, projects, and briefings by "most recently
accessed" without polluting the files themselves or creating git churn.

Storage: ``{workspace}/.access_log.json`` — a flat dict keyed by
``{kind}:{id}`` mapping to an ISO8601 timestamp.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

_LOG_FILENAME = ".access_log.json"


def _log_path(user_id: str) -> str:
    from prax.services.workspace_service import ensure_workspace
    root = ensure_workspace(user_id)
    return os.path.join(root, _LOG_FILENAME)


def _load(user_id: str) -> dict[str, str]:
    path = _log_path(user_id)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        logger.debug("Failed to load access log for %s", user_id, exc_info=True)
        return {}


def _save(user_id: str, data: dict[str, str]) -> None:
    path = _log_path(user_id)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"))
    except Exception:
        logger.debug("Failed to save access log for %s", user_id, exc_info=True)


def touch(user_id: str, kind: str, resource_id: str) -> None:
    """Record that a resource was accessed now.

    ``kind`` is one of ``"note"``, ``"course"``, ``"project"``,
    ``"briefing"`` — namespace so the same ID can exist in multiple kinds.
    """
    if not user_id or not resource_id:
        return
    data = _load(user_id)
    data[f"{kind}:{resource_id}"] = datetime.now(UTC).isoformat()
    # Keep the log bounded — drop the oldest entries if it grows too large.
    if len(data) > 5000:
        sorted_items = sorted(data.items(), key=lambda kv: kv[1], reverse=True)
        data = dict(sorted_items[:4000])
    _save(user_id, data)


def get(user_id: str, kind: str, resource_id: str) -> str:
    """Return the last access timestamp, or empty string if never accessed."""
    return _load(user_id).get(f"{kind}:{resource_id}", "")


def get_all(user_id: str, kind: str) -> dict[str, str]:
    """Return all access timestamps for a kind, mapped by resource_id."""
    data = _load(user_id)
    prefix = f"{kind}:"
    return {
        k[len(prefix):]: v
        for k, v in data.items()
        if k.startswith(prefix)
    }


def sort_key(
    access_time: str, fallback_time: str = "",
) -> tuple[str, str]:
    """Build a sort key for ordering by (access desc, fallback desc).

    Usage:
        items.sort(key=lambda x: sort_key(access_map.get(x.id, ""), x.created_at), reverse=True)

    Returns ``(access_time, fallback_time)`` — items sorted in reverse
    will have most-recently-accessed first, then most-recently-created.
    """
    return (access_time or "", fallback_time or "")
