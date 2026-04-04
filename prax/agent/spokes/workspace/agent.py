"""Workspace spoke agent — file management, archiving, links, latex."""
from __future__ import annotations

from langchain_core.tools import tool

from prax.agent.spokes._runner import run_spoke
from prax.settings import settings

SYSTEM_PROMPT = """\
You are the Workspace Agent for {agent_name}. You manage the user's
git-backed workspace — saving files, downloading URLs, searching,
archiving, sharing, and compiling LaTeX.

## Tools
- workspace_save / workspace_patch / workspace_read / workspace_list
- workspace_download — download files from URLs (PDFs, images, etc.)
- workspace_send_file — share a file via a public link
- workspace_archive / workspace_search / workspace_restore
- latex_compile — compile .tex to PDF
- log_link / links_history — URL bookmarking
- reread_instructions — reload the system prompt

Execute the task efficiently. Don't ask follow-up questions.
"""


def build_tools() -> list:
    """Return all tools available to the workspace spoke."""
    from prax.agent.workspace_tools import (
        latex_compile,
        links_history,
        log_link,
        reread_instructions,
        workspace_archive,
        workspace_download,
        workspace_list,
        workspace_patch,
        workspace_read,
        workspace_restore,
        workspace_save,
        workspace_search,
        workspace_send_file,
    )

    return [
        workspace_save, workspace_download, workspace_patch,
        workspace_read, workspace_list, workspace_send_file,
        workspace_archive, workspace_search, workspace_restore,
        latex_compile, log_link, links_history, reread_instructions,
    ]


@tool
def delegate_workspace(task: str) -> str:
    """Delegate a file/workspace task to the Workspace Agent.

    The Workspace Agent manages the user's git-backed workspace. Use this for:
    - "Save this content as notes.md"
    - "Download this PDF: https://..."
    - "List files in the workspace"
    - "Search workspace for keyword"
    - "Compile this LaTeX document"
    - "Share this file with a public link"
    - "Archive old files"
    - "Log this URL to my link history"

    Do NOT use for: creating notes/pages (use delegate_knowledge),
    memory operations (use delegate_memory), or code execution
    (use sandbox_shell or delegate_sandbox).

    Args:
        task: Description of the workspace task.
    """
    prompt = SYSTEM_PROMPT.format(agent_name=settings.agent_name)
    return run_spoke(
        task=task,
        system_prompt=prompt,
        tools=build_tools(),
        config_key="subagent_workspace",
        default_tier="low",
        role_name="Executor",
        channel=None,
        recursion_limit=15,
    )


def build_spoke_tools() -> list:
    return [delegate_workspace]
