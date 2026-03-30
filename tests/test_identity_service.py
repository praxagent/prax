"""Tests for the unified user identity service."""
from __future__ import annotations

import os
import tempfile

import pytest

# Patch the identity DB path before importing the service.
_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()

import prax.services.identity_service as ids

_original_db_path = ids._db_path

# Point at an ephemeral DB for the test run.
ids._db_path = lambda: _tmp_db.name  # type: ignore[assignment]
ids._initialized = False  # force re-init with new path


@pytest.fixture(autouse=True)
def _fresh_db():
    """Wipe the identity DB between tests."""
    conn = ids._connect()
    conn.executescript("""
        DELETE FROM user_identities;
        DELETE FROM users;
    """)
    conn.commit()
    conn.close()
    yield


# ---------------------------------------------------------------------------
# resolve_user — auto-create + lookup
# ---------------------------------------------------------------------------

def test_resolve_user_creates_new():
    user = ids.resolve_user("sms", "+15551234567", display_name="Alice")
    assert user.display_name == "Alice"
    assert user.workspace_dir.startswith("usr_")
    assert user.id  # UUID


def test_resolve_user_returns_existing():
    u1 = ids.resolve_user("sms", "+15551234567", display_name="Alice")
    u2 = ids.resolve_user("sms", "+15551234567", display_name="Should Ignore")
    assert u1.id == u2.id
    assert u2.display_name == "Alice"  # display_name not updated on re-resolve


def test_resolve_user_different_providers_create_separate_users():
    sms_user = ids.resolve_user("sms", "+15551234567")
    discord_user = ids.resolve_user("discord", "1234567890")
    assert sms_user.id != discord_user.id


def test_resolve_user_default_display_name_sms():
    user = ids.resolve_user("sms", "+15551234567")
    assert "4567" in user.display_name  # last 4 digits


def test_resolve_user_default_display_name_teamwork():
    user = ids.resolve_user("teamwork", "default")
    assert "TeamWork" in user.display_name


# ---------------------------------------------------------------------------
# get_user / get_user_by_identity
# ---------------------------------------------------------------------------

def test_get_user_found():
    created = ids.resolve_user("sms", "+15550000000", display_name="Bob")
    found = ids.get_user(created.id)
    assert found is not None
    assert found.display_name == "Bob"


def test_get_user_not_found():
    assert ids.get_user("nonexistent-uuid") is None


def test_get_user_by_identity():
    created = ids.resolve_user("discord", "9999")
    found = ids.get_user_by_identity("discord", "9999")
    assert found is not None
    assert found.id == created.id


def test_get_user_by_identity_not_found():
    assert ids.get_user_by_identity("discord", "0000") is None


# ---------------------------------------------------------------------------
# list_users
# ---------------------------------------------------------------------------

def test_list_users():
    ids.resolve_user("sms", "+1")
    ids.resolve_user("sms", "+2")
    users = ids.list_users()
    assert len(users) == 2


# ---------------------------------------------------------------------------
# update_user
# ---------------------------------------------------------------------------

def test_update_display_name():
    user = ids.resolve_user("sms", "+15550000001", display_name="OldName")
    updated = ids.update_user(user.id, display_name="NewName")
    assert updated is not None
    assert updated.display_name == "NewName"


def test_update_timezone():
    user = ids.resolve_user("sms", "+15550000002")
    updated = ids.update_user(user.id, timezone="America/New_York")
    assert updated is not None
    assert updated.timezone == "America/New_York"


def test_update_user_not_found():
    assert ids.update_user("nonexistent") is None


def test_update_ignores_unknown_fields():
    user = ids.resolve_user("sms", "+15550000003")
    updated = ids.update_user(user.id, display_name="X", unknown_field="ignored")
    assert updated is not None
    assert updated.display_name == "X"


# ---------------------------------------------------------------------------
# link_identity
# ---------------------------------------------------------------------------

def test_link_identity():
    user = ids.resolve_user("sms", "+15550000004")
    ok = ids.link_identity(user.id, "discord", "DISC123")
    assert ok is True

    # Should now be findable by discord identity
    found = ids.get_user_by_identity("discord", "DISC123")
    assert found is not None
    assert found.id == user.id


def test_link_identity_already_linked_to_same_user():
    user = ids.resolve_user("sms", "+15550000005")
    ids.link_identity(user.id, "discord", "DISC456")
    ok = ids.link_identity(user.id, "discord", "DISC456")
    assert ok is True  # idempotent


def test_link_identity_conflict():
    u1 = ids.resolve_user("sms", "+1111")
    u2 = ids.resolve_user("sms", "+2222")
    ids.link_identity(u1.id, "discord", "DISC789")
    ok = ids.link_identity(u2.id, "discord", "DISC789")
    assert ok is False  # already linked to u1


# ---------------------------------------------------------------------------
# get_identities
# ---------------------------------------------------------------------------

def test_get_identities():
    user = ids.resolve_user("sms", "+15550000006")
    ids.link_identity(user.id, "discord", "D100")
    identities = ids.get_identities(user.id)
    providers = {i["provider"] for i in identities}
    assert providers == {"sms", "discord"}


# ---------------------------------------------------------------------------
# archive_workspace
# ---------------------------------------------------------------------------

def test_archive_workspace_no_workspace():
    user = ids.resolve_user("sms", "+15550000007")
    # No workspace directory exists — should return None.
    result = ids.archive_workspace(user.id)
    assert result is None


def test_archive_workspace_creates_zip(tmp_path, monkeypatch):
    """archive_workspace should zip the workspace and re-init."""
    monkeypatch.setattr(ids.settings, "workspace_dir", str(tmp_path))

    user = ids.resolve_user("sms", "+15550000008")
    ws = tmp_path / user.workspace_dir
    ws.mkdir(parents=True)
    (ws / "test.txt").write_text("hello")

    # Mock ensure_workspace to avoid git init in tests.
    monkeypatch.setattr(
        "prax.services.workspace_service.ensure_workspace",
        lambda uid: str(ws),
    )

    result = ids.archive_workspace(user.id)
    assert result is not None
    assert result.endswith(".zip")
    assert os.path.isfile(result)


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
