"""Tests for the Claude Code sandbox tools."""
from __future__ import annotations

import json
from unittest.mock import patch

from prax.agent.claude_code_tools import (
    _run_coding_agent,
    _shell_escape,
    build_claude_code_tools,
    claude_code_ask,
    claude_code_message,
    claude_code_start_session,
)

# ---------------------------------------------------------------------------
# _shell_escape
# ---------------------------------------------------------------------------


class TestShellEscape:
    def test_simple_string(self):
        assert _shell_escape("hello") == "'hello'"

    def test_string_with_quotes(self):
        assert _shell_escape("it's") == "'it'\\''s'"

    def test_empty(self):
        assert _shell_escape("") == "''"


# ---------------------------------------------------------------------------
# _run_claude
# ---------------------------------------------------------------------------


class TestRunCodingAgent:
    def test_parses_json_output(self):
        data = {
            "result": [{"type": "text", "text": "Fixed the bug."}],
            "session_id": "conv-123",
            "cost_usd": 0.005,
        }
        with patch(
            "prax.agent.claude_code_tools.sandbox_service.run_shell",
            return_value={"stdout": json.dumps(data), "stderr": "", "exit_code": 0},
        ):
            result = _run_coding_agent("fix the bug")
            assert result["response"] == "Fixed the bug."
            assert result["conversation_id"] == "conv-123"
            assert result["cost"] == 0.005

    def test_handles_plain_string_result(self):
        data = {"result": "Done.", "session_id": "conv-456"}
        with patch(
            "prax.agent.claude_code_tools.sandbox_service.run_shell",
            return_value={"stdout": json.dumps(data), "stderr": "", "exit_code": 0},
        ):
            result = _run_coding_agent("do something")
            assert result["response"] == "Done."

    def test_handles_non_json_output(self):
        with patch(
            "prax.agent.claude_code_tools.sandbox_service.run_shell",
            return_value={"stdout": "raw text output", "stderr": "", "exit_code": 0},
        ):
            result = _run_coding_agent("do something")
            assert result["response"] == "raw text output"

    def test_handles_sandbox_error(self):
        with patch(
            "prax.agent.claude_code_tools.sandbox_service.run_shell",
            return_value={"error": "container not running"},
        ):
            result = _run_coding_agent("do something")
            assert "ERROR" in result["response"]

    def test_handles_nonzero_exit(self):
        with patch(
            "prax.agent.claude_code_tools.sandbox_service.run_shell",
            return_value={"stdout": "", "stderr": "command not found", "exit_code": 127},
        ):
            result = _run_coding_agent("do something")
            assert "ERROR" in result["response"]
            assert result["exit_code"] == 127

    def test_resume_adds_flag(self):
        data = {"result": "Resumed.", "session_id": "conv-789"}
        with patch(
            "prax.agent.claude_code_tools.sandbox_service.run_shell",
            return_value={"stdout": json.dumps(data), "stderr": "", "exit_code": 0},
        ) as mock_run:
            _run_coding_agent("continue", resume_id="conv-789")
            cmd = mock_run.call_args[0][0]
            assert "--resume" in cmd
            assert "conv-789" in cmd


# ---------------------------------------------------------------------------
# build_claude_code_tools
# ---------------------------------------------------------------------------


class TestBuildTools:
    def test_returns_empty_when_self_improve_disabled(self):
        with patch("prax.settings.settings.self_improve_enabled", False):
            assert build_claude_code_tools() == []

    def test_returns_tools_when_enabled(self):
        with patch("prax.settings.settings.self_improve_enabled", True):
            tools = build_claude_code_tools()
            assert len(tools) == 3
            names = {t.name for t in tools}
            assert names == {
                "claude_code_start_session",
                "claude_code_message",
                "claude_code_ask",
            }


# ---------------------------------------------------------------------------
# Individual tools
# ---------------------------------------------------------------------------


class TestStartSession:
    def test_empty_context(self):
        result = claude_code_start_session.invoke({"context": ""})
        assert "Error" in result

    def test_success(self):
        data = {
            "result": "Ready to help.",
            "session_id": "conv-abc",
        }
        with patch(
            "prax.agent.claude_code_tools.sandbox_service.run_shell",
            return_value={"stdout": json.dumps(data), "stderr": "", "exit_code": 0},
        ):
            result = claude_code_start_session.invoke({"context": "fix a bug"})
            assert "conv-abc" in result
            assert "Ready to help." in result


class TestMessage:
    def test_missing_fields(self):
        result = claude_code_message.invoke({"session_id": "", "message": ""})
        assert "required" in result.lower()

    def test_success(self):
        data = {"result": "Done!", "session_id": "conv-abc", "cost_usd": 0.003}
        with patch(
            "prax.agent.claude_code_tools.sandbox_service.run_shell",
            return_value={"stdout": json.dumps(data), "stderr": "", "exit_code": 0},
        ):
            result = claude_code_message.invoke({
                "session_id": "conv-abc",
                "message": "run the tests",
            })
            assert "Done!" in result


class TestAsk:
    def test_success(self):
        data = {"result": "v1.2.3"}
        with patch(
            "prax.agent.claude_code_tools.sandbox_service.run_shell",
            return_value={"stdout": json.dumps(data), "stderr": "", "exit_code": 0},
        ):
            result = claude_code_ask.invoke({"prompt": "what version?"})
            assert "v1.2.3" in result
