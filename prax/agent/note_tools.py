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


def _parse_tags(tags: str, default: list[str] | None = None) -> list[str]:
    if tags:
        return [t.strip() for t in tags.split(",") if t.strip()]
    return default or []


def _format_ingest_result(result: dict, verb: str = "Saved as note") -> str:
    if "error" in result:
        return result["error"]
    return (
        f"{verb}: **{result['title']}** (`{result['slug']}`)\n"
        f"URL: {result['url']}"
    )


# Phrases that indicate a note was fabricated because the real source
# couldn't be read.  We refuse to save such notes at the tool layer so
# fabrication can't slip past the system prompt rules.
_FABRICATION_MARKERS = (
    "(inferred)",
    "[inferred]",
    "inferred content",
    "likely content",
    "best guess",
    "best-guess",
    "probably contained",
    "this note is inferred",
    "could not access",
    "could not read",
    "couldn't access",
    "couldn't read",
    "fetch failed",
    "404 not found",
    "page not found",
)


def _looks_fabricated(title: str, content: str) -> str | None:
    """Detect notes that appear to be fabricated from failed source fetches.

    Returns a reason string if fabrication is detected, else None.
    """
    haystack = f"{title}\n{content[:2000]}".lower()
    hits = [m for m in _FABRICATION_MARKERS if m in haystack]
    if hits:
        return (
            f"note appears to describe its own source failure "
            f"(matched: {', '.join(hits[:3])})"
        )
    return None


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
    # Runtime guard: block fabricated notes even if the prompt rules were
    # ignored. If the source couldn't be read, no note should be created.
    fabrication_reason = _looks_fabricated(title, content)
    if fabrication_reason:
        return (
            f"BLOCKED — fabricated note refused: {fabrication_reason}. "
            f"Report the source failure to the user instead of saving a "
            f"made-up note. Do NOT retry with the same content. If the user "
            f"explicitly wants a note on the TOPIC without the source, "
            f"rewrite with a different title (no 'inferred'/'likely'/"
            f"'probably') and remove all source-failure disclaimers from "
            f"the content first."
        )

    # Quality review — reject raw dumps and low-effort content, up to
    # MAX_REVISIONS times before allowing the save through.
    try:
        from prax.services import note_quality
        review = note_quality.review_note(title, content)
        if not review["approved"] and not review["force_save"]:
            note_quality.increment_revision(title)
            return f"REVIEW REJECTED — {note_quality.format_feedback(review)}"
    except Exception:
        pass  # Review is best-effort — don't block saves on reviewer failure.

    try:
        result = note_service.save_and_publish(
            _get_user_id(), title, content, tags=_parse_tags(tags),
        )
        # Clear revision counter on successful save.
        try:
            from prax.services import note_quality
            note_quality.clear_revision(title)
        except Exception:
            pass
        return _format_ingest_result(result, verb="Note created")
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
    uid = _get_user_id()

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

        # Publish if NGROK is available; degrade gracefully if not.
        from prax.utils.ngrok import get_ngrok_url
        base_url = get_ngrok_url()
        if not base_url:
            return (
                f"Note updated: **{meta['title']}**\n"
                f"(saved locally — NGROK_URL not configured for web publishing)"
            )
        result = note_service.publish_notes(uid, base_url, slug=meta["slug"])
        if "error" in result:
            # Note is saved — Hugo publish is best-effort for the web page.
            import logging
            logging.getLogger(__name__).warning("Hugo publish failed: %s", result["error"])
            return (
                f"Note updated: **{meta['title']}** (`{meta['slug']}`)\n"
                f"(web page rebuild skipped — {result['error']})"
            )
        return (
            f"Note updated: **{meta['title']}**\n"
            f"URL: {result['url']}"
        )
    except FileNotFoundError:
        return f"Note `{note_id}` not found. Use note_list to see available notes."
    except Exception as e:
        return f"Error updating note: {e}"


@tool
def note_read(note_id: str) -> str:
    """Read the full content of a note by its slug.

    Use this when the user asks about a specific note, wants to review it,
    or when you need the content for editing or discussion.

    Args:
        note_id: The note slug (returned by note_create, note_list, or note_search).
    """
    uid = _get_user_id()
    try:
        note = note_service.get_note(uid, note_id)
        title = note.get("title", note_id)
        tags = note.get("tags", [])
        content = note.get("content", "")
        related = note.get("related", [])

        header = f"**{title}** (`{note_id}`)"
        if tags:
            header += f"\nTags: {', '.join(tags)}"
        if related:
            header += f"\nRelated: {', '.join(related)}"

        return f"{header}\n\n{content}"
    except FileNotFoundError:
        return f"Note `{note_id}` not found. Use note_list to see available notes."
    except Exception as e:
        return f"Error reading note: {e}"


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


@tool
def url_to_note(url: str, title: str = "", tags: str = "") -> str:
    """Fetch a web page and save its content as a note.

    Downloads the URL, extracts readable content, and publishes it as a
    rendered note the user can read in their browser.

    Args:
        url: The web page URL to fetch.
        title: Optional title (auto-detected from page if empty).
        tags: Comma-separated tags.
    """
    try:
        import requests
        from bs4 import BeautifulSoup

        resp = requests.get(url, timeout=30, headers={"User-Agent": "Prax/1.0"})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Extract title from page if not provided.
        if not title:
            title_el = soup.find("title")
            title = title_el.get_text(strip=True) if title_el else url

        # Extract main content — try article/main first, fall back to body.
        content_el = None
        for selector in ("article", "main", "[role=main]", ".post-content", ".entry-content"):
            content_el = soup.select_one(selector)
            if content_el:
                break
        if not content_el:
            content_el = soup.find("body")

        # Strip scripts, styles, navs.
        if content_el:
            for tag in content_el.find_all(["script", "style", "nav", "header", "footer", "aside"]):
                tag.decompose()
            text = content_el.get_text("\n\n", strip=True)
        else:
            text = soup.get_text("\n\n", strip=True)

        # Truncate if extremely long.
        if len(text) > 50000:
            text = text[:50000] + "\n\n*[Content truncated]*"

        result = note_service.save_and_publish(
            _get_user_id(), title, text,
            tags=_parse_tags(tags, default=["web"]),
            source_url=url,
        )
        return _format_ingest_result(result)
    except Exception as e:
        return f"Error creating note from URL: {e}"


@tool
def pdf_to_note(filename: str, title: str = "", tags: str = "") -> str:
    """Extract text from a PDF in the workspace and save it as a note.

    The PDF must already be saved in the active workspace (e.g. via
    workspace_save or as a received attachment).

    Args:
        filename: PDF filename in the active workspace.
        title: Optional title (defaults to filename).
        tags: Comma-separated tags.
    """
    uid = _get_user_id()
    try:
        from prax.services import workspace_service
        content_raw = workspace_service.read_file(uid, filename)

        # If it's binary PDF data, try extraction.
        if content_raw.startswith("%PDF") or not content_raw.strip():
            return (
                f"Cannot read {filename} as text. "
                "Use the sandbox to extract PDF content with a tool like pymupdf or pdfplumber, "
                "then pass the extracted text to note_create."
            )

        note_title = title or filename.replace(".pdf", "").replace("_", " ").title()
        result = note_service.save_and_publish(
            uid, note_title, content_raw,
            tags=_parse_tags(tags, default=["pdf"]),
        )
        return _format_ingest_result(result)
    except FileNotFoundError:
        return f"File {filename} not found in workspace."
    except Exception as e:
        return f"Error creating note from PDF: {e}"


@tool
def note_link(from_slug: str, to_slug: str) -> str:
    """Create a bidirectional link between two notes.

    Links are stored in each note's metadata and displayed on the rendered
    page as "Related Notes".  Use this to build connections between topics.

    Args:
        from_slug: The note to link from.
        to_slug: The note to link to.
    """
    uid = _get_user_id()
    try:
        # Read both notes to verify they exist.
        from_note = note_service.get_note(uid, from_slug)
        to_note = note_service.get_note(uid, to_slug)

        # Add links (stored as 'related' field in frontmatter).
        from_related = from_note.get("related", [])
        to_related = to_note.get("related", [])

        if to_slug not in from_related:
            from_related.append(to_slug)
        if from_slug not in to_related:
            to_related.append(from_slug)

        note_service.update_note(uid, from_slug, related=from_related)
        note_service.update_note(uid, to_slug, related=to_related)

        return f"Linked `{from_slug}` ↔ `{to_slug}`"
    except FileNotFoundError as e:
        return f"Note not found: {e}"
    except Exception as e:
        return f"Error linking notes: {e}"


def build_note_tools() -> list:
    """Return the list of note tools to register with the main agent."""
    return [
        note_create, note_read, note_update, note_list, note_search,
        url_to_note, pdf_to_note, note_link,
    ]
