"""Wrapper that adds runtime failure monitoring to plugin-provided tools.

When a monitored tool raises an exception, the failure is recorded in the
plugin registry.  After N consecutive failures the plugin is auto-rolled
back to its previous version.

For IMPORTED plugins, additional enforcement layers are applied:
  - Per-plugin call budget (prevents runaway recursion)
  - OS-level resource limits (CPU, memory, file descriptors)
  - Audit-hook and import-blocker context (via sandbox_guard)
"""
from __future__ import annotations

import contextvars
import logging
import threading
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from prax.plugins.registry import PluginTrust

logger = logging.getLogger(__name__)

# ContextVar identifying the plugin currently executing a tool invocation.
# The capabilities gateway and sandbox guard read these to enforce per-plugin policy.
current_plugin_rel_path: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_plugin_rel_path", default=None,
)
current_plugin_trust: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_plugin_trust", default=None,
)

# ---------------------------------------------------------------------------
# Per-plugin call budget — framework-enforced, not bypassable by plugins
# ---------------------------------------------------------------------------

_call_counts: dict[str, int] = {}  # plugin_rel_path -> calls this message
_call_counts_lock = threading.Lock()


def reset_plugin_call_counts() -> None:
    """Reset all plugin call counters.  Call once per user message."""
    with _call_counts_lock:
        _call_counts.clear()


def _increment_call_count(rel_path: str) -> int:
    """Increment and return the call count for *rel_path*."""
    with _call_counts_lock:
        _call_counts[rel_path] = _call_counts.get(rel_path, 0) + 1
        return _call_counts[rel_path]


# ---------------------------------------------------------------------------
# MonitoredTool wrapper
# ---------------------------------------------------------------------------

class MonitoredTool:
    """Thin wrapper around a plugin tool that records successes and failures.

    NOT a BaseTool subclass — avoids Pydantic annotation recursion.
    Instead, it produces a ``StructuredTool`` that delegates invocation
    through the monitoring layer.
    """

    def __init__(
        self,
        inner: BaseTool,
        plugin_rel_path: str,
        trust_tier: str = PluginTrust.BUILTIN,
    ) -> None:
        self.inner = inner
        self.plugin_rel_path = plugin_rel_path
        self.trust_tier = trust_tier

        # Build a StructuredTool that delegates to the inner tool with monitoring.
        def _monitored_run(**kwargs: Any) -> Any:
            from prax.plugins.loader import get_plugin_loader
            loader = get_plugin_loader()

            # Set context vars so downstream code can identify the calling plugin.
            path_token = current_plugin_rel_path.set(plugin_rel_path)
            trust_token = current_plugin_trust.set(trust_tier)
            try:
                # --- Enforcement layers for IMPORTED plugins ---
                if trust_tier == PluginTrust.IMPORTED:
                    return self._run_sandboxed(inner, kwargs, loader)

                # BUILTIN / WORKSPACE — run directly.
                result = inner.invoke(kwargs if kwargs else {})
                loader.record_tool_success(inner.name)
                return result
            except Exception as exc:
                rolled_back = loader.record_tool_failure(inner.name)
                if rolled_back:
                    logger.warning(
                        "Plugin tool %s auto-rolled back after failure: %s",
                        inner.name, exc,
                    )
                raise
            finally:
                current_plugin_rel_path.reset(path_token)
                current_plugin_trust.reset(trust_token)

        self.tool = StructuredTool.from_function(
            func=_monitored_run,
            name=inner.name,
            description=inner.description,
            args_schema=inner.args_schema,
        )

    def _run_sandboxed(self, inner: BaseTool, kwargs: dict, loader: Any) -> Any:
        """Execute an IMPORTED plugin tool with full sandbox enforcement."""
        from prax.plugins.policy import get_policy
        from prax.plugins.sandbox_guard import install_all_guards, resource_limits

        # Ensure audit hook and import blocker are installed.
        install_all_guards()

        policy = get_policy(self.trust_tier)

        # 1. Check per-plugin call budget.
        count = _increment_call_count(self.plugin_rel_path)
        if count > policy.max_tool_calls_per_message:
            logger.warning(
                "SECURITY: Plugin '%s' exceeded call budget (%d/%d) — blocked",
                self.plugin_rel_path, count, policy.max_tool_calls_per_message,
            )
            raise PermissionError(
                f"Plugin '{self.plugin_rel_path}' exceeded its call budget "
                f"of {policy.max_tool_calls_per_message} calls per message."
            )

        # 2. Apply OS-level resource limits.
        with resource_limits(
            cpu_seconds=policy.cpu_seconds_per_call,
            memory_bytes=policy.memory_bytes_per_call,
        ):
            result = inner.invoke(kwargs if kwargs else {})

        loader.record_tool_success(inner.name)
        return result

    @property
    def name(self) -> str:
        return self.inner.name

    @property
    def description(self) -> str:
        return self.inner.description


def wrap_with_monitoring(
    tool: BaseTool,
    rel_path: str,
    *,
    trust_tier: str = PluginTrust.BUILTIN,
) -> BaseTool:
    """Wrap a plugin tool with runtime monitoring.

    Returns a ``StructuredTool`` (a proper ``BaseTool`` subclass) so it
    integrates cleanly with LangChain agents.
    """
    monitored = MonitoredTool(inner=tool, plugin_rel_path=rel_path, trust_tier=trust_tier)
    return monitored.tool
