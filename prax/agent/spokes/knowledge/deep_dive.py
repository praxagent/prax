"""Deep-dive note pipeline — multi-agent synthesis for high-quality notes.

Wires the reusable ``SynthesisPipeline`` into the knowledge spoke so that
"deep dive" / "explainer" note requests go through a real write → review →
revise loop instead of a single-shot ReAct agent.

The pipeline has no separate research phase (notes are usually about a
specific source the orchestrator already fetched).  When the caller passes
``source_content`` the pipeline skips research entirely.  When research is
needed, the researcher phase delegates up to the research agent.
"""
from __future__ import annotations

import logging

from langchain.agents import create_agent as create_react_agent
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool

from prax.agent.llm_factory import build_llm
from prax.agent.pipelines import SynthesisPipeline
from prax.agent.user_context import current_user_id
from prax.settings import settings

logger = logging.getLogger(__name__)

MAX_REVISIONS = 3


# ---------------------------------------------------------------------------
# System prompts for the note writer and reviewer sub-agents
# ---------------------------------------------------------------------------


_NOTE_WRITER_PROMPT = """\
You are the Note Writer for {agent_name}. Your job is to produce a
high-quality deep-dive note on the requested topic — one that goes
significantly deeper than what a reader could find on Wikipedia.

## What a good deep-dive note looks like
- **Minimum 5 substantive sections** — each with real content, not just
  a heading and a sentence.  A deep dive that has fewer than 5 sections
  is almost certainly too shallow.
- **Clear section headings** (## for sections, ### for subsections)
- **Proper LaTeX math** — inline `$x^2 + y^2$`, display `$$R^\\top R = I$$`
- **Real examples with concrete data** — work through them step by step
  using realistic values, not abstract placeholders like "let x = some value".
  Show every intermediate computation so the reader can follow along.
- **Progressive depth** — start with intuition and motivation ("why does
  this exist? what problem does it solve?"), build to formalism, then
  demonstrate with examples, then discuss edge cases and pitfalls.
- **Explanatory transitions** — "the key insight is...", "this means...",
  "intuitively...", "note that..."
- **Mermaid diagrams** where they help visualize relationships
- **Synthesized prose**, not raw copy-paste from the source
- **Common misconceptions** — what do people get wrong about this topic?
- **Connections to related concepts** — how does this fit into the
  broader landscape?
- **Obsidian-style wikilinks** to 2–5 related existing notes where they
  naturally fit in the prose — syntax is ``[[slug]]`` for same-notebook
  links and ``[[project/notebook/slug]]`` for cross-notebook. The
  caller will give you a list of existing note slugs in the
  "Related existing notes" section — cite them where they fit.  A
  deep-dive note without cross-references is an isolated island.

## Source material
The caller provides research/source content as context. Use it as the
substrate for your explanation — DO NOT copy it verbatim. You are
SYNTHESIZING: reading the source, understanding the concepts, and
rewriting them in your own voice with added examples, intuition, and
structure. A deep dive must add value beyond the source — if the reader
could get the same information by reading the source directly, you
have failed.

## Wikilinks
If the caller provides a "Related existing notes" section, pull 2–5
relevant ``[[slug]]`` references into your prose where they naturally
fit the argument.  Do NOT invent slugs that weren't in the list — dead
wikilinks are caught by the health check and count against quality.
If the caller does not provide that section, it's fine to skip
wikilinks — don't guess at what notes exist.

## If you receive reviewer feedback
Address every point the reviewer raised. Don't just tweak — if the
reviewer says "no toy example in section 3", ADD a concrete toy example
in section 3. If they say "equation is broken", rewrite the equation in
proper LaTeX.

## Output format
Return ONLY the markdown note content. No preamble, no "here's the note",
no trailing commentary. The first line should be either the title (as
``# Title``) or the first section heading. The caller will wrap it in
whatever frontmatter it needs.
"""


_NOTE_REVIEWER_PROMPT = """\
You are the Note Reviewer for {agent_name}. Your job is to critique
deep-dive notes and either APPROVE them or send them back for revision.

## Your mandate
Be demanding. You are the last line of defense between the user and a
mediocre note. A deep dive must go DEEPER than what the reader could
find on Wikipedia. Reject anything that is a glorified summary.

## Rejection criteria (any one of these → REVISE)
1. **Raw copy from source** — if the note reads like a web page dump
   (orphan commas from stripped LaTeX, `[Image]` placeholders, duplicated
   variable definitions), reject it.
2. **Broken LaTeX** — equations should use `$...$` or `$$...$$`, not plain
   text. Reject orphan mathematical notation.
3. **No structure** — deep-dive notes should have clear section headings.
   Reject walls of text.
4. **No worked examples** — if there aren't any concrete worked examples
   with actual numbers and step-by-step intermediate computations, reject.
   Abstract "suppose we have X" without concrete values does not count.
5. **No explanatory prose** — if the note reads like bullet points or
   terse facts without transitions ("the key insight is...", "this means...",
   "note that..."), reject.
6. **Too shallow** — if a "deep dive" has fewer than 5 meaningful sections
   or feels like a summary instead of an explanation, reject. A deep dive
   must include motivation, intuition, formalism, examples, and
   connections — not just definitions.
7. **Factual errors or unverified claims** — if the note makes claims the
   source material doesn't support, reject.
8. **Glorified summary** — if the note merely restates information from
   the source without adding synthesis, insight, or pedagogical value,
   reject. The test: would a reader learn more from this note than from
   spending the same time reading the source directly? If not, reject.
9. **No motivation or "why"** — if the note jumps straight into definitions
   without explaining why the concept exists or what problem it solves,
   reject. Context and motivation are not optional.
10. **Missing progressive depth** — the note should build understanding
    from intuitive to formal. If it starts with formal definitions and
    never provides intuition, or if every section is at the same shallow
    level of depth, reject.

## Soft criteria (flag as "Should Improve", don't reject for these alone)
- **No wikilinks** — when the writer had access to a "Related existing
  notes" list and used zero ``[[slug]]`` references, note the missed
  cross-reference opportunity. A deep-dive note that is disconnected
  from the rest of the library is less valuable than one that plugs
  into the existing knowledge graph.  Do NOT reject on this criterion
  alone — it's an improvement signal, not a blocker.
- **Dead wikilinks** — if the writer invented a ``[[slug]]`` that
  wasn't in the provided list, flag it as must-fix (dead links
  pollute the graph).

## Approval
If none of the rejection criteria apply and the note is a genuine deep
dive that teaches the reader something they could not easily learn
elsewhere, approve it. Don't nitpick style — if the content is solid and
the structure is clear, approve. But do NOT approve out of fatigue or
because "it's mostly fine" — the reader deserves real depth.

## Output format
Start your response with EXACTLY one of:
- ``APPROVED`` — the note is ready to publish
- ``REVISE`` — send it back for another pass

Then provide your feedback in a structured list:

### Must Fix
- Specific, actionable issue #1
- Specific, actionable issue #2

### Should Improve
- Softer suggestions

### What's Good
- Acknowledge what the writer got right

Be specific. "The equations are broken" is not actionable. "The equation
in Section 2 reads as 'K = ,' instead of '$K = [0.1, 0.2]$'" is actionable.
"""


# ---------------------------------------------------------------------------
# Phase callables
# ---------------------------------------------------------------------------


def _make_researcher(source_content: str | None):
    """Build a researcher callable that uses pre-fetched source if provided."""
    def _researcher(topic: str, notes: str) -> str:
        if source_content:
            return source_content
        # Fall back to the full research spoke.
        from prax.agent.subagent import _run_subagent
        query = f"Research this topic for a deep-dive note:\n\n{topic}"
        if notes:
            query += f"\n\nAdditional context:\n{notes}"
        return _run_subagent(query, "research")
    return _researcher


def _collect_related_notes(topic: str, *, limit: int = 20) -> str:
    """Build a "Related existing notes" slug list for the writer prompt.

    Pulls the user's existing note titles + slugs so the writer can
    add ``[[wikilinks]]`` that actually resolve.  Returns an empty
    string when the service is unavailable or the list is empty — the
    writer's prompt handles the "no list" case gracefully.
    """
    try:
        from prax.services import note_service

        uid = current_user_id.get() or ""
        if not uid:
            return ""
        all_notes = note_service.list_notes(uid)[:limit]
        if not all_notes:
            return ""
        lines = ["## Related existing notes (use [[slug]] to link)"]
        for note in all_notes:
            slug = note.get("slug") or ""
            title = note.get("title") or slug
            tags = note.get("tags") or []
            tag_str = f" — tags: {', '.join(tags)}" if tags else ""
            lines.append(f"- ``[[{slug}]]`` — {title}{tag_str}")
        lines.append(
            "\nPick 2–5 that are topically adjacent to the note you're "
            "writing and weave them into your prose where they naturally "
            "fit. Do NOT invent slugs that aren't in this list."
        )
        return "\n".join(lines)
    except Exception:
        logger.debug("Could not collect related notes for writer", exc_info=True)
        return ""


def _note_writer(
    topic: str,
    research: str,
    feedback: str | None = None,
    previous_draft: str | None = None,
) -> str:
    """Run the note writer sub-agent and return the markdown draft."""
    from prax.plugins.llm_config import get_component_config

    cfg = get_component_config("subagent_note_writer")
    llm = build_llm(
        provider=cfg.get("provider"),
        model=cfg.get("model"),
        temperature=cfg.get("temperature") or 0.5,
        tier=cfg.get("tier") or "high",
    )

    # Writer has no tools — it just writes based on the provided material.
    graph = create_react_agent(llm, [])
    prompt = _NOTE_WRITER_PROMPT.format(agent_name=settings.agent_name)

    related = _collect_related_notes(topic)
    related_block = f"\n\n{related}\n\n" if related else "\n\n"

    if feedback and previous_draft:
        task = (
            f"# Topic\n{topic}\n\n"
            f"# Source material\n{research[:20_000]}"
            f"{related_block}"
            f"# Previous draft\n{previous_draft}\n\n"
            f"# Reviewer feedback\n{feedback}\n\n"
            "Rewrite the note addressing every point in the reviewer feedback. "
            "Return ONLY the revised markdown — no preamble."
        )
    else:
        task = (
            f"# Topic\n{topic}\n\n"
            f"# Source material\n{research[:20_000]}"
            f"{related_block}"
            "Write a deep-dive note on this topic using the source material "
            "as the substrate. Synthesize, don't copy. Weave in 2–5 "
            "``[[slug]]`` wikilinks from the related-notes list above "
            "where they naturally fit. Return ONLY the markdown content — "
            "no preamble."
        )

    logger.info("Note writer starting (revision=%s)", feedback is not None)
    try:
        result = graph.invoke(
            {"messages": [
                SystemMessage(content=prompt),
                HumanMessage(content=task),
            ]},
            config={"recursion_limit": 20},
        )
    except Exception as exc:
        logger.warning("Note writer failed: %s", exc, exc_info=True)
        return f"Writer failed: {exc}"

    for msg in reversed(result.get("messages", [])):
        if isinstance(msg, AIMessage) and msg.content:
            return msg.content

    return "Writer produced no output."


def _note_reviewer(draft: str, published_url: str | None, pass_number: int) -> str:
    """Run the note reviewer sub-agent and return APPROVED/REVISE feedback."""
    from prax.agent.spokes.content.reviewer import _pick_reviewer_llm

    # Reuse the content spoke's reviewer LLM picker — it already handles
    # cross-provider diversity.
    llm = _pick_reviewer_llm(writer_provider=settings.default_llm_provider)
    graph = create_react_agent(llm, [])
    prompt = _NOTE_REVIEWER_PROMPT.format(agent_name=settings.agent_name)

    task = (
        f"# Note draft (revision {pass_number})\n\n{draft}\n\n"
        "Review this note per your instructions. Start with APPROVED or REVISE, "
        "then provide specific feedback."
    )
    if published_url:
        task += f"\n\nPublished at: {published_url}"

    logger.info("Note reviewer starting (pass=%d)", pass_number)
    try:
        result = graph.invoke(
            {"messages": [
                SystemMessage(content=prompt),
                HumanMessage(content=task),
            ]},
            config={"recursion_limit": 15},
        )
    except Exception as exc:
        logger.warning("Note reviewer failed: %s", exc, exc_info=True)
        return (
            "REVISE\n\nReview system encountered an error. Content has not been "
            "quality-checked. Revise for completeness and depth before publishing."
        )

    for msg in reversed(result.get("messages", [])):
        if isinstance(msg, AIMessage) and msg.content:
            return msg.content

    return (
        "REVISE\n\nReviewer produced no output. Content has not been "
        "quality-checked. Revise for completeness and depth before publishing."
    )


def _note_publisher(
    title: str,
    content: str,
    tags: list[str] | None = None,
    slug: str | None = None,
) -> dict:
    """Publish a note via the note_service."""
    from prax.services.note_service import publish_notes, save_and_publish, update_note
    from prax.settings import settings

    uid = current_user_id.get() or "unknown"
    teamwork_url = settings.teamwork_base_url.rstrip("/")
    try:
        if slug:
            # Update existing note.
            result = update_note(uid, slug, content=content, title=title)
            pub = publish_notes(uid, teamwork_url, slug=slug)
            if "url" in pub:
                result["url"] = pub["url"]
            return result
        # New note.
        return save_and_publish(uid, title, content, tags=tags or [])
    except Exception as exc:
        logger.exception("Note publisher failed")
        return {"error": str(exc)}


def _post_status(message: str) -> None:
    """Post a status update to TeamWork for the Note Editor role."""
    try:
        from prax.services.teamwork_hooks import post_to_channel, set_role_status
        set_role_status("Note Editor", "working")
        post_to_channel("research", message, agent_name="Note Editor")
    except Exception:
        pass


def _finish() -> None:
    try:
        from prax.services.teamwork_hooks import set_role_status
        set_role_status("Note Editor", "idle")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Public tool
# ---------------------------------------------------------------------------


@tool
def note_deep_dive(topic: str, source_content: str = "", tags: str = "") -> str:
    """Create a deep-dive note via the multi-agent write → review → revise pipeline.

    Use this when the user asks for a note that explains, breaks down, or
    deep-dives a topic. The pipeline:

    1. Writes a first draft using a **high-tier writer LLM** (real synthesis,
       proper LaTeX, toy examples, explanatory prose).
    2. Publishes it.
    3. Sends it to a **cross-provider reviewer** that either APPROVES or
       rejects with specific feedback.
    4. If rejected, the writer revises based on feedback (up to 3 passes).
    5. Returns the final URL.

    This is WAY better than calling note_create with raw URL content —
    notes from this pipeline are actually synthesized, reviewed, and
    revised before the user sees them.

    Args:
        topic: What the note is about. Be specific — include the angle,
               depth, and any required elements (equations, examples,
               diagrams). First line becomes the title.
        source_content: The full text of the source material (e.g. the
               article the user shared). If empty, the pipeline will run
               research via the research spoke, but if you already have the
               article content from fetch_url_content, pass it here to
               skip redundant research.
        tags: Comma-separated tags for the note.

    Returns: A summary with the published URL and the reviewer's verdict.
    """
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    pipeline = SynthesisPipeline(
        researcher=_make_researcher(source_content or None),
        writer=_note_writer,
        publisher=_note_publisher,
        reviewer=_note_reviewer,
        max_revisions=MAX_REVISIONS,
        status_callback=_post_status,
        item_kind="Note",
        skip_research=bool(source_content),
        pre_fetched_research=source_content or "",
    )

    try:
        result = pipeline.run(topic, tags=tag_list)
    finally:
        _finish()

    return result.summary(item_kind="Note")
