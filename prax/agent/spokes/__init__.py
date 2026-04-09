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
    """Return delegation tools from all registered spokes.

    NOTE on memory: the memory spoke is intentionally NOT in this list.
    The medium-tier orchestrator was over-routing everything to
    delegate_memory as a catch-all drain (coverage harness found 15/36
    turns landing in memory, many misrouted). Memory writes now happen
    AUTOMATICALLY via the consolidation hook in the orchestrator's
    turn-end block, so Prax doesn't need to manually invoke a memory
    spoke for routine storage. Memory READS happen via the memory
    context injection at the start of every turn (STM scratchpad + LTM
    recall already in the system prompt). If explicit memory commands
    are ever needed again, enable delegate_memory via LLM routing
    config or put it behind a higher-tier orchestrator model.
    """
    from prax.agent.spokes.browser import build_spoke_tools as browser_spoke
    from prax.agent.spokes.content import build_spoke_tools as content_spoke
    from prax.agent.spokes.course import build_spoke_tools as course_spoke
    from prax.agent.spokes.finetune import build_spoke_tools as finetune_spoke
    from prax.agent.spokes.knowledge import build_spoke_tools as knowledge_spoke
    from prax.agent.spokes.sandbox import build_spoke_tools as sandbox_spoke
    from prax.agent.spokes.scheduler import build_spoke_tools as scheduler_spoke
    from prax.agent.spokes.sysadmin import build_spoke_tools as sysadmin_spoke
    from prax.agent.spokes.workspace import build_spoke_tools as workspace_spoke

    return [
        *browser_spoke(),
        *content_spoke(),
        *course_spoke(),
        *finetune_spoke(),
        *knowledge_spoke(),
        *sandbox_spoke(),
        *scheduler_spoke(),
        *sysadmin_spoke(),
        *workspace_spoke(),
    ]
