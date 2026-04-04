"""Context window management — budgeting, compaction, and overflow protection.

Implements the strategies from Anthropic's "Effective Context Engineering":
1. Token budgeting — allocates context window across sections
2. History truncation — sliding window with oldest-first removal
3. Tool result clearing — strips raw tool outputs from deep history
4. Compaction — LLM-summarizes old turns, preserving decisions and bugs
5. Overflow protection — validates total token count before LLM invocation

The context manager sits between the orchestrator and graph.invoke(),
ensuring the message payload never exceeds the model's context window.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------

def count_tokens(text: str, model: str = "gpt-4") -> int:
    """Count tokens using tiktoken. Falls back to word-based estimate."""
    try:
        from prax.token_management import num_tokens_from_string
        return num_tokens_from_string(text, model)
    except Exception:
        # Fallback: ~4 chars per token
        return len(text) // 4


def count_message_tokens(messages: list[BaseMessage], model: str = "gpt-4") -> int:
    """Count total tokens across all messages."""
    total = 0
    for msg in messages:
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        total += count_tokens(content, model) + 4  # overhead per message
    return total


# ---------------------------------------------------------------------------
# Context budget
# ---------------------------------------------------------------------------

# Known context windows for specific models (tokens).
# Leave ~20% headroom for the response.
_MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    # OpenAI
    "gpt-5.4-nano": 12_000,
    "gpt-5.4-mini": 100_000,
    "gpt-5.4": 100_000,
    "gpt-5.4-pro": 180_000,
    "gpt-4o": 100_000,
    "gpt-4o-mini": 100_000,
    "gpt-4-turbo": 100_000,
    "gpt-4": 6_000,
    "o3-mini": 100_000,
    "o3": 150_000,
    # Anthropic
    "claude-opus-4-6": 160_000,
    "claude-sonnet-4-6": 160_000,
    "claude-haiku-4-5-20251001": 160_000,
    "claude-3-5-sonnet-20241022": 160_000,
    "claude-3-haiku-20240307": 160_000,
    # Google
    "gemini-2.0-flash": 800_000,
    "gemini-2.5-pro": 800_000,
    # DeepSeek
    "deepseek-chat": 50_000,
    "deepseek-reasoner": 50_000,
    # Local / Ollama
    "qwen3-8b": 25_000,
}

# Fallback limits per tier when the model isn't in the lookup table
_TIER_FALLBACKS = {
    "low": 12_000,
    "medium": 50_000,
    "high": 100_000,
    "pro": 160_000,
}


@dataclass
class ContextBudget:
    """Token allocation for each context section."""
    system_prompt: int = 0
    memory: int = 0
    history: int = 0
    current_turn: int = 0
    total: int = 0
    limit: int = 0
    model: str = ""
    overflow: bool = False


def get_context_limit(tier: str = "low", model: str = "") -> int:
    """Return the context token limit for a specific model or tier.

    Checks the model name against known context windows first.
    Falls back to the tier-based default if the model isn't recognized.
    This ensures we use the actual model's capacity, not a lowest
    common denominator.
    """
    if model:
        # Try exact match
        if model in _MODEL_CONTEXT_WINDOWS:
            return _MODEL_CONTEXT_WINDOWS[model]
        # Try substring match (e.g., "claude-sonnet-4-6" matches "claude-sonnet-4-6-xxxx")
        for known, limit in _MODEL_CONTEXT_WINDOWS.items():
            if known in model or model in known:
                return limit

    return _TIER_FALLBACKS.get(tier, _TIER_FALLBACKS["low"])


# ---------------------------------------------------------------------------
# Tool result clearing
# ---------------------------------------------------------------------------

def clear_old_tool_results(
    messages: list[BaseMessage],
    keep_last_n: int = 6,
) -> list[BaseMessage]:
    """Replace old tool results with a short summary.

    Tool results deep in history are rarely needed — the agent's
    interpretation (in the subsequent AIMessage) is sufficient.
    Keeps the last N tool results intact for recency.

    This is "one of the safest, lightest-touch forms of compaction"
    per Anthropic's context engineering guide.
    """
    # Find all ToolMessage indices
    tool_indices = [
        i for i, m in enumerate(messages) if isinstance(m, ToolMessage)
    ]

    if len(tool_indices) <= keep_last_n:
        return messages  # nothing to clear

    # Clear all but the last N
    indices_to_clear = set(tool_indices[:-keep_last_n])
    result = []
    for i, msg in enumerate(messages):
        if i in indices_to_clear:
            # Replace with a compact stub
            tool_name = getattr(msg, "name", "") or "tool"
            result.append(ToolMessage(
                content=f"[Result from {tool_name} — cleared for context efficiency]",
                tool_call_id=getattr(msg, "tool_call_id", ""),
                name=getattr(msg, "name", None),
            ))
        else:
            result.append(msg)
    return result


# ---------------------------------------------------------------------------
# History truncation (sliding window)
# ---------------------------------------------------------------------------

def truncate_history(
    messages: list[BaseMessage],
    max_tokens: int,
    model: str = "gpt-4",
) -> list[BaseMessage]:
    """Truncate conversation history to fit within a token budget.

    Keeps the most recent messages. Drops oldest messages first.
    Always preserves the SystemMessage (if present) and the last
    HumanMessage.

    Returns a potentially shorter list of messages.
    """
    if not messages:
        return messages

    current_tokens = count_message_tokens(messages, model)
    if current_tokens <= max_tokens:
        return messages

    # Separate system message (always kept) from conversation
    system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
    conv_msgs = [m for m in messages if not isinstance(m, SystemMessage)]

    system_tokens = count_message_tokens(system_msgs, model) if system_msgs else 0
    available = max_tokens - system_tokens

    # Drop oldest messages until we fit
    while conv_msgs and count_message_tokens(conv_msgs, model) > available:
        dropped = conv_msgs.pop(0)
        logger.debug(
            "Context truncation: dropped %s message (%d tokens)",
            type(dropped).__name__,
            count_tokens(dropped.content if isinstance(dropped.content, str) else str(dropped.content), model),
        )

    # Prepend a notice that history was truncated
    if conv_msgs and not isinstance(conv_msgs[0], HumanMessage):
        # Ensure we start with a valid message pair
        while conv_msgs and not isinstance(conv_msgs[0], HumanMessage):
            conv_msgs.pop(0)

    return system_msgs + conv_msgs


# ---------------------------------------------------------------------------
# Compaction (LLM-based summarization)
# ---------------------------------------------------------------------------

def compact_history(
    messages: list[BaseMessage],
    max_tokens: int,
    model: str = "gpt-4",
    tier: str = "low",
) -> list[BaseMessage]:
    """Compact old conversation turns via LLM summarization.

    When the conversation exceeds max_tokens, the oldest turns are
    summarized into a single SystemMessage that preserves:
    - Key decisions and their reasoning
    - Unresolved issues or bugs
    - Important context the agent needs to continue
    - User preferences learned during the conversation

    The most recent turns are kept verbatim.
    """
    current_tokens = count_message_tokens(messages, model)
    if current_tokens <= max_tokens:
        return messages

    # Separate system and conversation
    system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
    conv_msgs = [m for m in messages if not isinstance(m, SystemMessage)]

    if len(conv_msgs) < 6:
        # Too few messages to compact meaningfully — fall back to truncation
        return truncate_history(messages, max_tokens, model)

    # Split: older half gets summarized, recent half stays verbatim
    split_point = len(conv_msgs) // 2
    old_msgs = conv_msgs[:split_point]
    recent_msgs = conv_msgs[split_point:]

    # Build a text representation of old messages for summarization
    old_text = ""
    for msg in old_msgs:
        role = type(msg).__name__.replace("Message", "")
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        # Truncate very long tool results
        if isinstance(msg, ToolMessage) and len(content) > 200:
            content = content[:200] + "..."
        old_text += f"[{role}] {content}\n\n"

    # Summarize via LLM
    try:
        from prax.agent.llm_factory import build_llm

        llm = build_llm(config_key="context_compaction", default_tier=tier)
        summary_prompt = (
            "Summarize this conversation history into a concise context block. "
            "Preserve:\n"
            "- Key decisions and their reasoning\n"
            "- Unresolved issues or bugs mentioned\n"
            "- User preferences and corrections\n"
            "- Important facts learned about the user\n"
            "- File paths, URLs, or identifiers referenced\n\n"
            "Discard:\n"
            "- Redundant tool outputs\n"
            "- Superseded information\n"
            "- Pleasantries and filler\n\n"
            f"Conversation to summarize:\n{old_text[:8000]}\n\n"
            "Summary (be concise but preserve critical context):"
        )
        result = llm.invoke(summary_prompt)
        summary = result.content.strip()

        # Create a compacted history
        compaction_msg = SystemMessage(
            content=f"[COMPACTED CONVERSATION HISTORY]\n{summary}\n[END COMPACTED HISTORY]"
        )

        logger.info(
            "Context compaction: %d messages → summary (%d tokens) + %d recent messages",
            len(old_msgs),
            count_tokens(summary, model),
            len(recent_msgs),
        )

        return system_msgs + [compaction_msg] + recent_msgs

    except Exception:
        logger.warning("Context compaction failed, falling back to truncation", exc_info=True)
        return truncate_history(messages, max_tokens, model)


# ---------------------------------------------------------------------------
# Main entry point — prepare context for LLM
# ---------------------------------------------------------------------------

def prepare_context(
    messages: list[BaseMessage],
    tier: str = "low",
    model: str = "",
) -> tuple[list[BaseMessage], ContextBudget]:
    """Prepare messages for LLM invocation with context management.

    Applies, in order:
    1. Tool result clearing (old tool outputs → stubs)
    2. Token counting and budget validation
    3. Compaction or truncation if over budget

    The context limit is resolved from the specific model name first,
    falling back to the tier-based default. This means a Claude model
    with 200K context gets a much larger budget than a nano model with
    16K — no lowest common denominator.

    Returns (prepared_messages, budget_info).
    """
    limit = get_context_limit(tier, model)

    # Step 1: Clear old tool results
    messages = clear_old_tool_results(messages, keep_last_n=6)

    # Step 2: Count tokens
    total = count_message_tokens(messages, model)

    # Step 3: If over budget, try compaction first, then truncation
    if total > limit:
        logger.info(
            "Context over budget: %d tokens > %d limit (tier=%s). Compacting...",
            total, limit, tier,
        )
        messages = compact_history(messages, limit, model, tier)
        total = count_message_tokens(messages, model)

        # If still over after compaction, hard truncate
        if total > limit:
            logger.warning(
                "Still over budget after compaction: %d > %d. Truncating...",
                total, limit,
            )
            messages = truncate_history(messages, limit, model)
            total = count_message_tokens(messages, model)

    # Build budget report
    system_tokens = sum(
        count_tokens(m.content if isinstance(m.content, str) else str(m.content), model)
        for m in messages if isinstance(m, SystemMessage)
    )
    budget = ContextBudget(
        system_prompt=system_tokens,
        history=total - system_tokens,
        total=total,
        limit=limit,
        model=model,
        overflow=total > limit,
    )

    return messages, budget
