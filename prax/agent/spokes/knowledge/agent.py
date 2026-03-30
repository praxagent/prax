"""Knowledge spoke agent — notes, projects, and knowledge management.

Prax delegates knowledge organization tasks here instead of keeping 13
note/project tools in the main orchestrator.  The knowledge agent manages
notes (create, update, search, link, ingest from URL/PDF) and research
projects (create, add notes/links/sources, generate briefs).
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
You are the Knowledge Agent for {agent_name}.  You manage notes and research
projects — the user's persistent knowledge base.

## Notes
Notes are markdown documents published as web pages (Hugo).  They support
LaTeX math, mermaid diagrams, code blocks, and tables.

- **note_create** — Create a new note and publish it.  Returns a shareable URL.
- **note_update** — Update an existing note (pass full content, not a diff).
- **note_list** — List all notes with slugs and tags.
- **note_search** — Search notes by title, tags, or content.
- **note_link** — Create bidirectional links between related notes.
- **url_to_note** — Fetch a web page and save it as a note.
- **pdf_to_note** — Extract text from a workspace PDF and save as a note.

## Research Projects
Projects group related notes, links, and source files for organized research.

- **project_create** — Create a new research project.
- **project_status** — View project details or list all projects.
- **project_add_note** — Link an existing note to a project.
- **project_add_link** — Add a reference URL to a project.
- **project_add_source** — Save a source file into a project directory.
- **project_brief** — Generate a combined markdown brief from all project materials.

## Workflow
1. **Understand** what the user wants — a new note, an update, a project, etc.
2. **Execute** using the appropriate tool(s).
3. **Report** the result — include URLs for published notes, project IDs, etc.

## Rules
- When creating notes, use full markdown with proper headings and formatting.
- For note_update, always pass the COMPLETE updated content, not a diff.
- When linking notes, verify both exist first.
- For projects, suggest linking related notes after creation.
- Keep responses concise — include the URL or ID, not verbose confirmations.
"""


# ---------------------------------------------------------------------------
# Tool assembly
# ---------------------------------------------------------------------------

def build_tools() -> list:
    """Return all tools available to the knowledge spoke."""
    from prax.agent.note_tools import build_note_tools
    from prax.agent.project_tools import build_project_tools

    return build_note_tools() + build_project_tools()


# ---------------------------------------------------------------------------
# Delegation function
# ---------------------------------------------------------------------------

@tool
def delegate_knowledge(task: str) -> str:
    """Delegate a knowledge management task to the Knowledge Agent.

    The Knowledge Agent manages notes (markdown web pages) and research
    projects (organized collections of notes, links, and sources).

    Use this for:
    - "Save this as a note" / "Make a note about X"
    - "Update my note on quantum computing"
    - "Find my notes about eigenvalues"
    - "Link these two notes together"
    - "Save this URL as a note"
    - "Create a research project on X"
    - "Add this note to my project"
    - "Generate a brief from my project materials"
    - "List my notes" / "List my projects"

    Do NOT use this for:
    - Blog posts (use delegate_content_editor)
    - Workspace files (use workspace_save directly)
    - Course materials (use course_save_material directly)

    Args:
        task: Description of the knowledge management task.  Include note
              slugs, project IDs, URLs, or content as needed.
    """
    prompt = SYSTEM_PROMPT.format(agent_name=settings.agent_name)
    return run_spoke(
        task=task,
        system_prompt=prompt,
        tools=build_tools(),
        config_key="subagent_knowledge",
        default_tier="low",
        role_name=None,
        channel=None,
        recursion_limit=30,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def build_spoke_tools() -> list:
    """Return the delegation tool for the main agent."""
    return [delegate_knowledge]
