"""Path helpers for per-user service state stored under the workspace root."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from prax.settings import settings

_DEFAULT_CONVERSATION_DB = "conversations.db"
_SERVICE_SUBDIR = (".services", "prax")


def _strip_quotes(value: str) -> str:
    value = (value or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _database_name(database_name: str | None = None) -> str:
    return _strip_quotes(database_name if database_name is not None else settings.database_name)


def _uses_default_conversation_db(database_name: str | None = None) -> bool:
    configured = _database_name(database_name)
    if not configured:
        return True
    return configured == _DEFAULT_CONVERSATION_DB or configured == f"./{_DEFAULT_CONVERSATION_DB}"


def _effective_user_id(user_id: str | None = None) -> str:
    if user_id:
        return user_id
    if settings.prax_user_id:
        return settings.prax_user_id
    try:
        from prax.agent.user_context import current_user_id
        return current_user_id.get() or ""
    except Exception:
        return ""


def service_state_dir(user_id: str | None = None) -> str:
    """Return the Prax service-state directory for a user workspace."""
    uid = _effective_user_id(user_id)
    if not uid:
        raise ValueError("No user_id or PRAX_USER_ID available for workspace service state.")

    from prax.services import workspace_service

    root = workspace_service.ensure_workspace(uid)
    path = workspace_service.safe_join(root, *_SERVICE_SUBDIR)
    os.makedirs(path, exist_ok=True)
    return path


def conversation_db_path(
    user_id: str | None = None,
    database_name: str | None = None,
) -> str:
    """Return the SQLite conversation-history DB path.

    Explicit ``DATABASE_NAME`` overrides are preserved for tests/admin usage.
    The legacy default ``conversations.db`` is redirected into the single-user
    workspace service-state directory whenever a user/workspace id is known.
    """
    configured = _database_name(database_name)
    if not _uses_default_conversation_db(database_name):
        return configured

    if _effective_user_id(user_id):
        return os.path.join(service_state_dir(user_id), _DEFAULT_CONVERSATION_DB)
    return configured or _DEFAULT_CONVERSATION_DB


def _legacy_db_path(database_name: str | None = None) -> str:
    configured = _database_name(database_name)
    return configured or _DEFAULT_CONVERSATION_DB


def migrate_legacy_conversation_db(
    user_id: str | None = None,
    database_name: str | None = None,
) -> dict:
    """Copy the old repo-root conversations.db into workspace service state once."""
    target = conversation_db_path(user_id, database_name)
    legacy = _legacy_db_path(database_name)
    if target == legacy:
        return {"status": "skipped", "reason": "target_is_legacy_path", "path": target}
    if not _uses_default_conversation_db(database_name):
        return {"status": "skipped", "reason": "explicit_database_name", "path": target}
    if not os.path.isfile(legacy):
        return {"status": "skipped", "reason": "legacy_missing", "path": target}

    marker = os.path.join(os.path.dirname(target), ".conversations_migrated")
    if os.path.exists(target):
        if not os.path.isfile(marker):
            _write_migration_marker(marker, legacy, target, "target_already_exists")
        return {"status": "skipped", "reason": "target_exists", "path": target}
    os.makedirs(os.path.dirname(target), exist_ok=True)
    try:
        source_conn = sqlite3.connect(legacy)
        target_conn = sqlite3.connect(target)
        try:
            source_conn.backup(target_conn)
        finally:
            target_conn.close()
            source_conn.close()
        method = "sqlite_backup"
    except sqlite3.DatabaseError:
        shutil.copy2(legacy, target)
        method = "file_copy"

    _write_migration_marker(marker, legacy, target, method)
    return {"status": "migrated", "path": target, "legacy_path": legacy, "method": method}


def _write_migration_marker(marker: str, legacy: str, target: str, method: str) -> None:
    Path(marker).write_text(
        json.dumps(
            {
                "migrated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                "legacy_path": legacy,
                "target_path": target,
                "method": method,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def ensure_conversation_db(
    user_id: str | None = None,
    database_name: str | None = None,
) -> str:
    """Migrate if needed, initialize the conversation DB, and return its path."""
    path = conversation_db_path(user_id, database_name)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    migrate_legacy_conversation_db(user_id, database_name)

    from prax.conversation_memory import init_database

    init_database(path)
    return path
