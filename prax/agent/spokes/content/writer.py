"""Writer sub-agent — produces and revises article drafts."""
from __future__ import annotations

import logging

from langchain.agents import create_agent as create_react_agent
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from prax.agent.llm_factory import build_llm
from prax.agent.spokes.content.prompts import WRITER_PROMPT
from prax.settings import settings

logger = logging.getLogger(__name__)


def _build_writer_tools() -> list:
    """Tools available to the Writer — web search and URL fetch for fact-checking."""
    from prax.agent.tools import background_search_tool, fetch_url_content

    return [background_search_tool, fetch_url_content]


def run_writer(
    topic: str,
    research_findings: str,
    feedback: str | None = None,
    previous_draft: str | None = None,
) -> str:
    """Run the Writer sub-agent and return the article markdown.

    On first call, produces a draft from research.  On subsequent calls,
    revises the draft based on reviewer feedback.
    """
    from prax.plugins.llm_config import get_component_config

    cfg = get_component_config("subagent_content_writer")
    llm = build_llm(
        provider=cfg.get("provider"),
        model=cfg.get("model"),
        temperature=cfg.get("temperature") or 0.7,
        tier=cfg.get("tier") or "medium",
    )

    tools = _build_writer_tools()
    graph = create_react_agent(llm, tools)
    prompt = WRITER_PROMPT.format(agent_name=settings.agent_name)

    # Build the task message
    if feedback and previous_draft:
        task = (
            f"## Topic\n{topic}\n\n"
            f"## Research Findings\n{research_findings}\n\n"
            f"## Previous Draft\n{previous_draft}\n\n"
            f"## Reviewer Feedback\n{feedback}\n\n"
            "Please revise the draft addressing all reviewer feedback."
        )
    else:
        task = (
            f"## Topic\n{topic}\n\n"
            f"## Research Findings\n{research_findings}\n\n"
            "Write a publication-ready blog post based on these research findings."
        )

    logger.info("Writer agent starting — topic: %s (revision=%s)", topic[:60], bool(feedback))

    try:
        result = graph.invoke(
            {"messages": [
                SystemMessage(content=prompt),
                HumanMessage(content=task),
            ]},
            config={"recursion_limit": 40},
        )
    except Exception as exc:
        logger.warning("Writer agent failed: %s", exc, exc_info=True)
        return f"Writer agent failed: {exc}"

    # Extract the final AI response (the article draft)
    for msg in reversed(result.get("messages", [])):
        if isinstance(msg, AIMessage) and msg.content:
            draft = msg.content
            logger.info("Writer agent completed — %d chars", len(draft))
            return draft

    return "Writer agent produced no output."
