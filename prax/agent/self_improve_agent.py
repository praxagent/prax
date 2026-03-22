"""Self-improvement sub-agent — diagnoses and fixes bugs in Prax's own code.

Prax delegates to this agent when it detects a bug or the user reports one.
The agent has access to:
  - Source introspection (read Prax's code)
  - Sandbox (OpenCode coding agent for writing/testing patches)
  - Codegen tools (deploy verified fixes to the live app)
  - Log reading (diagnose errors)

The source code is mounted in the sandbox at /source/ so OpenCode can read
and modify it directly.  In dev mode (bind mounts), changes in the sandbox
propagate to the live app via the shared filesystem.
"""
from __future__ import annotations

import logging

from langchain.agents import create_agent as create_react_agent
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool

from prax.agent.llm_factory import build_llm
from prax.settings import settings

logger = logging.getLogger(__name__)

_SELF_IMPROVE_PROMPT = """\
You are a self-improvement agent for {agent_name}.  Your job is to diagnose
and fix bugs in the application's own code.

## Available resources
- **Source code**: Use source_read/source_list to inspect the live codebase.
- **Logs**: Use read_logs to see recent errors and tracebacks.
- **Sandbox**: The app source is mounted at /source/ in the sandbox container.
  Use sandbox_start to launch OpenCode with a task like:
    "Read /source/prax/services/course_service.py and fix [describe bug].
     The fix should [describe expected behavior].  Write the patched file
     back to /source/prax/services/course_service.py."
  Use sandbox_message to guide OpenCode, sandbox_review to check results.
- **Deploy**: If self_improve tools are available, use them to verify and
  hot-swap fixes.  If they're not available (no git repo), the sandbox can
  write directly to /source/ in dev mode (bind-mounted to the live app).

## Workflow
1. **Diagnose**: Read logs and source to understand the bug fully.
2. **Fix**: Start a sandbox session with a clear task description that includes
   the relevant source code, the error, and what the fix should do.  Guide
   OpenCode with sandbox_message if needed.
3. **Verify**: Check that OpenCode's changes look correct with sandbox_review.
   If self_improve tools are available, use self_improve_verify.
4. **Deploy**: Use self_improve_deploy if available.  Otherwise tell the caller
   that the fix has been written to /source/ and the app will auto-reload
   (dev mode) or needs a container restart (production).
5. **Report**: Return a clear summary of what was wrong, what you changed,
   which files were modified, and whether the fix is deployed.

## Rules
- Always explain what you're changing and why.
- If you can't fix it in 3 attempts, stop and explain what's failing.
- Never silently modify code — always report changes.
- Prefer minimal, targeted fixes over broad refactors.
"""


def _build_self_improve_tools() -> list:
    """Assemble the tool set for the self-improvement agent."""
    from prax.agent.codegen_tools import build_codegen_tools
    from prax.agent.plugin_tools import source_list, source_read
    from prax.agent.sandbox_tools import build_sandbox_tools

    tools = [source_read, source_list] + build_sandbox_tools()

    # Add codegen tools if available (SELF_IMPROVE_ENABLED).
    tools.extend(build_codegen_tools())

    # Add read_logs if available.
    if settings.self_improve_enabled:
        from prax.agent.workspace_tools import read_logs
        tools.append(read_logs)

    return tools


@tool
def delegate_self_improve(task: str) -> str:
    """Delegate a bug fix or code improvement to the self-improvement agent.

    The self-improvement agent can read your source code, use the sandbox
    to write and test patches, and deploy fixes to the live app.

    Use this when you or the user find a bug in your own code, a tool is
    broken, or something needs changing in your implementation.

    Provide a detailed description including:
    - What's broken (error message, user report, unexpected behavior)
    - Which file/function you suspect is involved (if known)
    - What the fix should accomplish

    Args:
        task: Detailed description of the bug or improvement needed.
    """
    logger.info("Self-improve agent delegated: %s", task[:100])

    tools = _build_self_improve_tools()
    if not tools:
        return "No tools available for self-improvement."

    from prax.plugins.llm_config import get_component_config
    cfg = get_component_config("subagent_codegen")
    llm = build_llm(
        provider=cfg.get("provider"),
        model=cfg.get("model"),
        temperature=cfg.get("temperature"),
    )
    graph = create_react_agent(llm, tools)

    system_msg = _SELF_IMPROVE_PROMPT.format(agent_name=settings.agent_name)

    try:
        result = graph.invoke(
            {"messages": [
                SystemMessage(content=system_msg),
                HumanMessage(content=task),
            ]},
            config={"recursion_limit": 60},
        )
    except Exception as exc:
        logger.warning("Self-improve agent failed: %s", exc)
        return f"Self-improvement agent failed: {exc}"

    for msg in reversed(result.get("messages", [])):
        if isinstance(msg, AIMessage) and msg.content:
            logger.info("Self-improve agent completed: %s", msg.content[:100])
            return msg.content

    return "Self-improvement agent completed but produced no output."


def build_self_improve_tools() -> list:
    """Return the delegate tool for the main agent."""
    return [delegate_self_improve]
