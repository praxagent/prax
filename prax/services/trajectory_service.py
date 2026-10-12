"""Real-time trajectory export for fine-tuning.

Appends each conversation turn to a JSONL file in ChatML format as it
happens — no batch processing needed.  Each trajectory is tagged with
outcome metadata (correction, tool_failure, success, etc.) so the
fine-tuning pipeline can filter by quality.

Trajectories are stored in:
    {RECORDS_DIR}/users/{workspace}/trajectories/
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
    """Return the trajectories directory for a user, in the records directory."""
    from prax.services import records
    d = records.user_path(user_id, "trajectories", legacy=".prax/trajectories")
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

        from prax.services import record_chain
        filepath = _trajectories_dir(user_id) / filename
        record_chain.append(filepath, (json.dumps(example, ensure_ascii=False) + "\n").encode("utf-8"))

        logger.debug(
            "Trajectory exported: user=%s outcome=%s file=%s",
            user_id, outcome, filename,
        )
    except Exception:
        logger.debug("Trajectory export failed", exc_info=True)


# --- Full multi-agent trajectories (FULL_TRAJECTORIES_ENABLED) --------------
#
# export_trajectory above keeps only the user's message and the final reply.
# A training run on Prax's own multi-agent traffic needs the rest: the
# orchestrator's tool calls and delegations, and each spoke's own conversation
# (docs/research/mass-multi-agent-self-supervision.md — sub-agent windows were
# most of that method's supervision). One JSONL line per agent per turn, in
# OpenAI chat format with tool calls, linked by session/trace/span ids.

AGENTS_FILE = "agents.jsonl"
_MAX_MESSAGE_CHARS = 16_000


def _content_text(content) -> str:
    if isinstance(content, list):  # provider content blocks
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return str(content or "")


def _clip(text: str) -> str:
    if len(text) <= _MAX_MESSAGE_CHARS:
        return text
    cut = len(text) - _MAX_MESSAGE_CHARS
    return text[:_MAX_MESSAGE_CHARS] + f"\n[… {cut} characters cut]"


def full_chat(messages: list, redact=None) -> list[dict]:
    """*messages* as OpenAI-format chat, tool calls and tool results kept.
    *redact* is applied to every text and argument string (the caller passes
    the turn's secret scrubber); long tool results are clipped."""
    from langchain_core.messages import SystemMessage

    def r(text: str) -> str:
        text = _clip(text)
        return redact(text) if redact else text

    out: list[dict] = []
    for msg in messages:
        if isinstance(msg, SystemMessage):
            out.append({"role": "system", "content": r(_content_text(msg.content))})
        elif isinstance(msg, HumanMessage):
            out.append({"role": "user", "content": r(_content_text(msg.content))})
        elif isinstance(msg, AIMessage):
            entry: dict = {"role": "assistant", "content": r(_content_text(msg.content))}
            calls = []
            for call in getattr(msg, "tool_calls", None) or []:
                args = call.get("args") if isinstance(call, dict) else getattr(call, "args", {})
                name = call.get("name") if isinstance(call, dict) else getattr(call, "name", "")
                cid = call.get("id") if isinstance(call, dict) else getattr(call, "id", "")
                calls.append({"id": cid or "", "type": "function", "function": {
                    "name": name or "",
                    "arguments": r(json.dumps(args or {}, ensure_ascii=False, default=str)),
                }})
            if calls:
                entry["tool_calls"] = calls
            out.append(entry)
        elif isinstance(msg, ToolMessage):
            out.append({"role": "tool", "tool_call_id": getattr(msg, "tool_call_id", "") or "",
                        "name": getattr(msg, "name", "") or "",
                        "content": r(_content_text(msg.content))})
    return out


def export_agent_trajectory(
    user_id: str,
    *,
    role: str,
    task: str,
    response: str,
    messages: list,
    redact=None,
    ids: dict | None = None,
) -> None:
    """Append one agent's full conversation for this turn to ``agents.jsonl``.

    *role* is ``"orchestrator"`` or ``"spoke:<name>"``; *ids* links the lines
    of one turn (``session_id``, ``trace_id``, ``span_id``). The caller checks
    FULL_TRAJECTORIES_ENABLED and passes the secret scrubber as *redact*.
    Fire-and-forget: errors are logged, never raised.
    """
    if not user_id:
        return
    try:
        if role == "orchestrator":
            outcome, _ = _classify_outcome(task, response, messages)
        elif not response:
            outcome = "empty_response"
        elif any(isinstance(m, ToolMessage) and any(
                sig in _content_text(m.content).lower()
                for sig in ("error:", "failed:", "exception:", "traceback"))
                for m in messages):
            outcome = "tool_failure"
        else:
            outcome = "success"
        record = {
            "messages": full_chat(messages, redact),
            "metadata": {
                "role": role,
                "outcome": outcome,
                "user_id": user_id,
                **{k: str(v) for k, v in (ids or {}).items() if v},
                "scheduled": task.startswith(("[SCHEDULED_TASK", "[Reminder]")),
                "timestamp": datetime.now(UTC).isoformat(),
                "agent": settings.agent_name,
                "tool_count": sum(1 for m in messages if isinstance(m, ToolMessage)),
            },
        }
        _append_record_line(_trajectories_dir(user_id) / AGENTS_FILE,
                            json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        logger.debug("Agent trajectory export failed", exc_info=True)


def _append_record_line(path: Path, line: str) -> None:
    """Append *line* to a records file through the hash-chained journal
    (``prax/services/record_chain.py``) where that module exists, so the write
    is journaled like every other record write — a plain append to a
    journaled file would read as tampering. Where it does not exist yet (the
    record chain ships separately), a plain append."""
    data = line.encode("utf-8")
    try:
        from prax.services import record_chain
    except ImportError:
        with open(path, "ab") as f:
            f.write(data)
        return
    record_chain.append(path, data)


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
