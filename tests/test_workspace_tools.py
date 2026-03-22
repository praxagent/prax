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
