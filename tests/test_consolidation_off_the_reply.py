"""Turn-end consolidation must not hold the user's reply (2026-10-02: 22m43s)."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from prax.services import memory_service as ms


@pytest.fixture()
def slow_consolidation(monkeypatch):
    started, release = threading.Event(), threading.Event()

    class _Svc:
        calls = 0

        def consolidate(self, user_id):
            _Svc.calls += 1
            started.set()
            release.wait(5)
            return SimpleNamespace(entities_upserted=1, relations_upserted=0,
                                   memories_created=0, memories_failed=0)

    monkeypatch.setattr(ms, "get_memory_service", lambda: _Svc())
    monkeypatch.setattr(ms, "_CONSOLIDATE_EVERY_N_TURNS", 1)
    monkeypatch.setattr(ms, "_consolidation_turns_since", {})
    monkeypatch.setattr(ms, "_consolidating", set())
    yield _Svc, started, release
    release.set()


def test_the_reply_is_not_held(slow_consolidation, monkeypatch):
    svc, started, release = slow_consolidation
    monkeypatch.setattr(ms.settings, "memory_consolidation_in_background", True)
    t0 = time.monotonic()
    assert ms.maybe_consolidate("u1") is True
    assert time.monotonic() - t0 < 1.0          # returned while consolidation runs
    assert started.wait(2)
    release.set()


def test_a_second_trigger_while_running_is_skipped(slow_consolidation, monkeypatch):
    svc, started, release = slow_consolidation
    monkeypatch.setattr(ms.settings, "memory_consolidation_in_background", True)
    assert ms.maybe_consolidate("u1") is True
    assert started.wait(2)
    assert ms.maybe_consolidate("u1") is False   # one run per user at a time
    release.set()
    deadline = time.monotonic() + 3
    while "u1" in ms._consolidating and time.monotonic() < deadline:
        time.sleep(0.01)
    assert "u1" not in ms._consolidating         # released when the run ends
    assert svc.calls == 1


def test_in_line_mode_still_works(slow_consolidation, monkeypatch):
    svc, started, release = slow_consolidation
    monkeypatch.setattr(ms.settings, "memory_consolidation_in_background", False)
    release.set()
    assert ms.maybe_consolidate("u1") is True
    assert svc.calls == 1 and "u1" not in ms._consolidating


def test_a_failing_run_releases_the_user(monkeypatch):
    class _Boom:
        def consolidate(self, user_id):
            raise RuntimeError("extractor down")

    monkeypatch.setattr(ms, "get_memory_service", lambda: _Boom())
    monkeypatch.setattr(ms, "_CONSOLIDATE_EVERY_N_TURNS", 1)
    monkeypatch.setattr(ms, "_consolidation_turns_since", {})
    monkeypatch.setattr(ms, "_consolidating", set())
    monkeypatch.setattr(ms.settings, "memory_consolidation_in_background", False)
    assert ms.maybe_consolidate("u1") is False
    assert "u1" not in ms._consolidating
