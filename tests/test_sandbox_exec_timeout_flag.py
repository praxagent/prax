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


def test_an_older_prax_sandbox_without_the_field_still_builds(monkeypatch):
    # Prax and prax-sandbox deploy separately; a missing field must not take
    # every sandbox tool down with a TypeError.
    import dataclasses

    @dataclasses.dataclass
    class OldConfig:
        host: str = ""
        image: str = ""
        persistent: bool = True
        workspace_dir: str = ""
        default_model: str = ""
        max_concurrent: int = 1
        max_rounds: int = 1
        timeout: int = 1
        anthropic_key: str | None = None
        openai_key: str | None = None
        daemon_url: str | None = None
        daemon_token: str | None = None
        tls_verify: object = True
        client_cert: str | None = None
        client_key: str | None = None
        on_output: object = None
        resolve_workspace: object = None
        commit: object = None

    monkeypatch.setattr(sandbox_bridge, "SandboxConfig", OldConfig)
    monkeypatch.setattr(prax_settings.settings, "sandbox_exec_timeout_enforced", True)
    cfg = sandbox_bridge.build_config()
    assert not hasattr(cfg, "enforce_exec_timeout")
