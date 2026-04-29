import os
import sqlite3

from prax.conversation_memory import init_database
from prax.services import state_paths, workspace_service


def test_default_conversation_db_lives_under_user_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_service.settings, "workspace_dir", str(tmp_path / "workspaces"))
    monkeypatch.setattr(state_paths.settings, "workspace_dir", str(tmp_path / "workspaces"))
    monkeypatch.setattr(state_paths.settings, "prax_user_id", "solo-user")
    monkeypatch.setattr(state_paths.settings, "database_name", "conversations.db")

    path = state_paths.ensure_conversation_db()

    assert path.endswith("solo-user/.services/prax/conversations.db")
    assert os.path.exists(path)


def test_explicit_database_name_is_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr(state_paths.settings, "prax_user_id", "solo-user")
    explicit = str(tmp_path / "explicit.db")

    path = state_paths.ensure_conversation_db(database_name=explicit)

    assert path == explicit
    assert os.path.exists(path)


def test_legacy_conversations_db_migrates_once(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(workspace_service.settings, "workspace_dir", str(tmp_path / "workspaces"))
    monkeypatch.setattr(state_paths.settings, "workspace_dir", str(tmp_path / "workspaces"))
    monkeypatch.setattr(state_paths.settings, "prax_user_id", "solo-user")
    monkeypatch.setattr(state_paths.settings, "database_name", "conversations.db")

    init_database("conversations.db")
    conn = sqlite3.connect("conversations.db")
    conn.execute("INSERT INTO conversations (id, data) VALUES (?, ?)", (123, '[{"role":"user","content":"hi"}]'))
    conn.commit()
    conn.close()

    target = state_paths.ensure_conversation_db()

    conn = sqlite3.connect(target)
    try:
        row = conn.execute("SELECT data FROM conversations WHERE id = ?", (123,)).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert "hi" in row[0]
    assert os.path.exists(os.path.join(os.path.dirname(target), ".conversations_migrated"))
