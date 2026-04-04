"""Tests for real-time trajectory export service."""
from __future__ import annotations

import json
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from prax.services.trajectory_service import (
    _build_chatml,
    _classify_outcome,
    export_trajectory,
)


class TestClassifyOutcome:
    def test_success(self):
        outcome, fname = _classify_outcome("hello", "Hi there!", [])
        assert outcome == "success"
        assert fname == "completed.jsonl"

    def test_empty_response(self):
        outcome, fname = _classify_outcome("hello", "", [])
        assert outcome == "empty_response"
        assert fname == "failed.jsonl"

    def test_correction(self):
        outcome, fname = _classify_outcome("no that's wrong", "Sorry, let me fix that", [])
        assert outcome == "correction"
        assert fname == "failed.jsonl"

    def test_negative(self):
        outcome, fname = _classify_outcome("useless", "I'll try harder", [])
        assert outcome == "negative"
        assert fname == "failed.jsonl"

    def test_tool_failure(self):
        msgs = [ToolMessage(content="Error: connection refused", tool_call_id="tc1")]
        outcome, fname = _classify_outcome("do something", "I encountered an issue", msgs)
        assert outcome == "tool_failure"
        assert fname == "failed.jsonl"

    def test_tool_success_not_flagged(self):
        msgs = [ToolMessage(content="Result: 42", tool_call_id="tc1")]
        outcome, fname = _classify_outcome("calculate", "The answer is 42", msgs)
        assert outcome == "success"
        assert fname == "completed.jsonl"


class TestBuildChatML:
    def test_basic_structure(self):
        msgs = [
            HumanMessage(content="hello"),
            AIMessage(content="world"),
        ]
        example = _build_chatml("hello", "world", msgs)
        assert example["messages"][0]["role"] == "system"
        assert example["messages"][-1]["role"] == "assistant"
        assert example["messages"][-1]["content"] == "world"
        assert example["messages"][-2]["role"] == "user"
        assert example["messages"][-2]["content"] == "hello"

    def test_context_included(self):
        msgs = [
            HumanMessage(content="first"),
            AIMessage(content="response1"),
            HumanMessage(content="second"),
            AIMessage(content="response2"),
            HumanMessage(content="third"),
            AIMessage(content="response3"),
        ]
        example = _build_chatml("third", "response3", msgs)
        # Should have system + context + current turn
        assert len(example["messages"]) >= 3


class TestExportTrajectory:
    def test_writes_to_file(self, tmp_path):
        with patch("prax.services.trajectory_service._trajectories_dir", return_value=tmp_path):
            export_trajectory("user1", "hello", "Hi!", [])

        completed = tmp_path / "completed.jsonl"
        assert completed.exists()
        line = json.loads(completed.read_text().strip())
        assert line["metadata"]["outcome"] == "success"
        assert line["metadata"]["user_id"] == "user1"
        assert line["messages"][-1]["content"] == "Hi!"

    def test_failed_goes_to_failed_file(self, tmp_path):
        with patch("prax.services.trajectory_service._trajectories_dir", return_value=tmp_path):
            export_trajectory("user1", "no that's wrong", "Sorry!", [])

        failed = tmp_path / "failed.jsonl"
        assert failed.exists()
        line = json.loads(failed.read_text().strip())
        assert line["metadata"]["outcome"] == "correction"

    def test_skips_scheduled_tasks(self, tmp_path):
        with patch("prax.services.trajectory_service._trajectories_dir", return_value=tmp_path):
            export_trajectory("user1", "[SCHEDULED_TASK — ...] check email", "Done", [])

        assert not (tmp_path / "completed.jsonl").exists()
        assert not (tmp_path / "failed.jsonl").exists()

    def test_skips_empty_response(self, tmp_path):
        with patch("prax.services.trajectory_service._trajectories_dir", return_value=tmp_path):
            export_trajectory("user1", "hello", "", [])

        assert not (tmp_path / "completed.jsonl").exists()

    def test_session_id_in_metadata(self, tmp_path):
        with patch("prax.services.trajectory_service._trajectories_dir", return_value=tmp_path):
            export_trajectory("user1", "hello", "Hi!", [], session_id="abc123")

        line = json.loads((tmp_path / "completed.jsonl").read_text().strip())
        assert line["metadata"]["session_id"] == "abc123"

    def test_multiple_exports_append(self, tmp_path):
        with patch("prax.services.trajectory_service._trajectories_dir", return_value=tmp_path):
            export_trajectory("user1", "hello", "Hi!", [])
            export_trajectory("user1", "how are you", "Good!", [])

        lines = (tmp_path / "completed.jsonl").read_text().strip().splitlines()
        assert len(lines) == 2
