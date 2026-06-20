"""Tests for M6: selective prompt assembly + intent-clarification gate."""
from __future__ import annotations

from prax.agent.prompt_selection import select_sections

_PROMPT = """\
## Soul
You are Prax.

## Math & LaTeX
Render math with LaTeX.

## Teaching — the Faculty
Use the faculty for lessons.

## Truthfulness — MANDATORY
Never fabricate.

## Document Pipelines
Build PDFs and slides here.
"""


class TestSelectSections:
    def test_drops_irrelevant_optional_sections(self):
        out = select_sections(_PROMPT, "what's the capital of France?")
        # Core sections always kept.
        assert "## Soul" in out
        assert "## Truthfulness — MANDATORY" in out
        # Topic-specific optional sections dropped (no trigger words present).
        assert "## Math & LaTeX" not in out
        assert "## Teaching — the Faculty" not in out
        assert "## Document Pipelines" not in out

    def test_keeps_section_when_trigger_present(self):
        out = select_sections(_PROMPT, "help me solve this calculus integral")
        assert "## Math & LaTeX" in out          # 'calculus'/'integral' triggers
        assert "## Teaching — the Faculty" not in out  # still irrelevant

    def test_keeps_everything_relevant(self):
        out = select_sections(_PROMPT, "build me a pdf lesson with equations")
        # 'pdf' → document pipelines; 'lesson' → teaching; 'equations' → math
        assert "## Document Pipelines" in out
        assert "## Teaching — the Faculty" in out
        assert "## Math & LaTeX" in out

    def test_preamble_before_first_header_preserved(self):
        prompt = "intro text\n\n## Math & LaTeX\nmath\n"
        out = select_sections(prompt, "hello")
        assert out.startswith("intro text")
        assert "## Math & LaTeX" not in out

    def test_no_optional_sections_is_noop(self):
        prompt = "## Soul\nhi\n\n## Truthfulness\nno lies\n"
        assert select_sections(prompt, "anything") == prompt


class TestIntentClarification:
    def test_disabled_returns_none(self, monkeypatch):
        from prax.agent.orchestrator import ConversationAgent
        from prax.settings import settings
        monkeypatch.setattr(settings, "intent_clarification_enabled", False)
        assert ConversationAgent._maybe_clarify("delete everything") is None

    def test_scheduled_input_skipped(self, monkeypatch):
        from prax.agent.orchestrator import ConversationAgent
        from prax.settings import settings
        monkeypatch.setattr(settings, "intent_clarification_enabled", True)
        assert ConversationAgent._maybe_clarify("[SCHEDULED_TASK] daily briefing") is None

    def test_proceed_response_returns_none(self, monkeypatch):
        from prax.agent.orchestrator import ConversationAgent
        from prax.settings import settings
        monkeypatch.setattr(settings, "intent_clarification_enabled", True)

        class _Resp:
            content = "PROCEED"
        monkeypatch.setattr("prax.agent.llm_factory.build_llm",
                            lambda **kw: type("L", (), {"invoke": lambda self, m: _Resp()})())
        assert ConversationAgent._maybe_clarify("what time is it?") is None

    def test_question_returned(self, monkeypatch):
        from prax.agent.orchestrator import ConversationAgent
        from prax.settings import settings
        monkeypatch.setattr(settings, "intent_clarification_enabled", True)

        class _Resp:
            content = "Which database did you mean — staging or production?\n(extra)"
        monkeypatch.setattr("prax.agent.llm_factory.build_llm",
                            lambda **kw: type("L", (), {"invoke": lambda self, m: _Resp()})())
        q = ConversationAgent._maybe_clarify("drop the users table")
        assert q == "Which database did you mean — staging or production?"
