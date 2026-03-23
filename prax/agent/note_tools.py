"""LangChain tool wrappers for the notes system."""
from __future__ import annotations

from langchain_core.tools import tool

from prax.agent.user_context import current_user_id
from prax.services import note_service


def _get_user_id() -> str:
    uid = current_user_id.get()
    if not uid:
        return "unknown"
    return uid


@tool
def note_create(title: str, content: str, tags: str = "") -> str:
    """Create a note from the current conversation and publish it as a web page.

    Use this when the user asks you to "make this a note", "save this as a note",
    or when a conversation gets dense enough that a rendered page would be more
    useful than chat messages.  The note supports full markdown, LaTeX math
    ($$...$$ and $...$), mermaid diagrams, code blocks, tables, and images.

    Returns a shareable URL the user can open immediately.  You can update the
    note later with note_update as the conversation continues.

    Args:
        title: A descriptive title for the note.
        content: Full markdown content for the note.
        tags: Comma-separated tags for search (e.g. "math, linear-algebra").
    """
    from prax.utils.ngrok import get_ngrok_url

    uid = _get_user_id()
    base_url = get_ngrok_url()
    if not base_url:
        return (
            "Cannot publish note — NGROK_URL is not configured.\n"
            "Set NGROK_URL in your .env to enable note links."
        )

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    try:
        meta = note_service.create_note(uid, title, content, tag_list)
        result = note_service.publish_notes(uid, base_url, slug=meta["slug"])
        if "error" in result:
            return f"Note saved but Hugo build failed: {result['error']}"
        return (
            f"Note created: **{meta['title']}** (`{meta['slug']}`)\n"
            f"URL: {result['url']}\n"
            f"Update anytime with note_update."
        )
    except Exception as e:
        return f"Error creating note: {e}"


@tool
def note_update(note_id: str, content: str, title: str = "", tags: str = "") -> str:
    """Update an existing note and republish.

    Use this to iteratively refine a note during conversation — add more detail,
    diagrams, math, examples, etc.  Pass the FULL updated content, not a diff.

    Args:
        note_id: The note slug (returned by note_create or note_list).
        content: The complete updated markdown content.
        title: New title (leave empty to keep the current title).
        tags: New comma-separated tags (leave empty to keep current tags).
    """
    from prax.utils.ngrok import get_ngrok_url

    uid = _get_user_id()
    base_url = get_ngrok_url()
    if not base_url:
        return "Cannot publish — NGROK_URL is not configured."

    tag_list: list[str] | None = None
    if tags:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]

    try:
        meta = note_service.update_note(
            uid, note_id,
            content=content,
            title=title or None,
            tags=tag_list,
        )
        result = note_service.publish_notes(uid, base_url, slug=meta["slug"])
        if "error" in result:
            return f"Note updated but Hugo build failed: {result['error']}"
        return (
            f"Note updated: **{meta['title']}**\n"
            f"URL: {result['url']}"
        )
    except FileNotFoundError:
        return f"Note `{note_id}` not found. Use note_list to see available notes."
    except Exception as e:
        return f"Error updating note: {e}"


@tool
def note_list() -> str:
    """List all saved notes with their slugs, titles, and tags.

    Use this to find a note the user wants to revisit or update.
    """
    uid = _get_user_id()
    try:
        notes = note_service.list_notes(uid)
        if not notes:
            return "No notes yet. Use note_create to start one."
        lines = []
        for n in notes:
            tag_str = ", ".join(n["tags"]) if n["tags"] else ""
            lines.append(
                f"- **{n['title']}** (`{n['slug']}`)"
                + (f" — tags: {tag_str}" if tag_str else "")
            )
        return "Notes:\n" + "\n".join(lines)
    except Exception as e:
        return f"Error listing notes: {e}"


@tool
def note_search(query: str) -> str:
    """Search notes by title, tags, or content.

    Returns matching notes with a content snippet showing where the match was found.

    Args:
        query: Search term to look for.
    """
    uid = _get_user_id()
    try:
        results = note_service.search_notes(uid, query)
        if not results:
            return f"No notes matching '{query}'."
        lines = []
        for r in results:
            tag_str = ", ".join(r["tags"]) if r["tags"] else ""
            line = f"- **{r['title']}** (`{r['slug']}`)"
            if tag_str:
                line += f" — tags: {tag_str}"
            if r.get("snippet"):
                line += f"\n  {r['snippet']}"
            lines.append(line)
        return f"Found {len(results)} note(s):\n" + "\n".join(lines)
    except Exception as e:
        return f"Error searching notes: {e}"


def build_note_tools() -> list:
    """Return the list of note tools to register with the main agent."""
    return [note_create, note_update, note_list, note_search]
