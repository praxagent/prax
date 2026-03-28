"""Reviewer sub-agent — adversarial quality review with visual inspection.

Uses a different LLM provider than the Writer when possible (diverse agents
produce better reviews per the multi-agent debate literature).
"""
from __future__ import annotations

import logging

from langchain.agents import create_agent as create_react_agent
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from prax.agent.llm_factory import build_llm
from prax.agent.spokes.content.prompts import REVIEWER_PROMPT
from prax.settings import settings

logger = logging.getLogger(__name__)

# Provider → recommended review model.  Used when auto-selecting a different
# provider from the Writer.  Users can override via llm_routing.yaml.
_PROVIDER_REVIEW_MODELS: dict[str, str] = {
    "openai": "gpt-5.4",
    "anthropic": "claude-sonnet-4-20250514",
    "google": "gemini-2.5-pro",
}


def _available_providers() -> list[str]:
    """Return providers that have API keys configured."""
    providers = []
    if settings.openai_key:
        providers.append("openai")
    if settings.anthropic_key:
        providers.append("anthropic")
    if settings.google_vertex_project and settings.google_vertex_location:
        providers.append("google")
    return providers


def _pick_reviewer_llm(writer_provider: str | None = None):
    """Build an LLM for the Reviewer, preferring a different provider.

    Priority:
    1. Explicit config in llm_routing.yaml (user always wins)
    2. Different provider + that provider's high-tier model
    3. Same provider, HIGH tier (at least a different model from Writer's MEDIUM)
    """
    from prax.plugins.llm_config import get_component_config

    cfg = get_component_config("subagent_content_reviewer")

    # If the user explicitly configured the reviewer, respect that.
    if cfg.get("provider") or cfg.get("model"):
        return build_llm(
            provider=cfg.get("provider"),
            model=cfg.get("model"),
            temperature=cfg.get("temperature") or 0.3,
            tier=cfg.get("tier") or "high",
        )

    # Auto-select: try a different provider for diversity.
    writer_prov = (writer_provider or settings.default_llm_provider).lower()
    available = _available_providers()

    for candidate in available:
        if candidate != writer_prov and candidate in _PROVIDER_REVIEW_MODELS:
            model = _PROVIDER_REVIEW_MODELS[candidate]
            logger.info(
                "Reviewer using diverse provider: %s (%s) — Writer uses %s",
                candidate, model, writer_prov,
            )
            return build_llm(
                provider=candidate,
                model=model,
                temperature=cfg.get("temperature") or 0.3,
            )

    # Fallback: same provider, higher tier.
    logger.info("Reviewer using same provider (%s) at HIGH tier", writer_prov)
    return build_llm(
        provider=writer_prov,
        temperature=cfg.get("temperature") or 0.3,
        tier=cfg.get("tier") or "high",
    )


def _build_reviewer_tools() -> list:
    """Tools available to the Reviewer — browser for visual inspection, URL fetch."""
    from prax.agent.spokes.browser.agent import delegate_browser
    from prax.agent.tools import fetch_url_content

    return [delegate_browser, fetch_url_content]


def run_reviewer(
    draft: str,
    published_url: str | None = None,
    writer_provider: str | None = None,
    pass_number: int = 1,
) -> str:
    """Run the Reviewer sub-agent and return structured feedback.

    The feedback starts with APPROVED or REVISE, followed by categorized issues.
    """
    llm = _pick_reviewer_llm(writer_provider)
    tools = _build_reviewer_tools()
    graph = create_react_agent(llm, tools)
    prompt = REVIEWER_PROMPT.format(agent_name=settings.agent_name)

    task_parts = [f"## Draft (pass {pass_number})\n{draft}"]
    if published_url:
        task_parts.append(
            f"\n\n## Published URL\n{published_url}\n"
            "Please use delegate_browser to navigate to this URL, take a screenshot, "
            "and check the rendered page for visual issues (broken LaTeX, missing "
            "diagrams, layout problems)."
        )
    task_parts.append("\n\nProvide your review following the output format in your instructions.")

    task = "\n".join(task_parts)

    logger.info("Reviewer agent starting — pass %d, url=%s", pass_number, published_url or "none")

    try:
        result = graph.invoke(
            {"messages": [
                SystemMessage(content=prompt),
                HumanMessage(content=task),
            ]},
            config={"recursion_limit": 30},
        )
    except Exception as exc:
        logger.warning("Reviewer agent failed: %s", exc, exc_info=True)
        return f"APPROVED\n\nReviewer failed ({exc}) — publishing as-is."

    for msg in reversed(result.get("messages", [])):
        if isinstance(msg, AIMessage) and msg.content:
            review = msg.content
            logger.info("Reviewer agent completed (pass %d): %s", pass_number, review[:120])
            return review

    return "APPROVED\n\nReviewer produced no output — publishing as-is."
