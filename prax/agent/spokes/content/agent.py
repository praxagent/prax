"""Content Editor spoke — orchestrates the multi-agent content pipeline.

Pipeline: Research → Write → Publish → Review → Revise (loop) → Final Publish

The Content Editor is a procedural coordinator, not a ReAct agent.  It calls
sub-agents (researcher, writer, reviewer) in sequence and manages the
revision loop.  Prax delegates here via ``delegate_content_editor``.
"""
from __future__ import annotations

import logging

from langchain_core.tools import tool

from prax.settings import settings

logger = logging.getLogger(__name__)

MAX_REVISIONS = 3


def _research(topic: str, notes: str) -> str:
    """Phase 1: Parallel research on the topic."""
    from prax.agent.subagent import _run_subagent

    query = f"Research the following topic thoroughly for a blog post:\n\n{topic}"
    if notes:
        query += f"\n\nAdditional context from the user:\n{notes}"
    query += (
        "\n\nProvide: key findings with source URLs, relevant data/statistics, "
        "background context, different perspectives, and any recent developments."
    )

    logger.info("Content pipeline — Phase 1: Research")
    return _run_subagent(query, "research")


def _write(topic: str, research: str, feedback: str | None = None,
           previous_draft: str | None = None) -> str:
    """Phase 2/4: Write or revise the article."""
    from prax.agent.spokes.content.writer import run_writer

    phase = "Revise" if feedback else "Write"
    logger.info("Content pipeline — Phase: %s", phase)
    return run_writer(topic, research, feedback=feedback, previous_draft=previous_draft)


def _publish(title: str, content: str, tags: list[str] | None = None,
             slug: str | None = None) -> dict:
    """Phase 3/5: Publish or update the Hugo page."""
    from prax.agent.spokes.content.publisher import publish_draft, update_draft

    logger.info("Content pipeline — Phase: Publish (slug=%s)", slug or "new")
    if slug:
        return update_draft(slug, content, title=title)
    return publish_draft(title, content, tags=tags)


def _review(draft: str, url: str | None, pass_number: int) -> str:
    """Phase 3: Adversarial review with visual inspection."""
    from prax.agent.spokes.content.reviewer import run_reviewer

    logger.info("Content pipeline — Phase: Review (pass %d)", pass_number)
    return run_reviewer(
        draft, published_url=url,
        writer_provider=settings.default_llm_provider,
        pass_number=pass_number,
    )


def _is_approved(review: str) -> bool:
    """Check if the reviewer approved the draft.

    The reviewer's output must START with APPROVED (possibly bold-wrapped).
    """
    first_line = review.strip().split("\n")[0].strip()
    # Strip markdown bold markers
    cleaned = first_line.replace("*", "").replace("_", "").strip().upper()
    return cleaned.startswith("APPROVED")


def _post_status(message: str) -> None:
    """Post a status update to TeamWork."""
    try:
        from prax.services.teamwork_hooks import post_to_channel, set_role_status
        set_role_status("Content Editor", "working")
        post_to_channel("content", message, agent_name="Content Editor")
    except Exception:
        pass


def run_content_pipeline(topic: str, notes: str = "", tags: str = "") -> str:
    """Execute the full content creation pipeline.

    Returns a summary with the published URL or an error description.
    """
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    title = topic.strip().split("\n")[0][:120]  # First line as title, capped

    _post_status(f"Starting content pipeline: *{title}*")

    # --- Phase 1: Research ---
    _post_status("Researching topic...")
    research = _research(topic, notes)
    if not research or "failed" in research.lower()[:50]:
        return f"Research phase failed: {research}"

    # --- Phase 2: Write first draft ---
    _post_status("Writing first draft...")
    draft = _write(topic, research)
    if not draft or "failed" in draft.lower()[:50]:
        return f"Writing phase failed: {draft}"

    # --- Phase 3: Publish initial draft ---
    pub = _publish(title, draft, tags=tag_list)
    if "error" in pub:
        return f"Publishing failed: {pub['error']}"

    slug = pub.get("slug", "")
    url = pub.get("url", "")
    _post_status(f"First draft published: {url}")

    # --- Phase 4: Review-Revise loop (max MAX_REVISIONS passes) ---
    for pass_num in range(1, MAX_REVISIONS + 1):
        _post_status(f"Review pass {pass_num}/{MAX_REVISIONS}...")
        review = _review(draft, url, pass_num)

        if _is_approved(review):
            _post_status(f"Approved on pass {pass_num}! Final URL: {url}")
            logger.info("Content pipeline — APPROVED on pass %d", pass_num)
            _finish()
            return (
                f"Blog post published and approved after {pass_num} review pass(es).\n\n"
                f"**{title}**\n{url}\n\n"
                f"Reviewer verdict: {review[:500]}"
            )

        # Revise based on feedback
        _post_status(f"Revising based on feedback (pass {pass_num})...")
        draft = _write(topic, research, feedback=review, previous_draft=draft)
        if not draft or "failed" in draft.lower()[:50]:
            logger.warning("Revision failed on pass %d", pass_num)
            break

        # Re-publish the updated draft
        pub = _publish(title, draft, slug=slug)
        url = pub.get("url", url)

    # Exhausted revision cycles — publish whatever we have
    _post_status(f"Published after {MAX_REVISIONS} revision cycles: {url}")
    _finish()
    return (
        f"Blog post published after {MAX_REVISIONS} revision cycles "
        f"(reviewer did not fully approve).\n\n"
        f"**{title}**\n{url}\n\n"
        f"Last review feedback:\n{review[:500]}"
    )


def _finish() -> None:
    """Set TeamWork status to idle."""
    try:
        from prax.services.teamwork_hooks import set_role_status
        set_role_status("Content Editor", "idle")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Delegation function — what the orchestrator calls
# ---------------------------------------------------------------------------

@tool
def delegate_content_editor(topic: str, notes: str = "", tags: str = "") -> str:
    """Delegate blog post creation to the Content Editor pipeline.

    The Content Editor runs a multi-agent pipeline:
    1. **Research** — gathers sources, data, and context
    2. **Write** — produces a publication-ready draft
    3. **Publish** — deploys to Hugo for live preview
    4. **Review** — adversarial critique + visual inspection of the rendered page
    5. **Revise** — incorporates feedback (up to 3 cycles)

    The Reviewer uses a different LLM provider when available (e.g. Claude
    reviews GPT's writing) for diversity of perspective.

    Use this for:
    - "Write a blog post about quantum error correction"
    - "Create an article comparing React and Vue"
    - "Write a deep-dive on the latest transformer architectures"

    Args:
        topic: What the blog post should be about.  Be specific — include
               scope, angle, target audience if relevant.
        notes: Optional additional context, instructions, or constraints.
               E.g. "focus on practical applications" or "target audience
               is senior engineers".
        tags: Optional comma-separated tags for the published post.
    """
    return run_content_pipeline(topic, notes=notes, tags=tags)


def build_spoke_tools() -> list:
    """Return the delegation tool for the main agent."""
    return [delegate_content_editor]
