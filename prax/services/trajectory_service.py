"""Real-time trajectory export for fine-tuning.

Appends each conversation turn to a JSONL file in ChatML format as it
happens — no batch processing needed.  Each trajectory is tagged with
outcome metadata (correction, tool_failure, success, etc.) so the
fine-tuning pipeline can filter by quality.

Trajectories are stored in:
    {workspace}/{user_id}/.prax/trajectories/
        completed.jsonl   — successful exchanges
        failed.jsonl      — corrections, tool failures, user complaints

The ChatML format is compatible with OpenAI, Unsloth, Axolotl, and
LLaMA-Factory fine-tuning pipelines.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from prax.settings import settings

logger = logging.getLogger(__name__)

# Signals that the user is correcting the agent.
_CORRECTION_SIGNALS = [
    "no,", "no ", "nope", "wrong", "that's not", "that isn't",
    "actually,", "actually ", "i meant", "i said", "not what i",
    "try again", "do it again", "redo", "fix that", "you got it wrong",
    "incorrect", "that's wrong", "not right", "not correct",
]

# Signals of user dissatisfaction (but not explicit correction).
_NEGATIVE_SIGNALS = [
    "doesn't work", "didn't work", "not working", "broken",
    "useless", "unhelpful", "not helpful", "waste of time",
    "forget it", "never mind", "nevermind",
]


def _trajectories_dir(user_id: str) -> Path:
    """Return the trajectories directory for a user."""
    from prax.services.workspace_service import workspace_root
    d = Path(workspace_root(user_id)) / ".prax" / "trajectories"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _classify_outcome(
    user_input: str,
    response: str,
    messages: list,
) -> tuple[str, str]:
    """Classify the trajectory outcome.

    Returns (outcome, file_name):
        - ("success", "completed.jsonl") — normal exchange
        - ("correction", "failed.jsonl") — user corrected the agent
        - ("negative", "failed.jsonl") — user expressed dissatisfaction
        - ("tool_failure", "failed.jsonl") — a tool errored during the turn
        - ("empty_response", "failed.jsonl") — agent produced no output
    """
    if not response:
        return "empty_response", "failed.jsonl"

    # Check if the user's input was a correction of the previous turn
    lower_input = user_input.lower().strip()
    if any(sig in lower_input for sig in _CORRECTION_SIGNALS):
        return "correction", "failed.jsonl"

    if any(sig in lower_input for sig in _NEGATIVE_SIGNALS):
        return "negative", "failed.jsonl"

    # Check for tool errors in the message history
    for msg in messages:
        if isinstance(msg, ToolMessage) and msg.content:
            content = msg.content.lower()
            if any(sig in content for sig in ("error:", "failed:", "exception:", "traceback")):
                return "tool_failure", "failed.jsonl"

    return "success", "completed.jsonl"


def _build_chatml(
    user_input: str,
    response: str,
    messages: list,
) -> dict:
    """Build a ChatML training example from a conversation turn.

    Includes the system prompt, conversation context (up to 6 messages
    of context), the user input, and the assistant response.
    """
    example: dict = {"messages": []}

    # System message
    example["messages"].append({
        "role": "system",
        "content": f"You are {settings.agent_name}, a capable AI assistant.",
    })

    # Context — extract the last few human/assistant exchanges before this turn
    context_msgs = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            context_msgs.append({"role": "user", "content": msg.content})
        elif isinstance(msg, AIMessage) and msg.content:
            context_msgs.append({"role": "assistant", "content": msg.content})

    # Keep last 3 exchanges (6 messages) as context, excluding the current turn
    if len(context_msgs) > 2:
        context = context_msgs[-8:-2]  # skip the final user+assistant (current turn)
        for ctx in context:
            example["messages"].append(ctx)

    # Current turn
    example["messages"].append({"role": "user", "content": user_input})
    example["messages"].append({"role": "assistant", "content": response})

    return example


def export_trajectory(
    user_id: str,
    user_input: str,
    response: str,
    messages: list,
    session_id: str = "",
) -> None:
    """Export a conversation turn as a training trajectory.

    Called at the end of each orchestrator turn. Appends one JSONL line
    to either completed.jsonl or failed.jsonl based on the outcome.

    This is fire-and-forget — errors are logged but never raised.
    """
    if not user_id or not response:
        return

    # Skip scheduled tasks and system-generated inputs
    if user_input.startswith("[SCHEDULED_TASK") or user_input.startswith("[Reminder]"):
        return

    try:
        outcome, filename = _classify_outcome(user_input, response, messages)

        example = _build_chatml(user_input, response, messages)
        example["metadata"] = {
            "user_id": user_id,
            "outcome": outcome,
            "session_id": session_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "agent": settings.agent_name,
            "tool_count": sum(1 for m in messages if isinstance(m, ToolMessage)),
        }

        filepath = _trajectories_dir(user_id) / filename
        with open(filepath, "a") as f:
            f.write(json.dumps(example, ensure_ascii=False) + "\n")

        logger.debug(
            "Trajectory exported: user=%s outcome=%s file=%s",
            user_id, outcome, filename,
        )
    except Exception:
        logger.debug("Trajectory export failed", exc_info=True)


def get_trajectory_stats(user_id: str) -> dict:
    """Return trajectory counts for a user."""
    d = _trajectories_dir(user_id)
    stats = {}
    for filename in ("completed.jsonl", "failed.jsonl"):
        filepath = d / filename
        if filepath.exists():
            count = sum(1 for _ in open(filepath))
            stats[filename.replace(".jsonl", "")] = count
        else:
            stats[filename.replace(".jsonl", "")] = 0
    return stats
