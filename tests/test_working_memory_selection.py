"""What rides in the prompt as working memory, and why.

Prax injects a `## Working Memory (Scratchpad)` block every turn — it already
had MemGPT's self-edited working context. What it did NOT have was any use of
`importance`: selection was `stm_entries[-5:]`, the five most recently written.

So a fact deliberately saved at importance 0.9 fell out of the prompt as soon as
five pieces of trivia were written after it, and nothing told the agent it had
gone. The module docstring cites Park et al. — "relevance + recency + importance"
— and only recency was implemented here.
"""
from __future__ import annotations

import pytest

from prax.services import memory_service as ms


class _Entry:
    def __init__(self, key, content, importance, created_at):
        self.key = key
        self.content = content
        self.importance = importance
        self.created_at = created_at


def _install(monkeypatch, entries):
    monkeypatch.setattr("prax.services.memory.stm.stm_read",
                        lambda uid, key=None: entries, raising=False)
    # Keep the test to the STM block; LTM needs Qdrant.
    monkeypatch.setattr(ms.MemoryService, "_ltm_context",
                        lambda self, *a, **k: "", raising=False)


@pytest.fixture
def svc():
    return ms.MemoryService() if hasattr(ms, "MemoryService") else ms.get_memory_service()


def test_an_important_fact_outranks_newer_trivia(monkeypatch, svc):
    entries = [_Entry("prefers-metric", "always use metric units", 0.95, 1000)]
    entries += [_Entry(f"chatter{i}", f"noise {i}", 0.1, 2000 + i) for i in range(6)]
    _install(monkeypatch, entries)

    out = svc.build_memory_context("u1", "anything")

    assert "prefers-metric" in out, (
        "a 0.95-importance fact was pushed out by six pieces of 0.1 trivia")


def test_equal_importance_still_prefers_recent(monkeypatch, svc):
    entries = [_Entry(f"k{i}", f"v{i}", 0.5, 1000 + i) for i in range(8)]
    _install(monkeypatch, entries)

    out = svc.build_memory_context("u1", "anything")

    assert "k7" in out, "ties should break on recency, as before"
    assert "k0" not in out


def test_truncation_is_announced_not_silent(monkeypatch, svc):
    """A silently cut fact reads as a complete one."""
    _install(monkeypatch, [_Entry("long", "x" * 500, 0.9, 1000)])

    out = svc.build_memory_context("u1", "anything")

    assert "truncated" in out
    assert "stm_read('long')" in out, "tell it how to get the rest"


def test_the_agent_is_told_something_was_left_out(monkeypatch, svc):
    """Otherwise the block looks like the whole scratchpad."""
    entries = [_Entry(f"k{i}", f"v{i}", 0.5, 1000 + i) for i in range(9)]
    _install(monkeypatch, entries)

    out = svc.build_memory_context("u1", "anything")

    assert "4 more scratchpad entries not shown" in out


def test_an_empty_scratchpad_adds_no_heading(monkeypatch, svc):
    _install(monkeypatch, [])
    assert "Working Memory" not in svc.build_memory_context("u1", "anything")
