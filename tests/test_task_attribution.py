"""Who a board card says did the work.

For a long time `author` was validated against ("human", "prax"), so a card
filed by an external agent — a coding agent over MCP, say — had to be labelled
as the user or as Prax. Both are false, and attributing an agent's work to the
person reading the board is the specific failure worth naming: the board's whole
value is that you can tell at a glance who put something there.
"""
from __future__ import annotations

import pytest
import yaml

from prax.services import library_tasks


@pytest.fixture
def space(tmp_path, monkeypatch):
    root = tmp_path / "library" / "spaces" / "work"
    root.mkdir(parents=True)
    monkeypatch.setattr(library_tasks, "_project_dir",
                        lambda uid, proj: root, raising=False)
    monkeypatch.setattr(library_tasks, "_tasks_path",
                        lambda uid, proj: root / ".tasks.yaml", raising=False)
    (root / ".tasks.yaml").write_text(yaml.safe_dump({
        "columns": [{"id": "todo", "name": "To Do"},
                    {"id": "doing", "name": "Doing"},
                    {"id": "done", "name": "Done"}],
        "tasks": [],
    }), encoding="utf-8")
    return {"user": "u1", "project": "work"}


def test_an_external_agent_can_be_named_as_the_author(space):
    res = library_tasks.create_task(
        space["user"], space["project"], title="Ship it", author="Claude Code")
    assert "error" not in res
    assert res["task"]["author"] == "Claude Code"
    assert res["task"]["activity"][0]["actor"] == "Claude Code"


def test_human_and_prax_still_work(space):
    for who in ("human", "prax"):
        res = library_tasks.create_task(
            space["user"], space["project"], title=f"by {who}", author=who)
        assert res["task"]["author"] == who


def test_an_empty_author_is_refused_rather_than_defaulted(space):
    """Silently falling back to "human" is how the wrong name got on the card."""
    res = library_tasks.create_task(
        space["user"], space["project"], title="x", author="   ")
    assert "error" in res


def test_a_label_is_bounded_and_printable(space):
    # It is written by a caller and rendered in a UI.
    res = library_tasks.create_task(
        space["user"], space["project"], title="x", author="A" * 200)
    assert len(res["task"]["author"]) <= 40

    res2 = library_tasks.create_task(
        space["user"], space["project"], title="y", author="Claude\x00\x07 Code")
    assert res2["task"]["author"] == "Claude Code"


def test_a_move_records_the_agent_that_moved_it(space):
    created = library_tasks.create_task(
        space["user"], space["project"], title="x", author="Claude Code")
    tid = created["task"]["id"]

    moved = library_tasks.move_task(
        space["user"], space["project"], tid, "doing", editor="Claude Code")
    assert moved["status"] == "moved"
    assert moved["task"]["activity"][-1] == {
        **moved["task"]["activity"][-1],
        "actor": "Claude Code", "action": "moved", "from": "todo", "to": "doing"}
