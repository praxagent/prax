"""Tests for prax.services.task_runner_service + task_runner_tools.

The service spawns a synthetic ConversationAgent turn per pickup, so
the tests mock that boundary rather than running a real LLM. The
contract under test:

- When task_runner_enabled=False, nothing is picked up.
- Paused users skip pickup.
- in_flight flag prevents concurrent pickups for one user.
- Kanban pickup: leftmost non-done column, assignees contains 'prax',
  comment on start, comment on complete, task moved to done.
- Top-level todo pickup: assignee=='prax', completed on success.
- pause/resume state persists via yaml file.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from prax.services import library_service, library_tasks, workspace_service


@pytest.fixture
def runner_env(tmp_path, monkeypatch):
    """Isolated user workspace + fake ConversationAgent/reply."""
    user_id = "test_user"
    ws = tmp_path / user_id
    ws.mkdir()
    monkeypatch.setattr(library_service, "workspace_root", lambda _uid: str(ws))
    monkeypatch.setattr(workspace_service, "_workspace_root", lambda _uid: str(ws))

    from prax.settings import settings
    monkeypatch.setattr(settings, "task_runner_enabled", True)

    # Reset in-memory runner state between tests.
    from prax.services import task_runner_service
    task_runner_service._state.clear()

    # Mock the synthetic turn.
    fake_svc = MagicMock()
    fake_svc.reply = MagicMock(return_value="task done, all good")
    fake_svc_class = MagicMock(return_value=fake_svc)
    fake_agent_class = MagicMock()

    def _patch_imports():
        import sys
        import types
        # Stub modules to avoid booting the real agent stack.
        orch_mod = types.ModuleType("prax.agent.orchestrator")
        orch_mod.ConversationAgent = fake_agent_class
        conv_mod = types.ModuleType("prax.services.conversation_service")
        conv_mod.ConversationService = fake_svc_class
        sys.modules["prax.agent.orchestrator"] = orch_mod
        # conversation_service is already imported elsewhere; use setattr
        # only for ConversationService so other code keeps working.
        import prax.services.conversation_service as real_conv
        monkeypatch.setattr(real_conv, "ConversationService", fake_svc_class)

    _patch_imports()
    return {
        "user_id": user_id,
        "ws": ws,
        "fake_svc": fake_svc,
        "settings": settings,
    }


class TestDisabledAndPausedGates:
    def test_disabled_runner_does_nothing(self, runner_env, monkeypatch):
        from prax.services import task_runner_service
        monkeypatch.setattr(runner_env["settings"], "task_runner_enabled", False)
        workspace_service.add_todo(
            runner_env["user_id"], "do something", assignee="prax",
        )
        task_runner_service._poll_once(runner_env["user_id"])
        runner_env["fake_svc"].reply.assert_not_called()

    def test_paused_user_does_nothing(self, runner_env):
        from prax.services import task_runner_service
        workspace_service.add_todo(
            runner_env["user_id"], "do something", assignee="prax",
        )
        task_runner_service.pause(runner_env["user_id"])
        task_runner_service._poll_once(runner_env["user_id"])
        runner_env["fake_svc"].reply.assert_not_called()

    def test_pause_persists_across_state_clear(self, runner_env):
        from prax.services import task_runner_service
        task_runner_service.pause(runner_env["user_id"])
        # Simulate restart by dropping in-memory state.
        task_runner_service._state.clear()
        st = task_runner_service.status(runner_env["user_id"])
        assert st["paused"] is True


class TestTodoPickup:
    def test_picks_up_prax_assigned_todo_and_completes_it(self, runner_env):
        from prax.services import task_runner_service
        uid = runner_env["user_id"]
        workspace_service.add_todo(uid, "research X", assignee="user")
        workspace_service.add_todo(uid, "draft section on Y", assignee="prax")
        task_runner_service._poll_once(uid)
        runner_env["fake_svc"].reply.assert_called_once()
        # Todo 2 (prax-assigned) should be marked complete.
        todos = workspace_service.list_todos(uid, show_completed=True)
        prax_todo = next(t for t in todos if t["task"] == "draft section on Y")
        assert prax_todo["done"] is True

    def test_user_assigned_todo_is_ignored(self, runner_env):
        from prax.services import task_runner_service
        uid = runner_env["user_id"]
        workspace_service.add_todo(uid, "just a user todo", assignee="user")
        task_runner_service._poll_once(uid)
        runner_env["fake_svc"].reply.assert_not_called()

    def test_default_assignee_is_user(self, runner_env):
        from prax.services import task_runner_service
        uid = runner_env["user_id"]
        workspace_service.add_todo(uid, "no assignee specified")
        task_runner_service._poll_once(uid)
        # Default "user" → not picked up.
        runner_env["fake_svc"].reply.assert_not_called()


class TestKanbanPickup:
    def test_picks_up_prax_assigned_kanban_task(self, runner_env):
        from prax.services import task_runner_service
        uid = runner_env["user_id"]
        library_service.create_space(uid, "Demo")
        library_tasks.create_task(
            uid, "demo",
            title="wire the thing", description="auto-picked",
            assignees=["prax"],
        )
        task_runner_service._poll_once(uid)
        runner_env["fake_svc"].reply.assert_called_once()
        # The picked-up task should now be in the done column.
        done_tasks = library_tasks.list_tasks(uid, "demo", column="done")
        titles = [t["title"] for t in done_tasks]
        assert "wire the thing" in titles

    def test_unassigned_kanban_task_ignored(self, runner_env):
        from prax.services import task_runner_service
        uid = runner_env["user_id"]
        library_service.create_space(uid, "Demo")
        library_tasks.create_task(uid, "demo", title="user-owned work")
        task_runner_service._poll_once(uid)
        runner_env["fake_svc"].reply.assert_not_called()

    def test_kanban_task_in_done_is_skipped(self, runner_env):
        from prax.services import task_runner_service
        uid = runner_env["user_id"]
        library_service.create_space(uid, "Demo")
        created = library_tasks.create_task(
            uid, "demo", title="already finished",
            assignees=["prax"], column="done",
        )
        assert created["task"]["column"] == "done"
        task_runner_service._poll_once(uid)
        runner_env["fake_svc"].reply.assert_not_called()


class TestConcurrency:
    def test_in_flight_flag_blocks_second_tick(self, runner_env, monkeypatch):
        from prax.services import task_runner_service
        uid = runner_env["user_id"]
        workspace_service.add_todo(uid, "task A", assignee="prax")

        # Inject in_flight=True to simulate a concurrent turn.
        st = task_runner_service._get_state(uid)
        st.in_flight = True
        task_runner_service._poll_once(uid)
        runner_env["fake_svc"].reply.assert_not_called()


class TestToolWrappers:
    def test_status_tool_reports_disabled(self, runner_env, monkeypatch):
        import importlib
        monkeypatch.setattr(runner_env["settings"], "task_runner_enabled", False)
        from prax.agent.user_context import current_user_id
        current_user_id.set(runner_env["user_id"])
        module = importlib.reload(
            importlib.import_module("prax.agent.task_runner_tools")
        )
        result = module.task_runner_status.invoke({})
        assert "disabled" in result.lower()

    def test_pause_and_resume_roundtrip(self, runner_env):
        import importlib

        from prax.agent.user_context import current_user_id
        current_user_id.set(runner_env["user_id"])
        module = importlib.reload(
            importlib.import_module("prax.agent.task_runner_tools")
        )
        r1 = module.task_runner_pause.invoke({})
        assert "paused" in r1.lower()
        r2 = module.task_runner_resume.invoke({})
        assert "resumed" in r2.lower()
