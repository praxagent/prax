"""SANDBOX_EXEC_TIMEOUT_ENFORCED reaches the sandbox config, and is off by default."""
from __future__ import annotations

import prax.settings as prax_settings
from prax.services import sandbox_bridge


def test_off_by_default():
    field = type(prax_settings.settings).model_fields["sandbox_exec_timeout_enforced"]
    assert field.default is False


def test_flag_is_forwarded_to_the_sandbox_config(monkeypatch):
    # build_config imports settings lazily; patch the live object, not a
    # reference captured at this module's import.
    settings = prax_settings.settings
    monkeypatch.setattr(settings, "sandbox_exec_timeout_enforced", True)
    assert sandbox_bridge.build_config().enforce_exec_timeout is True
    monkeypatch.setattr(settings, "sandbox_exec_timeout_enforced", False)
    assert sandbox_bridge.build_config().enforce_exec_timeout is False
