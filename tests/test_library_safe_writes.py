"""Safe Library writes: a commit per write, a conflict instead of a silent
overwrite, a trash you can restore from, and per-note history.

Before: library writes were never committed (they were swept into whatever
unrelated commit ran next), the last save always won, and deletes were final.
"""
from __future__ import annotations

import subprocess
import threading

import pytest
from flask import Flask

from prax.services import library_history, library_service

USER = "usr_safe_writes"


def _git(ws, *args):
    return subprocess.run(["git", *args], cwd=ws, capture_output=True, text=True).stdout


@pytest.fixture
def ws(tmp_path, monkeypatch):
    root = tmp_path / USER
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                    "--allow-empty", "-m", "init"], cwd=root, check=True)
    monkeypatch.setattr(library_service, "workspace_root", lambda _uid: str(root))
    library_service.create_space(USER, "Linear Algebra")
    library_service.create_notebook(USER, "linear-algebra", "Lectures")
    return root


def _note(author="human", content="first draft"):
    out = library_service.create_note(USER, title="Eigenvalues", content=content,
                                      project="linear-algebra", notebook="lectures", author=author)
    return out["note"]


# --- a commit per write ----------------------------------------------------------

def test_every_write_is_a_commit_saying_who_and_what(ws):
    _note()
    library_service.update_note(USER, "linear-algebra", "lectures", "eigenvalues",
                                content="second draft", editor="prax", override_permission=True)
    log = _git(ws, "log", "--format=%an|%s")
    assert "Prax|library: prax edited linear-algebra/lectures/eigenvalues" in log
    assert "library: human created note linear-algebra/lectures" in log


def test_only_the_library_is_committed(ws):
    (ws / "active").mkdir()
    (ws / "active" / "unrelated.txt").write_text("not the library's")
    _note()
    assert "unrelated.txt" in _git(ws, "status", "--porcelain", "-uall")   # still uncommitted
    assert "unrelated.txt" not in _git(ws, "log", "--name-only", "--format=")


def test_a_write_made_of_writes_is_one_commit(ws):
    _note()
    before = len(_git(ws, "log", "--format=%h").split())
    library_service.delete_space(USER, "linear-algebra", archive_notes=True)   # archives, then trashes
    after = len(_git(ws, "log", "--format=%h").split())
    assert after == before + 1


def test_tell_teamwork_names_only(ws, monkeypatch):
    seen = []
    done = threading.Event()

    class TW:
        enabled = True

        def notify_library_changed(self, payload):
            seen.append(payload)
            done.set()
    monkeypatch.setattr("prax.services.teamwork_service.get_teamwork_client", lambda: TW())
    _note(content="secret body text")
    assert done.wait(3)
    assert seen[0]["space"] == "linear-algebra" and seen[0]["action"] == "created note"
    assert "secret body text" not in str(seen)


# --- no silent overwrite -----------------------------------------------------------

def test_a_stale_save_is_a_conflict_not_an_overwrite(ws):
    opened = _note()["updated_at"]
    library_service.update_note(USER, "linear-algebra", "lectures", "eigenvalues",
                                content="Prax's edit", editor="human")
    out = library_service.update_note(USER, "linear-algebra", "lectures", "eigenvalues",
                                      content="my edit from an old copy", editor="human",
                                      expected_updated_at=opened)
    assert out["conflict"] is True and out["current"]["content"].strip() == "Prax's edit"
    now = library_service.get_note(USER, "linear-algebra", "lectures", "eigenvalues")
    assert now["content"].strip() == "Prax's edit"


def test_an_outside_agent_cannot_edit_a_persons_note_without_permission(ws):
    _note(author="human")
    out = library_service.update_note(USER, "linear-algebra", "lectures", "eigenvalues",
                                      content="overwritten", editor="Some MCP Agent")
    assert "prax_may_edit is false" in out["error"]


# --- the trash -----------------------------------------------------------------------

def test_delete_goes_to_the_trash_and_restores(ws):
    _note()
    out = library_service.delete_note(USER, "linear-algebra", "lectures", "eigenvalues")
    assert library_service.get_note(USER, "linear-algebra", "lectures", "eigenvalues") is None
    items = library_service.trash_list(USER)
    assert items[0]["id"] == out["trash_id"] and items[0]["label"] == "Eigenvalues"
    assert library_service.trash_restore(USER, out["trash_id"])["status"] == "restored"
    assert library_service.get_note(USER, "linear-algebra", "lectures", "eigenvalues") is not None
    assert library_service.trash_list(USER) == []


def test_restore_refuses_to_clobber_or_orphan(ws):
    _note()
    first = library_service.delete_note(USER, "linear-algebra", "lectures", "eigenvalues")["trash_id"]
    _note(content="a new note with the same name")
    assert "already at" in library_service.trash_restore(USER, first)["error"]
    library_service.delete_note(USER, "linear-algebra", "lectures", "eigenvalues")
    library_service.delete_notebook(USER, "linear-algebra", "lectures")
    assert "Restore that from the trash first" in library_service.trash_restore(USER, first)["error"]


def test_purge_and_expiry(ws, monkeypatch):
    _note()
    tid = library_service.delete_note(USER, "linear-algebra", "lectures", "eigenvalues")["trash_id"]
    assert library_service.trash_purge(USER, tid)["status"] == "purged"
    assert library_service.trash_list(USER) == []
    _note()
    library_service.delete_note(USER, "linear-algebra", "lectures", "eigenvalues")
    assert library_history.purge_old(ws, days=0) == 0                 # 0 = keep forever
    monkeypatch.setattr(library_history, "_now", lambda: __import__("datetime").datetime(2099, 1, 1,
                        tzinfo=__import__("datetime").UTC))
    assert library_history.purge_old(ws, days=30) == 1


def test_a_bad_trash_id_is_refused(ws):
    assert "no trash item" in library_service.trash_restore(USER, "../../etc")["error"]


# --- history of a note ---------------------------------------------------------------

def test_history_version_diff_and_restore(ws):
    _note(content="version one")
    library_service.update_note(USER, "linear-algebra", "lectures", "eigenvalues",
                                content="version two", editor="human")
    versions = library_service.note_history(USER, "linear-algebra", "lectures", "eigenvalues")["versions"]
    assert len(versions) == 2
    old = library_service.note_version(USER, "linear-algebra", "lectures", "eigenvalues",
                                       versions[-1]["commit"])
    assert old["content"].strip() == "version one"
    assert "-version one" in old["diff"] and "+version two" in old["diff"]
    library_service.restore_note_version(USER, "linear-algebra", "lectures", "eigenvalues",
                                         versions[-1]["commit"])
    now = library_service.get_note(USER, "linear-algebra", "lectures", "eigenvalues")
    assert now["content"].strip() == "version one"
    assert len(library_service.note_history(USER, "linear-algebra", "lectures", "eigenvalues")["versions"]) == 3


def test_a_bad_commit_id_is_refused(ws):
    _note()
    assert "error" in library_service.note_version(USER, "linear-algebra", "lectures", "eigenvalues",
                                                   "HEAD; rm -rf /")


# --- the routes ------------------------------------------------------------------------

@pytest.fixture
def client(ws, monkeypatch):
    import prax.settings
    from prax.blueprints import teamwork_routes as tr
    monkeypatch.setattr(prax.settings.settings, "prax_api_key", "")
    monkeypatch.setattr(tr, "_get_teamwork_user_id", lambda: USER)
    app = Flask(__name__)
    app.register_blueprint(tr.teamwork_routes)
    return app.test_client()


def test_routes_conflict_history_trash_and_who(client, ws):
    note = _note(author="human")
    base = "/teamwork/library/notes/linear-algebra/lectures/eigenvalues"
    ok = client.patch(base, json={"content": "edited in the UI", "expected_updated_at": note["updated_at"]})
    assert ok.status_code == 200
    stale = client.patch(base, json={"content": "stale", "expected_updated_at": note["updated_at"]})
    assert stale.status_code == 409 and stale.get_json()["conflict"] is True
    assert "You (TeamWork)|library: human edited" in _git(ws, "log", "--format=%an|%s")
    versions = client.get(f"{base}/history").get_json()["versions"]
    assert client.get(f"{base}/history/{versions[-1]['commit']}").status_code == 200
    assert client.post(f"{base}/history/{versions[-1]['commit']}/restore").status_code == 200
    tid = client.delete(base).get_json()["trash_id"]
    assert client.get("/teamwork/library/trash").get_json()["items"][0]["id"] == tid
    assert client.post(f"/teamwork/library/trash/{tid}/restore").status_code == 200
