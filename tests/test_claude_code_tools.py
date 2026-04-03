"""Tests for the Claude Code bridge tools."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from prax.agent.claude_code_tools import (
    build_claude_code_tools,
    claude_code_ask,
    claude_code_end_session,
    claude_code_message,
    claude_code_start_session,
    is_bridge_available,
    reset_bridge_cache,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    """Ensure each test starts with a fresh bridge availability cache."""
    reset_bridge_cache()
    yield
    reset_bridge_cache()


# ---------------------------------------------------------------------------
# is_bridge_available
# ---------------------------------------------------------------------------


class TestBridgeAvailability:
    def test_no_url_configured(self):
        with patch("prax.agent.claude_code_tools._bridge_url", return_value=""):
            assert is_bridge_available() is False

    def test_bridge_reachable(self):
        resp = MagicMock()
        resp.ok = True
        resp.json.return_value = {"claude_version": "1.0", "repo_path": "/repo"}
        with (
            patch("prax.agent.claude_code_tools._bridge_url", return_value="http://localhost:9819"),
            patch("prax.agent.claude_code_tools.requests.get", return_value=resp),
        ):
            assert is_bridge_available() is True

    def test_bridge_unreachable(self):
        import requests as _req

        with (
            patch("prax.agent.claude_code_tools._bridge_url", return_value="http://localhost:9819"),
            patch("prax.agent.claude_code_tools.requests.get", side_effect=_req.ConnectionError),
        ):
            assert is_bridge_available() is False

    def test_result_is_cached(self):
        resp = MagicMock()
        resp.ok = True
        resp.json.return_value = {}
        with (
            patch("prax.agent.claude_code_tools._bridge_url", return_value="http://localhost:9819"),
            patch("prax.agent.claude_code_tools.requests.get", return_value=resp) as mock_get,
        ):
            assert is_bridge_available() is True
            assert is_bridge_available() is True
            mock_get.assert_called_once()

    def test_reset_clears_cache(self):
        resp = MagicMock()
        resp.ok = True
        resp.json.return_value = {}
        with (
            patch("prax.agent.claude_code_tools._bridge_url", return_value="http://localhost:9819"),
            patch("prax.agent.claude_code_tools.requests.get", return_value=resp) as mock_get,
        ):
            is_bridge_available()
            reset_bridge_cache()
            is_bridge_available()
            assert mock_get.call_count == 2


# ---------------------------------------------------------------------------
# build_claude_code_tools
# ---------------------------------------------------------------------------


class TestBuildTools:
    def test_returns_empty_when_bridge_down(self):
        with patch("prax.agent.claude_code_tools.is_bridge_available", return_value=False):
            assert build_claude_code_tools() == []

    def test_returns_tools_when_bridge_up(self):
        with patch("prax.agent.claude_code_tools.is_bridge_available", return_value=True):
            tools = build_claude_code_tools()
            assert len(tools) == 4
            names = {t.name for t in tools}
            assert names == {
                "claude_code_start_session",
                "claude_code_message",
                "claude_code_end_session",
                "claude_code_ask",
            }


# ---------------------------------------------------------------------------
# Individual tools
# ---------------------------------------------------------------------------


class TestStartSession:
    def test_bridge_down(self):
        with patch("prax.agent.claude_code_tools.is_bridge_available", return_value=False):
            result = claude_code_start_session.invoke({"context": "test"})
            assert "not running" in result

    def test_success(self):
        with (
            patch("prax.agent.claude_code_tools.is_bridge_available", return_value=True),
            patch(
                "prax.agent.claude_code_tools._post",
                return_value={"session_id": "abc123", "response": "Ready"},
            ),
        ):
            result = claude_code_start_session.invoke({"context": "fix a bug"})
            assert "abc123" in result
            assert "Ready" in result

    def test_error(self):
        with (
            patch("prax.agent.claude_code_tools.is_bridge_available", return_value=True),
            patch(
                "prax.agent.claude_code_tools._post",
                return_value={"error": "something broke"},
            ),
        ):
            result = claude_code_start_session.invoke({"context": ""})
            assert "Error" in result


class TestMessage:
    def test_missing_fields(self):
        result = claude_code_message.invoke({"session_id": "", "message": ""})
        assert "required" in result.lower()

    def test_success(self):
        with patch(
            "prax.agent.claude_code_tools._post",
            return_value={"response": "Done!", "turn": 3, "cost": 0.0042},
        ):
            result = claude_code_message.invoke({
                "session_id": "abc123",
                "message": "run the tests",
            })
            assert "Turn 3" in result
            assert "Done!" in result
            assert "$0.0042" in result


class TestEndSession:
    def test_success(self):
        with patch(
            "prax.agent.claude_code_tools._post",
            return_value={"turns": 5},
        ):
            result = claude_code_end_session.invoke({"session_id": "abc123"})
            assert "5 turns" in result


class TestAsk:
    def test_bridge_down(self):
        with patch("prax.agent.claude_code_tools.is_bridge_available", return_value=False):
            result = claude_code_ask.invoke({"prompt": "what version?"})
            assert "not running" in result

    def test_success(self):
        with (
            patch("prax.agent.claude_code_tools.is_bridge_available", return_value=True),
            patch(
                "prax.agent.claude_code_tools._post",
                return_value={"response": "v1.2.3"},
            ),
        ):
            result = claude_code_ask.invoke({"prompt": "what version?"})
            assert "v1.2.3" in result


# ---------------------------------------------------------------------------
# _post internals
# ---------------------------------------------------------------------------


class TestPost:
    def test_no_url(self):
        from prax.agent.claude_code_tools import _post

        with patch("prax.agent.claude_code_tools._bridge_url", return_value=""):
            result = _post("/health", {})
            assert "error" in result

    def test_auth_failure(self):
        from prax.agent.claude_code_tools import _post

        resp = MagicMock()
        resp.status_code = 401
        with (
            patch("prax.agent.claude_code_tools._bridge_url", return_value="http://localhost:9819"),
            patch("prax.agent.claude_code_tools.requests.post", return_value=resp),
        ):
            result = _post("/session/start", {})
            assert "auth failed" in result["error"].lower()

    def test_timeout(self):
        import requests as _req

        from prax.agent.claude_code_tools import _post

        with (
            patch("prax.agent.claude_code_tools._bridge_url", return_value="http://localhost:9819"),
            patch("prax.agent.claude_code_tools.requests.post", side_effect=_req.Timeout),
        ):
            result = _post("/ask", {})
            assert "timed out" in result["error"].lower()

    def test_connection_error_resets_cache(self):
        import requests as _req

        from prax.agent.claude_code_tools import _post

        with (
            patch("prax.agent.claude_code_tools._bridge_url", return_value="http://localhost:9819"),
            patch("prax.agent.claude_code_tools.requests.post", side_effect=_req.ConnectionError),
            patch("prax.agent.claude_code_tools.reset_bridge_cache") as mock_reset,
        ):
            result = _post("/session/start", {})
            assert "not running" in result["error"].lower()
            mock_reset.assert_called_once()
