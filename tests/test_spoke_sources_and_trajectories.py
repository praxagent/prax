"""Spokes report what they read (SPOKE_SOURCES_ENABLED) and record their whole
conversation as training data (FULL_TRAJECTORIES_ENABLED).

docs/research/mass-multi-agent-self-supervision.md: a spoke's reply is its
output contract, and sub-agent conversations are most of the supervision a
multi-agent training run needs.
"""
from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from prax.agent.claim_audit import audit_unread_citations, fetched_urls
from prax.agent.spokes import _runner
from prax.services import trajectory_service as ts

PAPER = "https://arxiv.org/abs/2610.12176"


def _call(name: str, args: dict, cid: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": cid}])


def _spoke_messages(*urls: str, reply: str = "The paper trains on its own traces.") -> list:
    msgs = [SystemMessage(content="You are the research agent."), HumanMessage(content="read it")]
    for i, url in enumerate(urls):
        msgs.append(_call("fetch_url_content", {"url": url}, f"c{i}"))
        msgs.append(ToolMessage(content="page text", name="fetch_url_content", tool_call_id=f"c{i}"))
    msgs.append(AIMessage(content=reply))
    return msgs


class TestFetchedUrls:
    def test_urls_a_tool_was_asked_to_open_in_order_deduplicated(self):
        msgs = _spoke_messages(PAPER, "https://github.com/x/y", "https://www.arxiv.org/abs/2610.12176/")
        assert fetched_urls(msgs) == [PAPER, "https://github.com/x/y"]

    @pytest.mark.parametrize("url", [
        "https://api.service.io/v1/data?api_key=abc123",
        "https://bucket.s3.amazonaws.com/f.pdf?X-Amz-Signature=deadbeef",
        "https://site.com/page?token=xyz",
        "http://localhost:8000/x", "https://example.com/doc",
    ])
    def test_credentials_local_and_placeholder_urls_are_left_out(self, url):
        assert fetched_urls(_spoke_messages(url)) == []

    def test_ordinary_queries_stay(self):
        url = "https://openreview.net/forum?id=abc"
        assert fetched_urls(_spoke_messages(url)) == [url]

    def test_tool_results_and_replies_are_not_fetches(self):
        msgs = [HumanMessage(content=f"see {PAPER}"),
                ToolMessage(content=f"result mentions {PAPER}", name="search", tool_call_id="s"),
                AIMessage(content=f"I think {PAPER}")]
        assert fetched_urls(msgs) == []


class TestSourcesFooter:
    def test_off_the_reply_is_unchanged(self, monkeypatch):
        from prax.settings import settings
        monkeypatch.setattr(settings, "spoke_sources_enabled", False)
        assert _runner._append_sources_read("answer", _spoke_messages(PAPER)) == "answer"

    def test_on_the_reply_ends_with_what_was_fetched(self, monkeypatch):
        from prax.settings import settings
        monkeypatch.setattr(settings, "spoke_sources_enabled", True)
        out = _runner._append_sources_read("answer", _spoke_messages(PAPER, "https://github.com/x/y"))
        assert out.startswith("answer")
        assert "[Sources this agent fetched]" in out
        assert f"- {PAPER}" in out and "- https://github.com/x/y" in out

    def test_links_the_reply_already_gives_are_not_repeated(self, monkeypatch):
        from prax.settings import settings
        monkeypatch.setattr(settings, "spoke_sources_enabled", True)
        assert _runner._append_sources_read(f"see {PAPER}", _spoke_messages(PAPER)) == f"see {PAPER}"

    def test_the_footer_is_bounded(self, monkeypatch):
        from prax.settings import settings
        monkeypatch.setattr(settings, "spoke_sources_enabled", True)
        urls = [f"https://site{i}.org/p" for i in range(25)]
        out = _runner._append_sources_read("a", _spoke_messages(*urls), limit=20)
        assert out.count("\n- https://") == 20 and "and 5 more" in out

    def test_it_removes_read_before_cites_false_flag(self, monkeypatch):
        """The case the footer exists for: a spoke read a page, summarized it
        without the link, and the orchestrator cites it. Without the footer the
        citation looks unread; with it, it was seen in the delegate's result."""
        from prax.settings import settings
        monkeypatch.setattr(settings, "spoke_sources_enabled", True)
        spoke_reply = _runner._append_sources_read(
            "It distils workflows into weights.", _spoke_messages(PAPER))
        reply = f"MASS ({PAPER}) distils workflows into weights."

        def turn(delegate_result: str) -> list:
            return [HumanMessage(content="what is MASS?"),
                    _call("delegate_research", {"task": "read the MASS paper"}, "d1"),
                    ToolMessage(content=delegate_result, name="delegate_research", tool_call_id="d1"),
                    AIMessage(content=reply)]

        assert audit_unread_citations(reply, turn("It distils workflows into weights.")) is not None
        assert audit_unread_citations(reply, turn(spoke_reply)) is None


class TestFullTrajectories:
    def test_full_chat_keeps_tool_calls_and_results(self):
        chat = ts.full_chat(_spoke_messages(PAPER))
        assert [m["role"] for m in chat] == ["system", "user", "assistant", "tool", "assistant"]
        call = chat[2]["tool_calls"][0]
        assert call["function"]["name"] == "fetch_url_content"
        assert json.loads(call["function"]["arguments"]) == {"url": PAPER}
        assert chat[3] == {"role": "tool", "tool_call_id": "c0", "name": "fetch_url_content",
                           "content": "page text"}

    def test_redaction_reaches_arguments_and_results(self):
        msgs = [HumanMessage(content="log in with hunter2"),
                _call("browser_fill", {"password": "hunter2"}, "c"),
                ToolMessage(content="typed hunter2", name="browser_fill", tool_call_id="c"),
                AIMessage(content="done")]
        chat = ts.full_chat(msgs, redact=lambda t: t.replace("hunter2", "***"))
        assert "hunter2" not in json.dumps(chat)

    def test_long_results_are_clipped(self):
        msgs = [ToolMessage(content="x" * (ts._MAX_MESSAGE_CHARS + 500), name="t", tool_call_id="c")]
        content = ts.full_chat(msgs)[0]["content"]
        assert len(content) < ts._MAX_MESSAGE_CHARS + 100 and "500 characters cut" in content

    def test_export_writes_one_linked_line(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ts, "_trajectories_dir", lambda _uid: tmp_path)
        ts.export_agent_trajectory("u1", role="spoke:research", task="read it",
                                   response="answer", messages=_spoke_messages(PAPER),
                                   ids={"trace_id": "t1", "span_id": "s1", "session_id": ""})
        rec = json.loads((tmp_path / ts.AGENTS_FILE).read_text())
        assert rec["metadata"]["role"] == "spoke:research"
        assert rec["metadata"]["trace_id"] == "t1" and "session_id" not in rec["metadata"]
        assert rec["metadata"]["outcome"] == "success" and rec["metadata"]["tool_count"] == 1

    def test_a_spoke_whose_tool_errored_is_marked(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ts, "_trajectories_dir", lambda _uid: tmp_path)
        msgs = [_call("t", {}, "c"), ToolMessage(content="Error: 404", name="t", tool_call_id="c"),
                AIMessage(content="couldn't")]
        ts.export_agent_trajectory("u1", role="spoke:browser", task="x", response="couldn't",
                                   messages=msgs)
        assert json.loads((tmp_path / ts.AGENTS_FILE).read_text())["metadata"]["outcome"] == "tool_failure"


class TestSpokeExportFlag:
    class _Span:
        trace_id, span_id = "t9", "s9"

        class ctx:  # noqa: N801 — mimics the span's attribute shape
            class graph:  # noqa: N801
                session_id = "sess"

    def test_off_nothing_is_written(self, tmp_path, monkeypatch):
        from prax.settings import settings
        monkeypatch.setattr(settings, "full_trajectories_enabled", False)
        monkeypatch.setattr(ts, "_trajectories_dir", lambda _uid: tmp_path)
        _runner._export_spoke_trajectory("research", "task", "answer", _spoke_messages(PAPER), self._Span)
        assert not (tmp_path / ts.AGENTS_FILE).exists()

    def test_on_the_spokes_conversation_is_recorded_with_its_ids(self, tmp_path, monkeypatch):
        from prax.agent.user_context import current_user_id
        from prax.settings import settings
        monkeypatch.setattr(settings, "full_trajectories_enabled", True)
        monkeypatch.setattr(ts, "_trajectories_dir", lambda _uid: tmp_path)
        token = current_user_id.set("u1")
        try:
            _runner._export_spoke_trajectory("research", "task", "answer", _spoke_messages(PAPER), self._Span)
        finally:
            current_user_id.reset(token)
        meta = json.loads((tmp_path / ts.AGENTS_FILE).read_text())["metadata"]
        assert meta["role"] == "spoke:research"
        assert (meta["trace_id"], meta["span_id"], meta["session_id"]) == ("t9", "s9", "sess")


def test_both_flags_default_off():
    from prax.settings import AppSettings
    fields = AppSettings.model_fields
    assert fields["spoke_sources_enabled"].default is False
    assert fields["full_trajectories_enabled"].default is False
