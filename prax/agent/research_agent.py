"""Research sub-agent — deep, multi-source research on any topic.

Prax delegates to this agent when a question requires thorough investigation:
searching multiple sources, reading pages, cross-referencing claims, and
producing a structured synthesis.  The research agent has access to web search,
URL fetching, arXiv, and all reader plugins.

For broad, multi-topic questions the agent can also decompose the question
into distinct subtopics and spawn parallel sub-research clones via the
``research_subtopics`` tool.  A depth guard prevents runaway recursion.
"""
from __future__ import annotations

import concurrent.futures
import contextvars
import json
import logging

from langchain.agents import create_agent as create_react_agent
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool

from prax.agent.llm_factory import build_llm
from prax.settings import settings

logger = logging.getLogger(__name__)

# Tracks how deep we are in the research-agent recursion chain.  0 is the
# top-level invocation; 1 is a sub-research clone spawned by research_subtopics;
# 2 is the hard cap — sub-sub-research is refused.
_research_depth: contextvars.ContextVar[int] = contextvars.ContextVar(
    "_research_depth", default=0,
)

# Hard caps for decomposition.
_MAX_RESEARCH_DEPTH = 2
_MAX_SUBTOPICS = 5
_MAX_PARALLEL_WORKERS = 3
_SUBTOPIC_TIMEOUT_SECONDS = 90

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

## Multi-Model Consensus (Professor capability)
You have access to `multi_model_query` — it queries multiple AI models
(OpenAI, Anthropic, Google) with the same question and synthesizes a
consensus. This uses **expensive pro-tier models**, so use it wisely.

**When to use multi_model_query:**
- The topic is genuinely contested (experts disagree, sources conflict)
- Factual accuracy is critical (medical, legal, financial, scientific claims)
- The orchestrator explicitly asks for multi-model analysis or "professor"
- You did your own research first and the results are contradictory or uncertain

**When NOT to use it:**
- Simple factual lookups (use web search instead)
- The question has a clear, well-documented answer
- You're just summarizing a single source or paper
- The topic is subjective (opinions, preferences, creative writing)
- You haven't done your own research yet — do YOUR work first

DO NOT be lazy. You are the primary researcher. multi_model_query is your
escalation path for hard problems, not a shortcut for easy ones. If you
use multi_model_query for something you could have answered with a web
search, you're wasting expensive API calls.

If multi_model_query returns "requires at least 2 providers," it means
the user hasn't configured enough API keys for multi-model consensus.
Just report your own findings in that case.

## Task decomposition (research_subtopics)
For broad, multi-topic questions that naturally split into distinct sub-questions,
use research_subtopics to spawn parallel sub-research. Examples:

WHEN to decompose:
- "Compare the architectures of Mamba, Transformer, and RWKV" -> 3 subtopics
- "What are the tradeoffs of 4 different KV cache compression techniques?" -> 4 subtopics
- "Survey the state of the art in X, Y, and Z" -> 3 subtopics

WHEN NOT to decompose (just research directly):
- Simple factual questions
- Single-topic deep dives
- Questions that require integrated reasoning across the whole topic
- Any question you can answer with 2-3 web searches

Cost: Each subtopic spawns a full research agent with its own model call budget.
Only decompose when the parallelism genuinely saves time AND each subtopic is
substantive enough to warrant its own investigation.

Hard limit: Maximum 5 subtopics. Maximum depth 2 (sub-agents cannot further
decompose). If you try to decompose too deeply or too wide, the tool will
refuse — just research the remaining topics directly.

## Rules
- NEVER make up facts, URLs, paper titles, or statistics.
- If your searches return nothing useful, say so honestly.
- Prefer recent sources over old ones when both are available.
- If the question is about a specific paper or project, try arXiv and
  direct URL fetching before general web search.
- Be thorough but concise — the caller will synthesize your report into
  a user-facing response.
"""


def _build_research_tools(depth: int = 0) -> list:
    """Assemble the tool set for the research agent.

    Args:
        depth: Current recursion depth.  At depth 0 (top-level) the agent
            gets the ``research_subtopics`` decomposition tool.  Sub-agents
            (depth >= 1) do NOT — they must just research directly.
    """
    from prax.agent.tools import (
        background_search_tool,
        fetch_url_content,
        get_current_datetime,
    )
    from prax.plugins.loader import get_plugin_loader

    tools = (
        [background_search_tool, fetch_url_content, get_current_datetime]
        + get_plugin_loader().get_tools()
    )

    # Add multi-model consensus (professor) if at least 2 LLM providers
    # are configured. This uses expensive pro-tier models.
    try:
        from prax.agent.spokes.professor.agent import _available_providers, multi_model_query
        if len(_available_providers()) >= 2:
            tools.append(multi_model_query)
            logger.info("Professor capability enabled (%d providers available)",
                        len(_available_providers()))
    except Exception:
        pass  # Professor not available — that's fine

    # Only the top-level research agent can decompose into sub-research.
    if depth == 0:
        tools.append(research_subtopics)

    return tools


def _run_research(question: str, depth: int = 0) -> str:
    """Core research logic — runs a single ReAct research pass.

    This is the inner helper shared by the public ``delegate_research`` tool
    and the ``research_subtopics`` decomposition tool.  It sets the
    ``_research_depth`` contextvar for the duration of the call so any
    nested ``research_subtopics`` invocations see the correct depth.

    Args:
        question: The research question.
        depth: Recursion depth for this invocation.  0 is top-level.
    """
    token = _research_depth.set(depth)
    try:
        logger.info("Research agent (depth=%d): %s", depth, question[:80])

        tools = _build_research_tools(depth=depth)
        if not tools:
            return "No research tools available."

        from prax.plugins.llm_config import get_component_config
        cfg = get_component_config("subagent_research")
        llm = build_llm(
            provider=cfg.get("provider"),
            model=cfg.get("model"),
            temperature=cfg.get("temperature"),
            tier=cfg.get("tier") or "low",
        )
        graph = create_react_agent(llm, tools)

        system_msg = _RESEARCH_PROMPT.format(agent_name=settings.agent_name)

        try:
            result = graph.invoke(
                {"messages": [
                    SystemMessage(content=system_msg),
                    HumanMessage(content=question),
                ]},
                config={"recursion_limit": 30},
            )
        except Exception as exc:
            logger.warning("Research agent failed: %s", exc, exc_info=True)
            return f"Research agent failed: {exc}"

        # Log tool calls for debugging.
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
                    "Research agent completed (depth=%d, %d tool calls): %s",
                    depth, tool_count, msg.content[:80],
                )
                return msg.content

        return "Research agent completed but produced no output."
    finally:
        _research_depth.reset(token)


def _parse_subtopics(subtopics_json) -> list[str]:
    """Tolerant parser for the ``research_subtopics`` input.

    Accepts a Python list directly, a JSON-encoded string, or a JSON-encoded
    string wrapped in extra whitespace.  Raises ValueError with a helpful
    message on failure.
    """
    # Already a list? Great.
    if isinstance(subtopics_json, list):
        items = subtopics_json
    elif isinstance(subtopics_json, str):
        raw = subtopics_json.strip()
        if not raw:
            raise ValueError("subtopics input was empty")
        try:
            items = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Could not parse subtopics as JSON: {exc}. "
                "Expected a JSON array of strings, "
                'e.g. [\"What is X?\", \"How does Y work?\"]'
            ) from exc
    else:
        raise ValueError(
            f"Unsupported subtopics input type: {type(subtopics_json).__name__}. "
            "Expected JSON string or list of strings."
        )

    if not isinstance(items, list):
        raise ValueError(
            "Subtopics must be a JSON array (list), "
            f"got {type(items).__name__}."
        )

    cleaned: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise ValueError(
                f"Each subtopic must be a string, got {type(item).__name__}."
            )
        text = item.strip()
        if text:
            cleaned.append(text)
    return cleaned


@tool
def research_subtopics(subtopics_json: str) -> str:
    """Spawn parallel research on multiple subtopics and return their combined findings.

    Call this when the original question is too broad to answer in a single
    pass and naturally decomposes into distinct sub-questions. Each subtopic
    becomes its own research task run by a clone of this agent.

    Args:
        subtopics_json: JSON array of sub-questions, e.g.:
            ["What is X?", "How does Y compare to Z?", "What are the tradeoffs of W?"]

    Returns: Combined research report with each subtopic as a clearly labeled section.
    """
    current_depth = _research_depth.get()
    if current_depth >= _MAX_RESEARCH_DEPTH:
        msg = (
            f"research_subtopics: depth limit reached (current depth "
            f"{current_depth}, max {_MAX_RESEARCH_DEPTH}).  Sub-research "
            "agents cannot further decompose — research the remaining "
            "topics directly instead."
        )
        logger.warning(msg)
        return msg

    try:
        subtopics = _parse_subtopics(subtopics_json)
    except ValueError as exc:
        return f"research_subtopics: {exc}"

    if not subtopics:
        return (
            "research_subtopics: received an empty list of subtopics.  "
            "Provide at least one sub-question, "
            'e.g. [\"What is X?\", \"How does Y work?\"].'
        )

    warning_note = ""
    if len(subtopics) > _MAX_SUBTOPICS:
        warning_note = (
            f"\n\n(Note: {len(subtopics)} subtopics requested; truncated to "
            f"the first {_MAX_SUBTOPICS} — hard limit for cost control.)\n"
        )
        logger.warning(
            "research_subtopics truncating %d subtopics to %d",
            len(subtopics), _MAX_SUBTOPICS,
        )
        subtopics = subtopics[:_MAX_SUBTOPICS]

    child_depth = current_depth + 1
    logger.info(
        "research_subtopics spawning %d parallel sub-researches at depth %d",
        len(subtopics), child_depth,
    )

    def _worker(subtopic: str) -> str:
        # Run inside a copy of the caller's context so the child sees the
        # incremented depth (and any other contextvars) but cannot leak
        # mutations back into the parent.
        ctx = contextvars.copy_context()

        def _call():
            _research_depth.set(child_depth)
            return _run_research(subtopic, depth=child_depth)

        return ctx.run(_call)

    results: list[tuple[str, str]] = []
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(_MAX_PARALLEL_WORKERS, len(subtopics)),
    ) as executor:
        future_to_topic = {
            executor.submit(_worker, topic): topic for topic in subtopics
        }
        # Preserve the original ordering in the final report.
        topic_to_future = {topic: fut for fut, topic in future_to_topic.items()}
        for topic in subtopics:
            fut = topic_to_future[topic]
            try:
                findings = fut.result(timeout=_SUBTOPIC_TIMEOUT_SECONDS)
            except concurrent.futures.TimeoutError:
                findings = (
                    f"[error] Sub-research for this subtopic timed out after "
                    f"{_SUBTOPIC_TIMEOUT_SECONDS} seconds."
                )
                logger.warning("Subtopic timed out: %s", topic[:80])
                fut.cancel()
            except Exception as exc:
                findings = f"[error] Sub-research failed: {exc}"
                logger.warning(
                    "Subtopic failed: %s — %s", topic[:80], exc, exc_info=True,
                )
            results.append((topic, findings))

    sections = []
    for idx, (topic, findings) in enumerate(results, start=1):
        sections.append(f"## Subtopic {idx}: {topic}\n\n{findings}".rstrip())

    combined = "\n\n".join(sections)
    if warning_note:
        combined = combined + warning_note
    return combined


@tool
def delegate_research(question: str) -> str:
    """Delegate a research question to a dedicated research agent.

    The research agent will search multiple sources, read primary documents,
    cross-reference claims, and return a structured report with citations.

    **ALWAYS use this for questions about external topics, current events,
    academic papers, technical subjects, or "what's out there" queries.**
    The research agent pulls from the live web; the memory spoke only
    knows what the user has personally told Prax.

    Use this for questions that need depth:
    - "What are the latest findings on X?" — external knowledge, use research
    - "What are current best practices for Y?" — external, use research
    - "Find the paper about Z and summarize the key results"
    - "Compare approaches A and B — what does the literature say?"
    - "What's the current state of the art in Q?"
    - "How does [external concept] work?"
    - "What are recent developments in [field]?"

    DO NOT route these to delegate_memory — memory is for the user's personal
    facts (preferences, timezone, past conversations about their projects).
    Research is for the outside world. When in doubt, if the question is
    about "the world" rather than "me/us/my stuff," use research.

    Args:
        question: A clear, self-contained research question.  Include any
                  context the agent needs — it cannot see your conversation.
    """
    logger.info("Research agent delegated: %s", question[:80])
    from prax.services.teamwork_hooks import post_to_channel, set_role_status
    set_role_status("Researcher", "working")

    try:
        content = _run_research(question, depth=0)
    finally:
        set_role_status("Researcher", "idle")

    # Route findings to #research channel.
    try:
        post_to_channel("research", content[:3000], agent_name="Researcher")
    except Exception:
        logger.debug("post_to_channel failed", exc_info=True)
    return content


def build_research_tools() -> list:
    """Return the research delegation tool for the main agent."""
    return [delegate_research]
