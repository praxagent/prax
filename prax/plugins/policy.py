"""Per-tier capability policies for the plugin security sandbox.

Each trust tier (BUILTIN, WORKSPACE, IMPORTED) gets a frozen policy
that controls what the plugin is allowed to do.  The capabilities
gateway (:mod:`prax.plugins.capabilities`) consults these policies
before servicing any request from a plugin.
"""
from __future__ import annotations

from dataclasses import dataclass

from prax.plugins.registry import PluginTrust


@dataclass(frozen=True)
class PluginPolicy:
    """Immutable policy governing what a plugin may access."""

    can_access_env: bool = False
    can_access_settings: bool = False
    can_make_http: bool = True
    can_run_commands: bool = True
    can_use_llm: bool = True
    max_http_requests_per_invocation: int = 50
    max_tool_calls_per_message: int = 10
    cpu_seconds_per_call: int = 30
    memory_bytes_per_call: int = 512 * 1024 * 1024  # 512 MB


TRUST_POLICIES: dict[str, PluginPolicy] = {
    PluginTrust.BUILTIN: PluginPolicy(
        can_access_env=True,
        can_access_settings=True,
    ),
    PluginTrust.WORKSPACE: PluginPolicy(),
    PluginTrust.IMPORTED: PluginPolicy(),
}


def get_policy(trust_tier: str) -> PluginPolicy:
    """Return the policy for *trust_tier*, defaulting to the most restrictive."""
    return TRUST_POLICIES.get(trust_tier, TRUST_POLICIES[PluginTrust.IMPORTED])
