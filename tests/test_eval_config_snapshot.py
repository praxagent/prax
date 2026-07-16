"""Key-free tests for the eval reproducibility snapshot.

The load-bearing guarantee: the snapshot pins every feature flag + models +
commit (so a published result is reproducible) but NEVER captures a secret.
"""
from __future__ import annotations

import json

from prax.eval.config_snapshot import eval_config_snapshot


def test_snapshot_shape_and_captures_flags():
    s = eval_config_snapshot()
    assert set(s) >= {"git_commit", "flags", "run", "env"}
    assert isinstance(s["flags"], dict) and s["flags"], "should capture flags"
    # Every flag value is a bool, keyed by its SCREAMING_CASE env alias.
    assert all(isinstance(v, bool) for v in s["flags"].values())
    assert all(k == k.upper() for k in s["flags"])
    # A known behaviour flag is present so a reproducer can match it.
    assert "TOOL_ECONOMY_ENABLED" in s["flags"]


def test_snapshot_never_leaks_a_secret(monkeypatch):
    # Plant a distinctive secret in a real secret-bearing setting; it must not
    # appear anywhere in the serialized snapshot (whitelist excludes it).
    from prax.settings import settings

    sentinel = "sk-LEAKCANARY-do-not-publish-1234567890"
    monkeypatch.setattr(settings, "serper_dev_api_key", sentinel, raising=False)
    monkeypatch.setattr(settings, "openrouter_api_key", sentinel, raising=False)

    blob = json.dumps(eval_config_snapshot())
    assert sentinel not in blob, "snapshot leaked an API key value"
