"""Professor spoke agent — multi-model consensus for high-stakes research.

Queries multiple LLM providers (OpenAI, Anthropic, Google) with the same
question, then synthesizes a consensus answer highlighting agreement,
disagreement, and unique insights from each model.

Use for: complex research, fact verification, important decisions,
nuanced analysis where a single model's perspective isn't enough.
"""
from __future__ import annotations

import logging

from langchain_core.tools import tool

from prax.agent.spokes._runner import run_spoke
from prax.settings import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are the Professor Agent — a multi-model research synthesizer.
Your specialty is querying multiple AI models on the same question and
producing a rigorous consensus analysis.

You exist because individual AI models have blind spots. By triangulating
across models from different providers (OpenAI, Anthropic, Google), you
catch errors, fill gaps, and produce more reliable answers than any single model.

IMPORTANT: Use pro-tier models for maximum quality. This spoke is for
high-stakes research, not casual questions.

Workflow:
1. Receive the research question
2. Call multi_model_query with the question
3. Return the synthesized consensus to the orchestrator

Do NOT add your own analysis on top — the synthesis IS the output.
"""


# ---------------------------------------------------------------------------
# Internal tools
# ---------------------------------------------------------------------------

def _available_providers() -> list[dict]:
    """Return a list of available provider configs for multi-model querying.

    Each entry is a dict with 'provider' and 'model' keys, using pro-tier
    models where possible.
    """
    from prax.agent.model_tiers import Tier, get_tier_configs

    providers: list[dict] = []
    tier_configs = get_tier_configs()

    # Prefer pro tier model if available, else fall back to high tier
    pro_cfg = tier_configs[Tier.PRO]
    high_cfg = tier_configs[Tier.HIGH]

    if settings.openai_key:
        # Use the pro model name if pro is enabled, else high, else default
        if pro_cfg.enabled:
            model = pro_cfg.model
        elif high_cfg.enabled:
            model = high_cfg.model
        else:
            model = settings.base_model
        providers.append({"provider": "openai", "model": model})

    if settings.anthropic_key:
        # Anthropic pro tier: claude-opus-4-20250514; high: claude-sonnet-4-20250514
        providers.append({
            "provider": "anthropic",
            "model": "claude-sonnet-4-20250514",
        })

    if settings.google_vertex_project and settings.google_vertex_location:
        providers.append({
            "provider": "google",
            "model": "gemini-2.5-pro",
        })

    return providers


def _query_single_model(
    provider: str, model: str, question: str,
) -> dict[str, str]:
    """Query a single model and return its response.

    Returns a dict with 'provider', 'model', and 'response' keys.
    On error, 'response' contains the error description.
    """
    from prax.agent.llm_factory import build_llm

    label = f"{provider}/{model}"
    logger.info("Professor querying %s", label)

    try:
        llm = build_llm(provider=provider, model=model, temperature=0.3)
        result = llm.invoke(
            f"Answer the following question thoroughly and accurately. "
            f"Provide specific facts, data, and reasoning.\n\n"
            f"Question: {question}"
        )
        response = result.content if hasattr(result, "content") else str(result)
        logger.info("Professor received response from %s (%d chars)", label, len(response))
        return {"provider": provider, "model": model, "response": response}
    except Exception as exc:
        logger.warning("Professor failed to query %s: %s", label, exc)
        return {
            "provider": provider,
            "model": model,
            "response": f"[ERROR querying {label}: {exc}]",
        }


def _synthesize_responses(
    question: str, responses: list[dict[str, str]],
) -> str:
    """Use the default LLM to synthesize a consensus from multiple model responses."""
    from prax.agent.llm_factory import build_llm

    # Build the synthesis prompt with all model responses
    response_sections = []
    for resp in responses:
        label = f"{resp['provider']}/{resp['model']}"
        response_sections.append(
            f"### Response from {label}\n{resp['response']}"
        )

    all_responses = "\n\n---\n\n".join(response_sections)

    synthesis_prompt = (
        f"You are synthesizing responses from {len(responses)} different AI models "
        f"to the same question. Analyze their responses and produce a structured "
        f"consensus report.\n\n"
        f"## Original Question\n{question}\n\n"
        f"## Model Responses\n\n{all_responses}\n\n"
        f"## Your Task\n"
        f"Produce a structured consensus in EXACTLY this format:\n\n"
        f"## Multi-Model Consensus\n\n"
        f"### Agreement (all models concur)\n"
        f"- Point 1\n"
        f"- Point 2\n\n"
        f"### Disagreement (models differ)\n"
        f"- Topic: Model A says X, Model B says Y\n\n"
        f"### Unique Insights\n"
        f"- Model A raised: ...\n"
        f"- Model B noted: ...\n\n"
        f"### Synthesis\n"
        f"[Combined answer incorporating the strongest elements from each model]\n\n"
        f"Be thorough. Every claim in the synthesis should be traceable to at least "
        f"one model's response. Flag any factual claims where models disagree."
    )

    try:
        llm = build_llm(temperature=0.2, tier="high")
        result = llm.invoke(synthesis_prompt)
        return result.content if hasattr(result, "content") else str(result)
    except Exception as exc:
        logger.warning("Professor synthesis failed: %s", exc)
        # Fallback: return raw responses if synthesis fails
        return (
            f"## Multi-Model Consensus\n\n"
            f"*Synthesis step failed ({exc}). Raw responses below.*\n\n"
            f"{all_responses}"
        )


@tool
def multi_model_query(question: str, models: str = "auto") -> str:
    """Query multiple AI models and synthesize their responses.

    Args:
        question: The question to ask all models.
        models: Comma-separated model list, or "auto" for default set.
    """
    # Determine which models to query
    if models == "auto":
        providers = _available_providers()
    else:
        # Parse comma-separated list: "openai/gpt-5.4,anthropic/claude-sonnet-4-20250514"
        providers = []
        for entry in models.split(","):
            entry = entry.strip()
            if "/" in entry:
                prov, mod = entry.split("/", 1)
                providers.append({"provider": prov.strip(), "model": mod.strip()})
            else:
                logger.warning(
                    "Skipping invalid model spec '%s' — expected 'provider/model'",
                    entry,
                )

    if len(providers) < 2:
        available_names = [f"{p['provider']}/{p['model']}" for p in providers]
        return (
            f"Multi-model query requires at least 2 providers, but only "
            f"{len(providers)} available: {available_names}. "
            f"Configure additional API keys (OPENAI_KEY, ANTHROPIC_KEY, "
            f"GOOGLE_VERTEX_PROJECT) to enable more providers."
        )

    # Query each model sequentially
    responses: list[dict[str, str]] = []
    for prov in providers:
        resp = _query_single_model(prov["provider"], prov["model"], question)
        responses.append(resp)

    # Count successful responses
    successful = [r for r in responses if not r["response"].startswith("[ERROR")]
    if len(successful) < 2:
        error_details = "\n".join(
            f"- {r['provider']}/{r['model']}: {r['response'][:200]}"
            for r in responses
        )
        return (
            f"Only {len(successful)} model(s) responded successfully "
            f"(minimum 2 required).\n\nDetails:\n{error_details}"
        )

    # Synthesize consensus
    logger.info(
        "Professor synthesizing %d responses (%d successful)",
        len(responses), len(successful),
    )
    return _synthesize_responses(question, responses)


def _build_tools() -> list:
    """Return the internal tools available to the Professor spoke."""
    return [multi_model_query]


# ---------------------------------------------------------------------------
# Delegation function — what the orchestrator calls
# ---------------------------------------------------------------------------

@tool
def delegate_professor(task: str) -> str:
    """Delegate a research question to the Professor for multi-model analysis.

    The Professor queries multiple AI models (e.g., GPT, Claude, Gemini) with
    the same question, then synthesizes a consensus answer highlighting:
    - Points all models agree on (high confidence)
    - Points where models disagree (needs investigation)
    - Unique insights from individual models

    Use for: complex research, fact verification, important decisions,
    nuanced analysis where a single model's perspective isn't enough.

    Args:
        task: The research question or analysis task. Be specific and detailed.
    """
    prompt = SYSTEM_PROMPT.format(agent_name=settings.agent_name)
    return run_spoke(
        task=task,
        system_prompt=prompt,
        tools=_build_tools(),
        config_key="subagent_professor",
        default_tier="pro",
        role_name="Professor",
        channel=None,
        recursion_limit=20,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def build_spoke_tools() -> list:
    """Return the delegation tool for the main agent."""
    return [delegate_professor]
