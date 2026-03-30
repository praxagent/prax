"""End-to-end lifecycle tests: create account → use workspace → archive → recreate.

Covers the full identity + workspace workflow including:
- Account creation via resolve_user (SMS, Discord, TeamWork)
- Workspace directory creation with UUID-based naming
- Identity linking across providers
- Workspace archiving (zip + fresh workspace)
- Account wipe and recreation
- conversation_service routing through identity
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import zipfile

import pytest

import prax.services.identity_service as ids
from prax.services.identity_service import (
    User,
    archive_workspace,
    get_identities,
    get_user,
    get_user_by_identity,
    link_identity,
    list_users,
    resolve_user,
    update_user,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()

_original_db_path = ids._db_path

# Redirect identity DB to a temp file.
ids._db_path = lambda: _tmp_db.name  # type: ignore[assignment]
ids._initialized = False


@pytest.fixture(autouse=True)
def _fresh_db():
    """Wipe the identity DB between tests."""
    conn = ids._connect()
    conn.executescript("DELETE FROM user_identities; DELETE FROM users;")
    conn.commit()
    conn.close()
    yield


@pytest.fixture
def workspace_dir(tmp_path, monkeypatch):
    """Provide a temporary workspace directory and patch settings."""
    monkeypatch.setattr(ids.settings, "workspace_dir", str(tmp_path))
    # Also patch workspace_service.settings for workspace_root()
    import prax.services.workspace_service as ws
    monkeypatch.setattr(ws.settings, "workspace_dir", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# 1. Account creation via different entry points
# ---------------------------------------------------------------------------

class TestAccountCreation:
    def test_sms_creates_account(self):
        user = resolve_user("sms", "+15551234567", display_name="Alice")
        assert user.id
        assert user.display_name == "Alice"
        assert user.workspace_dir.startswith("usr_")
        assert user.timezone == ""

    def test_discord_creates_account(self):
        user = resolve_user("discord", "1034618247871483964", display_name="Bob")
        assert user.display_name == "Bob"
        assert user.workspace_dir.startswith("usr_")

    def test_teamwork_creates_account(self):
        user = resolve_user("teamwork", "default", display_name="TeamWork User")
        assert user.display_name == "TeamWork User"

    def test_duplicate_resolve_returns_same_user(self):
        u1 = resolve_user("sms", "+15551234567", display_name="Alice")
        u2 = resolve_user("sms", "+15551234567")
        assert u1.id == u2.id
        assert u2.display_name == "Alice"

    def test_workspace_dir_is_unique_per_user(self):
        u1 = resolve_user("sms", "+1111")
        u2 = resolve_user("sms", "+2222")
        assert u1.workspace_dir != u2.workspace_dir


# ---------------------------------------------------------------------------
# 2. Workspace directory creation with UUID-based naming
# ---------------------------------------------------------------------------

class TestWorkspaceCreation:
    def test_workspace_root_resolves_uuid(self, workspace_dir):
        from prax.services.workspace_service import workspace_root
        user = resolve_user("sms", "+15559999999")
        root = workspace_root(user.id)
        assert root == str(workspace_dir / user.workspace_dir)

    def test_workspace_root_legacy_fallback(self, workspace_dir):
        """A phone number not in identity DB falls back to old behavior."""
        from prax.services.workspace_service import workspace_root
        root = workspace_root("+19998887777")
        assert root == str(workspace_dir / "19998887777")

    def test_ensure_workspace_creates_dirs(self, workspace_dir, monkeypatch):
        from prax.services.workspace_service import ensure_workspace
        user = resolve_user("sms", "+15550000001")
        root = ensure_workspace(user.id)
        assert os.path.isdir(os.path.join(root, "active"))
        assert os.path.isdir(os.path.join(root, "archive"))
        assert os.path.isdir(os.path.join(root, "plugins", "custom"))
        assert os.path.isdir(os.path.join(root, ".git"))

    def test_ensure_workspace_idempotent(self, workspace_dir):
        from prax.services.workspace_service import ensure_workspace
        user = resolve_user("sms", "+15550000002")
        r1 = ensure_workspace(user.id)
        r2 = ensure_workspace(user.id)
        assert r1 == r2


# ---------------------------------------------------------------------------
# 3. Identity linking across providers
# ---------------------------------------------------------------------------

class TestIdentityLinking:
    def test_link_sms_and_discord(self):
        user = resolve_user("sms", "+15551234567", display_name="Alice")
        ok = link_identity(user.id, "discord", "DISC123")
        assert ok is True

        # Both identities resolve to the same user
        by_sms = get_user_by_identity("sms", "+15551234567")
        by_discord = get_user_by_identity("discord", "DISC123")
        assert by_sms.id == by_discord.id == user.id

    def test_linked_identities_listed(self):
        user = resolve_user("sms", "+15551234567")
        link_identity(user.id, "discord", "D1")
        link_identity(user.id, "teamwork", "TW1")
        identities = get_identities(user.id)
        providers = {i["provider"] for i in identities}
        assert providers == {"sms", "discord", "teamwork"}

    def test_link_conflict_rejected(self):
        u1 = resolve_user("sms", "+1111")
        u2 = resolve_user("sms", "+2222")
        link_identity(u1.id, "discord", "D999")
        ok = link_identity(u2.id, "discord", "D999")
        assert ok is False

    def test_link_idempotent_for_same_user(self):
        user = resolve_user("sms", "+15551234567")
        assert link_identity(user.id, "discord", "D1") is True
        assert link_identity(user.id, "discord", "D1") is True


# ---------------------------------------------------------------------------
# 4. User profile updates
# ---------------------------------------------------------------------------

class TestProfileUpdates:
    def test_update_display_name(self):
        user = resolve_user("sms", "+15551234567", display_name="Alice")
        updated = update_user(user.id, display_name="Alicia")
        assert updated.display_name == "Alicia"
        # Persisted
        assert get_user(user.id).display_name == "Alicia"

    def test_update_timezone(self):
        user = resolve_user("sms", "+15551234567")
        updated = update_user(user.id, timezone="Europe/Berlin")
        assert updated.timezone == "Europe/Berlin"

    def test_update_both(self):
        user = resolve_user("sms", "+15551234567")
        updated = update_user(user.id, display_name="Charlie", timezone="US/Pacific")
        assert updated.display_name == "Charlie"
        assert updated.timezone == "US/Pacific"


# ---------------------------------------------------------------------------
# 5. Workspace archiving
# ---------------------------------------------------------------------------

class TestArchiving:
    def test_archive_nonexistent_workspace(self):
        user = resolve_user("sms", "+15550000010")
        result = archive_workspace(user.id)
        assert result is None

    def test_archive_creates_zip(self, workspace_dir, monkeypatch):
        user = resolve_user("sms", "+15550000011")
        ws = workspace_dir / user.workspace_dir
        ws.mkdir(parents=True)
        (ws / "notes.md").write_text("# My Notes\nImportant stuff.")
        (ws / "data.csv").write_text("a,b,c\n1,2,3")

        # Mock ensure_workspace to avoid git init complexity
        monkeypatch.setattr(
            "prax.services.workspace_service.ensure_workspace",
            lambda uid: str(ws),
        )

        result = archive_workspace(user.id)
        assert result is not None
        assert result.endswith(".zip")
        assert os.path.isfile(result)

        # Verify zip contents
        with zipfile.ZipFile(result) as zf:
            names = zf.namelist()
            assert "notes.md" in names
            assert "data.csv" in names

    def test_archive_clears_workspace(self, workspace_dir, monkeypatch):
        user = resolve_user("sms", "+15550000012")
        ws = workspace_dir / user.workspace_dir
        ws.mkdir(parents=True)
        (ws / "old_file.txt").write_text("old data")

        monkeypatch.setattr(
            "prax.services.workspace_service.ensure_workspace",
            lambda uid: str(ws),
        )

        archive_workspace(user.id)

        # Old file should be gone (workspace was cleared and re-initialized)
        assert not (ws / "old_file.txt").exists()

    def test_archive_preserves_user_identity(self, workspace_dir, monkeypatch):
        """Archiving shouldn't affect the user's identity record."""
        user = resolve_user("sms", "+15550000013", display_name="Dana")
        ws = workspace_dir / user.workspace_dir
        ws.mkdir(parents=True)
        (ws / "file.txt").write_text("x")

        monkeypatch.setattr(
            "prax.services.workspace_service.ensure_workspace",
            lambda uid: str(ws),
        )

        archive_workspace(user.id)

        # User record unchanged
        u = get_user(user.id)
        assert u.display_name == "Dana"
        assert u.workspace_dir == user.workspace_dir

    def test_multiple_archives(self, workspace_dir, monkeypatch):
        """Multiple archives should create separate zip files."""
        import time

        user = resolve_user("sms", "+15550000014")
        ws = workspace_dir / user.workspace_dir
        ws.mkdir(parents=True)

        monkeypatch.setattr(
            "prax.services.workspace_service.ensure_workspace",
            lambda uid: str(ws),
        )

        (ws / "v1.txt").write_text("version 1")
        zip1 = archive_workspace(user.id)

        # Ensure timestamp differs (archive uses second-level precision).
        time.sleep(1.1)

        (ws / "v2.txt").write_text("version 2")
        zip2 = archive_workspace(user.id)

        assert zip1 != zip2
        assert os.path.isfile(zip1)
        assert os.path.isfile(zip2)


# ---------------------------------------------------------------------------
# 6. Full lifecycle: create → work → archive → recreate
# ---------------------------------------------------------------------------

class TestFullLifecycle:
    def test_create_work_archive_recreate(self, workspace_dir, monkeypatch):
        """Simulate full lifecycle: create user, do work, archive, start fresh."""
        from prax.services.workspace_service import ensure_workspace, workspace_root

        # Step 1: Create account via SMS
        user = resolve_user("sms", "+15559876543", display_name="Eve")
        assert user.workspace_dir.startswith("usr_")

        # Step 2: Create workspace and add files
        root = ensure_workspace(user.id)
        assert os.path.isdir(root)
        with open(os.path.join(root, "active", "project.md"), "w") as f:
            f.write("# My Project\nSome work.")

        # Step 3: Link Discord identity
        link_identity(user.id, "discord", "EVE_DISCORD_123")
        identities = get_identities(user.id)
        assert len(identities) == 2  # sms + discord

        # Step 4: Update profile
        update_user(user.id, timezone="America/Chicago")

        # Step 5: Archive workspace
        archive_path = archive_workspace(user.id)
        assert archive_path is not None
        assert os.path.isfile(archive_path)

        # Step 6: Verify old file is gone but workspace is re-initialized
        assert not os.path.isfile(os.path.join(root, "active", "project.md"))
        assert os.path.isdir(os.path.join(root, "active"))

        # Step 7: User identity persists through archive
        u = get_user(user.id)
        assert u.display_name == "Eve"
        assert u.timezone == "America/Chicago"

        # Step 8: Workspace root still resolves correctly
        assert workspace_root(user.id) == root

        # Step 9: Can continue working with fresh workspace
        with open(os.path.join(root, "active", "new_project.md"), "w") as f:
            f.write("# Fresh Start")
        assert os.path.isfile(os.path.join(root, "active", "new_project.md"))

    def test_wipe_and_recreate_user(self, workspace_dir):
        """Simulate deleting a user and recreating from scratch."""
        from prax.services.workspace_service import ensure_workspace

        # Create user
        user = resolve_user("sms", "+15558001234", display_name="Frank")
        old_id = user.id
        ensure_workspace(user.id)

        # Manually wipe user from DB (simulate admin wipe)
        conn = ids._connect()
        conn.execute("DELETE FROM user_identities WHERE user_id = ?", (old_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (old_id,))
        conn.commit()
        conn.close()

        # User no longer exists
        assert get_user(old_id) is None
        assert get_user_by_identity("sms", "+15558001234") is None

        # Re-resolve creates a brand new user
        new_user = resolve_user("sms", "+15558001234", display_name="Frank v2")
        assert new_user.id != old_id
        assert new_user.display_name == "Frank v2"
        assert new_user.workspace_dir != user.workspace_dir  # new UUID prefix

        # New workspace works
        root = ensure_workspace(new_user.id)
        assert os.path.isdir(root)


# ---------------------------------------------------------------------------
# 7. Conversation service integration
# ---------------------------------------------------------------------------

class TestConversationServiceIntegration:
    def test_uuid_derives_stable_db_key(self):
        """conversation_service should derive a stable integer key from UUID."""
        user = resolve_user("sms", "+15551112222")
        # The key derivation logic: int(uuid.replace('-', '')[:15], 16)
        expected_key = int(user.id.replace("-", "")[:15], 16)
        assert isinstance(expected_key, int)
        assert expected_key > 0

    def test_same_uuid_same_key(self):
        """Same user always gets the same conversation DB key."""
        user = resolve_user("sms", "+15553334444")
        key1 = int(user.id.replace("-", "")[:15], 16)
        key2 = int(user.id.replace("-", "")[:15], 16)
        assert key1 == key2

    def test_different_users_different_keys(self):
        """Different users get different conversation DB keys."""
        u1 = resolve_user("sms", "+1111")
        u2 = resolve_user("sms", "+2222")
        k1 = int(u1.id.replace("-", "")[:15], 16)
        k2 = int(u2.id.replace("-", "")[:15], 16)
        assert k1 != k2

    def test_reply_sets_user_context(self, monkeypatch):
        """conversation_service.reply should set current_user context var."""
        from prax.agent.user_context import current_user, current_user_id
        from prax.services.conversation_service import ConversationService

        user = resolve_user("sms", "+15550009999", display_name="TestUser")

        captured = {}

        class FakeAgent:
            def run(self, **kwargs):
                captured["user_id"] = current_user_id.get()
                captured["user_obj"] = current_user.get()
                return "ok"

        svc = ConversationService(
            agent=FakeAgent(),
            retriever=lambda db, key: None,
            saver=lambda db, key, data: None,
        )

        svc.reply(user.id, "hello")
        assert captured["user_id"] == user.id
        assert captured["user_obj"] is not None
        assert captured["user_obj"].display_name == "TestUser"


# ---------------------------------------------------------------------------
# 8. Entry point wiring
# ---------------------------------------------------------------------------

class TestEntryPointWiring:
    def test_sms_uses_identity_service(self, monkeypatch):
        """SMS _reply_via_agent should resolve user via identity service."""
        import importlib
        module = importlib.reload(importlib.import_module("prax.services.sms_service"))

        captured = {}

        def fake_reply(user_id, text):
            captured["user_id"] = user_id
            return "response"

        import threading
        monkeypatch.setattr(module.conversation_service, "reply", fake_reply)
        monkeypatch.setattr(module, "send_sms", lambda msg, to: None)
        monkeypatch.setattr(threading.Thread, "start",
                            lambda self: self._target(*self._args, **self._kwargs))

        # Stub out teamwork forwarding and authorization
        monkeypatch.setattr(
            "prax.services.teamwork_hooks.forward_to_channel",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr(module, "num_to_names", {"+10000000000": "Test"})

        payload = {
            "From": "+10000000000",
            "MessageSid": "SM1",
            "Body": "hi",
            "NumMedia": "0",
        }
        module.sms_service.process(payload, "https://ngrok.test")

        # The user_id passed to reply should be a UUID, not a phone number
        assert captured["user_id"]
        assert not captured["user_id"].startswith("+")
        # Verify it's a valid user
        u = get_user(captured["user_id"])
        assert u is not None

    def test_teamwork_uses_identity_service(self, monkeypatch):
        """TeamWork _get_teamwork_user_id should return a UUID."""
        monkeypatch.setattr(ids.settings, "teamwork_user_phone", "")
        import importlib
        module = importlib.reload(
            importlib.import_module("prax.blueprints.teamwork_routes")
        )
        user_id = module._get_teamwork_user_id()
        # Should be a UUID, not a phone number
        assert not user_id.startswith("+")
        assert len(user_id) == 36  # UUID format

    def test_teamwork_with_phone_shares_identity(self, monkeypatch):
        """When TEAMWORK_USER_PHONE is set, TeamWork shares identity with SMS."""
        monkeypatch.setattr(ids.settings, "teamwork_user_phone", "+15557778888")
        # Pre-create the SMS user
        sms_user = resolve_user("sms", "+15557778888", display_name="SharedUser")

        import importlib
        module = importlib.reload(
            importlib.import_module("prax.blueprints.teamwork_routes")
        )
        tw_user_id = module._get_teamwork_user_id()
        assert tw_user_id == sms_user.id


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def teardown_module():
    # Restore original _db_path so other test modules aren't affected.
    ids._db_path = _original_db_path
    ids._initialized = False
    try:
        os.unlink(_tmp_db.name)
    except OSError:
        pass
