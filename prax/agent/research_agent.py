"""Research sub-agent — deep, multi-source research on any topic.

Prax delegates to this agent when a question requires thorough investigation:
searching multiple sources, reading pages, cross-referencing claims, and
producing a structured synthesis.  The research agent has access to web search,
URL fetching, arXiv, and all reader plugins.
"""
from __future__ import annotations

import logging

from langchain.agents import create_agent as create_react_agent
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool

from prax.agent.llm_factory import build_llm
from prax.settings import settings

logger = logging.getLogger(__name__)

_RESEARCH_PROMPT = """\
You are a research agent for {agent_name}.  Your job is to investigate a
question thoroughly and return a structured, honest report.

## How to research
1. **Search broadly first.**  Run 2-3 searches with different phrasings to
   find the best sources.  Don't stop at the first result.
2. **Read primary sources.**  Fetch the actual pages/papers — don't rely on
   search snippets alone.  Snippets are often misleading or truncated.
3. **Cross-reference.**  If two sources disagree, note the disagreement.
   If only one source says something, flag it as unverified.
4. **Cite everything.**  Every claim in your report should have a source URL.
   If you can't cite it, don't include it.

## What to return
A structured report with:
- **Key findings** — the main answers to the question, with citations
- **Sources** — list of URLs you actually read (not just searched)
- **Confidence notes** — what you're confident about vs. what's uncertain
- **Gaps** — what you couldn't find or verify

## Rules
- NEVER make up facts, URLs, paper titles, or statistics.
- If your searches return nothing useful, say so honestly.
- Prefer recent sources over old ones when both are available.
- If the question is about a specific paper or project, try arXiv and
  direct URL fetching before general web search.
- Be thorough but concise — the caller will synthesize your report into
  a user-facing response.
"""


def _build_research_tools() -> list:
    """Assemble the tool set for the research agent."""
    from prax.agent.tools import (
        background_search_tool,
        fetch_url_content,
        get_current_datetime,
    )
    from prax.plugins.loader import get_plugin_loader

    return (
        [background_search_tool, fetch_url_content, get_current_datetime]
        + get_plugin_loader().get_tools()
    )


@tool
def delegate_research(question: str) -> str:
    """Delegate a research question to a dedicated research agent.

    The research agent will search multiple sources, read primary documents,
    cross-reference claims, and return a structured report with citations.

    Use this for questions that need depth:
    - "What are the latest findings on X?"
    - "Find the paper about Y and summarize the key results"
    - "Compare approaches A and B — what does the literature say?"
    - "What's the current state of the art in Z?"

    Args:
        question: A clear, self-contained research question.  Include any
                  context the agent needs — it cannot see your conversation.
    """
    logger.info("Research agent delegated: %s", question[:80])
    from prax.services.teamwork_hooks import set_role_status, post_to_channel
    set_role_status("Researcher", "working")

    tools = _build_research_tools()
    if not tools:
        return "No research tools available."

    from prax.plugins.llm_config import get_component_config
    cfg = get_component_config("subagent_research")
    llm = build_llm(
        provider=cfg.get("provider"),
        model=cfg.get("model"),
        temperature=cfg.get("temperature"),
    )
    graph = create_react_agent(llm, tools)

    system_msg = _RESEARCH_PROMPT.format(agent_name=settings.agent_name)

    try:
        result = graph.invoke(
            {"messages": [
                SystemMessage(content=system_msg),
                HumanMessage(content=question),
            ]},
            config={"recursion_limit": 80},
        )
    except Exception as exc:
        logger.warning("Research agent failed: %s", exc, exc_info=True)
        return f"Research agent failed: {exc}"

    # Log tool calls for debugging.
    from langchain_core.messages import ToolMessage
    tool_count = 0
    for msg in result.get("messages", []):
        if isinstance(msg, AIMessage):
            for tc in getattr(msg, "tool_calls", []) or []:
                tool_count += 1
                logger.info(
                    "Research agent tool: %s(%s)",
                    tc.get("name"), str(tc.get("args", {}))[:80],
                )

    # Extract the final response.
    for msg in reversed(result.get("messages", [])):
        if isinstance(msg, AIMessage) and msg.content:
            logger.info(
                "Research agent completed (%d tool calls): %s",
                tool_count, msg.content[:80],
            )
            set_role_status("Researcher", "idle")
            # Route findings to #research channel.
            post_to_channel("research", msg.content[:3000], agent_name="Researcher")
            return msg.content

    set_role_status("Researcher", "idle")
    return "Research agent completed but produced no output."


def build_research_tools() -> list:
    """Return the research delegation tool for the main agent."""
    return [delegate_research]
