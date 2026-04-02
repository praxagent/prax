"""Tests for sandbox_tools LangChain wrappers."""
import importlib

from prax.agent.user_context import current_user_id


def test_sandbox_start(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.sandbox_tools"))
    svc = importlib.import_module("prax.services.sandbox_service")

    monkeypatch.setattr(
        svc, "start_session",
        lambda uid, task, model=None: {"session_id": "abc-123-456", "status": "running", "model": model or "anthropic/claude-test"},
    )
    current_user_id.set("+10000000000")

    result = module.sandbox_start.invoke({"task_description": "Build a calculator"})
    assert "abc-123-456" in result
    assert "running" in result.lower() or "started" in result.lower()


def test_sandbox_start_error(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.sandbox_tools"))
    svc = importlib.import_module("prax.services.sandbox_service")

    monkeypatch.setattr(svc, "start_session", lambda uid, task, model=None: {"error": "Docker not available"})
    current_user_id.set("+10000000000")

    result = module.sandbox_start.invoke({"task_description": "Task"})
    assert "Failed" in result


def test_sandbox_message(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.sandbox_tools"))
    svc = importlib.import_module("prax.services.sandbox_service")

    monkeypatch.setattr(
        svc, "send_message",
        lambda uid, msg, model=None, session_id=None: {"session_id": "abc", "model": "anthropic/test", "response": {"content": "Done"}},
    )
    current_user_id.set("+10000000000")

    result = module.sandbox_message.invoke({"message": "Add tests"})
    assert "Done" in result


def test_sandbox_message_with_model_switch(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.sandbox_tools"))
    svc = importlib.import_module("prax.services.sandbox_service")

    monkeypatch.setattr(
        svc, "send_message",
        lambda uid, msg, model=None, session_id=None: {"session_id": "abc", "model": model or "default", "response": "switched"},
    )
    current_user_id.set("+10000000000")

    result = module.sandbox_message.invoke({"message": "Try again", "model": "openai/gpt-5"})
    assert "openai/gpt-5" in result


def test_sandbox_review(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.sandbox_tools"))
    svc = importlib.import_module("prax.services.sandbox_service")

    monkeypatch.setattr(
        svc, "review_session",
        lambda uid, session_id=None: {
            "session_id": "abc-123-456",
            "status": "running",
            "model": "anthropic/test",
            "elapsed_seconds": 42,
            "timeout_seconds": 1800,
            "files": ["main.py", "test.py"],
            "opencode_state": {},
        },
    )
    current_user_id.set("+10000000000")

    result = module.sandbox_review.invoke({})
    assert "main.py" in result
    assert "running" in result.lower()


def test_sandbox_finish(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.sandbox_tools"))
    svc = importlib.import_module("prax.services.sandbox_service")

    monkeypatch.setattr(
        svc, "finish_session",
        lambda uid, summary="", session_id=None: {"session_id": "abc", "status": "finished", "archived_path": "/archive/code/abc"},
    )
    current_user_id.set("+10000000000")

    result = module.sandbox_finish.invoke({"summary": "Built a calculator"})
    assert "finished" in result.lower() or "archived" in result.lower()


def test_sandbox_abort(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.sandbox_tools"))
    svc = importlib.import_module("prax.services.sandbox_service")

    monkeypatch.setattr(svc, "abort_session", lambda uid, session_id=None: {"session_id": "abc", "status": "aborted"})
    current_user_id.set("+10000000000")

    result = module.sandbox_abort.invoke({})
    assert "aborted" in result.lower()


def test_sandbox_search(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.sandbox_tools"))
    svc = importlib.import_module("prax.services.sandbox_service")

    monkeypatch.setattr(
        svc, "search_solutions",
        lambda uid, q: [{"session_id": "abc123", "path": "/p", "snippet": "beamer presentation"}],
    )
    current_user_id.set("+10000000000")

    result = module.sandbox_search.invoke({"query": "beamer"})
    assert "beamer" in result


def test_sandbox_search_no_results(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.sandbox_tools"))
    svc = importlib.import_module("prax.services.sandbox_service")

    monkeypatch.setattr(svc, "search_solutions", lambda uid, q: [])
    current_user_id.set("+10000000000")

    result = module.sandbox_search.invoke({"query": "nothing"})
    assert "No archived" in result


def test_sandbox_execute(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.sandbox_tools"))
    svc = importlib.import_module("prax.services.sandbox_service")

    monkeypatch.setattr(
        svc, "execute_solution",
        lambda uid, sid, command=None: {"session_id": "new-session", "status": "running", "model": "anthropic/test"},
    )
    current_user_id.set("+10000000000")

    result = module.sandbox_execute.invoke({"solution_id": "abc123"})
    assert "Re-executing" in result
