"""First-boot orphaning (review finding, reproduced 2026-09-05).

The sequence that lost session one on a fresh single-user deployment:

1. boot: ``ensure_conversation_db()`` ran BEFORE the identity DB existed and
   derived service state for ``PRAX_USER_ID`` as if the directory name were a
   user id → ``workspaces/<PRAX_USER_ID>/`` was created for nobody;
2. first message: TeamWork resolved the real user, minted under ``usr_<id8>``;
   notes and history went there;
3. next boot: ``reconcile_workspace_dir`` repointed the user at
   ``<PRAX_USER_ID>`` and skipped the symlink because that directory already
   existed → everything from session one became invisible.

These tests pin the fix from three sides: the boot flow end to end, the boot
ORDER in app.py, and the merge/refuse behaviour of the reconcile step.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

import prax.services.identity_service as ids
from prax.conversation_memory import add_dict_to_list, retrieve_dict
from prax.services import state_paths, workspace_service

PRIMARY = "usr_primary"


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A fresh identity DB and workspace root; PRAX_USER_ID set, no phone."""
    db = tmp_path / "identity.db"
    monkeypatch.setattr(ids, "_db_path", lambda: str(db))
    monkeypatch.setattr(ids, "_initialized", False)
    ws = tmp_path / "workspaces"
    ws.mkdir()
    # conftest patches every prax module's `settings` to one object, so setting
    # it once here reaches identity_service, state_paths and workspace_service.
    monkeypatch.setattr(ids.settings, "workspace_dir", str(ws))
    monkeypatch.setattr(ids.settings, "prax_user_id", PRIMARY)
    monkeypatch.setattr(ids.settings, "teamwork_user_phone", "")
    monkeypatch.setattr(ids.settings, "database_name", "conversations.db")
    return ws


def _boot() -> str:
    """The identity-relevant part of create_app(), in the FIXED order."""
    ids.init_identity_db()
    ids.reconcile_workspace_dir()
    return state_paths.ensure_conversation_db()


def _first_message_user() -> ids.User:
    """Resolve the user exactly as the TeamWork webhook does on the first message."""
    return ids.resolve_user(*ids.primary_user_identity())


# ── The incident, end to end ──────────────────────────────────────────────────

def test_session_one_note_and_history_survive_a_restart(env):
    _boot()

    # Session one: first message → user resolved → a note and a turn are written.
    user = _first_message_user()
    root = Path(workspace_service.ensure_workspace(user.id))
    (root / "active" / "session-one.md").write_text("remember me", encoding="utf-8")
    key = 4242
    add_dict_to_list(state_paths.ensure_conversation_db(user.id), key,
                     {"role": "user", "content": "hello from session one"})

    # Restart.
    _boot()

    again = _first_message_user()
    assert again.id == user.id
    root2 = Path(workspace_service.workspace_root(again.id))
    # Old code: the row was repointed at workspaces/usr_primary (created empty
    # at boot one), so the note written under usr_<id8> was not here.
    assert (root2 / "active" / "session-one.md").read_text(encoding="utf-8") == "remember me"
    history = retrieve_dict(state_paths.ensure_conversation_db(again.id), key)
    assert history and history[-1]["content"] == "hello from session one"
    # And it all lives under the directory PRAX_USER_ID names (the sandbox mount).
    assert root2.resolve() == (env / PRIMARY).resolve()


def test_boot_creates_the_primary_user_before_service_state_is_derived(env):
    """After boot the user exists, owns PRAX_USER_ID, and service state resolves to it."""
    path = _boot()
    user = ids.get_user_by_workspace(PRIMARY)
    assert user is not None
    assert state_paths._effective_user_id() == user.id  # not the raw directory name
    assert Path(path).resolve() == (env / PRIMARY / ".services" / "prax" / "conversations.db").resolve()


def test_effective_user_id_falls_back_to_the_slug_when_nobody_owns_it(env):
    ids.init_identity_db()
    assert state_paths._effective_user_id() == PRIMARY


def test_app_boots_identity_before_conversation_db():
    """Drift guard on app.py: reconcile must run before the first ensure_conversation_db()."""
    src = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    body = src[src.index("def create_app"):]
    assert body.index("reconcile_workspace_dir()") < body.index("ensure_conversation_db(")


# ── reconcile: merge or refuse, never silently repoint ──────────────────────

def _split_user(env: Path, old_name: str = "usr_old") -> ids.User:
    """A user whose row points at *old_name* (the pre-fix state after session one)."""
    ids.init_identity_db()
    user = _first_message_user()
    conn = ids._connect()
    conn.execute("UPDATE users SET workspace_dir = ? WHERE id = ?", (old_name, user.id))
    conn.commit()
    conn.close()
    return ids.get_user(user.id)


def test_existing_new_dir_is_merged_into_not_skipped(env):
    user = _split_user(env)
    old = env / "usr_old"
    (old / "active").mkdir(parents=True)
    (old / "active" / "note.md").write_text("data", encoding="utf-8")
    (old / "top.txt").write_text("t", encoding="utf-8")
    new = env / PRIMARY
    (new / "active").mkdir(parents=True)  # the empty skeleton boot one left behind

    ids.reconcile_workspace_dir()

    assert ids.get_user(user.id).workspace_dir == PRIMARY
    assert (new / "active" / "note.md").read_text(encoding="utf-8") == "data"
    assert (new / "top.txt").read_text(encoding="utf-8") == "t"
    # The old name keeps working (stale references, the scheduler's dir scan).
    assert old.is_symlink() and old.resolve() == new.resolve()


def test_colliding_files_refuse_loudly_and_leave_everything_in_place(env, caplog):
    user = _split_user(env)
    old = env / "usr_old"
    old.mkdir()
    (old / "notes.md").write_text("session one", encoding="utf-8")
    new = env / PRIMARY
    new.mkdir()
    (new / "notes.md").write_text("something else", encoding="utf-8")

    with caplog.at_level(logging.ERROR, logger="prax.services.identity_service"):
        ids.reconcile_workspace_dir()

    # Old code: repointed the row and skipped the symlink → session one invisible.
    assert ids.get_user(user.id).workspace_dir == "usr_old"
    assert (old / "notes.md").read_text(encoding="utf-8") == "session one"
    assert (new / "notes.md").read_text(encoding="utf-8") == "something else"
    assert any("NOT reconciling" in r.getMessage() and "notes.md" in r.getMessage()
               for r in caplog.records)


def test_old_name_that_is_a_symlink_to_the_new_dir_just_repoints(env):
    """TJ's layout: usr_* entries are symlinks to one canonical directory."""
    user = _split_user(env, old_name="usr_link")
    new = env / PRIMARY
    new.mkdir()
    (new / "keep.md").write_text("k", encoding="utf-8")
    (env / "usr_link").symlink_to(new)

    ids.reconcile_workspace_dir()

    assert ids.get_user(user.id).workspace_dir == PRIMARY
    assert (new / "keep.md").read_text(encoding="utf-8") == "k"
    assert (env / "usr_link").is_symlink()


def test_missing_new_dir_keeps_the_symlink_behaviour(env):
    user = _split_user(env)
    old = env / "usr_old"
    old.mkdir()
    (old / "a.md").write_text("a", encoding="utf-8")

    ids.reconcile_workspace_dir()

    new = env / PRIMARY
    assert ids.get_user(user.id).workspace_dir == PRIMARY
    assert new.is_symlink() and (new / "a.md").read_text(encoding="utf-8") == "a"


def test_no_prax_user_id_means_no_primary_user(env, monkeypatch):
    monkeypatch.setattr(ids.settings, "prax_user_id", "")
    ids.init_identity_db()
    assert ids.ensure_primary_user() is None
    assert ids.list_users() == []
