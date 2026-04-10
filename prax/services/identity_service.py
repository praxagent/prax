"""Unified user identity service — UUID-based user records with provider linking.

Every user gets a stable UUID regardless of how they connect (SMS, Discord,
TeamWork web UI).  Provider-specific IDs (phone numbers, Discord user IDs,
TeamWork channel hashes) are linked to the canonical user via the
``user_identities`` table.

Usage::

    from prax.services.identity_service import resolve_user, update_user

    user = resolve_user("sms", "+15551234567")   # auto-creates if new
    user = resolve_user("discord", "1034618247871483964")
    update_user(user.id, display_name="Alice", timezone="America/New_York")
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
import sqlite3
import threading
import uuid
from datetime import UTC, datetime

from prax.settings import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_initialized = False


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class User:
    id: str                          # UUID4
    display_name: str
    workspace_dir: str               # e.g. "usr_a1b2c3d4"
    timezone: str                    # e.g. "America/New_York", "" = unset
    created_at: str                  # ISO 8601


# ---------------------------------------------------------------------------
# Database lifecycle
# ---------------------------------------------------------------------------

def _db_path() -> str:
    return settings.identity_db


def _connect() -> sqlite3.Connection:
    global _initialized
    conn = sqlite3.connect(_db_path())
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    if not _initialized:
        _ensure_tables(conn)
        _initialized = True
    return conn


def _ensure_tables(conn: sqlite3.Connection) -> None:
    """Create tables if they don't exist (idempotent)."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id            TEXT PRIMARY KEY,
            display_name  TEXT NOT NULL DEFAULT '',
            workspace_dir TEXT NOT NULL,
            timezone      TEXT NOT NULL DEFAULT '',
            created_at    TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS user_identities (
            provider    TEXT NOT NULL,
            external_id TEXT NOT NULL,
            user_id     TEXT NOT NULL REFERENCES users(id),
            linked_at   TEXT NOT NULL,
            PRIMARY KEY (provider, external_id)
        );

        CREATE INDEX IF NOT EXISTS idx_identities_user
            ON user_identities(user_id);
    """)


def init_identity_db(db_path: str | None = None) -> None:
    """Create tables if they don't exist.  Safe to call on every startup."""
    path = db_path or _db_path()
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id            TEXT PRIMARY KEY,
            display_name  TEXT NOT NULL DEFAULT '',
            workspace_dir TEXT NOT NULL,
            timezone      TEXT NOT NULL DEFAULT '',
            created_at    TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS user_identities (
            provider    TEXT NOT NULL,
            external_id TEXT NOT NULL,
            user_id     TEXT NOT NULL REFERENCES users(id),
            linked_at   TEXT NOT NULL,
            PRIMARY KEY (provider, external_id)
        );

        CREATE INDEX IF NOT EXISTS idx_identities_user
            ON user_identities(user_id);
    """)
    conn.commit()
    conn.close()
    logger.info("Identity database initialized at %s", path)


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------

def resolve_user(provider: str, external_id: str, display_name: str = "") -> User:
    """Look up or auto-create a user for (provider, external_id).

    If the identity is already linked, returns the existing user.
    Otherwise creates a new user, links the identity, and returns it.

    If ``display_name`` is provided, it's used for new users (ignored for
    existing ones — use ``update_user`` to change names).
    """
    with _lock:
        conn = _connect()
        try:
            row = conn.execute(
                """SELECT u.id, u.display_name, u.workspace_dir, u.timezone, u.created_at
                   FROM users u
                   JOIN user_identities i ON i.user_id = u.id
                   WHERE i.provider = ? AND i.external_id = ?""",
                (provider, external_id),
            ).fetchone()

            if row:
                return User(*row)

            # Auto-create
            user_id = str(uuid.uuid4())
            workspace_dir = f"usr_{user_id[:8]}"
            now = datetime.now(UTC).isoformat()

            if not display_name:
                display_name = _default_display_name(provider, external_id)

            conn.execute(
                "INSERT INTO users (id, display_name, workspace_dir, timezone, created_at) "
                "VALUES (?, ?, ?, '', ?)",
                (user_id, display_name, workspace_dir, now),
            )
            conn.execute(
                "INSERT INTO user_identities (provider, external_id, user_id, linked_at) "
                "VALUES (?, ?, ?, ?)",
                (provider, external_id, user_id, now),
            )
            conn.commit()
            logger.info(
                "Created user %s (%s) for %s:%s → workspace %s",
                display_name, user_id[:8], provider, external_id[:20], workspace_dir,
            )
            return User(user_id, display_name, workspace_dir, "", now)
        finally:
            conn.close()


def get_user(user_id: str) -> User | None:
    """Get a user by UUID."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT id, display_name, workspace_dir, timezone, created_at "
            "FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        return User(*row) if row else None
    finally:
        conn.close()


def get_user_by_identity(provider: str, external_id: str) -> User | None:
    """Look up a user by provider identity without auto-creating."""
    conn = _connect()
    try:
        row = conn.execute(
            """SELECT u.id, u.display_name, u.workspace_dir, u.timezone, u.created_at
               FROM users u
               JOIN user_identities i ON i.user_id = u.id
               WHERE i.provider = ? AND i.external_id = ?""",
            (provider, external_id),
        ).fetchone()
        return User(*row) if row else None
    finally:
        conn.close()


def list_users() -> list[User]:
    """Return all users."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, display_name, workspace_dir, timezone, created_at "
            "FROM users ORDER BY created_at"
        ).fetchall()
        return [User(*r) for r in rows]
    finally:
        conn.close()


def update_user(user_id: str, **kwargs: str) -> User | None:
    """Update user fields.  Accepted kwargs: display_name, timezone."""
    allowed = {"display_name", "timezone"}
    updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not updates:
        return get_user(user_id)

    with _lock:
        conn = _connect()
        try:
            sets = ", ".join(f"{k} = ?" for k in updates)
            vals = list(updates.values()) + [user_id]
            conn.execute(f"UPDATE users SET {sets} WHERE id = ?", vals)
            conn.commit()
            return get_user(user_id)
        finally:
            conn.close()


def link_identity(user_id: str, provider: str, external_id: str) -> bool:
    """Link a provider identity to an existing user.

    Returns True if linked, False if the identity is already linked elsewhere.
    """
    with _lock:
        conn = _connect()
        try:
            existing = conn.execute(
                "SELECT user_id FROM user_identities WHERE provider = ? AND external_id = ?",
                (provider, external_id),
            ).fetchone()
            if existing:
                return existing[0] == user_id  # True if already linked to this user
            conn.execute(
                "INSERT INTO user_identities (provider, external_id, user_id, linked_at) "
                "VALUES (?, ?, ?, ?)",
                (provider, external_id, user_id, datetime.now(UTC).isoformat()),
            )
            conn.commit()
            logger.info("Linked %s:%s → user %s", provider, external_id[:20], user_id[:8])
            return True
        finally:
            conn.close()


def get_identities(user_id: str) -> list[dict]:
    """Return all provider identities linked to a user."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT provider, external_id, linked_at FROM user_identities WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        return [{"provider": r[0], "external_id": r[1], "linked_at": r[2]} for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Migration — import existing phone/Discord users
# ---------------------------------------------------------------------------

def reconcile_workspace_dir() -> None:
    """Ensure the active user's workspace_dir matches PRAX_USER_ID.

    Called on startup.  If the user already exists in the identity DB but
    their ``workspace_dir`` doesn't match the ``PRAX_USER_ID`` env var
    (e.g. first deploy after setting the var, or a migration), update it
    so the Docker volume mount and the identity service agree.
    """
    prax_user_id = settings.prax_user_id
    if not prax_user_id:
        return

    # Find the user that TeamWork will resolve to
    phone = getattr(settings, "teamwork_user_phone", "")
    if phone:
        user = get_user_by_identity("sms", phone)
    else:
        user = get_user_by_identity("teamwork", "default")

    if user and user.workspace_dir != prax_user_id:
        logger.info(
            "Reconciling workspace_dir: %s → %s (to match PRAX_USER_ID)",
            user.workspace_dir, prax_user_id,
        )
        conn = _connect()
        try:
            conn.execute(
                "UPDATE users SET workspace_dir = ? WHERE id = ?",
                (prax_user_id, user.id),
            )
            conn.commit()
        finally:
            conn.close()

        # Create a symlink from the old dir to the new one if needed
        old_dir = os.path.join(settings.workspace_dir, user.workspace_dir)
        new_dir = os.path.join(settings.workspace_dir, prax_user_id)
        if os.path.isdir(old_dir) and not os.path.exists(new_dir):
            os.symlink(os.path.abspath(old_dir), new_dir)
            logger.info("Symlinked workspace %s → %s", old_dir, new_dir)


def migrate_legacy_users() -> int:
    """Import users from env-var-based identity maps (PHONE_TO_NAME_MAP, etc).

    Also links Discord identities via DISCORD_TO_PHONE_MAP.
    Creates symlinks for existing workspace directories.
    Safe to call repeatedly — skips already-linked identities.
    """
    migrated = 0

    # Import phone users
    phone_names: dict[str, str] = {}
    if settings.phone_to_name_map:
        try:
            phone_names = json.loads(settings.phone_to_name_map)
        except (json.JSONDecodeError, TypeError):
            pass

    for phone, name in phone_names.items():
        existing = get_user_by_identity("sms", phone)
        if existing:
            continue
        user = resolve_user("sms", phone, display_name=name)

        # Symlink old workspace dir → new
        old_dir = os.path.join(settings.workspace_dir, phone.lstrip("+"))
        new_dir = os.path.join(settings.workspace_dir, user.workspace_dir)
        if os.path.isdir(old_dir) and not os.path.exists(new_dir):
            os.symlink(os.path.abspath(old_dir), new_dir)
            logger.info("Symlinked workspace %s → %s", old_dir, new_dir)

        migrated += 1

    # Link Discord identities to existing phone users
    discord_phone_map: dict[str, str] = {}
    if settings.discord_to_phone_map:
        try:
            raw = settings.discord_to_phone_map
            if raw and raw.lower() != "false":
                discord_phone_map = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            pass

    discord_names: dict[str, str] = {}
    if settings.discord_allowed_users:
        try:
            discord_names = json.loads(settings.discord_allowed_users)
        except (json.JSONDecodeError, TypeError):
            pass

    for discord_id, phone in discord_phone_map.items():
        phone_user = get_user_by_identity("sms", phone)
        if phone_user:
            link_identity(phone_user.id, "discord", discord_id)

    # Create standalone Discord users (no phone mapping)
    for discord_id, name in discord_names.items():
        if discord_id in discord_phone_map:
            continue  # Already linked above
        existing = get_user_by_identity("discord", discord_id)
        if not existing:
            resolve_user("discord", discord_id, display_name=name)
            migrated += 1

    if migrated:
        logger.info("Migrated %d legacy users to identity database", migrated)
    return migrated


# ---------------------------------------------------------------------------
# Workspace archiving
# ---------------------------------------------------------------------------

def archive_workspace(user_id: str) -> str | None:
    """Archive a user's workspace as a timestamped zip and create a fresh one.

    Returns the archive path, or None if no workspace exists.
    """
    import shutil

    user = get_user(user_id)
    if not user:
        return None

    ws_root = os.path.join(settings.workspace_dir, user.workspace_dir)
    if not os.path.isdir(ws_root):
        return None

    # Create archives dir
    archives_dir = os.path.join(settings.workspace_dir, ".archives")
    os.makedirs(archives_dir, exist_ok=True)

    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    archive_name = f"{user.workspace_dir}_{timestamp}"
    archive_path = os.path.join(archives_dir, archive_name)

    # Create zip archive
    shutil.make_archive(archive_path, "zip", ws_root)
    logger.info("Archived workspace %s → %s.zip", ws_root, archive_path)

    # Remove old workspace contents (keep the directory)
    for item in os.listdir(ws_root):
        item_path = os.path.join(ws_root, item)
        if os.path.isdir(item_path):
            shutil.rmtree(item_path)
        else:
            os.remove(item_path)

    # Re-initialize fresh workspace
    from prax.services.workspace_service import ensure_workspace
    ensure_workspace(user_id)

    return f"{archive_path}.zip"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _default_display_name(provider: str, external_id: str) -> str:
    """Generate a reasonable default display name from the provider identity."""
    if provider == "sms":
        # Try phone_to_name_map
        try:
            names = json.loads(settings.phone_to_name_map or "{}")
            if external_id in names:
                return names[external_id]
        except (json.JSONDecodeError, TypeError):
            pass
        # Use last 4 digits of phone
        return f"User ({external_id[-4:]})"

    if provider == "discord":
        # Try discord_allowed_users
        try:
            users = json.loads(settings.discord_allowed_users or "{}")
            if external_id in users:
                return users[external_id]
        except (json.JSONDecodeError, TypeError):
            pass
        return f"Discord User ({external_id[-4:]})"

    if provider == "teamwork":
        return "TeamWork User"

    return f"User ({external_id[-4:]})"
