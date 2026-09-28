"""Files Prax posts to TeamWork chat link to where they really are.

Two ways the link used to 404: TeamWork's project pointed at the phone-number
directory while Prax wrote to the user's ``usr_*`` workspace, and the link was
built as ``active/<name the model passed>`` whatever the file's real location.
"""
from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

import prax.agent.workspace_tools as wt
import prax.blueprints.teamwork_routes as tr
from prax.agent.user_context import current_channel_id


@pytest.fixture
def posted(monkeypatch, tmp_path):
    sent = []
    fake = SimpleNamespace(
        enabled=True, project_id="p-1",
        send_message=lambda **kw: sent.append(kw))
    monkeypatch.setattr("prax.services.teamwork_service.get_teamwork_client", lambda: fake)

    def no_discord(*a, **k):
        raise RuntimeError("discord not running")
    monkeypatch.setattr("prax.services.discord_service.send_file", no_discord)
    monkeypatch.setattr(wt.workspace_service, "_workspace_root", lambda uid: str(tmp_path))
    monkeypatch.setattr(wt, "_get_user_id", lambda: "u1")
    token = current_channel_id.set("chan-1")
    yield sent
    current_channel_id.reset(token)


def _link(sent):
    (att,) = sent[-1]["extra_data"]["attachments"]
    url = urlsplit(att["url"])
    return att, url.path, parse_qs(url.query)["path"][0]


def test_file_in_active_links_to_active(posted, tmp_path):
    (tmp_path / "active").mkdir()
    (tmp_path / "active" / "m.png").write_bytes(b"x")
    wt.workspace_send_file.func("m.png")
    att, path, rel = _link(posted)
    assert path == "/api/workspace/p-1/download" and rel == "active/m.png"
    assert att["content_type"] == "image/png" and att["name"] == "m.png"


def test_file_at_workspace_root_links_to_the_root(posted, tmp_path):
    # The sandbox wrote /workspace/m.png; the old link said active/m.png.
    (tmp_path / "m.png").write_bytes(b"x")
    wt.workspace_send_file.func("/workspace/m.png")
    assert _link(posted)[2] == "m.png"


def test_awkward_names_are_url_encoded(posted, tmp_path):
    (tmp_path / "active").mkdir()
    (tmp_path / "active" / "a b&c.png").write_bytes(b"x")
    wt.workspace_send_file.func("a b&c.png")
    att, _, rel = _link(posted)
    assert rel == "active/a b&c.png" and "&c" not in att["url"]


def test_teamwork_project_uses_the_users_real_workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(tr, "_get_teamwork_user_id", lambda: "uuid-1")
    monkeypatch.setattr("prax.services.workspace_service.workspace_root",
                        lambda uid: str(tmp_path / "usr_90c2b48f"))
    assert tr.teamwork_workspace_dir() == "usr_90c2b48f"


def test_an_unresolvable_user_registers_no_directory(monkeypatch):
    def boom():
        raise RuntimeError("identity db unavailable")
    monkeypatch.setattr(tr, "_get_teamwork_user_id", boom)
    assert tr.teamwork_workspace_dir() is None
