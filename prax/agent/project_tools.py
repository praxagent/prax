"""LangChain tool wrappers for the research project system."""
from __future__ import annotations

from langchain_core.tools import tool

from prax.agent.user_context import current_user_id
from prax.services import project_service


def _get_user_id() -> str:
    uid = current_user_id.get()
    if not uid:
        return "unknown"
    return uid


@tool
def project_create(title: str, description: str = "") -> str:
    """Create a new research project to group notes, links, and sources.

    Use this when the user wants to organize research around a topic.
    Returns the project ID for future reference.

    Args:
        title: A descriptive title for the project.
        description: Optional longer description of the project goals.
    """
    try:
        data = project_service.create_project(_get_user_id(), title, description)
        return (
            f"Project created: **{data['title']}** (id: `{data['id']}`)\n"
            f"Status: {data['status']}\n\n"
            f"Add notes with project_add_note, links with project_add_link, "
            f"and source files with project_add_source."
        )
    except Exception as e:
        return f"Failed to create project: {e}"


@tool
def project_status(project_id: str = "") -> str:
    """Show project details.  If project_id is empty, lists all projects.

    Use this to review what's in a project or to find a project_id.

    Args:
        project_id: The project ID to inspect.  Leave empty to list all.
    """
    uid = _get_user_id()
    try:
        if not project_id:
            projects = project_service.list_projects(uid)
            if not projects:
                return "No projects yet.  Use project_create to start one."
            lines = []
            for p in projects:
                lines.append(
                    f"- **{p['title']}** (`{p['id']}`) — "
                    f"{p['status']}, "
                    f"{p['notes_count']} notes, "
                    f"{p['links_count']} links, "
                    f"{p['sources_count']} sources"
                )
            return "Projects:\n" + "\n".join(lines)

        data = project_service.get_project(uid, project_id)
        lines = [
            f"**{data['title']}** (`{data['id']}`)",
            f"Description: {data.get('description') or '(none)'}",
            f"Status: {data['status']}",
            f"Notes: {', '.join(data.get('notes', [])) or '(none)'}",
        ]
        links = data.get("links", [])
        if links:
            lines.append("Links:")
            for link in links:
                link_title = link.get("title") or link["url"]
                lines.append(f"  - [{link_title}]({link['url']})")
        else:
            lines.append("Links: (none)")
        sources = data.get("sources", [])
        lines.append(f"Sources: {', '.join(sources) or '(none)'}")
        return "\n".join(lines)
    except FileNotFoundError:
        return f"Project `{project_id}` not found."
    except Exception as e:
        return f"Error: {e}"


@tool
def project_add_note(project_id: str, note_slug: str) -> str:
    """Link an existing note to a research project.

    The note must already exist (created via note_create).  Duplicates are
    ignored — adding the same note twice is safe.

    Args:
        project_id: The project to add the note to.
        note_slug: The slug of the note to link.
    """
    try:
        data = project_service.add_note_to_project(
            _get_user_id(), project_id, note_slug,
        )
        return (
            f"Note `{note_slug}` linked to project `{project_id}`.\n"
            f"Project now has {len(data.get('notes', []))} note(s)."
        )
    except FileNotFoundError:
        return f"Project `{project_id}` not found."
    except Exception as e:
        return f"Error: {e}"


@tool
def project_add_link(project_id: str, url: str, title: str = "") -> str:
    """Add a reference link to a research project.

    Args:
        project_id: The project to add the link to.
        url: The URL to add.
        title: Optional title for the link.
    """
    try:
        data = project_service.add_link_to_project(
            _get_user_id(), project_id, url, title,
        )
        return (
            f"Link added to project `{project_id}`.\n"
            f"Project now has {len(data.get('links', []))} link(s)."
        )
    except FileNotFoundError:
        return f"Project `{project_id}` not found."
    except Exception as e:
        return f"Error: {e}"


@tool
def project_add_source(project_id: str, filename: str, content: str) -> str:
    """Save a source file into a research project's directory.

    Use this for PDFs, text excerpts, data files, etc.

    Args:
        project_id: The project to add the source to.
        filename: Filename to save (e.g. "paper_summary.md").
        content: The file content.
    """
    try:
        data = project_service.add_source_to_project(
            _get_user_id(), project_id, filename, content,
        )
        return (
            f"Source `{filename}` saved to project `{project_id}`.\n"
            f"Project now has {len(data.get('sources', []))} source(s)."
        )
    except FileNotFoundError:
        return f"Project `{project_id}` not found."
    except Exception as e:
        return f"Error: {e}"


@tool
def project_brief(project_id: str) -> str:
    """Generate a combined brief from all project notes, links, and sources.

    Returns a single markdown document combining everything in the project.

    Args:
        project_id: The project to generate a brief for.
    """
    try:
        return project_service.generate_project_brief(_get_user_id(), project_id)
    except FileNotFoundError:
        return f"Project `{project_id}` not found."
    except Exception as e:
        return f"Error generating brief: {e}"


def build_project_tools() -> list:
    """Return the list of project tools to register with the main agent."""
    return [
        project_create,
        project_status,
        project_add_note,
        project_add_link,
        project_add_source,
        project_brief,
    ]
