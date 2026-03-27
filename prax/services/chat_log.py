"""Per-channel chat log — separate JSONL files for each communication channel.

Each channel (sms, discord, teamwork, browser, terminal) gets its own append-only
JSONL file under ``<workspace>/chats/<channel>.jsonl``.  This keeps conversations
separate in storage and UI while letting Prax search across all of them.

Files are auto-rotated when they exceed ``_MAX_BYTES`` — the current file is
moved to ``<channel>.old.jsonl`` (one generation of history kept for quick access)
and older rotations go to ``archive/chat_logs/``.

Design goals:
- **Fast writes**: append-only, no locking beyond workspace lock
- **Fast reads**: ``recent()`` reads tail of JSONL, no DB query
- **Cross-channel search**: ``search_all()`` greps all channel files
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import UTC, datetime

from prax.services.workspace_service import ensure_workspace, get_lock, workspace_root

logger = logging.getLogger(__name__)

# Channel names (canonical).
CHANNELS = ("sms", "discord", "teamwork", "browser", "terminal")


def _max_bytes() -> int:
    from prax.settings import settings
    return settings.chat_log_max_kb * 1024

def _archive_keep() -> int:
    from prax.settings import settings
    return settings.chat_log_keep_rotated


def _chats_dir(user_id: str) -> str:
    return os.path.join(workspace_root(user_id), "chats")


def _channel_path(user_id: str, channel: str) -> str:
    return os.path.join(_chats_dir(user_id), f"{channel}.jsonl")


def _ensure_chats_dir(user_id: str) -> str:
    d = _chats_dir(user_id)
    os.makedirs(d, exist_ok=True)
    return d


def _rotate_if_needed(path: str, channel: str) -> None:
    """Rotate a channel log file if it exceeds the size limit."""
    try:
        if not os.path.isfile(path) or os.path.getsize(path) < _max_bytes():
            return

        parent = os.path.dirname(path)
        root = os.path.dirname(parent)  # workspace root
        archive_dir = os.path.join(root, "archive", "chat_logs")
        os.makedirs(archive_dir, exist_ok=True)

        # Move current → .old (fast recent-history access).
        old_path = path.replace(".jsonl", ".old.jsonl")
        if os.path.isfile(old_path):
            # Move .old to timestamped archive.
            ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            archived = os.path.join(archive_dir, f"{channel}.{ts}.jsonl")
            shutil.move(old_path, archived)

        shutil.move(path, old_path)

        # Prune old archives.
        prefix = f"{channel}."
        archives = sorted(
            [f for f in os.listdir(archive_dir) if f.startswith(prefix)],
            reverse=True,
        )
        for old in archives[_archive_keep():]:
            os.remove(os.path.join(archive_dir, old))

    except OSError:
        logger.debug("Chat log rotation failed for %s", path, exc_info=True)


def append(user_id: str, channel: str, role: str, content: str) -> None:
    """Append a message to a channel's chat log.

    Args:
        user_id: The user/phone identifier.
        channel: One of ``CHANNELS`` (sms, discord, teamwork, browser, terminal).
        role: ``user`` or ``assistant``.
        content: Message text.
    """
    if not content or channel not in CHANNELS:
        return

    ensure_workspace(user_id)
    _ensure_chats_dir(user_id)
    path = _channel_path(user_id, channel)

    with get_lock(user_id):
        _rotate_if_needed(path, channel)
        entry = {
            "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "role": role,
            "channel": channel,
            "content": content[:10_000],  # cap to prevent bloat
        }
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            logger.debug("Failed to write chat log for %s/%s", user_id, channel, exc_info=True)


def recent(user_id: str, channel: str, limit: int = 50) -> list[dict]:
    """Read the most recent messages from a channel's chat log.

    Returns a list of dicts with ``ts``, ``role``, ``channel``, ``content`` keys,
    ordered oldest-first (i.e. chronological).
    """
    path = _channel_path(user_id, channel)
    if not os.path.isfile(path):
        return []

    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return []

    messages: list[dict] = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            messages.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return messages


def recent_as_text(user_id: str, channel: str, limit: int = 50) -> str:
    """Return recent messages formatted as readable text for agent context."""
    msgs = recent(user_id, channel, limit)
    if not msgs:
        return ""
    lines = []
    for m in msgs:
        role = m.get("role", "?").upper()
        ts = m.get("ts", "")
        content = m.get("content", "")
        lines.append(f"[{ts}] {role}: {content}")
    return "\n".join(lines)


def search_channel(user_id: str, channel: str, query: str,
                   max_results: int = 20) -> list[dict]:
    """Search a single channel's chat log for messages matching *query*."""
    results: list[dict] = []
    query_lower = query.lower()

    # Search current file, then .old file.
    for path in (_channel_path(user_id, channel),
                 _channel_path(user_id, channel).replace(".jsonl", ".old.jsonl")):
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in reversed(f.readlines()):
                    if len(results) >= max_results:
                        return results
                    line = line.strip()
                    if not line or query_lower not in line.lower():
                        continue
                    try:
                        msg = json.loads(line)
                        results.append(msg)
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue

    # Also search archived files.
    root = workspace_root(user_id)
    archive_dir = os.path.join(root, "archive", "chat_logs")
    if os.path.isdir(archive_dir):
        prefix = f"{channel}."
        for fname in sorted(os.listdir(archive_dir), reverse=True):
            if not fname.startswith(prefix):
                continue
            if len(results) >= max_results:
                break
            fpath = os.path.join(archive_dir, fname)
            try:
                with open(fpath, encoding="utf-8", errors="replace") as f:
                    for line in reversed(f.readlines()):
                        if len(results) >= max_results:
                            break
                        line = line.strip()
                        if not line or query_lower not in line.lower():
                            continue
                        try:
                            results.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
            except OSError:
                continue

    return results


def search_all(user_id: str, query: str, max_results: int = 20) -> list[dict]:
    """Search all channel chat logs for messages matching *query*.

    Returns results from all channels, most recent first, with a ``channel``
    key on each so Prax knows where the conversation happened.
    """
    all_results: list[dict] = []
    per_channel = max(max_results // len(CHANNELS), 5)

    for ch in CHANNELS:
        hits = search_channel(user_id, ch, query, per_channel)
        all_results.extend(hits)

    # Sort by timestamp descending, take top max_results.
    all_results.sort(key=lambda m: m.get("ts", ""), reverse=True)
    return all_results[:max_results]


def list_active_channels(user_id: str) -> list[str]:
    """Return channel names that have at least one message logged."""
    active = []
    for ch in CHANNELS:
        path = _channel_path(user_id, ch)
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            active.append(ch)
    return active
