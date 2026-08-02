"""Provider content-shape normalization (prax/agent/message_text.py).

Regression cover for a bug that shipped: OpenAI's Responses API returns a LIST
of content blocks, and the orchestrator did `msg.content + str`, crashing every
turn with `TypeError: can only concatenate list (not "str") to list`. The
quieter half — `str(list_content)` yielding a Python repr — had already bitten
the spoke runner.
"""
from types import SimpleNamespace

from langchain_core.messages import AIMessage

from prax.agent.message_text import content_text, message_text


class TestContentText:
    def test_plain_string_passes_through(self):
        assert content_text("hello") == "hello"

    def test_none_and_empty_are_empty_string(self):
        assert content_text(None) == ""
        assert content_text([]) == ""

    def test_responses_api_block_list_is_flattened(self):
        blocks = [{"type": "text", "text": "536"}]
        assert content_text(blocks) == "536"

    def test_multiple_text_blocks_join_with_newline(self):
        blocks = [{"type": "text", "text": "one"}, {"type": "text", "text": "two"}]
        assert content_text(blocks) == "one\ntwo"

    def test_bare_string_blocks_are_kept(self):
        assert content_text(["a", "b"]) == "a\nb"

    def test_alternate_key_shapes(self):
        assert content_text([{"text": "x"}]) == "x"
        assert content_text([{"content": "y"}]) == "y"

    def test_non_text_blocks_are_dropped_not_leaked(self):
        """Reasoning/tool blocks must never reach the user's reply."""
        blocks = [
            {"type": "reasoning", "summary": "secret chain of thought"},
            {"type": "text", "text": "the answer"},
            {"type": "tool_use", "name": "shell", "input": {"cmd": "ls"}},
        ]
        out = content_text(blocks)
        assert out == "the answer"
        assert "secret" not in out and "shell" not in out

    def test_no_python_repr_ever_escapes(self):
        """The silent failure mode: str(list) leaking `[{'type': ...}]`."""
        out = content_text([{"type": "text", "text": "hi"}])
        assert "{" not in out and "'type'" not in out


class TestMessageText:
    def test_reads_content_off_a_message(self):
        assert message_text(AIMessage(content="plain")) == "plain"

    def test_reads_block_list_off_a_message(self):
        assert message_text(AIMessage(content=[{"type": "text", "text": "b"}])) == "b"

    def test_object_without_content_is_empty(self):
        assert message_text(SimpleNamespace()) == ""

    def test_result_is_always_concatenable(self):
        """The actual crash: the orchestrator appends a notice to this."""
        for shape in ("s", [{"type": "text", "text": "s"}], ["s"], None, []):
            assert isinstance(content_text(shape) + "\n[notice]", str)
