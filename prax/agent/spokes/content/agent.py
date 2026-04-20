"""Content Editor spoke — orchestrates the multi-agent content pipeline.

Two modes:
- **blog** (default): Research → Write → Publish → Review → Revise (loop)
- **course_module**: Sandbox-based rich content via the Course Author sub-agent

The Content Editor is a procedural coordinator, not a ReAct agent.  It uses
the reusable ``SynthesisPipeline`` from ``prax.agent.pipelines`` to orchestrate
the research → write → review → revise loop.  Prax delegates here via
``delegate_content_editor``.
"""
from __future__ import annotations

import logging

from langchain_core.tools import tool

from prax.agent.pipelines import SynthesisPipeline
from prax.settings import settings

logger = logging.getLogger(__name__)

MAX_REVISIONS = 3


# ---------------------------------------------------------------------------
# Phase callables — wired into the SynthesisPipeline
# ---------------------------------------------------------------------------


def _research(topic: str, notes: str) -> str:
    """Phase 1: Research the topic via the research sub-agent."""
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
    """Phase 4: Adversarial review with visual inspection."""
    from prax.agent.spokes.content.reviewer import run_reviewer

    logger.info("Content pipeline — Phase: Review (pass %d)", pass_number)
    return run_reviewer(
        draft, published_url=url,
        writer_provider=settings.default_llm_provider,
        pass_number=pass_number,
    )


# Backwards-compat: older tests call this.
def _is_approved(review: str) -> bool:
    """Check if the reviewer approved the draft."""
    return SynthesisPipeline._is_approved(review)


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

    pipeline = SynthesisPipeline(
        researcher=_research,
        writer=_write,
        publisher=_publish,
        reviewer=_review,
        max_revisions=MAX_REVISIONS,
        status_callback=_post_status,
        item_kind="Blog post",
    )

    try:
        result = pipeline.run(topic, notes=notes, tags=tag_list)
    finally:
        _finish()

    return result.summary(item_kind="Blog post")


def _finish() -> None:
    """Set TeamWork status to idle."""
    try:
        from prax.services.teamwork_hooks import set_role_status
        set_role_status("Content Editor", "idle")
    except Exception:
        pass


def _run_course_author(task: str) -> str:
    """Run the course author sub-agent for rich course module content."""
    from prax.agent.course_author_agent import (
        _COURSE_AUTHOR_PROMPT,
        _build_course_author_tools,
    )
    from prax.agent.spokes._runner import run_spoke

    prompt = _COURSE_AUTHOR_PROMPT.format(agent_name=settings.agent_name)
    return run_spoke(
        task=task,
        system_prompt=prompt,
        tools=_build_course_author_tools(),
        config_key="subagent_codegen",
        default_tier="medium",
        role_name="Content Editor",
        channel="content",
        recursion_limit=80,
    )


# ---------------------------------------------------------------------------
# Delegation function — what the orchestrator calls
# ---------------------------------------------------------------------------

@tool
def delegate_content_editor(
    topic: str, notes: str = "", tags: str = "",
    mode: str = "blog",
) -> str:
    """Delegate content creation to the Content Editor pipeline.

    Two modes:
    - **blog** (default): Multi-agent pipeline — Research → Write → Publish →
      Review → Revise (up to 3 cycles).  The Reviewer uses a different LLM
      provider for adversarial diversity.
    - **course_module**: Rich course content via the sandbox — produces markdown
      with mermaid diagrams, LaTeX, code blocks, and structured pedagogy.
      Include the course_id and module number in the topic.

    Use this for:
    - "Write a blog post about quantum error correction"
    - "Create an article comparing React and Vue"
    - "Write a deep-dive on the latest transformer architectures"
    - "Generate content for Module 3 of course abc123" (mode="course_module")

    Args:
        topic: What to write about.  Be specific — include scope, angle,
               target audience.  For course_module mode, include the course_id
               and module number.
        notes: Optional additional context, instructions, or constraints.
        tags: Optional comma-separated tags (blog mode only).
        mode: "blog" (default) for the full review pipeline, or
              "course_module" for sandbox-based course content.
    """
    if mode == "course_module":
        task = topic
        if notes:
            task += f"\n\nAdditional context: {notes}"
        return _run_course_author(task)
    return run_content_pipeline(topic, notes=notes, tags=tags)


def build_spoke_tools() -> list:
    """Return the delegation tool for the main agent.

    Office-document tools (create_pdf/presentation/spreadsheet) are
    NOT re-exported here — they live in the workspace spoke now.
    Keeping orchestrator tool count below the ~50-tool degradation
    threshold that Anthropic documents for tool selection accuracy.
    """
    return [delegate_content_editor]
