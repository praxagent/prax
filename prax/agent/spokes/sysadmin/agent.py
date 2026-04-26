"""Sysadmin spoke agent — plugin management, config, and self-maintenance.

Prax delegates system administration tasks here instead of keeping ~30 plugin/
config/source tools in the main orchestrator's tool list.  The sysadmin agent
can also sub-delegate to the self-improvement and plugin-fix agents for complex
code changes.
"""
from __future__ import annotations

import logging

from langchain_core.tools import tool

from prax.agent.spokes._runner import run_spoke
from prax.settings import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are the System Administration agent for {agent_name}.  You handle all
plugin management, configuration, source inspection, and self-maintenance tasks.

## What you can do

### Plugin management (imported / shared plugins)
- **plugin_import** — import a plugin from a git repository
- **plugin_import_list** — list all imported plugins
- **plugin_import_update** — pull latest version of a plugin
- **plugin_check_updates** / **plugin_check_all_updates** — check for upstream updates
- **plugin_import_remove** — remove an imported plugin
- **plugin_import_activate** — activate an imported plugin

### Plugin development (custom / workspace plugins)
- **plugin_read** / **plugin_write** / **plugin_test** — read, write, test plugins
- **plugin_activate** / **plugin_rollback** / **plugin_status** — lifecycle management
- **plugin_remove** / **plugin_catalog** / **plugin_list** — discovery and cleanup

### Prompt management
- **prompt_read** / **prompt_write** / **prompt_rollback** / **prompt_list** — manage
  system prompts and their versions

### LLM routing configuration
- **llm_config_read** / **llm_config_update** / **model_tiers_info** — view and change
  which models are used for which components

### Source introspection
- **source_read** / **source_list** / **source_grep** — read Prax's own source code
- **code_structure** — analyze a file's classes, functions, imports via AST parsing
- **code_dependencies** — map import dependencies across a directory
- **code_search_ast** — find functions/classes/methods by name using AST (not text grep)

### Workspace sync
- **workspace_set_remote** / **workspace_push** — configure and push to remote git
- **workspace_share_file** / **workspace_unshare_file** / **workspace_list_shares** — share workspace files via public ngrok URL (only on explicit user request, typically for SMS/Discord); list and revoke active shares

### Complex fixes (sub-delegation)
- **delegate_self_improve** — delegate bug fixes in Prax's own code to the
  self-improvement agent (has sandbox + codegen access)
- **delegate_plugin_fix** — delegate plugin creation/fixes to the plugin
  engineering agent (has sandbox access)

## Workflow
1. **Understand** the request — is it a simple config change, a plugin install,
   or something that needs code changes?
2. **Simple tasks**: handle directly with the appropriate tool.
3. **Complex tasks**: sub-delegate to delegate_self_improve (Prax bugs) or
   delegate_plugin_fix (plugin creation/fixes).
4. **Report** clearly what you did, what changed, and whether anything needs
   user attention (e.g. security warnings to acknowledge).

## Rules
- For plugin updates, always check for security warnings after updating.
- When changing LLM config, show the user what changed.
- For source changes, prefer delegate_self_improve over direct edits.
- Keep responses concise — the orchestrator will relay them to the user.
"""


# ---------------------------------------------------------------------------
# Tool assembly
# ---------------------------------------------------------------------------

def build_tools() -> list:
    """Return all tools available to the sysadmin spoke."""
    from prax.agent.ast_tools import build_ast_tools
    from prax.agent.plugin_fix_agent import delegate_plugin_fix
    from prax.agent.plugin_tools import build_plugin_tools
    from prax.agent.self_improve_agent import delegate_self_improve

    return (
        build_plugin_tools()
        + build_ast_tools()
        + [delegate_self_improve, delegate_plugin_fix]
    )


# ---------------------------------------------------------------------------
# Delegation function — this is what the orchestrator calls
# ---------------------------------------------------------------------------

@tool
def delegate_sysadmin(task: str) -> str:
    """Delegate a system administration task to the Sysadmin Agent.

    The Sysadmin Agent owns **Prax's own operational state**: plugin
    management, configuration, source inspection, activity logs, system
    status, self-maintenance, and code changes.

    Use this for:
    - **System state queries**: "What plugins are installed?", "Check system
      status", "Show me my recent activity logs", "What's the current config?",
      "How healthy is Prax right now?"
    - "Install this plugin from GitHub"
    - "Check if any plugins have updates"
    - "Update all plugins to latest"
    - "List my plugins and their versions"
    - "Remove the flight_search plugin"
    - "Change the model for subagent_research to gpt-4o"
    - "Read the source of prax/agent/tools.py"
    - "Create a new plugin that does X"
    - "Fix the broken weather plugin"
    - "Show me the system prompt"
    - "Push my workspace to the remote"

    **Important**: Questions like "what plugins are installed" or "show me
    the activity logs" are SYSTEM STATE questions — they belong here, NOT
    in delegate_memory (which is for facts about the user, not about Prax's
    own operational state). If the user asks about how Prax is configured
    or what tools are available, that's sysadmin territory.

    Do NOT use this for user-facing tasks like research, browsing, or content
    creation — those go to other spokes or the orchestrator directly.

    Args:
        task: A clear description of the system administration task.
              Include plugin names, URLs, config keys, or file paths as needed.
    """
    prompt = SYSTEM_PROMPT.format(agent_name=settings.agent_name)
    return run_spoke(
        task=task,
        system_prompt=prompt,
        tools=build_tools(),
        config_key="subagent_sysadmin",
        role_name="Sysadmin",
        channel=None,  # sysadmin results go back to orchestrator, not a channel
        recursion_limit=80,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def build_spoke_tools() -> list:
    """Return the delegation tool for the main agent."""
    return [delegate_sysadmin]
