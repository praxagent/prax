"""Turn-end consolidation must not hold the user's reply (2026-10-02: 22m43s),
and — now that it runs in the background — must never run twice at once for
one user, whoever asks (the turn-end trigger or the memory_consolidate tool)."""
from __future__ import annotations

import threading
import time

import pytest

from prax.services import memory_service as ms
from prax.services.memory import consolidation
from prax.services.memory.models import ConsolidationResult


@pytest.fixture()
def slow_consolidation(monkeypatch):
    """The REAL MemoryService.consolidate (which owns the one-run-per-user
    guard) over a consolidate_user that blocks until released."""
    started, release = threading.Event(), threading.Event()
    calls = []

    def _consolidate_user(user_id):
        calls.append(user_id)
        started.set()
        release.wait(5)
        return ConsolidationResult(entities_upserted=1)

    svc = ms.MemoryService.__new__(ms.MemoryService)   # no store connections
    svc._available = True
    monkeypatch.setattr(consolidation, "consolidate_user", _consolidate_user)
    monkeypatch.setattr(ms, "get_memory_service", lambda: svc)
    monkeypatch.setattr(ms, "_CONSOLIDATE_EVERY_N_TURNS", 1)
    monkeypatch.setattr(ms, "_consolidation_turns_since", {})
    monkeypatch.setattr(ms, "_consolidating", set())
    yield svc, calls, started, release
    release.set()


def _wait_idle(user_id, timeout=3.0):
    deadline = time.monotonic() + timeout
    while ms.is_consolidating(user_id) and time.monotonic() < deadline:
        time.sleep(0.01)


def test_the_reply_is_not_held(slow_consolidation, monkeypatch):
    svc, calls, started, release = slow_consolidation
    monkeypatch.setattr(ms.settings, "memory_consolidation_in_background", True)
    t0 = time.monotonic()
    assert ms.maybe_consolidate("u1") is True
    assert time.monotonic() - t0 < 1.0          # returned while consolidation runs
    assert started.wait(2)
    release.set()
    _wait_idle("u1")
    assert not ms.is_consolidating("u1")


def test_a_second_trigger_while_running_is_skipped_and_retried_next_turn(slow_consolidation, monkeypatch):
    svc, calls, started, release = slow_consolidation
    monkeypatch.setattr(ms.settings, "memory_consolidation_in_background", True)
    assert ms.maybe_consolidate("u1") is True
    assert started.wait(2)
    assert ms.maybe_consolidate("u1") is False   # busy: skipped
    assert ms._consolidation_turns_since.get("u1", 0) == 1   # not reset: next turn retries
    release.set()
    _wait_idle("u1")
    assert calls == ["u1"]


def test_the_manual_tool_does_not_start_a_second_run(slow_consolidation, monkeypatch):
    """The turn-end run is in the background, so memory_consolidate in the next
    turn can arrive mid-run. It must not extract the same batches again."""
    svc, calls, started, release = slow_consolidation
    monkeypatch.setattr(ms.settings, "memory_consolidation_in_background", True)
    assert ms.maybe_consolidate("u1") is True
    assert started.wait(2)

    from prax.agent import memory_tools
    monkeypatch.setattr(memory_tools, "_uid", lambda: "u1")
    out = memory_tools.memory_consolidate.invoke({})
    assert "already running" in out
    assert calls == ["u1"]                        # still one run
    release.set()
    _wait_idle("u1")


def test_another_user_is_not_blocked(slow_consolidation, monkeypatch):
    svc, calls, started, release = slow_consolidation
    monkeypatch.setattr(ms.settings, "memory_consolidation_in_background", True)
    assert ms.maybe_consolidate("u1") is True
    assert started.wait(2)
    assert ms.is_consolidating("u1") and not ms.is_consolidating("u2")
    release.set()
    _wait_idle("u1")


def test_in_line_mode_still_works(slow_consolidation, monkeypatch):
    svc, calls, started, release = slow_consolidation
    monkeypatch.setattr(ms.settings, "memory_consolidation_in_background", False)
    release.set()
    assert ms.maybe_consolidate("u1") is True
    assert calls == ["u1"] and not ms.is_consolidating("u1")


def test_a_failing_run_releases_the_user(monkeypatch):
    def _boom(user_id):
        raise RuntimeError("extractor down")

    svc = ms.MemoryService.__new__(ms.MemoryService)
    svc._available = True
    monkeypatch.setattr(consolidation, "consolidate_user", _boom)
    monkeypatch.setattr(ms, "_consolidating", set())
    result = svc.consolidate("u1")
    assert isinstance(result, ConsolidationResult) and not result.already_running
    assert not ms.is_consolidating("u1")
