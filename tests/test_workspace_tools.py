import importlib

from prax.agent.user_context import current_user_id


def test_workspace_save(monkeypatch):
    module = importlib.reload(importlib.import_module('prax.agent.workspace_tools'))
    ws = importlib.import_module('prax.services.workspace_service')

    calls = []
    monkeypatch.setattr(ws, 'save_file', lambda uid, fn, content: calls.append((uid, fn)))
    current_user_id.set('+10000000000')

    result = module.workspace_save.invoke({"filename": "test.md", "content": "hello"})
    assert "Saved" in result
    assert calls[0] == ('+10000000000', 'test.md')


def test_workspace_read(monkeypatch):
    module = importlib.reload(importlib.import_module('prax.agent.workspace_tools'))
    ws = importlib.import_module('prax.services.workspace_service')

    monkeypatch.setattr(ws, 'read_file', lambda uid, fn: "# Content")
    current_user_id.set('+10000000000')

    result = module.workspace_read.invoke({"filename": "test.md"})
    assert "# Content" in result


def test_workspace_read_not_found(monkeypatch):
    module = importlib.reload(importlib.import_module('prax.agent.workspace_tools'))
    ws = importlib.import_module('prax.services.workspace_service')

    def raise_fnf(uid, fn):
        raise FileNotFoundError()

    monkeypatch.setattr(ws, 'read_file', raise_fnf)
    current_user_id.set('+10000000000')

    result = module.workspace_read.invoke({"filename": "missing.md"})
    assert "not found" in result


def test_workspace_list(monkeypatch):
    module = importlib.reload(importlib.import_module('prax.agent.workspace_tools'))
    ws = importlib.import_module('prax.services.workspace_service')

    monkeypatch.setattr(ws, 'list_active', lambda uid: ["a.md", "b.md"])
    current_user_id.set('+10000000000')

    result = module.workspace_list.invoke({})
    assert "a.md" in result
    assert "b.md" in result


def test_workspace_list_empty(monkeypatch):
    module = importlib.reload(importlib.import_module('prax.agent.workspace_tools'))
    ws = importlib.import_module('prax.services.workspace_service')

    monkeypatch.setattr(ws, 'list_active', lambda uid: [])
    current_user_id.set('+10000000000')

    result = module.workspace_list.invoke({})
    assert "empty" in result.lower()


def test_workspace_archive(monkeypatch):
    module = importlib.reload(importlib.import_module('prax.agent.workspace_tools'))
    ws = importlib.import_module('prax.services.workspace_service')

    calls = []
    monkeypatch.setattr(ws, 'archive_file', lambda uid, fn: calls.append(fn) or "/archived")
    current_user_id.set('+10000000000')

    result = module.workspace_archive.invoke({"filename": "paper.md"})
    assert "Archived" in result
    assert calls == ["paper.md"]


def test_workspace_search(monkeypatch):
    module = importlib.reload(importlib.import_module('prax.agent.workspace_tools'))
    ws = importlib.import_module('prax.services.workspace_service')

    monkeypatch.setattr(ws, 'search_archive', lambda uid, q: [
        {"filename": "old_paper.md", "snippet": "quantum computing results"},
    ])
    current_user_id.set('+10000000000')

    result = module.workspace_search.invoke({"query": "quantum"})
    assert "old_paper.md" in result
    assert "quantum" in result


def test_workspace_search_no_match(monkeypatch):
    module = importlib.reload(importlib.import_module('prax.agent.workspace_tools'))
    ws = importlib.import_module('prax.services.workspace_service')

    monkeypatch.setattr(ws, 'search_archive', lambda uid, q: [])
    current_user_id.set('+10000000000')

    result = module.workspace_search.invoke({"query": "nonexistent"})
    assert "No archived" in result


def test_workspace_restore(monkeypatch):
    module = importlib.reload(importlib.import_module('prax.agent.workspace_tools'))
    ws = importlib.import_module('prax.services.workspace_service')

    calls = []
    monkeypatch.setattr(ws, 'restore_file', lambda uid, fn: calls.append(fn) or "/restored")
    current_user_id.set('+10000000000')

    result = module.workspace_restore.invoke({"filename": "paper.md"})
    assert "Restored" in result
    assert calls == ["paper.md"]


def test_missing_user_id_falls_back(monkeypatch):
    module = importlib.reload(importlib.import_module('prax.agent.workspace_tools'))
    ws = importlib.import_module('prax.services.workspace_service')

    calls = []
    monkeypatch.setattr(ws, 'list_active', lambda uid: calls.append(uid) or [])
    current_user_id.set(None)

    module.workspace_list.invoke({})
    assert calls == ["unknown"]


class TestThinkTool:
    def test_think_returns_ok(self, monkeypatch):
        module = importlib.reload(importlib.import_module('prax.agent.workspace_tools'))
        ws = importlib.import_module('prax.services.workspace_service')
        monkeypatch.setattr(ws, 'append_trace', lambda uid, entries: None)
        current_user_id.set('+10000000000')
        result = module.think.invoke({"reasoning": "Should I use tool A or B?"})
        assert result == "OK"

    def test_think_records_to_trace(self, monkeypatch):
        module = importlib.reload(importlib.import_module('prax.agent.workspace_tools'))
        ws = importlib.import_module('prax.services.workspace_service')
        recorded = []
        monkeypatch.setattr(ws, 'append_trace', lambda uid, entries: recorded.extend(entries))
        current_user_id.set('+10000000000')
        module.think.invoke({"reasoning": "Evaluating options"})
        assert len(recorded) == 1
        assert recorded[0]["type"] == "think"
        assert "[THINK]" in recorded[0]["content"]
        assert "Evaluating options" in recorded[0]["content"]

    def test_think_no_user_id_still_returns_ok(self, monkeypatch):
        module = importlib.reload(importlib.import_module('prax.agent.workspace_tools'))
        current_user_id.set(None)
        result = module.think.invoke({"reasoning": "private reasoning"})
        assert result == "OK"

    def test_think_trace_error_swallowed(self, monkeypatch):
        module = importlib.reload(importlib.import_module('prax.agent.workspace_tools'))
        ws = importlib.import_module('prax.services.workspace_service')
        monkeypatch.setattr(ws, 'append_trace', lambda uid, entries: (_ for _ in ()).throw(RuntimeError("disk full")))
        current_user_id.set('+10000000000')
        result = module.think.invoke({"reasoning": "should not crash"})
        assert result == "OK"


class TestRequestExtendedBudget:
    def test_extends_budget(self, monkeypatch):
        module = importlib.reload(importlib.import_module('prax.agent.workspace_tools'))
        from prax.agent import governed_tool as gov
        gov._tool_call_count = 10
        gov._tool_call_budget = 15
        current_user_id.set('+10000000000')
        result = module.request_extended_budget.invoke({"reason": "need more", "additional_calls": 20})
        assert "Budget extended by 20" in result
        assert gov._tool_call_budget == 35  # 15 + 20

    def test_caps_at_50(self, monkeypatch):
        module = importlib.reload(importlib.import_module('prax.agent.workspace_tools'))
        from prax.agent import governed_tool as gov
        gov._tool_call_count = 0
        gov._tool_call_budget = 10
        current_user_id.set('+10000000000')
        result = module.request_extended_budget.invoke({"reason": "big task", "additional_calls": 100})
        assert "Budget extended by 50" in result
        assert gov._tool_call_budget == 60  # 10 + 50

    def test_minimum_is_5(self, monkeypatch):
        module = importlib.reload(importlib.import_module('prax.agent.workspace_tools'))
        from prax.agent import governed_tool as gov
        gov._tool_call_count = 0
        gov._tool_call_budget = 10
        current_user_id.set('+10000000000')
        result = module.request_extended_budget.invoke({"reason": "tiny", "additional_calls": 1})
        assert "Budget extended by 5" in result
        assert gov._tool_call_budget == 15  # 10 + 5
