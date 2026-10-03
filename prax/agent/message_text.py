"""Flatten LangChain message content to plain text.

``BaseMessage.content`` is a **string for some providers and a list of content
blocks for others** (``[{"type": "text", "text": "..."}, ...]``) — notably
OpenAI's Responses API (the `-pro`/o-series path, and every OpenAI model once
keyless mode routes through the proxy) and Anthropic. Code that assumes `str`
fails two ways against the list form, and only one of them is loud:

- ``content + "suffix"`` raises ``TypeError: can only concatenate list (not
  "str") to list`` — a hard crash on every orchestrator turn.
- ``str(content)`` silently yields ``"[{'type': 'text', 'text': '...'}]"``,
  the Python repr, which then flows into markers, ``startswith`` checks,
  audits and the user's reply as garbage.

Both were real: the spoke runner hit the silent one (a prefix check on
preserved tool evidence), and the orchestrator hit the loud one the first time
a live eval ran an o-series model through the secrets proxy. Every extraction
of text from a message goes through ``message_text`` so a third provider
shape is one edit, not a codebase sweep.
"""
from __future__ import annotations

import json
from typing import Any

from prax.agent.hard_floors import CREDENTIAL_TOOLS
from prax.agent.turn_secrets import scrub

__all__ = [
    "message_text", "content_text", "tool_output_text",
    "preview_for_tool", "args_preview_for_tool", "error_preview_for_tool",
    "WITHHELD_OUTPUT",
]

#: What every observability sink shows instead of a credential tool's output.
WITHHELD_OUTPUT = "(output withheld: credential tool)"


def content_text(raw: Any) -> str:
    """Return the plain text of a message ``content`` value, any shape."""
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        parts: list[str] = []
        for block in raw:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                # Skip non-text blocks (reasoning summaries, images, tool-use
                # payloads): they are not part of the visible reply, and
                # concatenating them would leak internals into the answer.
                btype = block.get("type")
                if btype in {"reasoning", "thinking", "image", "image_url",
                             "tool_use", "tool_result"}:
                    continue
                # Common shapes: {"type": "text", "text": "..."} (Anthropic /
                # Responses API), {"text": "..."}, or {"content": "..."}.
                text = block.get("text") or block.get("content") or ""
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(p for p in parts if p)
    return str(raw)


def message_text(msg: Any) -> str:
    """Return the plain text of a message object (or "" if it has none)."""
    return content_text(getattr(msg, "content", None))


def _is_content_block(item: Any) -> bool:
    return isinstance(item, str) or (
        isinstance(item, dict) and any(k in item for k in ("type", "text", "content"))
    )


def tool_output_text(output: Any) -> str:
    """Return the plain text of a tool result, as a tool callback receives it.

    ``on_tool_end`` is handed a ``ToolMessage`` whenever the tool was invoked
    with a ToolCall — every ToolNode call — and the tool's raw return value
    otherwise: a str, a dict, a list of records, a ``Command``, anything.
    ``str()`` of the message is its pydantic repr (``content='…' name='…'
    tool_call_id='…'``) and slicing the message raises ``TypeError``, so read
    its content and flatten it. Never raises: callbacks run on every tool call,
    and a preview must not cost the span or trace node it decorates.
    """
    if output is None:
        return ""
    try:
        raw = getattr(output, "content", output)
        if isinstance(raw, list) and not all(_is_content_block(b) for b in raw):
            # A list of records, not message content: content_text keeps only
            # text blocks and would reduce it to "".
            return str(raw)
        return content_text(raw)
    except Exception:  # noqa: BLE001 - a broken __str__ must not escape a callback
        return f"<unprintable {type(output).__name__}>"


def preview_for_tool(tool_name: str | None, output: Any, limit: int | None) -> str:
    """A tool result as an observability sink may show it, capped at *limit*
    (``None``: uncapped).

    Credential tools (``hard_floors.CREDENTIAL_TOOLS``) return secrets in plain
    text — ``browser_login`` hands back ``username=…\\npassword=…`` under the
    default ``BROWSER_SECRETS_OUT_OF_CONTEXT=false`` — and every place that
    previews tool output would otherwise copy the password out of the model's
    context into storage and screens that outlive it. Their output is replaced
    by ``WITHHELD_OUTPUT``, whatever it says; the model still receives it.
    Any other tool's output has this turn's registered secret values masked
    (``turn_secrets.scrub``) before it is capped.

    Exactly what uses it, and so is covered: the execution trace's tool spans
    (``trace.GraphCallbackHandler``), OTel span attributes
    (``observability.callbacks``), TeamWork live output and activity pushed by
    the trace handler, the spoke and sub-agent tool logs
    (``spokes._runner._log_tool_calls``) and the orchestrator's ``trace.log``.
    The governance audit entry applies the same rule in
    ``action_policy.log_action``. Not covered: the model's own context; the
    conversation history; a reply in which a model repeats a secret, outside
    ``trace.log`` (a spoke's answer as posted to TeamWork, span summaries of
    answers); any form of a secret other than as written or as ``repr`` /
    JSON escape it; and secrets that never came out of a credential tool. A
    span's argument hashes (``args_sha256``) are computed over the raw
    arguments, so they stay comparable with the secrets proxy's wire record.
    """
    if tool_name in CREDENTIAL_TOOLS:
        return WITHHELD_OUTPUT
    return scrub(tool_output_text(output))[:limit]


def error_preview_for_tool(tool_name: str | None, error: Any, limit: int | None) -> str:
    """A tool's exception as a sink may show it, capped at *limit*.

    A credential tool's exception message is its own output, so only the
    exception type is shown; any other tool's has this turn's registered
    secret values masked.
    """
    if tool_name in CREDENTIAL_TOOLS:
        return f"{type(error).__name__} ({WITHHELD_OUTPUT})"
    try:
        return scrub(str(error))[:limit]
    except Exception:  # noqa: BLE001 - a broken __str__ must not escape a callback
        return f"<unprintable {type(error).__name__}>"


def args_preview_for_tool(tool_name: str | None, args: Any, limit: int | None) -> str:
    """Tool-call arguments as a sink may show them, capped at *limit*
    (``None``: uncapped).

    For a credential tool only the argument NAMES are shown, every value masked:
    which site was asked for is not secret today, but a credential tool is the
    one place a future argument could be, and the names are what a reader needs.
    Any other tool's arguments have this turn's registered secret values masked
    (``turn_secrets.scrub``) — the password ``browser_login`` returned, typed
    with ``browser_fill(text=…)`` — before they are capped, so no prefix of
    one survives the cut.
    """
    if tool_name in CREDENTIAL_TOOLS:
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                return "***"
        if isinstance(args, dict):
            return str({k: "***" for k in args})[:limit]
        return "***"
    try:
        return scrub(str(args))[:limit]
    except Exception:  # noqa: BLE001 - a broken __str__ must not escape a callback
        return f"<unprintable {type(args).__name__}>"
