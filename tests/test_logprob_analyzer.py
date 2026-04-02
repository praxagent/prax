"""Tests for Active Inference Phase 3 (conditional logprob entropy analysis)."""

from prax.agent.logprob_analyzer import (
    LogprobCallbackHandler,
    ToolCallEntropy,
    drain_entropy_buffer,
    get_entropy_for_tool,
)


class TestToolCallEntropy:
    def test_is_uncertain_high_entropy(self):
        entry = ToolCallEntropy(
            tool_name="workspace_save",
            mean_logprob=-3.0,
            min_logprob=-5.0,
            entropy_score=0.6,
            high_entropy_tokens=["database", ".py"],
        )
        assert entry.is_uncertain

    def test_is_certain_low_entropy(self):
        entry = ToolCallEntropy(
            tool_name="workspace_read",
            mean_logprob=-0.5,
            min_logprob=-1.0,
            entropy_score=0.1,
        )
        assert not entry.is_uncertain


class TestEntropyBuffer:
    def setup_method(self):
        # Clear buffer between tests.
        drain_entropy_buffer()

    def test_drain_clears_buffer(self):
        # Buffer should be empty after drain.
        entries = drain_entropy_buffer()
        assert entries == []

    def test_get_entropy_for_tool_returns_none_when_empty(self):
        assert get_entropy_for_tool("nonexistent") is None


class TestLogprobCallbackHandler:
    def test_no_crash_on_empty_response(self):
        handler = LogprobCallbackHandler()

        class FakeResponse:
            generations = []

        handler.on_llm_end(FakeResponse())
        assert drain_entropy_buffer() == []

    def test_no_crash_on_missing_logprobs(self):
        handler = LogprobCallbackHandler()

        class FakeGeneration:
            generation_info = {}
            message = None

        class FakeResponse:
            generations = [[FakeGeneration()]]

        handler.on_llm_end(FakeResponse())
        assert drain_entropy_buffer() == []

    def test_processes_logprobs_from_generation_info(self):
        handler = LogprobCallbackHandler()

        class FakeMessage:
            tool_calls = [{"name": "workspace_save", "args": {}}]
            response_metadata = {}

        class FakeGeneration:
            generation_info = {
                "logprobs": {
                    "content": [
                        {"token": "workspace", "logprob": -0.1},
                        {"token": "_save", "logprob": -0.2},
                        {"token": "notes", "logprob": -3.5},  # uncertain
                        {"token": ".md", "logprob": -0.05},
                    ]
                }
            }
            message = FakeMessage()

        class FakeResponse:
            generations = [[FakeGeneration()]]

        handler.on_llm_end(FakeResponse())
        entries = drain_entropy_buffer()
        assert len(entries) == 1
        assert entries[0].tool_name == "workspace_save"
        assert entries[0].mean_logprob < 0
        assert "notes" in entries[0].high_entropy_tokens

    def test_processes_logprobs_from_response_metadata(self):
        handler = LogprobCallbackHandler()

        class FakeMessage:
            tool_calls = [{"name": "workspace_read", "args": {}}]
            response_metadata = {
                "logprobs": {
                    "content": [
                        {"token": "config", "logprob": -0.3},
                        {"token": ".yaml", "logprob": -0.1},
                    ]
                }
            }

        class FakeGeneration:
            generation_info = {}
            message = FakeMessage()

        class FakeResponse:
            generations = [[FakeGeneration()]]

        handler.on_llm_end(FakeResponse())
        entries = drain_entropy_buffer()
        assert len(entries) == 1
        assert entries[0].tool_name == "workspace_read"
        # Low entropy — model is confident.
        assert entries[0].entropy_score < 0.2

    def test_multiple_tool_calls(self):
        handler = LogprobCallbackHandler()

        class FakeMessage:
            tool_calls = [
                {"name": "tool_a", "args": {}},
                {"name": "tool_b", "args": {}},
            ]
            response_metadata = {}

        class FakeGeneration:
            generation_info = {
                "logprobs": {
                    "content": [
                        {"token": "x", "logprob": -1.0},
                    ]
                }
            }
            message = FakeMessage()

        class FakeResponse:
            generations = [[FakeGeneration()]]

        handler.on_llm_end(FakeResponse())
        entries = drain_entropy_buffer()
        assert len(entries) == 2
        tool_names = {e.tool_name for e in entries}
        assert tool_names == {"tool_a", "tool_b"}
