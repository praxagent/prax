"""Self-improvement sub-agent — diagnoses and fixes bugs in Prax's own code.

Prax delegates to this agent when it detects a bug or the user reports one.
The agent has access to:
  - Source introspection (read Prax's code)
  - Native codegen tools (self_improve_read/write/patch/test/lint/verify/deploy)
    — Prax edits/tests/deploys its own code; there is no external coding agent.
  - run_python + sandbox shell (run/verify code)
  - Log reading (diagnose errors)

Changes are made in an isolated git worktree (self_improve_start) and hot-swapped
into the live app via self_improve_deploy after self_improve_verify passes.
"""
from __future__ import annotations

import logging

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool

from prax.agent.agent_loop import build_agent_loop
from prax.agent.llm_factory import build_llm
from prax.settings import settings

logger = logging.getLogger(__name__)

_SELF_IMPROVE_PROMPT = """\
You are a self-improvement agent for {agent_name}.  Your job is to diagnose
and fix bugs in the application's own code.

## Available resources
- **Source code**: Use source_read/source_list/source_grep to inspect the
  live codebase.  Use source_grep to find functions, imports, and patterns
  BEFORE reading individual files — don't guess file paths.
- **Logs**: Use read_logs to see recent errors and tracebacks.
- **Failure journal**: Check the failure journal for observed agent failures
  that need fixing — these are concrete, user-reported problems with traces.
- **Code editing**: Use the self_improve_* tools for direct code modification:
  - self_improve_start — create isolated worktree
  - self_improve_search — grep within the worktree
  - self_improve_read — read a file from the worktree
  - **self_improve_patch** — PREFERRED for edits: targeted old_text→new_text
    replacement.  Much safer than rewriting entire files.
  - self_improve_write — write a complete file (use for NEW files only)
  - self_improve_diff — review your changes before deploying
  - self_improve_verify — run tests + lint + startup check
  - self_improve_deploy — hot-swap verified changes into the live app
- **You are the coding harness.** There is NO external coding agent (OpenCode /
  Claude Code / Codex) to delegate to — write, test, and verify the fix yourself
  with the self_improve_* tools and run_python. Reason through the change, make
  surgical edits with self_improve_patch, and iterate: edit → self_improve_verify
  → read the failure → refine. This is your own loop, not a black box.

## Workflow
1. **Search**: Use source_grep to find relevant code.  Understand the
   existing patterns before making changes.
2. **Diagnose**: Read the specific files you need to modify.  If fixing a
   bug, check logs with read_logs.
3. **Branch**: Use self_improve_start to create an isolated worktree.
4. **Edit**: Use self_improve_patch for surgical edits to existing files.
   Use self_improve_write ONLY for creating new files.  Use run_python and the
   sandbox shell to run/verify code as you go.
5. **Review**: Use self_improve_diff to see all changes before deploying.
   Verify it looks right — catch accidental changes here.
6. **Verify**: Use self_improve_verify (tests + lint + startup check).
7. **Deploy**: Use self_improve_deploy to hot-swap into the live app.
8. **Report**: Summarize what changed, which files were modified, and
   whether the fix is deployed.

## Rules
- **Search before editing** — never guess file locations or function names.
- **Patch over write** — always use self_improve_patch for existing files.
  Only use self_improve_write for brand-new files.
- **Review before deploying** — always check self_improve_diff.
- Always explain what you're changing and why.
- If you can't fix it in 3 attempts, stop and explain what's failing.
- Never silently modify code — always report changes.
- Prefer minimal, targeted fixes over broad refactors.
"""


def _build_self_improve_tools() -> list:
    """Assemble the tool set for the self-improvement agent."""
    from prax.agent.codegen_tools import build_codegen_tools
    from prax.agent.plugin_tools import source_grep, source_list, source_read
    from prax.agent.sandbox_tools import build_sandbox_tools

    tools = [source_read, source_list, source_grep] + build_sandbox_tools()

    # Add codegen tools if available (SELF_IMPROVE_ENABLED).
    tools.extend(build_codegen_tools())

    # run_python + read_logs — Prax edits/tests its own code natively.
    if settings.self_improve_enabled:
        from prax.agent.workspace_tools import read_logs, run_python
        tools.extend([run_python, read_logs])

    # (The Claude Code CLI bridge was removed — the sandbox no longer ships a
    #  coding-agent CLI, and Prax is the coding harness: it writes/tests/verifies
    #  its own changes with self_improve_read/write/test/lint/verify/deploy.)

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
        tier=cfg.get("tier") or "medium",
    )
    graph = build_agent_loop(llm, tools)

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
