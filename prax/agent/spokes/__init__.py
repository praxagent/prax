"""Spoke agents — focused sub-agents that the orchestrator delegates to.

Each spoke lives in its own subdirectory and exports a ``build_spoke_tools()``
function that returns the delegation tool(s) for the main agent.

To add a new spoke:

1. Create ``prax/agent/spokes/<name>/``
2. Add ``agent.py`` with:
   - ``SYSTEM_PROMPT`` — the spoke's role description and instructions
   - ``build_tools()`` — returns the tools the spoke can use
   - ``delegate_<name>()`` decorated with ``@tool`` — the delegation entry point
   - ``build_spoke_tools()`` — returns ``[delegate_<name>]``
3. Import and register in this file's ``build_all_spoke_tools()``
4. Remove the spoke's tools from ``tools.py:build_default_tools()``
   (replaced by the single delegation tool)

See ``prax/agent/spokes/browser/`` for the reference implementation.
"""
from __future__ import annotations


def build_all_spoke_tools() -> list:
    """Return delegation tools from all registered spokes."""
    from prax.agent.spokes.browser import build_spoke_tools as browser_spoke
    from prax.agent.spokes.content import build_spoke_tools as content_spoke
    from prax.agent.spokes.finetune import build_spoke_tools as finetune_spoke
    from prax.agent.spokes.knowledge import build_spoke_tools as knowledge_spoke
    from prax.agent.spokes.sandbox import build_spoke_tools as sandbox_spoke
    from prax.agent.spokes.sysadmin import build_spoke_tools as sysadmin_spoke

    return [
        *browser_spoke(),
        *content_spoke(),
        *finetune_spoke(),
        *knowledge_spoke(),
        *sandbox_spoke(),
        *sysadmin_spoke(),
    ]
