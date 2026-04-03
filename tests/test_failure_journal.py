"""Tests for the failure journal."""
from __future__ import annotations

import json

import pytest

from prax.services.memory.failure_journal import (
    FailureCase,
    _auto_classify,
    get_failure_stats,
    get_failures,
    record_failure,
    resolve_failure,
)


# ---------------------------------------------------------------------------
# FailureCase dataclass
# ---------------------------------------------------------------------------


class TestFailureCase:
    def test_auto_generates_id(self):
        case = FailureCase(user_id="u1", user_input="hello")
        assert len(case.id) == 16
        assert case.created_at
        assert case.importance == 0.8

    def test_preserves_explicit_fields(self):
        case = FailureCase(
            id="custom",
            user_id="u1",
            failure_category="hallucination",
            resolved=True,
        )
        assert case.id == "custom"
        assert case.failure_category == "hallucination"
        assert case.resolved is True


# ---------------------------------------------------------------------------
# Auto-classification
# ---------------------------------------------------------------------------


class TestAutoClassify:
    def test_wrong_tool_detected(self):
        assert _auto_classify("", "", "used the wrong tool") == "wrong_tool"

    def test_hallucination_detected(self):
        assert _auto_classify("", "the file doesn't exist", "it made up a URL") == "hallucination"

    def test_asked_instead_of_acting(self):
        assert _auto_classify("", "", "just do it, stop asking me") == "asked_instead_of_acting"

    def test_incomplete_detected(self):
        assert _auto_classify("", "", "it didn't finish the task, left out half") == "incomplete"

    def test_no_signals_returns_empty(self):
        assert _auto_classify("hello", "hi there", "thanks") == ""

    def test_too_slow_detected(self):
        assert _auto_classify("", "", "it took forever to respond") == "too_slow"


# ---------------------------------------------------------------------------
# record_failure / get_failures
# ---------------------------------------------------------------------------


class TestRecordAndGet:
    def test_record_and_retrieve(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "prax.services.memory.failure_journal._journal_dir",
            lambda: tmp_path,
        )
        # Suppress Neo4j and Qdrant (not available in tests)
        monkeypatch.setattr(
            "prax.services.memory.failure_journal._store_neo4j",
            lambda c: None,
        )
        monkeypatch.setattr(
            "prax.services.memory.failure_journal._store_qdrant",
            lambda c: None,
        )

        case = record_failure(
            user_id="u1",
            user_input="make me a presentation",
            agent_output="I don't know how to do that",
            trace_id="trace123",
            feedback_comment="you have the txt2presentation tool!",
        )

        assert case.id
        assert case.failure_category == ""  # no strong signal in this feedback
        assert case.resolved is False

        cases = get_failures(user_id="u1")
        assert len(cases) == 1
        assert cases[0].user_input == "make me a presentation"
        assert cases[0].trace_id == "trace123"

    def test_extracts_tools_from_graph_snapshot(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "prax.services.memory.failure_journal._journal_dir",
            lambda: tmp_path,
        )
        monkeypatch.setattr(
            "prax.services.memory.failure_journal._store_neo4j",
            lambda c: None,
        )
        monkeypatch.setattr(
            "prax.services.memory.failure_journal._store_qdrant",
            lambda c: None,
        )

        graph = {
            "trace_id": "t1",
            "nodes": [
                {"name": "orchestrator", "spoke_or_category": "agent"},
                {"name": "background_search_tool", "spoke_or_category": "tool"},
                {"name": "delegate_browser", "spoke_or_category": "tool"},
            ],
        }
        case = record_failure(
            user_id="u1",
            user_input="search google",
            agent_output="here are results",
            graph_snapshot=graph,
            feedback_comment="wrong tool",
        )
        assert "background_search_tool" in case.tools_involved
        assert "delegate_browser" in case.tools_involved
        assert case.failure_category == "wrong_tool"

    def test_auto_classifies_hallucination(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "prax.services.memory.failure_journal._journal_dir",
            lambda: tmp_path,
        )
        monkeypatch.setattr(
            "prax.services.memory.failure_journal._store_neo4j",
            lambda c: None,
        )
        monkeypatch.setattr(
            "prax.services.memory.failure_journal._store_qdrant",
            lambda c: None,
        )

        case = record_failure(
            user_id="u1",
            user_input="what's the weather",
            agent_output="It's 72 degrees in Narnia",
            feedback_comment="that's not true, it made up the temperature",
        )
        assert case.failure_category == "hallucination"


# ---------------------------------------------------------------------------
# resolve_failure
# ---------------------------------------------------------------------------


class TestResolveFailure:
    def test_resolve_updates_case(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "prax.services.memory.failure_journal._journal_dir",
            lambda: tmp_path,
        )
        monkeypatch.setattr(
            "prax.services.memory.failure_journal._store_neo4j",
            lambda c: None,
        )
        monkeypatch.setattr(
            "prax.services.memory.failure_journal._store_qdrant",
            lambda c: None,
        )

        case = record_failure(
            user_id="u1",
            user_input="do X",
            agent_output="wrong",
            feedback_comment="bad",
        )
        assert not case.resolved

        # Suppress Neo4j for resolve
        monkeypatch.setattr(
            "prax.services.memory.failure_journal._session",
            lambda: None,
            raising=False,
        )

        updated = resolve_failure(case.id, "Fixed the prompt to handle X correctly")
        assert updated

        cases = get_failures(user_id="u1")
        assert cases[0].resolved is True
        assert "Fixed the prompt" in cases[0].resolution


# ---------------------------------------------------------------------------
# get_failure_stats
# ---------------------------------------------------------------------------


class TestFailureStats:
    def test_stats_with_categories_and_tools(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "prax.services.memory.failure_journal._journal_dir",
            lambda: tmp_path,
        )
        monkeypatch.setattr(
            "prax.services.memory.failure_journal._store_neo4j",
            lambda c: None,
        )
        monkeypatch.setattr(
            "prax.services.memory.failure_journal._store_qdrant",
            lambda c: None,
        )

        record_failure(
            user_id="u1", user_input="q1", agent_output="a1",
            feedback_comment="used the wrong tool",
        )
        record_failure(
            user_id="u1", user_input="q2", agent_output="a2",
            feedback_comment="it hallucinated a URL that doesn't exist",
        )
        record_failure(
            user_id="u1", user_input="q3", agent_output="a3",
            feedback_comment="used the wrong tool again",
        )

        stats = get_failure_stats(user_id="u1")
        assert stats["total"] == 3
        assert stats["unresolved"] == 3
        assert stats["categories"]["wrong_tool"] == 2
        assert stats["categories"]["hallucination"] == 1

    def test_empty_stats(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "prax.services.memory.failure_journal._journal_dir",
            lambda: tmp_path,
        )
        stats = get_failure_stats(user_id="u1")
        assert stats["total"] == 0
        assert stats["resolution_rate"] == 0.0
