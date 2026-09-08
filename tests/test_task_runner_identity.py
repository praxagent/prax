"""Task runner: run as the real user, and never mark a failed turn Completed.

Two review findings:

* ``scheduler_service._load_all_users`` resolved the workspace directory name
  to the canonical user id for schedules (the 09-02 fix) but, four lines later,
  still registered the task runner with the raw directory name — so its
  synthetic turns ran as a user that does not exist.
* ``task_runner_service`` treated any non-raising ``reply()`` as success.  The
  orchestrator swallows exceptions and timeouts into fixed apology strings, so
  "I hit an internal error…" (and an empty answer) moved the user's card to
  Done with a "Completed." comment.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import prax.services.identity_service as ids
from prax.services import library_service, library_tasks, task_runner_service, workspace_service

INTERNAL_ERROR = ("I hit an internal error while working on that request. "
                  "Error: RuntimeError: boom")
TIMEOUT = ("I hit a turn timeout while working on that request: 600s. I stopped "
           "waiting so the session doesn't stay stuck.")


# ── Registration under the canonical id ──────────────────────────────────────

@pytest.fixture
def identity_env(tmp_path, monkeypatch):
    monkeypatch.setattr(ids, "_db_path", lambda: str(tmp_path / "identity.db"))
    monkeypatch.setattr(ids, "_initialized", False)
    ids.init_identity_db()
    ws = tmp_path / "ws"
    ws.mkdir()
    from prax.services import scheduler_service as sched
    monkeypatch.setattr(sched.settings, "workspace_dir", str(ws))
    monkeypatch.setattr(sched.settings, "task_runner_enabled", True)
    monkeypatch.setattr(sched, "_scheduler", MagicMock())
    return ws, sched


def test_task_runner_is_registered_with_the_canonical_user_id(identity_env, monkeypatch):
    ws, sched = identity_env
    user = ids.resolve_user("teamwork", "default")
    (ws / user.workspace_dir).mkdir()           # the workspace dir, no schedules.yaml
    (ws / "usr_nobody").mkdir()                 # a directory no identity row owns

    registered: list[str] = []
    monkeypatch.setattr(task_runner_service, "register_user",
                        lambda _scheduler, uid: registered.append(uid))

    sched._load_all_users()

    # Old code: registered the directory NAME (usr_<id8>) for every workspace.
    assert user.id in registered
    assert user.workspace_dir not in registered
    assert "usr_nobody" in registered           # no identity → the name is all there is


# ── Completion requires a non-error answer ───────────────────────────────────

@pytest.mark.parametrize("response, why", [
    ("", "no response"),
    ("   ", "no response"),
    (None, "no response"),
    (INTERNAL_ERROR, "failed"),
    (TIMEOUT, "failed"),
])
def test_failure_reason_flags_empty_and_orchestrator_failure_text(response, why):
    reason = task_runner_service.failure_reason(response)
    assert reason and why in reason


def test_failure_reason_accepts_a_real_answer():
    assert task_runner_service.failure_reason("Done: wrote the summary to notes/x.md") is None
    # Mentioning an error is not the same as the orchestrator's failure sentinel.
    assert task_runner_service.failure_reason(
        "The build logged an internal error at step 3; I fixed it and it now passes.") is None


def test_sentinel_openers_match_the_orchestrator_source():
    """Drift guard: the runner detects the exact strings the orchestrator emits."""
    import inspect

    from prax.agent import orchestrator
    src = inspect.getsource(orchestrator)
    for opener in task_runner_service.FAILED_TURN_OPENERS:
        assert opener in src, opener


@pytest.fixture
def runner_env(tmp_path, monkeypatch):
    """One Kanban task assigned to prax, with the synthetic turn mocked."""
    user_id = "test_user"
    ws = tmp_path / user_id
    ws.mkdir()
    monkeypatch.setattr(library_service, "workspace_root", lambda _uid: str(ws))
    monkeypatch.setattr(workspace_service, "_workspace_root", lambda _uid: str(ws))
    from prax.settings import settings
    monkeypatch.setattr(settings, "task_runner_enabled", True)
    task_runner_service._state.clear()

    fake_svc = MagicMock()
    fake_svc_class = MagicMock(return_value=fake_svc)
    import prax.services.conversation_service as real_conv
    monkeypatch.setattr(real_conv, "ConversationService", fake_svc_class)
    import sys
    import types
    orch_mod = types.ModuleType("prax.agent.orchestrator")
    orch_mod.ConversationAgent = MagicMock()
    monkeypatch.setitem(sys.modules, "prax.agent.orchestrator", orch_mod)

    library_service.create_space(user_id, "Ops")
    created = library_tasks.create_task(user_id, "ops", title="Do the thing", assignees=["prax"])
    return {"user_id": user_id, "slug": "ops", "task_id": created["task"]["id"],
            "reply": fake_svc.reply}


def _task(env):
    return next(t for t in library_tasks.list_tasks(env["user_id"], env["slug"])
                if t["id"] == env["task_id"])


@pytest.mark.parametrize("response", ["", INTERNAL_ERROR, TIMEOUT])
def test_a_failed_turn_does_not_move_the_card_to_done(runner_env, response):
    runner_env["reply"].return_value = response

    task_runner_service._poll_once(runner_env["user_id"])

    task = _task(runner_env)
    # Old code: column == "done" with a "Completed." comment for all three.
    assert task["column"] != "done"
    comments = [c["text"] for c in task.get("comments", [])]
    assert any("Task runner failed" in c for c in comments), comments
    assert not any(c.startswith("Completed.") for c in comments), comments


def test_a_real_answer_still_completes_the_card(runner_env):
    runner_env["reply"].return_value = "Wrote the runbook to notes/runbook.md."

    task_runner_service._poll_once(runner_env["user_id"])

    task = _task(runner_env)
    assert task["column"] == "done"
