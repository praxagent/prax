"""The record of what Prax did lives outside the workspace the sandbox mounts.

arXiv 2609.30266 ("LLM Agents Can Easily Tamper With Their Own Traces"):
agents delete or rewrite their session logs when asked, when a planted
instruction tells them to, and unprompted when it raises their reward. Prax's
records were inside the workspace directory, which the sandbox mounts
read-write (production mounts every user's workspace), so one sandbox_shell
call reached them. They live in RECORDS_DIR now: by default records/ beside
the workspace directory, never inside it.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path

import pytest

from prax.services import records

USER = "usr_records"


@pytest.fixture
def layout(tmp_path, monkeypatch):
    """The default layout: workspaces/ and, beside it, records/."""
    import prax.settings
    ws = tmp_path / "workspaces"
    ws.mkdir()
    monkeypatch.setattr(prax.settings.settings, "workspace_dir", str(ws))
    monkeypatch.setattr(prax.settings.settings, "records_dir", "")
    monkeypatch.setattr("prax.services.workspace_service.settings", prax.settings.settings, raising=False)
    records._reset_for_tests()
    root = ws / USER
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    return ws


def _under(path: Path, base: Path) -> bool:
    path, base = path.resolve(), base.resolve()
    return path == base or base in path.parents


def test_records_default_to_beside_the_workspace_dir(layout):
    root = records.records_root()
    assert root == (layout.parent / "records").resolve()
    assert not _under(root, layout)


def test_a_records_dir_inside_the_workspace_is_called_out(layout, monkeypatch, caplog):
    import prax.settings
    monkeypatch.setattr(prax.settings.settings, "records_dir", str(layout / "records"))
    with caplog.at_level(logging.WARNING, logger="prax.services.records"):
        records.records_root()
    assert "the sandbox mounts" in caplog.text


def test_nothing_a_turn_records_lands_in_the_workspace(layout):
    """Every kind of record, written the way Prax writes it, ends up outside
    the workspace directory, and nothing is left behind inside it."""
    from prax.agent import trace as trace_mod
    from prax.services import feedback_service, parked_approvals, trajectory_service, workspace_service

    workspace_service.append_trace(USER, [{"type": "user", "content": "hello"}])
    (trace_mod._graphs_dir() / "graphs-2026-10-06.jsonl").write_text("{}\n")
    feedback_service.submit_feedback(USER, "positive", trace_id="t1", message_content="ok")
    trajectory_service.export_trajectory(USER, "hi", "hello there", [])
    parked_approvals._save([{"id": "x"}])

    written = [p for p in records.records_root().rglob("*") if p.is_file()]
    names = {p.name for p in written}
    assert {"trace.log", "graphs-2026-10-06.jsonl", "feedback.jsonl", "parked_approvals.json"} <= names
    assert any(p.parent.name == "trajectories" for p in written)
    leftovers = [p for p in layout.rglob("*") if p.is_file() and ".git" not in p.parts]
    assert leftovers == [], leftovers


def test_records_in_their_old_places_move_once(layout):
    from prax.agent import trace as trace_mod
    from prax.services import feedback_service, parked_approvals, trajectory_service

    old = layout / ".prax"
    (old / "graphs").mkdir(parents=True)
    (old / "graphs" / "graphs-2026-09-01.jsonl").write_text('{"trace_id": "old"}\n')
    (old / "feedback").mkdir()
    (old / "feedback" / "feedback.jsonl").write_text("{}\n")
    (old / "parked_approvals.json").write_text(json.dumps([{"id": "parked-before"}]))
    traj = layout / USER / ".prax" / "trajectories"
    traj.mkdir(parents=True)
    (traj / "completed.jsonl").write_text("{}\n")

    assert (trace_mod._graphs_dir() / "graphs-2026-09-01.jsonl").read_text() == '{"trace_id": "old"}\n'
    assert (feedback_service._feedback_dir() / "feedback.jsonl").exists()
    assert parked_approvals._load() == [{"id": "parked-before"}]
    assert (trajectory_service._trajectories_dir(USER) / "completed.jsonl").exists()
    assert not (old / "graphs").exists() and not (old / "feedback").exists()
    assert not (old / "parked_approvals.json").exists() and not traj.exists()


def test_a_move_never_overwrites(layout):
    """Something already in the records wins; the old copy stays where it was."""
    (layout / ".prax").mkdir()
    (layout / ".prax" / "parked_approvals.json").write_text("[]")
    target = records.records_root() / "parked_approvals.json"
    target.write_text(json.dumps([{"id": "current"}]))
    from prax.services import parked_approvals
    assert parked_approvals._load() == [{"id": "current"}]
    assert (layout / ".prax" / "parked_approvals.json").exists()


def test_identities_sharing_a_workspace_share_one_record(layout):
    """A phone number and a TeamWork id symlinked to one workspace kept one
    trace log; keyed by the workspace's real name, they still do."""
    os.symlink(layout / USER, layout / "usr_alias")
    assert records.user_dir(USER) == records.user_dir("usr_alias")
