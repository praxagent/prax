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


def test_artifact_locator_finds_recent_note_urls_before_filename_noise(monkeypatch):
    module = importlib.reload(importlib.import_module('prax.agent.workspace_tools'))
    ws = importlib.import_module('prax.services.workspace_service')
    note_service = importlib.import_module('prax.services.note_service')
    library_service = importlib.import_module('prax.services.library_service')

    trace_tail = """
=== 2026-04-29T02:41:39Z ===
[TOOL_RESULT] [delegate_knowledge] Done: http://localhost:8000/notes/create-a-polished-video-presentation-package-for-the-paper-t/
=== 2026-04-29T02:47:53Z ===
[TOOL_RESULT] [delegate_knowledge] Done — note created here: http://localhost:8000/notes/video-presentation-note-for-the-paper-predictive-pursuit-eme/
"""
    monkeypatch.setattr(ws, 'read_trace_tail', lambda uid, lines=1200: trace_tail)
    monkeypatch.setattr(ws, 'list_active', lambda uid: [
        "three_marks_presentation.tex",
        "Attention_Is_All_You_Need_slides.tex",
    ])
    monkeypatch.setattr(ws, 'search_archive', lambda uid, q: [])
    monkeypatch.setattr(note_service, 'list_notes', lambda uid: [])
    monkeypatch.setattr(library_service, 'list_notes', lambda uid: [])
    monkeypatch.setattr(library_service, 'list_outputs', lambda uid: [])
    current_user_id.set('+10000000000')

    result = module.artifact_locator.invoke({"query": "where is it?"})

    assert "video-presentation-note-for-the-paper-predictive-pursuit-eme" in result
    assert "create-a-polished-video-presentation-package" in result
    assert result.find("video-presentation-note-for-the-paper-predictive-pursuit-eme") < result.find(
        "create-a-polished-video-presentation-package",
    )
    assert "three_marks_presentation.tex" not in result


def test_artifact_locator_uses_note_index_when_trace_has_no_urls(monkeypatch):
    module = importlib.reload(importlib.import_module('prax.agent.workspace_tools'))
    ws = importlib.import_module('prax.services.workspace_service')
    note_service = importlib.import_module('prax.services.note_service')
    library_service = importlib.import_module('prax.services.library_service')

    monkeypatch.setattr(ws, 'read_trace_tail', lambda uid, lines=1200: "")
    monkeypatch.setattr(ws, 'list_active', lambda uid: [])
    monkeypatch.setattr(ws, 'search_archive', lambda uid, q: [])
    monkeypatch.setattr(note_service, 'list_notes', lambda uid: [
        {
            "slug": "video-presentation-note-for-the-paper",
            "title": "Video presentation note for the paper",
        },
    ])
    monkeypatch.setattr(library_service, 'list_notes', lambda uid: [])
    monkeypatch.setattr(library_service, 'list_outputs', lambda uid: [])
    current_user_id.set('+10000000000')

    result = module.artifact_locator.invoke({"query": "video presentation"})

    assert "/notes/video-presentation-note-for-the-paper/" in result
    assert "note index" in result


def test_build_workspace_tools_includes_artifact_locator():
    module = importlib.reload(importlib.import_module('prax.agent.workspace_tools'))

    names = {tool.name for tool in module.build_workspace_tools()}

    assert "artifact_locator" in names


def test_workspace_restore(monkeypatch):
    module = importlib.reload(importlib.import_module('prax.agent.workspace_tools'))
    ws = importlib.import_module('prax.services.workspace_service')

    calls = []
    monkeypatch.setattr(ws, 'restore_file', lambda uid, fn: calls.append(fn) or "/restored")
    current_user_id.set('+10000000000')

    result = module.workspace_restore.invoke({"filename": "paper.md"})
    assert "Restored" in result
    assert calls == ["paper.md"]


class TestEditWithLinter:
    """Syntax validation gates writes — broken edits never hit disk."""

    def _module(self):
        return importlib.reload(importlib.import_module('prax.agent.workspace_tools'))

    def test_valid_python_saves(self, monkeypatch):
        module = self._module()
        ws = importlib.import_module('prax.services.workspace_service')
        calls = []
        monkeypatch.setattr(ws, 'save_file', lambda uid, fn, content: calls.append(fn))
        current_user_id.set('+10000000000')
        result = module.workspace_save.invoke({
            "filename": "ok.py", "content": "def f():\n    return 1\n",
        })
        assert "Saved" in result
        assert calls == ["ok.py"]

    def test_broken_python_rejected(self, monkeypatch):
        module = self._module()
        ws = importlib.import_module('prax.services.workspace_service')
        calls = []
        monkeypatch.setattr(ws, 'save_file', lambda uid, fn, content: calls.append(fn))
        current_user_id.set('+10000000000')
        result = module.workspace_save.invoke({
            "filename": "broken.py", "content": "def f(:\n    return 1\n",
        })
        assert "Rejected" in result
        assert "syntax error" in result.lower()
        assert calls == []  # never reached disk

    def test_valid_json_saves(self, monkeypatch):
        module = self._module()
        ws = importlib.import_module('prax.services.workspace_service')
        calls = []
        monkeypatch.setattr(ws, 'save_file', lambda uid, fn, content: calls.append(fn))
        current_user_id.set('+10000000000')
        result = module.workspace_save.invoke({
            "filename": "ok.json", "content": '{"a": 1, "b": [2, 3]}',
        })
        assert "Saved" in result
        assert calls == ["ok.json"]

    def test_broken_json_rejected(self, monkeypatch):
        module = self._module()
        ws = importlib.import_module('prax.services.workspace_service')
        calls = []
        monkeypatch.setattr(ws, 'save_file', lambda uid, fn, content: calls.append(fn))
        current_user_id.set('+10000000000')
        result = module.workspace_save.invoke({
            "filename": "broken.json", "content": '{"a": 1, "b": [2, 3',
        })
        assert "Rejected" in result
        assert "JSON" in result
        assert calls == []

    def test_valid_yaml_saves(self, monkeypatch):
        module = self._module()
        ws = importlib.import_module('prax.services.workspace_service')
        calls = []
        monkeypatch.setattr(ws, 'save_file', lambda uid, fn, content: calls.append(fn))
        current_user_id.set('+10000000000')
        result = module.workspace_save.invoke({
            "filename": "config.yaml", "content": "foo: 1\nbar:\n  - a\n  - b\n",
        })
        assert "Saved" in result

    def test_broken_yaml_rejected(self, monkeypatch):
        module = self._module()
        ws = importlib.import_module('prax.services.workspace_service')
        calls = []
        monkeypatch.setattr(ws, 'save_file', lambda uid, fn, content: calls.append(fn))
        current_user_id.set('+10000000000')
        # Unclosed flow mapping — yaml.safe_load raises.
        result = module.workspace_save.invoke({
            "filename": "broken.yml", "content": "foo: {a: 1, b: 2\n",
        })
        assert "Rejected" in result
        assert calls == []

    def test_markdown_passes_through(self, monkeypatch):
        module = self._module()
        ws = importlib.import_module('prax.services.workspace_service')
        calls = []
        monkeypatch.setattr(ws, 'save_file', lambda uid, fn, content: calls.append(fn))
        current_user_id.set('+10000000000')
        # Even "syntactically-broken-looking" non-mermaid markdown saves fine.
        result = module.workspace_save.invoke({
            "filename": "notes.md", "content": "# Title\n\n```python\ndef f(:",
        })
        assert "Saved" in result
        assert calls == ["notes.md"]

    def test_markdown_with_valid_mermaid_saves(self, monkeypatch):
        module = self._module()
        ws = importlib.import_module('prax.services.workspace_service')
        calls = []
        monkeypatch.setattr(ws, 'save_file', lambda uid, fn, content: calls.append(fn))
        current_user_id.set('+10000000000')
        result = module.workspace_save.invoke({
            "filename": "diagram.md",
            "content": "# Title\n\n```mermaid\nflowchart TD\n  A --> B\n```\n",
        })
        assert "Saved" in result
        assert calls == ["diagram.md"]

    def test_markdown_with_broken_mermaid_rejected(self, monkeypatch):
        module = self._module()
        ws = importlib.import_module('prax.services.workspace_service')
        calls = []
        monkeypatch.setattr(ws, 'save_file', lambda uid, fn, content: calls.append(fn))
        current_user_id.set('+10000000000')
        # Prose first line — exact failure mode from the conditional-misalignment note.
        result = module.workspace_save.invoke({
            "filename": "broken.md",
            "content": (
                "# Title\n\n"
                "```mermaid\n"
                "An analytical diagram: training context and triggers\n"
                "  A --> B\n"
                "```\n"
            ),
        })
        assert "Rejected" in result
        assert "mermaid" in result.lower()
        assert calls == []

    def test_patch_rejected_when_result_broken(self, monkeypatch):
        module = self._module()
        ws = importlib.import_module('prax.services.workspace_service')
        saves = []
        monkeypatch.setattr(
            ws, 'read_file',
            lambda uid, fn: "def f():\n    return 1\n",
        )
        monkeypatch.setattr(ws, 'save_file', lambda uid, fn, content: saves.append(content))
        current_user_id.set('+10000000000')
        # Patch that would introduce a syntax error.
        result = module.workspace_patch.invoke({
            "filename": "broken.py",
            "old_text": "def f():",
            "new_text": "def f(:",
        })
        assert "Rejected" in result
        assert saves == []

    def test_patch_allowed_when_result_valid(self, monkeypatch):
        module = self._module()
        ws = importlib.import_module('prax.services.workspace_service')
        saves = []
        monkeypatch.setattr(
            ws, 'read_file',
            lambda uid, fn: "def f():\n    return 1\n",
        )
        monkeypatch.setattr(ws, 'save_file', lambda uid, fn, content: saves.append(content))
        current_user_id.set('+10000000000')
        result = module.workspace_patch.invoke({
            "filename": "ok.py",
            "old_text": "return 1",
            "new_text": "return 2",
        })
        assert "Patched" in result
        assert saves and "return 2" in saves[0]


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
