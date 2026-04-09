"""Tests for the P1+P2+P5 batch from
``docs/research/prax-changes-from-todo-research.md``:

- P1: Task provenance (``source`` + ``source_justification``)
- P2: Plan + task confidence signal
- P5: Plan context cap (compact rendering in ``get_workspace_context``)
"""
from __future__ import annotations

import pytest

from prax.services import library_service, library_tasks, workspace_service

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

USER = "+15550001111"


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """Isolated workspace for both workspace_service and library_service."""
    monkeypatch.setattr(workspace_service.settings, "workspace_dir", str(tmp_path))
    # library_service.workspace_root is a thin wrapper around the same settings
    monkeypatch.setattr(
        library_service, "workspace_root",
        lambda _uid: str(tmp_path / _uid.replace("+", "_")),
    )
    (tmp_path / USER.replace("+", "_")).mkdir()
    return tmp_path


# ---------------------------------------------------------------------------
# P1 — Task provenance
# ---------------------------------------------------------------------------

class TestTaskProvenance:
    def _setup(self, ws):
        library_service.create_space(USER, "Business")

    def test_default_source_is_user_request(self, ws):
        self._setup(ws)
        result = library_tasks.create_task(USER, "business", title="Write spec")
        assert result["task"]["source"] == "user_request"
        assert result["task"]["source_justification"] == ""
        # Activity log records source
        assert result["task"]["activity"][0]["source"] == "user_request"

    def test_agent_derived_source_accepted(self, ws):
        self._setup(ws)
        result = library_tasks.create_task(
            USER, "business", title="Write spec",
            author="prax", source="agent_derived",
        )
        assert result["task"]["source"] == "agent_derived"

    def test_tool_output_requires_justification(self, ws):
        self._setup(ws)
        result = library_tasks.create_task(
            USER, "business", title="Follow up with Alice",
            author="prax", source="tool_output",
        )
        assert "error" in result
        assert "source_justification" in result["error"]

    def test_tool_output_with_justification_succeeds(self, ws):
        self._setup(ws)
        result = library_tasks.create_task(
            USER, "business", title="Follow up with Alice",
            author="prax", source="tool_output",
            source_justification="From calendar_read: meeting note said to follow up",
        )
        assert result["status"] == "created"
        assert result["task"]["source"] == "tool_output"
        assert "calendar_read" in result["task"]["source_justification"]

    def test_invalid_source_rejected(self, ws):
        self._setup(ws)
        result = library_tasks.create_task(
            USER, "business", title="X", source="malicious",
        )
        assert "error" in result

    def test_whitespace_only_justification_rejected(self, ws):
        self._setup(ws)
        result = library_tasks.create_task(
            USER, "business", title="X", author="prax",
            source="tool_output", source_justification="   ",
        )
        assert "error" in result


# ---------------------------------------------------------------------------
# P2 — Confidence signal
# ---------------------------------------------------------------------------

class TestTaskConfidence:
    def _setup(self, ws):
        library_service.create_space(USER, "Business")

    def test_default_confidence_is_medium(self, ws):
        self._setup(ws)
        result = library_tasks.create_task(USER, "business", title="Task")
        assert result["task"]["confidence"] == "medium"

    def test_confidence_low_persists(self, ws):
        self._setup(ws)
        result = library_tasks.create_task(
            USER, "business", title="Task", confidence="low",
        )
        assert result["task"]["confidence"] == "low"

    def test_confidence_high_persists(self, ws):
        self._setup(ws)
        result = library_tasks.create_task(
            USER, "business", title="Task", confidence="high",
        )
        assert result["task"]["confidence"] == "high"

    def test_invalid_confidence_falls_back_to_medium(self, ws):
        self._setup(ws)
        result = library_tasks.create_task(
            USER, "business", title="Task", confidence="super-high",
        )
        assert result["task"]["confidence"] == "medium"

    def test_update_task_changes_confidence(self, ws):
        self._setup(ws)
        created = library_tasks.create_task(USER, "business", title="Task")
        task_id = created["task"]["id"]
        result = library_tasks.update_task(
            USER, "business", task_id, confidence="low",
        )
        assert result["status"] == "updated"
        assert "confidence" in result["changed"]
        task = library_tasks.get_task(USER, "business", task_id)
        assert task["confidence"] == "low"


class TestPlanConfidence:
    def test_default_plan_confidence_is_medium(self, ws):
        plan = workspace_service.create_plan(USER, "Ship feature", ["step 1", "step 2"])
        assert plan["confidence"] == "medium"

    def test_plan_confidence_low_persists(self, ws):
        plan = workspace_service.create_plan(
            USER, "Ship feature", ["step 1"], confidence="low",
        )
        assert plan["confidence"] == "low"
        re_read = workspace_service.read_plan(USER)
        assert re_read["confidence"] == "low"

    def test_invalid_plan_confidence_falls_back_to_medium(self, ws):
        plan = workspace_service.create_plan(
            USER, "Ship feature", ["step 1"], confidence="bogus",
        )
        assert plan["confidence"] == "medium"


# ---------------------------------------------------------------------------
# P5 — Plan context cap (compact rendering)
# ---------------------------------------------------------------------------

class TestPlanContextCap:
    def test_small_plan_renders_fully(self, ws):
        workspace_service.create_plan(
            USER, "Small goal",
            ["step one", "step two", "step three"],
        )
        ctx = workspace_service.get_workspace_context(USER)
        # All three steps should appear verbatim
        assert "step one" in ctx
        assert "step two" in ctx
        assert "step three" in ctx
        # No compaction marker
        assert "Plan compacted" not in ctx

    def test_large_plan_step_count_triggers_compaction(self, ws):
        # 7 steps > PLAN_STEP_LIMIT of 6
        steps = [f"step number {i}" for i in range(1, 8)]
        workspace_service.create_plan(USER, "Many-step goal", steps)
        ctx = workspace_service.get_workspace_context(USER)
        assert "Plan compacted" in ctx
        # Current step is fully rendered
        assert "step number 1" in ctx
        # Late steps should not appear (they're cut)
        assert "step number 7" not in ctx

    def test_large_plan_char_count_triggers_compaction(self, ws):
        # 4 short steps but one huge one -> exceed 800 chars
        big = "x" * 900
        steps = ["alpha", "beta", big, "gamma"]
        workspace_service.create_plan(USER, "Verbose goal", steps)
        ctx = workspace_service.get_workspace_context(USER)
        assert "Plan compacted" in ctx

    def test_compact_plan_includes_confidence(self, ws):
        steps = [f"step number {i}" for i in range(1, 8)]
        workspace_service.create_plan(USER, "Goal", steps, confidence="low")
        ctx = workspace_service.get_workspace_context(USER)
        assert "confidence: low" in ctx

    def test_full_plan_includes_confidence(self, ws):
        workspace_service.create_plan(
            USER, "Goal", ["step one", "step two"], confidence="high",
        )
        ctx = workspace_service.get_workspace_context(USER)
        assert "confidence: high" in ctx

    def test_compact_plan_marks_current_and_upcoming(self, ws):
        steps = [f"step {i}" for i in range(1, 10)]
        workspace_service.create_plan(USER, "Goal", steps)
        # Mark step 3 done to advance current
        workspace_service.complete_plan_step(USER, 1)
        workspace_service.complete_plan_step(USER, 2)
        workspace_service.complete_plan_step(USER, 3)
        ctx = workspace_service.get_workspace_context(USER)
        assert "Plan compacted" in ctx
        # Current step is step 4
        assert "step 4" in ctx
        # Next up: step 5 and 6
        assert "step 5" in ctx
        # Remaining count is shown
        assert "more step" in ctx
