"""Key-free tests for the benchmark registry + live-executor wiring."""
from __future__ import annotations

import pytest

from prax.eval.benchmarks import (
    ADAPTER_NAMES,
    get_adapter,
    live_orchestrator_replay,
)


def test_registry_returns_each_adapter():
    for name in ADAPTER_NAMES:
        ad = get_adapter(name)
        assert ad.name == name
        assert ad.cases()  # non-empty seed set


def test_unknown_adapter_raises():
    with pytest.raises(ValueError):
        get_adapter("does-not-exist")


def test_live_replay_factory_is_callable_without_running():
    # building the closure must not invoke the orchestrator (no keys in CI)
    fn = live_orchestrator_replay(tier="low")
    assert callable(fn)
