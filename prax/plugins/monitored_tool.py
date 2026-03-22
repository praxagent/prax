"""Wrapper that adds runtime failure monitoring to plugin-provided tools.

When a monitored tool raises an exception, the failure is recorded in the
plugin registry.  After N consecutive failures the plugin is auto-rolled
back to its previous version.
"""
import logging
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

logger = logging.getLogger(__name__)


class MonitoredTool:
    """Thin wrapper around a plugin tool that records successes and failures.

    NOT a BaseTool subclass — avoids Pydantic annotation recursion.
    Instead, it produces a ``StructuredTool`` that delegates invocation
    through the monitoring layer.
    """

    def __init__(self, inner: BaseTool, plugin_rel_path: str) -> None:
        self.inner = inner
        self.plugin_rel_path = plugin_rel_path

        # Build a StructuredTool that delegates to the inner tool with monitoring.
        def _monitored_run(**kwargs: Any) -> Any:
            from prax.plugins.loader import get_plugin_loader
            loader = get_plugin_loader()
            try:
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

        self.tool = StructuredTool.from_function(
            func=_monitored_run,
            name=inner.name,
            description=inner.description,
            args_schema=inner.args_schema,
        )

    @property
    def name(self) -> str:
        return self.inner.name

    @property
    def description(self) -> str:
        return self.inner.description


def wrap_with_monitoring(tool: BaseTool, rel_path: str) -> BaseTool:
    """Wrap a plugin tool with runtime monitoring.

    Returns a ``StructuredTool`` (a proper ``BaseTool`` subclass) so it
    integrates cleanly with LangChain agents.
    """
    monitored = MonitoredTool(inner=tool, plugin_rel_path=rel_path)
    return monitored.tool
